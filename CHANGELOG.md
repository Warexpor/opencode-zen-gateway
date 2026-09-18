# Changelog

## 0.2.1 — 2026-09-18

- Out-of-box launchers: `./zen-gateway` (Linux/macOS) and `zen-gateway.cmd` / `START.cmd` (Windows).
- `install.sh` / `install.cmd` put `zen-gateway` on PATH. Linux SOCKS helpers: `proxy-on.sh` / `proxy-off.sh` / `proxy-status.sh`.
- Loads `.env` next to the gateway if present (does not override already-set env vars).

## 0.2.0 — 2026-09-18

- Free tier now requires a real semver User-Agent, an OpenCode session id, `stream: true`, and the builtin tool set. Default version is `1.18.31`.
- Session and request ids match `Identifier.create` (`ses_`/`msg_` + 12 hex + 14 base62). Other session ids are not forwarded.
- Upstream inference bodies always stream and include `builtin_tools.json`. Non-streaming clients still get one JSON response.

## 0.1.6 — 2026-08-12

- Public repo polish: MIT license, README for third-party clones, LICENSE included in the share zip.

## 0.1.5 — 2026-08-12

- Portable share zip via `PACK.cmd` / `pack.py`. Unix `start.sh`. Zip is runtime-only (no git, logs, keys, SOCKS switch file).

## 0.1.4 — 2026-08-12

- Forward upstream headers including `Retry-After` (429s were dropping it).
- SSE: handle CRLF framing, drop cost frames only when JSON metadata, `Connection: close` so clients do not hang.
- `ZEN_GATEWAY_SOCKS5=off` forces direct (was parsed as hostname `off`). HTTP upstream supported. Chunked request bodies. Tests: `py -3 -m unittest tests.test_gateway -v`.

## 0.1.3 — 2026-08-12

- `START.cmd` prefers `py -3` over Hermes `python` on PATH so SOCKS5 ON finds PySocks. Missing-module error prints that interpreter's pip command.

## 0.1.2 — 2026-08-12

- SOCKS5 on/off via `proxy-on.bat` / `proxy-off.bat` / `proxy-status.bat` (local `socks5.url`, not Windows-wide `setx`). Restart the gateway after flipping.

## 0.1.1 — 2026-08-12

- Upstream SOCKS5 via `ZEN_GATEWAY_SOCKS5` (or `SOCKS_PROXY` / `ALL_PROXY` / `HTTPS_PROXY` / `HTTP_PROXY` when the URL is `socks5://` or `socks5h://`). Supports user/pass and remote DNS. Requires PySocks.

## 0.1.0 — 2026-08-12

- Initial gateway: OpenAI `/v1/chat/completions`, `/v1/responses`, `/v1/models` and Anthropic `/v1/messages` passthrough to OpenCode Zen.
- Injects `x-opencode-client`, `x-opencode-session`, `x-opencode-request`, `x-opencode-project`, and `User-Agent: opencode/<version>`.
- Strips Zen cost SSE frames. Stable session id per API key so Zen sticky routing still works.
