#!/usr/bin/env python3
"""OpenCode Zen gateway for other harnesses.

Any OpenAI- or Anthropic-compatible client points here. Requests go to
https://opencode.ai/zen with the headers and body shape the OpenCode CLI
uses, so Zen's free tier accepts them.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple
from urllib.parse import unquote, urlsplit

HOST = "127.0.0.1"
PORT = int(os.environ.get("ZEN_GATEWAY_PORT", "8789"))
UPSTREAM = os.environ.get("ZEN_GATEWAY_UPSTREAM", "https://opencode.ai/zen").rstrip("/")
OPENCODE_VERSION = os.environ.get("ZEN_GATEWAY_OPENCODE_VERSION", "1.18.31")
CLIENT = os.environ.get("ZEN_GATEWAY_CLIENT", "cli")
PROJECT = os.environ.get("ZEN_GATEWAY_PROJECT", "global")
USER_AGENT = os.environ.get(
    "ZEN_GATEWAY_USER_AGENT", f"opencode/{OPENCODE_VERSION}"
)
ENV_KEY = os.environ.get("OPENCODE_API_KEY", "").strip()
UPSTREAM_TIMEOUT = int(os.environ.get("ZEN_GATEWAY_TIMEOUT", "600"))
ROOT = Path(__file__).resolve().parent
SOCKS_SWITCH = ROOT / "socks5.url"
# Schemas captured from OpenCode CLI 1.18.31 `build` on 2026-09-18.
# Console's free tier rejects inference bodies that do not contain this set.
_OFFICIAL_TOOLS_PATH = ROOT / "builtin_tools.json"

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


# packages/opencode/src/id/id.ts create(prefix, "ascending"):
#   prefix + "_" + 6-byte timestamp hex + 14 base62 chars.
# The free-tier gate rejects anything else (UUIDs, short random ids).
_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_OC_ID = re.compile(r"^(?:ses|msg)_[0-9a-f]{12}[0-9A-Za-z]{14}$")
_id_lock = threading.Lock()
_id_last_ms = 0
_id_counter = 0


def oc_id(prefix: str) -> str:
    global _id_last_ms, _id_counter
    with _id_lock:
        now_ms = int(time.time() * 1000)
        if now_ms != _id_last_ms:
            _id_last_ms = now_ms
            _id_counter = 0
        _id_counter += 1
        counter = _id_counter
        stamp = now_ms
    packed = (stamp * 0x1000 + counter) & ((1 << 48) - 1)
    tail = "".join(_B62[b % 62] for b in secrets.token_bytes(14))
    return f"{prefix}{packed.to_bytes(6, 'big').hex()}{tail}"


def valid_oc_id(value: str) -> bool:
    return bool(_OC_ID.fullmatch(value))


def session_for(auth: str, incoming: Optional[str]) -> str:
    if incoming:
        candidate = incoming.strip()
        if valid_oc_id(candidate) and candidate.startswith("ses_"):
            return candidate
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
    for prefix in ("opencode/", "opencode-zen/", "opencode-go/", "zen-gw/"):
        if model.startswith(prefix):
            return model[len(prefix) :]
    return model


def uses_responses_api(model: str) -> bool:
    """Zen serves Muse Spark (incl. contributor-free) on /v1/responses, not chat."""
    mid = strip_model_prefix(model).lower()
    return mid.startswith("muse-spark")


def uses_messages_api(model: str) -> bool:
    """Zen serves Union Alpha Free on /v1/messages (@ai-sdk/anthropic), not chat/responses."""
    mid = strip_model_prefix(model).lower()
    return mid == "union-alpha"


def load_official_chat_tools() -> List[dict]:
    data = json.loads(_OFFICIAL_TOOLS_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise SystemExit(f"builtin tools missing or empty: {_OFFICIAL_TOOLS_PATH}")
    return data


OFFICIAL_CHAT_TOOLS: List[dict] = load_official_chat_tools()


def tool_name(tool: dict) -> str:
    fn = tool.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]
    name = tool.get("name")
    return name if isinstance(name, str) else ""


def as_responses_tool(tool: dict) -> dict:
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    out = {
        "type": "function",
        "name": fn.get("name"),
        "description": fn.get("description") or "",
        "parameters": fn.get("parameters") or fn.get("input_schema") or {"type": "object", "properties": {}},
    }
    if "strict" in tool:
        out["strict"] = tool["strict"]
    elif "strict" in fn:
        out["strict"] = fn["strict"]
    return out


OFFICIAL_RESPONSES_TOOLS: List[dict] = [as_responses_tool(tool) for tool in OFFICIAL_CHAT_TOOLS]


def merge_tools(existing: object, official: List[dict]) -> List[dict]:
    """Official tools first. Extra harness tools stay, so the model can still call them."""
    current = [tool for tool in existing if isinstance(tool, dict)] if isinstance(existing, list) else []
    official_names = {tool_name(tool) for tool in official}
    extras = [tool for tool in current if tool_name(tool) not in official_names]
    return list(official) + extras


def _content_to_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
        return "".join(parts)
    return str(content)


def chat_to_messages_body(obj: dict) -> dict:
    """Translate OpenAI chat.completions to Zen Anthropic /v1/messages for Union Alpha."""
    model = strip_model_prefix(str(obj.get("model") or ""))
    out: dict = {"model": model, "max_tokens": 1024, "messages": []}
    max_tokens = obj.get("max_tokens") or obj.get("max_completion_tokens") or obj.get("max_output_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        out["max_tokens"] = max_tokens
    system_bits: List[str] = []
    messages = obj.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "user")
            text = _content_to_text(msg.get("content"))
            if role == "system":
                if text:
                    system_bits.append(text)
                continue
            if role not in ("user", "assistant"):
                role = "user"
            out["messages"].append({"role": role, "content": text})
    if not out["messages"]:
        out["messages"] = [{"role": "user", "content": str(obj.get("input") or obj.get("prompt") or "Hello")}]
    if system_bits:
        out["system"] = "\n\n".join(system_bits)
    if obj.get("stream"):
        out["stream"] = True
    return out


def messages_to_chat_completion(obj: dict) -> tuple[int, bytes, str]:
    """Fold an Anthropic Messages response into a non-stream chat.completion."""
    text_parts: List[str] = []
    content = obj.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
    text = "".join(text_parts)
    usage_in = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    chat = {
        "id": str(obj.get("id") or "chatcmpl-zen"),
        "object": "chat.completion",
        "model": str(obj.get("model") or "union-alpha"),
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": text},
        }],
        "usage": {
            "prompt_tokens": int(usage_in.get("input_tokens") or 0),
            "completion_tokens": int(usage_in.get("output_tokens") or 0),
            "total_tokens": int(usage_in.get("input_tokens") or 0) + int(usage_in.get("output_tokens") or 0),
        },
    }
    raw = json.dumps(chat, ensure_ascii=False).encode("utf-8")
    return 200, raw, "application/json"


def chat_to_responses_body(obj: dict) -> dict:
    """Translate an OpenAI chat.completions body to Zen's /v1/responses shape."""
    model = strip_model_prefix(str(obj.get("model") or ""))
    messages = obj.get("messages")
    if isinstance(messages, list) and messages:
        input_items: List[dict] = []
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "user")
            text = _content_to_text(msg.get("content"))
            if role == "system":
                # Responses API prefers instructions for system text when present.
                continue
            input_items.append({"role": role, "content": text})
        system_bits = [
            _content_to_text(m.get("content"))
            for m in messages
            if isinstance(m, dict) and m.get("role") == "system"
        ]
        out: dict = {"model": model, "input": input_items or "Hello"}
        instructions = "\n\n".join(s for s in system_bits if s)
        if instructions:
            out["instructions"] = instructions
    else:
        out = {"model": model, "input": str(obj.get("input") or obj.get("prompt") or "Hello")}

    max_tokens = obj.get("max_tokens") or obj.get("max_completion_tokens") or obj.get("max_output_tokens")
    if isinstance(max_tokens, int) and max_tokens > 0:
        # Muse defaults to high reasoning effort and can burn the entire budget
        # on reasoning_tokens with an empty output — keep a usable floor.
        out["max_output_tokens"] = max(max_tokens, 256)
    else:
        out["max_output_tokens"] = 512
    # Free tier rejects stream=false. Buffer the SSE and fold it back for the client.
    out["stream"] = True
    tools = obj.get("tools")
    if isinstance(tools, list) and tools:
        converted = [as_responses_tool(tool) for tool in tools if isinstance(tool, dict)]
        if converted:
            out["tools"] = converted
            out["tool_choice"] = obj.get("tool_choice") or "auto"
    # Prefer minimal reasoning unless the client already set one — otherwise
    # contributor-free Muse often returns status=incomplete with output=[].
    reasoning = obj.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        out["reasoning"] = {"effort": reasoning["effort"]}
    else:
        out["reasoning"] = {"effort": "minimal"}
    for key in ("temperature", "top_p"):
        if key in obj:
            out[key] = obj[key]
    return out


