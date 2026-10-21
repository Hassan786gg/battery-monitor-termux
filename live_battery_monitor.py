#!/usr/bin/env python3
"""Live Android battery dashboard for Termux + Termux:API (no root)."""

import argparse
import ast
from collections import deque
from itertools import islice
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
import unicodedata
from datetime import datetime
from urllib.error import URLError
from urllib.request import urlopen

VERSION = "1.3.0"
POLL_SECONDS = 2
AVERAGE_WINDOW_SECONDS = 30
COMMAND = "termux-battery-status"
POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")
TYPEC_ROOT = Path("/sys/class/typec")
SOURCE_URL = "https://raw.githubusercontent.com/Hassan786gg/battery-monitor-termux/main/live_battery_monitor.py"
MAX_DOWNLOAD = 1024 * 1024
TTY = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"
USE_COLOR = TTY and not os.environ.get("NO_COLOR")
RESET = "\033[0m"
COLORS = {
    # Restrained ANSI palette: readable on light and dark Termux themes.
    "cyan": "\033[36m", "green": "\033[32m", "yellow": "\033[33m",
    "red": "\033[31m", "dim": "\033[2m", "bold": "\033[1m",
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


def battery_power_watts(data):
    """Signed battery-side estimate: mV * microamps / 1e9, not charger input."""
    if not isinstance(data, dict):
        return None
    voltage = finite_number(data.get("voltage"))
    current = current_number(data.get("current"))
    if voltage is None or not (0 < voltage <= 2147483647) or current is None:
        return None
    return finite_number((voltage / 1000) * (current / 1000000))


def read_power_io(supply_root=None, typec_root=None):
    """Best-effort kernel measurements. Never writes sysfs or requests root."""
    supply_root = Path(supply_root) if supply_root is not None else POWER_SUPPLY_ROOT
    typec_root = Path(typec_root) if typec_root is not None else TYPEC_ROOT
    result = {"input": None, "output": None, "ports": [], "roles": [], "reason": ""}
    denied = False
    truncated = False

    def text(path):
        nonlocal denied
        try:
            with path.open("r", encoding="utf-8") as handle:
                return handle.read(256).strip()
        except PermissionError:
            denied = True
        except (OSError, UnicodeError):
            pass
        return ""

    def nodes(root):
        nonlocal denied, truncated
        try:
            found = list(islice(root.iterdir(), 33))
            truncated = truncated or len(found) > 32
            return sorted(found[:32])
        except PermissionError:
            denied = True
        except OSError:
            pass
        return []

    def micro(path, positive=False):
        raw = finite_number(text(path))
        if raw is None or not (0 <= raw <= 1e12) or (positive and raw == 0):
            return None
        return raw / 1000000

    for node in nodes(supply_root):
        kind = text(node / "type").upper()
        if not (kind.startswith("USB") or kind in ("MAINS", "WIRELESS")):
            continue
        if finite_number(text(node / "online")) not in (1, 2):
            continue
        volts = micro(node / "voltage_now", positive=True)
        amps = micro(node / "current_now")
        watts = micro(node / "power_now")
        method = "power_now" if watts is not None else None
        if watts is None and volts is not None and amps is not None:
            watts = finite_number(volts * amps)
            method = "V*I estimate" if watts is not None else None
        result["ports"].append({
            "supply": clean_text(node.name), "kind": kind,
            "voltage_v": volts, "current_a": amps, "watts": watts,
            "method": method, "limit_watts": micro(node / "input_power_limit"),
        })

    active_ports = 0
    for node in nodes(typec_root):
        if not re.fullmatch(r"port\d+", node.name):
            continue
        try:
            if not (typec_root / (node.name + "-partner")).is_dir():
                continue
        except OSError:
            continue
        active_ports += 1
        raw = text(node / "power_role").lower()
        selected = re.findall(r"\[(sink|source)\]", raw)
        role = selected[0] if len(selected) == 1 else (raw if raw in ("sink", "source") else None)
        if role:
            result["roles"].append({"port": node.name, "role": role})

    usb = [port for port in result["ports"] if port["kind"].startswith("USB")]
    # Do not combine several supply aliases, guess a connector mapping, use a
    # configured limit as live draw, or infer USB direction from battery status.
    if (not truncated and len(usb) == 1 and len(result["ports"]) == 1
            and active_ports == 1 and len(result["roles"]) == 1
            and usb[0]["watts"] is not None):
        direction = "input" if result["roles"][0]["role"] == "sink" else "output"
        result[direction] = dict(usb[0], port=result["roles"][0]["port"])
        result["reason"] = "USB power role and supply mapping identified; opposite flow is not measured."
    elif denied:
        result["reason"] = "Android denied access to some power telemetry; no permission changes attempted."
    elif truncated:
        result["reason"] = "Too many telemetry nodes for an unambiguous bounded probe."
    elif result["ports"]:
        result["reason"] = "Live values or an unambiguous connected USB source/sink mapping are unavailable."
    else:
        result["reason"] = "No readable online external power telemetry; unsupported, restricted, or disconnected."
    return result


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


def cell_width(text):
    """Terminal cells, not Python characters (no extra Termux dependency)."""
    return sum(1 if char == "\ufe0f" else
               0 if unicodedata.combining(char) or unicodedata.category(char) in ("Cf", "Mn", "Me")
               else 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1
               for char in text)


def fit_text(text, width, pad=False):
    """Sanitize and fit a single line without splitting wide characters."""
    text = "".join(char for char in clean_text(text)
                   if unicodedata.category(char) != "Cf")
    width = max(0, width)
    if cell_width(text) > width:
        used, clipped = 0, []
        for char in text:
            cells = cell_width(char)
            if used + cells > max(0, width - 1):
                break
            clipped.append(char)
            used += cells
        text = "".join(clipped) + ("…" if width else "")
    return text + (" " * (width - cell_width(text)) if pad else "")


def render_frame(data, error=None, columns=78, rows=None, animation_step=None, updated_at=None):
    """Return a complete frame; rows=None keeps all readings for redirected logs.

    Choose rows by importance before drawing, never slice a finished frame.
    Leave the last terminal column and row unused to prevent autowrap/scroll.
    """
    available = max(0, columns - 1)
    budget = None if rows is None else max(0, rows - 1)
    if not available or budget == 0:
        return ""
    indent = "  " if columns >= 40 else ""
    width = min(74, available - len(indent))
    framed = width >= 16 and (budget is None or budget >= 5)
    inner = width - 4 if framed else available
    entries = []

    def row(text="", color=None, priority=5):
        entries.append((text, color, priority))

    def metric(label, value, color=None, priority=3):
        # Keep values readable on phones instead of wrapping padded labels.
        if inner < 44:
            label = {"SYSTEM HEALTH FLAG": "HEALTH FLAG", "BATTERY POWER (EST)": "BATT W (EST)",
                     "TEMPERATURE": "TEMP", "CURRENT (RAW)": "RAW CURRENT",
                     "DEVICE INPUT W": "USB INPUT W", "DEVICE OUTPUT W": "USB OUTPUT W",
                     "CHARGE COUNTER": "COUNTER"}.get(label, label)
        label_width = min(20, max(5, inner // 2))
        value = fit_text(value, max(1, inner - label_width - 1))
        row(fit_text(label, label_width, pad=True) + " " + value, color, priority)

    # Animation is deliberately confined to this decorative activity mark;
    # telemetry values and timestamps remain tied to the last API poll.
    activity = ""
    if animation_step is not None and inner >= 24:
        activity = "  " + "·∘°"[int(animation_step) % 3]
    row(f"BATTERY / LIVE MONITOR  v{VERSION}{activity}", "bold", 1 if inner >= 16 else 8)
    updated = (updated_at or datetime.now().astimezone()).strftime("%H:%M:%S %Z")
    row("Developer: Hassan Raza · Updated " + updated, "dim", 8)
    failed = bool(error) or not isinstance(data, dict)
    if failed:
        row("Could not read battery data", "yellow", 0)
        # Error prose can wrap, but is still subject to the frame's height budget.
        message = clean_text(error or "Unexpected battery API response.")
        for part in textwrap.wrap(message, width=max(1, inner))[:8]:
            row(part, None, 2)
    else:
        pct = finite_number(data.get("percentage"))
        if pct is None:
            level, scale = finite_number(data.get("level")), finite_number(data.get("scale"))
            if level is not None and scale is not None and scale > 0:
                pct = finite_number(level / scale * 100)
        if pct is None or pct < 0:
            percent_text, charge_color, pct = "n/a", "dim", None
        else:
            pct = min(100, pct)
            percent_text = f"{pct:.0f}%"
            charge_color = "green" if pct >= 60 else ("yellow" if pct >= 25 else "red")
        gauge_size = min(24, max(0, inner - 16))
        filled = round(pct / 100 * gauge_size) if pct is not None else 0
        gauge = "━" * filled + "─" * (gauge_size - filled)
        charge = f"▌ CHARGE  {percent_text:>4}  {gauge}".rstrip()
        if inner < 16:
            charge = percent_text + (" CHARGE" if inner >= 10 else "")
        row(charge, charge_color, 0)
        status = clean_text(data.get("status") or "n/a").replace("_", " ")
        plugged = clean_text(data.get("plugged") or "n/a").replace("_", " ")
        health = clean_text(data.get("health") or "n/a").replace("_", " ")
        health_color = "green" if health == "GOOD" else ("yellow" if health == "n/a" else "red")
        row(f"STATE    {status} · {plugged}", "cyan", 2)
        metric("SYSTEM HEALTH FLAG", health, health_color, 3)
        metric("TEMPERATURE", value_or(data.get("temperature"), lambda x: f"{x:.1f} °C"), priority=3)
        current = current_number(data.get("current"))
        if current is not None and current < 0 and status.strip().upper() == "CHARGING":
            row("Charging reported; raw current sign is negative.", "yellow", 3)
        metric("BATTERY POWER (EST)", value_or(battery_power_watts(data), lambda x: f"{abs(x):.2f} W"), "cyan", 4)
        row("Battery-side magnitude; not charger input.", "dim", 4)
        # Section rules are ordinary prioritized rows: they appear with their
        # reading when there is room, but disappear before data is squeezed.
        row("──── CURRENT / BATTERY ────", "dim", 4)
        metric("CURRENT (RAW)", value_or(current, lambda x: f"{x / 1000:.0f} mA"), priority=4)
        average = current_number(data.get("current_average"))
        if average is not None:
            metric("AVG (PHONE/RAW)", value_or(average, lambda x: f"{x / 1000:.0f} mA"), priority=5)
        else:
            average = current_number(data.get("_app_current_average"))
            count = data.get("_app_current_samples", 0)
            metric(f"AVG (APP/RAW, {count})", value_or(average, lambda x: f"{x / 1000:.0f} mA"), priority=5)
        metric("VOLTAGE", value_or(data.get("voltage"), lambda x: f"{x / 1000:.3f} V", True), priority=5)
        io_data = data.get("_power_io") or {}
        row("──── DEVICE POWER ────", "dim", 5)
        for direction in ("input", "output"):
            reading = io_data.get(direction)
            metric(f"DEVICE {direction.upper()} W", value_or(reading.get("watts"), lambda x: f"{x:.2f} W")
                   if reading is not None else "n/a (not measured)", "cyan", 5)
            if reading is not None:
                volts = value_or(reading.get("voltage_v"), lambda x: f"{x:.3f} V")
                amps = value_or(reading.get("current_a"), lambda x: f"{x:.3f} A")
                row(f"{reading['supply']}/{reading['port']}: {volts} · {amps} · {reading['method']}", "dim", 6)
        if not io_data.get("input") and not io_data.get("output"):
            for reading in io_data.get("ports", [])[:2]:
                if reading.get("watts") is not None:
                    row(f"PORT {reading['supply']}: {reading['watts']:.2f} W (direction unknown)", None, 6)
            row("I/O unavailable? Run: battery-monitor diagnose", "dim", 7)
        for reading in io_data.get("ports", [])[:2]:
            if reading.get("limit_watts") is not None:
                row(f"{reading['supply']} LIMIT: {reading['limit_watts']:.2f} W (not live draw)", "dim", 6)
        row("──── DEVICE LIMITS ────", "dim", 6)
        metric("DEVICE MAX W", "n/a (not exposed)", priority=6)
        metric("CHARGE COUNTER", value_or(data.get("charge_counter"), lambda x: f"{x / 1000:.0f} mAh", True), priority=6)
        metric("CYCLE COUNT", value_or(data.get("cycle"), lambda x: f"{x:.0f}", True), priority=6)
        row("Raw sign is phone-reported. App average: last 30s; count shown.", "dim", 7)
        row("Live telemetry only · No standard battery-wear %", "dim", 7)

    footer = f"{'Retry' if failed else 'Live'} {POLL_SECONDS}s · Ctrl+C to quit"
    # Two border lines and one footer are reserved before choosing content.
    capacity = len(entries) if budget is None else max(0, budget - (3 if framed else 1))
    # Rules are enhancement rows, never a reason to hide telemetry. On a
    # normal-height phone display, omit the whole set unless all three fit;
    # taller displays and redirected output get the stronger hierarchy.
    section_rows = [i for i, (text, _, _) in enumerate(entries)
                    if text.startswith("──── ")]
    if budget is not None and section_rows and len(entries) > capacity:
        entries = [entry for i, entry in enumerate(entries) if i not in section_rows]
        capacity = len(entries) if budget is None else max(0, budget - (3 if framed else 1))
    hidden = max(0, len(entries) - capacity)
    selected = set(sorted(range(len(entries)), key=lambda i: (entries[i][2], i))[:capacity])
    chosen = [entry for i, entry in enumerate(entries) if i in selected]
    if hidden:
        footer = f"+{hidden} hidden · Resize · Ctrl+C quit"
        if failed:
            footer = f"Retry {POLL_SECONDS}s · " + footer
        if width < 32:
            footer = "Ctrl+C quit · Resize for more"
        if width < 12:
            footer = "Ctrl+C"
    if budget == 1:
        # A single usable row must contain the result, not just a title/footer.
        entry = min(entries, key=lambda entry: entry[2])
        return paint(fit_text(entry[0], available), entry[1])
    if framed:
        # The label makes the outer frame an information hierarchy rather than
        # a purely decorative box. Keep the rule subdued and the title cyan.
        frame_width = inner + 2
        title = " POWER / TELEMETRY "
        title = fit_text(title, max(0, frame_width - 2))
        remaining = max(0, frame_width - 1 - cell_width(title))
        top = (indent + "╭─" + paint(title, "cyan") +
               paint("─" * remaining, "dim") + "╮")
        lines = [top]
        for text, color, _ in chosen:
            lines.append(indent + "│ " + paint(fit_text(text, inner, pad=True), color) + " │")
        lines.append(indent + "╰" + "─" * (inner + 2) + "╯")
        lines.append(indent + paint(fit_text(footer, width), "dim"))
    else:
        lines = [paint(fit_text(text, available), color) for text, color, _ in chosen]
        lines.append(paint(fit_text(footer, available), "dim"))
    return "\n".join(lines)


class FrameWriter:
    """Write only changed rows for low-flicker TTY animation."""

    def __init__(self):
        self._rows = []
        self._dimensions = None
        self._height = 0

    def finish(self):
        """Return shell output to a clean row, even after a partial redraw/resize."""
        terminal = shutil.get_terminal_size((78, 24))
        row = min(self._height + 1, max(1, terminal.lines))
        print(f"{RESET}\033[{row};1H\033[J\033[?25h", end="", flush=True)

    def write(self, frame, dimensions=None):
        rows = frame.split("\n") if frame else []
        if dimensions is None:
            terminal = shutil.get_terminal_size((78, 24))
            dimensions = (terminal.columns, terminal.lines)
        dimensions = tuple(dimensions)
        old = self._rows
        resized = self._dimensions is not None and dimensions != self._dimensions
        # Track the footprint before writing: Ctrl+C may interrupt the output.
        self._height = len(rows) if resized else max(len(old), len(rows))
        # Dirty updates must not leave the hidden cursor inside a dashboard row:
        # the terminal can echo ^C there before Python handles the interrupt.
        prompt_row = min(len(rows) + 1, max(1, dimensions[1]))
        park = f"\033[{prompt_row};1H"
        if resized:
            # A dirty diff can address rows that no longer exist after a
            # shrink. Clear/repaint from home instead, without old-row cursor
            # addressing; the rendered frame already fits the new dimensions.
            repaint = "\033[2J\033[H"
            if rows:
                repaint += "\033[K\n".join(rows) + "\033[K"
            print(repaint + park, end="", flush=True)
            self._rows = rows
            self._dimensions = dimensions
            self._height = len(rows)
            return
        changed = []
        for index, line in enumerate(rows):
            if index >= len(old) or line != old[index]:
                changed.append(f"\033[{index + 1};1H{line}\033[K")
        # Clear rows left behind by a resize or a shorter error frame.
        for index in range(len(rows), len(old)):
            changed.append(f"\033[{index + 1};1H\033[K")
        if changed:
            print("".join(changed) + park, end="", flush=True)
        self._rows = rows
        self._dimensions = dimensions
        self._height = len(rows)


def display(data, error=None, animation_step=None, writer=None, updated_at=None):
    """Recompute layout on every refresh, including after a terminal resize."""
    terminal = shutil.get_terminal_size((78, 24))
    frame = render_frame(data, error, terminal.columns, terminal.lines if TTY else None, animation_step, updated_at)
    if TTY:
        if writer is None:
            # Preserve the simple full repaint behavior for direct callers/tests.
            frame = "\033[K\n".join(frame.split("\n"))
            print("\033[H" + frame + "\033[K\033[J", end="", flush=True)
        else:
            writer.write(frame, (terminal.columns, terminal.lines))
    else:
        print(frame, flush=True)


def wait_for_refresh(data, error=None, animate=True, writer=None, updated_at=None):
    """Wait for the next telemetry poll, animating only on a capable TTY.

    The monotonic deadline keeps polling at two seconds even when a redraw is
    delayed. ``sleep`` remains injectable for existing main-loop tests.
    """
    deadline = time.monotonic() + POLL_SECONDS
    if not (animate and TTY):
        time.sleep(max(0, deadline - time.monotonic()))
        return
    step = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.25, remaining))
        if time.monotonic() >= deadline:
            return
        step += 1
        display(data, error, animation_step=step, writer=writer, updated_at=updated_at)


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
    parser.add_argument("--no-animation", action="store_true", help="Disable the decorative live activity indicator")
    parser.add_argument("command", nargs="?", choices=["update", "diagnose"], help="Update, or print read-only I/O diagnostics")
    args = parser.parse_args(argv)
    if args.command == "update":
        return update()
    if args.command == "diagnose":
        print(json.dumps(read_power_io(), indent=2, ensure_ascii=True))
        return 0
    history = CurrentHistory()
    animate = not args.no_animation and os.environ.get("BATTERY_MONITOR_NO_ANIMATION") != "1"
    writer = FrameWriter() if TTY else None
    interrupted = False
    try:
        if TTY:
            print("\033[2J\033[H\033[?25l", end="", flush=True)
        while True:
            data, error = read_battery_status()
            if error:
                history.reset()
            else:
                data = history.with_average(data)
                if isinstance(data, dict):
                    data["_power_io"] = read_power_io()
            updated_at = datetime.now().astimezone()
            display(data, error, writer=writer, updated_at=updated_at)
            wait_for_refresh(data, error, animate, writer, updated_at)
    except KeyboardInterrupt:
        interrupted = True
    except BrokenPipeError:
        # Prevent Python's shutdown flush from printing a second pipe error.
        try:
            with open(os.devnull, "w") as sink:
                os.dup2(sink.fileno(), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return 0
    finally:
        if writer is not None:
            try:
                writer.finish()
            except BrokenPipeError:
                pass
    if interrupted:
        print(("" if TTY else "\n") + "Battery monitor stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
