#!/usr/bin/env python3
"""Live Android battery dashboard for Termux + Termux:API (no root)."""

import argparse
import ast
from collections import deque
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime
from urllib.error import URLError
from urllib.request import urlopen

VERSION = "1.1.1"
POLL_SECONDS = 2
AVERAGE_WINDOW_SECONDS = 30
COMMAND = "termux-battery-status"
SOURCE_URL = "https://raw.githubusercontent.com/Hassan786gg/battery-monitor-termux/main/live_battery_monitor.py"
MAX_DOWNLOAD = 1024 * 1024
TTY = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"
USE_COLOR = TTY and not os.environ.get("NO_COLOR")
RESET = "\033[0m"
COLORS = {
    "cyan": "\033[96m", "green": "\033[92m", "yellow": "\033[93m",
    "red": "\033[91m", "dim": "\033[2m", "bold": "\033[1m",
}
CONTROL_CODES = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x1f\x7f-\x9f]")


def clean_text(value):
    """Do not let API error messages inject terminal controls."""
    return CONTROL_CODES.sub(" ", str(value))


def paint(text, color=None):
    if USE_COLOR and color in COLORS:
        return f"{COLORS[color]}{text}{RESET}"
    return str(text)


def finite_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def current_number(value):
    """BatteryManager current is a signed int in microamps; reject sentinels."""
    number = finite_number(value)
    if number is None or not (-2147483648 < number <= 2147483647):
        return None
    return number


class CurrentHistory:
    """Sample mean of valid raw current readings in the last 30 seconds."""

    def __init__(self):
        self.samples = deque(maxlen=128)
        self.state = None

    def reset(self):
        self.samples.clear()
        self.state = None

    def with_average(self, data, now=None):
        if not isinstance(data, dict):
            self.reset()
            return data
        now = time.monotonic() if now is None else now
        state = tuple(clean_text(data.get(key) or "").strip().upper()
                      for key in ("status", "plugged"))
        if state != self.state or (self.samples and now < self.samples[-1][0]):
            self.reset()
        self.state = state
        while self.samples and now - self.samples[0][0] > AVERAGE_WINDOW_SECONDS:
            self.samples.popleft()
        current = current_number(data.get("current"))
        if current is None:
            # Never present an old estimate as a fresh reading after API failure.
            self.samples.clear()
        else:
            self.samples.append((now, current))
        enriched = dict(data)
        count = len(self.samples)
        enriched["_app_current_average"] = (
            math.fsum(value / count for _, value in self.samples) if count else None
        )
        enriched["_app_current_samples"] = count
        return enriched


def read_battery_status():
    """Return (battery_data, error_message), including recoverable API errors."""
    if shutil.which(COMMAND) is None:
        return None, (
            "Missing termux-battery-status. Run: pkg install termux-api. "
            "Also install the Termux:API Android app from the same source as Termux."
        )
    try:
        result = subprocess.run(
            [COMMAND], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10, check=False,
        )
    except subprocess.TimeoutExpired:
        return None, "Battery API timed out. Check that the Termux:API Android app is installed."
    except OSError as exc:
        return None, f"Could not start the battery API: {exc}"
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "No details returned."
        return None, f"Battery API error: {detail}"
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None, "The battery API returned unreadable data."
    if not isinstance(data, dict) or not data:
        return None, "The battery API returned no battery information."
    if data.get("error"):
        return None, f"Battery API error: {data['error']}"
    return data, None


def value_or(value, formatter=str, nonnegative=False):
    number = finite_number(value)
    if number is None or (nonnegative and number < 0):
        return "n/a"
    try:
        return formatter(number)
    except (TypeError, ValueError, OverflowError):
        return "n/a"