def responses_output_text(resp: dict) -> str:
    parts: List[str] = []
    for item in resp.get("output") or []:
        if not isinstance(item, dict):
            continue
        for block in item.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("output_text", "text") and isinstance(block.get("text"), str):
                parts.append(block["text"])
    if parts:
        return "".join(parts)
    # Some Zen payloads put a summary string on the response itself.
    for key in ("output_text", "text"):
        if isinstance(resp.get(key), str) and resp[key]:
            return resp[key]
    return ""


def responses_to_chat_completion(resp: dict, want_stream: bool) -> Tuple[int, bytes, str]:
    """Return (status, body, content_type) for a chat.completions client."""
    status = 200
    text = responses_output_text(resp)
    finish = "stop"
    if resp.get("status") == "incomplete":
        finish = "length"
    usage_in = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    chat = {
        "id": resp.get("id") or oc_id("chatcmpl_"),
        "object": "chat.completion",
        "created": int(resp.get("created_at") or time.time()),
        "model": resp.get("model") or "muse-spark",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {"role": "assistant", "content": text},
            }
        ],
        "usage": {
            "prompt_tokens": usage_in.get("input_tokens") or usage_in.get("prompt_tokens") or 0,
            "completion_tokens": usage_in.get("output_tokens") or usage_in.get("completion_tokens") or 0,
            "total_tokens": usage_in.get("total_tokens")
            or (
                (usage_in.get("input_tokens") or 0) + (usage_in.get("output_tokens") or 0)
            ),
        },
    }
    if want_stream:
        chunk_delta = {
            "id": chat["id"],
            "object": "chat.completion.chunk",
            "created": chat["created"],
            "model": chat["model"],
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}],
        }
        chunk_done = {
            "id": chat["id"],
            "object": "chat.completion.chunk",
            "created": chat["created"],
            "model": chat["model"],
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
        }
        sse = (
            f"data: {json.dumps(chunk_delta, ensure_ascii=False)}\n\n"
            f"data: {json.dumps(chunk_done, ensure_ascii=False)}\n\n"
            "data: [DONE]\n\n"
        ).encode("utf-8")
        return status, sse, "text/event-stream"
    return status, json.dumps(chat, ensure_ascii=False).encode("utf-8"), "application/json"


