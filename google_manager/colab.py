"""Run the installed Colab client with just the selected account's grant."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from .accounts import UserError
from .colab_auth import prepare_authorization


GPUS = ("CPU", "T4", "L4", "A100", "H100", "G4")
TPUS = ("V5E1", "V6E1")
HARDWARE = GPUS + TPUS
HIGH_RAM_CHOICES = frozenset({"CPU", "T4", "A100", "H100", "G4"})
REPO = Path(__file__).resolve().parent.parent


class ProviderError(UserError):
    def __init__(self, message, status=502, uncertain=False):
        super().__init__(message, status)
        self.uncertain = uncertain


def region_from_provider(value):
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if (url.scheme != "https" or url.username is not None or url.password is not None
                or url.port not in (None, 443) or not host.endswith((".prod.r.colab.dev", ".googleusercontent.com"))):
            return None
        prefix, separator, _ = host.partition(".prod.")
        match = re.fullmatch(r"([a-z]+(?:-[a-z]+)+[0-9]+)(?:-[a-z])?", prefix.rsplit(".", 1)[-1])
        return match[1] if separator and match else None
    except (TypeError, ValueError):
        return None


class Colab:
    def __init__(self, python=None):
        if getattr(sys, "frozen", False):
            self.command = [sys.executable, "--colab-worker"]
        else:
            interpreter = python or os.environ.get("GOOGLE_MANAGER_COLAB_PYTHON") or self.find_python()
            self.command = [interpreter, "-E", "-B", "-m", "google_manager.colab_worker"] if interpreter else None

    @staticmethod
    def find_python():
        if importlib.util.find_spec("colab_cli") is not None:
            return sys.executable
        command = shutil.which("colab") or str(Path.home() / ".local" / "bin" / "colab")
        python = Path(command).resolve().with_name("python")
        if python.is_file():
            return str(python)
        return None

    def _request(self, action, token, **options):
        if not self.command:
            raise ProviderError("请先安装 google-colab-cli，或在启动时指定其 Python 路径。", 503)
        try:
            process = subprocess.run(
                self.command,
                cwd=REPO,
                input=json.dumps({"action": action, "access_token": token, **options}),
                text=True,
                capture_output=True,
                timeout=120,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            raise ProviderError("Colab 响应超时，请刷新这个账号后核对实例。", 504, uncertain=True) from None
        except OSError:
            raise ProviderError("无法启动已安装的 Colab 客户端。", 503) from None
        try:
            result = json.loads(process.stdout)
            if process.returncode != 0 or not isinstance(result, dict):
                raise ValueError
        except ValueError:
            raise ProviderError("Colab 客户端响应异常，请刷新账号后核对。", uncertain=True) from None
        status = result.get("status")
        if type(status) is int and status >= 400:
            raise ProviderError(result.get("error") or "Colab 请求未完成。", status, result.get("uncertain", False))
        return result

    def observe(self, token):
        return self._request("observe", token)

    def authorize(self):
        return prepare_authorization(self.command, REPO)

    def start(self, token, gpu, nonce, *, high_mem=False):
        return self._request("start", token, gpu=gpu, nonce=nonce, high_mem=high_mem)["instance_id"]

    def inspect_start(self, token, gpu, nonce, *, high_mem=False):
        return self._request("inspect_start", token, gpu=gpu, nonce=nonce, high_mem=high_mem)["instance_id"]

    def stop(self, token, instance_id):
        self._request("stop", token, instance_id=instance_id)

    def inspect(self, token, instance_id):
        return self._request("inspect", token, instance_id=instance_id)

    def editor_connection(self, token, instance_id, project):
        return self._request("editor_connection", token, instance_id=instance_id, project=project)

    def submit_job(self, token, instance_id, job_id, spec):
        return self._request("submit_job", token, instance_id=instance_id, job_id=job_id, spec=spec)

    def review_job(self, token, instance_id, job_id, kind, destination):
        return self._request("review_job", token, instance_id=instance_id, job_id=job_id,
                             kind=kind, destination=str(destination))
