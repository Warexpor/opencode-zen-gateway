#!/bin/sh
# Back-compat alias. Prefer: ./zen-gateway
exec "$(CDPATH= cd -- "$(dirname "$0")" && pwd)/zen-gateway" "$@"
