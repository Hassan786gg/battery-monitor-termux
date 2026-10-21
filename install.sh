#!/data/data/com.termux/files/usr/bin/bash
set -eu

if ! command -v pkg >/dev/null 2>&1 || [ -z "${PREFIX:-}" ]; then
    echo "Run this installer inside the Termux app."
    exit 1
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT_SOURCE="$SCRIPT_DIR/live_battery_monitor.py"

if [ ! -f "$SCRIPT_SOURCE" ]; then
    echo "Could not find live_battery_monitor.py beside this installer."
    exit 1
fi

pkg install -y python termux-api
mkdir -p "$PREFIX/bin"
cp "$SCRIPT_SOURCE" "$PREFIX/bin/battery-monitor"
chmod 755 "$PREFIX/bin/battery-monitor"

echo
echo "Installed. Run: battery-monitor"
echo "Make sure the Termux:API Android app is installed from the same source as Termux."