def iter_sse_json(raw: bytes):
    events, rest = split_sse(raw)
    if rest.strip():
        events.append(rest)
    for event in events:
        payload = _sse_data_payload(event)
        if not payload or payload == b"[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def chat_from_sse(raw: bytes, fallback_model: str = "") -> dict:
    """Fold an OpenAI chat or Responses SSE body into one chat.completion."""
    text: List[str] = []
    tool_acc: Dict[int, dict] = {}
    finish = "stop"
    cid = oc_id("chatcmpl_")
    created = int(time.time())
    model = fallback_model
    usage = None
    for obj in iter_sse_json(raw):
        if isinstance(obj.get("id"), str):
            cid = obj["id"]
        if isinstance(obj.get("model"), str):
            model = obj["model"]
        if isinstance(obj.get("created"), int):
            created = obj["created"]
        if isinstance(obj.get("usage"), dict):
            usage = obj["usage"]
        kind = obj.get("type")
        if kind == "response.output_text.delta" and isinstance(obj.get("delta"), str):
            text.append(obj["delta"])
        if kind == "response.completed" and isinstance(obj.get("response"), dict):
            resp = obj["response"]
            got = responses_output_text(resp)
            if got:
                text = [got]
            if isinstance(resp.get("id"), str):
                cid = resp["id"]
            if isinstance(resp.get("model"), str):
                model = resp["model"]
            if isinstance(resp.get("created_at"), int):
                created = resp["created_at"]
            if isinstance(resp.get("usage"), dict):
                usage = resp["usage"]
            if resp.get("status") == "incomplete":
                finish = "length"
        for choice in obj.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]
            delta = choice.get("delta") or choice.get("message") or {}
            if not isinstance(delta, dict):
                continue
            if isinstance(delta.get("content"), str):
                text.append(delta["content"])
            for call in delta.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                idx = int(call.get("index") or 0)
                slot = tool_acc.setdefault(
                    idx,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if isinstance(call.get("id"), str):
                    slot["id"] = call["id"]
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                if isinstance(fn.get("name"), str):
                    slot["function"]["name"] += fn["name"]
                if isinstance(fn.get("arguments"), str):
                    slot["function"]["arguments"] += fn["arguments"]
    message: dict = {"role": "assistant", "content": "".join(text)}
    if tool_acc:
        message["tool_calls"] = [tool_acc[i] for i in sorted(tool_acc)]
        if finish == "stop":
            finish = "tool_calls"
    chat = {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
    }
    if isinstance(usage, dict):
        chat["usage"] = {
            "prompt_tokens": usage.get("input_tokens") or usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("output_tokens") or usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens")
            or ((usage.get("input_tokens") or usage.get("prompt_tokens") or 0) + (usage.get("output_tokens") or usage.get("completion_tokens") or 0)),
        }
    return chat


def responses_from_sse(raw: bytes) -> dict:
    text: List[str] = []
    last = None
    for obj in iter_sse_json(raw):
        if obj.get("type") == "response.completed" and isinstance(obj.get("response"), dict):
            return obj["response"]
        if obj.get("type") == "response.output_text.delta" and isinstance(obj.get("delta"), str):
            text.append(obj["delta"])
        if obj.get("object") == "response":
            last = obj
    if isinstance(last, dict):
        return last
    return {
        "id": oc_id("resp_"),
        "object": "response",
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "".join(text)}]}],
    }


