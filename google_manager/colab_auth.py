"""Private process adapter for the official Colab CLI browser authorization."""

import json
import logging
import os
import select
import subprocess
import sys
from datetime import datetime, timezone
from importlib.resources import files
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .accounts import UserError


LIFETIME = 600
MAX_CODE = 4096
MAX_PACKET = 16 * 1024
UNAVAILABLE = "无法启动 Colab 官方授权，请重新连接 Google。"
REJECTED = "Google 未接受这次授权码。请重新授权并复制新码。"


def google_authorization_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "accounts.google.com" or parts.fragment
        or parts.path not in {"/o/oauth2/auth", "/o/oauth2/v2/auth"}):
        raise ValueError
    params = dict(parse_qsl(parts.query))
    params["prompt"] = "consent select_account"
    return urlunsplit(parts._replace(query=urlencode(params)))


class ColabAuthorization:
    def __init__(self, process):
        self.process = process

    @classmethod
    def start(cls, command, cwd):
        if not command:
            raise UserError(UNAVAILABLE, 503)
        try:
            process = subprocess.Popen(
                [*command, "--authorize"], cwd=cwd, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise UserError(UNAVAILABLE, 503) from None
        worker = cls(process)
        try:
            if not select.select([process.stdout], [], [], 12)[0]:
                raise ValueError
            packet = json.loads(process.stdout.readline(MAX_PACKET))
            return worker, google_authorization_url(packet["url"])
        except (OSError, ValueError, KeyError, TypeError):
            worker.close()
            raise UserError(UNAVAILABLE, 503) from None

    def exchange(self, code):
        try:
            output, _ = self.process.communicate((code + "\n").encode("ascii"), timeout=30)
            if not output or len(output) > MAX_PACKET or self.process.returncode:
                raise ValueError
            packet = json.loads(output)
            return packet["token"], packet["client"]
        except subprocess.TimeoutExpired:
            raise UserError("Google 核对授权码超时。请重新连接 Google。", 504) from None
        except (OSError, ValueError, KeyError, TypeError):
            raise UserError(REJECTED, 401) from None
        finally:
            self.close()

    def close(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def run_authorization():
    # Invoke the upstream CLI's flow without loading or touching its shared token store.
    # Its URL prompt and one-time code travel only through this child's private pipes.
    from colab_cli import auth

    def report_url(message, **_options):
        url = message.strip()
        if url.startswith("https://"):
            print(json.dumps({"url": url}), flush=True)

    def read_code(_prompt):
        if not select.select([sys.stdin], [], [], LIFETIME)[0]:
            raise EOFError
        code = sys.stdin.readline(MAX_CODE + 2).strip()
        if not 0 < len(code) <= MAX_CODE:
            raise ValueError
        return code

    logging.disable(logging.CRITICAL)
    # Google supports granting a subset of the CLI scopes. The app validates
    # the email and Colab grants before binding this profile.
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    auth.typer = SimpleNamespace(echo=report_url)
    auth.input = read_code
    config = json.loads(files("colab_cli").joinpath("oauth_config.json").read_text(encoding="utf-8"))
    credentials = auth._run_remote_flow(config)
    expiry = credentials.expiry
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    print(json.dumps({
        "token": {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expires_in": (expiry - datetime.now(timezone.utc)).total_seconds(),
            "scope": " ".join(credentials.granted_scopes or credentials.scopes),
        },
        "client": {"client_id": credentials.client_id, "client_secret": credentials.client_secret},
    }), flush=True)
