#!/usr/bin/env python
"""
Arc (G2/G3) and relative (G91) height compensation in level_gcode_line().

A machine simulator executes the emitted G-code, so the checks look at where the
tool really goes, not only at the text.

Run: python -m pytest tests/test_levelling_arc_g91.py -q
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import re
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from appPlugins import levelling_interp as li
from appPlugins.levelling_interp import level_gcode_line, new_levelling_state

DEC = 4
TOL = 10 ** -DEC

_WORD = re.compile(r'([A-Z])\s*([+\-]?(?:\d+\.?\d*|\.\d+))')


def plane(x, y):
    return math.sqrt(2.0) / 100.0 * x - math.pi / 500.0 * y + 0.1


def flat(x, y):
    return 0.0


def strip_comments(line):
    line = re.sub(r'\([^)]*\)', '', line)
    return line.split(';')[0]


def words(line):
    """dict letter -> last value of the line, without comments."""
    return {letter: float(num) for letter, num in _WORD.findall(strip_comments(line))}


class Machine:
    """Executes G0/G1 text with G90/G91, as a controller would (no arcs)."""

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z
        self.absolute = True
        self.g = 0
        self.trace = []

    def feed(self, text):
        for line in text.split('\n'):
            found = _WORD.findall(strip_comments(line))
            gs = [float(n) for letter, n in found if letter == 'G']
            assert 2.0 not in gs and 3.0 not in gs, 'arc left in the output: %r' % line
            if 90.0 in gs:
                self.absolute = True
            if 91.0 in gs:
                self.absolute = False
            if 0.0 in gs:
                self.g = 0
            if 1.0 in gs:
                self.g = 1
            moved = {letter: float(n) for letter, n in found if letter in 'XYZ'}
            if not moved:
                continue
            for axis, value in moved.items():
                attr = axis.lower()
                setattr(self, attr, value if self.absolute else getattr(self, attr) + value)
            self.trace.append((self.x, self.y, self.z, self.g))


def run(state, offset, *lines):
    """Feed lines to level_gcode_line, return the list of the outputs."""
    return [level_gcode_line(line, state, offset, DEC) for line in lines]


def prime(x, y, z, offset=flat, arc_segment=0.5):
    """A state whose X/Y/Z are known after 'G90 G1 X.. Y.. Z..'. Returns (state, output)."""
    state = new_levelling_state(arc_segment=arc_segment)
    out = level_gcode_line('G90 G1 X%s Y%s Z%s' % (x, y, z), state, offset, DEC)
    return state, out


def points_of(out):
    return [(w['X'], w['Y']) for w in map(words, out.split('\n'))]


class TestNewState(unittest.TestCase):
    def test_defaults(self):
        state = new_levelling_state()
        self.assertEqual(state['plane'], 17)
        self.assertFalse(state['arc_abs'])
        self.assertFalse(state['inverse_feed'])
        self.assertEqual(state['applied'], 0.0)
        self.assertEqual(state['known'], set())
        self.assertEqual(state['arc_segment'], 1.0)
        self.assertEqual(state['arcs_split'], 0)
        self.assertEqual(state['arcs'], 0)
        self.assertEqual(state['relative'], 0)

    def test_arc_segment_must_be_positive(self):
        for bad in (0, 0.0, -1.0):
            with self.assertRaises(ValueError):
                new_levelling_state(arc_segment=bad)

    def test_arc_segment_stored(self):
        self.assertEqual(new_levelling_state(arc_segment=0.25)['arc_segment'], 0.25)

    def test_arc_distance_modes_are_modal_settings(self):
        self.assertIn(90.1, li._MODAL_SETTING_G_CODES)
        self.assertIn(91.1, li._MODAL_SETTING_G_CODES)

    def test_plane_arc_mode_and_feed_mode_words(self):
        state = new_levelling_state()
        run(state, flat, 'G18')
        self.assertEqual(state['plane'], 18)
        run(state, flat, 'G19')
        self.assertEqual(state['plane'], 19)
        run(state, flat, 'G17')
        self.assertEqual(state['plane'], 17)
        run(state, flat, 'G90.1')
        self.assertTrue(state['arc_abs'])
        run(state, flat, 'G91.1')
        self.assertFalse(state['arc_abs'])
        run(state, flat, 'G93')
        self.assertTrue(state['inverse_feed'])
        run(state, flat, 'G94')
        self.assertFalse(state['inverse_feed'])

    def test_known_axes_follow_absolute_words(self):
        state = new_levelling_state()
        run(state, flat, 'G1 Z-1')
        self.assertEqual(state['known'], {'Z'})
        run(state, flat, 'G0 X1 Y2')
        self.assertEqual(state['known'], {'X', 'Y', 'Z'})
        run(state, flat, 'G53 G0 Z0')
        self.assertEqual(state['known'], {'X', 'Y'})


class TestArcSplitAbsolute(unittest.TestCase):
    def assert_on_circle(self, pts, centre, r):
        for x, y in pts:
            self.assertAlmostEqual(math.hypot(x - centre[0], y - centre[1]), r, delta=2 * TOL)

    def split(self, start, line, offset=flat, z=-1.0):
        state, _ = prime(start[0], start[1], z, offset)
        out = level_gcode_line(line, state, offset, DEC)
        return state, out

    def test_g3_quarter_arc(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 I-10 J0')
        lines = out.split('\n')
        self.assertGreater(len(lines), 8)
        for ln in lines:
            self.assertTrue(ln.startswith('G1 X'), ln)
            self.assertIsNone(re.search(r'G0?[23]\b', ln))
        pts = points_of(out)
        self.assert_on_circle(pts, (0, 0), 10)
        self.assertEqual(pts[-1], (0.0, 10.0))
        for ln in lines:
            self.assertAlmostEqual(words(ln)['Z'], -1.0, places=DEC)
        self.assertEqual(state['arcs_split'], 1)
        self.assertEqual(state['arcs'], 0)
        self.assertEqual(state['G'], 3)
        self.assertEqual((state['X'], state['Y'], state['Z']), (0.0, 10.0, -1.0))

    def test_g2_quarter_arc(self):
        state, out = self.split((0, 10), 'G2 X10 Y0 I0 J-10')
        for ln in out.split('\n'):
            self.assertTrue(ln.startswith('G1 X'), ln)
        pts = points_of(out)
        self.assert_on_circle(pts, (0, 0), 10)
        self.assertEqual(pts[-1], (10.0, 0.0))
        # clockwise from the top to the right: every point is in the first quadrant
        for x, y in pts:
            self.assertGreaterEqual(x, -TOL)
            self.assertGreaterEqual(y, -TOL)
        self.assertEqual(state['G'], 2)

    def test_segment_length_and_angle_limits(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 I-10 J0')
        pts = [(10.0, 0.0)] + points_of(out)
        for a, b in zip(pts, pts[1:]):
            self.assertLessEqual(math.hypot(b[0] - a[0], b[1] - a[1]), 0.5 + TOL)

    def test_arc_segment_limits_segment_count(self):
        state, _ = prime(10, 0, -1, flat, arc_segment=100.0)
        out = level_gcode_line('G3 X0 Y10 I-10 J0', state, flat, DEC)
        # 100 mm segments: the 10 degree limit decides (90 / 10 = 9)
        self.assertEqual(len(out.split('\n')), 9)

    def test_plane_offset_on_every_segment(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 I-10 J0', offset=plane)
        for ln in out.split('\n'):
            w = words(ln)
            self.assertAlmostEqual(w['Z'], -1.0 + plane(w['X'], w['Y']), delta=TOL)
        self.assertAlmostEqual(state['applied'], plane(0, 10), delta=TOL)

    def test_r_format_all_four_cases(self):
        cases = [
            # (line, centre, test of the points)
            ('G3 X0 Y10 R10', (0, 0)),
            ('G3 X0 Y10 R-10', (10, 10)),
            ('G2 X0 Y10 R10', (10, 10)),
            ('G2 X0 Y10 R-10', (0, 0)),
        ]
        sizes = {}
        for line, centre in cases:
            with self.subTest(line=line):
                state, out = self.split((10, 0), line)
                pts = points_of(out)
                self.assert_on_circle(pts, centre, 10)
                self.assertEqual(pts[-1], (0.0, 10.0))
                self.assertEqual(state['arcs_split'], 1)
                sizes[line] = pts
        self.assertGreater(max(x for x, _ in sizes['G3 X0 Y10 R-10']), 19.9)   # the long way, to the right
        self.assertLess(min(x for x, _ in sizes['G2 X0 Y10 R-10']), -9.9)      # the long way, to the left
        for x, y in sizes['G3 X0 Y10 R10']:                                    # the short way
            self.assertGreaterEqual(x, -TOL)
        for x, y in sizes['G2 X0 Y10 R10']:
            self.assertLessEqual(x, 10 + TOL)
            self.assertGreaterEqual(x, -TOL)
        self.assertGreater(len(sizes['G3 X0 Y10 R-10']), 2 * len(sizes['G3 X0 Y10 R10']))

    def test_r_chord_equal_to_diameter(self):
        state, out = self.split((10, 0), 'G3 X-10 Y0 R10')
        self.assert_on_circle(points_of(out), (0, 0), 10)

    def test_full_circle_with_endpoint_words(self):
        for line, sign in (('G3 X10 Y0 I-10 J0', 1), ('G2 X10 Y0 I-10 J0', -1), ('G2 I-10 J0', -1)):
            with self.subTest(line=line):
                state, out = self.split((10, 0), line)
                pts = [(10.0, 0.0)] + points_of(out)
                self.assert_on_circle(pts, (0, 0), 10)
                self.assertEqual(pts[-1], (10.0, 0.0))
                self.assertGreaterEqual(len(pts) - 1, 36)
                total = 0.0
                for a, b in zip(pts, pts[1:]):
                    step = math.atan2(b[1], b[0]) - math.atan2(a[1], a[0])
                    step = (step + math.pi) % (2 * math.pi) - math.pi
                    self.assertLessEqual(abs(step), math.radians(10) + 1e-3)
                    total += step
                self.assertAlmostEqual(total, sign * 2 * math.pi, places=3)
                self.assertEqual(state['arcs_split'], 1)

    def test_helical_arc(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 Z-2 I-10 J0')
        zs = [words(ln)['Z'] for ln in out.split('\n')]
        n = len(zs)
        for i, z in enumerate(zs, start=1):
            self.assertAlmostEqual(z, -1.0 - i / n, delta=TOL)
        self.assertEqual(zs[-1], -2.0)
        self.assertEqual(state['Z'], -2.0)

    def test_helical_arc_with_plane(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 Z-2 I-10 J0', offset=plane)
        lines = out.split('\n')
        n = len(lines)
        for i, ln in enumerate(lines, start=1):
            w = words(ln)
            self.assertAlmostEqual(w['Z'], -1.0 - i / n + plane(w['X'], w['Y']), delta=TOL)
        self.assertAlmostEqual(state['applied'], plane(0, 10), delta=TOL)

    def test_feed_and_comment_only_on_first_segment(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 I-10 J0 F150 (quarter arc)')
        lines = out.split('\n')
        self.assertTrue(lines[0].startswith('G1 X'))
        self.assertIn(' F150', lines[0])
        self.assertTrue(lines[0].endswith(' (quarter arc)'), lines[0])
        for ln in lines[1:]:
            self.assertNotIn('F', ln)
            self.assertNotIn('(', ln)

    def test_semicolon_comment_and_modal_words_on_first_segment(self):
        state, out = self.split((10, 0), 'G90 G17 G3 X0 Y10 I-10 J0 ; round')
        lines = out.split('\n')
        self.assertTrue(lines[0].startswith('G90 G17 G1 X'), lines[0])
        self.assertTrue(lines[0].endswith(' ; round'), lines[0])
        for ln in lines[1:]:
            self.assertTrue(ln.startswith('G1 X'))
            self.assertNotIn(';', ln)

    def test_p1_is_allowed(self):
        state, out = self.split((10, 0), 'G3 X0 Y10 I-10 J0 P1')
        self.assertEqual(state['arcs_split'], 1)
        self.assertNotIn('P', out)

    def test_absolute_arc_centre_mode(self):
        state, _ = prime(10, 0, -1)
        run(state, flat, 'G90.1')
        out = level_gcode_line('G3 X0 Y10 I0 J0', state, flat, DEC)
        self.assert_on_circle(points_of(out), (0, 0), 10)
        self.assertEqual(state['arcs_split'], 1)
        run(state, flat, 'G91.1')
        out = level_gcode_line('G3 X-10 Y0 I0 J-10', state, flat, DEC)    # incremental again
        self.assert_on_circle(points_of(out), (0, 0), 10)

    def test_modal_g_state_stays_arc_after_split(self):
        state, _ = self.split((10, 0), 'G3 X0 Y10 I-10 J0')
        # a bare valid arc line is split again
        out = level_gcode_line('X-10 Y0 I0 J-10', state, flat, DEC)
        self.assertEqual(state['arcs_split'], 2)
        self.assertNotIn('G3', out)
        self.assert_on_circle(points_of(out), (0, 0), 10)


class TestArcNotCompensated(unittest.TestCase):
    LINE = 'G3 X0 Y10 I-10 J0'

    def check(self, line, setup=(), primed=True, **expect):
        if primed:
            state, _ = prime(10, 0, -1)
        else:
            state = new_levelling_state()
        for pre in setup:
            run(state, flat, pre)
        out = level_gcode_line(line, state, flat, DEC)
        self.assertEqual(out, line)
        self.assertEqual(state['arcs'], 1)
        self.assertEqual(state['arcs_split'], 0)
        return state

    def test_radius_mismatch(self):
        self.check('G2 X0 Y10 I-9 J0')

    def test_g18_plane(self):
        self.check(self.LINE, setup=['G18'])

    def test_g19_plane(self):
        self.check(self.LINE, setup=['G19'])

    def test_inverse_feed(self):
        self.check('G3 X0 Y10 I-10 J0 F2', setup=['G93'])

    def test_unknown_start(self):
        state = self.check(self.LINE, primed=False)
        self.assertEqual((state['X'], state['Y']), (0.0, 10.0))

    def test_only_some_axes_known(self):
        state = new_levelling_state()
        run(state, flat, 'G1 X10 Y0')
        out = level_gcode_line(self.LINE, state, flat, DEC)
        self.assertEqual(out, self.LINE)
        self.assertEqual(state['arcs'], 1)

    def test_extra_words(self):
        for line in ('G3 X0 Y10 I-10 J0 S1000', 'G3 X0 Y10 I-10 J0 M7', 'N10 G3 X0 Y10 I-10 J0'):
            with self.subTest(line=line):
                self.check(line)

    def test_other_g_word_on_the_line(self):
        self.check('G3 G4 X0 Y10 I-10 J0')

    def test_p2(self):
        self.check('G3 X0 Y10 I-10 J0 P2')

    def test_no_centre(self):
        self.check('G3 X0 Y10')

    def test_i_j_and_r_together(self):
        self.check('G3 X0 Y10 I-10 J0 R10')

    def test_chord_longer_than_diameter(self):
        self.check('G3 X40 Y0 R10')     # the start is (10, 0): the chord is 30

    def test_r_with_equal_ends(self):
        self.check('G3 X10 Y0 R10')

    def test_zero_radius(self):
        self.check('G3 X10 Y0 I0 J0')

    def test_state_is_updated_and_applied_cleared(self):
        state = self.check('G2 X0 Y10 Z2 I-9 J0')
        self.assertEqual((state['X'], state['Y'], state['Z']), (0.0, 10.0, 2.0))
        self.assertEqual(state['applied'], 0.0)
        self.assertEqual(state['G'], 2)

    def test_relative_bad_arc_counts_as_arc(self):
        state = self.check('G2 X-10 Y10 I-9 J0', setup=['G91'])
        self.assertEqual((state['X'], state['Y']), (0.0, 10.0))

    def test_bare_bad_arc_after_split_gets_explicit_word(self):
        for word, line in (('G3', 'G3 X0 Y10 I-10 J0'), ('G2', 'G2 X0 Y10 I0 J-10')):
            with self.subTest(word=word):
                state, _ = prime(10 if word == 'G3' else 0, 0 if word == 'G3' else 10, -1)
                level_gcode_line(line, state, flat, DEC)
                self.assertEqual(state['arcs_split'], 1)
                out = level_gcode_line('X5 Y5 I1 J0', state, flat, DEC)
                self.assertEqual(out, word + ' X5 Y5 I1 J0')
                self.assertEqual(state['arcs'], 1)

    def test_explicit_word_is_not_doubled(self):
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G2 X5 Y5 I1 J0', state, flat, DEC)
        self.assertEqual(out, 'G2 X5 Y5 I1 J0')


class TestAppliedOffset(unittest.TestCase):
    def test_absolute_g1_sets_applied(self):
        state, _ = prime(3, 4, -1, plane)
        self.assertAlmostEqual(state['applied'], plane(3, 4), delta=TOL)

    def test_g0_with_z_clears_applied_and_g0_without_z_keeps_it(self):
        state, _ = prime(3, 4, -1, plane)
        before = state['applied']
        run(state, plane, 'G0 X5 Y5')
        self.assertEqual(state['applied'], before)
        run(state, plane, 'G0 Z5')
        self.assertEqual(state['applied'], 0.0)

    def test_g1_at_unknown_position_clears_applied(self):
        state, _ = prime(3, 4, -1, plane)
        run(state, plane, 'G53 G0 X0 Y0')
        run(state, plane, 'G1 Z-2')
        self.assertEqual(state['applied'], 0.0)


class TestRelativeG1(unittest.TestCase):
    def test_constant_offset_exact_line(self):
        off = lambda x, y: 0.5
        state, _ = prime(0, 0, -1, off)
        run(state, off, 'G91')
        self.assertEqual(level_gcode_line('G1 X1 Y1 Z-0.5', state, off, DEC), 'G1 X1 Y1 Z-0.5000')
        self.assertAlmostEqual(state['applied'], 0.5)

    def test_no_z_word_and_no_height_change_keeps_the_line(self):
        off = lambda x, y: 0.5
        state, _ = prime(0, 0, -1, off)
        run(state, off, 'G91')
        self.assertEqual(level_gcode_line('G1 X1 Y1', state, off, DEC), 'G1 X1 Y1')
        self.assertEqual(state['relative'], 0)

    def test_z_word_is_added_when_the_surface_moves(self):
        state, _ = prime(0, 0, -1, plane)
        run(state, plane, 'G91')
        out = level_gcode_line('G1 X10 Y0 (cut)', state, plane, DEC)
        self.assertEqual(out, 'G1 X10 Y0 Z%.4f (cut)' % (plane(10, 0) - plane(0, 0)))

    def sequence(self, moves, offset=plane):
        """Prime at (0,0,-1), then relative moves; check machine Z == programmed Z + offset at every cut."""
        state, out = prime(0, 0, -1, offset)
        machine = Machine()
        machine.feed(out)
        run(state, offset, 'G91')
        machine.absolute = False
        px, py, pz = 0.0, 0.0, -1.0
        for dx, dy, dz in moves:
            parts = ['G1']
            if dx is not None:
                parts.append('X%.4f' % dx)
                px += dx
            if dy is not None:
                parts.append('Y%.4f' % dy)
                py += dy
            if dz is not None:
                parts.append('Z%.4f' % dz)
                pz += dz
            out = level_gcode_line(' '.join(parts), state, offset, DEC)
            machine.feed(out)
            self.assertAlmostEqual(machine.z, pz + offset(px, py), delta=TOL,
                                   msg='after %r -> %r' % (parts, out))
        return state, machine

    def test_sequence_follows_the_surface(self):
        moves = [(1.0, 0.5, None), (0.3, None, -0.2), (None, 2.0, None), (-4.0, -1.0, 0.7),
                 (5.0, 5.0, -0.1), (0.0, 0.0, -0.3), (12.5, -3.25, None)]
        state, machine = self.sequence(moves)
        self.assertEqual(state['relative'], 0)

    def test_sequence_with_bilinear_like_surface(self):
        surface = lambda x, y: 0.05 * math.sin(x / 3.0) + 0.04 * math.cos(y / 2.0)
        moves = [(0.7, 0.4, None)] * 20 + [(-0.5, 0.6, -0.05)] * 10
        self.sequence(moves, offset=surface)

    def test_thousand_small_moves_do_not_drift(self):
        state, out = prime(0, 0, -1, plane)
        machine = Machine()
        machine.feed(out)
        run(state, plane, 'G91')
        machine.absolute = False
        px = py = 0.0
        for i in range(1000):
            dx, dy = 0.0137, (0.0071 if i % 3 else -0.0213)
            px += dx
            py += dy
            machine.feed(level_gcode_line('G1 X%.4f Y%.4f' % (dx, dy), state, plane, DEC))
            self.assertLessEqual(abs(machine.z - (-1.0 + plane(px, py))), TOL, 'step %d' % i)

    def test_mixed_absolute_and_relative(self):
        state, out = prime(0, 0, -1, plane)
        machine = Machine()
        machine.feed(out)
        out = level_gcode_line('G1 X5 Y0', state, plane, DEC)          # absolute, compensated
        machine.feed(out)
        self.assertAlmostEqual(machine.z, -1 + plane(5, 0), delta=TOL)
        machine.feed(level_gcode_line('G91', state, plane, DEC))
        machine.feed(level_gcode_line('G1 X1 Y1 Z-0.5', state, plane, DEC))
        self.assertAlmostEqual(machine.z, -1.5 + plane(6, 1), delta=TOL)
        machine.feed(level_gcode_line('G90', state, plane, DEC))
        machine.feed(level_gcode_line('G0 Z5', state, plane, DEC))
        self.assertEqual(state['applied'], 0.0)
        self.assertEqual(machine.z, 5.0)
        machine.feed(level_gcode_line('G91', state, plane, DEC))
        machine.feed(level_gcode_line('G1 X1 Y1 Z-6', state, plane, DEC))
        self.assertAlmostEqual(machine.z, -1.0 + plane(7, 2), delta=TOL)
        self.assertAlmostEqual(state['applied'], plane(7, 2), delta=TOL)

    def test_relative_rapid_is_not_changed(self):
        state, _ = prime(0, 0, -1, plane)
        run(state, plane, 'G91')
        before = state['applied']
        self.assertEqual(level_gcode_line('G0 X5 Y5 Z2', state, plane, DEC), 'G0 X5 Y5 Z2')
        self.assertEqual(state['applied'], before)
        self.assertEqual(state['relative'], 0)

    def test_before_any_absolute_word(self):
        state = new_levelling_state()
        run(state, plane, 'G91')
        self.assertEqual(level_gcode_line('G1 X1 Y1 Z-1', state, plane, DEC), 'G1 X1 Y1 Z-1')
        self.assertEqual(state['relative'], 1)

    def test_relative_move_after_partial_absolute_words(self):
        state = new_levelling_state()
        run(state, plane, 'G1 X1 Y1')          # no Z word: Z is not known
        run(state, plane, 'G91')
        self.assertEqual(level_gcode_line('G1 X1 Y1', state, plane, DEC), 'G1 X1 Y1')
        self.assertEqual(state['relative'], 1)

    def test_absolute_word_makes_relative_compensation_possible(self):
        state = new_levelling_state()
        run(state, plane, 'G91')
        level_gcode_line('G1 X1 Y1 Z-1', state, plane, DEC)
        self.assertEqual(state['relative'], 1)
        run(state, plane, 'G90', 'G0 X0 Y0 Z1', 'G91')
        out = level_gcode_line('G1 X1 Y1 Z-1', state, plane, DEC)
        self.assertNotEqual(out, 'G1 X1 Y1 Z-1')
        self.assertEqual(state['relative'], 1)


class TestArcsInRelativeMode(unittest.TestCase):
    def check_relative_arc(self, state, machine, out, end, z_end, offset):
        machine.feed(out)
        self.assertAlmostEqual(machine.x, end[0], places=8)
        self.assertAlmostEqual(machine.y, end[1], places=8)
        self.assertAlmostEqual(machine.z, z_end + offset(*end), delta=TOL)

    def test_g91_on_the_arc_line(self):
        state, out = prime(0, 10, -1, plane)
        machine = Machine()
        machine.feed(out)
        arc = level_gcode_line('G91 G2 X10 Y-10 I0 J-10', state, plane, DEC)
        self.assertTrue(arc.startswith('G91 G1 X'), arc.split('\n')[0])
        n_before = len(machine.trace)
        self.check_relative_arc(state, machine, arc, (10.0, 0.0), -1.0, plane)
        for x, y, z, g in machine.trace[n_before:]:
            self.assertAlmostEqual(math.hypot(x, y), 10.0, delta=2 * TOL)
        self.assertFalse(state['abs'])
        self.assertEqual(state['arcs_split'], 1)
        self.assertAlmostEqual(state['applied'], plane(10, 0), delta=TOL)
        # sum of the X/Y deltas is the programmed delta
        sx = sum(words(ln)['X'] for ln in arc.split('\n'))
        sy = sum(words(ln)['Y'] for ln in arc.split('\n'))
        self.assertAlmostEqual(sx, 10.0, places=8)
        self.assertAlmostEqual(sy, -10.0, places=8)

    def test_arc_in_g91_mode(self):
        state, out = prime(10, 0, -1, plane)
        machine = Machine()
        machine.feed(out)
        machine.feed(level_gcode_line('G91', state, plane, DEC))
        arc = level_gcode_line('G3 X-10 Y10 I-10 J0', state, plane, DEC)
        n_before = len(machine.trace)
        self.check_relative_arc(state, machine, arc, (0.0, 10.0), -1.0, plane)
        for x, y, z, g in machine.trace[n_before:]:
            self.assertAlmostEqual(math.hypot(x, y), 10.0, delta=2 * TOL)
        for ln in arc.split('\n'):
            self.assertTrue(ln.startswith('G1 X'), ln)

    def test_helical_arc_in_g91_mode(self):
        state, out = prime(10, 0, -1, plane)
        machine = Machine()
        machine.feed(out)
        machine.feed(level_gcode_line('G91', state, plane, DEC))
        arc = level_gcode_line('G3 X-10 Y10 Z-1 I-10 J0', state, plane, DEC)
        self.check_relative_arc(state, machine, arc, (0.0, 10.0), -2.0, plane)

    def test_full_circle_in_g91_mode_returns_to_the_start(self):
        state, out = prime(10, 0, -1, plane)
        machine = Machine()
        machine.feed(out)
        machine.feed(level_gcode_line('G91', state, plane, DEC))
        arc = level_gcode_line('G2 I-10 J0', state, plane, DEC)
        self.check_relative_arc(state, machine, arc, (10.0, 0.0), -1.0, plane)

    def test_g90_arc_then_relative_moves(self):
        state, out = prime(10, 0, -1, plane)
        machine = Machine()
        machine.feed(out)
        machine.feed(level_gcode_line('G3 X0 Y10 I-10 J0', state, plane, DEC))
        machine.feed(level_gcode_line('G91', state, plane, DEC))
        machine.feed(level_gcode_line('G1 X2 Y2', state, plane, DEC))
        self.assertAlmostEqual(machine.z, -1.0 + plane(2, 12), delta=TOL)


class TestPositionLoss(unittest.TestCase):
    def setUp(self):
        self.state, _ = prime(1, 2, -1, plane)

    def relative_move_is_counted(self):
        before = self.state['relative']
        run(self.state, plane, 'G91')
        line = 'G1 X1 Y1 Z-1'
        self.assertEqual(level_gcode_line(line, self.state, plane, DEC), line)
        self.assertEqual(self.state['relative'], before + 1)

    def test_g28_z(self):
        self.assertEqual(level_gcode_line('G28 Z0', self.state, plane, DEC), 'G28 Z0')
        self.assertIsNone(self.state['Z'])
        self.assertEqual(self.state['known'], {'X', 'Y'})
        self.assertEqual((self.state['X'], self.state['Y']), (1.0, 2.0))
        self.relative_move_is_counted()

    def test_g91_g28_z(self):
        self.assertEqual(level_gcode_line('G91 G28 Z0', self.state, plane, DEC), 'G91 G28 Z0')
        self.assertFalse(self.state['abs'])
        self.assertIsNone(self.state['Z'])
        self.assertEqual(self.state['known'], {'X', 'Y'})
        line = 'G1 X1 Y1 Z-1'
        self.assertEqual(level_gcode_line(line, self.state, plane, DEC), line)
        self.assertEqual(self.state['relative'], 1)

    def test_g28_without_axes(self):
        self.assertEqual(level_gcode_line('G28', self.state, plane, DEC), 'G28')
        self.assertEqual(self.state['known'], set())
        for axis in 'XYZ':
            self.assertIsNone(self.state[axis])
        self.relative_move_is_counted()

    def test_g30_xy(self):
        self.assertEqual(level_gcode_line('G30 X0 Y0', self.state, plane, DEC), 'G30 X0 Y0')
        self.assertEqual(self.state['known'], {'Z'})
        self.assertIsNone(self.state['X'])
        self.assertIsNone(self.state['Y'])

    def test_g53_removes_known(self):
        run(self.state, plane, 'G53 G0 Z0')
        self.assertNotIn('Z', self.state['known'])

    def test_axis_known_again_after_an_absolute_word(self):
        run(self.state, plane, 'G28 Z0')
        run(self.state, plane, 'G91')
        line = 'G1 X1 Y1 Z-1'
        self.assertEqual(level_gcode_line(line, self.state, plane, DEC), line)
        run(self.state, plane, 'G90', 'G0 Z5', 'G91')
        self.assertEqual(self.state['known'], {'X', 'Y', 'Z'})
        out = level_gcode_line(line, self.state, plane, DEC)
        self.assertNotEqual(out, line)
        self.assertEqual(self.state['relative'], 1)


class TestModalWordPlacement(unittest.TestCase):
    """The modal G2/G3 word of an unchanged arc goes after '/' and 'N<digits>'."""

    def check(self, line, expected):
        state, _ = prime(10, 0, -1)
        level_gcode_line('G3 X0 Y10 I-10 J0', state, flat, DEC)        # split: the machine is in G1
        self.assertEqual(state['arcs_split'], 1)
        out = level_gcode_line(line, state, flat, DEC)
        self.assertEqual(out, expected)
        self.assertEqual(state['arcs'], 1)

    def test_plain_line(self):
        self.check('X5 Y5 I1 J0', 'G3 X5 Y5 I1 J0')

    def test_line_number(self):
        self.check('N10 X5 Y5 I1 J0', 'N10 G3 X5 Y5 I1 J0')
        self.check('N 10 X5 Y5 I1 J0', 'N 10 G3 X5 Y5 I1 J0')
        self.check('N10X5 Y5 I1 J0', 'N10G3 X5 Y5 I1 J0')

    def test_block_delete(self):
        self.check('/X5 Y5 I1 J0', '/G3 X5 Y5 I1 J0')
        self.check('/ X5 Y5 I1 J0', '/ G3 X5 Y5 I1 J0')

    def test_block_delete_and_line_number(self):
        self.check('/N10 X5 Y5 I1 J0', '/N10 G3 X5 Y5 I1 J0')
        self.check('/ N10 X5 Y5 I1 J0', '/ N10 G3 X5 Y5 I1 J0')

    def test_leading_whitespace_stays_in_front(self):
        self.check('  X5 Y5 I1 J0', '  G3 X5 Y5 I1 J0')
        self.check('  / N10 X5 Y5 I1 J0', '  / N10 G3 X5 Y5 I1 J0')
        self.check(' N10  X5 Y5 I1 J0', ' N10  G3 X5 Y5 I1 J0')

    def test_n_inside_the_line_is_not_a_line_number(self):
        self.check('X5 Y5 I1 J0 (N10 note)', 'G3 X5 Y5 I1 J0 (N10 note)')


class TestCoordinateSystemLoss(unittest.TestCase):
    """G92 and G10 L2/L20 move the work coordinate system: tracked positions are lost."""

    def lost(self, line):
        state, _ = prime(1, 2, -1, plane)
        self.assertEqual(level_gcode_line(line, state, plane, DEC), line)
        return {axis for axis in 'XYZ' if state[axis] is None}, state

    def test_g92_loses_named_axes(self):
        self.assertEqual(self.lost('G92 Z0')[0], {'Z'})
        self.assertEqual(self.lost('G92 X0 Y0')[0], {'X', 'Y'})
        self.assertEqual(self.lost('G92 X0 Y0 Z0')[0], {'X', 'Y', 'Z'})

    def test_g92_without_axes(self):
        self.assertEqual(self.lost('G92')[0], set())

    def test_g92_removes_known(self):
        _, state = self.lost('G92 Z0')
        self.assertEqual(state['known'], {'X', 'Y'})

    def test_g92_1_2_3_lose_all(self):
        for line in ('G92.1', 'G92.2', 'G92.3', 'G92.1 X0', 'G92.2 Z0'):
            with self.subTest(line=line):
                lost, state = self.lost(line)
                self.assertEqual(lost, {'X', 'Y', 'Z'})
                self.assertEqual(state['known'], set())

    def test_g10_l20_loses_named_axes(self):
        self.assertEqual(self.lost('G10 L20 P1 Z0')[0], {'Z'})
        self.assertEqual(self.lost('G10 L20 P2 X0 Y0')[0], {'X', 'Y'})

    def test_g10_l2_loses_all(self):
        for line in ('G10 L2 P1 X5', 'G10 L2 P0 Z1', 'G10 L2 P1'):
            with self.subTest(line=line):
                self.assertEqual(self.lost(line)[0], {'X', 'Y', 'Z'})

    def test_other_g10_forms_change_nothing(self):
        for line in ('G10 L1 P1 Z-5', 'G10 L10 P1 Z0', 'G10 L11 P1 Z0', 'G10 L20', 'G10'):
            with self.subTest(line=line):
                lost, state = self.lost(line)
                self.assertEqual(lost, set())
                self.assertEqual(state['known'], {'X', 'Y', 'Z'})

    def test_relative_move_is_counted_until_an_absolute_word(self):
        state, _ = prime(1, 2, -1, plane)
        run(state, plane, 'G92 Z0', 'G91')
        line = 'G1 X1 Y1 Z-1'
        self.assertEqual(level_gcode_line(line, state, plane, DEC), line)
        # a G92 Z0 with no probe before it ends the frame of the map (see test_levelling_frame)
        self.assertEqual(state['frame'], 1)

    def test_arc_after_g92_is_not_split(self):
        state, _ = prime(10, 0, -1, plane)
        run(state, plane, 'G92 X0')
        line = 'G3 X0 Y10 I-10 J0'
        self.assertEqual(level_gcode_line(line, state, plane, DEC), line)
        self.assertEqual(state['arcs'], 1)
        self.assertEqual(state['arcs_split'], 0)

    def test_modes_on_the_line_still_apply(self):
        state, _ = prime(1, 2, -1, plane)
        run(state, plane, 'G91 G92 Z0')
        self.assertFalse(state['abs'])


class TestToolPassesArcSegment(unittest.TestCase):
    def tool_and_target(self, gcode, units='MM', **target_attrs):
        from tests.test_levelling_audit import make_audit_tool
        tool = make_audit_tool(units=units)
        tool._al_prepare_interp = MagicMock(return_value=True)
        tool._al_offset_fn = lambda x, y: 0.5
        tool._al_decimals = 4
        tool._al_points = [(0.0, 0.0, 0.5)]
        target = SimpleNamespace(tools={1: {'gcode': gcode}}, units=units, **target_attrs)
        return tool, target

    def segment_used(self, **kwargs):
        from tests.test_levelling_audit import tl_module
        tool, target = self.tool_and_target('G1 Z-1\n', **kwargs)
        with patch.object(tl_module, 'new_levelling_state', wraps=li.new_levelling_state) as spy:
            tool.autolevell_gcode(target, 'v')
        return spy.call_args.kwargs['arc_segment']

    def test_smaller_of_seg_x_and_seg_y(self):
        self.assertEqual(self.segment_used(seg_x=2.0, seg_y=1.5), 1.5)
        self.assertEqual(self.segment_used(seg_x=0.8, seg_y=1.5), 0.8)

    def test_only_one_positive(self):
        self.assertEqual(self.segment_used(seg_x=0, seg_y=2.0), 2.0)
        self.assertEqual(self.segment_used(seg_x=None, seg_y=0.7), 0.7)
        self.assertEqual(self.segment_used(seg_x=3.0), 3.0)

    def test_units_fallback(self):
        self.assertEqual(self.segment_used(), 1.0)
        self.assertEqual(self.segment_used(seg_x=0, seg_y=0), 1.0)
        self.assertEqual(self.segment_used(units='IN'), 0.04)

    def test_split_message_and_output(self):
        from tests.test_levelling_audit import emitted
        tool, target = self.tool_and_target('G90 G1 X10 Y0 Z-1\nG3 X0 Y10 I-10 J0\n', seg_x=1.0)
        result = tool.autolevell_gcode(target, 'v')
        text = result[1]
        self.assertNotIn('G3', text)
        self.assertTrue(text.endswith('\n'))
        self.assertIn('Arc moves split into line moves: 1', emitted(tool))
        self.assertFalse(any('not height-compensated' in m for m in emitted(tool)))

    def test_uncompensated_arc_still_warns(self):
        from tests.test_levelling_audit import emitted
        tool, target = self.tool_and_target('G3 X0 Y10 I-10 J0\n')
        tool.autolevell_gcode(target, 'v')
        self.assertTrue(any('Arc moves were not height-compensated: 1' in m for m in emitted(tool)))
        self.assertFalse(any('split' in m for m in emitted(tool)))

    def test_unparsed_lines_warn(self):
        from tests.test_levelling_audit import emitted
        tool, target = self.tool_and_target('G90 G1 X10 Y0 Z-1\nG1 X[5] Y0\n')
        tool.autolevell_gcode(target, 'v')
        self.assertTrue(any('expressions or unknown words' in m and m.endswith(': 1')
                            for m in emitted(tool)))


class TestShortArcIsNotAFullCircle(unittest.TestCase):
    # review of 6a302b32: endpoints 1 output unit apart are a short arc, not 360 degrees
    def test_tiny_ccw_arc_stays_short(self):
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G3 X10 Y0.0001 I-10 J0', state, flat, DEC)
        for x, y in points_of(out):
            self.assertGreater(x, 9.99)
            self.assertLess(abs(y), 0.01)
        self.assertEqual(points_of(out)[-1], (10.0, 0.0001))

    def test_tiny_cw_arc_stays_short(self):
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G2 X10 Y-0.0001 I-10 J0', state, flat, DEC)
        self.assertEqual(len(out.split('\n')), 1)

    def test_nearly_full_arc_stays_nearly_full(self):
        # the end is just behind the start in the arc direction: about 360 degrees
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G3 X10 Y-0.0001 I-10 J0', state, flat, DEC)
        self.assertLess(min(x for x, _y in points_of(out)), -9.9)

    def test_equal_ends_are_still_a_full_circle(self):
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G3 X10 Y0 I-10 J0', state, flat, DEC)
        self.assertLess(min(x for x, _y in points_of(out)), -9.9)
        self.assertEqual(state['arcs_split'], 1)

    def test_equal_ends_after_relative_moves_are_a_full_circle(self):
        state, _ = prime(10, 0, -1)
        run(state, flat, 'G91 G1 X0.1', 'G1 X0.2', 'G1 X-0.3')
        out = level_gcode_line('G3 I-10 J0', state, flat, DEC)
        self.assertGreater(len(out.split('\n')), 30)


class TestBlockDelete(unittest.TestCase):
    # review of 6a302b32: an optional "/" line may be skipped by the controller
    def test_arc_after_optional_arc_is_not_split(self):
        state, _ = prime(10, 0, -1)
        level_gcode_line('G90.1', state, flat, DEC)
        out = run(state, flat, '/G3 X0 Y10 I0 J0', 'G3 X-10 Y0 I0 J0')
        self.assertTrue(out[0].startswith('/'))
        self.assertEqual(out[1], 'G3 X-10 Y0 I0 J0')
        self.assertEqual(state['arcs'], 1)

    def test_relative_move_after_optional_arc_is_not_compensated(self):
        def saddle(x, y):
            return 0.01 * x * y
        state, _ = prime(10, 0, -1, saddle)
        out = run(state, saddle, '/G3 X0 Y10 I-10 J0', 'G91 G1 X1 Y0')
        self.assertEqual(out[1], 'G91 G1 X1 Y0')
        self.assertEqual(state['relative'], 1)

    def test_relative_move_after_optional_same_z_rapid_is_not_compensated(self):
        def saddle(x, y):
            return 0.01 * x * y
        state, _ = prime(10, 10, -1, saddle)
        out = run(state, saddle, '/G0 Z-1', 'G91 G1 X1 Y0')
        self.assertEqual(out[0], '/G0 Z-1')
        self.assertEqual(out[1], 'G91 G1 X1 Y0')
        self.assertIsNone(state['applied'])
        self.assertEqual(state['relative'], 1)

    def test_optional_word_with_the_same_value_does_not_make_the_axis_known(self):
        # X keeps its 0.0 default, but only the optional line would set it
        def tilt(x, y):
            return 0.01 * x * y + 0.02 * x
        state = new_levelling_state()
        out = run(state, tilt, 'G90 G0 Y5 Z5', 'G1 Z-1', '/G0 X0', 'G91 G1 X1')
        self.assertNotIn('X', state['known'])
        self.assertEqual(out[-1], 'G91 G1 X1')
        self.assertEqual(state['relative'], 1)

    def test_optional_mode_change_makes_the_mode_unknown(self):
        state, _ = prime(10, 0, -1, plane)
        out = run(state, plane, '/G91', 'G1 X1 Y1', 'G90 G1 X2 Y2 Z-1', 'G1 X3 Y3')
        self.assertEqual(out[1], 'G1 X1 Y1')
        self.assertIn('Z%.4f' % (-1 + plane(2, 2)), out[2])
        self.assertIn('Z%.4f' % (-1 + plane(3, 3)), out[3])

    def test_optional_plane_and_arc_mode_block_the_split(self):
        for optional in ('/G18', '/G90.1', '/G93'):
            state, _ = prime(10, 0, -1)
            out = run(state, flat, optional, 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0')
            self.assertTrue(out[2].startswith('G3'), optional)
            self.assertEqual(state['arcs'], 1, optional)

    def test_optional_line_without_effect_changes_nothing(self):
        state, _ = prime(10, 0, -1)
        out = run(state, flat, '/M8', 'G3 X0 Y10 I-10 J0')
        self.assertEqual(state['arcs_split'], 1)
        self.assertNotIn('G3', out[1])

    def test_absolute_words_restore_the_position(self):
        state, _ = prime(10, 0, -1, plane)
        out = run(state, plane, '/G1 X5 Y5', 'G1 X1 Y1 Z-1', 'G3 X-1 Y1 I-1 J0')
        self.assertIn('Z%.4f' % (-1 + plane(1, 1)), out[1])
        self.assertEqual(state['arcs_split'], 1)


class TestUnknownMovesAreCounted(unittest.TestCase):
    # a cutting line that can not be levelled because of an unknown position or mode must be counted
    def counted(self, *lines):
        state = new_levelling_state(arc_segment=0.5)
        out = run(state, plane, 'G90 G0 X0 Y0 Z5', 'G1 X1 Y1 Z-1', *lines)
        return state, out

    def test_axis_unknown_after_optional_move(self):
        state, out = self.counted('/G1 X2 Y2 Z-0.5', 'Z-0.4483')
        self.assertEqual(out[-1], 'Z-0.4483')
        self.assertEqual(state['unknown'], 1)

    def test_bare_line_after_optional_arc_mode(self):
        state, out = self.counted('/G2', 'X-2.67 Y5.29')
        self.assertEqual(out[-1], 'X-2.67 Y5.29')
        self.assertEqual(state['unknown'], 1)

    def test_distance_mode_unknown(self):
        state, out = self.counted('/G91', 'G1 Z-0.5')
        self.assertEqual(out[-1], 'G1 Z-0.5')
        self.assertEqual(state['unknown'], 1)

    def test_g1_after_g53_is_counted(self):
        state, _ = self.counted('G53 G0 Z-5', 'G1 X3 Y3')
        self.assertEqual(state['unknown'], 1)

    def test_optional_cancel_does_not_clear_the_unknown_mode(self):
        state, out = self.counted('/G2', '/G80', 'X3 Y3')
        self.assertEqual(out[-1], 'X3 Y3')
        self.assertEqual(state['unknown'], 1)

    def test_rapid_is_not_counted(self):
        state, _ = self.counted('/G91', 'G0 Z5')
        self.assertEqual(state['unknown'], 0)

    def test_drill_cycle_positions_are_not_counted(self):
        state, _ = self.counted('G81 X2 Y2 Z-1 R1', 'X3 Y3', 'X4 Y4', 'G80')
        self.assertEqual(state['unknown'], 0)

    def test_known_motion_word_clears_the_unknown_mode(self):
        state, out = self.counted('/G2', 'G1 X2 Y2 Z-1', 'X3 Y3')
        self.assertIn('Z%.4f' % (-1 + plane(3, 3)), out[-1])
        self.assertEqual(state['unknown'], 0)

    def test_tool_warns(self):
        from tests.test_levelling_audit import emitted
        tool, target = TestToolPassesArcSegment.tool_and_target(
            None, 'G90 G0 X0 Y0 Z5\nG1 X1 Y1 Z-1\n/G1 X2 Y2 Z-0.5\nZ-0.4\n')
        tool.autolevell_gcode(target, 'v')
        self.assertTrue(any('unknown position or mode' in m and m.endswith(': 1')
                            for m in emitted(tool)))


class TestUnparsedWords(unittest.TestCase):
    # review of 6a302b32: expression or parameter words must not be dropped
    def test_expression_feed_keeps_the_arc(self):
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G3 X0 Y10 I-10 J0 F[100]', state, flat, DEC)
        self.assertEqual(out, 'G3 X0 Y10 I-10 J0 F[100]')
        self.assertEqual(state['arcs_split'], 0)
        self.assertEqual(state['unparsed'], 1)

    def test_expression_axis_keeps_the_arc(self):
        state, _ = prime(10, 0, -1)
        out = level_gcode_line('G3 X[0] Y[10] I-10 J0', state, flat, DEC)
        self.assertEqual(out, 'G3 X[0] Y[10] I-10 J0')
        self.assertEqual(state['arcs_split'], 0)

    def test_parameter_axis_makes_the_position_unknown(self):
        state, _ = prime(10, 0, -1, plane)
        out = run(state, plane, 'G1 X#1 Y5', 'G1 X2 Y2', 'G1 X3 Y3 Z-1')
        self.assertEqual(out[0], 'G1 X#1 Y5')
        self.assertEqual(out[1], 'G1 X2 Y2')
        self.assertIn('Z%.4f' % (-1 + plane(3, 3)), out[2])

    def test_lower_case_line_makes_the_position_unknown(self):
        state, _ = prime(10, 0, -1, plane)
        out = run(state, plane, 'g01 x5 y5', 'G1 X2 Y2')
        self.assertEqual(out, ['g01 x5 y5', 'G1 X2 Y2'])

    def test_percent_and_comments_are_understood(self):
        state, _ = prime(10, 0, -1, plane)
        out = run(state, plane, '%', 'G1 X2 Y2 (cut) ; note', 'N10 G1 X3 Y3')
        self.assertEqual(state['unparsed'], 0)
        self.assertIn('Z%.4f' % (-1 + plane(2, 2)), out[1])
        self.assertIn('Z%.4f' % (-1 + plane(3, 3)), out[2])


if __name__ == '__main__':
    unittest.main()
