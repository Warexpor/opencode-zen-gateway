# OpenCode Zen gateway

Local reverse proxy so other coding harnesses (Cursor, Cline, Continue, Aider, Grok CLI, Claude Code, and anything OpenAI-compatible) can call [OpenCode Zen](https://opencode.ai/docs/zen) with your Zen API key.

The official OpenCode agent sends identity headers on every Zen request. This process forwards your call to `https://opencode.ai/zen` and adds those headers so the request looks like the OpenCode CLI.

Listen: `http://127.0.0.1:8789/v1`

Python 3 stdlib only. PySocks is optional and only required when SOCKS5 is on.

## Run

Windows: `START.cmd`

Unix: `sh start.sh` or `python3 gateway.py`

Health: `http://127.0.0.1:8789/healthz`

Paste your Zen key into the harness as the OpenAI (or Anthropic) API key. The gateway forwards `Authorization`. Set `OPENCODE_API_KEY` only if you want the gateway to override whatever the harness sends.

| Variable | Default | Meaning |
|---|---|---|
| `OPENCODE_API_KEY` | (empty) | Overrides the harness API key |
| `ZEN_GATEWAY_PORT` | `8789` | Listen port |
| `ZEN_GATEWAY_CLIENT` | `cli` | `x-opencode-client` |
| `ZEN_GATEWAY_PROJECT` | `global` | `x-opencode-project` |
| `ZEN_GATEWAY_OPENCODE_VERSION` | `1.18.4` | Used in `User-Agent: opencode/<ver>` |
| `ZEN_GATEWAY_USER_AGENT` | `opencode/<ver>` | Full User-Agent override |
| `ZEN_GATEWAY_SOCKS5` | (empty) | Force a SOCKS5 URL, or `off` to go direct |

## Point a harness at it

Base URL: `http://127.0.0.1:8789/v1`

Model ids are Zen ids. An `opencode/` prefix is stripped.

Examples: `deepseek-v4-flash-free`, `mimo-v2.5-free`, `big-pickle`

Current list: [opencode.ai/docs/zen](https://opencode.ai/docs/zen)

**Cursor.** Settings → Models → OpenAI API key = Zen key, Override Base URL = `http://127.0.0.1:8789/v1`. Cursor cloud often blocks localhost. Put a public HTTPS tunnel in front if that happens.

**Cline / Continue / Aider / LiteLLM.** OpenAI provider, same base URL and key, model = a Zen id.

**Claude Code.** Only models Zen serves on `/zen/v1/messages` (Claude family). `ANTHROPIC_BASE_URL=http://127.0.0.1:8789` and `ANTHROPIC_API_KEY` = Zen key. Free oa-compat models will not work on that path.

## SOCKS5

Harnesses still connect to `127.0.0.1:8789`. Only the gateway’s calls to `opencode.ai` go through the proxy. This does not set Windows-wide `HTTP_PROXY`.

- `proxy-on.bat` — ON, default `socks5://127.0.0.1:10808`
- `proxy-off.bat` — OFF, direct to Zen
- `proxy-status.bat` — current switch

Optional: `proxy-on.bat socks5://user:pass@127.0.0.1:1080`

Restart `START.cmd` after flipping. Then `python -m pip install -r requirements.txt` if PySocks is missing. `START.cmd` prefers `py -3` so a random venv `python` on PATH is not used.

## What it injects

Matches `packages/opencode/src/session/llm/request.ts` in [anomalyco/opencode](https://github.com/anomalyco/opencode):

- `User-Agent: opencode/<version>`
- `x-opencode-client: cli`
- `x-opencode-project: global`
- `x-opencode-session` — stable per API key (or passthrough)
- `x-opencode-request` — new `msg_…` per call

Zen `inference-cost` SSE frames are stripped so clients that expect a plain OpenAI stream do not break. `Retry-After` on 429s is forwarded.

## Limits

This does not raise Zen’s free-tier cap. Free models are IP-day limited on Zen’s side. Paid Zen balance does not buy extra free-model quota. After `FreeUsageLimitError`, wait until UTC midnight or switch off a `-free` model.

## Tests

```
python -m unittest tests.test_gateway -v
```

## Share zip

`PACK.cmd` (or `python pack.py`) writes `dist/opencode-zen-gateway-*.zip`: runnable files only. No `.git`, logs, `socks5.url`, `.env`, or tests.

## License

MIT
