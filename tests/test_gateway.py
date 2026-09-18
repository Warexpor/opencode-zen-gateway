#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gateway  # noqa: E402


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Helpers(unittest.TestCase):
    def test_normalize_double_v1(self):
        self.assertEqual(gateway.normalize_path("/v1/v1/chat/completions"), "/v1/chat/completions")
        self.assertEqual(gateway.normalize_path("/chat/completions"), "/v1/chat/completions")
        self.assertEqual(gateway.normalize_path("/v1/models?foo=1"), "/v1/models")

    def test_upstream_path(self):
        self.assertEqual(
            gateway.upstream_path("/v1/chat/completions", "/zen"),
            "/zen/v1/chat/completions",
        )
        self.assertEqual(
            gateway.upstream_path("/v1/v1/models?limit=2", "/zen"),
            "/zen/v1/models?limit=2",
        )

    def test_strip_model_prefix(self):
        self.assertEqual(gateway.strip_model_prefix("opencode/big-pickle"), "big-pickle")
        self.assertEqual(gateway.strip_model_prefix("big-pickle"), "big-pickle")
        self.assertEqual(gateway.strip_model_prefix("opencode-go/kimi-k2.5"), "kimi-k2.5")
        self.assertEqual(gateway.strip_model_prefix("zen-gw/muse-spark-1.3-contributor-free"), "muse-spark-1.3-contributor-free")

    def test_uses_responses_api(self):
        self.assertTrue(gateway.uses_responses_api("muse-spark-1.3-contributor-free"))
        self.assertTrue(gateway.uses_responses_api("zen-gw/muse-spark-1.2"))
        self.assertFalse(gateway.uses_responses_api("big-pickle"))
        self.assertFalse(gateway.uses_responses_api("mimo-v2.5-free"))

    def test_chat_to_responses_and_back(self):
        chat = {
            "model": "zen-gw/muse-spark-1.3-contributor-free",
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Say hi"},
            ],
            "max_tokens": 64,
            "stream": True,
        }
        translated = gateway.chat_to_responses_body(chat)
        self.assertEqual(translated["model"], "muse-spark-1.3-contributor-free")
        self.assertEqual(translated["max_output_tokens"], 256)
        self.assertTrue(translated["stream"])
        self.assertEqual(translated["reasoning"], {"effort": "minimal"})
        self.assertEqual(translated["instructions"], "Be brief.")
        self.assertEqual(translated["input"], [{"role": "user", "content": "Say hi"}])

        fake = {
            "id": "resp_test",
            "created_at": 1700000000,
            "status": "completed",
            "model": "muse-spark-1.3-contributor-free",
            "output": [{"content": [{"type": "output_text", "text": "hello"}]}],
            "usage": {"input_tokens": 3, "output_tokens": 1},
        }
        status, body, ctype = gateway.responses_to_chat_completion(fake, want_stream=False)
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/json")
        out = json.loads(body.decode())
        self.assertEqual(out["choices"][0]["message"]["content"], "hello")

        status, sse, ctype = gateway.responses_to_chat_completion(fake, want_stream=True)
        self.assertEqual(ctype, "text/event-stream")
        self.assertIn(b"data: ", sse)
        self.assertIn(b"[DONE]", sse)

    def test_rewrite_body_strips_prefix_only_when_needed(self):
        raw = json.dumps({"model": "opencode/big-pickle", "messages": []}).encode()
        out = json.loads(gateway.rewrite_body("/v1/chat/completions", raw))
        self.assertEqual(out["model"], "big-pickle")
        same = json.dumps({"model": "big-pickle"}).encode()
        self.assertEqual(gateway.rewrite_body("/v1/chat/completions", same), same)

    def test_parse_socks5(self):
        t = gateway.parse_socks5("socks5://127.0.0.1:10808")
        self.assertEqual((t.host, t.port, t.rdns), ("127.0.0.1", 10808, False))
        t = gateway.parse_socks5("socks5h://u:p@10.0.0.1:9050")
        self.assertEqual((t.host, t.port, t.username, t.password, t.rdns), ("10.0.0.1", 9050, "u", "p", True))
        t = gateway.parse_socks5("127.0.0.1:10808")
        self.assertEqual((t.host, t.port), ("127.0.0.1", 10808))
        self.assertIsNone(gateway.parse_socks5("http://127.0.0.1:8080"))
        self.assertIsNone(gateway.parse_socks5("off"))
        self.assertIsNone(gateway.parse_socks5("direct"))
        crlf = gateway.parse_socks5("socks5://127.0.0.1:10808\r\n")
        self.assertEqual(crlf.port, 10808)

    def test_socks_forced_off(self):
        self.assertTrue(gateway.socks_forced_off("off"))
        self.assertTrue(gateway.socks_forced_off("DIRECT"))
        self.assertFalse(gateway.socks_forced_off("socks5://127.0.0.1:10808"))

    def test_session_stable_per_key(self):
        a = gateway.session_for("key-a", None)
        b = gateway.session_for("key-a", None)
        c = gateway.session_for("key-b", None)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(gateway.valid_oc_id(a))
        kept = gateway.session_for("x", a)
        self.assertEqual(kept, a)
        rejected = gateway.session_for("fresh-key", "ses_custom")
        self.assertNotEqual(rejected, "ses_custom")
        self.assertTrue(gateway.valid_oc_id(rejected))

    def test_apply_free_tier_tools_and_stream(self):
        raw = json.dumps(
            {
                "model": "big-pickle",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": False,
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "custom_tool", "parameters": {"type": "object"}},
                    }
                ],
            }
        ).encode()
        out_b, meta = gateway.apply_free_tier("/v1/chat/completions", raw)
        out = json.loads(out_b)
        self.assertTrue(out["stream"])
        self.assertTrue(meta["fold"])
        self.assertFalse(meta["want_stream"])
        names = [tool["function"]["name"] for tool in out["tools"]]
        self.assertEqual(names[:11], [gateway.tool_name(tool) for tool in gateway.OFFICIAL_CHAT_TOOLS])
        self.assertEqual(names[-1], "custom_tool")
        translated = gateway.chat_to_responses_body(out)
        self.assertTrue(translated["stream"])
        self.assertIn("grep", [tool["name"] for tool in translated["tools"]])

    def test_split_sse_crlf(self):
        events, rest = gateway.split_sse(b'data: {"a":1}\r\n\r\ndata: {"b":2}\r\n\r\npartial')
        self.assertEqual(events, [b'data: {"a":1}', b'data: {"b":2}'])
        self.assertEqual(rest, b"partial")

    def test_cost_frame_json_only(self):
        self.assertTrue(gateway.is_cost_frame(b'data: {"type":"inference-cost","cost":0.01}'))
        self.assertTrue(gateway.is_cost_frame(b'data: {"x-opencode-type":"cost"}'))
        self.assertFalse(gateway.is_cost_frame(b"data: [DONE]"))
        self.assertFalse(
            gateway.is_cost_frame(
                b'data: {"choices":[{"delta":{"content":"mention inference-cost here"}}]}'
            )
        )


