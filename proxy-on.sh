#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
URL=${1:-socks5://127.0.0.1:10808}
printf '%s\n' "$URL" > "$ROOT/socks5.url"
echo
echo "  SOCKS5 ON"
echo "  $URL"
echo
echo "  Only this gateway's calls to opencode.ai use the proxy."
echo "  Restart ./zen-gateway if it is already running."
echo
