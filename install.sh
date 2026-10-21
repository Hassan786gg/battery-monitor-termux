#!/data/data/com.termux/files/usr/bin/bash
set -eu

if ! command -v pkg >/dev/null 2>&1 || [ -z "${PREFIX:-}" ]; then
    echo "Run this installer inside the Termux app." >&2
    exit 1
fi

case "${1:-}" in
    ""|--no-run) ;;
    *) echo "Usage: bash install.sh [--no-run]" >&2; exit 2 ;;
esac

pkg install -y python termux-api curl </dev/null
mkdir -p "$PREFIX/bin"
DOWNLOAD="$(mktemp "$PREFIX/bin/.battery-monitor.XXXXXX")"
trap 'rm -f "$DOWNLOAD"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
curl -fsSL --connect-timeout 15 --max-time 90 --retry 3 \
    https://raw.githubusercontent.com/Hassan786gg/battery-monitor-termux/main/live_battery_monitor.py \
    -o "$DOWNLOAD"
# Validate before replacing a working installation and fix Android's shebang.
python - "$DOWNLOAD" "$PREFIX/bin/python" <<'PY'
import ast
from pathlib import Path
import sys
path = Path(sys.argv[1])
try:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
except (OSError, UnicodeError, SyntaxError) as exc:
    raise SystemExit(f"Invalid download: {exc}. Existing installation was not replaced.")
functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
if not {"main", "display", "update"}.issubset(functions):
    raise SystemExit("Invalid battery-monitor download; existing installation was not replaced.")
path.write_text("#!" + sys.argv[2] + "\n" + source.split("\n", 1)[1], encoding="utf-8")
PY
chmod 755 "$DOWNLOAD"
mv -f "$DOWNLOAD" "$PREFIX/bin/battery-monitor"
trap - EXIT INT TERM

echo
echo "Installed. Run: battery-monitor"
echo "Update later: battery-monitor update"
echo "The Termux:API Android companion app is also required (same source as Termux)."
if [ "${1:-}" != --no-run ]; then
    exec "$PREFIX/bin/battery-monitor" </dev/null
fi
