"""Entry point for a single private Colab production job."""

import fcntl
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import time
import wave
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from runtime_probe import runtime_hardware

VIDEO_PORT = 18897
EDITOR_PORT = 18898
QWEN_PORT = 18899
LOCAL_HTTP = build_opener(ProxyHandler({}))


class JobError(Exception):
    pass


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def publish(folder, state, phase, *, artifact=None, engine=None, hardware=None):
    row = {
        "id": folder.name,
        "state": state,
        "phase": phase,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if engine:
        row["engine"] = engine
    if hardware is not None:
        row["hardware"] = hardware
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


def prepare_breeze(folder, cache, spec):
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
    return python, source, checkpoint


def synthesize(folder, cache, spec):
    python, source, checkpoint = prepare_breeze(folder, cache, spec)
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


def comfy_call(path, body=None, *, binary=False, port=VIDEO_PORT):
    request = Request(f"http://127.0.0.1:{port}{path}", data=(
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


def wait_video(folder, graph, *, port=VIDEO_PORT):
    answer = comfy_call("/prompt", {"prompt": graph, "client_id": folder.name}, port=port)
    job = answer.get("prompt_id")
    if not isinstance(job, str) or not job or answer.get("node_errors"):
        raise JobError("ComfyUI 没有接受视频任务。")
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        row = comfy_call("/history/" + job, port=port).get(job, {})
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
                            output.write_bytes(comfy_call("/view?" + query, binary=True, port=port))
                            return output
            raise JobError("ComfyUI 已结束，但没有返回 MP4。")
        time.sleep(4)
    raise JobError("视频生成超过 30 分钟，已结束本次模型进程。")


def checkout(source, url, revision):
    source.mkdir(parents=True, exist_ok=True)
    run_command(["git", "init", "-q"], cwd=source, timeout=60)
    run_command(["git", "fetch", "--depth=1", url, revision], cwd=source, timeout=300)
    run_command(["git", "checkout", "--force", "--detach", revision], cwd=source, timeout=60)


def prepare_weights(source, file):
    target = source / "models" / file["path"]
    receipt = target.with_suffix(".gm-verified")

    def fingerprint():
        info = target.stat()
        return f'{file["sha256"]}:{info.st_size}:{info.st_mtime_ns}:{info.st_ino}'

    if target.is_file() and receipt.is_file() and receipt.read_text() == fingerprint():
        return
    cached = False
    if not target.exists() and "/Comfy-Org/MiniMax-H3/" in file["url"]:
        for candidate in (Path("/content/drive/MyDrive/h3-models") / target.name, Path("/content/ComfyUI/models") / file["path"]):
            if candidate.is_file() and candidate.stat().st_size == file["size"] and sha256(candidate) == file["sha256"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.unlink(missing_ok=True)
                target.symlink_to(candidate)
                cached = True
                break
    if not cached:
        if not target.is_file() and shutil.disk_usage(source).free < file["size"] + 2 * 1024**3:
            raise JobError("实例磁盘空间不足。请使用空间更大的实例或已有的模型缓存。")
        download(file["url"], target, file["size"], file["sha256"])
    receipt.write_text(fingerprint())


def prepare_comfy(folder, cache, recipe, *, cpu=False, weights=True):
    source = cache / "comfyui"
    revision = recipe["comfy_commit"]
    if not (source / "main.py").exists():
        publish(folder, "running", "安装 ComfyUI")
        checkout(source, "https://github.com/Comfy-Org/ComfyUI.git", revision)
    publish(folder, "running", "核对 ComfyUI 环境")
    # Colab provides CUDA PyTorch. Keep added dependencies in a video-only environment.
    if not cpu:
        run_command([sys.executable, "-c", "import torch; assert torch.cuda.is_available(); assert torch.ones(1, device='cuda').item() == 1"], timeout=60)
    python = venv(cache / "video-env-1", ["-r", str(source / "requirements.txt")], system_packages=True)
    files = recipe["files"] if weights else []
    for index, file in enumerate(files, 1):
        publish(folder, "running", f"下载并校验模型 {index}/{len(files)}")
        prepare_weights(source, file)
    return source, python


def generate_video(folder, cache, spec):
    recipe = spec["recipe"]
    source, python = prepare_comfy(folder, cache, recipe)

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
        close_process(process)


def h3_profile(hardware):
    vram = max((gpu["vram_mib"] for gpu in hardware["gpus"]), default=0)
    if vram < 14 * 1024:
        raise JobError("H3 需要可识别的 NVIDIA GPU。请选 G4、H100、A100、L4 或 T4。")
    if vram < 36 * 1024 and hardware["ram_mib"] < 30 * 1024:
        raise JobError("这台低显存实例的系统内存不足 30 GiB。请选择高内存或更高显存的实例。")
    return "low" if vram < 36 * 1024 else "high" if vram >= 76 * 1024 else "balanced"


def launch_service(folder, args, health, *, cwd, env=None):
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", urlsplit(health).port)) == 0:
            raise JobError("应用端口已被占用。请选择另一台实例，或在原工作台结束服务后重试。")
    with (folder / "service.log").open("ab") as log:
        process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    try:
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with LOCAL_HTTP.open(health, timeout=4) as response:
                    if response.status == 200:
                        return process
            except OSError:
                time.sleep(2)
        raise JobError("应用启动失败。请核对这台实例的依赖、内存和磁盘空间。")
    except BaseException:
        close_process(process)
        raise


def verify_h3_editor(project, legacy):
    if project != "h3":
        return
    nodes = ["MiniMaxH3ImageToVideo"]
    if legacy:
        nodes.extend(("MiniMaxH3MotionContextClipStream", "MiniMaxH3ContextTaperNoise",
                      "MiniMaxH3TwoPassLatentReconcileT8Advanced", "MiniMaxH3LatentUpscaleWithModel"))
    for node in nodes:
        if node not in comfy_call("/object_info/" + node, port=EDITOR_PORT):
            raise JobError(f"ComfyUI 已启动，但节点 {node} 没有加载。")


def start_editor(folder, cache, spec, hardware):
    project = spec["project"]
    profile = h3_profile(hardware) if project == "h3" else None
    cpu = spec["allocated_gpu"] == "CPU"
    source, python = prepare_comfy(folder, cache, spec["recipe"], cpu=cpu, weights=project != "comfy")
    stamp = source / ".google-manager-editor"
    legacy = spec.get("legacy_nodes", False)
    if stamp.is_file():
        try:
            comfy_call("/system_stats", port=EDITOR_PORT)
            if legacy and stamp.read_text() != "legacy":
                raise JobError("此实例已打开基础 ComfyUI。请在专用实例部署旧工作流节点。")
            verify_h3_editor(project, legacy)
            return
        except (OSError, ValueError):
            pass
    if legacy:
        publish(folder, "running", "安装旧工作流的四个自定义节点")
        for node in spec["recipe"]["legacy_nodes"]:
            target = source / "custom_nodes" / node["directory"]
            marker = target / (".gm-node-" + node["commit"])
            if marker.is_file():
                continue
            checkout(target, node["url"], node["commit"])
            if (target / "requirements.txt").is_file():
                run_command([sys.executable, "-m", "pip", "--python", str(python), "install", "-r", str(target / "requirements.txt")])
            marker.touch()
    publish(folder, "running", "启动私有 ComfyUI 工作台")
    args = [str(python), "main.py", "--listen", "127.0.0.1", "--port", str(EDITOR_PORT), "--disable-auto-launch", "--preview-method", "none"]
    if not legacy:
        args.append("--disable-all-custom-nodes")
    if cpu:
        args.append("--cpu")
    elif profile == "low":
        args.append("--lowvram")
    elif profile == "high" and spec["allocated_gpu"] == "G4":
        args.append("--highvram")
    process = launch_service(folder, args, f"http://127.0.0.1:{EDITOR_PORT}/system_stats", cwd=source)
    try:
        verify_h3_editor(project, legacy)
    except Exception:
        close_process(process)
        raise
    stamp.write_text("legacy" if legacy else "core")


def start_qwen(folder, cache, recipe):
    root = cache / "qwen06"
    for file in recipe["files"]:
        download(file["url"], root / file["path"], file["size"], file["sha256"])
    health = f"http://127.0.0.1:{QWEN_PORT}/health"
    destination = root / "b6500"
    stamp = root / ".google-manager-service"
    if stamp.is_file():
        try:
            with LOCAL_HTTP.open(health, timeout=4) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
    destination.mkdir(exist_ok=True)
    with zipfile.ZipFile(root / "llama-b6500-bin-ubuntu-x64.zip") as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination.resolve()) or stat.S_ISLNK(member.external_attr >> 16):
                raise JobError("Qwen 运行库归档包含不支持的路径。")
        archive.extractall(destination)
    binary, = destination.rglob("llama-server")
    binary.chmod(0o700)
    libraries = ":".join(sorted({str(path.parent) for path in destination.rglob("*.so*")}))
    args = [str(binary), "--model", str(root / "Qwen3-0.6B-Q8_0.gguf"), "--host", "127.0.0.1", "--port", str(QWEN_PORT),
            "--ctx-size", "2048", "--threads", "4", "--n-gpu-layers", "0", "--jinja"]
    publish(folder, "running", "启动 Qwen3 对话服务")
    launch_service(folder, args, health, cwd=destination, env={**os.environ, "LD_LIBRARY_PATH": libraries})
    stamp.touch()


def deploy(folder, cache, spec):
    hardware = runtime_hardware()
    if spec["project"] == "breeze":
        prepare_breeze(folder, cache, spec)
    elif spec["project"] == "qwen":
        start_qwen(folder, cache, spec["recipe"])
    else:
        start_editor(folder, cache, spec, hardware)
    publish(folder, "done", "部署完成，可以开始创作", hardware=hardware)


def generate_h3(folder, cache, spec):
    hardware = runtime_hardware()
    profile = h3_profile(hardware)
    if profile == "low" and spec["frames"] > 124:
        raise JobError("这台低显存实例只开放约 5 秒的 H3 试跑。更长镜头请选择高显存实例。")
    start_editor(folder, cache, spec, hardware)
    graph = spec["recipe"]["graph"]
    width, height = (832, 480) if profile == "low" else (1344, 768)
    if spec["ratio"] == "portrait":
        width, height = height, width
    graph["5"]["inputs"].update(prompt=spec["text"], width=width, height=height, length=spec["frames"])
    graph["6"]["inputs"]["noise_seed"] = spec["seed"]
    graph["15"]["inputs"]["filename_prefix"] = f"gm/{folder.name}/h3"
    publish(folder, "running", "MiniMax H3 正在生成视频和声音")
    return wait_video(folder, graph, port=EDITOR_PORT)


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
            if spec["kind"] == "deploy":
                deploy(folder, cache, spec)
                return
            if spec["kind"] == "tts":
                engine, output = "Breeze TTS 2", synthesize(folder, cache, spec)
            elif spec.get("engine") == "MiniMax H3":
                engine, output = "MiniMax H3", generate_h3(folder, cache, spec)
            else:
                engine, output = "Wan2.2", generate_video(folder, cache, spec)
            publish(folder, "done", "云端文件已生成，等待下载", artifact=output, engine=engine)
    except JobError as exc:
        publish(folder, "failed", str(exc))
    except Exception:
        publish(folder, "failed", "任务在实例上失败。请核对可用空间、SDK 与网络。")
        raise


if __name__ == "__main__":
    run(Path(sys.argv[1]))
