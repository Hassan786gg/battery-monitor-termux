#!/usr/bin/env python3
"""Live Android battery dashboard for Termux + Termux:API.

Reads Android battery telemetry through Termux:API. Android's standard API
reports a system health flag, not a reliable battery-wear percentage.
"""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime

POLL_SECONDS = 2
COMMAND = "termux-battery-status"
TTY = sys.stdout.isatty()
USE_COLOR = TTY and not os.environ.get("NO_COLOR") and os.environ.get("TERM", "") != "dumb"
RESET = "\033[0m"
COLORS = {
    "cyan": "\033[96m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "dim": "\033[2m",
    "bold": "\033[1m",
}


def paint(text, color=None):
    if USE_COLOR and color in COLORS:
        return f"{COLORS[color]}{text}{RESET}"
    return str(text)


def read_battery_status():
    """Return (battery_data, error_message)."""
    if shutil.which(COMMAND) is None:
        return None, (
            "Missing termux-battery-status. In Termux run: pkg install termux-api. "
            "Install the Termux:API app from the same source as Termux."
        )

    try:
        result = subprocess.run(
            [COMMAND], capture_output=True, text=True, timeout=10, check=False
        )
    except subprocess.TimeoutExpired:
        return None, "Battery API timed out. Check that Termux:API is installed and open."
    except OSError as exc:
        return None, f"Could not start the battery API: {exc}"

    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "No details returned."
        return None, f"Battery API error: {detail}"

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None, "The battery API returned unreadable data."

    if not isinstance(data, dict):
        return None, "Unexpected battery API response."
    return data, None


def value_or(value, formatter=str):
    if value is None:
        return "n/a"
    try:
        return formatter(value)
    except (TypeError, ValueError, OverflowError):
        return str(value)


def display(data, error=None):
    if TTY:
        print("\033[2J\033[H", end="")

    columns = shutil.get_terminal_size((78, 24)).columns
    total_width = max(40, min(74, columns - 4))
    inner = total_width - 6

    def border(top=False, bottom=False):
        left, right = ("┌", "┐") if top else (("└", "┘") if bottom else ("├", "┤"))
        print("  " + left + "─" * (inner + 2) + right)

    def row(text="", color=None):
        parts = textwrap.wrap(str(text), width=inner) or [""]
        for part in parts:
            print("  │ " + paint(part.ljust(inner), color) + " │")

    print()
    border(top=True)
    row("BATTERY  /  LIVE MONITOR", "bold")
    border()
    row("Updated  " + datetime.now().astimezone().strftime("%Y-%m-%d  %H:%M:%S %Z"), "dim")

    if error:
        row()
        row("Could not read battery data", "yellow")
        row(error)
        border(bottom=True)
        print(paint(f"  Retrying in {POLL_SECONDS}s  ·  Ctrl+C to quit", "dim"))
        return

    percentage = data.get("percentage")
    if percentage is None:
        level, scale = data.get("level"), data.get("scale")
        if isinstance(level, (int, float)) and isinstance(scale, (int, float)) and scale:
            percentage = round(level * 100 / scale)

    try:
        pct = max(0, min(100, float(percentage))) if percentage is not None else None
    except (TypeError, ValueError):
        pct = None

    if pct is None:
        percent_text, gauge, charge_color = "n/a", "░" * 12, "dim"
    else:
        filled = round(pct / 100 * 12)
        gauge = "█" * filled + "░" * (12 - filled)
        percent_text = f"{pct:.0f}%"
        charge_color = "green" if pct >= 60 else ("yellow" if pct >= 25 else "red")

    status = str(data.get("status") or "n/a").replace("_", " ")
    plugged = str(data.get("plugged") or "n/a").replace("_", " ")
    health = str(data.get("health") or "n/a").replace("_", " ")
    health_color = "green" if health == "GOOD" else ("yellow" if health == "n/a" else "red")

    row()
    row(f"CHARGE   {percent_text:>4}   {gauge}", charge_color)
    row(f"STATE    {status}   ·   {plugged}", "cyan")
    border()
    row(f"SYSTEM HEALTH FLAG   {health}", health_color)
    row("TEMPERATURE          " + value_or(data.get("temperature"), lambda x: f"{float(x):.1f} °C"))
    row("VOLTAGE              " + value_or(data.get("voltage"), lambda x: f"{float(x) / 1000:.3f} V"))
    row("CURRENT              " + value_or(data.get("current"), lambda x: f"{float(x) / 1000:.0f} mA"))
    row("AVERAGE CURRENT      " + value_or(data.get("current_average"), lambda x: f"{float(x) / 1000:.0f} mA"))
    row("CHARGE COUNTER       " + value_or(data.get("charge_counter"), lambda x: f"{float(x) / 1000:.0f} mAh"))
    row("CYCLE COUNT          " + value_or(data.get("cycle")))
    border(bottom=True)
    print(paint("  Live telemetry only · Android doesn't expose a standard battery-wear %", "dim"))
    print(paint(f"  Refreshes every {POLL_SECONDS}s  ·  Ctrl+C to quit", "dim"))


def main():
    try:
        while True:
            data, error = read_battery_status()
            display(data, error)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        if TTY:
            print("\033[2J\033[H", end="")
        print("Battery monitor stopped.")


if __name__ == "__main__":
    main()
