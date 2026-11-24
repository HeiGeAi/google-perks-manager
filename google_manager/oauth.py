"""Account-scoped Google authorization, with Desktop OAuth and Colab CLI flows."""

import base64
import hashlib
import json
import math
import secrets
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .accounts import UserError, read_private_json
from .colab_auth import CLI_REDIRECT, LIFETIME, MAX_CODE


AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
COLAB_SCOPE = "https://www.googleapis.com/auth/colaboratory"
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
SCOPES = ("openid", EMAIL_SCOPE, COLAB_SCOPE)
MAX_JSON = 64 * 1024
CODE_TIMEOUT = "连接 Google 授权服务超时。请检查本机网络或系统代理，再获取新授权码。"
CODE_UNREACHABLE = "无法连接 Google 授权服务。请检查本机网络或系统代理，再获取新授权码。"
CODE_REJECTED = "Google 拒绝了这次授权码。请重新获取并粘贴新码。"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def google_json(url, *, form=None, bearer=None):
    headers = {"Accept": "application/json"}
    data = None
    exchanging_code = url == TOKEN_URL and form is not None and form.get("grant_type") == "authorization_code"
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urlencode(form).encode("ascii")
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    request = Request(url, data=data, headers=headers)
    try:
        handlers = [NoRedirect()]
        if getattr(sys, "frozen", False):
            import certifi

            context = ssl.create_default_context()
            context.load_verify_locations(cafile=certifi.where())
            handlers.append(HTTPSHandler(context=context))
        with build_opener(*handlers).open(request, timeout=15) as response:
            payload = response.read(MAX_JSON + 1)
        if len(payload) > MAX_JSON:
            raise ValueError
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError
        return decoded
    except HTTPError as exc:
        if exchanging_code and exc.code == 400:
            try:
                with exc:
                    failure = json.loads(exc.read(MAX_JSON + 1))
            except (OSError, ValueError):
                failure = {}
            if isinstance(failure, dict) and failure.get("error") == "invalid_grant":
                raise UserError(CODE_REJECTED, 401) from None
        if exc.code in {400, 401, 403}:
            raise UserError("Google 未接受授权。请核对账号、Desktop OAuth 客户端和 Colab 权限。", 401) from None
        raise UserError("Google 授权服务暂时无法完成请求。", 502) from None
    except (URLError, ValueError, OSError) as exc:
        if exchanging_code:
            reason = exc.reason if isinstance(exc, URLError) else exc
            if isinstance(reason, TimeoutError):
                raise UserError(CODE_TIMEOUT, 504) from None
            if isinstance(exc, OSError):
                raise UserError(CODE_UNREACHABLE, 502) from None
        raise UserError("Google 授权服务暂时无法连接，请稍后刷新。", 502) from None


def read_desktop_client(path):
    if not isinstance(path, str) or not path.strip():
        raise UserError("登录需要 Google Desktop OAuth 客户端 JSON。请填写下载文件的路径。")
    try:
        client_file = Path(path.strip()).expanduser()
        if not client_file.is_file():
            raise ValueError
        with client_file.open("rb") as handle:
            payload = handle.read(MAX_JSON + 1)
        if len(payload) > MAX_JSON:
            raise ValueError
        config = json.loads(payload)["installed"]
        client_id = config["client_id"]
        client_secret = config.get("client_secret") or ""
        if (
            not isinstance(client_id, str)
            or not client_id.endswith(".apps.googleusercontent.com")
            or not isinstance(client_secret, str)
            or len(client_id) > 512
            or len(client_secret) > 512
        ):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise UserError("无法读取 Google Desktop OAuth 客户端 JSON，请核对文件类型。") from None
    return {"client_id": client_id, "client_secret": client_secret}


def identity(token, expected_email, expected_subject=None):
    user = google_json(USERINFO_URL, bearer=token)
    subject = user.get("sub")
    email = user.get("email")
    if (
        not isinstance(subject, str)
        or not subject
        or not isinstance(email, str)
        or user.get("email_verified") is not True
        or email.casefold() != expected_email.casefold()
        or (expected_subject is not None and subject != expected_subject)
    ):
        raise UserError("Google 登录账号与档案邮箱不一致，请切回正确账号再授权。", 409)
    return subject


def token_record(payload, client, previous=None):
    previous = previous or {}
    access = payload.get("access_token")
    refresh = payload.get("refresh_token") or previous.get("refresh_token")
    scopes = payload.get("scope")
    duration = payload.get("expires_in")
    if (
        not isinstance(access, str) or not access
        or not isinstance(refresh, str) or not refresh
        or not isinstance(duration, (int, float)) or isinstance(duration, bool)
        or not math.isfinite(duration) or duration <= 0
        or str(payload.get("token_type", "Bearer")).casefold() != "bearer"
    ):
        raise UserError("Google 没有返回可持续使用的授权，请重新连接该账号。", 401)
    if scopes is not None:
        granted = set(scopes.split()) if isinstance(scopes, str) else set()
        if COLAB_SCOPE not in granted or not ({EMAIL_SCOPE, "email"} & granted):
            raise UserError("授权缺少 Google 邮箱或 Colab 权限，请核对同意页面。", 401)
    return {
        **client,
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": time.time() + duration,
    }


