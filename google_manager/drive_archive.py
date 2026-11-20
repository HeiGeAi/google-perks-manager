"""Copy verified production outputs to a Google Drive for desktop sync folder."""

import hashlib
import os
import re
import stat
import tempfile
import threading
from pathlib import Path

from .accounts import UserError, read_object, timestamp, write_private_json
from .local_files import open_local_fd


WORKSPACE = "Google Perks Manager"
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
DEVICE_NAME = re.compile(r"(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", re.IGNORECASE)
CHUNK = 1024 * 1024


def project_name(value):
    if isinstance(value, str):
        name = value.strip()
        if (0 < len(name) <= 60 and len(name.encode("utf-8")) <= 180
            and not INVALID_NAME.search(name) and not name.startswith(".") and not name.endswith(".")
            and not DEVICE_NAME.fullmatch(name.split(".", 1)[0].rstrip())):
            return name
    raise UserError("请输入有效的项目文件夹名称，最多 60 个字符。")


def directory(path, *, create=False):
    if create:
        path.mkdir(exist_ok=True)
    info = path.lstat()
    # Windows cloud placeholders are valid. Junctions and symlinks redirect writes.
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000:
        raise OSError("Expected a real directory")
    return path


def file_checksum(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000:
        raise UserError("同步目录中存在同名文件，无法安全覆盖。", 409)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_output(source, target, expected):
    try:
        previous = file_checksum(target)
    except FileNotFoundError:
        previous = None
    if previous is not None:
        if previous != expected:
            raise UserError("同步目录中存在同名文件，无法安全覆盖。", 409)
        return True

    fd, staged = tempfile.mkstemp(prefix=".gpm-", suffix=".tmp", dir=target.parent)
    try:
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as copied, os.fdopen(open_local_fd(source), "rb") as original:
            for chunk in iter(lambda: original.read(CHUNK), b""):
                copied.write(chunk)
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise UserError("本机作品的校验值已变化，无法转存。", 409)
        if os.path.lexists(target):
            raise UserError("同步目录中存在同名文件，无法安全覆盖。", 409)
        os.replace(staged, target)
        return False
    finally:
        Path(staged).unlink(missing_ok=True)


class DriveArchive:
    def __init__(self, store, production):
        self.store = store
        self.production = production
        self.settings = store.root / "drive-archive.json"
        self.lock = threading.Lock()

    def root(self, value):
        try:
            if not isinstance(value, str) or len(value) > 4096 or not Path(value).is_absolute():
                raise ValueError
            root = directory(Path(value).resolve(strict=True))
            data = self.store.root.resolve()
            if root == root.parent or root.is_relative_to(data) or data.is_relative_to(root):
                raise ValueError
            return root
        except (OSError, ValueError, RuntimeError):
            raise UserError("同步目录不可用，请在 Google Drive 桌面版中重新选择。", 409) from None

    def configure(self, account_id, path):
        self.store.one(account_id)
        with self.lock:
            root = self.root(path)
            records = read_object(self.settings)
            for other_id, record in records.items():
                if other_id == account_id:
                    continue
                other = Path(record["root"])
                if root.is_relative_to(other) or other.is_relative_to(root):
                    raise UserError("这个同步目录已关联其他账号，请选择目标账号的目录。", 409)
            if records.get(account_id, {}).get("root") != str(root):
                records[account_id] = {"root": str(root)}
                write_private_json(self.settings, records)

    def state(self, account_id):
        self.store.one(account_id)
        with self.lock:
            record = read_object(self.settings).get(account_id, {})
            configured = bool(record.get("root"))
            projects = []
            try:
                root = self.root(record.get("root"))
                workspace = root / WORKSPACE
                if os.path.lexists(workspace):
                    for item in directory(workspace).iterdir():
                        try:
                            if project_name(item.name) == item.name:
                                projects.append(directory(item).name)
                        except (UserError, OSError):
                            continue
            except (UserError, OSError):
                return {"configured": configured, "ready": False, "projects": [], "last_project": None}
            return {"configured": configured, "ready": True, "projects": sorted(projects, key=str.casefold),
                    "last_project": record.get("last_project")}

    def archive(self, job_id, account_id, project):
        self.store.one(account_id)
        name = project_name(project)
        row, source = self.production.artifact_path(job_id)
        expected = row["artifact"].get("sha256", "")
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise UserError("本机作品缺少校验值，无法转存。", 409)
        title = INVALID_NAME.sub("_", row["title"]).strip(" .") or "output"
        title = title.encode("utf-8")[:120].decode("utf-8", "ignore").rstrip(" .")
        if DEVICE_NAME.fullmatch(title.split(".", 1)[0].rstrip()):
            title = "_" + title
        filename = f"{title} - {job_id[:12]}{source.suffix}"
        with self.lock:
            records = read_object(self.settings)
            record = records.get(account_id, {})
            root = self.root(record.get("root"))
            try:
                workspace = directory(root / WORKSPACE, create=True)
                target = directory(workspace / name, create=True) / filename
                reused = copy_output(source, target, expected)
            except OSError:
                raise UserError("无法写入同步目录，请检查 Google Drive 桌面版的状态后重试。", 409) from None
            if record.get("last_project") != target.parent.name:
                record["last_project"] = target.parent.name
                write_private_json(self.settings, records)
            receipt = {"account_id": account_id, "project": target.parent.name, "filename": target.name}
            current = row.get("drive_archive", {})
            if not all(current.get(key) == value for key, value in receipt.items()):
                self.production.update(job_id, drive_archive={**receipt, "copied_at": timestamp()})
            return {**receipt, "reused": reused}
