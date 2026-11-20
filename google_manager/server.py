"""Loopback-only HTTP service for Google Perks Manager."""

import argparse
import html
import hmac
import json
import mimetypes
import os
import re
import secrets
import signal
import stat
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import __version__
from .accounts import UserError, label, write_private_json
from .agent_service import AGENT_HEADER, READ_COMMANDS, RUNTIME_FILE, execute, read_command, redact_email
from .local_files import open_local_fd, owned
from .manager import Manager
from .production import ACTIVE


ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 16 * 1024
MAX_CONTACT_QR = 2 * 1024 * 1024
BIND = "127.0.0.1"
PRODUCT_ID = "com.heige.google-manager"
PRODUCTS = {"zh-CN": "谷歌羊毛管理器", "en": "Google Perks Manager"}
CONTACT_ROUTE = "/brand/wechat-contact.png"
UPDATE_HEADER = "X-Google-Manager-Update"
CONTENT_SECURITY = (
    "default-src 'self'; img-src 'self' data:; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


def token_matches(expected, presented):
    return bool(expected and presented and hmac.compare_digest(expected.encode(), presented.encode()))


class LocalServer(ThreadingHTTPServer):
    def __init__(self, manager, port, web_root=ROOT / "web", *, desktop=False, update_token=None):
        self.manager = manager
        self.web_root = Path(web_root).resolve()
        self.desktop = desktop
        self.agent_token = secrets.token_urlsafe(32)
        self.update_token = update_token
        self.gate = threading.Lock()
        self.mutations = 0
        self.updating = False
        super().__init__((BIND, port), Handler)

    @contextmanager
    def agent_discovery(self):
        path = self.manager.store.root / RUNTIME_FILE
        write_private_json(path, {"port": self.server_port, "pid": os.getpid(), "token": self.agent_token})
        try:
            yield
        finally:
            path.unlink(missing_ok=True)

    @contextmanager
    def mutation(self):
        with self.gate:
            if self.updating:
                raise UserError("正在安装更新，请在应用重启后重试。", 409)
            self.mutations += 1
        try:
            yield
        finally:
            with self.gate:
                self.mutations -= 1

    def prepare_update(self):
        with self.gate:
            if self.updating:
                return
            if self.mutations:
                raise UserError("有账号或创作操作正在处理。", 409)
            accounts = self.manager.store.all()
            if any(row.get("operation_pending") or self.manager.oauth.connecting(row["id"]) for row in accounts):
                raise UserError("请先处理正在等待的账号授权或 Colab 操作。", 409)

            production = self.manager.production
            if not production.lock.acquire(blocking=False):
                raise UserError("请等待正在处理的创作任务。", 409)
            try:
                if production.working or any(job["state"] in ACTIVE for job in production._rows()):
                    raise UserError("请先核对进行中或待取回的创作任务。", 409)
            finally:
                production.lock.release()
            self.updating = True

    def cancel_update(self):
        with self.gate:
            self.updating = False

    def contact_qr(self):
        folder = self.manager.store.root / "branding"
        if folder.is_symlink():
            return None
        path = folder / "wechat-contact.png"
        try:
            fd = open_local_fd(path)
            with os.fdopen(fd, "rb") as image:
                info = os.fstat(image.fileno())
                if not stat.S_ISREG(info.st_mode) or not owned(path, info, private=False) or info.st_size > MAX_CONTACT_QR:
                    return None
                data = image.read(MAX_CONTACT_QR + 1)
        except OSError:
            return None
        return data if len(data) <= MAX_CONTACT_QR and data.startswith(b"\x89PNG\r\n\x1a\n") else None


class Handler(BaseHTTPRequestHandler):
    server_version = "GoogleManager"
    sys_version = ""

    def setup(self):
        self.request.settimeout(8)
        super().setup()

    def log_message(self, *_args):
        # OAuth callback URLs contain a one-time code. No access log.
        return

    def response(self, payload, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY)
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            return

    def json_response(self, value, status=200):
        self.response(
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def local_origin(self, *, writing=False):
        port = self.server.server_port
        host = self.headers.get("Host")
        if host not in {f"{BIND}:{port}", f"localhost:{port}"}:
            raise UserError("谷歌羊毛管理器只能通过本机地址访问。", 403)
        origin = self.headers.get("Origin")
        if (writing or origin is not None) and origin != f"http://{host}":
            raise UserError("请求来源不属于当前谷歌羊毛管理器页面。", 403)

    def body(self):
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            raise UserError("请求需使用 JSON 格式。", 415)
        try:
            size = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise UserError("请求长度无效。", 400) from None
        if not 1 <= size <= MAX_BODY:
            raise UserError("请求长度不符合要求。", 413)
        try:
            body = json.loads(self.rfile.read(size))
        except (ValueError, TimeoutError):
            raise UserError("无法读取 JSON 请求。", 400) from None
        if not isinstance(body, dict):
            raise UserError("请求格式不正确。")
        return body

    def request_path(self):
        path = urlsplit(self.path).path
        if not path.startswith("/") or path.startswith("//"):
            raise UserError("页面不存在。", 404)
        return path

    def language(self):
        query = parse_qs(urlsplit(self.path).query, max_num_fields=12)
        candidates = [
            query.get("lang", [None])[0],
            os.environ.get("GOOGLE_MANAGER_LANGUAGE"),
            self.headers.get("Accept-Language", "").split(",", 1)[0].split(";", 1)[0],
        ]
        for candidate in candidates:
            if candidate and candidate.lower().startswith("zh"):
                return "zh-CN"
            if candidate and candidate.lower().startswith("en"):
                return "en"
        return "zh-CN"

    def do_GET(self):
        try:
            self.local_origin()
            path = self.request_path()
            manager = self.server.manager
            if path == "/api/health":
                self.json_response({
                    "status": "ok",
                    "product_id": PRODUCT_ID,
                    "product": PRODUCTS[self.language()],
                    "version": __version__,
                    "has_contact_qr": self.server.contact_qr() is not None,
                })
            elif path == "/api/state":
                self.json_response(manager.state())
            elif path == "/api/production/jobs":
                query = parse_qs(urlsplit(self.path).query, max_num_fields=3)
                account = query.get("account", [None])[0]
                self.json_response({"jobs": manager.production.list(account)})
            elif match := re.fullmatch(r"/api/production/jobs/([a-f0-9]{32})/media", path):
                self.job_media(match[1])
            elif path == "/oauth/callback":
                with self.server.mutation():
                    self.oauth_callback()
            elif unquote(path) == CONTACT_ROUTE:
                image = self.server.contact_qr()
                if image is None:
                    raise UserError("页面不存在。", 404)
                self.response(image, "image/png")
            elif path.startswith("/api/"):
                raise UserError("接口不存在。", 404)
            else:
                self.static(path)
        except UserError as exc:
            self.json_response({"error": str(exc)}, exc.status)
        except (OSError, ValueError, KeyError):
            self.json_response({"error": "本机服务暂时无法读取数据。"}, 500)

    def do_POST(self):
        is_agent = False
        try:
            self.local_origin(writing=True)
            path = self.request_path()
            is_agent = path == "/api/agent"
            if is_agent and not token_matches(self.server.agent_token, self.headers.get(AGENT_HEADER)):
                raise UserError("The local Agent credentials could not be verified.", 403)
            if path in {"/api/update/prepare", "/api/update/cancel"}:
                if not token_matches(self.server.update_token, self.headers.get(UPDATE_HEADER)):
                    raise UserError("更新请求未通过本机验证。", 403)
                if path.endswith("/prepare"):
                    self.server.prepare_update()
                    self.json_response({"ready": True})
                else:
                    self.server.cancel_update()
                    self.json_response({"ready": False})
                return

            body = self.body()
            if is_agent:
                command = read_command(body)
                if command in READ_COMMANDS:
                    status, details = execute(self.server.manager, self.dispatch, command, body)
                else:
                    with self.server.mutation():
                        status, details = execute(self.server.manager, self.dispatch, command, body)
                self.json_response(details, status)
            else:
                with self.server.mutation():
                    status, details = self.dispatch(path, body)
                    result = {**self.server.manager.state(), **details}
                self.json_response(result, status)
        except UserError as exc:
            self.json_response({"error": redact_email(str(exc)) if is_agent else str(exc)}, exc.status)
        except (OSError, ValueError, KeyError):
            self.json_response({"error": "本机服务暂时无法处理请求。"}, 500)

    def dispatch(self, path, body):
        manager = self.server.manager
        if path == "/api/production/jobs":
            return 202, {"job": manager.production.start(body)}
        if match := re.fullmatch(r"/api/production/jobs/([a-f0-9]{32})/refresh", path):
            return 202, {"job": manager.production.refresh(match[1])}
        if path == "/api/accounts":
            account = manager.store.create(body.get("name"), body.get("email"))
            return 201, {"account_id": account["id"]}

        match = re.fullmatch(r"/api/accounts/([a-f0-9]{32})(?:/(.+))?", path)
        if not match:
            raise UserError("接口不存在。", 404)
        account_id, action = match.groups()
        if action == "connect":
            redirect_uri = f"http://{BIND}:{self.server.server_port}/oauth/callback"
            return 200, manager.connect(account_id, body, redirect_uri)
        if action in {"connect/code", "connect/cancel"}:
            attempt = body.get("attempt")
            if not isinstance(attempt, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{43}", attempt):
                raise UserError("授权会话已结束，请重新连接 Google。", 409)
            if action == "connect/code":
                code = body.get("code")
                manager.finish_cli_login(account_id, attempt, code.strip() if isinstance(code, str) else code)
            else:
                with manager.store.account_lock(account_id):
                    manager.oauth.cancel(account_id, attempt)
            return 200, {}
        if action == "import-cli":
            manager.import_cli(account_id, body.get("token_file"))
            return 200, {"message": "已用 Google 核对邮箱并连接这个账号。"}
        if action == "disconnect":
            with manager.store.account_lock(account_id):
                manager.oauth.cancel(account_id)
                manager.store.disconnect(account_id)
            return 200, {"message": "已移除这个账号在本机的授权。"}
        if action == "rename":
            manager.store.update(account_id, name=label(body.get("name"), "账号名称", 48))
            return 200, {}
        if action == "refresh":
            manager.refresh(account_id)
            return 200, {}
        if action == "instances":
            operation = manager.start(account_id, body)
            return 200, {"operation": operation, "message": operation["message"]}
        if action == "instances/stop":
            operation = manager.stop(account_id, body)
            return 200, {"operation": operation, "message": operation["message"]}
        if action and action.startswith("benefits/"):
            benefit_id = unquote(action.removeprefix("benefits/"))
            manager.update_benefit(account_id, benefit_id, body)
            return 200, {}
        raise UserError("接口不存在。", 404)

    def oauth_callback(self):
        query = parse_qs(urlsplit(self.path).query, max_num_fields=12)
        state = query.get("state", [None])
        code = query.get("code", [None])
        status = 200
        lang = self.language()
        english = lang == "en"
        try:
            if len(state) != 1 or not state[0] or len(code) != 1:
                raise UserError("登录响应无法核对，请重新连接。")
            with self.server.mutation():
                self.server.manager.oauth_callback(state[0], code[0], declined="error" in query or not code[0])
            message = ("Google account verified. Return to Google Perks Manager to refresh Colab."
                       if english else "已核对 Google 账号。返回谷歌羊毛管理器刷新 Colab。")
        except UserError as exc:
            status = exc.status
            message = ("Google authorization did not complete. Return to the app and reconnect the account."
                       if english else str(exc))
        except (OSError, ValueError, KeyError):
            status = 500
            message = ("Could not save authorization locally. Return to the app and reconnect."
                       if english else "本机无法保存授权，请回到谷歌羊毛管理器重新连接。")
        return_url = "/"
        if self.server.desktop:
            return_url = "googleperksmanager://return" if os.name == "nt" else "googlemanager://return"
        title = "Google authorization" if english else "Google 授权"
        back = "Return to Google Perks Manager" if english else "返回谷歌羊毛管理器"
        page = (
            f"<!doctype html><html lang='{lang}'><meta charset='utf-8'>"
            f"<title>{PRODUCTS[lang]}</title><main><h1>{title}</h1>"
            f"<p>{html.escape(message)}</p><p><a href='{return_url}'>{back}</a></p></main></html>"
        )
        self.response(page.encode("utf-8"), "text/html; charset=utf-8", status)

    def static(self, path):
        relative = unquote(path).lstrip("/") or "index.html"
        if any(part in {"", ".", ".."} or part.startswith(".") for part in relative.split("/")):
            raise UserError("页面不存在。", 404)
        file = (self.server.web_root / relative).resolve()
        if not file.is_relative_to(self.server.web_root) or not file.is_file():
            raise UserError("页面不存在。", 404)
        media_type = mimetypes.guess_type(file)[0] or "application/octet-stream"
        if media_type.startswith("text/") or media_type in {"application/javascript", "application/json"}:
            media_type += "; charset=utf-8"
        self.response(file.read_bytes(), media_type)

    def do_HEAD(self):
        try:
            self.local_origin()
            match = re.fullmatch(r"/api/production/jobs/([a-f0-9]{32})/media", self.request_path())
            if not match:
                raise UserError("页面不存在。", 404)
            self.job_media(match[1], head=True)
        except (UserError, OSError, ValueError):
            self.send_response(404)
            self.end_headers()

    def job_media(self, job_id, *, head=False):
        job, file = self.server.manager.production.artifact_path(job_id)
        with file.open("rb") as handle:
            size = job["artifact"]["size"]
            start, end = 0, size - 1
            raw_range = self.headers.get("Range")
            if raw_range:
                match = re.fullmatch(r"bytes=([0-9]{0,16})-([0-9]{0,16})", raw_range)
                if match and match[1]:
                    start = int(match[1])
                    end = min(end, int(match[2])) if match[2] else end
                elif match and match[2] and int(match[2]) > 0:
                    start = max(0, size - int(match[2]))
                else:
                    start = size
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            mime = "audio/wav" if job["kind"] == "tts" else "video/mp4"
            self.send_response(206 if raw_range else 200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if raw_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head:
                return
            handle.seek(start)
            remaining = end - start + 1
            try:
                while remaining:
                    chunk = handle.read(min(remaining, 256 * 1024))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except OSError:
                return


def default_data_dir():
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local")
        return local / "HeiGeAi" / "GooglePerksManager"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "谷歌管理器"
    return Path.home() / ".local" / "share" / "google-manager"


def stop_with_desktop(server, parent_pid):
    # Finder can force-quit the window process. Release the account lock then, too.
    if os.name == "nt":
        # The host keeps the redirected pipe open for the lifetime of its window.
        sys.stdin.read(1)
    else:
        while parent_pid > 1 and os.getppid() == parent_pid:
            time.sleep(0.5)
    server.shutdown()


def terminate_service(_signum, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description="Google Perks Manager local service")
    parser.add_argument("--port", type=int, default=18782)
    parser.add_argument("--announce-port", action="store_true", help="向启动本服务的桌面应用报告端口")
    parser.add_argument("--data-dir", default=os.environ.get("GOOGLE_MANAGER_DATA_DIR") or default_data_dir())
    parser.add_argument("--oauth-client", help="Google Desktop OAuth 客户端 JSON 的本机路径")
    args = parser.parse_args()
    parent_pid = os.getppid()
    previous_handler = signal.signal(signal.SIGTERM, terminate_service)
    try:
        manager = Manager(args.data_dir, ROOT / "benefits.json", client_config=args.oauth_client)
        try:
            with LocalServer(manager, args.port, desktop=args.announce_port,
                             update_token=os.environ.get("GOOGLE_MANAGER_UPDATE_TOKEN")) as server, server.agent_discovery():
                if args.announce_port:
                    threading.Thread(target=stop_with_desktop, args=(server, parent_pid), daemon=True).start()
                    print(f"GM_PORT={server.server_port}", flush=True)
                else:
                    print(f"{PRODUCTS['zh-CN']} http://{BIND}:{server.server_port}/", flush=True)
                server.serve_forever()
        finally:
            manager.oauth.close()
            manager.store.close()
    except KeyboardInterrupt:
        return 0
    except (OSError, UserError) as exc:
        print(f"{PRODUCTS['zh-CN']}启动失败。{exc}", file=sys.stderr)
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
