"""Additional responsive/property coverage for the battery dashboard UI.

This file intentionally tests public rendering behavior without prescribing exact
border glyphs, section-label wording, or animation art.
"""
import copy
import re
import unittest
from unittest import mock

import live_battery_monitor as monitor


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CLOCK = re.compile(r"\b\d{2}:\d{2}:\d{2}(?:\s+\S+)?\b")

BASE = {
    "percentage": 73,
    "status": "CHARGING",
    "plugged": "AC",
    "health": "GOOD",
    "temperature": 31.5,
    "voltage": 4224,
    "current": -2472000,
    "current_average": -2400000,
    "charge_counter": 3000000,
    "cycle": 123,
}


def plain(frame):
    return ANSI.sub("", frame)


def without_clock(frame):
    return CLOCK.sub("<clock>", plain(frame))


class UIRefinementTests(unittest.TestCase):
    def setUp(self):
        self.color = mock.patch.object(monitor, "USE_COLOR", False)
        self.color.start()
        self.addCleanup(self.color.stop)

    def assert_fits(self, frame, columns, rows):
        text = plain(frame)
        self.assertLessEqual(len(text.splitlines()), max(0, rows - 1),
                             (columns, rows, repr(text)))
        for line in text.splitlines():
            self.assertLessEqual(monitor.cell_width(line), max(0, columns - 1),
                                 (columns, rows, repr(line)))

    def test_dimension_matrix_and_priority_for_all_data_states(self):
        states = [
            dict(BASE),
            dict(BASE, status="DISCHARGING", plugged="NONE", percentage=19, current=0),
            dict(BASE, status="FULL", plugged="NONE", percentage=100, current=0),
            {"percentage": 0, "status": "DISCHARGING", "plugged": "NONE", "current": 0},
            {"status": "UNKNOWN", "plugged": None, "health": None},
        ]
        errors = [(None, None), (None, "API error: permission denied"),
                  (None, "missing API " + "错误" * 80)]
        for columns in (1, 2, 3, 4, 7, 8, 12, 16, 20, 32, 40, 64, 80, 100, 130):
            for rows in (1, 2, 3, 4, 5, 6, 8, 10, 14, 20, 28, 36, 42):
                for data in states:
                    for _, error in errors:
                        with self.subTest(columns=columns, rows=rows, error=bool(error),
                                          status=data.get("status")):
                            self.assert_fits(monitor.render_frame(data, error, columns, rows),
                                             columns, rows)

    def test_unicode_controls_and_colors_do_not_break_terminal_geometry(self):
        data = dict(BASE, status="充電中⚡\x1b[31m\n" + "状态" * 60,
                    plugged="USB\x00\u202e電源", health="e\u0301" * 100)
        for use_color in (False, True):
            with self.subTest(use_color=use_color), mock.patch.object(
                    monitor, "USE_COLOR", use_color):
                for columns in (1, 8, 16, 32, 80, 130):
                    frame = monitor.render_frame(data, columns=columns, rows=20)
                    self.assert_fits(frame, columns, 20)
                    self.assertNotIn("\x1b[31m", plain(frame))
                    self.assertNotIn("\x00", plain(frame))
                    self.assertNotIn("\n", plain(frame.replace("\n", "")))

    def test_unrestricted_frame_preserves_full_telemetry(self):
        data = dict(BASE, _power_io={
            "input": {"watts": 12.0, "voltage_v": 5.0, "current_a": 2.4,
                      "supply": "usb", "port": "port0", "method": "V*I estimate"},
            "output": None, "ports": [],
        })
        frame = monitor.render_frame(data, columns=130, rows=None)
        for value in ("73%", "31.5", "4.224", "-2472", "-2400", "12.00",
                      "3000", "123", "CHARGING", "SYSTEM", "TEMPERATURE"):
            self.assertIn(value, frame)
        self.assertNotIn("hidden", frame.lower())

    def test_rendering_never_mutates_any_payload_or_nested_telemetry(self):
        payload = dict(BASE, _power_io={
            "input": {"watts": 8, "supply": "电源", "port": "port0",
                      "voltage_v": 5, "current_a": 1.6, "method": "direct"},
            "ports": [{"supply": "usb", "limit_watts": 18}],
        })
        snapshot = copy.deepcopy(payload)
        monitor.render_frame(payload, columns=37, rows=12)
        monitor.render_frame(payload, columns=130, rows=None)
        self.assertEqual(payload, snapshot)

    def test_animation_changes_decoration_only(self):
        data = dict(BASE, status="DISCHARGING", plugged="NONE", percentage=42,
                    current=-1234000, current_average=-1200000)
        phase_none_a = monitor.render_frame(data, columns=100, rows=24, animation_step=None)
        phase_none_b = monitor.render_frame(data, columns=100, rows=24, animation_step=None)
        self.assertEqual(without_clock(phase_none_a), without_clock(phase_none_b))
        frames = [monitor.render_frame(data, columns=100, rows=24, animation_step=i)
                  for i in (0, 1, 2)]
        self.assertGreater(len({without_clock(frame) for frame in frames}), 1)
        stable_tokens = ("42%", "-1234", "-1200", "31.5", "4.224", "3000", "123")
        for frame in frames:
            for token in stable_tokens:
                self.assertIn(token, frame)
            # Animation must not invent a charge/filled bar for discharging data.
            self.assertNotIn("⚡", frame)
            self.assertNotRegex(plain(frame), r"STATE\s+CHARGING(?:\s|·)")
        # If phases differ, only the decorative header activity may differ;
        # numeric readings and the charge gauge remain fixed.
        baseline = without_clock(frames[0]).splitlines()
        for frame in frames[1:]:
            candidate = without_clock(frame).splitlines()
            self.assertEqual(len(baseline), len(candidate))
            for index, (before, after) in enumerate(zip(baseline, candidate)):
                if before != after:
                    self.assertIn("BATTERY / LIVE MONITOR", before)
                    self.assertIn("BATTERY / LIVE MONITOR", after)


if __name__ == "__main__":
    unittest.main()
