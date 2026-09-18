#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
rm -f "$ROOT/socks5.url"
echo
echo "  SOCKS5 OFF"
echo "  Upstream goes direct to opencode.ai"
echo
echo "  Restart ./zen-gateway if it is already running."
echo
