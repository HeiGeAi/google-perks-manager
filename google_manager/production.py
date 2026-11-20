"""Persistent production jobs and locally verified audio/video artifacts."""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

from .accounts import UserError, label, private_directory, read_object, timestamp, write_private_json
from .deployment import PROJECTS, project_for, require_supported_runtime
from .local_files import open_local_fd
from .remote_jobs import ID

ACTIVE = {"queued", "running", "remote", "check"}
VIDEO_SIZES = {"landscape": (1280, 704), "portrait": (704, 1280)}
MEDIA_NAMES = {"tts": "output.wav", "video": "output.mp4", "mux": "output.mp4"}
MAX_LOCAL_SOURCE = 1024 * 1024 * 1024


def media_tool():
    override = os.environ.get("GOOGLE_MANAGER_MEDIA_HELPER")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        name = "GooglePerksManager.exe" if os.name == "nt" else "GoogleManagerMedia"
        return Path(sys.executable).parent.parent / name
    dist = Path(__file__).resolve().parent.parent / "dist"
    relative = "Contents/Resources/GoogleManagerMedia"
    current = dist / "谷歌羊毛管理器.app" / relative
    return current if current.is_file() else dist / "谷歌管理器.app" / relative


def validate_output(path, kind):
    if not path.is_file() or path.stat().st_size == 0:
        raise UserError("没有生成可用的媒体文件。")
    if kind == "tts":
        try:
            with wave.open(str(path), "rb") as audio:
                frames, rate = audio.getnframes(), audio.getframerate()
                if rate < 8000 or frames < 1 or not audio.readframes(min(frames, rate)):
                    raise ValueError
                seconds = round(frames / rate, 3)
                return {"seconds": seconds, "sample_rate": rate}
        except (ValueError, EOFError, wave.Error):
            raise UserError("新生成的音频无法读取。") from None
    try:
        result = subprocess.run(
            [str(media_tool()), "probe", str(path)], capture_output=True, timeout=20, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        probe = json.loads(result.stdout)
        return {"seconds": round(probe["seconds"], 3), "width": probe["width"], "height": probe["height"]}
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise UserError("新生成的视频无法播放或缺少视频轨。") from None


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_local_media(selected, folder, field, extensions):
    src = Path(selected).expanduser()
    if not src.is_absolute() or src.suffix.lower() not in extensions:
        raise UserError("请选择支持的本机媒体文件。")
    destination = folder / (field + src.suffix.lower())
    try:
        src_fd = open_local_fd(src)
        with os.fdopen(src_fd, "rb") as original:
            info = os.fstat(original.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_LOCAL_SOURCE:
                raise UserError("请选择 1 GB 以内的普通媒体文件。")
            with destination.open("xb") as output:
                if os.name != "nt":
                    os.fchmod(output.fileno(), 0o600)
                remaining = info.st_size
                while remaining:
                    data = original.read(min(remaining, 1024 * 1024))
                    if not data:
                        raise UserError("读取时源文件发生变化，请重新选择。")
                    output.write(data)
                    remaining -= len(data)
    except OSError:
        raise UserError("本机媒体文件无法读取，请重新选择。") from None
    return destination


class Production:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.store.root / "production"
        private_directory(self.root)
        self.lock = threading.RLock()
        self.working = set()
        for job in self._rows():
            if job["state"] in ACTIVE and job["mode"] == "mac":
                self.update(job["id"], state="interrupted", phase="App 已退出。请重新提交本机任务。")
            elif job["state"] in {"queued", "running", "remote"}:
                self.update(job["id"], state="check", phase="上次等待已中断。可核对云端状态或取回作品。")

    def _rows(self):
        for folder in self.root.iterdir():
            if ID.fullmatch(folder.name) and folder.is_dir() and not folder.is_symlink():
                job = read_object(folder / "job.json")
                if job:
                    yield job

    def get(self, job_id):
        if not isinstance(job_id, str) or not ID.fullmatch(job_id):
            raise UserError("任务不存在。", 404)
        row = read_object(self.root / job_id / "job.json")
        if not row:
            raise UserError("任务不存在。", 404)
        return row

    @staticmethod
    def public(row):
        return {key: value for key, value in row.items() if key != "spec"}

    def list(self, account_id=None):
        if account_id:
            self.manager.store.one(account_id)
        with self.lock:
            jobs = [self.public(job) for job in self._rows()
                    if job["mode"] == "mac" or job["account_id"] == account_id or self.has_local_output(job)]
        return sorted(jobs, key=lambda job: (job["created_at"], job["id"]), reverse=True)

    def has_local_output(self, row):
        try:
            self.saved_file(row)
            return True
        except UserError:
            return False

    def update(self, job_id, **changes):
        with self.lock:
            row = self.get(job_id)
            row.update(**changes, updated_at=timestamp())
            write_private_json(self.root / job_id / "job.json", row)
            return row

    def mux_source(self, body, field, kinds):
        job_id = body.get(field + "_job")
        if not job_id:
            return {field + "_path": label(body.get(field + "_path"), "媒体文件", 1024)}
        job = self.get(job_id)
        if job["kind"] not in kinds or job["state"] != "done":
            raise UserError("请选择已生成的媒体。")
        self.saved_file(job)
        return {field + "_job": job_id}

    def spec(self, body):
        kind, mode = body.get("kind"), body.get("mode")
        if not isinstance(kind, str) or kind not in (*MEDIA_NAMES, "deploy") or mode not in ("mac", "colab"):
            raise UserError("请选择可用的创作功能和运行环境。")
        if kind == "deploy":
            project = body.get("project")
            legacy = body.get("legacy_nodes", False)
            if (mode != "colab" or not isinstance(project, str) or project not in PROJECTS
                    or type(legacy) is not bool or (legacy and project != "h3")):
                raise UserError("请选择可部署的项目。")
            return {"kind": kind, "project": project, "legacy_nodes": legacy}
        if kind == "mux":
            if mode != "mac":
                raise UserError("音画合成在本机执行。")
            return {"kind": kind, **self.mux_source(body, "video", {"video", "mux"}),
                    **self.mux_source(body, "audio", {"tts"})}
        text = body.get("text")
        limit = 600 if kind == "tts" else 1800
        if not isinstance(text, str) or not text.strip() or len(text) > limit or "\x00" in text:
            raise UserError(f"请输入 1 至 {limit} 个字符。")
        if kind == "tts":
            if mode != "colab":
                raise UserError("Breeze TTS 2 配音在 Colab GPU 上执行。")
            instruction = body.get("instruction", "")
            if not isinstance(instruction, str) or len(instruction) > 400 or "\x00" in instruction:
                raise UserError("音色与语气说明最多 400 个字符。")
            return {"kind": kind, "text": text.strip(), "instruction": instruction.strip(),
                    "engine": "Breeze TTS 2"}
        if mode != "colab":
            raise UserError("视频生成需要 Colab GPU 实例。")
        engine = body.get("engine", "Wan2.2")
        if engine not in ("MiniMax H3", "Wan2.2"):
            raise UserError("请选择支持的视频模型。")
        ratio = body.get("ratio", "landscape")
        length = body.get("seconds", 5)
        h3_frames = {5: 124, 10: 243, 15: 362}
        durations = h3_frames if engine == "MiniMax H3" else {3, 5}
        if not isinstance(ratio, str) or ratio not in VIDEO_SIZES or type(length) is not int or length not in durations:
            raise UserError("请选择视频比例和片长。")
        width, height = VIDEO_SIZES[ratio]
        seed = body.get("seed")
        if type(seed) is not int or not 0 <= seed <= 4294967295:
            raise UserError("随机种子必须是 0 至 4294967295 的整数。")
        if engine == "MiniMax H3":
            return {"kind": kind, "engine": engine, "text": text.strip(), "ratio": ratio,
                    "frames": h3_frames[length], "seed": seed}
        # Preserve the stored spec of existing Wan2.2 jobs and retries.
        return {"kind": kind, "text": text.strip(), "width": width, "height": height,
                "frames": length * 24 + 1, "seed": seed}

    def start(self, body):
        job_id = body.get("id")
        if not isinstance(job_id, str) or not ID.fullmatch(job_id):
            raise UserError("无法建立任务，请重新打开表单。")
        spec = self.spec(body)
        title = PROJECTS[spec["project"]]["name"] if spec["kind"] == "deploy" else label(body.get("title"), "作品名称", 80)
        mode = body["mode"]
        if mode == "mac":
            return self._register(job_id, title, mode, spec)
        if body.get("confirmed") is not True:
            raise UserError("请确认目标实例在运行期间可能消耗 CU。", 403)
        account_id, instance_id = body.get("account_id"), body.get("instance_id")
        if not isinstance(account_id, str) or not isinstance(instance_id, str):
            raise UserError("请选择已授权的 Google 账号和实例。")
        store = self.manager.store
        with store.account_lock(account_id):
            profile = store.one(account_id)
            if not profile["subject"] or profile.get("authorization_invalid") or not store.has_credential(account_id):
                raise UserError("请先连接这个 Google 账号。", 401)
            match = next((i for i in profile["observation"]["instances"] if i["id"] == instance_id), None)
            if not match:
                raise UserError("请刷新 Colab 实例并选择目标。", 409)
            # Hardware and region are checked again from the private assignment before the worker writes anything.
            require_supported_runtime(project_for(spec), match["gpu"], match.get("region"))
            return self._register(job_id, title, mode, spec, account_id, instance_id)

    def _register(self, job_id, title, mode, spec, account_id=None, instance_id=None):
        with self.lock:
            folder = self.root / job_id
            if (folder / "job.json").is_file():
                same = self.get(job_id)
                if (same["mode"], same["account_id"], same["instance_id"], same["spec"]) != (mode, account_id, instance_id, spec):
                    raise UserError("该任务编号已经用过，请重新打开表单。", 409)
                return self.public(same)
            if mode == "colab" and self.waiting(account_id, instance_id):
                raise UserError("该实例有未处理的任务。请先核对并取回作品。", 409)
            private_directory(folder)
            stamp = timestamp()
            row = {"id": job_id, "title": title, "kind": spec["kind"], "mode": mode,
                   "account_id": account_id, "instance_id": instance_id, "spec": spec,
                   "state": "queued", "phase": "准备任务", "created_at": stamp, "updated_at": stamp}
            if spec["kind"] == "deploy":
                row["project"] = spec["project"]
            write_private_json(folder / "job.json", row)
            self._launch(row, submit=True)
            return self.public(row)

    def waiting(self, account_id, instance_id):
        with self.lock:
            return [job for job in self._rows() if job["mode"] == "colab"
                    and job["account_id"] == account_id and job["instance_id"] == instance_id and job["state"] in ACTIVE]

    def allow_release(self, account_id, instance_id, discard):
        with self.lock:
            waiting = self.waiting(account_id, instance_id)
            if waiting and (not discard or any(job["id"] in self.working or job["state"] != "check" for job in waiting)):
                raise UserError("这个实例还有未取回的创作任务。请在创作中先核对进度或取回文件。", 409)

    def mark_released(self, account_id, instance_id):
        for job in self.waiting(account_id, instance_id):
            self.update(job["id"], state="failed", phase="实例已释放，作品未取回。")

    def refresh(self, job_id):
        # Resolve the account lock before taking the production lock, as in start/stop.
        row = self.get(job_id)
        if row["mode"] != "colab":
            return self.public(row)
        with self.manager.store.account_lock(row["account_id"]), self.lock:
            row = self.get(job_id)
            if row["state"] in ACTIVE and row["id"] not in self.working:
                self._launch(row, submit=False)
            return self.public(row)

    def _launch(self, row, *, submit):
        job_id = row["id"]
        self.working.add(job_id)
        thread = threading.Thread(target=self._work, args=(row, submit), daemon=True, name=f"production-{job_id[:8]}")
        thread.start()

    def _finish(self, job, incoming, *, engine=None):
        media = validate_output(incoming, job["kind"])
        digest = digest_file(incoming)
        name = MEDIA_NAMES[job["kind"]]
        target = incoming.parent / name
        os.chmod(incoming, 0o600)
        incoming.replace(target)
        artifact = {"name": name, "size": target.stat().st_size, "sha256": digest, **media}
        if engine:
            artifact["engine"] = engine
        self.update(job["id"], state="done", phase="作品已保存到本机", artifact=artifact)

    def _mac(self, job, incoming):
        spec = job["spec"]
        folder = incoming.parent
        self.update(job["id"], state="running", phase="合成配音与画面")
        try:
            video = (self.artifact_path(spec["video_job"])[1] if spec.get("video_job")
                     else copy_local_media(spec["video_path"], folder, "picture", {".mp4", ".mov", ".m4v"}))
            audio = (self.artifact_path(spec["audio_job"])[1] if spec.get("audio_job")
                     else copy_local_media(spec["audio_path"], folder, "narration", {".wav", ".mp3", ".m4a", ".aif", ".aiff"}))
            subprocess.run([str(media_tool()), "mux", str(video), str(audio), str(incoming)],
                           check=True, capture_output=True, timeout=240,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        finally:
            for source in (*folder.glob("picture.*"), *folder.glob("narration.*")):
                source.unlink(missing_ok=True)
        self._finish(job, incoming)

    def _access(self, job):
        store = self.manager.store
        with store.account_lock(job["account_id"]):
            return self.manager.oauth.access(store.one(job["account_id"]), store)

    def _cloud(self, job, incoming, submit):
        job_id, instance = job["id"], job["instance_id"]
        colab = self.manager.colab
        token = self._access(job)
        if submit:
            self.update(job_id, state="running", phase="核对所选实例并提交任务")
            colab.submit_job(token, instance, job_id, job["spec"])
            self.update(job_id, state="remote", phase="Colab 已收到任务")
        budget = 3600
        deadline = time.monotonic() + budget
        renew = time.monotonic() + 240
        missing = 0
        while True:
            if time.monotonic() >= renew:
                token = self._access(job)
                renew = time.monotonic() + 240
            result = colab.review_job(token, instance, job_id, job["kind"], incoming)
            state = result["state"]
            if state == "done":
                if job["kind"] == "deploy":
                    self.update(job_id, state="done", phase=result["phase"], hardware=result["hardware"])
                    return
                if incoming.stat().st_size != result["size"] or digest_file(incoming) != result["sha256"]:
                    raise UserError("下载后的媒体文件校验失败，请重新取回。", 502)
                engine = result.get("engine")
                if job["spec"].get("engine") and job["spec"]["engine"] != engine:
                    raise UserError("云端结果的引擎与任务要求不一致。", 502)
                self._finish(job, incoming, engine=engine)
                return
            if state == "failed":
                self.update(job_id, state="failed", phase=result["phase"])
                return
            if state == "missing":
                missing += 1
                if not submit or missing >= 4:
                    self.update(job_id, state="check", phase="实例尚未返回此任务的记录，请稍后核对。")
                    return
            else:
                missing = 0
                self.update(job_id, state="remote", phase=result["phase"])
            if time.monotonic() >= deadline:
                self.update(job_id, state="check", phase="App 已停止等待。请刷新核对云端与作品。")
                return
            time.sleep(12)

    def _work(self, job, submit):
        job_id = job["id"]
        incoming = self.root / job_id / (".incoming-" + MEDIA_NAMES.get(job["kind"], "deployment"))
        try:
            if job["mode"] == "mac":
                self._mac(job, incoming)
            else:
                self._cloud(job, incoming, submit)
        except UserError as exc:
            row = self.get(job_id)
            uncertain = (job["mode"] == "colab" and (
                getattr(exc, "uncertain", False) or row["state"] in {"remote", "check"} or not submit
            ))
            state = "check" if uncertain else "failed"
            if exc.status == 404 and job["mode"] == "colab":
                state = "failed"
            self.update(job_id, state=state, phase=str(exc))
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            state = "check" if job["mode"] == "colab" else "failed"
            phase = "请刷新任务核对 Colab 与作品。" if state == "check" else "本机生成失败，请核对媒体格式和可用空间。"
            self.update(job_id, state=state, phase=phase)
        finally:
            incoming.unlink(missing_ok=True)
            with self.lock:
                self.working.discard(job_id)

    def artifact_path(self, job_id):
        row = self.get(job_id)
        return row, self.saved_file(row)

    def saved_file(self, row):
        if row["state"] != "done" or row["kind"] not in MEDIA_NAMES:
            raise UserError("作品尚未保存。", 404)
        folder = self.root / row["id"]
        path = folder / MEDIA_NAMES[row["kind"]]
        try:
            folder_info = folder.lstat()
            info = path.lstat()
            redirected = getattr(folder_info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            redirected |= getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if (stat.S_ISDIR(folder_info.st_mode) and stat.S_ISREG(info.st_mode) and not redirected
                and info.st_size == (row.get("artifact") or {}).get("size")):
                return path
        except (OSError, ValueError):
            pass
        raise UserError("找不到已保存的作品文件。", 404)
