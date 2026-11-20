"""Use the selected Colab assignment's private notebook and contents APIs."""

import base64
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from .colab import ProviderError

RECIPES = Path(__file__).resolve().parent.parent / "recipes"
REMOTE_ROOT = "/content/google-manager/jobs"
MAX_ARTIFACT = 64 * 1024 * 1024
ID = re.compile(r"[a-f0-9]{32}")
DIGEST = re.compile(r"[a-f0-9]{64}")
CREATION_GPUS = ("L4", "A100", "H100", "G4")

LAUNCH = '''
import base64, json, os, pathlib, subprocess, sys
_gm_id = {job_id!r}
_gm_folder = pathlib.Path({root!r}) / _gm_id
_gm_folder.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
try:
    _gm_folder.mkdir(mode=0o700)
except FileExistsError:
    print("GM_KNOWN_" + _gm_id)
else:
    try:
        (_gm_folder / "request.json").write_bytes(base64.b64decode({payload!r}))
        (_gm_folder / "run.py").write_bytes(base64.b64decode({runner!r}))
        with (_gm_folder / "run.log").open("ab") as _gm_log:
            subprocess.Popen(
                [sys.executable, str(_gm_folder / "run.py"), str(_gm_folder)],
                cwd=_gm_folder, stdin=subprocess.DEVNULL,
                stdout=_gm_log, stderr=_gm_log, start_new_session=True,
            )
    except OSError:
        (_gm_folder / "status.json").write_text(json.dumps({{
            "id": _gm_id, "state": "failed", "phase": "远端无法启动任务进程。"
        }}, ensure_ascii=False), encoding="utf-8")
        raise
    print("GM_KNOWN_" + _gm_id)
'''


def launch_code(job_id, spec):
    recipe = "breeze.json" if spec["kind"] == "tts" else "wan22.json"
    spec = {**spec, "recipe": json.loads((RECIPES / recipe).read_text(encoding="utf-8"))}
    return LAUNCH.format(
        root=REMOTE_ROOT, job_id=job_id,
        payload=base64.b64encode(json.dumps(spec, ensure_ascii=False).encode("utf-8")).decode("ascii"),
        runner=base64.b64encode((RECIPES / "run_job.py").read_bytes()).decode("ascii"),
    )


def assignment_proxy(client, instance):
    for item in client.list_assignments():
        if item.endpoint == instance:
            proxy = item.runtime_proxy_info
            url = urlsplit(proxy.url)
            if (
                url.scheme == "https" and url.hostname
                and url.hostname.endswith((".prod.r.colab.dev", ".googleusercontent.com"))
                and url.port in {None, 443} and not url.username and not url.password
                and url.path in {"", "/"} and not url.query and not url.fragment and proxy.token
            ):
                return proxy.url.rstrip("/"), proxy.token, item.accelerator.value
            raise ProviderError("Colab 返回了无法核对的私有连接地址。")
    raise ProviderError("当前账号已查不到这个 Colab 实例。", 404)


def submit(runtime_url, proxy_token, job_id, spec):
    from colab_cli.runtime import ColabRuntime

    # No history logger: the submitted code contains the user's private prompt.
    logging.disable(logging.CRITICAL)
    runtime = ColabRuntime(runtime_url, proxy_token)
    try:
        outputs = runtime.execute_code(launch_code(job_id, spec), timeout=50)
        if any(row.get("output_type") == "error" for row in outputs):
            raise ProviderError("Colab 没有确认收到任务，请刷新任务核对。", uncertain=True)
        output = "".join(str(row.get("text", "")) for row in outputs if row.get("output_type") == "stream")
        if "GM_KNOWN_" + job_id not in output:
            raise ProviderError("Colab 没有返回提交确认，请刷新任务核对。", uncertain=True)
    except ProviderError:
        raise
    except Exception:
        raise ProviderError("私有通道中断。请刷新该任务，避免重复生成。", uncertain=True) from None
    finally:
        # This kernel was created by this connection only. The detached job
        # keeps running, and unrelated kernels on the instance stay intact.
        runtime.stop(shutdown_kernel=True)
    return {"state": "submitted"}


def get_contents(session, url, token, suffix, limit, *, binary=False):
    try:
        with session.get(
            url + "/api/contents/" + suffix,
            params={"content": "1", "colab-runtime-proxy-token": token, "authuser": "0",
                    **({"format": "base64"} if binary else {})},
            headers={"X-Colab-Runtime-Proxy-Token": token},
            stream=True,
        ) as response:
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise ProviderError("Colab 私有文件通道暂时不可用。", uncertain=True)
            parts = bytearray()
            for chunk in response.iter_content(256 * 1024):
                if len(parts) + len(chunk) > limit:
                    raise ProviderError("云端文件超出传输上限。", uncertain=True)
                parts.extend(chunk)
        row = json.loads(parts)
        data = row["content"]
        if row.get("type") != "file" or not isinstance(data, str):
            raise ValueError
        if row.get("format") == "base64":
            return base64.b64decode(data.replace("\r", "").replace("\n", ""), validate=True)
        if not binary and row.get("format") == "text":
            return data.encode("utf-8")
        raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise ProviderError("无法读取云端任务文件，请稍后刷新。", uncertain=True) from None


def review(session, url, token, job_id, kind, destination):
    prefix = REMOTE_ROOT.strip("/") + "/" + job_id + "/"
    content = get_contents(session, url, token, prefix + "status.json", 16 * 1024)
    if content is None:
        return {"state": "missing"}
    try:
        status = json.loads(content)
        state = status["state"]
        if status.get("id") != job_id or state not in {"running", "done", "failed"} or not isinstance(status["phase"], str):
            raise ValueError
        safe = {"state": state, "phase": status["phase"][:140]}
        if status.get("engine") in ("Breeze TTS 2", "Wan2.2"):
            safe["engine"] = status["engine"]
        if state != "done":
            return safe
        size, digest = status["size"], status["sha256"]
        if type(size) is not int or not 1 <= size <= MAX_ARTIFACT or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ProviderError("云端任务的状态或文件校验信息无效。", uncertain=True) from None
    name = "output.wav" if kind == "tts" else "output.mp4"
    # JSON transport encodes file bytes as base64. The reported size caps both reads.
    encoded_size = ((size + 2) // 3) * 4
    line_breaks = ((encoded_size + 75) // 76) * 4
    data = get_contents(session, url, token, prefix + name, encoded_size + line_breaks + 16384, binary=True)
    if data is None or len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise ProviderError("远端作品没有通过大小与 SHA-256 校验。请再取回一次。", uncertain=True)
    with open(destination, "wb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(data)
    return {**safe, "size": size, "sha256": digest}


def execute_job(action, client, session_type, payload):
    job_id, instance = payload.get("job_id"), payload.get("instance_id")
    if not isinstance(job_id, str) or not ID.fullmatch(job_id):
        raise ProviderError("创作任务标识无效。", 400)
    url, token, gpu = assignment_proxy(client, instance)
    if action == "submit_job":
        spec = payload["spec"]
        if gpu not in CREATION_GPUS:
            raise ProviderError("请选择 L4、G4、A100 或 H100 实例。", 409)
        usage = client.get_consumption_user_info()
        if usage.paid_compute_units_balance <= 0:
            raise ProviderError("该账号须有可用 Colab CU 才能通过桌面工作台提交生成。", 409)
        return submit(url, token, job_id, spec)
    with session_type() as runtime_session:
        runtime_session.trust_env = False
        return review(runtime_session, url, token, job_id, payload["kind"], payload["destination"])
