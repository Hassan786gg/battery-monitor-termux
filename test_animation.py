"""Runtime regressions for low-flicker animation and reduced-motion controls."""
import contextlib
from datetime import datetime, timezone
import io
import os
import re
import unittest
from unittest import mock

import live_battery_monitor as monitor

DATA={'percentage':75,'status':'CHARGING','plugged':'AC','health':'GOOD','temperature':32.5,'voltage':4224,'current':2472000}

class AnimationTests(unittest.TestCase):
    def test_titled_frame_edges_align_at_phone_and_desktop_widths(self):
        with mock.patch.object(monitor,'USE_COLOR',False):
            for columns in (20,32,40,80,130):
                frame=monitor.render_frame(DATA,columns=columns,rows=40)
                lines=frame.splitlines()
                self.assertEqual(monitor.cell_width(lines[0]),monitor.cell_width(lines[-2]))
                self.assertEqual(monitor.cell_width(lines[0]),monitor.cell_width(lines[1]))

    def test_dirty_writer_skips_unchanged_and_writes_only_changed_row(self):
        writer=monitor.FrameWriter()
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            writer.write('header\nfirst\nfooter')
            out.seek(0);out.truncate()
            writer.write('header\nfirst\nfooter')
            self.assertEqual(out.getvalue(),'')
            writer.write('header\nsecond\nfooter')
        self.assertIn('second',out.getvalue())
        self.assertNotIn('header',out.getvalue())
        self.assertNotIn('footer',out.getvalue())
        self.assertNotIn('\x1b[2J',out.getvalue())

    def test_wait_animates_at_most_four_times_per_second_without_api_reads(self):
        clock=[0.0]
        sleeps=[]
        updated=datetime(2026,10,21,10,50,tzinfo=timezone.utc)
        def sleep(seconds):
            sleeps.append(seconds);clock[0]+=seconds
        with mock.patch.object(monitor,'TTY',True), \
             mock.patch.object(monitor.time,'monotonic',side_effect=lambda:clock[0]), \
             mock.patch.object(monitor.time,'sleep',side_effect=sleep), \
             mock.patch.object(monitor,'display') as display, \
             mock.patch.object(monitor,'read_battery_status') as api:
            monitor.wait_for_refresh(DATA,animate=True,updated_at=updated)
        api.assert_not_called()
        self.assertEqual(clock[0],monitor.POLL_SECONDS)
        self.assertTrue(all(0<duration<=0.25 for duration in sleeps))
        self.assertLessEqual(display.call_count,monitor.POLL_SECONDS*4)
        self.assertGreater(display.call_count,0)
        for call in display.call_args_list:
            self.assertIs(call.args[0],DATA)
            self.assertEqual(call.kwargs['updated_at'],updated)

    def test_non_tty_and_disabled_animation_sleep_once_without_redraw(self):
        for tty,animate in ((False,True),(True,False),(False,False)):
            with self.subTest(tty=tty,animate=animate), mock.patch.object(monitor,'TTY',tty), \
                 mock.patch.object(monitor.time,'monotonic',return_value=0), \
                 mock.patch.object(monitor.time,'sleep') as sleep, \
                 mock.patch.object(monitor,'display') as display:
                monitor.wait_for_refresh(DATA,animate=animate)
                display.assert_not_called()
                sleep.assert_called_once_with(monitor.POLL_SECONDS)

    def test_sample_timestamp_stays_fixed_across_animation_phases(self):
        updated=datetime(2026,10,21,10,50,tzinfo=timezone.utc)
        with mock.patch.object(monitor,'USE_COLOR',False):
            frames=[monitor.render_frame(DATA,columns=100,rows=40,animation_step=i,updated_at=updated) for i in range(3)]
        timestamp=[line for line in frames[0].splitlines() if 'Updated' in line][0]
        self.assertIn('10:50:00',timestamp)
        for frame in frames:
            self.assertIn(timestamp,frame)
            self.assertIn('75%',frame)
            self.assertIn('2472 mA',frame)

    def test_cli_and_environment_disable_motion(self):
        for args,environment,expected in (([],{},True),(['--no-animation'],{},False),([] ,{'BATTERY_MONITOR_NO_ANIMATION':'1'},False)):
            with self.subTest(args=args,environment=environment), \
                 mock.patch.dict(os.environ,environment,clear=True), \
                 mock.patch.object(monitor,'TTY',False), \
                 mock.patch.object(monitor,'read_battery_status',return_value=(dict(DATA),None)), \
                 mock.patch.object(monitor,'read_power_io',return_value={}), \
                 mock.patch.object(monitor,'display'), \
                 mock.patch.object(monitor,'wait_for_refresh',side_effect=KeyboardInterrupt) as wait, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(monitor.main(args),0)
                self.assertEqual(wait.call_args.args[2],expected)

    def test_cursor_is_restored_after_interruption(self):
        out=io.StringIO()
        with mock.patch.object(monitor,'TTY',True), \
             mock.patch.object(monitor,'read_battery_status',side_effect=KeyboardInterrupt), \
             contextlib.redirect_stdout(out):
            monitor.main([])
        self.assertIn('\x1b[?25l',out.getvalue())
        self.assertIn('\x1b[?25h',out.getvalue())

    def test_resizing_writer_does_not_address_rows_below_new_terminal(self):
        writer=monitor.FrameWriter()
        out=io.StringIO()
        with mock.patch.object(monitor,'TTY',True), mock.patch.object(monitor,'USE_COLOR',False), \
             mock.patch.object(monitor.shutil,'get_terminal_size',side_effect=[os.terminal_size((80,40)),os.terminal_size((32,8))]), \
             contextlib.redirect_stdout(out):
            monitor.display(DATA,writer=writer)
            out.seek(0);out.truncate()
            monitor.display(DATA,writer=writer)
        rows=[int(value) for value in re.findall(r'\x1b\[(\d+);\d+H',out.getvalue())]
        self.assertTrue(all(row<=8 for row in rows),rows)
        self.assertIn('75%',out.getvalue())

if __name__=='__main__':
    unittest.main()
