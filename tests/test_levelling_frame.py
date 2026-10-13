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


def spatial(x, y):
    """Different physical XY positions have different height offsets."""
    return 0.01 * x * y + 0.5


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
        self.assertTrue(any('Moves in another coordinate system or units, or after a change of the work '
                            'origin or of the Z zero, a subprogram call or a G word that can not be read, '
                            'were not height-compensated: 2'
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

    def test_expression_origin_shift_warns_for_later_cuts(self):
        from tests.test_levelling_audit import emitted
        for shift in ('G92 X[0] Y[0]', 'G10 L20 P0 X[0] Y[0]'):
            with self.subTest(shift=shift):
                tool, target = self.tool_and_target(
                    'G54\nG0 X10 Y10 Z5\n' + shift + '\nG0 X10 Y10 Z5\nG1 Z-1\n')
                tool._al_offset_fn = spatial
                result = tool.autolevell_gcode(target, 'v')
                self.assertIn(shift, result[1])
                self.assertIn('G1 Z-1\n', result[1])
                self.assertTrue(any('another coordinate system or units' in m and m.endswith(': 1')
                                    and m.startswith('[WARNING_NOTCL]') for m in emitted(tool)))


class TestNoDeclarationAfterAMove(unittest.TestCase):
    """A WCS or units word after a move never declares the frame, also when the position is lost."""

    def check_after(self, *lost):
        state = new_levelling_state(units='MM')
        out = run(state, 'G0 X10 Y10 Z5', 'G1 Z-1', *lost, 'G55', 'G0 X10 Y10 Z5', 'G1 Z-1')
        self.assertEqual(out[1], 'G1 Z-0.5000')
        self.assertEqual(out[-1], 'G1 Z-1')
        self.assertIsNone(state['home_wcs'])
        self.assertEqual(state['frame'], 1)

    def test_after_g28(self):
        self.check_after('G28')

    def test_after_g53_move(self):
        self.check_after('G53 G0 X0 Y0 Z0')

    def test_after_an_unparsed_line(self):
        self.check_after('G0 X[5]')

    def test_after_g92_1(self):
        self.check_after('G92.1')

    def test_after_a_relative_move(self):
        state = new_levelling_state(units='MM')
        out = run(state, 'G91 G0 X1', 'G90 G55', 'G0 X10 Y10 Z5', 'G1 Z-1')
        self.assertEqual(out[-1], 'G1 Z-1')
        self.assertEqual(state['frame'], 1)

    def test_units_word_after_a_lost_position(self):
        state = new_levelling_state()
        out = run(state, 'G0 X10 Y10 Z5', 'G1 Z-1', 'G28', 'G20', 'G0 X1 Y1 Z1', 'G1 Z-0.1')
        self.assertIsNone(state['home_units'])
        self.assertEqual(out[-1], 'G1 Z-0.1')
        self.assertEqual(state['frame'], 1)

    def test_wcs_on_the_first_move_still_declares(self):
        state = new_levelling_state(units='MM')
        out = run(state, 'G55 G0 X10 Y10 Z5', 'G1 Z-1')
        self.assertEqual(state['home_wcs'], 55.0)
        self.assertEqual(out[-1], 'G1 Z-0.5000')


class TestWorkOriginShift(unittest.TestCase):
    """A line that moves the X/Y work origin puts the rest of the program out of the frame."""

    def check_shift(self, shift):
        # the G54 header makes the later 'G54' a return to the frame of the map
        state = fresh('MM', 'G54')
        out = run(state, 'G1 Z-1', shift, 'G0 X10 Y10 Z5', 'G1 Z-1', 'G54', 'G0 X1 Y1 Z5', 'G1 Z-1')
        self.assertEqual(out[0], 'G1 Z-0.5000')
        self.assertEqual(out[3], 'G1 Z-1')
        self.assertEqual(out[6], 'G1 Z-1')
        self.assertEqual(state['frame'], 2)

    def test_g92_xy(self):
        self.check_shift('G92 X0 Y0')

    def test_g92_x_only(self):
        self.check_shift('G92 X0')

    def test_g10_l20(self):
        self.check_shift('G10 L20 P0 X0 Y0')

    def test_g10_l2(self):
        self.check_shift('G10 L2 P1 X5 Y5')

    def test_g10_l2_rotation(self):
        self.check_shift('G10 L2 P1 R30')

    def test_g92_1_2_3(self):
        for code in ('G92.1', 'G92.2', 'G92.3'):
            with self.subTest(code=code):
                self.check_shift(code)

    def test_shift_also_stops_arc_splitting(self):
        state = fresh('MM')
        out = run(state, 'G92 X0 Y0', 'G0 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0')
        self.assertNotIn('\n', out[-1])
        self.assertEqual(state['arcs'], 1)

    def test_z_zero_without_a_probe_ends_the_frame(self):
        # the Z reference of the map is lost: no later word can restore it
        for shift in ('G92 Z0', 'G10 L2 P1 Z-3', 'G10 L20 P0 Z0', 'G52 Z1'):
            with self.subTest(shift=shift):
                state = fresh('MM', 'G54')
                out = run(state, shift, 'G54', 'G0 X1 Y1 Z5', 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])
                self.assertEqual(state['frame'], 1)

    def test_z_zero_on_another_wcs_keeps_the_frame(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G10 L2 P2 Z-3', 'G10 L20 P3 Z0', 'G0 X1 Y1 Z5', 'G1 Z-1')
        self.assertEqual(out[-1], 'G1 Z-0.5000')
        self.assertFalse(state['shifted'])

    def test_optional_shift_also_stops_compensation(self):
        state = fresh('MM')
        out = run(state, '/G92 X0 Y0', 'G0 X10 Y10 Z5', 'G1 Z-1')
        self.assertEqual(out[-1], 'G1 Z-1')
        self.assertEqual(state['frame'], 1)


class TestFrameAuditRegressions(unittest.TestCase):
    def test_unresolved_origin_shift_cannot_resume_after_absolute_positions(self):
        for shift in ('G92 X[0] Y[0]', 'G92 X#1', 'G10 L20 P0 X[0] Y[0]',
                      'G10 L2 P1 X#1', 'G10 L2 P1 R[30]', 'G10 L[20] P1 X0',
                      'G10 L#1 P1 X0', '/G92 X[0]'):
            with self.subTest(shift=shift):
                state = new_levelling_state(units='MM')
                lines = ('G54', 'G0 X10 Y10 Z5', 'G1 Z-1', shift,
                         'G0 X10 Y10 Z5', 'G1 Z-1', 'G54', 'G0 X20 Y20 Z5', 'G1 Z-1')
                out = [level_gcode_line(line, state, spatial, DEC) for line in lines]
                self.assertEqual(out[2], 'G1 Z0.5000')
                self.assertEqual(out[3], shift)
                self.assertEqual(out[5], 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])
                self.assertEqual(state['frame'], 2)
                self.assertEqual(state['unparsed'], 1)

    def test_nonmoving_header_does_not_block_first_wcs(self):
        # a Z zero header (G92 Z0, G10 L20 P0 Z0, ...) ends the frame, see TestZZero
        for header in ('G10 L1 P1 X5', 'G10 L10 P1 Z0', 'G4 P1'):
            with self.subTest(header=header):
                state = new_levelling_state(units='MM')
                self.assertEqual(level_gcode_line(header, state, spatial, DEC), header)
                self.assertFalse(state['moved'])
                out = [level_gcode_line(line, state, spatial, DEC)
                       for line in ('G54', 'G0 X10 Y10 Z5', 'G1 Z-1')]
                self.assertEqual(state['home_wcs'], 54)
                self.assertEqual(out[-1], 'G1 Z0.5000')
                self.assertEqual(state['frame'], 0)

    def test_possible_motion_still_blocks_first_wcs(self):
        for move in ('G28', 'G30', 'G53 G0 X0', 'G81 X1 Y1', 'G123 X1', 'G0 X[1]'):
            with self.subTest(move=move):
                state = new_levelling_state(units='MM')
                level_gcode_line(move, state, spatial, DEC)
                self.assertTrue(state['moved'])
                out = [level_gcode_line(line, state, spatial, DEC)
                       for line in ('G54', 'G0 X10 Y10 Z5', 'G1 Z-1')]
                self.assertIsNone(state['home_wcs'])
                self.assertEqual(out[-1], 'G1 Z-1')

    def test_g10_targets_map_not_just_active_wcs(self):
        wcs_words = ('G54', 'G55', 'G56', 'G57', 'G58', 'G59', 'G59.1', 'G59.2', 'G59.3')
        for home_p, home in enumerate(wcs_words, 1):
            for active in (home, 'G55' if home == 'G54' else 'G54'):
                for p in range(10):
                    for change in ('L2 X5 Y5', 'L20 X0 Y0', 'L2 R30', 'L20 X[0] Y#1'):
                        with self.subTest(home=home, active=active, p=p, change=change):
                            state = fresh('MM', home)
                            level_gcode_line(active, state, spatial, DEC)
                            shift = 'G10 P%d %s' % (p, change)
                            self.assertEqual(level_gcode_line(shift, state, spatial, DEC), shift)
                            shifted = (active == home if p == 0 else p == home_p)
                            out = [level_gcode_line(line, state, spatial, DEC)
                                   for line in (home, 'G0 X10 Y10 Z5', 'G1 Z-1')]
                            self.assertEqual(out[-1], 'G1 Z-1' if shifted else 'G1 Z0.5000')
                            self.assertEqual(state['shifted'], shifted)
                            self.assertEqual(state['frame'], int(shifted))

    def test_unknown_g10_target_is_conservative(self):
        for p_word in ('', 'P[2]', 'P#1', 'P-1', 'P10', 'P1.5', 'P2[0]', 'P2 P3'):
            for l_word in ('L2', 'L20'):
                with self.subTest(p_word=p_word, l_word=l_word):
                    state = fresh('MM', 'G54')
                    shift = 'G10 %s %s X0 Y0' % (l_word, p_word)
                    level_gcode_line(shift, state, spatial, DEC)
                    out = [level_gcode_line(line, state, spatial, DEC)
                           for line in ('G0 X10 Y10 Z5', 'G1 Z-1')]
                    self.assertEqual(out[-1], 'G1 Z-1')
                    self.assertTrue(state['shifted'])
                    self.assertEqual(state['frame'], 1)

    def test_p0_with_unknown_active_wcs_is_conservative(self):
        for prefix in ((), ('G54', '/G55')):
            with self.subTest(prefix=prefix):
                state = new_levelling_state(units='MM')
                run(state, *prefix)
                run(state, 'G10 L2 P0 X5 Y5', 'G54', 'G0 X10 Y10 Z5')
                self.assertEqual(level_gcode_line('G1 Z-1', state, spatial, DEC), 'G1 Z-1')
                self.assertTrue(state['shifted'])

    def test_expression_z_zero_ends_the_frame(self):
        # the new Z value can not be read, so the map can not be re-referenced
        for shift in ('G92 Z[0]', 'G10 L2 P1 Z#1', 'G10 L20 P0 Z[0]'):
            with self.subTest(shift=shift):
                # the probe is inside the map, so only the expression stops the re-reference
                state = new_levelling_state(units='MM', map_bounds=(0, 0, 20, 20))
                run(state, 'G54', 'G0 X10 Y10 Z5', 'G38.2 Z-5')
                out = [level_gcode_line(line, state, spatial, DEC)
                       for line in (shift, 'G0 X10 Y10 Z5', 'G1 Z-1')]
                self.assertEqual(out[0], shift)
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])

    def test_local_offset_scale_and_rotation_shift_the_frame(self):
        for shift in ('G52 X10 Y10', 'G52 Y5', 'G68 X0 Y0 R30', 'G68 R30', 'G51 X0 Y0 P2', 'G51 P2'):
            with self.subTest(shift=shift):
                state = fresh('MM', 'G54')
                out = run(state, shift, 'G54', 'G0 X10 Y10 Z5', 'G1 Z-1')
                self.assertEqual(out[0], shift)
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])
                self.assertEqual(state['frame'], 1)

    def test_z_only_local_offset_ends_the_frame(self):
        state = fresh('MM', 'G54')
        out = run(state, 'G52 Z1', 'G0 X10 Y10 Z5', 'G1 Z-1')
        self.assertEqual(out[-1], 'G1 Z-1')
        self.assertTrue(state['shifted'])


