#!/data/data/com.termux/files/usr/bin/bash
set -eu

if ! command -v pkg >/dev/null 2>&1 || [ -z "${PREFIX:-}" ]; then
    echo "Run this installer inside the Termux app."
    exit 1
fi

pkg install -y python termux-api curl </dev/null
mkdir -p "$PREFIX/bin"
DOWNLOAD="$(mktemp "$PREFIX/bin/.battery-monitor.XXXXXX")"
trap 'rm -f "$DOWNLOAD"' EXIT
curl -fsSL --retry 3 \
    https://raw.githubusercontent.com/Hassan786gg/battery-monitor-termux/main/live_battery_monitor.py \
    -o "$DOWNLOAD"
# Validate the download before replacing a working installation.
python -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$DOWNLOAD"
chmod 755 "$DOWNLOAD"
mv -f "$DOWNLOAD" "$PREFIX/bin/battery-monitor"
trap - EXIT

echo
echo "Installed. Next time, run: battery-monitor"
echo "Make sure the Termux:API Android app is installed from the same source as Termux."
exec "$PREFIX/bin/battery-monitor"
