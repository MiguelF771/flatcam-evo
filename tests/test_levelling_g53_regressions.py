#!/usr/bin/env python
"""G53 modal-motion and work-position safety regressions."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from appPlugins.levelling_interp import level_gcode_line, new_levelling_state


class TestG53Regressions(unittest.TestCase):
    @staticmethod
    def offset_fn(x, y):
        return 0.01 * x + 0.02 * y + 0.5

    def setUp(self):
        self.state = new_levelling_state()
        level_gcode_line("G1 X0 Y0 Z-1", self.state, self.offset_fn)

    def test_g53_g0_keeps_following_bare_xy_rapid(self):
        line = "G90 G53 G0 Z0"
        self.assertEqual(level_gcode_line(line, self.state, self.offset_fn), line)
        self.assertEqual(
            level_gcode_line("X1 Y1", self.state, self.offset_fn), "X1 Y1"
        )
        self.assertEqual(self.state['G'], 0)

    def test_g53_g1_selects_modal_feed_from_rapid(self):
        level_gcode_line("G0 X0 Y0 Z2", self.state, self.offset_fn)
        line = "G53 G1 Z0 (machine move)"
        self.assertEqual(level_gcode_line(line, self.state, self.offset_fn), line)
        self.assertEqual(self.state['G'], 1)

    def test_g53_g1_does_not_reuse_stale_work_z(self):
        line = "G53 G1 Z0"
        self.assertEqual(level_gcode_line(line, self.state, self.offset_fn), line)
        self.assertEqual(
            level_gcode_line("X1 Y1", self.state, self.offset_fn), "X1 Y1"
        )
        self.assertEqual(
            level_gcode_line("G1 X2 Y3 Z-1", self.state, self.offset_fn),
            "G1 X2 Y3 Z-0.4200"
        )
        self.assertEqual(
            level_gcode_line("X3 Y4", self.state, self.offset_fn),
            "X3 Y4 Z-0.3900"
        )

    def test_g53_xy_requires_known_work_xy_before_compensation(self):
        line = "G53 G1 X100 Y200"
        self.assertEqual(level_gcode_line(line, self.state, self.offset_fn), line)
        self.assertEqual(
            level_gcode_line("G1 Z-2", self.state, self.offset_fn), "G1 Z-2"
        )
        self.assertEqual(
            level_gcode_line("X1", self.state, self.offset_fn), "X1"
        )
        self.assertEqual(
            level_gcode_line("Y2", self.state, self.offset_fn), "Y2 Z-1.4500"
        )

    def test_relative_work_move_cannot_restore_unknown_z(self):
        line = "G53 G1 Z0"
        self.assertEqual(level_gcode_line(line, self.state, self.offset_fn), line)
        line = "G91 G1 X1 Y1 Z-1"
        self.assertEqual(level_gcode_line(line, self.state, self.offset_fn), line)
        self.assertFalse(self.state['abs'])
        self.assertEqual(self.state['relative'], 1)
        self.assertEqual(
            level_gcode_line("G90 X2 Y2", self.state, self.offset_fn), "G90 X2 Y2"
        )
        self.assertTrue(self.state['abs'])
        self.assertEqual(
            level_gcode_line("G1 X2 Y2 Z-1", self.state, self.offset_fn),
            "G1 X2 Y2 Z-0.4400"
        )


class TestG53Compatibility(unittest.TestCase):
    @staticmethod
    def offset_fn(x, y):
        return 0.5

    def test_distance_modes_on_g53_remain_modal(self):
        state = new_levelling_state()
        for line, absolute in [("G91 G53 G0 Z0", False), ("G90 G53 G0 Z0", True)]:
            self.assertEqual(level_gcode_line(line, state, self.offset_fn), line)
            self.assertEqual(state['abs'], absolute)

    def test_ordinary_rapids_and_positive_z_feed_policy(self):
        state = new_levelling_state()
        line = "G0 X0 Y0 Z2"
        self.assertEqual(level_gcode_line(line, state, self.offset_fn), line)
        self.assertEqual(
            level_gcode_line("G1 X1 Y1", state, self.offset_fn),
            "G1 X1 Y1 Z2.5000"
        )
        # relative moves from a known position are compensated too (G91 delta)
        self.assertEqual(
            level_gcode_line("G91 G1 X1 Y1 Z1", state, self.offset_fn),
            "G91 G1 X1 Y1 Z1.0000"
        )
        self.assertEqual(
            level_gcode_line("G90 G1 X3 Y3", state, self.offset_fn),
            "G90 G1 X3 Y3 Z3.5000"
        )

    def test_existing_nonmotion_compatibility(self):
        state = new_levelling_state()
        level_gcode_line("G1 X1 Y2 Z-1", state, self.offset_fn)
        before = state.copy()
        for line in ["G21 G90 G17 G94","M3 S1000", "(G53 G0 Z0)"]:
            self.assertEqual(level_gcode_line(line, state, self.offset_fn), line)
            self.assertEqual(state, before)
        self.assertEqual(
            level_gcode_line("X2 Y3", state, self.offset_fn), "X2 Y3 Z-0.5000"
        )


if __name__ == '__main__':
    unittest.main()