@dataclass(frozen=True)
class Login:
    account_id: str
    verifier: str
    redirect_uri: str
    client: dict
    deadline: float
    flow: str = "desktop"


class GoogleOAuth:
    def __init__(self):
        self._pending = {}
        self._lock = threading.Lock()

    def _clear_expired(self):
        now = time.monotonic()
        for state, login in list(self._pending.items()):
            if login.deadline < now:
                del self._pending[state]

    def connecting(self, account_id):
        with self._lock:
            self._clear_expired()
            return any(login.account_id == account_id for login in self._pending.values())

    def cancel(self, account_id, attempt=None):
        with self._lock:
            self._cancel(account_id, attempt)

    def _cancel(self, account_id, attempt=None):
        for state, login in list(self._pending.items()):
            if login.account_id == account_id and (attempt is None or attempt == state):
                del self._pending[state]

    def close(self):
        with self._lock:
            self._pending.clear()

    def begin(self, account, path, redirect_uri):
        client = read_desktop_client(path)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=")
        login = Login(account["id"], verifier, redirect_uri, client, time.monotonic() + LIFETIME)
        with self._lock:
            self._clear_expired()
            self._cancel(account["id"])
            self._pending[state] = login
        query = urlencode({
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent select_account",
            "state": state,
            "code_challenge": challenge.decode("ascii"),
            "code_challenge_method": "S256",
            "login_hint": account["email"],
        })
        return {"open_url": f"{AUTHORIZE_URL}?{query}", "flow": "desktop", "attempt": state}

    def begin_cli(self, account, colab):
        url, client, verifier = colab.authorize()
        state = secrets.token_urlsafe(32)
        login = Login(account["id"], verifier, CLI_REDIRECT, client, time.monotonic() + LIFETIME, flow="cli")
        with self._lock:
            self._clear_expired()
            self._cancel(account["id"])
            self._pending[state] = login
        return {"open_url": url, "flow": "cli", "attempt": state}

    def account_for_state(self, state):
        with self._lock:
            self._clear_expired()
            login = self._pending.get(state)
        if login is None or login.flow != "desktop":
            raise UserError("登录链接已过期，请回到谷歌管理器重新连接。", 400)
        return login.account_id

    def consume(self, state):
        with self._lock:
            self._clear_expired()
            login = self._pending.get(state)
            if login is None or login.flow != "desktop":
                raise UserError("登录链接已过期，请回到谷歌管理器重新连接。", 400)
            del self._pending[state]
        return login

    def finish_cli(self, account, state, code):
        if (not isinstance(code, str) or not 0 < len(code) <= MAX_CODE
            or not code.isascii() or any(char.isspace() for char in code)):
            raise UserError("请粘贴 Google 页面显示的完整授权码。")
        with self._lock:
            self._clear_expired()
            login = self._pending.get(state)
            if login is None or login.flow != "cli" or login.account_id != account["id"]:
                raise UserError("授权会话已结束，请重新连接 Google。", 409)
            del self._pending[state]
        return self.exchange(account, login, code)

    def exchange(self, account, login, code):
        form = {
            **login.client,
            "code": code,
            "code_verifier": login.verifier,
            "grant_type": "authorization_code",
            "redirect_uri": login.redirect_uri,
        }
        if not form["client_secret"]:
            del form["client_secret"]
        credential = token_record(google_json(TOKEN_URL, form=form), login.client)
        subject = identity(credential["access_token"], account["email"], account["subject"])
        return subject, credential

    def import_cli(self, account, path):
        if not isinstance(path, str) or not path.strip():
            raise UserError("请选择官方 Colab CLI 的本机授权文件。")
        try:
            data = read_private_json(Path(path).expanduser())
            client = {name: data[name] for name in ("client_id", "client_secret")}
            refresh = data["refresh_token"]
            scopes = data["scopes"]
            if (not isinstance(client["client_id"], str) or len(client["client_id"]) > 512
                or not client["client_id"].endswith(".apps.googleusercontent.com")
                or not isinstance(client["client_secret"], str) or len(client["client_secret"]) > 512
                or not isinstance(refresh, str) or not 0 < len(refresh) <= 4096
                or not isinstance(scopes, list) or COLAB_SCOPE not in scopes or EMAIL_SCOPE not in scopes):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            raise UserError("无法读取官方 Colab CLI 授权文件，或授权缺少邮箱及 Colab 权限。") from None
        form = {**client, "grant_type": "refresh_token", "refresh_token": refresh}
        credential = token_record(google_json(TOKEN_URL, form=form), client, {"refresh_token": refresh})
        return identity(credential["access_token"], account["email"], account["subject"]), credential

    def access(self, account, store):
        saved = store.credential(account["id"])
        if not saved or not account["subject"]:
            raise UserError("请先连接这个 Google 账号。", 401)
        if saved["expires_at"] <= time.time() + 60:
            client = {key: saved[key] for key in ("client_id", "client_secret")}
            form = {
                **client,
                "grant_type": "refresh_token",
                "refresh_token": saved["refresh_token"],
            }
            if not form["client_secret"]:
                del form["client_secret"]
            saved = token_record(google_json(TOKEN_URL, form=form), client, saved)
            identity(saved["access_token"], account["email"], account["subject"])
            store.save_credential(account["id"], saved)
        else:
            identity(saved["access_token"], account["email"], account["subject"])
        return saved["access_token"]