class ProxyHTTP(unittest.TestCase):
    def setUp(self):
        self.seen = {}
        parent = self

        class Up(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(me):
                if me.path.startswith("/zen/v1/models"):
                    body = json.dumps({"data": [{"id": "big-pickle"}]}).encode()
                    me.send_response(429)
                    me.send_header("Content-Type", "application/json")
                    me.send_header("Retry-After", "7")
                    me.send_header("Content-Length", str(len(body)))
                    me.end_headers()
                    me.wfile.write(body)
                    return
                me.send_response(404)
                me.end_headers()

            def do_POST(me):
                n = int(me.headers.get("Content-Length", "0") or 0)
                raw = me.rfile.read(n)
                parent.seen["headers"] = {k.lower(): v for k, v in me.headers.items()}
                parent.seen["path"] = me.path
                parent.seen["body"] = json.loads(raw.decode())
                sse = (
                    b'data: {"choices":[{"delta":{"content":"hi"}}]}\r\n\r\n'
                    b'data: {"type":"inference-cost","usd":0}\r\n\r\n'
                    b"data: [DONE]\r\n\r\n"
                )
                me.send_response(200)
                me.send_header("Content-Type", "text/event-stream")
                me.send_header("Connection", "close")
                me.end_headers()
                me.wfile.write(sse)

        self.up = ThreadingHTTPServer(("127.0.0.1", 0), Up)
        self.up.allow_reuse_address = True
        threading.Thread(target=self.up.serve_forever, daemon=True).start()
        self.gw_port = _free_port()
        env = os.environ.copy()
        env.pop("OPENCODE_API_KEY", None)
        env["ZEN_GATEWAY_SOCKS5"] = "off"
        env["ZEN_GATEWAY_PORT"] = str(self.gw_port)
        env["ZEN_GATEWAY_UPSTREAM"] = f"http://127.0.0.1:{self.up.server_address[1]}/zen"
        env["ZEN_GATEWAY_TIMEOUT"] = "5"
        log_path = ROOT / "logs" / "test-gateway-stdout.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fp = open(log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "gateway.py")],
            env=env,
            stdout=self._log_fp,
            stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                urlopen(f"http://127.0.0.1:{self.gw_port}/healthz", timeout=0.3)
                break
            except Exception:
                if self.proc.poll() is not None:
                    self._log_fp.flush()
                    self.fail(f"gateway exited {self.proc.returncode}: {log_path.read_text(encoding='utf-8')}")
                time.sleep(0.05)
        else:
            self.fail("gateway did not start")

    def tearDown(self):
        self.proc.kill()
        self.proc.wait(timeout=3)
        self._log_fp.close()
        self.up.shutdown()

    def test_health(self):
        raw = urlopen(f"http://127.0.0.1:{self.gw_port}/healthz").read()
        data = json.loads(raw)
        self.assertTrue(data["ok"])
        self.assertIsNone(data["socks5"])

    def test_retry_after_forwarded(self):
        try:
            urlopen(f"http://127.0.0.1:{self.gw_port}/v1/models")
            self.fail("expected 429")
        except HTTPError as e:
            self.assertEqual(e.code, 429)
            self.assertEqual(e.headers.get("Retry-After"), "7")

    def test_chat_headers_and_sse_cost_strip(self):
        req = Request(
            f"http://127.0.0.1:{self.gw_port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "opencode/big-pickle",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer oc-test",
            },
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            body = resp.read()
        self.assertIn(b"hi", body)
        self.assertNotIn(b"inference-cost", body)
        self.assertIn(b"[DONE]", body)
        self.assertEqual(self.seen["path"], "/zen/v1/chat/completions")
        self.assertEqual(self.seen["body"]["model"], "big-pickle")
        h = self.seen["headers"]
        self.assertEqual(h.get("user-agent"), gateway.USER_AGENT)
        self.assertEqual(h.get("x-opencode-client"), "cli")
        self.assertTrue(h.get("x-opencode-session", "").startswith("ses_"))
        self.assertTrue(h.get("x-opencode-request", "").startswith("msg_"))
        self.assertTrue(gateway.valid_oc_id(h.get("x-opencode-session", "")))
        self.assertTrue(gateway.valid_oc_id(h.get("x-opencode-request", "")))
        self.assertEqual(h.get("authorization"), "Bearer oc-test")
        names = [tool["function"]["name"] for tool in self.seen["body"]["tools"]]
        self.assertIn("grep", names)
        self.assertIn("glob", names)
        self.assertTrue(self.seen["body"]["stream"])

    def test_nonstream_client_gets_json(self):
        req = Request(
            f"http://127.0.0.1:{self.gw_port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "big-pickle",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                }
            ).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer oc-test"},
            method="POST",
        )
        with urlopen(req, timeout=5) as resp:
            self.assertIn("application/json", resp.headers.get("Content-Type", ""))
            out = json.loads(resp.read())
        self.assertEqual(out["choices"][0]["message"]["content"], "hi")
        self.assertTrue(self.seen["body"]["stream"])

    def test_404_json(self):
        try:
            urlopen(f"http://127.0.0.1:{self.gw_port}/v1/nope")
            self.fail("expected 404")
        except HTTPError as e:
            self.assertEqual(e.code, 404)
            data = json.loads(e.read())
            self.assertEqual(data["error"]["type"], "not_found")


if __name__ == "__main__":
    unittest.main()
