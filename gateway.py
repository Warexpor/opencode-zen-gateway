#!/usr/bin/env python3
"""OpenCode Zen gateway for other harnesses.

Any OpenAI- or Anthropic-compatible client points here. Requests go to
https://opencode.ai/zen with official OpenCode CLI identity headers so
Zen applies the same free-model path as the OpenCode agent.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import socket
import ssl
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import unquote, urlsplit

HOST = "127.0.0.1"
PORT = int(os.environ.get("ZEN_GATEWAY_PORT", "8789"))
UPSTREAM = os.environ.get("ZEN_GATEWAY_UPSTREAM", "https://opencode.ai/zen").rstrip("/")
OPENCODE_VERSION = os.environ.get("ZEN_GATEWAY_OPENCODE_VERSION", "1.18.4")
CLIENT = os.environ.get("ZEN_GATEWAY_CLIENT", "cli")
PROJECT = os.environ.get("ZEN_GATEWAY_PROJECT", "global")
USER_AGENT = os.environ.get(
    "ZEN_GATEWAY_USER_AGENT", f"opencode/{OPENCODE_VERSION}"
)
ENV_KEY = os.environ.get("OPENCODE_API_KEY", "").strip()
UPSTREAM_TIMEOUT = int(os.environ.get("ZEN_GATEWAY_TIMEOUT", "600"))
ROOT = Path(__file__).resolve().parent
SOCKS_SWITCH = ROOT / "socks5.url"

_UP = urlsplit(UPSTREAM)
_UP_HOST = _UP.hostname or "opencode.ai"
_UP_TLS = (_UP.scheme or "https").lower() != "http"
_UP_PORT = _UP.port or (443 if _UP_TLS else 80)
_UP_PREFIX = (_UP.path or "").rstrip("/")
_SSL_CTX = ssl.create_default_context()

SOCKS_OFF = frozenset({"off", "0", "false", "no", "direct", "none", "disable", "disabled"})

HOP = {
    "host",
    "content-length",
    "connection",
    "transfer-encoding",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "upgrade",
    "accept-encoding",
}

PASSTHROUGH_PREFIXES = (
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/models",
)


class Socks5Target(NamedTuple):
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    rdns: bool


_SOCKS: Optional[Socks5Target] = None
_SOCKS_CONN_CLS: Optional[type] = None


def _socks_switch_url() -> str:
    try:
        return SOCKS_SWITCH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def socks_forced_off(env: Optional[str] = None) -> bool:
    val = (os.environ.get("ZEN_GATEWAY_SOCKS5", "") if env is None else env).strip().lower()
    return val in SOCKS_OFF


def _socks_env_urls() -> list[str]:
    env = os.environ.get("ZEN_GATEWAY_SOCKS5", "").strip()
    if socks_forced_off(env):
        return []
    out: list[str] = []
    if env:
        out.append(env)
    file_url = _socks_switch_url()
    if file_url:
        out.append(file_url)
    return out


def parse_socks5(url: str) -> Optional[Socks5Target]:
    raw = url.strip().strip('"').strip("'")
    if not raw:
        return None
    lower = raw.lower()
    if lower in SOCKS_OFF:
        return None
    rdns = False
    if lower.startswith("socks5h://"):
        rdns = True
    elif lower.startswith("socks5://"):
        rdns = False
    elif "://" in raw:
        return None
    else:
        if ":" not in raw and "." not in raw:
            return None
        raw = "socks5://" + raw
    parsed = urlsplit(raw)
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port or 1080
    user = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    return Socks5Target(host, port, user, password, rdns)


def _plain_conn_cls():
    return http.client.HTTPSConnection if _UP_TLS else http.client.HTTPConnection


def _init_socks() -> None:
    global _SOCKS, _SOCKS_CONN_CLS
    target = None
    for url in _socks_env_urls():
        target = parse_socks5(url)
        if target:
            break
    if not target:
        return
    try:
        import socks as _socks
    except ImportError:
        raise SystemExit(
            "SOCKS5 is set (%s:%d) but this Python has no PySocks.\n"
            "  %s\n"
            "  Install:  \"%s\" -m pip install PySocks\n"
            "  Or run proxy-off.bat."
            % (target.host, target.port, sys.executable, sys.executable)
        )

    base = _plain_conn_cls()

    class Socks5Connection(base):  # type: ignore[valid-type,misc]
        def connect(self) -> None:
            sock = _socks.socksocket()
            if self.timeout is not None:
                sock.settimeout(self.timeout)
            sock.set_proxy(
                _socks.SOCKS5,
                target.host,
                target.port,
                rdns=target.rdns,
                username=target.username,
                password=target.password,
            )
            sock.connect((self.host, self.port))
            if getattr(self, "_tunnel_host", None):
                self.sock = sock
                self._tunnel()
                sock = self.sock
            if _UP_TLS:
                ctx = getattr(self, "_context", None) or ssl.create_default_context()
                self.sock = ctx.wrap_socket(sock, server_hostname=self.host)
            else:
                self.sock = sock
            try:
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass

    _SOCKS = target
    _SOCKS_CONN_CLS = Socks5Connection


_init_socks()

LOG_DIR = ROOT / "logs"
_log_lock = threading.Lock()
_log_fp = None
_session_lock = threading.Lock()
_sessions: Dict[str, str] = {}


def log(msg: str) -> None:
    global _log_fp
    ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with _log_lock:
        try:
            if _log_fp is None:
                LOG_DIR.mkdir(parents=True, exist_ok=True)
                day = datetime.now().strftime("%Y%m%d")
                _log_fp = open(LOG_DIR / f"gateway-{day}.log", "a", encoding="utf-8", buffering=1)
            _log_fp.write(line + "\n")
        except OSError:
            pass


def oc_id(prefix: str, n: int = 24) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return prefix + "".join(secrets.choice(alphabet) for _ in range(n))


def session_for(auth: str, incoming: Optional[str]) -> str:
    if incoming and incoming.strip():
        return incoming.strip()
    key = hashlib.sha256(auth.encode("utf-8", "replace")).hexdigest()[:32]
    with _session_lock:
        sid = _sessions.get(key)
        if not sid:
            sid = oc_id("ses_")
            _sessions[key] = sid
        return sid


def normalize_path(path: str) -> str:
    p = path.split("?", 1)[0]
    while p.startswith("/v1/v1/"):
        p = p[3:]
    if p in ("/chat/completions", "/completions", "/responses", "/messages", "/models"):
        p = "/v1" + p
    return p


def upstream_path(local_path: str, prefix: str = _UP_PREFIX) -> str:
    p = normalize_path(local_path)
    q = ""
    if "?" in local_path:
        q = "?" + local_path.split("?", 1)[1]
    if p.startswith("/v1/"):
        return f"{prefix}/v1/{p[4:]}{q}"
    return f"{prefix}{p}{q}"


def strip_model_prefix(model: str) -> str:
    for prefix in ("opencode/", "opencode-zen/", "opencode-go/"):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


def rewrite_body(path: str, body: bytes) -> bytes:
    if not body:
        return body
    np = normalize_path(path)
    if np not in (
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/responses",
        "/v1/messages",
    ):
        return body
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return body
    if not isinstance(obj, dict):
        return body
    model = obj.get("model")
    if isinstance(model, str):
        stripped = strip_model_prefix(model)
        if stripped == model:
            return body
        obj["model"] = stripped
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return body


def _sse_data_payload(event: bytes) -> bytes:
    data_parts: List[bytes] = []
    for line in event.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n"):
        if line.startswith(b"data:"):
            data_parts.append(line[5:].strip())
    if data_parts:
        return b"\n".join(data_parts)
    return event.strip()


def is_cost_frame(payload: bytes) -> bool:
    raw = _sse_data_payload(payload)
    if not raw or raw == b"[DONE]":
        return False
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(obj, dict):
        return False
    kind = obj.get("type") or obj.get("x-opencode-type")
    if kind in ("inference-cost", "cost"):
        return True
    return "inference-cost" in obj or "x-opencode-type" in obj


def split_sse(buf: bytes) -> Tuple[List[bytes], bytes]:
    norm = buf.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    events: List[bytes] = []
    while b"\n\n" in norm:
        event, norm = norm.split(b"\n\n", 1)
        events.append(event)
    return events, norm


def open_upstream(
    method: str, path: str, headers: Dict[str, str], body: Optional[bytes]
) -> Tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    cls = _SOCKS_CONN_CLS or _plain_conn_cls()
    kwargs: dict = {"timeout": UPSTREAM_TIMEOUT}
    if _UP_TLS:
        kwargs["context"] = _SSL_CTX
    conn = cls(_UP_HOST, _UP_PORT, **kwargs)
    conn.request(method, path, body=body, headers=headers)
    return conn, conn.getresponse()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        log("%s - %s" % (self.address_string(), fmt % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/healthz", "/health"):
            payload = json.dumps(
                {
                    "ok": True,
                    "listen": f"http://{HOST}:{PORT}",
                    "upstream": UPSTREAM,
                    "client": CLIENT,
                    "user_agent": USER_AGENT,
                    "socks5": (
                        {
                            "host": _SOCKS.host,
                            "port": _SOCKS.port,
                            "rdns": _SOCKS.rdns,
                            "auth": bool(_SOCKS.username),
                        }
                        if _SOCKS
                        else None
                    ),
                    "socks5_switch": "on" if _socks_switch_url() else "off",
                }
            ).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()

    def _read_chunked(self) -> bytes:
        chunks: List[bytes] = []
        while True:
            line = self.rfile.readline()
            if not line:
                break
            size_s = line.split(b";", 1)[0].strip()
            try:
                size = int(size_s, 16)
            except ValueError:
                break
            if size == 0:
                self.rfile.readline()
                break
            chunks.append(self.rfile.read(size))
            self.rfile.read(2)
        return b"".join(chunks)

    def _read_body(self) -> bytes:
        te = (self.headers.get("Transfer-Encoding") or "").lower()
        if "chunked" in te:
            return self._read_chunked()
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return b""
        return self.rfile.read(n)

    def _auth(self) -> str:
        if ENV_KEY:
            return ENV_KEY
        incoming = (self.headers.get("Authorization") or "").strip()
        if incoming.lower().startswith("bearer "):
            return incoming[7:].strip()
        if incoming:
            return incoming
        return (self.headers.get("x-api-key") or self.headers.get("api-key") or "").strip()

    def _upstream_headers(self, body: bytes) -> Dict[str, str]:
        auth = self._auth()
        incoming_session = (
            self.headers.get("x-opencode-session")
            or self.headers.get("x-session-id")
            or self.headers.get("x-session-affinity")
        )
        headers: Dict[str, str] = {
            "Host": _UP_HOST,
            "Accept": "text/event-stream, application/json",
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
            "x-opencode-client": CLIENT,
            "x-opencode-project": self.headers.get("x-opencode-project") or PROJECT,
            "x-opencode-session": session_for(auth, incoming_session),
            "x-opencode-request": self.headers.get("x-opencode-request") or oc_id("msg_"),
        }
        if auth:
            headers["Authorization"] = f"Bearer {auth}"
        ctype = self.headers.get("Content-Type")
        if ctype:
            headers["Content-Type"] = ctype
        elif body:
            headers["Content-Type"] = "application/json"
        return headers

    def _json_error(self, status: int, message: str, err_type: str) -> None:
        msg = json.dumps({"error": {"message": message, "type": err_type}}).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(msg)))
        self.end_headers()
        self.wfile.write(msg)

    def _proxy(self) -> None:
        local = self.path
        np = normalize_path(local)
        if not any(np.startswith(p) for p in PASSTHROUGH_PREFIXES):
            self._json_error(404, "not a Zen API path", "not_found")
            return
        body = rewrite_body(local, self._read_body())
        up_path = upstream_path(local)
        headers = self._upstream_headers(body)
        model = ""
        try:
            if body:
                obj = json.loads(body)
                if isinstance(obj, dict):
                    model = str(obj.get("model") or "")
        except json.JSONDecodeError:
            pass
        log(
            f"{self.command} {np} -> {up_path} model={model or '-'} "
            f"session={headers.get('x-opencode-session', '')[:16]}…"
        )
        conn = None
        try:
            conn, resp = open_upstream(self.command, up_path, headers, body or None)
            self._write_response(resp)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log("client disconnected")
        except Exception as exc:
            log(f"upstream error: {exc}")
            if not getattr(self, "_started", False):
                self._json_error(502, str(exc), "gateway_error")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _write_response(self, resp: http.client.HTTPResponse) -> None:
        ctype = (resp.getheader("Content-Type") or "").lower()
        sse = "text/event-stream" in ctype
        self.send_response(resp.status)
        self._started = True
        self._cors()
        for k, v in resp.getheaders():
            if k.lower() in HOP:
                continue
            self.send_header(k, v)
        if not sse:
            data = resp.read()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            if resp.status >= 400:
                log(f"upstream {resp.status}: {data[:400]!r}")
            return
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        buf = b""
        try:
            while True:
                chunk = resp.read(512)
                if not chunk:
                    break
                buf += chunk
                events, buf = split_sse(buf)
                for event in events:
                    if is_cost_frame(event):
                        continue
                    self.wfile.write(event + b"\n\n")
                    self.wfile.flush()
            if buf.strip() and not is_cost_frame(buf):
                self.wfile.write(buf if buf.endswith(b"\n\n") else buf + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log("client disconnected during SSE")


class GatewayServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def server_bind(self) -> None:
        super().server_bind()
        try:
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass


def main() -> int:
    httpd = GatewayServer((HOST, PORT), Handler)
    log(f"opencode-zen-gateway http://{HOST}:{PORT} -> {UPSTREAM}")
    log(f"client={CLIENT} ua={USER_AGENT} key={'env' if ENV_KEY else 'from harness Authorization'}")
    if _SOCKS:
        log(
            "SOCKS5 ON -> %s:%d rdns=%s auth=%s"
            % (_SOCKS.host, _SOCKS.port, _SOCKS.rdns, bool(_SOCKS.username))
        )
    else:
        log("SOCKS5 OFF (direct)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
