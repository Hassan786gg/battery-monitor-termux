"""Responsive rendering regressions; run with python3 -m unittest discover."""
import contextlib
import io
import os
import re
import unittest
from unittest import mock

import live_battery_monitor as monitor


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SAMPLE = {
    "percentage": 75, "status": "CHARGING", "plugged": "AC", "health": "GOOD",
    "temperature": 32.5, "voltage": 4224, "current": -2472000,
    "_app_current_average": -2400000, "_app_current_samples": 12,
    "charge_counter": 3000000, "cycle": 123,
}


class LayoutTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(monitor, "USE_COLOR", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_fits(self, frame, columns, rows):
        plain = ANSI.sub("", frame)
        self.assertLessEqual(len(plain.splitlines()), max(0, rows - 1))
        for line in plain.splitlines():
            self.assertLessEqual(monitor.cell_width(line), max(0, columns - 1), repr(line))

    def test_dimension_matrix_normal_error_and_verbose_io(self):
        data = dict(SAMPLE, status="充電中⚡" * 100, health="e\u0301" * 100)
        data["_power_io"] = {
            "input": {"watts": 12, "voltage_v": 5, "current_a": 2.4,
                      "supply": "电源" * 100, "port": "port0", "method": "V*I estimate"},
            "ports": [{"supply": "usb", "limit_watts": 18}],
        }
        for columns in (1, 2, 4, 8, 16, 20, 32, 40, 80, 120):
            for rows in (1, 2, 3, 4, 5, 6, 8, 10, 16, 24, 50):
                for payload, error in ((SAMPLE, None), (data, None), (None, "API failed 电源 " * 100)):
                    with self.subTest(columns=columns, rows=rows, error=bool(error)):
                        self.assert_fits(monitor.render_frame(payload, error, columns, rows), columns, rows)

    def test_short_frame_is_closed_and_keeps_key_readings(self):
        frame = monitor.render_frame(SAMPLE, columns=80, rows=8)
        lines = frame.splitlines()
        self.assertTrue(lines[0].lstrip().startswith("╭"))
        self.assertTrue(lines[-2].lstrip().startswith("╰"))
        self.assertIn("CHARGE", frame)
        self.assertIn("75%", frame)
        self.assertIn("CHARGING", frame)
        self.assertIn("hidden", lines[-1])
        self.assertIn("Ctrl+C", lines[-1])
        self.assertNotIn("Developer:", frame)

    def test_tiny_screens_prioritize_result_over_header(self):
        self.assertIn("75%", monitor.render_frame(SAMPLE, columns=8, rows=4))
        self.assertIn("75%", monitor.render_frame(SAMPLE, columns=8, rows=2))
        frame = monitor.render_frame(None, "API failed", columns=80, rows=2)
        self.assertIn("Could not read battery data", frame)
        self.assertEqual(len(frame.splitlines()), 1)

    def test_standard_terminal_has_all_primary_metrics_and_no_hidden_notice(self):
        frame = monitor.render_frame(SAMPLE, columns=80, rows=24)
        for label in ("Developer: Hassan Raza", "Updated", "SYSTEM HEALTH FLAG", "TEMPERATURE",
                      "BATTERY POWER (EST)", "CURRENT (RAW)", "AVG (APP/RAW, 12)",
                      "DEVICE INPUT W", "DEVICE OUTPUT W", "CHARGE COUNTER", "CYCLE COUNT"):
            self.assertIn(label, frame)
        self.assertNotIn("hidden", frame)
        self.assertIn("not charger input", frame)

    def test_unicode_and_control_text_fit_without_splitting_wide_glyphs(self):
        self.assertEqual(monitor.cell_width("电源"), 4)
        self.assertEqual(monitor.cell_width("e\u0301"), 1)
        self.assertEqual(monitor.fit_text("电源", 3), "电…")
        self.assertEqual(monitor.fit_text("电源", 0), "")
        self.assertEqual(monitor.cell_width(monitor.fit_text("电源", 8, pad=True)), 8)
        fitted = monitor.fit_text("abc\x1b[31m\x00\n\u202eevil", 20)
        self.assertNotIn("\x1b", fitted)
        self.assertNotIn("\x00", fitted)
        self.assertNotIn("\n", fitted)
        self.assertNotIn("\u202e", fitted)
        self.assertLessEqual(monitor.cell_width(monitor.fit_text("❤️" * 10, 8)), 8)

    def test_error_remains_closed_and_has_detail_and_retry(self):
        frame = monitor.render_frame(None, "API failed: permission denied", columns=80, rows=8)
        self.assertIn("Could not read battery data", frame)
        self.assertIn("permission denied", frame)
        self.assertIn("Retry", frame)
        self.assertTrue(frame.splitlines()[-2].lstrip().startswith("╰"))

    def test_resizing_and_color_repaint_have_no_final_newline(self):
        out = io.StringIO()
        sizes = [os.terminal_size((80, 40)), os.terminal_size((32, 8)), os.terminal_size((80, 40))]
        with mock.patch.object(monitor, "TTY", True), mock.patch.object(monitor, "USE_COLOR", True), \
             mock.patch.object(monitor.shutil, "get_terminal_size", side_effect=sizes), \
             contextlib.redirect_stdout(out):
            for _ in sizes:
                monitor.display(SAMPLE)
        frames = out.getvalue().split("\x1b[H")[1:]
        self.assertEqual(len(frames), 3)
        for frame, size in zip(frames, sizes):
            self.assertTrue(frame.endswith("\x1b[J"))
            self.assertFalse(frame.endswith("\n"))
            self.assertTrue(all(line.endswith("\x1b[K") for line in frame.splitlines()[:-1]))
            self.assertTrue(frame.endswith("\x1b[K\x1b[J"))
            self.assert_fits(frame, size.columns, size.lines)
        self.assertIn("CYCLE COUNT", frames[0])
        self.assertNotIn("CYCLE COUNT", frames[1])
        self.assertIn("CYCLE COUNT", frames[2])

    def test_redirected_output_keeps_all_rows_even_with_short_terminal(self):
        out = io.StringIO()
        with mock.patch.object(monitor, "TTY", False), \
             mock.patch.object(monitor.shutil, "get_terminal_size", return_value=os.terminal_size((80, 3))), \
             contextlib.redirect_stdout(out):
            monitor.display(SAMPLE)
        frame = out.getvalue()
        self.assertIn("CYCLE COUNT", frame)
        self.assertIn("Developer: Hassan Raza", frame)
        self.assertNotIn("\x1b", frame)
        self.assertNotIn("hidden", frame)
        self.assertGreater(len(frame.splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
