#!/usr/bin/env python3
"""Regression tests for the Termux battery monitor."""
import ast
import contextlib
import io
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock
from urllib.error import URLError
from urllib.request import Request

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import live_battery_monitor as monitor  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        return self.payload if size < 0 else self.payload[:size]


class MonitorTests(unittest.TestCase):
    def capture_display(self, data, error=None, columns=80, lines=24, tty=False):
        out = io.StringIO()
        with mock.patch.object(monitor.shutil, "get_terminal_size", return_value=os.terminal_size((columns, lines))), \
             mock.patch.object(monitor, "TTY", tty), mock.patch.object(monitor, "USE_COLOR", False), \
             contextlib.redirect_stdout(out):
            monitor.display(data, error)
        return out.getvalue().rstrip("\n")

    def test_finite_number_rejects_bool_nan_inf_and_bad_values(self):
        for value in (True, False, float("nan"), float("inf"), float("-inf"), None, "wat"):
            with self.subTest(value=value):
                self.assertIsNone(monitor.finite_number(value))
        self.assertEqual(monitor.finite_number("3.5"), 3.5)

    def test_display_handles_invalid_scale_and_values_without_crashing(self):
        for scale in (True, 0, -1, float("inf"), float("nan")):
            with self.subTest(scale=scale):
                frame = self.capture_display({"level": 50, "scale": scale, "temperature": float("nan")})
                self.assertIn("CHARGE", frame)
                self.assertIn("n/a", frame)
        frame = self.capture_display({"percentage": True, "voltage": -1, "cycle": float("inf")})
        self.assertIn("n/a", frame)

    def test_display_normal_and_error_include_developer_credit(self):
        normal = self.capture_display({"percentage": 75, "status": "CHARGING", "plugged": "AC", "health": "GOOD"})
        error = self.capture_display(None, "API failed\n\033[31mboom\x00")
        for frame in (normal, error):
            self.assertIn("Developer: Hassan Raza", frame)
        self.assertIn("Could not read battery data", error)
        self.assertNotIn("\x1b", error)
        self.assertNotIn("\x00", error)
        self.assertIn("API failed", error)

    def test_display_sanitizes_long_status_and_all_lines_fit(self):
        status = "STATUS_" + "X" * 300 + "\033[31m" + "\x00" * 4
        for columns in (8, 20, 32, 40, 80):
            with self.subTest(columns=columns):
                frame = self.capture_display({"percentage": 50, "status": status, "plugged": status, "health": status}, columns=columns)
                self.assertNotIn("\x1b", frame)
                self.assertNotIn("\x00", frame)
                self.assertTrue(all(len(line) <= columns - 1 for line in frame.splitlines()), frame)

    def test_display_never_exceeds_terminal_height_budget(self):
        frame = self.capture_display(
            {"percentage": 50, "status": "CHARGING", "plugged": "AC", "health": "GOOD"},
            columns=80,
            lines=8,
            tty=True,
        )
        self.assertLessEqual(len(frame.splitlines()), 7)

    def test_read_battery_status_timeout_malformed_error_object_and_empty(self):
        with mock.patch.object(monitor.shutil, "which", return_value="/bin/api"), \
             mock.patch.object(monitor.subprocess, "run", side_effect=monitor.subprocess.TimeoutExpired("api", 10)):
            data, error = monitor.read_battery_status()
            self.assertIsNone(data)
            self.assertIn("Battery API timed out", error)
        with mock.patch.object(monitor.shutil, "which", return_value="/bin/api"), \
             mock.patch.object(monitor.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="{", stderr="")):
            data, error = monitor.read_battery_status()
            self.assertIsNone(data)
            self.assertIn("unreadable data", error)
        with mock.patch.object(monitor.shutil, "which", return_value="/bin/api"):
            for stdout in ('{"error":"permission denied"}', '{}', '[]'):
                result = mock.Mock(returncode=0, stdout=stdout, stderr="")
                with mock.patch.object(monitor.subprocess, "run", return_value=result):
                    data, error = monitor.read_battery_status()
                    self.assertIsNone(data)
                    self.assertTrue(error)
            result = mock.Mock(returncode=1, stdout="", stderr="bad api")
            with mock.patch.object(monitor.subprocess, "run", return_value=result):
                data, error = monitor.read_battery_status()
                self.assertIsNone(data)
                self.assertIn("bad api", error)

    def test_main_update_dispatches_without_launching_monitor(self):
        with mock.patch.object(monitor, "update", return_value=7) as update, \
             mock.patch.object(monitor, "read_battery_status", side_effect=AssertionError("monitor launched")):
            self.assertEqual(monitor.main(["update"]), 7)
        update.assert_called_once_with()

    def test_main_rejects_unknown_arguments(self):
        with self.assertRaises(SystemExit) as raised:
            monitor.main(["--not-a-real-option"])
        self.assertEqual(raised.exception.code, 2)

    def update_with_payload(self, directory, payload, target_content="old", **patches):
        prefix = Path(directory)
        destination = prefix / "bin" / "battery-monitor"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(target_content, encoding="utf-8")
        defaults = {"PREFIX": str(prefix)}
        defaults.update(patches.pop("env", {}))
        with mock.patch.dict(os.environ, defaults, clear=False), \
             mock.patch.object(monitor.shutil, "which", return_value="/bin/pkg"), \
             mock.patch.object(monitor, "urlopen", return_value=FakeResponse(payload)):
            result = monitor.update()
        return result, destination

    def test_update_installs_actual_source_with_termux_shebang_755_and_no_launch(self):
        source = (ROOT / "live_battery_monitor.py").read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            result, destination = self.update_with_payload(temp, source)
            self.assertEqual(result, 0)
            installed = destination.read_bytes()
            self.assertTrue(installed.startswith(f"#!{Path(temp) / 'bin' / 'python'}\n".encode()))
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o755)
            ast.parse(installed.decode())
            self.assertNotIn("subprocess.run(", installed.decode().split("\n", 1)[0])
            self.assertEqual(list(destination.parent.glob(".battery-monitor.*")), [])

    def test_update_failures_preserve_target_and_clean_temps(self):
        source = (ROOT / "live_battery_monitor.py").read_bytes()
        failures = [
            ("network", None, URLError("offline"), None),
            ("syntax", b"#!/usr/bin/env python3\ndef broken(:\n", None, None),
            ("oversize", b"x" * (monitor.MAX_DOWNLOAD + 1), None, None),
        ]
        for name, payload, network_error, _ in failures:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "bin" / "battery-monitor"
                target.parent.mkdir(parents=True)
                target.write_text("keep", encoding="utf-8")
                network_patch = (mock.patch.object(monitor, "urlopen", side_effect=network_error)
                                 if network_error else
                                 mock.patch.object(monitor, "urlopen", return_value=FakeResponse(payload)))
                with mock.patch.dict(os.environ, {"PREFIX": temp}, clear=False), \
                     mock.patch.object(monitor.shutil, "which", return_value="/bin/pkg"), network_patch:
                    self.assertEqual(monitor.update(), 1)
                self.assertEqual(target.read_text(), "keep")
                self.assertEqual(list(target.parent.glob(".battery-monitor.*")), [])
        for name, replace_side_effect in (("replace", OSError("replace failed")), ("permission", PermissionError("chmod failed"))):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / "bin" / "battery-monitor"
                target.parent.mkdir(parents=True)
                target.write_text("keep", encoding="utf-8")
                patcher = mock.patch.object(monitor.os, "replace", side_effect=replace_side_effect) if name == "replace" else mock.patch.object(monitor.os, "fchmod", side_effect=replace_side_effect)
                with mock.patch.dict(os.environ, {"PREFIX": temp}, clear=False), \
                     mock.patch.object(monitor.shutil, "which", return_value="/bin/pkg"), \
                     mock.patch.object(monitor, "urlopen", return_value=FakeResponse(source)), patcher:
                    self.assertEqual(monitor.update(), 1)
                self.assertEqual(target.read_text(), "keep")
                self.assertEqual(list(target.parent.glob(".battery-monitor.*")), [])


if __name__ == "__main__":
    unittest.main()
