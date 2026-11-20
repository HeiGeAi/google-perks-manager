"""Entry point for a single private Colab production job."""

import fcntl
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener, urlopen

VIDEO_PORT = 18897
LOCAL_HTTP = build_opener(ProxyHandler({}))


class JobError(Exception):
    pass


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def publish(folder, state, phase, *, artifact=None, engine=None):
    row = {
        "id": folder.name,
        "state": state,
        "phase": phase,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if engine:
        row["engine"] = engine
    if artifact:
        row["size"] = artifact.stat().st_size
        row["sha256"] = sha256(artifact)
    target = folder / "status.json"
    temp = folder / ".status"
    temp.write_text(json.dumps(row, ensure_ascii=False), encoding="utf-8")
    temp.replace(target)


def download(url, target, size, digest):
    if target.is_file() and target.stat().st_size == size and sha256(target) == digest:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    checksum = hashlib.sha256()
    received = 0
    try:
        with urlopen(url, timeout=45) as response, temporary.open("wb") as handle:
            deadline = time.monotonic() + 1500
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > size or time.monotonic() > deadline:
                    raise JobError("模型下载超出大小或时限，请重试。")
                checksum.update(chunk)
                handle.write(chunk)
        if received != size or checksum.hexdigest() != digest:
            raise JobError("下载的模型文件校验失败。")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def run_command(args, *, timeout=1200, cwd=None, env=None):
    try:
        subprocess.run(args, cwd=cwd, env=env, check=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError):
        raise JobError("模型环境的命令执行失败。请核对实例的网络、磁盘和 Python 版本。") from None


def venv(root, requirements, *, system_packages=False):
    python = root / "bin/python"
    ready = root / ".gm-ready-1"
    if ready.is_file() and python.is_file():
        return python
    flags = ["--system-site-packages"] if system_packages else []
    run_command([sys.executable, "-m", "venv", "--without-pip", *flags, str(root)], timeout=60)
    run_command([sys.executable, "-m", "pip", "--python", str(python), "install", "--disable-pip-version-check", *requirements])
    ready.touch()
    return python


def fetch_checkpoint(root, recipe):
    checkpoint = root / "checkpoint"
    files = recipe["files"]
    stamp = checkpoint / (".gm-" + recipe["revision"])
    if stamp.is_file() and all((checkpoint / f["path"]).is_file() and (checkpoint / f["path"]).stat().st_size == f["size"] for f in files):
        return checkpoint

    old = Path("/content/cloud-deck-breeze-tts-2/checkpoint")
    # The old app and this app use the same pinned checkpoints. Verify before reuse.
    if old.is_dir() and all((old / f["path"]).is_file() and (old / f["path"]).stat().st_size == f["size"] for f in files):
        if all(sha256(old / f["path"]) == f["sha256"] for f in files):
            return old

    def fetch(file):
        download(file["url"], checkpoint / file["path"], file["size"], file["sha256"])

    # Three weight shards are independent. This also overlaps model I/O with env setup.
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(fetch, sorted(files, key=lambda file: file["size"], reverse=True)))
    stamp.touch()
    return checkpoint


def breeze_env(root, source, revision):
    python = root / "venv/bin/python"
    stamp = root / ("venv/.gm-deps-" + revision)
    if not (stamp.is_file() and python.is_file()):
        if not python.is_file():
            run_command([sys.executable, "-m", "venv", "--without-pip", str(root / "venv")], timeout=80)
        uv = shutil.which("uv")
        if uv:
            pip = [uv, "pip", "install", "--python", str(python)]
        else:
            pip = [sys.executable, "-m", "pip", "--python", str(python), "install", "--disable-pip-version-check"]
        run_command([*pip, "--index-url", "https://download.pytorch.org/whl/cu128",
                     "torch==2.9.1+cu128", "torchaudio==2.9.1+cu128"], timeout=1200)
        run_command([*pip, "-r", str(source / "requirements.txt")], timeout=600)
    run_command([str(python), "-c",
                 "import torch, torchaudio; assert (torch.__version__, torchaudio.__version__) == "
                 "('2.9.1+cu128', '2.9.1+cu128'); assert torch.cuda.is_available(); "
                 "assert torch.ones(1, device='cuda').item() == 1"], timeout=90)
    stamp.touch()
    return python


