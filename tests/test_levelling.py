#!/usr/bin/env python
"""
Unit tests for the autolevelling interpolation and parsing helpers.

Run: python tests/test_levelling.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest

from appPlugins.levelling_interp import (
    build_bilinear_grid,
    bilinear_offset,
    nearest_offset,
    _group_values,
    parse_height_map_line,
    parse_grbl_probe_output,
    parse_grbl_active_wcs,
    parse_grbl_work_offset,
    match_nearest_point,
    match_tolerance,
    new_levelling_state,
    level_gcode_line,
)


def plane(x, y):
    return 0.01 * x - 0.02 * y + 0.1


class TestBilinear(unittest.TestCase):
    def setUp(self):
        xs = [0, 10, 20]
        ys = [0, 5, 10]
        points = []
        for x in xs:
            for y in ys:
                points.append((x, y, plane(x, y)))
        # shuffle deterministically
        self.points = [
            points[4], points[0], points[7], points[1], points[8],
            points[2], points[5], points[3], points[6],
        ]
        self.grid = build_bilinear_grid(self.points)
        self.assertIsNotNone(self.grid)

    def test_corners(self):
        for x, y in [(0, 0), (20, 0), (0, 10), (20, 10)]:
            self.assertAlmostEqual(
                bilinear_offset(self.grid, x, y), plane(x, y), places=9
            )

    def test_edge_midpoints(self):
        for x, y in [(10, 0), (10, 10), (0, 5), (20, 5)]:
            self.assertAlmostEqual(
                bilinear_offset(self.grid, x, y), plane(x, y), places=9
            )

    def test_center(self):
        self.assertAlmostEqual(
            bilinear_offset(self.grid, 10, 5), plane(10, 5), places=9
        )

    def test_arbitrary_inside_point(self):
        self.assertAlmostEqual(
            bilinear_offset(self.grid, 13.3, 7.1), plane(13.3, 7.1), places=9
        )

    def test_clamp_low(self):
        val = bilinear_offset(self.grid, -5, 5)
        expected = bilinear_offset(self.grid, 0, 5)
        self.assertAlmostEqual(val, expected, places=9)

    def test_clamp_high(self):
        val = bilinear_offset(self.grid, 25, 12)
        expected = bilinear_offset(self.grid, 20, 10)
        self.assertAlmostEqual(val, expected, places=9)


class TestGridDetection(unittest.TestCase):
    def setUp(self):
        self.xs = [0, 10, 20]
        self.ys = [0, 5, 10]

    def _points(self):
        points = []
        for x in self.xs:
            for y in self.ys:
                points.append((x, y, plane(x, y)))
        return points

    def test_off_grid_point_returns_none(self):
        points = self._points()
        # move one point off-grid in X
        x, y, z = points[0]
        points[0] = (10.3 if x == 0 else x, y, z)
        result = build_bilinear_grid(points)
        self.assertIsNone(result)

    def test_missing_point_returns_none(self):
        points = self._points()
        points.pop()
        result = build_bilinear_grid(points)
        self.assertIsNone(result)

    def test_noise_still_builds_grid(self):
        points = self._points()
        noisy = [(x + 1e-9, y - 1e-9, z) for (x, y, z) in points]
        result = build_bilinear_grid(noisy)
        self.assertIsNotNone(result)
        xs, ys, grid = result
        self.assertEqual(len(xs), 3)
        self.assertEqual(len(ys), 3)

    def test_single_row_returns_none(self):
        points = [(x, 0, plane(x, 0)) for x in self.xs]
        result = build_bilinear_grid(points)
        self.assertIsNone(result)

    def test_single_column_returns_none(self):
        points = [(0, y, plane(0, y)) for y in self.ys]
        result = build_bilinear_grid(points)
        self.assertIsNone(result)

    def test_duplicate_cell_returns_none(self):
        points = self._points()
        # add an extra point that snaps to an already-filled cell
        points.append((points[0][0], points[0][1], points[0][2]))
        result = build_bilinear_grid(points)
        self.assertIsNone(result)


class TestGroupValues(unittest.TestCase):
    def test_no_chaining_across_tol_span(self):
        # 0, 0.4e-6, 0.8e-6, 1.2e-6 with tol=1e-6: each consecutive pair
        # is within tol of its neighbor, but the span from 0 to 1.2e-6
        # exceeds tol, so this must NOT collapse into a single group.
        # Anchoring to the first value of the group (not the previous
        # value) gives 2 groups: {0, 0.4e-6, 0.8e-6} and {1.2e-6}.
        groups = _group_values([0, 0.4e-6, 0.8e-6, 1.2e-6], 1e-6)
        self.assertEqual(len(groups), 2)


class TestNearest(unittest.TestCase):
    def setUp(self):
        self.points = [
            (0, 0, 1.0),
            (10, 0, 2.0),
            (0, 10, 3.0),
            (10, 10, 4.0),
            (5, 5, 5.0),
        ]

    def test_query_on_point(self):
        self.assertEqual(nearest_offset(self.points, 10, 0), 2.0)

    def test_query_near_point(self):
        self.assertEqual(nearest_offset(self.points, 9.9, 0.1), 2.0)

    def test_query_equidistant_returns_first(self):
        # (0,0) and (10,0) are both distance 5 from (5, 0), and both
        # come before the closer (5,5) point... use a point equidistant
        # between (0,0) and (10,0) but excluding (5,5) by using y offset
        # that keeps (0,0) and (10,0) tied and closer than others.
        points = [
            (0, 0, 1.0),
            (10, 0, 2.0),
        ]
        result = nearest_offset(points, 5, 0)
        self.assertEqual(result, 1.0)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            nearest_offset([], 0, 0)


class TestParseHeightMapLine(unittest.TestCase):
    def test_mach_comma(self):
        self.assertEqual(parse_height_map_line("1.0,2.0,-0.05"), (1.0, 2.0, -0.05))

    def test_mach_comma_extra_columns(self):
        self.assertEqual(
            parse_height_map_line("1.0,2.0,-0.05,0,0,0"), (1.0, 2.0, -0.05)
        )

    def test_mach_comma_with_spaces(self):
        self.assertEqual(
            parse_height_map_line("1.0, 2.0, -0.05"), (1.0, 2.0, -0.05)
        )

    def test_linuxcnc_space_separated(self):
        self.assertEqual(
            parse_height_map_line("1.0 2.0 -0.05 0 0 0 0 0 0"),
            (1.0, 2.0, -0.05),
        )

    def test_tabs(self):
        self.assertEqual(
            parse_height_map_line("1.0\t2.0\t-0.05"), (1.0, 2.0, -0.05)
        )

    def test_empty_string(self):
        self.assertIsNone(parse_height_map_line(""))

    def test_newline_only(self):
        self.assertIsNone(parse_height_map_line("\n"))

    def test_non_numeric_tokens(self):
        self.assertIsNone(parse_height_map_line("abc,def,ghi"))

    def test_too_few_tokens(self):
        self.assertIsNone(parse_height_map_line("1.0,2.0"))


class TestParseGrbl(unittest.TestCase):
    def test_probe_output_in_order(self):
        text = (
            "ok\n"
            "<Idle|MPos:0.000,0.000,0.000|FS:0,0>\n"
            "[PRB:1.000,2.000,-0.100:1]\n"
            "ok\n"
            "[PRB:3.000,4.000,-0.200:1]\n"
            "[MSG:Probe complete]\n"
            "[PRB:5.000,6.000,-0.300:1]\n"
            "ok\n"
        )
        result = parse_grbl_probe_output(text)
        self.assertEqual(
            result,
            [
                (1.0, 2.0, -0.1),
                (3.0, 4.0, -0.2),
                (5.0, 6.0, -0.3),
            ],
        )

    def test_probe_output_four_axis(self):
        text = "[PRB:1.000,2.000,3.000,4.000:1]\n"
        result = parse_grbl_probe_output(text)
        self.assertEqual(result, [(1.0, 2.0, 3.0)])

    def test_probe_output_flag_zero_raises(self):
        text = "[PRB:1.000,2.000,-0.100:0]\n"
        with self.assertRaises(ValueError) as ctx:
            parse_grbl_probe_output(text)
        msg = str(ctx.exception)
        self.assertIn("1.0", msg)
        self.assertIn("2.0", msg)

    def test_probe_output_empty(self):
        self.assertEqual(parse_grbl_probe_output(""), [])

    def test_active_wcs_from_modal_response(self):
        text = "[GC:G0 G55 G17 G21 G90 G94 M5 M9 T0 F0 S0]\nok\n"
        self.assertEqual(parse_grbl_active_wcs(text), 'G55')

    def test_work_offset_full_sample(self):
        text = (
            "[G54:-100.000,-50.000,-20.000]\n"
            "[G55:0.000,0.000,0.000]\n"
            "[G92:1.000,2.000,3.000]\n"
            "[TLO:0.500]\n"
            "[PRB:0.000,0.000,0.000:0]\n"
            "ok\n"
        )
        result = parse_grbl_work_offset(text)
        self.assertAlmostEqual(result[0], -99.0)
        self.assertAlmostEqual(result[1], -48.0)
        self.assertAlmostEqual(result[2], -20.0 + 3.0 + 0.5)

    def test_work_offset_missing_g92_and_tlo(self):
        text = "[G54:-100.000,-50.000,-20.000]\nok\n"
        result = parse_grbl_work_offset(text)
        self.assertEqual(result, (-100.0, -50.0, -20.0))

    def test_work_offset_missing_g54_raises(self):
        text = "[G55:0.000,0.000,0.000]\nok\n"
        with self.assertRaises(ValueError):
            parse_grbl_work_offset(text)

    def test_work_offset_uses_selected_g55(self):
        text = (
            "[G54:-100.000,-50.000,-20.000]\n"
            "[G55:10.000,20.000,30.000]\n"
            "[G92:1.000,2.000,3.000]\n"
            "[TLO:0.500]\n"
        )
        result = parse_grbl_work_offset(text, active_wcs='G55')
        self.assertEqual(result, (11.0, 22.0, 33.5))

    def test_work_offset_missing_selected_g55_names_g55(self):
        text = "[G54:0.000,0.000,0.000]\n"
        with self.assertRaises(ValueError) as ctx:
            parse_grbl_work_offset(text, active_wcs='G55')
        self.assertIn('G55', str(ctx.exception))


class TestMatchNearestPoint(unittest.TestCase):
    def setUp(self):
        self.points = {
            'a': (0, 0),
            'b': (10, 0),
            'c': (0, 10),
        }

    def test_within_tolerance(self):
        self.assertEqual(match_nearest_point(self.points, 0.5, 0.5, 1.0), 'a')

    def test_outside_tolerance(self):
        self.assertIsNone(match_nearest_point(self.points, 5, 5, 1.0))

    def test_tie_returns_first(self):
        points = {
            'first': (0, 0),
            'second': (10, 0),
        }
        self.assertEqual(match_nearest_point(points, 5, 0, 100), 'first')


class TestMatchTolerance(unittest.TestCase):
    def test_half_min_distance_below_cap(self):
        # closest pair is 1.0 apart -> half = 0.5, capped at 5.0 -> 0.5
        points = [(0, 0), (1, 0), (10, 10)]
        self.assertAlmostEqual(match_tolerance(points, cap=5.0), 0.5)

    def test_capped_when_points_far_apart(self):
        # closest pair is 10 apart -> half = 5.0, but cap is 0.5
        points = [(0, 0), (10, 0)]
        self.assertAlmostEqual(match_tolerance(points, cap=0.5), 0.5)

    def test_single_point_uses_cap(self):
        self.assertEqual(match_tolerance([(0, 0)], cap=0.02), 0.02)

    def test_no_points_uses_cap(self):
        self.assertEqual(match_tolerance([], cap=0.5), 0.5)

    def test_duplicate_points_yield_zero(self):
        points = [(1, 1), (1, 1), (5, 5)]
        self.assertEqual(match_tolerance(points, cap=0.5), 0.0)


class TestLevelGcodeLine(unittest.TestCase):
    @staticmethod
    def offset_fn(x, y):
        return 0.01 * x + 0.1

    def test_basic_linear_move(self):
        state = new_levelling_state()
        line = "G01 X10.0 Y5.0 Z-0.1"
        result = level_gcode_line(line, state, self.offset_fn)
        expected_z = -0.1 + self.offset_fn(10.0, 5.0)
        self.assertEqual(result, "G01 X10.0 Y5.0 Z{0:.4f}".format(expected_z))

    def test_modal_z_appended_on_next_line(self):
        state = new_levelling_state()
        level_gcode_line("G01 Z-0.1", state, self.offset_fn)
        self.assertEqual(state['Z'], -0.1)
        result = level_gcode_line("X10.0 Y5.0", state, self.offset_fn)
        expected_z = -0.1 + self.offset_fn(10.0, 5.0)
        self.assertEqual(result, "X10.0 Y5.0 Z{0:.4f}".format(expected_z))
        self.assertEqual(state['Z'], -0.1)

    def test_spaced_words(self):
        state = new_levelling_state()
        line = "G 01 X 10.0 Y 5.0 Z -0.1"
        result = level_gcode_line(line, state, self.offset_fn)
        expected_z = -0.1 + self.offset_fn(10.0, 5.0)
        self.assertEqual(
            result, "G 01 X 10.0 Y 5.0 Z {0:.4f}".format(expected_z)
        )

    def test_no_spaces(self):
        state = new_levelling_state()
        state['G'] = 1
        line = "X10.0000Y5.0000Z-0.1000"
        result = level_gcode_line(line, state, self.offset_fn)
        expected_z = -0.1 + self.offset_fn(10.0, 5.0)
        self.assertEqual(
            result, "X10.0000Y5.0000Z{0:.4f}".format(expected_z)
        )

    def test_rapid_move_unchanged(self):
        state = new_levelling_state()
        line = "G00 X10 Y5 Z-0.1"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)

    def test_rapid_then_positive_z_feed_is_compensated(self):
        # every G1 move is compensated, also above Z0 (policy since 2026-10-12)
        state = new_levelling_state()
        level_gcode_line("G00 Z2.0", state, lambda x, y: 0.5)
        result = level_gcode_line("G01 X1 Y1", state, lambda x, y: 0.5)
        self.assertEqual(result, "G01 X1 Y1 Z2.5000")

    def test_arc_move_unchanged_and_counted(self):
        state = new_levelling_state()
        state['G'] = 1
        state['Z'] = -0.1
        line = "G02 X10 Y5 I1 J0"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)
        self.assertEqual(state['arcs'], 1)

    def test_comment_line_unchanged(self):
        state = new_levelling_state()
        line = "(this is a comment)"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)

    def test_semicolon_comment_line_unchanged(self):
        state = new_levelling_state()
        line = "; this is a comment"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)

    def test_blank_line_unchanged(self):
        state = new_levelling_state()
        result = level_gcode_line("", state, self.offset_fn)
        self.assertEqual(result, "")

    def test_inline_comment_preserved(self):
        state = new_levelling_state()
        # no default start position: set X/Y/Z first (X/Y are named on the line)
        level_gcode_line("G0 X0 Y0 Z0", state, self.offset_fn)
        state['G'] = 1
        line = "G01 X1 Y1 (cut)"
        result = level_gcode_line(line, state, self.offset_fn)
        expected_z = 0.0 + self.offset_fn(1.0, 1.0)
        self.assertEqual(
            result, "G01 X1 Y1 Z{0:.4f} (cut)".format(expected_z)
        )

    def test_non_motion_g_words_no_state_change(self):
        state = new_levelling_state()
        result = level_gcode_line("G21 G90 G17 G94", state, self.offset_fn)
        self.assertEqual(result, "G21 G90 G17 G94")
        self.assertEqual(state['G'], 0)

        result = level_gcode_line("G01 X1 Y1 Z-0.1", state, self.offset_fn)
        self.assertEqual(state['G'], 1)
        expected_z = -0.1 + self.offset_fn(1.0, 1.0)
        self.assertEqual(result, "G01 X1 Y1 Z{0:.4f}".format(expected_z))

    def test_g38_2_not_levelled(self):
        state = new_levelling_state()
        state['G'] = 1
        state['Z'] = -0.1
        result = level_gcode_line("G38.2 Z-2 F50", state, self.offset_fn)
        self.assertEqual(result, "G38.2 Z-2 F50")
        self.assertIsNone(state['G'])

    def test_g38_2_then_bare_move_not_levelled(self):
        state = new_levelling_state()
        level_gcode_line("G01 Z-0.1", state, self.offset_fn)
        level_gcode_line("G38.2 Z-2 F50", state, self.offset_fn)
        self.assertIsNone(state['G'])
        line = "X1 Y1"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)
        self.assertIsNone(state['G'])

    def test_g31_then_bare_move_not_levelled(self):
        state = new_levelling_state()
        level_gcode_line("G01 Z-0.1", state, self.offset_fn)
        level_gcode_line("G31 Z-2 F50", state, self.offset_fn)
        self.assertIsNone(state['G'])
        line = "X1 Y1"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)
        self.assertIsNone(state['G'])

    def test_g80_cancels_motion_modal(self):
        state = new_levelling_state()
        level_gcode_line("G01 Z-0.1", state, self.offset_fn)
        level_gcode_line("G80", state, self.offset_fn)
        self.assertIsNone(state['G'])

    def test_toolchange_probe_mach3_sequence(self):
        # mirrors preprocessors/Toolchange_Probe_MACH3.py's toolchange
        # G-code: probe (G31), zero the probed axis (G92), rapid clear
        # (G00), then a real cut move (G01) that must still be levelled
        # from its own original Z, not from anything G92 wrote.
        state = new_levelling_state()
        # no default start position: position X/Y/Z first
        level_gcode_line("G00 X0 Y0 Z5", state, self.offset_fn)

        r1 = level_gcode_line("G01 Z-0.1", state, self.offset_fn)
        expected_z1 = -0.1 + self.offset_fn(0.0, 0.0)
        self.assertEqual(r1, "G01 Z{0:.4f}".format(expected_z1))

        r2 = level_gcode_line("G01 X1 Y1", state, self.offset_fn)
        expected_z2 = -0.1 + self.offset_fn(1.0, 1.0)
        self.assertEqual(r2, "G01 X1 Y1 Z{0:.4f}".format(expected_z2))

        r3 = level_gcode_line("G31 Z-5 F50", state, self.offset_fn)
        self.assertEqual(r3, "G31 Z-5 F50")
        self.assertIsNone(state['G'])

        r4 = level_gcode_line("G92 Z0", state, self.offset_fn)
        self.assertEqual(r4, "G92 Z0")
        # G92 leaves the modal G alone, but the work Z is no longer valid
        self.assertIsNone(state['G'])
        self.assertIsNone(state['Z'])
        self.assertEqual((state['X'], state['Y']), (1.0, 1.0))

        r5 = level_gcode_line("G00 Z2", state, self.offset_fn)
        self.assertEqual(r5, "G00 Z2")
        self.assertEqual(state['G'], 0)

        r6 = level_gcode_line("G01 Z-0.1", state, self.offset_fn)
        expected_z6 = -0.1 + self.offset_fn(1.0, 1.0)
        self.assertEqual(r6, "G01 Z{0:.4f}".format(expected_z6))
        self.assertEqual(state['Z'], -0.1)

        r7 = level_gcode_line("G01 X2 Y2", state, self.offset_fn)
        expected_z7 = -0.1 + self.offset_fn(2.0, 2.0)
        self.assertEqual(r7, "G01 X2 Y2 Z{0:.4f}".format(expected_z7))
        self.assertEqual(state['Z'], -0.1)

    def test_g92_1_loses_the_work_position(self):
        state = new_levelling_state()
        state['X'] = 1.0
        state['Y'] = 1.0
        state['Z'] = -0.1
        state['G'] = 1
        line = "G92.1 X0 Y0 Z0"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)
        # the work coordinate system changed: X/Y/Z are unknown, G stays
        self.assertIsNone(state['X'])
        self.assertIsNone(state['Y'])
        self.assertIsNone(state['Z'])
        self.assertEqual(state['G'], 1)

    def test_g28_xy_makes_xy_unknown_and_keeps_z(self):
        state = new_levelling_state()
        state['X'] = 1.0
        state['Y'] = 1.0
        state['Z'] = -0.5
        result = level_gcode_line("G28 X0 Y0", state, self.offset_fn)
        self.assertEqual(result, "G28 X0 Y0")
        self.assertIsNone(state['X'])
        self.assertIsNone(state['Y'])
        self.assertEqual(state['Z'], -0.5)

    def test_lowercase_line_unchanged(self):
        state = new_levelling_state()
        state['G'] = 1
        line = "g01 x10.0 y5.0 z-0.1"
        result = level_gcode_line(line, state, self.offset_fn)
        self.assertEqual(result, line)

    def test_m_code_only_unchanged(self):
        state = new_levelling_state()
        self.assertEqual(
            level_gcode_line("M3 S1000", state, self.offset_fn), "M3 S1000"
        )

    def test_tool_change_unchanged(self):
        state = new_levelling_state()
        self.assertEqual(level_gcode_line("T1", state, self.offset_fn), "T1")

    def test_feed_only_unchanged(self):
        state = new_levelling_state()
        self.assertEqual(
            level_gcode_line("F100", state, self.offset_fn), "F100"
        )

    def test_decimals_param(self):
        state = new_levelling_state()
        line = "G01 X10.0 Y5.0 Z-0.1"
        result = level_gcode_line(line, state, self.offset_fn, decimals=2)
        expected_z = -0.1 + self.offset_fn(10.0, 5.0)
        self.assertEqual(result, "G01 X10.0 Y5.0 Z{0:.2f}".format(expected_z))


if __name__ == '__main__':
    unittest.main()