INFERENCE_PATHS = (
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
    "/v1/messages",
)


def apply_free_tier(path: str, body: bytes) -> Tuple[bytes, dict]:
    """Shape an inference body so Console's free-tier gate accepts it.

    The gate (as of 2026-09-18) requires all of:
    - User-Agent opencode/<semver >= 1.18.0>  (header, not body)
    - x-opencode-session in Identifier.create form
    - stream true
    - the OpenCode builtin tool set present (extra tools are allowed)
    """
    meta = {
        "want_stream": False,
        "fold": False,
        "fold_responses": False,
        "model": "",
        "shaped": False,
    }
    np = normalize_path(path)
    if np not in INFERENCE_PATHS or not body:
        return body, meta
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return body, meta
    if not isinstance(obj, dict):
        return body, meta
    meta["model"] = str(obj.get("model") or "")
    meta["want_stream"] = bool(obj.get("stream"))
    if np == "/v1/responses":
        obj["tools"] = merge_tools(obj.get("tools"), OFFICIAL_RESPONSES_TOOLS)
    elif np != "/v1/messages":
        obj["tools"] = merge_tools(obj.get("tools"), OFFICIAL_CHAT_TOOLS)
    else:
        return body, meta
    if "tool_choice" not in obj:
        obj["tool_choice"] = "auto"
    obj["stream"] = True
    meta["shaped"] = True
    meta["fold"] = (not meta["want_stream"]) and np in ("/v1/chat/completions", "/v1/completions")
    meta["fold_responses"] = (not meta["want_stream"]) and np == "/v1/responses"
    return json.dumps(obj, ensure_ascii=False).encode("utf-8"), meta


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
        muse_chat = False
        union_chat = False
        want_stream = False
        fold = False
        fold_responses = False
        model = ""
        try:
            if body:
                obj = json.loads(body)
                if isinstance(obj, dict):
                    model = str(obj.get("model") or "")
                    want_stream = bool(obj.get("stream"))
                    if np == "/v1/chat/completions" and uses_messages_api(model):
                        union_chat = True
                        translated = chat_to_messages_body(obj)
                        body = json.dumps(translated, ensure_ascii=False).encode("utf-8")
                        up_path = f"{_UP_PREFIX}/v1/messages"
                        model = str(translated.get("model") or model)
                        want_stream = bool(translated.get("stream"))
                    elif np == "/v1/chat/completions" and uses_responses_api(model):
                        shaped, meta = apply_free_tier(local, body)
                        obj = json.loads(shaped)
                        want_stream = bool(meta["want_stream"])
                        muse_chat = True
                        translated = chat_to_responses_body(obj)
                        body = json.dumps(translated, ensure_ascii=False).encode("utf-8")
                        up_path = f"{_UP_PREFIX}/v1/responses"
                        model = str(translated.get("model") or model)
                    else:
                        body, meta = apply_free_tier(local, body)
                        want_stream = bool(meta["want_stream"])
                        fold = bool(meta["fold"])
                        fold_responses = bool(meta["fold_responses"])
                        if meta["model"]:
                            model = str(meta["model"])
        except json.JSONDecodeError:
            pass
        headers = self._upstream_headers(body)
        if union_chat and "anthropic-version" not in {k.lower() for k in headers}:
            headers["anthropic-version"] = "2023-06-01"
        log(
            f"{self.command} {np} -> {up_path} model={model or '-'} "
            f"session={headers.get('x-opencode-session', '')[:16]}…"
            f"{' muse->responses' if muse_chat else ''}"
            f"{' union->messages' if union_chat else ''}"
        )
        conn = None
        try:
            conn, resp = open_upstream(self.command, up_path, headers, body or None)
            if muse_chat:
                self._write_muse_chat_response(resp, want_stream)
            elif union_chat:
                self._write_union_chat_response(resp, want_stream)
            else:
                self._write_response(
                    resp,
                    fold_json=fold,
                    fold_responses=fold_responses,
                    fallback_model=model,
                )
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

    def _write_muse_chat_response(self, resp: http.client.HTTPResponse, want_stream: bool) -> None:
        raw = resp.read()
        if resp.status >= 400:
            self.send_response(resp.status)
            self._started = True
            self._cors()
            ctype = resp.getheader("Content-Type") or "application/json"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            retry = resp.getheader("Retry-After")
            if retry:
                self.send_header("Retry-After", retry)
            self.end_headers()
            self.wfile.write(raw)
            log(f"upstream {resp.status}: {raw[:400]!r}")
            return
        ctype_in = (resp.getheader("Content-Type") or "").lower()
        if "text/event-stream" in ctype_in or raw.lstrip().startswith((b"data:", b"event:")):
            chat = chat_from_sse(raw, "muse-spark")
            status, out, ctype = responses_to_chat_completion(
                {
                    "id": chat["id"],
                    "created_at": chat["created"],
                    "status": "incomplete" if chat["choices"][0]["finish_reason"] == "length" else "completed",
                    "model": chat["model"],
                    "output": [{
                        "content": [{
                            "type": "output_text",
                            "text": chat["choices"][0]["message"].get("content") or "",
                        }]
                    }],
                    "usage": chat.get("usage") or {},
                },
                want_stream,
            )
            # Keep tool calls the SSE folder above drops.
            if chat["choices"][0]["message"].get("tool_calls") and not want_stream:
                out = json.dumps(chat, ensure_ascii=False).encode("utf-8")
                ctype = "application/json"
            self.send_response(status)
            self._started = True
            self._cors()
            self.send_header("Content-Type", ctype)
            if want_stream:
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("Connection", "close")
                self.close_connection = True
                self.end_headers()
                self.wfile.write(out)
                self.wfile.flush()
                return
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_error(502, "invalid responses payload from Zen", "gateway_error")
            return
        if not isinstance(obj, dict):
            self._json_error(502, "invalid responses payload from Zen", "gateway_error")
            return
        status, out, ctype = responses_to_chat_completion(obj, want_stream)
        self.send_response(status)
        self._started = True
        self._cors()
        self.send_header("Content-Type", ctype)
        if want_stream:
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            self.wfile.write(out)
            self.wfile.flush()
            return
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _write_union_chat_response(self, resp: http.client.HTTPResponse, want_stream: bool) -> None:
        raw = resp.read()
        if resp.status >= 400:
            self.send_response(resp.status)
            self._started = True
            self._cors()
            ctype = resp.getheader("Content-Type") or "application/json"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            retry = resp.getheader("Retry-After")
            if retry:
                self.send_header("Retry-After", retry)
            self.end_headers()
            self.wfile.write(raw)
            log(f"upstream {resp.status}: {raw[:400]!r}")
            return
        if want_stream:
            # Streaming Anthropic→chat translation is out of scope; fail closed.
            self._json_error(502, "union-alpha chat streaming rewrite is not supported; use /v1/messages", "gateway_error")
            return
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json_error(502, "invalid messages payload from Zen", "gateway_error")
            return
        if not isinstance(obj, dict):
            self._json_error(502, "invalid messages payload from Zen", "gateway_error")
            return
        status, out, ctype = messages_to_chat_completion(obj)
        self.send_response(status)
        self._started = True
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def _write_response(
        self,
        resp: http.client.HTTPResponse,
        fold_json: bool = False,
        fold_responses: bool = False,
        fallback_model: str = "",
    ) -> None:
        ctype = (resp.getheader("Content-Type") or "").lower()
        sse = "text/event-stream" in ctype
        if fold_json or fold_responses:
            raw = resp.read()
            looks_sse = sse or raw.lstrip().startswith(b"data:") or raw.lstrip().startswith(b"event:")
            if resp.status >= 400 or not looks_sse:
                self.send_response(resp.status)
                self._started = True
                self._cors()
                self.send_header("Content-Type", resp.getheader("Content-Type") or "application/json")
                self.send_header("Content-Length", str(len(raw)))
                retry = resp.getheader("Retry-After")
                if retry:
                    self.send_header("Retry-After", retry)
                self.end_headers()
                self.wfile.write(raw)
                if resp.status >= 400:
                    log(f"upstream {resp.status}: {raw[:400]!r}")
                return
            if fold_responses:
                payload = responses_from_sse(raw)
            else:
                payload = chat_from_sse(raw, fallback_model)
            out = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._started = True
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
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
