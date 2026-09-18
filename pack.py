#!/usr/bin/env python3
"""Build a shareable zip: runtime files only, no git/logs/keys/switch state."""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "0.2.1"
DIST = ROOT / "dist"
NAME = f"opencode-zen-gateway-{VERSION}"
OUT = DIST / f"{NAME}.zip"

# What a recipient needs to run it. No .git, logs, socks5.url, .env, tests, AGENTS.md.
FILES = [
    "gateway.py",
    "builtin_tools.json",
    "zen-gateway",
    "zen-gateway.cmd",
    "START.cmd",
    "start.sh",
    "install.sh",
    "install.cmd",
    "proxy-on.bat",
    "proxy-off.bat",
    "proxy-status.bat",
    "proxy-on.sh",
    "proxy-off.sh",
    "proxy-status.sh",
    "README.md",
    "CHANGELOG.md",
    "LICENSE",
    "requirements.txt",
    ".env.example",
]


def main() -> int:
    missing = [f for f in FILES if not (ROOT / f).is_file()]
    if missing:
        raise SystemExit("missing: " + ", ".join(missing))
    DIST.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in FILES:
            zf.write(ROOT / rel, arcname=f"{NAME}/{rel}")
    print(OUT)
    print("files:")
    with zipfile.ZipFile(OUT) as zf:
        for info in zf.infolist():
            print(f"  {info.file_size:6d}  {info.filename}")
    print(f"bytes {OUT.stat().st_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
