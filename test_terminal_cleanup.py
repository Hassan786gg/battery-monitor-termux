"""Shell handoff regressions; run with python3 -m unittest discover -v."""
import contextlib
import io
import os
import re
import unittest
from unittest import mock

import live_battery_monitor as monitor


DATA = {
    "percentage": 96, "status": "DISCHARGING", "plugged": "UNPLUGGED",
    "health": "GOOD", "temperature": 35.9, "voltage": 4197, "current": 267000,
}
POSITION = re.compile(r"\x1b\[(\d+);(\d+)H")


class TerminalCleanupTests(unittest.TestCase):
    def test_full_and_dirty_redraws_park_cursor_below_frame(self):
        for dimensions in ((80, 40), (64, 24), (32, 8), (8, 2), (1, 1)):
            with self.subTest(dimensions=dimensions):
                writer = monitor.FrameWriter()
                for phase in (0, 1):
                    frame = monitor.render_frame(DATA, columns=dimensions[0],
                                                 rows=dimensions[1], animation_step=phase)
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        writer.write(frame, dimensions)
                    if out.getvalue():
                        row = min(len(frame.splitlines()) + 1, dimensions[1])
                        self.assertTrue(out.getvalue().endswith(f"\x1b[{row};1H"))

    def test_finish_clears_echo_and_restores_cursor_at_column_one(self):
        writer = monitor.FrameWriter()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            writer.write("header\nreading\nfooter", (80, 40))
            out.seek(0)
            out.truncate()
            with mock.patch.object(monitor.shutil, "get_terminal_size",
                                   return_value=os.terminal_size((80, 40))):
                writer.finish()
        self.assertEqual(out.getvalue(), monitor.RESET + "\x1b[4;1H\x1b[J\x1b[?25h")

    def test_exit_after_resize_before_next_redraw_clamps_to_visible_row(self):
        writer = monitor.FrameWriter()
        with contextlib.redirect_stdout(io.StringIO()):
            writer.write("\n".join(["reading"] * 30), (80, 40))
        out = io.StringIO()
        with mock.patch.object(monitor.shutil, "get_terminal_size",
                               return_value=os.terminal_size((32, 8))), \
                contextlib.redirect_stdout(out):
            writer.finish()
        self.assertIn("\x1b[8;1H\x1b[J", out.getvalue())
        self.assertTrue(all(int(row) <= 8 for row, _ in POSITION.findall(out.getvalue())))

    def test_interrupted_first_write_keeps_its_full_footprint(self):
        writer = monitor.FrameWriter()
        with mock.patch("builtins.print", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                writer.write("header\nreading\nfooter", (80, 40))
        out = io.StringIO()
        with mock.patch.object(monitor.shutil, "get_terminal_size",
                               return_value=os.terminal_size((80, 40))), \
                contextlib.redirect_stdout(out):
            writer.finish()
        self.assertIn("\x1b[4;1H", out.getvalue())

    def capture_main(self, tty=True, animate=True, early=False, failure=KeyboardInterrupt):
        out = io.StringIO()

        def stop(data, error, enabled, writer, updated_at):
            if enabled:
                monitor.display(data, error, animation_step=1, writer=writer, updated_at=updated_at)
            raise failure

        with mock.patch.object(monitor, "TTY", tty), \
                mock.patch.object(monitor, "USE_COLOR", False), \
                mock.patch.object(monitor.shutil, "get_terminal_size",
                                  return_value=os.terminal_size((80, 40))), \
                mock.patch.object(monitor, "read_battery_status", return_value=(dict(DATA), None),
                                  side_effect=KeyboardInterrupt if early else None), \
                mock.patch.object(monitor, "read_power_io", return_value={}), \
                mock.patch.object(monitor, "wait_for_refresh", side_effect=stop), \
                contextlib.redirect_stdout(out):
            try:
                monitor.main([] if animate else ["--no-animation"])
            finally:
                # Model the shell printing a prompt and echoing the next command.
                print("~ $ battery-monitor", end="")
        return out.getvalue()

    def test_stop_message_and_next_command_follow_cleanup_not_dirty_cursor(self):
        for animate in (True, False):
            with self.subTest(animate=animate):
                output = self.capture_main(animate=animate)
                frame = monitor.render_frame(DATA, columns=80, rows=40)
                expected_row = len(frame.splitlines()) + 1
                tail = (f"{monitor.RESET}\x1b[{expected_row};1H\x1b[J\x1b[?25h"
                        "Battery monitor stopped.\n~ $ battery-monitor")
                self.assertTrue(output.endswith(tail), repr(output[-160:]))
                self.assertEqual(output.count("Battery monitor stopped."), 1)

    def test_interrupt_before_first_frame_returns_to_first_clean_row(self):
        output = self.capture_main(early=True)
        self.assertTrue(output.endswith(monitor.RESET + "\x1b[1;1H\x1b[J\x1b[?25h"
                                        "Battery monitor stopped.\n~ $ battery-monitor"))

    def test_redirected_output_has_no_terminal_controls(self):
        output = self.capture_main(tty=False)
        self.assertNotIn("\x1b", output)
        self.assertIn("\nBattery monitor stopped.\n~ $ battery-monitor", output)

    def test_unexpected_error_still_restores_terminal(self):
        out = io.StringIO()
        with mock.patch.object(monitor, "TTY", True), \
                mock.patch.object(monitor, "read_battery_status", side_effect=RuntimeError("API")), \
                contextlib.redirect_stdout(out):
            with self.assertRaisesRegex(RuntimeError, "API"):
                monitor.main([])
        self.assertTrue(out.getvalue().endswith("\x1b[J\x1b[?25h"))
        self.assertNotIn("Battery monitor stopped.", out.getvalue())


if __name__ == "__main__":
    unittest.main()
