"""Open an account-scoped Colab application through Google's private SSH transport."""

import http.client
import io
import select
import socket
import socketserver
import threading
from urllib.parse import urlencode, urlsplit

from .accounts import UserError


class WebSocketStream:
    def __init__(self, connection, host):
        self.connection = connection
        self.host = host
        self.buffer = bytearray()

    @property
    def _closed(self):
        # Paramiko checks this socket flag while its transport thread shuts down.
        return not self.connection.connected

    def settimeout(self, timeout):
        self.connection.settimeout(timeout)

    def getpeername(self):
        return self.host, 443

    def send(self, data):
        self.connection.send_binary(bytes(data))
        return len(data)

    def recv(self, size):
        from websocket import ABNF, WebSocketTimeoutException

        while not self.buffer:
            try:
                opcode, data = self.connection.recv_data()
            except WebSocketTimeoutException as error:
                raise socket.timeout() from error
            if opcode == ABNF.OPCODE_CLOSE:
                return b""
            if opcode in {ABNF.OPCODE_BINARY, ABNF.OPCODE_TEXT}:
                self.buffer.extend(data)
        chunk = bytes(self.buffer[:size])
        del self.buffer[:size]
        return chunk

    def close(self):
        self.connection.shutdown()


def editor_request_allowed(header, port):
    lines = header.decode("iso-8859-1").split("\r\n")
    method, path, _ = lines[0].split(" ")
    headers = {}
    for line in lines[1:]:
        if not line:
            break
        name, separator, value = line.partition(":")
        name = name.lower()
        if not separator or line[0].isspace() or (name in headers and name in {"host", "origin", "sec-fetch-site"}):
            return False
        headers[name] = value.strip()
    host = f"127.0.0.1:{port}"
    origin = headers.get("origin")
    if headers.get("host") != host or (origin and origin != f"http://{host}") or not path.startswith("/") or path.startswith("//"):
        return False
    if headers.get("sec-fetch-site") in {"cross-site", "same-site"}:
        return method == "GET" and path == "/" and headers.get("sec-fetch-mode") == "navigate"
    return method not in {"CONNECT", "TRACE"}


class EditorConnection(socketserver.BaseRequestHandler):
    def handle(self):
        peer = self.request
        try:
            with self.server.slots:
                peer.settimeout(12)
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    chunk = peer.recv(8192)
                    if not chunk or len(request) + len(chunk) > 64 * 1024:
                        return
                    request.extend(chunk)
                header, separator, body = request.partition(b"\r\n\r\n")
                if not editor_request_allowed(header, self.server.server_address[1]):
                    peer.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    return
                if not any(line.lower() == b"upgrade: websocket" for line in header.split(b"\r\n")[1:]):
                    lines = [line for line in header.split(b"\r\n") if not line.lower().startswith(b"connection:")]
                    request = b"\r\n".join([*lines, b"Connection: close"]) + separator + body
                channel = self.server.transport.open_channel("direct-tcpip", ("127.0.0.1", self.server.remote_port),
                                                             self.client_address, timeout=12)
                with channel:
                    channel.settimeout(30)
                    channel.sendall(request)
                    while self.server.transport.is_active():
                        readable, _, _ = select.select([peer, channel], [], [], 30)
                        for source in readable:
                            data = source.recv(64 * 1024)
                            if not data:
                                return
                            (channel if source is peer else peer).sendall(data)
        except Exception:
            # Browser disconnects and expired VM channels both close just this connection.
            return


class EditorListener(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, transport, remote_port):
        self.transport = transport
        self.remote_port = remote_port
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(("127.0.0.1", 0), EditorConnection)
        threading.Thread(target=self.serve_forever, daemon=True, name="colab-editor").start()

    def close(self):
        self.transport.close()
        self.shutdown()
        self.server_close()


def connect_editor(details):
    import paramiko
    import websocket
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

    url = urlsplit(details["runtime_url"])
    if (url.scheme != "https" or not url.hostname or not url.hostname.endswith((".prod.r.colab.dev", ".googleusercontent.com"))
            or url.port not in {None, 443} or url.username or url.password or url.query or url.fragment
            or details["remote_port"] not in {18898, 18899}):
        raise ValueError("Invalid endpoint")
    private = Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption())
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private.decode("ascii")))
    connection = websocket.WebSocket()
    transport = None
    try:
        query = urlencode([
            ("colab-runtime-proxy-token", details["proxy_token"]),
            ("colab-client-agent", "colab-cli"),
            ("colab-client-agent", "ssh-mode-proxy"),
        ])
        connection.connect(f"wss://{url.hostname}/colab/ssh?{query}", timeout=15, redirect_limit=0,
                           header=[f"X-Colab-Ssh-Pubkey: {key.get_name()} {key.get_base64()}",
                                   "X-Colab-Client-Agent: colab-cli"])
        transport = paramiko.Transport(WebSocketStream(connection, url.hostname))
        transport.auth_timeout = 15
        transport.start_client(timeout=15)
        transport.auth_publickey("root", key)
        transport.set_keepalive(20)
        return EditorListener(transport, details["remote_port"])
    except BaseException:
        if transport is not None:
            transport.close()
        connection.shutdown()
        raise


class EditorTunnels:
    def __init__(self):
        self.lock = threading.Lock()
        self.listeners = {}

    def open(self, account_id, instance_id, details):
        key = (account_id, instance_id)
        with self.lock:
            listener = self.listeners.get(key)
            if listener is None or not listener.transport.is_active() or listener.remote_port != details["remote_port"]:
                if listener is not None:
                    self.listeners.pop(key).close()
                try:
                    listener = connect_editor(details)
                except Exception:
                    raise UserError("无法建立 Colab 私有应用通道。请核对实例仍在线，或关闭占用它的 SSH 连接后重试。", 502) from None
                self.listeners[key] = listener
            port = listener.server_address[1]
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=12)
            try:
                client.request("GET", "/system_stats" if listener.remote_port == 18898 else "/health")
                if client.getresponse().status != 200:
                    raise OSError("The application did not pass its health check")
            except (OSError, http.client.HTTPException):
                self.listeners.pop(key).close()
                raise UserError("实例上的应用暂时无法打开。请重新部署后再试。", 502) from None
            finally:
                client.close()
            return port

    def close_for(self, account_id, instance_id=None):
        with self.lock:
            for key in list(self.listeners):
                if key[0] == account_id and (instance_id is None or key[1] == instance_id):
                    self.listeners.pop(key).close()
