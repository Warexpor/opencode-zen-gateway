# opencode-zen-gateway

Local reverse proxy. Other coding harnesses talk OpenAI- or Anthropic-shaped HTTP here; this process forwards to OpenCode Zen (`https://opencode.ai/zen`) with the caller’s Zen API key and the request shape Console's free tier currently accepts (semver User-Agent, `ses_` session id, `stream: true`, builtin tools in `builtin_tools.json`).

| | |
|--|--|
| Listen | `http://127.0.0.1:8789` (`ZEN_GATEWAY_PORT`) |
| Upstream | `https://opencode.ai/zen` |
| Start | `START.cmd` or `python gateway.py` |
| Tests | `python -m unittest tests.test_gateway -v` |
| SOCKS5 | `proxy-on.bat` / `proxy-off.bat` (local `socks5.url`). Default `socks5://127.0.0.1:10808`. |
| Share | `PACK.cmd` → `dist/opencode-zen-gateway-*.zip` |
| Stack | Python 3 stdlib. PySocks only if SOCKS5 is on. |
