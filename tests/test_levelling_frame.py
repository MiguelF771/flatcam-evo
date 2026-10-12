#!/usr/bin/env python
"""
Frame of the height map: the work coordinate system (G54..G59.3) and the units
(G20/G21) of the probing. A move in another frame is never compensated, and a
fresh state has no default start position.

Run: python -m pytest tests/test_levelling_frame.py -q
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from appPlugins import levelling_interp as li
from appPlugins.levelling_interp import level_gcode_line, new_levelling_state

DEC = 4
UP = 0.5


def up(x, y):
    """A height map that raises everything by 0.5, so a compensated line always differs."""
    return UP


def run(state, *lines):
    return [level_gcode_line(line, state, up, DEC) for line in lines]


def fresh(units='MM', *lines):
    """A state of the given job units, positioned by 'G90 G0 X0 Y0 Z5' after the given lines."""
    state = new_levelling_state(units=units)
    run(state, *lines)
    run(state, 'G90 G0 X0 Y0 Z5')
    return state


class TestUnitsState(unittest.TestCase):
    def test_new_keys(self):
        state = new_levelling_state()
        self.assertIsNone(state['home_units'])
        self.assertIsNone(state['units'])
        self.assertIsNone(state['home_wcs'])
        self.assertIsNone(state['wcs'])
        self.assertEqual(state['frame'], 0)

    def test_units_argument(self):
        for text, code in (('MM', 21), ('IN', 20), ('mm', 21), ('in', 20), (None, None)):
            state = new_levelling_state(units=text)
            self.assertEqual(state['home_units'], code)
            self.assertEqual(state['units'], code)

    def test_arc_segment_is_still_the_first_argument(self):
        self.assertEqual(new_levelling_state(0.25)['arc_segment'], 0.25)


class TestUnits(unittest.TestCase):
    def test_header_then_cuts_are_compensated(self):
        state = new_levelling_state(units='MM')
        out = run(state, 'G21', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1')
        self.assertEqual(out[-1], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(state['frame'], 0)

    def test_other_units_stop_the_compensation_and_the_same_come_back(self):
        state = fresh('MM')
        out = run(state, 'G1 X1 Y1 Z-1', 'G20', 'G1 X2 Y2 Z-1', 'X3 Y3', 'G91 G1 X1', 'G90')
        self.assertEqual(out[0], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(out[2:5], ['G1 X2 Y2 Z-1', 'X3 Y3', 'G91 G1 X1'])
        self.assertEqual(state['frame'], 3)
        out = run(state, 'G21', 'G1 X4 Y4 Z-1')
        self.assertEqual(out[-1], 'G1 X4 Y4 Z-0.5000')
        self.assertEqual(state['frame'], 3)

    def test_the_axes_are_lost_by_a_units_word(self):
        state = fresh('MM')
        run(state, 'G1 X1 Y1 Z-1', 'G20')
        for axis in 'XYZ':
            self.assertIsNone(state[axis])
        self.assertEqual(state['known'], set())

    def test_same_units_word_keeps_the_axes(self):
        state = fresh('MM')
        run(state, 'G21')
        self.assertEqual(state['known'], {'X', 'Y', 'Z'})
        self.assertEqual(state['units'], 21)

    def test_job_in_inches_with_a_metric_program(self):
        state = new_levelling_state(units='IN')
        out = run(state, 'G21', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1')
        self.assertEqual(out[-2:], ['G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1'])
        self.assertEqual(state['frame'], 2)

    def test_job_in_inches_with_an_inch_program(self):
        state = new_levelling_state(units='IN')
        out = run(state, 'G20', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1')
        self.assertEqual(out[-1], 'G1 X1 Y1 Z-0.5000')

    def test_unknown_job_units_take_the_first_word_before_a_move(self):
        state = new_levelling_state()
        out = run(state, 'G20', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1')
        self.assertEqual(state['home_units'], 20)
        self.assertEqual(out[-1], 'G1 X1 Y1 Z-0.5000')

    def test_unknown_job_units_a_different_later_word_stops_the_compensation(self):
        state = new_levelling_state()
        out = run(state, 'G21', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1', 'G20', 'G1 X2 Y2 Z-1',
                  'G21', 'G1 X3 Y3 Z-1')
        self.assertEqual(state['home_units'], 21)
        self.assertEqual(out[3], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(out[5], 'G1 X2 Y2 Z-1')
        self.assertEqual(out[7], 'G1 X3 Y3 Z-0.5000')
        self.assertEqual(state['frame'], 1)

    def test_unknown_job_units_a_first_word_after_a_move_is_not_home(self):
        state = new_levelling_state()
        out = run(state, 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1', 'G21', 'G1 X2 Y2 Z-1')
        self.assertIsNone(state['home_units'])
        self.assertEqual(out[2], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(out[4], 'G1 X2 Y2 Z-1')
        self.assertEqual(state['frame'], 1)

    def test_units_word_on_a_line_that_is_not_understood(self):
        state = fresh('MM')
        out = run(state, 'G20 X[1]', 'G21', 'G1 X1 Y1 Z-1')
        self.assertEqual(state['units'], 21)
        self.assertEqual(state['unparsed'], 1)
        self.assertEqual(out[-1], 'G1 X1 Y1 Z-0.5000')
        state = fresh('MM')
        run(state, 'G20 X[1]')
        self.assertEqual(state['units'], 20)

    def test_units_word_with_a_move_on_the_same_line(self):
        state = fresh('MM')
        out = run(state, 'G20 G1 X1 Y1 Z-1')
        self.assertEqual(out[0], 'G20 G1 X1 Y1 Z-1')
        self.assertEqual(state['frame'], 1)


class TestWcs(unittest.TestCase):
    def test_header_then_cuts_are_compensated(self):
        state = new_levelling_state(units='MM')
        out = run(state, 'G54', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1')
        self.assertEqual(state['home_wcs'], 54)
        self.assertEqual(out[-1], 'G1 X1 Y1 Z-0.5000')

    def test_other_wcs_stops_the_compensation_and_the_same_comes_back(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G55', 'G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1', 'G54', 'G1 X3 Y3 Z-1')
        self.assertEqual(out[1:3], ['G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1'])
        self.assertEqual(out[4], 'G1 X3 Y3 Z-0.5000')
        self.assertEqual(state['frame'], 2)

    def test_wcs_word_loses_the_axes(self):
        state = fresh('MM', 'G54')
        run(state, 'G55')
        for axis in 'XYZ':
            self.assertIsNone(state[axis])
        self.assertEqual(state['known'], set())
        out = run(state, 'G54', 'G1 X5')
        self.assertEqual(out[1], 'G1 X5')
        self.assertEqual(state['unknown'], 1)

    def test_wcs_and_move_on_one_line(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G55 G1 X1 Y1 Z-1')
        self.assertEqual(out[0], 'G55 G1 X1 Y1 Z-1')
        self.assertEqual(state['frame'], 1)

    def test_wcs_after_the_first_move_without_a_header_wcs(self):
        state = new_levelling_state(units='MM')
        out = run(state, 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1', 'G54', 'G1 X2 Y2 Z-1', 'G1 X3 Y3 Z-1')
        self.assertIsNone(state['home_wcs'])
        self.assertEqual(out[2], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(out[4:], ['G1 X2 Y2 Z-1', 'G1 X3 Y3 Z-1'])
        self.assertEqual(state['frame'], 2)

    def test_every_wcs_word_is_a_frame_word(self):
        for code in ('G55', 'G56', 'G57', 'G58', 'G59', 'G59.1', 'G59.2', 'G59.3'):
            state = fresh('MM', 'G54')
            out = run(state, code, 'G1 X1 Y1 Z-1')
            self.assertEqual(out[1], 'G1 X1 Y1 Z-1', code)
            self.assertEqual(state['frame'], 1, code)

    def test_g59_n_are_modal_settings(self):
        for code in (59.1, 59.2, 59.3):
            self.assertIn(code, li._MODAL_SETTING_G_CODES)

    def test_g59_1_with_axes_under_g1_is_a_move_not_a_non_motion_line(self):
        state = fresh('MM', 'G54')
        run(state, 'G1 X0 Y0 Z-1')
        out = run(state, 'G59.1 X1 Y1')
        self.assertEqual(out[0], 'G59.1 X1 Y1')
        self.assertEqual(state['frame'], 1)
        self.assertEqual(state['known'], {'X', 'Y'})
        self.assertEqual((state['X'], state['Y']), (1.0, 1.0))


class TestOutOfFrameMoves(unittest.TestCase):
    def test_arc_is_not_split_and_keeps_its_g_word(self):
        state = fresh('MM', 'G54')
        run(state, 'G1 X10 Y0 Z-1')
        out = run(state, 'G55', 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0', 'X-10 Y0 I0 J-10')
        self.assertEqual(out[2], 'G3 X0 Y10 I-10 J0')
        self.assertEqual(out[3], 'G3 X-10 Y0 I0 J-10')
        self.assertEqual(state['arcs'], 2)
        self.assertEqual(state['arcs_split'], 0)
        self.assertEqual(state['frame'], 1)

    def test_arc_in_other_units_is_not_split(self):
        state = fresh('MM')
        run(state, 'G1 X10 Y0 Z-1', 'G20')
        out = run(state, 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0')
        self.assertEqual(out[1], 'G3 X0 Y10 I-10 J0')
        self.assertEqual(state['arcs'], 1)

    def test_arc_in_the_frame_is_split(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0')
        self.assertNotIn('G3', out[1])
        self.assertEqual(state['arcs_split'], 1)

    def test_relative_moves_are_not_changed_and_are_counted(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G55', 'G91', 'G1 X1 Y1 Z-1', 'G1 Z-0.5')
        self.assertEqual(out[2:], ['G1 X1 Y1 Z-1', 'G1 Z-0.5'])
        self.assertEqual(state['frame'], 2)
        self.assertEqual(state['relative'], 0)

    def test_rapids_and_non_motion_lines_are_not_counted(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G55', 'G0 X1 Y1 Z5', 'G4 P1', 'M3 S1000', 'G1 F100')
        self.assertEqual(out[1:], ['G0 X1 Y1 Z5', 'G4 P1', 'M3 S1000', 'G1 F100'])
        self.assertEqual(state['frame'], 0)

    def test_axes_are_tracked_out_of_frame(self):
        state = fresh('MM', 'G54')
        run(state, 'G55', 'G90', 'G1 X1 Y2 Z-1')
        self.assertEqual((state['X'], state['Y'], state['Z']), (1.0, 2.0, -1.0))
        self.assertEqual(state['known'], {'X', 'Y', 'Z'})
        run(state, 'G91', 'G1 X1')
        self.assertEqual(state['X'], 2.0)

    def test_applied_after_an_absolute_z_out_of_frame(self):
        state = fresh('MM', 'G54')
        run(state, 'G1 X1 Y1 Z-1')
        self.assertEqual(state['applied'], UP)
        run(state, 'G55', 'G1 X2 Y2 Z-1')
        self.assertEqual(state['applied'], 0.0)

    def test_applied_is_kept_by_other_out_of_frame_moves(self):
        state = fresh('MM', 'G54')
        run(state, 'G1 X1 Y1 Z-1', 'G55')
        run(state, 'G1 X2 Y2')
        self.assertEqual(state['applied'], UP)
        run(state, 'G91', 'G1 X1 Z-1')
        self.assertEqual(state['applied'], UP)


class TestBlockDelete(unittest.TestCase):
    def test_optional_units_word_makes_the_units_unknown(self):
        state = fresh('MM')
        out = run(state, '/G20', 'G21', 'G1 X1 Y1 Z-1')
        self.assertEqual(out[0], '/G20')
        self.assertEqual(out[2], 'G1 X1 Y1 Z-0.5000')
        state = fresh('MM')
        out = run(state, '/G20', 'G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1')
        self.assertIsNone(state['units'])
        self.assertEqual(out[1:], ['G1 X1 Y1 Z-1', 'G1 X2 Y2 Z-1'])
        self.assertEqual(state['frame'], 2)

    def test_optional_units_word_needs_absolute_words_again(self):
        state = fresh('MM')
        out = run(state, '/G20', 'G21', 'G1 X1', 'G1 Y1 Z-1')
        self.assertEqual(out[2], 'G1 X1')
        self.assertEqual(state['unknown'], 1)
        self.assertEqual(out[3], 'G1 Y1 Z-0.5000')

    def test_optional_wcs_word_makes_the_wcs_unknown(self):
        state = fresh('MM', 'G54')
        out = run(state, '/G55', 'G1 X1 Y1 Z-1')
        self.assertIsNone(state['wcs'])
        self.assertEqual(out[1], 'G1 X1 Y1 Z-1')
        self.assertEqual(state['frame'], 1)
        out = run(state, 'G54', 'G1 X2 Y2 Z-1')
        self.assertEqual(out[1], 'G1 X2 Y2 Z-0.5000')

    def test_optional_word_that_repeats_the_frame_changes_nothing(self):
        state = fresh('MM', 'G54')
        out = run(state, '/G21', '/G54', 'G1 X1 Y1 Z-1')
        self.assertEqual(out[2], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(state['units'], 21)
        self.assertEqual(state['wcs'], 54)

    def test_optional_word_after_a_move_with_unknown_job_units(self):
        state = new_levelling_state()
        out = run(state, 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1', '/G20', 'G1 X2 Y2 Z-1')
        self.assertEqual(out[2], 'G1 X1 Y1 Z-0.5000')
        self.assertEqual(out[4], 'G1 X2 Y2 Z-1')
        self.assertEqual(state['frame'], 1)

    def test_optional_header_word_is_not_trusted(self):
        state = new_levelling_state()
        out = run(state, '/G21', 'G90', 'G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1')
        self.assertEqual(out[-1], 'G1 X1 Y1 Z-1')
        self.assertEqual(state['frame'], 1)


class TestNoDefaultStart(unittest.TestCase):
    def test_new_state_has_no_position(self):
        state = new_levelling_state()
        for axis in 'XYZ':
            self.assertIsNone(state[axis])
        self.assertEqual(state['known'], set())

    def test_cuts_with_an_unknown_axis_are_not_changed_and_are_counted(self):
        state = new_levelling_state()
        out = run(state, 'G1 Z-1', 'X10')
        self.assertEqual(out, ['G1 Z-1', 'X10'])
        self.assertEqual(state['unknown'], 2)

    def test_a_cut_that_completes_the_position_is_compensated(self):
        state = new_levelling_state()
        out = run(state, 'G1 Z-1', 'X10 Y5')
        self.assertEqual(out[0], 'G1 Z-1')
        self.assertEqual(out[1], 'X10 Y5 Z-0.5000')
        self.assertEqual(state['unknown'], 1)

    def test_positioned_first_then_compensated(self):
        state = new_levelling_state()
        out = run(state, 'G0 X0 Y0 Z5', 'G1 Z-1', 'X10 Y5')
        self.assertEqual(out[1], 'G1 Z-0.5000')
        self.assertEqual(out[2], 'X10 Y5 Z-0.5000')
        self.assertEqual(state['unknown'], 0)

    def test_relative_moves_from_an_unknown_start(self):
        state = new_levelling_state()
        out = run(state, 'G91', 'G1 X1 Y1 Z-1', 'G1 X1')
        self.assertEqual(out[1:], ['G1 X1 Y1 Z-1', 'G1 X1'])
        self.assertEqual(state['relative'], 2)

    def test_arc_from_an_unknown_start(self):
        state = new_levelling_state()
        out = run(state, 'G3 X0 Y10 I-10 J0')
        self.assertEqual(out[0], 'G3 X0 Y10 I-10 J0')
        self.assertEqual(state['arcs'], 1)


class TestToolPassesTheUnits(unittest.TestCase):
    def tool_and_target(self, gcode, units='MM', **target_attrs):
        from tests.test_levelling_audit import make_audit_tool
        tool = make_audit_tool(units=units or 'MM')
        tool._al_prepare_interp = MagicMock(return_value=True)
        tool._al_offset_fn = up
        tool._al_decimals = 4
        tool._al_points = [(0.0, 0.0, 0.5)]
        target = SimpleNamespace(tools={1: {'gcode': gcode}}, **target_attrs)
        if units is not None:
            target.units = units
        return tool, target

    def units_used(self, **kwargs):
        from tests.test_levelling_audit import tl_module
        tool, target = self.tool_and_target('G0 X0 Y0 Z5\n', **kwargs)
        with patch.object(tl_module, 'new_levelling_state', wraps=li.new_levelling_state) as spy:
            tool.autolevell_gcode(target, 'v')
        return spy.call_args.kwargs['units']

    def test_job_units_are_passed(self):
        self.assertEqual(self.units_used(units='MM'), 'MM')
        self.assertEqual(self.units_used(units='IN'), 'IN')

    def test_target_without_units(self):
        self.assertIsNone(self.units_used(units=None))

    def test_frame_warning(self):
        from tests.test_levelling_audit import emitted
        tool, target = self.tool_and_target('G21\nG0 X0 Y0 Z5\nG55\nG1 X1 Y1 Z-1\nG1 X2 Y2 Z-1\n')
        result = tool.autolevell_gcode(target, 'v')
        self.assertIn('G1 X1 Y1 Z-1', result[1])
        self.assertTrue(any('Moves in another coordinate system or units were not height-compensated: 2'
                            in m and m.startswith('[WARNING_NOTCL]') for m in emitted(tool)))

    def test_no_frame_warning_in_the_frame(self):
        from tests.test_levelling_audit import emitted
        tool, target = self.tool_and_target('G21\nG54\nG0 X0 Y0 Z5\nG1 X1 Y1 Z-1\n')
        result = tool.autolevell_gcode(target, 'v')
        self.assertIn('G1 X1 Y1 Z-0.5000', result[1])
        self.assertFalse(any('another coordinate system' in m for m in emitted(tool)))

    def test_inch_job_with_a_metric_program_warns(self):
        from tests.test_levelling_audit import emitted
        tool, target = self.tool_and_target('G21\nG0 X0 Y0 Z5\nG1 X1 Y1 Z-1\n', units='IN')
        tool.autolevell_gcode(target, 'v')
        self.assertTrue(any('another coordinate system or units' in m and m.endswith(': 1')
                            for m in emitted(tool)))


if __name__ == '__main__':
    unittest.main()