class TestHiddenGWords(unittest.TestCase):
    """A G word that the tool can not read (lower case, expression) can be any G code."""

    def test_hidden_g_word_never_comes_back(self):
        # G92.1/.2/.3 reset the X/Y origin with no X/Y word; G21 G54 can not undo that
        for hidden in ('g20', 'G[20]', 'G#1', 'g55', 'g92.1', 'G[92.1]', 'g92.2', 'g92.3',
                       'g92 x0 y0', 'G[92] X0 Y0', 'G#5 Y0', 'g10 l20 p1 x0', 'g10 l2 p1 r30',
                       'g68 r30', 'G1 g52 x1'):
            with self.subTest(hidden=hidden):
                state = fresh('MM', 'G21 G54')
                out = run(state, 'G1 Z-1', hidden, 'G21 G54', 'G0 X10 Y10 Z5', 'G1 Z-1')
                self.assertEqual(out[0], 'G1 Z-0.5000')
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])
                self.assertEqual(state['frame'], 1)
                self.assertEqual(state['unparsed'], 1)

    def test_unparsed_line_without_a_hidden_g_word_keeps_the_frame(self):
        for line in ('X[5]', '#1=5', 'G0 X[5]', 'G1 F[100]'):
            with self.subTest(line=line):
                state = fresh('MM', 'G21 G54')
                out = run(state, line, 'G0 X10 Y10 Z5', 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-0.5000')
                self.assertFalse(state['shifted'])
                self.assertEqual(state['frame'], 0)


class TestSubprogramCall(unittest.TestCase):
    """The tool does not see the codes of a subprogram, so a call ends the frame."""

    def test_call_ends_the_frame(self):
        for call in ('O100 call', 'o100 call', 'O<probe> CALL', 'M98 P100', 'M98P100 L2',
                     'M098 P1', 'G65 P9001', 'N10 M98 P100', 'O100 sub', 'O100 endsub',
                     'o101 while [#1 LT 3]', 'O102 if [#1 GT 0]', 'O103 repeat [5]',
                     'O104 do', 'O100 return'):
            with self.subTest(call=call):
                state = fresh('MM', 'G21 G54')
                out = run(state, 'G1 Z-1', call, 'G21 G54', 'G0 X10 Y10 Z5', 'G1 Z-1')
                self.assertEqual(out[0], 'G1 Z-0.5000')
                self.assertEqual(out[1], call)
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])

    def test_other_words_are_not_a_call(self):
        for line in ('M9', 'M8', 'M3 S1000', 'M980', 'M98.1', 'O1234', 'O0001 (PCB)', 'M2'):
            with self.subTest(line=line):
                state = fresh('MM', 'G21 G54')
                out = run(state, line, 'G0 X10 Y10 Z5', 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-0.5000')
                self.assertFalse(state['shifted'])


class TestCutterCompensation(unittest.TestCase):
    """Under G41/G42 the tool is off the programmed X/Y, so the map is read at the wrong point."""

    def test_moves_under_g41_g42_are_not_compensated(self):
        for comp in ('G41', 'G41 D1', 'G42 D1', 'G41.1 D2', 'G42.1 D2 L3'):
            with self.subTest(comp=comp):
                state = fresh('MM', 'G21 G54')
                out = run(state, comp, 'G1 X1 Y1 Z-1', 'X2 Y2', 'G91 G1 X1 Z-0.1', 'G90',
                          'G40', 'G1 X3 Y3 Z-1')
                self.assertEqual(out[1:4], ['G1 X1 Y1 Z-1', 'X2 Y2', 'G91 G1 X1 Z-0.1'])
                self.assertEqual(out[-1], 'G1 X3 Y3 Z-0.5000')
                self.assertEqual(state['cutter'], 3)
                self.assertEqual(state['relative'], 0)

    def test_comp_on_the_move_line(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G41 D1 G1 X1 Y1 Z-1', 'G40 G1 X2 Y2 Z-1')
        self.assertEqual(out[0], 'G41 D1 G1 X1 Y1 Z-1')
        self.assertEqual(out[1], 'G40 G1 X2 Y2 Z-0.5000')
        self.assertEqual(state['cutter'], 1)

    def test_comp_word_under_g1_is_still_a_move(self):
        # G41 is a modal setting: "G41 X1 Y1" under a modal G1 is a cut
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G1 Z-1', 'G41 D1 X1 Y1')
        self.assertEqual(out[-1], 'G41 D1 X1 Y1')
        self.assertEqual(state['cutter'], 1)
        self.assertEqual(state['X'], 1.0)

    def test_arcs_under_comp_are_not_split(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G0 X10 Y0 Z-1', 'G41 D1', 'G3 X0 Y10 I-10 J0')
        self.assertNotIn('\n', out[-1])
        self.assertEqual(state['arcs'], 1)
        self.assertEqual(state['arcs_split'], 0)

    def test_comp_z_word_sets_the_offset_to_zero(self):
        state = fresh('MM', 'G21 G54')
        run(state, 'G1 Z-1', 'G41 D1', 'G1 X1 Z-2')
        self.assertEqual(state['applied'], 0.0)

    def test_optional_comp_words_make_comp_unknown(self):
        for lines in (('/G41 D1',), ('G41 D1', '/G40')):
            with self.subTest(lines=lines):
                state = fresh('MM', 'G21 G54')
                out = run(state, *lines, 'G1 X1 Y1 Z-1')
                self.assertEqual(out[-1], 'G1 X1 Y1 Z-1')
                self.assertIsNone(state['cutter_comp'])
                self.assertEqual(state['cutter'], 1)
                out = run(state, 'G40', 'G1 X1 Y1 Z-1')
                self.assertEqual(out[-1], 'G1 X1 Y1 Z-0.5000')

    def test_tool_warns(self):
        from tests.test_levelling_audit import emitted
        tool, target = TestToolPassesTheUnits().tool_and_target(
            'G21\nG0 X0 Y0 Z5\nG41 D1\nG1 X1 Y1 Z-1\nG1 X2 Y2\nG40\n')
        tool.autolevell_gcode(target, 'v')
        self.assertTrue(any('cutter radius compensation (G41/G42)' in m and m.endswith(': 2')
                            for m in emitted(tool)))


class TestLeadingComment(unittest.TestCase):
    """A comment at the start of a line can not hide the commands after it."""

    def test_commands_after_a_leading_comment_apply(self):
        cases = (('(enable cutter) G41 D1', 'cutter', 1),
                 ('(call) M98 P100', 'shifted', True),
                 ('(reset origin) G92 X0 Y0', 'shifted', True),
                 ('(a)(b) g92.1', 'shifted', True),
                 ('(c) O100 call', 'shifted', True),
                 ('(c) G55', 'frame', 1),
                 ('(c) G20', 'frame', 1))
        for line, key, value in cases:
            with self.subTest(line=line):
                state = fresh('MM', 'G21 G54')
                out = run(state, line, 'G1 X10 Y10 Z-1')
                self.assertEqual(out, [line, 'G1 X10 Y10 Z-1'])
                self.assertEqual(state[key], value)

    def test_move_after_a_leading_comment_is_compensated(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, '(cut) G1 X10 Y10 Z-1', '(next) X20 Y20', '(c) X30 (d) Y30 ; e')
        self.assertEqual(out, ['(cut) G1 X10 Y10 Z-0.5000', '(next) X20 Y20 Z-0.5000',
                               '(c) X30 (d) Y30 Z-0.5000 ; e'])

    def test_unsplit_arc_gets_its_g_word_after_the_comment(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G0 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0 G41 D1', '(c) N10 X-10 Y0 I0 J-10')
        self.assertEqual(out[-1], '(c) N10 G3 X-10 Y0 I0 J-10')

    def test_comment_lines_change_nothing(self):
        for line in ('(only a comment)', '; only a comment', '(a) (b)', '(a) ; b', '  (x) '):
            with self.subTest(line=line):
                state = fresh('MM', 'G21 G54')
                before = {k: (set(v) if isinstance(v, set) else v) for k, v in state.items()}
                self.assertEqual(level_gcode_line(line, state, up, DEC), line)
                self.assertEqual(state, before)


class TestSlashInComment(unittest.TestCase):
    """A "/" in a comment is not a block delete; only a "/" at the start of the line is."""

    def arc(self, line):
        state = fresh('MM', 'G21 G54')
        run(state, 'G0 X10 Y0 Z-1')
        return state, level_gcode_line(line, state, up, DEC).split('\n')

    def test_comment_with_slash_does_not_make_an_arc_optional(self):
        for line in ('(arc / cut) G3 X0 Y10 I-10 J0', 'G3 X0 Y10 I-10 J0 (arc / cut)',
                     '(a/b) G3 X0 Y10 I-10 J0'):
            with self.subTest(line=line):
                state, out = self.arc(line)
                self.assertGreater(len(out), 1)
                self.assertFalse(any(text.lstrip().startswith('/') for text in out))
                self.assertEqual(state['applied'], UP)
                self.assertEqual(state['known'], {'X', 'Y', 'Z'})

    def test_real_block_delete_arc_is_still_optional(self):
        for line in ('/(arc cut) G3 X0 Y10 I-10 J0', ' / G3 X0 Y10 I-10 J0 (a / b)'):
            with self.subTest(line=line):
                state, out = self.arc(line)
                self.assertGreater(len(out), 1)
                self.assertTrue(all(text.startswith('/') for text in out))
                self.assertIsNone(state['applied'])

    def test_comment_with_slash_does_not_make_a_line_optional(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, '(a/b) G1 X10 Y10 Z-1', 'G91 G1 X1 Z0')
        self.assertEqual(out, ['(a/b) G1 X10 Y10 Z-0.5000', 'G91 G1 X1 Z0.0000'])
        self.assertEqual(state['applied'], UP)
        self.assertEqual(state['relative'], 0)

    def test_unsplit_arc_with_slash_comment_stays_mandatory(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G0 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0 G41 D1', '(a / b) X-10 Y0 I0 J-10')
        self.assertEqual(out[-1], '(a / b) G3 X-10 Y0 I0 J-10')
        self.assertEqual(state['known'], {'X', 'Y', 'Z'})


def slope(x, y):
    """A sloped map: h(0,0) = 0, h(5,0) = 0.1, h(10,10) = 0.5."""
    return 0.02 * x + 0.03 * y


def run_on(fn, state, *lines):
    return [level_gcode_line(line, state, fn, DEC) for line in lines]


def mapped(*lines):
    """A state with a 20 x 20 map, in G54, at X0 Y0 Z5."""
    state = new_levelling_state(units='MM', map_bounds=(0, 0, 20, 20))
    run_on(slope, state, 'G21 G54', 'G0 X0 Y0 Z5', *lines)
    return state


class TestZZero(unittest.TestCase):
    """A Z zero change after a contact probe re-references the map to the probed point."""

    def test_mach3_toolchange_probe(self):
        state = mapped()
        out = run_on(slope, state, 'G00 X5 Y0', 'M0', 'F100', 'G31 Z-10', 'G92 Z0', 'G00 Z1',
                     'G31 Z-10', 'G92 Z0', 'G00 Z2', 'G00 X10 Y10', 'G01 Z-0.1', 'G01 X0 Y0')
        # -0.1 + h(10,10) - h(5,0) and -0.1 + h(0,0) - h(5,0)
        self.assertEqual(out[-2:], ['G01 Z0.3000', 'G01 X0 Y0 Z-0.2000'])
        self.assertAlmostEqual(state['z_ref'], 0.1)
        self.assertEqual(state['frame'], 0)

    def test_grbl_probe_and_g10_l20(self):
        for zero in ('G10 L20 P0 Z0', 'G10 L20 P1 Z0', 'G92 Z0'):
            with self.subTest(zero=zero):
                state = mapped()
                out = run_on(slope, state, 'G0 X5 Y0', 'G38.2 Z-10 F50', zero, 'G0 Z2',
                             'G0 X10 Y10', 'G1 Z-0.1')
                self.assertEqual(out[-1], 'G1 Z0.3000')

    def test_new_z_value_is_taken_into_account(self):
        # the touched surface becomes Z2: the surface at (10,10) is at 0.5 - 0.1 + 2
        state = mapped()
        out = run_on(slope, state, 'G0 X5 Y0', 'G38.2 Z-10', 'G92 Z2', 'G0 Z5', 'G0 X10 Y10', 'G1 Z-0.1')
        self.assertEqual(out[-1], 'G1 Z2.3000')

    def test_a_later_zero_replaces_the_reference(self):
        state = mapped()
        run_on(slope, state, 'G0 X5 Y0', 'G38.2 Z-10', 'G92 Z0', 'G0 Z5',
               'G0 X10 Y0', 'G38.2 Z-10', 'G92 Z0', 'G0 Z5')
        self.assertAlmostEqual(state['z_ref'], 0.2)
        out = run_on(slope, state, 'G0 X10 Y10', 'G1 Z-0.1')
        self.assertEqual(out[-1], 'G1 Z0.2000')

    def test_split_arc_and_relative_move_use_the_reference(self):
        state = mapped()
        run_on(slope, state, 'G0 X5 Y0', 'G38.2 Z-10', 'G92 Z0', 'G0 Z5', 'G0 X10 Y0', 'G1 Z-1')
        arc = level_gcode_line('G3 X0 Y10 I-10 J0', state, slope, DEC).split('\n')
        # -1 + h(0,10) - h(5,0)
        self.assertEqual(arc[-1], 'G1 X0.0000 Y10.0000 Z-0.8000')
        out = run_on(slope, state, 'G91 G1 X10 Z0')
        # from (0,10) to (10,10): the offset grows by 0.2
        self.assertEqual(out[-1], 'G91 G1 X10 Z0.2000')

    def test_no_reference_ends_the_frame(self):
        cases = {
            'probe outside the map': ('G0 X30 Y0', 'G38.2 Z-10', 'G92 Z0'),
            'probe with X/Y words': ('G0 X5 Y0', 'G38.2 X6 Z-10', 'G92 Z0'),
            'a move after the probe': ('G0 X5 Y0', 'G38.2 Z-10', 'G0 Z1', 'G92 Z0'),
            'probe without a contact check': ('G0 X5 Y0', 'G38.3 Z-10', 'G92 Z0'),
            'no probe': ('G0 X5 Y0', 'G92 Z0'),
            'G10 L2 sets the offset by value': ('G0 X5 Y0', 'G38.2 Z-10', 'G10 L2 P1 Z-3'),
            'optional zero': ('G0 X5 Y0', 'G38.2 Z-10', '/G92 Z0'),
            'optional probe': ('G0 X5 Y0', '/G38.2 Z-10', 'G92 Z0'),
            'probe at an unknown X/Y': ('G28', 'G38.2 Z-10', 'G92 Z0'),
        }
        for name, lines in cases.items():
            with self.subTest(name):
                state = mapped()
                out = run_on(slope, state, *lines, 'G0 Z5', 'G0 X10 Y10 Z5', 'G1 Z-0.1')
                self.assertEqual(out[-1], 'G1 Z-0.1')
                self.assertTrue(state['shifted'])

    def test_mach3_preprocessor_output(self):
        # the real start code and tool change code of Toolchange_Probe_MACH3
        from camlib import AttrDict
        from preprocessors.Toolchange_Probe_MACH3 import Toolchange_Probe_MACH3
        p = AttrDict(
            units='MM', decimals=4, coords_decimals=4, fr_decimals=2, tool=2, toolC=0.1,
            xy_toolchange=(5.0, 0.0), z_toolchange=15.0, startz=None, z_end=15.0, xy_end=None,
            z_move=2.0, z_p_depth=-10.0, feedrate_probe=100.0, f_plunge=False, toolchange=True,
            feedrate=100.0, z_feedrate=50.0, feedrate_rapid=500.0, z_cut=-0.1, multidepth=False,
            steps_per_circle=16, pp_geometry_name='Toolchange_Probe_MACH3', spindlespeed=10000,
            obj_options={'type': 'Geometry', 'tool_dia': 0.1, 'xmin': 0.0, 'xmax': 20.0,
                         'ymin': 0.0, 'ymax': 20.0})
        pp = Toolchange_Probe_MACH3()
        code = pp.start_code(p) + pp.toolchange_code(p)
        self.assertIn('G31 Z-10.0000', code)
        self.assertIn('G92 Z0', code)
        state = new_levelling_state(units='MM', map_bounds=(0, 0, 20, 20))
        run_on(slope, state, *code.splitlines())
        out = run_on(slope, state, 'G00 X10 Y10', 'G01 Z-0.1', 'G01 X0 Y0')
        # the probe touched the surface at (5, 0): -0.1 + h(x, y) - h(5, 0)
        self.assertEqual(out[1:], ['G01 Z0.3000', 'G01 X0 Y0 Z-0.2000'])
        self.assertEqual((state['frame'], state['shifted']), (0, False))

    def test_zero_in_another_frame_ends_the_frame(self):
        # "G20 G92 Z2" sets 2 inch, not 2 mm; G92 on G55 gives an unknown Z offset to G54
        cases = {
            'inch zero': ('G20', 'G92 Z2', 'G21'),
            'inch zero on the same line': ('G20 G92 Z2', 'G21'),
            'zero on another WCS': ('G55', 'G92 Z0', 'G54'),
            'G10 L20 P1 from another WCS': ('G55', 'G10 L20 P1 Z0', 'G54'),
            # a frame change after the probe ends the probe, also if the frame comes back
            'units change and back': ('G20', 'G21', 'G92 Z0'),
            'WCS change and back': ('G55', 'G54', 'G92 Z0'),
        }
        for name, lines in cases.items():
            with self.subTest(name):
                state = mapped()
                out = run_on(slope, state, 'G0 X5 Y0', 'G38.2 Z-10', *lines,
                             'G0 X10 Y10 Z5', 'G1 Z-0.1')
                self.assertEqual(out[-1], 'G1 Z-0.1')
                self.assertEqual(state['z_ref'], 0.0)
                self.assertTrue(state['shifted'])

    def test_no_map_bounds_no_reference(self):
        state = new_levelling_state(units='MM')
        out = run_on(slope, state, 'G0 X5 Y0 Z5', 'G38.2 Z-10', 'G92 Z0', 'G0 X10 Y10 Z5', 'G1 Z-0.1')
        self.assertEqual(out[-1], 'G1 Z-0.1')
        self.assertTrue(state['shifted'])

    def test_zero_on_another_wcs_changes_nothing(self):
        state = mapped()
        out = run_on(slope, state, 'G10 L20 P2 Z0', 'G0 X10 Y10 Z5', 'G1 Z-0.1')
        self.assertEqual(out[-1], 'G1 Z0.4000')
        self.assertEqual(state['z_ref'], 0.0)
        self.assertFalse(state['shifted'])

    def test_tool_passes_the_map_bounds(self):
        from tests.test_levelling_audit import tl_module
        tool, target = TestToolPassesTheUnits().tool_and_target('G0 X0 Y0 Z5\n')
        tool._al_points = [(0.0, 1.0, 0.5), (4.0, -2.0, 0.1), (2.0, 3.0, 0.2)]
        with patch.object(tl_module, 'new_levelling_state', wraps=li.new_levelling_state) as spy:
            tool.autolevell_gcode(target, 'v')
        self.assertEqual(spy.call_args.kwargs['map_bounds'], (0.0, -2.0, 4.0, 3.0))


class TestExtraWcs(unittest.TestCase):
    """Mach3 "G59 P<n>" and Fanuc "G54.1 P<n>": each P value is its own coordinate system."""

    def test_each_p_value_is_a_wcs(self):
        for home, other in (('G59 P7', 'G59 P8'), ('G54.1 P1', 'G54.1 P2'), ('G59', 'G59 P7'),
                            ('G59 P7', 'G59')):
            with self.subTest(home=home, other=other):
                state = fresh('MM', home)
                out = run(state, 'G1 Z-1', other, 'G0 X0 Y0 Z5', 'G1 Z-1', home, 'G0 X0 Y0 Z5', 'G1 Z-1')
                self.assertEqual(out[0], 'G1 Z-0.5000')
                self.assertEqual(out[3], 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-0.5000')
                self.assertEqual(state['frame'], 1)

    def test_p_value_that_can_not_be_read(self):
        state = fresh('MM', 'G59 P7')
        out = run(state, 'G59 P[7]', 'G59 P7', 'G0 X0 Y0 Z5', 'G1 Z-1')
        self.assertEqual(out[-1], 'G1 Z-0.5000')
        state = fresh('MM', 'G59 P7')
        out = run(state, 'G59 P[7]', 'G0 X0 Y0 Z5', 'G1 Z-1')
        self.assertEqual(out[-1], 'G1 Z-1')

    def test_g10_on_an_extra_wcs_is_conservative(self):
        state = fresh('MM', 'G59 P7')
        out = run(state, 'G10 L2 P7 X5', 'G0 X0 Y0 Z5', 'G1 Z-1')
        self.assertEqual(out[-1], 'G1 Z-1')
        self.assertTrue(state['shifted'])


class TestCannedCycles(unittest.TestCase):
    """Canned cycle moves are not compensated and are reported."""

    def test_cycle_moves_are_counted(self):
        for cycle in ('G81', 'G82', 'G83', 'G73', 'G84', 'G85', 'G89', 'G98 G81', 'G99 G83'):
            with self.subTest(cycle=cycle):
                state = fresh('MM', 'G21 G54')
                out = run(state, cycle + ' X1 Y1 Z-1 R1 F100', 'X2 Y2', 'Y3', 'G80', 'G0 X4 Y4 Z5',
                          'G1 Z-1')
                self.assertEqual(out[:3], [cycle + ' X1 Y1 Z-1 R1 F100', 'X2 Y2', 'Y3'])
                self.assertEqual(out[-1], 'G1 Z-0.5000')
                self.assertEqual(state['cycles'], 3)
                self.assertEqual(state['unknown'], 0)

    def test_z_is_unknown_after_a_cycle(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G81 X1 Y1 Z-1 R1', 'G80', 'G1 X2 Y2')
        self.assertEqual(out[-1], 'G1 X2 Y2')
        self.assertEqual(state['unknown'], 1)
        self.assertEqual((state['X'], state['Y']), (2.0, 2.0))

    def test_optional_cycle_makes_the_mode_unknown(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G0 X1 Y1 Z5', '/G81 X1 Y1 Z-1 R1', 'X2 Y2')
        self.assertEqual(out[-1], 'X2 Y2')
        self.assertIsNone(state['cycle'])
        self.assertEqual(state['cycles'], 2)

    def test_tool_warns(self):
        from tests.test_levelling_audit import emitted
        tool, target = TestToolPassesTheUnits().tool_and_target(
            'G21\nG0 X0 Y0 Z5\nG81 X1 Y1 Z-1 R1\nX2 Y2\nG80\n')
        tool.autolevell_gcode(target, 'v')
        self.assertTrue(any('Canned cycle moves (G73, G76, G81..G89) were not height-compensated: 2' in m
                            for m in emitted(tool)))


class TestUnknownGCodes(unittest.TestCase):
    """A G code that the tool does not know can move the machine."""

    def test_unknown_code_makes_the_position_unknown(self):
        for code in ('G77', 'G76', 'G12 I5', 'G13 I5', 'G5 X1 Y1 I1 J1 P1 Q1', 'G33 Z-1 K1',
                     'G5.2 X1 Y1', 'G123'):
            with self.subTest(code=code):
                state = fresh('MM', 'G21 G54')
                out = run(state, 'G1 Z-1', code, 'G1 Z-1', 'G0 X1 Y1 Z5', 'G1 Z-1')
                self.assertEqual(out[0], 'G1 Z-0.5000')
                self.assertEqual(out[2], 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-0.5000')

    def test_bare_move_after_an_unknown_code_is_counted(self):
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G1 Z-1', 'G77', 'X1 Y1 Z-1')
        self.assertEqual(out[-1], 'X1 Y1 Z-1')
        self.assertEqual(state['unknown'], 1)

    def test_unknown_code_with_axis_words_is_counted(self):
        state = fresh('MM', 'G21 G54')
        run(state, 'G12 I5', 'G5 X1 Y1 I1 J1 P1 Q1')
        self.assertEqual(state['unknown'], 2)

    def test_known_codes_that_do_not_move_keep_the_position(self):
        for code in ('G4 P1', 'G98', 'G99', 'G61.1', 'G97 S1000',
                     'G96 S100', 'G95', 'G69', 'G15', 'G28.1', 'G30.1', 'G50', 'G8'):
            with self.subTest(code=code):
                state = fresh('MM', 'G21 G54')
                out = run(state, 'G1 Z-1', code, 'G1 X1 Y1')
                self.assertEqual(out[-1], 'G1 X1 Y1 Z-0.5000')
                self.assertEqual(state['unknown'], 0)

    def test_tool_length_offset_makes_z_unknown(self):
        # G43.1 Z1 is a tool length, not a position: before the fix it gave 'G1 X1 Y1 Z1.5000'
        for code in ('G43 H1', 'G43.1 Z1', 'G43.2 H2', 'G49'):
            with self.subTest(code=code):
                state = fresh('MM', 'G21 G54')
                out = run(state, 'G1 Z-1', code, 'G1 X1 Y1', 'G0 Z5', 'G1 Z-1')
                self.assertEqual(out[2], 'G1 X1 Y1')
                self.assertEqual(state['unknown'], 1)
                self.assertEqual(out[-1], 'G1 Z-0.5000')

    def test_probe_stop_is_not_a_position(self):
        # before the fix, 'G1 X1 Y1' after 'G38.2 Z-10' gave 'G1 X1 Y1 Z-9.5000'
        state = fresh('MM', 'G21 G54')
        out = run(state, 'G38.2 Z-10', 'G1 X1 Y1')
        self.assertEqual(out[-1], 'G1 X1 Y1')
        self.assertIsNone(state['Z'])

    def test_codes_that_change_the_meaning_of_x_y_end_the_frame(self):
        for code in ('G16', 'G7', 'G51.1 X0', 'G51 P2', 'G68 R30'):
            with self.subTest(code=code):
                state = fresh('MM', 'G21 G54')
                out = run(state, code, 'G0 X1 Y1 Z5', 'G1 Z-1')
                self.assertEqual(out[-1], 'G1 Z-1')
                self.assertTrue(state['shifted'])

    def test_g70_g71_are_units_words(self):
        # ISEL_CNC writes G71 (mm) in its header
        state = fresh('MM', 'G71')
        out = run(state, 'G1 Z-1', 'G70', 'G0 X0 Y0 Z5', 'G1 Z-1')
        self.assertEqual(out[0], 'G1 Z-0.5000')
        self.assertEqual(out[-1], 'G1 Z-1')
        self.assertEqual(state['frame'], 1)


if __name__ == '__main__':
    unittest.main()