def close_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def breeze_speech(folder, base, spec):
    form = {"text": spec["text"], "seed": spec.get("seed", 42), "cfg_scale": 1.0}
    if spec.get("instruction"):
        form.update(instruction=spec["instruction"], cfg_scale=2.0)
    request = Request(base + "/v1/audio/speech", data=urlencode(form).encode("ascii"),
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    output = folder / "output.wav"
    with LOCAL_HTTP.open(request, timeout=120) as response:
        rate = int(response.headers.get("X-Sample-Rate", 0))
        if response.headers.get("X-Sample-Format") != "s16le" or rate != 24000:
            raise JobError("Breeze 返回了未知音频格式。")
        with wave.open(str(output), "wb") as wav:
            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            written = 0
            while chunk := response.read(128 * 1024):
                written += len(chunk)
                if written > 64 * 1024 * 1024:
                    raise JobError("Breeze 音频超出传输上限。")
                wav.writeframesraw(chunk)
        if not written or written % 2:
            raise JobError("Breeze 返回了空音频或未完整返回。")
    return output


def synthesize(folder, cache, spec):
    recipe = spec["recipe"]
    root = cache / "breeze-tts-2"
    source = root / "source"
    revision = recipe["code_commit"]
    publish(folder, "running", "准备 Breeze TTS 2 权重和 GPU 环境")
    source.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        weights = pool.submit(fetch_checkpoint, root, recipe)
        if not (source / (".gm-source-" + revision)).is_file():
            run_command(["git", "init", "-q"], cwd=source, timeout=60)
            run_command(["git", "fetch", "--depth=1", "https://github.com/breezeblue-ai/breeze-tts.git", revision], cwd=source, timeout=300)
            run_command(["git", "checkout", "--detach", revision], cwd=source, timeout=60)
            (source / (".gm-source-" + revision)).touch()
        if not shutil.which("sox"):
            subprocess_env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
            run_command(["apt-get", "update", "-qq"], env=subprocess_env, timeout=180)
            run_command(["apt-get", "install", "-y", "-qq", "sox"], env=subprocess_env, timeout=180)
        python = breeze_env(root, source, revision)
        if not weights.done():
            publish(folder, "running", "核对 Breeze TTS 2 权重文件")
        checkpoint = weights.result()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    publish(folder, "running", "在 Colab GPU 上加载 Breeze TTS 2")
    process = subprocess.Popen(
        [str(python), "-m", "breeze_infer.api", str(checkpoint), "--host", "127.0.0.1", "--port", str(port), "--no-fast-all"],
        cwd=source, stdin=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise JobError("Breeze TTS 2 加载失败，请检查实例依赖及显存。")
            try:
                with LOCAL_HTTP.open(base + "/health", timeout=4) as health:
                    if json.load(health).get("sample_rate") == 24000:
                        break
            except (OSError, ValueError):
                time.sleep(3)
        else:
            raise JobError("Breeze TTS 2 加载超时。")
        publish(folder, "running", "Breeze TTS 2 正在生成配音")
        return breeze_speech(folder, base, spec)
    finally:
        close_process(process)


def comfy_call(path, body=None, *, binary=False):
    request = Request(f"http://127.0.0.1:{VIDEO_PORT}{path}", data=(
        json.dumps(body).encode("utf-8") if body is not None else None
    ), headers={"Content-Type": "application/json"} if body is not None else {})
    with LOCAL_HTTP.open(request, timeout=45) as response:
        maximum = 64 * 1024 * 1024 if binary else 3 * 1024 * 1024
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise JobError("视频或模型回应超出可传输大小。")
    return data if binary else json.loads(data)


def video_graph(folder, recipe, spec):
    graph = recipe["graph"]
    graph["4"]["inputs"]["text"] = spec["text"]
    graph["7"]["inputs"].update(width=spec["width"], height=spec["height"], length=spec["frames"])
    graph["8"]["inputs"]["seed"] = spec["seed"]
    graph["11"]["inputs"]["filename_prefix"] = f"gm/{folder.name}/clip"
    return graph


def wait_video(folder, graph):
    answer = comfy_call("/prompt", {"prompt": graph, "client_id": folder.name})
    job = answer.get("prompt_id")
    if not isinstance(job, str) or not job or answer.get("node_errors"):
        raise JobError("ComfyUI 没有接受视频任务。")
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        row = comfy_call("/history/" + job).get(job, {})
        status = row.get("status", {})
        if status.get("status_str") == "error":
            raise JobError("视频生成失败。请检查该实例的显存和模型状态。")
        if status.get("completed"):
            for node in row.get("outputs", {}).values():
                for key in ("videos", "gifs", "images"):
                    for item in node.get(key, []):
                        filename = item.get("filename", "")
                        if item.get("type") == "output" and filename.lower().endswith(".mp4"):
                            query = urlencode({"filename": filename, "subfolder": item.get("subfolder", ""), "type": "output"})
                            output = folder / "output.mp4"
                            output.write_bytes(comfy_call("/view?" + query, binary=True))
                            return output
            raise JobError("ComfyUI 已结束，但没有返回 MP4。")
        time.sleep(4)
    raise JobError("视频生成超过 30 分钟，已结束本次模型进程。")


def generate_video(folder, cache, spec):
    recipe = spec["recipe"]
    source = cache / "comfyui"
    revision = recipe["comfy_commit"]
    if not (source / "main.py").exists():
        publish(folder, "running", "安装 ComfyUI")
        source.mkdir(parents=True, exist_ok=True)
        run_command(["git", "init", "-q"], cwd=source, timeout=60)
        run_command(["git", "fetch", "--depth=1", "https://github.com/Comfy-Org/ComfyUI.git", revision], cwd=source, timeout=300)
        run_command(["git", "checkout", "--force", "--detach", revision], cwd=source, timeout=60)

    publish(folder, "running", "核对 CUDA 与视频依赖")
    # Colab provides CUDA PyTorch. Keep added dependencies in a video-only environment.
    run_command([sys.executable, "-c", "import torch; assert torch.cuda.is_available(); assert torch.ones(1, device='cuda').item() == 1"], timeout=60)
    python = venv(cache / "video-env-1", ["-r", str(source / "requirements.txt")], system_packages=True)
    for index, file in enumerate(recipe["files"], 1):
        publish(folder, "running", f"下载并核对视频模型 {index}/3")
        download(file["url"], source / "models" / file["path"], file["size"], file["sha256"])

    publish(folder, "running", "启动本次视频模型")
    process = subprocess.Popen(
        [str(python), str(source / "main.py"), "--listen", "127.0.0.1", "--port", str(VIDEO_PORT),
         "--disable-auto-launch", "--disable-all-custom-nodes", "--preview-method", "none"],
        cwd=source, stdin=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise JobError("视频模型未启动，请核对 CUDA、内存与可用端口。")
            try:
                comfy_call("/system_stats")
                break
            except (OSError, ValueError):
                time.sleep(3)
        else:
            raise JobError("视频模型启动超时。")
        publish(folder, "running", "生成 Wan2.2 视频")
        return wait_video(folder, video_graph(folder, recipe, spec))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def run(folder):
    try:
        spec = json.loads((folder / "request.json").read_text(encoding="utf-8"))
        workspace = folder.parent.parent
        cache = workspace / "models"
        cache.mkdir(exist_ok=True, mode=0o700)
        with (workspace / ".generation.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JobError("这个实例已有生成任务。等当前任务完成后再试。") from None
            audio = spec["kind"] == "tts"
            output = synthesize(folder, cache, spec) if audio else generate_video(folder, cache, spec)
            engine = "Breeze TTS 2" if audio else "Wan2.2"
            publish(folder, "done", "云端文件已生成，等待下载", artifact=output, engine=engine)
    except JobError as exc:
        publish(folder, "failed", str(exc))
    except Exception:
        publish(folder, "failed", "任务在实例上失败。请核对可用空间、SDK 与网络。")
        raise


if __name__ == "__main__":
    run(Path(sys.argv[1]))
