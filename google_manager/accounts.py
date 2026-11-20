"""Private local account records and independently scoped credentials."""

import fcntl
import json
import os
import re
import stat
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


class UserError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def empty_observation():
    return {
        "status": "unverified",
        "checked_at": None,
        "balance_cu": None,
        "hourly_cu": None,
        "instances": [],
        "eligible_gpus": [],
        "error": None,
    }


def label(value, name, limit):
    if not isinstance(value, str):
        raise UserError(f"请填写{name}。")
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit or any(ord(c) < 32 or ord(c) == 127 for c in cleaned):
        raise UserError(f"{name}须填写 1 至 {limit} 个字符。")
    return cleaned


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise UserError("请选择当前用户专用的数据目录，目录权限应为 700。")


def read_private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise UserError("本机数据文件的权限不符合要求。")
        return json.load(handle)


def write_private_json(path, data):
    fd, temp = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_object(path):
    try:
        data = read_private_json(path)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise UserError("本机数据无法读取，请核对权限与文件。", 500) from None
    if not isinstance(data, dict):
        raise UserError("本机数据格式错误。", 500)
    return data


class AccountStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().absolute()
        private_directory(self.root)
        self.profiles = self.root / "profiles"
        private_directory(self.profiles)
        self.lock = threading.RLock()
        self._locks = {}
        lock_fd = os.open(self.root / ".instance.lock", os.O_CREAT | os.O_NOFOLLOW | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(lock_fd)
            raise UserError("这个数据目录已有一个谷歌管理器在运行。") from None
        self.lock_fd = lock_fd

    def close(self):
        os.close(self.lock_fd)

    def _load(self):
        return read_object(self.root / "accounts.json")

    def _save(self, data):
        write_private_json(self.root / "accounts.json", data)

    def all(self):
        with self.lock:
            return list(self._load().values())

    def one(self, account_id):
        if not re.fullmatch(r"[a-f0-9]{32}", account_id):
            raise UserError("找不到这个账号。", 404)
        with self.lock:
            account = self._load().get(account_id)
            if account is None:
                raise UserError("找不到这个账号。", 404)
            return account

    def account_lock(self, account_id):
        with self.lock:
            self.one(account_id)
            return self._locks.setdefault(account_id, threading.RLock())

    def create(self, name, email):
        account_name = label(name, "账号名称", 48)
        account_email = label(email, "Google 邮箱", 254)
        if not re.fullmatch(r"[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+", account_email):
            raise UserError("请填写完整的 Google 邮箱。")
        with self.lock:
            data = self._load()
            if any(item["email"].casefold() == account_email.casefold() for item in data.values()):
                raise UserError("这个邮箱已添加。", 409)
            account_id = uuid.uuid4().hex
            data[account_id] = {
                "id": account_id,
                "name": account_name,
                "email": account_email,
                "observation": empty_observation(),
                "benefit_status": {},
                "instance_names": {},
                "subject": None,
                "auth_error": None,
            }
            self._save(data)
            return data[account_id]

    def update(self, account_id, **values):
        with self.lock:
            account = self.one(account_id)
            account.update(values)
            data = self._load()
            data[account_id] = account
            self._save(data)
            return account

    def _token_file(self, account_id):
        return self.profiles / account_id / "oauth.json"

    def credential(self, account_id):
        self.one(account_id)
        return read_object(self._token_file(account_id))

    def has_credential(self, account_id):
        return self._token_file(account_id).is_file()

    def save_credential(self, account_id, data):
        self.one(account_id)
        path = self._token_file(account_id)
        private_directory(path.parent)
        write_private_json(path, data)

    def bind(self, account_id, subject, credential):
        with self.lock:
            account = self.one(account_id)
            if account["subject"] and account["subject"] != subject:
                raise UserError("Google 账号身份与这个档案的授权记录不一致。", 409)
            if any(a["id"] != account_id and a["subject"] == subject for a in self.all()):
                raise UserError("这个 Google 账号已关联其他档案。", 409)
            self.update(account_id, subject=subject, auth_error=None)
            self.save_credential(account_id, credential)

    def disconnect(self, account_id):
        with self.lock:
            self.one(account_id)
            self._token_file(account_id).unlink(missing_ok=True)
            self.update(
                account_id,
                observation=empty_observation(),
                instance_names={},
                subject=None,
                auth_error=None,
                operation_pending=False,
            )
