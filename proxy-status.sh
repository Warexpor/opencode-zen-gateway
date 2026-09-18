#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
echo
if [ -f "$ROOT/socks5.url" ]; then
  echo "  SOCKS5 ON"
  tr -d '\r' < "$ROOT/socks5.url"
else
  echo "  SOCKS5 OFF"
  echo "  Upstream goes direct to opencode.ai"
fi
echo
