#!/bin/sh
# Put `zen-gateway` on PATH (~/.local/bin). Run from the repo or a share zip.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
BIN=${XDG_BIN_HOME:-$HOME/.local/bin}
mkdir -p "$BIN"

TARGET="$BIN/zen-gateway"
cat > "$TARGET" <<EOF
#!/bin/sh
exec "$ROOT/zen-gateway" "\$@"
EOF
chmod +x "$TARGET" "$ROOT/zen-gateway" "$ROOT/start.sh" \
  "$ROOT/proxy-on.sh" "$ROOT/proxy-off.sh" "$ROOT/proxy-status.sh" \
  "$ROOT/install.sh" 2>/dev/null || true

echo
echo "  Installed: $TARGET"
echo "  Points at: $ROOT"
echo
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    echo "  $BIN is not on PATH. Add this to your shell rc:"
    echo "    export PATH=\"$BIN:\$PATH\""
    echo
    ;;
esac
echo "  Then run:  zen-gateway"
echo "  Health:    http://127.0.0.1:8789/healthz"
echo