def display(data, error=None):
    """Build one frame, then repaint once to reduce flicker and stale rows."""
    terminal = shutil.get_terminal_size((78, 24))
    columns = max(8, terminal.columns)
    indent = "  " if columns >= 20 else ""
    # Leave one column spare so terminal autowrap does not add blank lines.
    width = min(72, columns - len(indent) - 1)
    inner = max(1, width - 4)
    lines = []

    def border(top=False, bottom=False):
        left, right = ("┌", "┐") if top else (("└", "┘") if bottom else ("├", "┤"))
        lines.append(indent + left + "─" * (inner + 2) + right)

    def row(text="", color=None):
        parts = textwrap.wrap(clean_text(text), width=inner) or [""]
        for part in parts:
            lines.append(indent + "│ " + paint(part.ljust(inner), color) + " │")

    def footer(text):
        for part in textwrap.wrap(clean_text(text), width=max(1, columns - len(indent) - 1)):
            lines.append(indent + paint(part, "dim"))

    border(top=True)
    row("BATTERY / LIVE MONITOR", "bold")
    row("Developer: Hassan Raza", "dim")
    border()
    row("Updated " + datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"), "dim")

    if error or not isinstance(data, dict):
        row()
        row("Could not read battery data", "yellow")
        row(error or "Unexpected battery API response.")
        border(bottom=True)
        footer(f"Retrying in {POLL_SECONDS}s · Ctrl+C to quit")
    else:
        pct = finite_number(data.get("percentage"))
        if pct is None:
            level, scale = finite_number(data.get("level")), finite_number(data.get("scale"))
            if level is not None and scale is not None and scale > 0:
                pct = finite_number(level / scale * 100)
        if pct is None or pct < 0:
            percent_text, gauge, charge_color = "n/a", "░" * 12, "dim"
        else:
            pct = min(100, pct)
            filled = round(pct / 100 * 12)
            gauge = "█" * filled + "░" * (12 - filled)
            percent_text = f"{pct:.0f}%"
            charge_color = "green" if pct >= 60 else ("yellow" if pct >= 25 else "red")
        status = clean_text(data.get("status") or "n/a").replace("_", " ")
        plugged = clean_text(data.get("plugged") or "n/a").replace("_", " ")
        health = clean_text(data.get("health") or "n/a").replace("_", " ")
        health_color = "green" if health == "GOOD" else ("yellow" if health == "n/a" else "red")
        row()
        row(f"CHARGE   {percent_text:>4}   {gauge}", charge_color)
        row(f"STATE    {status} · {plugged}", "cyan")
        border()
        row(f"SYSTEM HEALTH FLAG   {health}", health_color)
        row("TEMPERATURE          " + value_or(data.get("temperature"), lambda x: f"{x:.1f} °C"))
        row("VOLTAGE              " + value_or(data.get("voltage"), lambda x: f"{x / 1000:.3f} V", True))
        current = current_number(data.get("current"))
        average = current_number(data.get("current_average"))
        row("CURRENT (RAW)        " + value_or(current, lambda x: f"{x / 1000:.0f} mA"))
        if average is not None:
            row("AVG (PHONE/RAW)      " + value_or(average, lambda x: f"{x / 1000:.0f} mA"))
        else:
            average = current_number(data.get("_app_current_average"))
            count = data.get("_app_current_samples", 0)
            row(f"AVG (APP/RAW, {count})".ljust(21) + value_or(average, lambda x: f"{x / 1000:.0f} mA"))
        if current is not None and current < 0 and status.strip().upper() == "CHARGING":
            row("Charging reported; raw current sign is negative.", "yellow")
        row("CHARGE COUNTER       " + value_or(data.get("charge_counter"), lambda x: f"{x / 1000:.0f} mAh", True))
        row("CYCLE COUNT          " + value_or(data.get("cycle"), lambda x: f"{x:.0f}", True))
        border(bottom=True)
        footer("Raw sign is phone-reported. App average: last 30s; count shown.")
        footer("Live telemetry only · Android does not expose a standard battery-wear %")
        footer(f"Refreshes every {POLL_SECONDS}s · Ctrl+C to quit")
    if TTY and len(lines) > max(1, terminal.lines - 1):
        # Do not scroll the whole screen on every refresh in a short window.
        budget = max(1, terminal.lines - 1)
        notice = "Resize for more readings · Ctrl+C to quit"
        lines = lines[:max(0, budget - 1)]
        lines.append(indent + notice[:max(1, columns - len(indent) - 1)])
    frame = "\n".join(lines)
    if TTY:
        print("\033[H" + frame + "\033[J", end="", flush=True)
    else:
        print(frame, flush=True)


def update():
    """Download, validate, and atomically replace the installed command."""
    prefix = os.environ.get("PREFIX")
    if not prefix or shutil.which("pkg") is None:
        print("Run battery-monitor update inside Termux.", file=sys.stderr)
        return 1
    destination = Path(prefix) / "bin" / "battery-monitor"
    temporary = None
    try:
        print("Checking for the latest battery-monitor…", flush=True)
        with urlopen(SOURCE_URL, timeout=25) as response:
            payload = response.read(MAX_DOWNLOAD + 1)
        if not payload or len(payload) > MAX_DOWNLOAD:
            raise ValueError("Empty or oversized update download")
        source = payload.decode("utf-8")
        tree = ast.parse(source)
        functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
        if not {"main", "display", "update"}.issubset(functions):
            raise ValueError("The download is not a compatible battery-monitor release")
        # Use the actual Termux interpreter, not /usr/bin/env on Android.
        source = f"#!{Path(prefix) / 'bin' / 'python'}\n" + source.split("\n", 1)[1]
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=".battery-monitor.", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(source)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o755)
        os.replace(temporary, destination)
        temporary = None
    except KeyboardInterrupt:
        print("\nUpdate cancelled.", file=sys.stderr)
        return 130
    except (OSError, URLError, UnicodeError, SyntaxError, ValueError) as exc:
        print(f"Update failed: {clean_text(exc)}. Existing installation was not replaced.", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    print("Update complete. Run battery-monitor to open the dashboard.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Live Android battery monitor for Termux.")
    parser.add_argument("--version", action="version", version=f"battery-monitor {VERSION}")
    parser.add_argument("command", nargs="?", choices=["update"], help="Install the latest version and exit")
    args = parser.parse_args(argv)
    if args.command == "update":
        return update()
    history = CurrentHistory()
    try:
        if TTY:
            print("\033[2J\033[H\033[?25l", end="", flush=True)
        while True:
            data, error = read_battery_status()
            if error:
                history.reset()
            else:
                data = history.with_average(data)
            display(data, error)
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nBattery monitor stopped.", flush=True)
    except BrokenPipeError:
        # Prevent Python's shutdown flush from printing a second pipe error.
        try:
            with open(os.devnull, "w") as sink:
                os.dup2(sink.fileno(), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return 0
    finally:
        if TTY:
            try:
                print(RESET + "\033[?25h\n", end="", flush=True)
            except BrokenPipeError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
