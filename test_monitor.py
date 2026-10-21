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

    def test_current_number_preserves_negative_microamps_and_rejects_sentinels(self):
        self.assertEqual(monitor.current_number(-2472000), -2472000)
        self.assertEqual(monitor.current_number("-2472000"), -2472000)
        for value in (None, "", "bad", float("nan"), float("inf"), -2147483648, 2147483648):
            with self.subTest(value=value):
                self.assertIsNone(monitor.current_number(value))

    def test_history_app_average_fallback_for_missing_or_invalid_phone_average(self):
        history = monitor.CurrentHistory()
        enriched = history.with_average({"current": 2000, "current_average": "not-a-number"}, now=1)
        self.assertEqual(enriched["_app_current_average"], 2000)
        self.assertEqual(enriched["_app_current_samples"], 1)
        frame = self.capture_display(enriched)
        self.assertIn("AVG (APP/RAW, 1)", frame)
        self.assertIn("2 mA", frame)

        enriched = history.with_average({"current": 3000}, now=2)
        self.assertEqual(enriched["_app_current_average"], 2500)
        frame = self.capture_display(dict(enriched, current_average=float("nan")))
        self.assertIn("AVG (APP/RAW, 2)", frame)

    def test_display_prefers_valid_phone_average_including_zero(self):
        frame = self.capture_display({
            "current": -2472000, "current_average": 0,
            "_app_current_average": 9000, "_app_current_samples": 3,
            "status": "CHARGING",
        })
        self.assertIn("CURRENT (RAW)        -2472 mA", frame)
        self.assertIn("AVG (PHONE/RAW)      0 mA", frame)
        self.assertNotIn("AVG (APP/RAW", frame)

    def test_missing_instantaneous_current_clears_history_instead_of_using_stale_average(self):
        history = monitor.CurrentHistory()
        history.with_average({"current": 7000}, now=10)
        enriched = history.with_average({"current": None}, now=12)
        self.assertIsNone(enriched["_app_current_average"])
        self.assertEqual(enriched["_app_current_samples"], 0)
        self.assertEqual(len(history.samples), 0)

    def test_history_uses_signed_sample_mean_and_does_not_mutate_input(self):
        history = monitor.CurrentHistory()
        original = {"current": -2472000, "status": "charging", "nested": {"keep": True}}
        snapshot = dict(original)
        history.with_average(original, now=0)
        enriched = history.with_average({"current": -1000, "status": "charging"}, now=1)
        self.assertEqual(enriched["_app_current_average"], -1236500)
        self.assertEqual(original, snapshot)
        self.assertNotIn("_app_current_average", original)
        self.assertNotIn("_app_current_samples", original)

    def test_history_expires_samples_after_thirty_seconds_even_with_gaps(self):
        history = monitor.CurrentHistory()
        history.with_average({"current": 1000}, now=0)
        history.with_average({"current": 3000}, now=10)
        enriched = history.with_average({"current": 5000}, now=40)
        self.assertEqual(enriched["_app_current_samples"], 2)
        self.assertEqual(enriched["_app_current_average"], 4000)
        enriched = history.with_average({"current": 7000}, now=71)
        self.assertEqual(enriched["_app_current_samples"], 1)
        self.assertEqual(enriched["_app_current_average"], 7000)

    def test_history_queue_is_bounded_to_128_samples(self):
        history = monitor.CurrentHistory()
        for value in range(200):
            history.with_average({"current": value}, now=100)
        self.assertEqual(len(history.samples), 128)
        self.assertEqual(history.samples[0], (100, 72))
        self.assertEqual(history.samples[-1], (100, 199))

    def test_history_state_and_plugged_transitions_reset_samples(self):
        history = monitor.CurrentHistory()
        history.with_average({"current": 1000, "status": "charging", "plugged": "ac"}, now=1)
        history.with_average({"current": 3000, "status": "charging", "plugged": "ac"}, now=2)
        enriched = history.with_average({"current": 9000, "status": "discharging", "plugged": "ac"}, now=3)
        self.assertEqual(enriched["_app_current_average"], 9000)
        self.assertEqual(enriched["_app_current_samples"], 1)
        enriched = history.with_average({"current": 11000, "status": "discharging", "plugged": "usb"}, now=4)
        self.assertEqual(enriched["_app_current_average"], 11000)
        self.assertEqual(enriched["_app_current_samples"], 1)

    def test_history_rejects_nonfinite_and_sentinel_current_values(self):
        history = monitor.CurrentHistory()
        for value in (float("nan"), float("inf"), -2147483648, 2147483648, "oops"):
            with self.subTest(value=value):
                enriched = history.with_average({"current": value}, now=1)
                self.assertIsNone(enriched["_app_current_average"])
                self.assertEqual(enriched["_app_current_samples"], 0)

    def test_history_backward_time_resets_before_accepting_new_sample(self):
        history = monitor.CurrentHistory()
        history.with_average({"current": 1000}, now=10)
        enriched = history.with_average({"current": 5000}, now=9)
        self.assertEqual(enriched["_app_current_average"], 5000)
        self.assertEqual(enriched["_app_current_samples"], 1)

    def test_battery_power_watts_uses_signed_battery_side_millivolt_microamp_input(self):
        negative = monitor.battery_power_watts({"voltage": 4224, "current": -2472000})
        positive = monitor.battery_power_watts({"voltage": "4224", "current": "2472000"})
        self.assertAlmostEqual(negative, -10.441728, places=9)
        self.assertAlmostEqual(positive, 10.441728, places=9)
        self.assertEqual(monitor.battery_power_watts({"voltage": 4224, "current": 0}), 0.0)

    def test_battery_power_watts_rejects_bad_voltage_and_current_safely(self):
        bad_voltages = (None, True, False, float("nan"), float("inf"), -1, 0, 2147483648)
        for voltage in bad_voltages:
            with self.subTest(voltage=voltage):
                self.assertIsNone(monitor.battery_power_watts({"voltage": voltage, "current": 1000}))
        bad_currents = (None, True, False, float("nan"), float("inf"), -2147483648, 2147483648, "bad")
        for current in bad_currents:
            with self.subTest(current=current):
                self.assertIsNone(monitor.battery_power_watts({"voltage": 4224, "current": current}))
        for value in (None, [], "not a mapping"):
            with self.subTest(value=value):
                self.assertIsNone(monitor.battery_power_watts(value))
        self.assertIsNone(monitor.battery_power_watts({"current": 1000}))

    def test_display_shows_positive_battery_magnitude_and_never_charger_power_claims(self):
        for status in ("CHARGING", "FULL", "UNPLUGGED"):
            with self.subTest(status=status):
                frame = self.capture_display({
                    "voltage": 4224, "current": -2472000, "status": status,
                    "plugged": "AC" if status == "CHARGING" else "NONE",
                })
                self.assertIn("BATTERY POWER (EST)", frame)
                self.assertIn("10.44 W", frame)
                self.assertIn("Battery-side magnitude; not charger input.", frame)
                self.assertIn("DEVICE MAX W         n/a (not exposed)", frame)
                self.assertIn("CHARGER INPUT W      n/a (not exposed)", frame)
                self.assertNotIn("charger input 10.44", frame.lower())
                self.assertNotIn("max rating", frame.lower())

    def test_display_marks_invalid_power_as_na_and_keeps_power_rows_within_width(self):
        readable = self.capture_display({"voltage": 0, "current": -2472000}, columns=80)
        self.assertIn("BATTERY POWER (EST)  n/a", readable)
        for columns in (8, 20, 32, 40, 80):
            with self.subTest(columns=columns):
                frame = self.capture_display({"voltage": 0, "current": -2472000}, columns=columns)
                self.assertTrue(all(len(line) <= columns - 1 for line in frame.splitlines()), frame)

    def test_main_resets_history_when_battery_api_returns_error(self):
        fake_history = mock.Mock()
        fake_history.with_average.side_effect = AssertionError("must not enrich API errors")
        fake_history.reset.side_effect = KeyboardInterrupt
        with mock.patch.object(monitor, "CurrentHistory", return_value=fake_history), \
             mock.patch.object(monitor, "TTY", False), \
             mock.patch.object(monitor, "read_battery_status", return_value=(None, "API failed")), \
             mock.patch.object(monitor, "display"):
            self.assertEqual(monitor.main([]), 0)
        fake_history.reset.assert_called_once_with()

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
