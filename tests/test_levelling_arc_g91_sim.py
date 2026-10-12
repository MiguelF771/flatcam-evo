#!/usr/bin/env python
"""
Adversarial check of arc (G2/G3) and relative (G91) height compensation.

A small machine simulator (G0/G1/G2/G3 with I/J and R, G90/G91, G90.1/G91.1,
G17/G18/G19, F, comments, optional block delete) runs the ORIGINAL program and
the LEVELLED program line by line. For every cutting move of the levelled
program, every sampled point must have

    Z == (Z of the original path at the same X/Y) + offset(x, y)

The offset is a plane a*x + b*y + c, so the linear interpolation along a G1
segment is exact and the tolerance stays tight:

    tolerance = 0.5 * 10**-decimals      Z rounding of a vertex (G90 text, or the
                                         rounded Z delta of G91: the delta is taken
                                         against the machine Z, so it does not add up)
              + 1e-5                     offset change by the rounding of X/Y, float noise
              + slope term               (|dz| / xy length of the original move) * (rounding of
                                         X/Y: at most 0.71 * 10**-decimals per vertex, or the
                                         measured X/Y error of the start/end if larger): a
                                         vertex that is a little off the original path in X/Y
                                         has the Z of a neighbouring point of a steep move
              + helix term              (|dz| / |sweep|) * 0.13 * h**3, h = half the
                                         angle of the longest chord, <= pi / 36: the Z of
                                         a chord is linear in the chord, the Z of a helix
                                         is linear in the angle; the angle of a point
                                         of a chord differs from the linear one by at
                                         most 0.128 * h**3.

The sideways error of a chord (sagitta) does not enter the Z check: the sample is
projected on the original path and the offset is taken at the sample. It is
checked on its own: a sample is at most `sagitta + 4 * 10**-decimals` from the
original path.

The first move after an uncompensated position (a G0 retract, a G0 X/Y in G91,
an unsplit arc) ramps from the old offset to the new one: of that move only the
end point is checked, the following moves are checked at every sample.

Run: python -m pytest tests/test_levelling_arc_g91_sim.py -q
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import random
import re
import unittest
from types import SimpleNamespace

from appPlugins.levelling_interp import level_gcode_line, new_levelling_state

DEC = 4
TWO_PI = 2.0 * math.pi
BASE_TOL = 0.5 * 10 ** -DEC + 1e-5
HALF_ANGLE = math.pi / 36.0     # half the largest chord angle (10 degrees)
DS_ORIG = 0.02                  # sampling step of arcs of the original path
DS_LEV = 0.05                   # sampling step of the lines

_WORD = re.compile(r'([A-Z])\s*([+\-]?(?:\d+\.?\d*|\.\d+))')


def plane(x, y):
    """Offset a*x + b*y + c: linear, so the interpolation along a segment is exact."""
    return math.sqrt(2.0) / 100.0 * x - math.pi / 500.0 * y + 0.1


def _strip_comments(line):
    line = re.sub(r'\([^)]*\)', '', line)
    return line.split(';')[0]


def _angle_sweep(a_start, a_end, clockwise):
    """Magnitude of the sweep from a_start to a_end, in [0, 2*pi)."""
    if clockwise:
        return (a_start - a_end) % TWO_PI
    return (a_end - a_start) % TWO_PI


class Sim:
    """
    Executes G-code text. `run()` returns the moves it made:
    SimpleNamespace(kind, g, start, end, pts, rmax, sweep, extra) where kind is
    'rapid', 'cut', 'arc' (G17 arc, pts = dense samples) or 'opaque_arc'
    (an arc in G18/G19: only the end point is known).
    """

    def __init__(self, x=0.0, y=0.0, z=0.0, block_delete=False):
        self.pos = [x, y, z]
        self.absolute = True
        self.arc_abs = False
        self.plane = 17
        self.g = None
        self.block_delete = block_delete

    def run(self, text):
        moves = []
        for raw in text.split('\n'):
            moves.extend(self._line(raw))
        return moves

    def _line(self, raw):
        line = _strip_comments(raw)
        if self.block_delete and line.lstrip().startswith('/'):
            return []
        words = {}
        gs = []
        for letter, num in _WORD.findall(line):
            if letter == 'G':
                gs.append(float(num))
            else:
                words[letter] = float(num)
        for g in gs:
            if g in (0.0, 1.0, 2.0, 3.0):
                self.g = int(g)
            elif g in (17.0, 18.0, 19.0):
                self.plane = int(g)
            elif g == 90.0:
                self.absolute = True
            elif g == 91.0:
                self.absolute = False
            elif g == 90.1:
                self.arc_abs = True
            elif g == 91.1:
                self.arc_abs = False
        has_axis = any(a in words for a in 'XYZ')
        if self.g in (0, 1):
            return self._linear(words) if has_axis else []
        if self.g in (2, 3) and (has_axis or any(a in words for a in 'IJKR')):
            return self._arc(words)
        assert not has_axis, 'axis words without a motion mode: %r' % raw
        return []

    def _target(self, words):
        end = list(self.pos)
        for i, axis in enumerate('XYZ'):
            if axis in words:
                end[i] = words[axis] if self.absolute else end[i] + words[axis]
        return end

    def _linear(self, words):
        start = tuple(self.pos)
        end = tuple(self._target(words))
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        n = max(1, int(math.ceil(length / DS_LEV)))
        pts = [tuple(s + (e - s) * i / n for s, e in zip(start, end)) for i in range(1, n + 1)]
        pts[-1] = end
        self.pos = list(end)
        return [SimpleNamespace(kind='rapid' if self.g == 0 else 'cut', g=self.g, start=start,
                                end=end, pts=pts, rmax=0.0, sweep=0.0, extra=0.0)]

    def _arc(self, words):
        start = tuple(self.pos)
        end = tuple(self._target(words))
        clockwise = self.g == 2
        if self.plane != 17:
            self.pos = list(end)
            return [SimpleNamespace(kind='opaque_arc', g=self.g, start=start, end=end,
                                    pts=[end], rmax=0.0, sweep=0.0, extra=0.0)]
        if 'R' in words:
            centre = self._centre_r(start, end, words['R'], clockwise)
        elif self.arc_abs:
            centre = (words['I'], words['J'])
        else:
            centre = (start[0] + words.get('I', 0.0), start[1] + words.get('J', 0.0))
        a_s = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a_e = math.atan2(end[1] - centre[1], end[0] - centre[0])
        r_s = math.hypot(start[0] - centre[0], start[1] - centre[1])
        r_e = math.hypot(end[0] - centre[0], end[1] - centre[1])
        if math.hypot(end[0] - start[0], end[1] - start[1]) < 1e-6:
            sweep = TWO_PI
        else:
            sweep = _angle_sweep(a_s, a_e, clockwise)
        signed = -sweep if clockwise else sweep
        rmax = max(r_s, r_e)
        n = max(16, int(math.ceil(rmax * sweep / DS_ORIG)))
        pts = []
        for i in range(1, n + 1):
            t = i / n
            ang = a_s + signed * t
            r = r_s + (r_e - r_s) * t
            pts.append((centre[0] + r * math.cos(ang), centre[1] + r * math.sin(ang),
                        start[2] + (end[2] - start[2]) * t))
        pts[-1] = end
        half = min(HALF_ANGLE, sweep / 2.0)
        extra = abs(end[2] - start[2]) / sweep * 0.13 * half ** 3
        self.pos = list(end)
        return [SimpleNamespace(kind='arc', g=self.g, start=start, end=end, pts=pts, rmax=rmax,
                                sweep=sweep, extra=extra)]

    @staticmethod
    def _centre_r(start, end, radius, clockwise):
        """Both candidate centres; the one whose sweep is <= 180 degrees for R > 0, > 180 for R < 0."""
        dx, dy = end[0] - start[0], end[1] - start[1]
        d = math.hypot(dx, dy)
        height = math.sqrt(max(0.0, radius * radius - d * d / 4.0))
        mx, my = start[0] + dx / 2.0, start[1] + dy / 2.0
        candidates = [(mx - height * dy / d, my + height * dx / d),
                      (mx + height * dy / d, my - height * dx / d)]
        for c in candidates:
            a_s = math.atan2(start[1] - c[1], start[0] - c[0])
            a_e = math.atan2(end[1] - c[1], end[0] - c[0])
            sweep = _angle_sweep(a_s, a_e, clockwise)
            if (radius > 0 and sweep <= math.pi + 1e-9) or (radius < 0 and sweep >= math.pi - 1e-9):
                return c
        raise AssertionError('no centre for R%s' % radius)


# ---------------------------------------------------------------- checking

def _project(poly, ptr, window, x, y):
    """
    Closest point to (x, y) on the segments poly[ptr:ptr+window].
    Returns (segment index, Z at the closest point, distance).
    """
    best = None
    last = min(len(poly) - 1, ptr + window)
    for j in range(ptr, last):
        ax, ay, az = poly[j]
        bx, by, bz = poly[j + 1]
        vx, vy = bx - ax, by - ay
        len2 = vx * vx + vy * vy
        t = 0.0 if len2 == 0.0 else max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / len2))
        dist = math.hypot(x - (ax + t * vx), y - (ay + t * vy))
        if best is None or dist < best[2] - 1e-12:
            best = (j, az + (bz - az) * t, dist)
    return best


def _xy_length(poly):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(poly, poly[1:]))


class Report:
    def __init__(self):
        self.worst_z = 0.0
        self.worst_xy = 0.0
        self.samples = 0


def check_program(test, lines, offset=plane, arc_segment=0.5, block_delete=False, report=None,
                  expect_all_compensated=True, level_offset=None):
    """
    Run `lines` through level_gcode_line and the simulator, check every line against `offset`.
    `level_offset` (default: `offset`) is the offset given to level_gcode_line: a different
    one must make the check fail.
    Returns (state, original sim, levelled sim, list of (line, out)).
    """
    report = report or Report()
    level_offset = level_offset or offset
    state = new_levelling_state(arc_segment=arc_segment)
    orig, lev = Sim(block_delete=block_delete), Sim(block_delete=block_delete)
    pairs = []
    for line in lines:
        arcs_before = state['arcs']
        out = level_gcode_line(line, state, level_offset, DEC)
        pairs.append((line, out))
        o_start, l_start = tuple(orig.pos), tuple(lev.pos)
        o_moves, l_moves = orig.run(line), lev.run(out)
        absolute = orig.absolute
        ctx = '\n  in: %r\n  out: %r' % (line, out)
        if not o_moves:
            test.assertEqual(l_moves, [], 'levelled line moves, the original does not' + ctx)
            continue
        test.assertEqual(len(o_moves), 1, ctx)
        om = o_moves[0]
        test.assertTrue(l_moves, 'levelled line does not move' + ctx)
        if om.kind == 'rapid':
            _check_rapid(test, om, l_moves, o_start, l_start, absolute, line, ctx)
        elif state['arcs'] > arcs_before:
            # an arc that was left alone must still run as the same arc
            test.assertEqual([m.kind for m in l_moves], [om.kind],
                             'the unsplit arc must still run as an arc' + ctx)
            test.assertEqual(l_moves[0].g, om.g, ctx)
            test.assertLessEqual(math.dist(l_moves[0].end[:2], om.end[:2]), 2 * 10 ** -DEC, ctx)
        else:
            _check_cut(test, om, l_moves, o_start, l_start, offset, arc_segment, report, ctx)
    if expect_all_compensated:
        test.assertEqual((state['arcs'], state['relative']), (0, 0), 'not compensated: %r' % (state,))
    return state, orig, lev, pairs


def _check_rapid(test, om, l_moves, o_start, l_start, absolute, line, ctx):
    """A rapid is not changed: absolute axes go to the same value, the others move by the same delta."""
    test.assertEqual([m.kind for m in l_moves], ['rapid'], ctx)
    lm = l_moves[0]
    given = {letter for letter, _ in _WORD.findall(_strip_comments(line))}
    for i, axis in enumerate('XYZ'):
        if absolute and axis in given:
            test.assertEqual(lm.end[i], om.end[i], ctx)
        else:
            test.assertAlmostEqual(lm.end[i] - l_start[i], om.end[i] - o_start[i], delta=1e-9, msg=ctx)


def _check_cut(test, om, l_moves, o_start, l_start, offset, arc_segment, report, ctx):
    test.assertTrue(all(m.kind == 'cut' and m.g == 1 for m in l_moves),
                    'a split arc must leave only G1 moves' + ctx)
    # the start is compensated if the machine is above the original by the offset there
    start_err = abs((l_start[2] - o_start[2]) - offset(l_start[0], l_start[1]))
    pre_ok = start_err <= 2 * BASE_TOL
    poly = [om.start] + list(om.pts)
    degenerate = _xy_length(poly) < 1e-9
    if om.kind == 'arc':
        sag = om.rmax * (1.0 - math.cos(min(HALF_ANGLE, arc_segment / (2.0 * om.rmax))))
        xy_bound = sag + 4 * 10 ** -DEC
    else:
        xy_bound = 4 * 10 ** -DEC
    window = 3 * int(math.ceil(DS_LEV / DS_ORIG)) + 8
    # The rounded X/Y of the start and end vertices move the machine sideways of the
    # original path by xy_dev (measured; at most about 10**-decimals). On a steep move that
    # changes Z by slope * xy_dev: part of the tolerance.
    # Every vertex is rounded to 10**-decimals per axis: within 0.71 * 10**-decimals of the exact one.
    xy_dev = max(math.dist(l_start[:2], o_start[:2]), math.dist(l_moves[-1].end[:2], om.end[:2]),
                 0.71 * 10 ** -DEC)
    length = _xy_length(poly)
    slope = 0.0 if degenerate else abs(om.end[2] - om.start[2]) / length
    ptr = 0
    for index, lm in enumerate(l_moves):
        last_move = index == len(l_moves) - 1
        full = (pre_ok or index > 0) and not degenerate
        # a small error of the start (up to 2 * BASE_TOL, else the first move is only checked
        # at its end) fades out along the first move
        tol = BASE_TOL + om.extra + slope * xy_dev + (start_err if index == 0 and pre_ok else 0.0)
        points = lm.pts if full else [lm.end]
        for k, (x, y, z) in enumerate(points):
            if degenerate or (last_move and k == len(points) - 1):
                z_orig, dist = om.end[2], 0.0
            else:
                ptr, z_orig, dist = _project(poly, ptr, window if full else len(poly), x, y)
            err = abs(z - (z_orig + offset(x, y)))
            report.samples += 1
            report.worst_z = max(report.worst_z, err - om.extra - slope * xy_dev)
            test.assertLessEqual(dist, xy_bound, 'sample off the original path' + ctx)
            test.assertLessEqual(err, tol, 'Z error %.3g (tolerance %.3g) at (%.4f, %.4f)%s'
                                 % (err, tol, x, y, ctx))
        if last_move:
            report.worst_xy = max(report.worst_xy, math.dist(lm.end[:2], om.end[:2]))


# -------------------------------------------------------------- generator

def f6(v, decimals=6):
    text = '%.*f' % (decimals, v)
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return '0' if text in ('', '-0') else text


class Gen:
    """Random valid programs; the position is tracked by a Sim of the original program."""

    def __init__(self, rng, decimals=6, g91=True, arcs=True, abs_ij=False, rformat=True,
                 helical=True, chain=True):
        self.rng, self.decimals = rng, decimals
        self.g91, self.arcs, self.abs_ij = g91, arcs, abs_ij
        self.rformat, self.helical, self.chain = rformat, helical, chain
        self.sim = Sim()
        self.lines = []
        self.n_arcs = 0

    def num(self, v):
        return f6(v, self.decimals)

    def emit(self, line):
        self.lines.append(line)
        self.sim.run(line)

    def word(self, axis, target):
        """Axis word that brings the axis to `target` (absolute or as a delta)."""
        value = target
        if not self.sim.absolute:
            value = target - self.sim.pos['XYZ'.index(axis)]
        return axis + self.num(value)

    def prologue(self):
        r = self.rng
        self.emit('G21 G17 G90 G94')
        if self.abs_ij:
            self.emit('G90.1')
        self.emit('G0 X%s Y%s Z%s' % (self.num(r.uniform(-20, 20)), self.num(r.uniform(-20, 20)), self.num(5)))
        self.emit('G1 X%s Y%s Z%s F100' % (self.num(r.uniform(-20, 20)), self.num(r.uniform(-20, 20)),
                                           self.num(r.uniform(-1.5, 0.2))))

    def op_cut(self, motion='G1'):
        r = self.rng
        axes = [a for a in 'XYZ' if r.random() < 0.7] or ['X']
        parts = [motion] if (motion != 'G%d' % self.sim.g or r.random() < 0.5) else []
        for a in axes:
            if a == 'Z':
                target = r.uniform(-2.0, 0.5) if motion == 'G1' else r.uniform(2.0, 6.0)
            else:
                target = self.sim.pos['XYZ'.index(a)] + r.uniform(-12, 12)
            parts.append(self.word(a, target))
        if motion == 'G1' and r.random() < 0.3:
            parts.append('F%d' % r.randint(50, 500))
        self.emit(' '.join(parts))

    def op_mode(self):
        self.emit('G91' if self.sim.absolute else 'G90')

    def op_arc(self, kind=None):
        r = self.rng
        sim = self.sim
        kind = kind or r.choice(['small', 'medium', 'large', 'full'])
        clockwise = r.random() < 0.5
        radius = r.uniform(0.4, 15.0)
        sweep = {'small': lambda: math.radians(r.uniform(3, 30)),
                 'medium': lambda: math.radians(r.uniform(30, 170)),
                 'large': lambda: math.radians(r.uniform(190, 350)),
                 'full': lambda: TWO_PI}[kind]()
        direction = r.uniform(0, TWO_PI)
        sx, sy, sz = sim.pos
        cx, cy = sx + radius * math.cos(direction), sy + radius * math.sin(direction)
        a_s = direction + math.pi
        a_e = a_s - sweep if clockwise else a_s + sweep
        ex, ey = cx + radius * math.cos(a_e), cy + radius * math.sin(a_e)
        ez = sz
        if self.helical and r.random() < 0.4:
            ez = sz + r.uniform(-1.0, 1.0)
        full = sweep == TWO_PI
        parts = []
        if not (self.chain and sim.g == (2 if clockwise else 3) and r.random() < 0.8):
            parts.append('G2' if clockwise else 'G3')
        use_r = (self.rformat and not full and sweep > math.radians(5)
                 and abs(sweep - math.pi) > math.radians(2) and r.random() < 0.4)
        if full:
            # the end is the start: no X/Y word, or the same X/Y in G90
            if sim.absolute and r.random() < 0.5 and float(self.num(sx)) == sx and float(self.num(sy)) == sy:
                parts += ['X' + self.num(sx), 'Y' + self.num(sy)]
        else:
            parts += [self.word('X', ex), self.word('Y', ey)]
        if ez != sz or r.random() < 0.1:
            parts.append(self.word('Z', ez))
        if use_r:
            parts.append('R' + self.num(radius if sweep < math.pi else -radius))
        elif sim.arc_abs:
            parts += ['I' + self.num(cx), 'J' + self.num(cy)]
        else:
            i_val, j_val = cx - sx, cy - sy
            if abs(round(i_val, self.decimals)) < 1e-9 and r.random() < 0.8:
                parts.append('J' + self.num(j_val))
            elif abs(round(j_val, self.decimals)) < 1e-9 and r.random() < 0.8:
                parts.append('I' + self.num(i_val))
            else:
                parts += ['I' + self.num(i_val), 'J' + self.num(j_val)]
        if r.random() < 0.2:
            parts.append('F%d' % r.randint(50, 400))
        text = ' '.join(parts)
        if r.random() < 0.2:
            text += ' (arc)'
        self.emit(text)
        self.n_arcs += 1

    def program(self, n_ops):
        r = self.rng
        self.prologue()
        for _ in range(n_ops):
            choice = r.random()
            if choice < 0.12 and self.g91:
                self.op_mode()
            elif choice < 0.22:
                self.op_cut('G0')
            elif choice < 0.52 or not self.arcs:
                self.op_cut('G1')
            else:
                self.op_arc()
        return self.lines


class SimSelfTest(unittest.TestCase):
    """The simulator itself, against hand-computed geometry."""

    def test_quarter_circle_ccw(self):
        s = Sim(10, 0, 0)
        (m,) = s.run('G3 X0 Y10 I-10 J0')
        self.assertAlmostEqual(m.sweep, math.pi / 2)
        for x, y, _ in m.pts:
            self.assertAlmostEqual(math.hypot(x, y), 10.0, places=9)
        self.assertEqual(s.pos, [0, 10, 0])

    def test_quarter_circle_cw_goes_the_long_way_round(self):
        s = Sim(10, 0, 0)
        (m,) = s.run('G2 X0 Y10 I-10 J0')
        self.assertAlmostEqual(m.sweep, 1.5 * math.pi)

    def test_r_format_both_signs(self):
        for cmd, expected in (('G3 X0 Y10 R10', math.pi / 2), ('G3 X0 Y10 R-10', 1.5 * math.pi),
                              ('G2 X0 Y10 R10', math.pi / 2), ('G2 X0 Y10 R-10', 1.5 * math.pi)):
            s = Sim(10, 0, 0)
            (m,) = s.run(cmd)
            self.assertAlmostEqual(m.sweep, expected, msg=cmd)
        # G2 R10: the short clockwise arc has its centre at (10, 10)
        s = Sim(10, 0, 0)
        (m,) = s.run('G2 X0 Y10 R10')
        for x, y, _ in m.pts:
            self.assertAlmostEqual(math.hypot(x - 10, y - 10), 10.0, places=6)

    def test_full_circle_and_helix(self):
        s = Sim(5, 0, 0)
        (m,) = s.run('G3 Z-1 I-5 J0')
        self.assertAlmostEqual(m.sweep, TWO_PI)
        self.assertAlmostEqual(m.pts[len(m.pts) // 2 - 1][2], -0.5, delta=0.01)
        self.assertEqual(s.pos, [5, 0, -1])

    def test_relative_and_absolute_ij(self):
        s = Sim(10, 10, 0)
        s.run('G91')
        (m,) = s.run('G3 X-10 Y-10 I-10 J0')                       # centre (0, 10)
        self.assertAlmostEqual(m.rmax, 10.0)
        self.assertAlmostEqual(m.sweep, 1.5 * math.pi)
        s = Sim(10, 10, 0)
        s.run('G90.1')
        (m,) = s.run('G3 X0 Y20 I0 J10')
        self.assertAlmostEqual(m.sweep, math.pi / 2)
        s.run('G91.1')
        s.run('G91')
        (m,) = s.run('G3 X-10 Y-10 I-10 J0')
        self.assertAlmostEqual(m.rmax, 10.0)

    def test_g18_arc_is_opaque_and_modal_g_is_needed(self):
        s = Sim(0, 0, 0)
        s.run('G18')
        s.g = 1
        (m,) = s.run('X5 Z3 I1 K0')
        self.assertEqual(m.kind, 'cut')
        s.g = 2
        (m,) = s.run('X5 Z3 I1 K0')
        self.assertEqual(m.kind, 'opaque_arc')

    def test_block_delete(self):
        s = Sim(block_delete=True)
        self.assertEqual(s.run('/G1 X5'), [])
        self.assertEqual(len(s.run('G1 X5')), 1)

    def test_the_checker_is_not_blind(self):
        """Levelling with a slightly different offset than the one checked must fail."""
        def wrong(x, y):
            return plane(x, y) + 0.0003 * x
        lines = ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0']
        check_program(self, lines)
        with self.assertRaises(AssertionError):
            check_program(self, lines, level_offset=wrong)
        with self.assertRaises(AssertionError):                 # a helix that is flat at the end
            check_program(self, ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', 'G3 X0 Y10 Z-2 I-10 J0'],
                          level_offset=lambda x, y: plane(x, y) + (0.002 if x < 3 else 0.0))


class TestRandomArcs(unittest.TestCase):
    """Random G2/G3 arcs, chained, in G90 and G91, I/J and R, helical and full."""

    def run_many(self, seed, count, ops=14, **kwargs):
        rng = random.Random(seed)
        report = Report()
        for k in range(count):
            gen = Gen(rng, **kwargs)
            lines = gen.program(ops)
            with self.subTest(program=k):
                state, orig, lev, pairs = check_program(self, lines, report=report)
                self.assertEqual(state['arcs_split'], gen.n_arcs)
        return report

    def test_g90_only_ij_and_r(self):
        self.run_many(1, 25, g91=False)

    def test_g90_and_g91_mixed(self):
        self.run_many(2, 25)

    def test_arcs_only_chained(self):
        rng = random.Random(3)
        for k in range(20):
            gen = Gen(rng)
            gen.prologue()
            for _ in range(10):
                gen.op_arc()
            with self.subTest(program=k):
                check_program(self, gen.lines)

    def test_arcs_only_chained_in_g91(self):
        rng = random.Random(4)
        for k in range(20):
            gen = Gen(rng)
            gen.prologue()
            gen.emit('G91')
            for _ in range(10):
                gen.op_arc()
            with self.subTest(program=k):
                check_program(self, gen.lines)

    def test_every_sweep_class_both_modes(self):
        rng = random.Random(5)
        for kind in ('small', 'medium', 'large', 'full'):
            for g91 in (False, True):
                for k in range(8):
                    gen = Gen(rng)
                    gen.prologue()
                    if g91:
                        gen.emit('G91')
                    for _ in range(3):
                        gen.op_arc(kind)
                    with self.subTest(kind=kind, g91=g91, program=k):
                        state = check_program(self, gen.lines)[0]
                        self.assertEqual(state['arcs_split'], 3)

    def test_helical_only(self):
        rng = random.Random(6)
        for k in range(20):
            gen = Gen(rng, helical=True)
            gen.prologue()
            if k % 2:
                gen.emit('G91')
            for _ in range(6):
                gen.op_arc()
            with self.subTest(program=k):
                check_program(self, gen.lines)

    def test_on_the_program_grid(self):
        # numbers with the same decimals as the output: X/Y must come out exact
        rng = random.Random(7)
        report = Report()
        for k in range(15):
            gen = Gen(rng, decimals=DEC)
            lines = gen.program(12)
            with self.subTest(program=k):
                check_program(self, lines, report=report)
        self.assertLessEqual(report.worst_xy, 2 * 10 ** -DEC)

    def test_absolute_ij(self):
        rng = random.Random(8)
        for k in range(20):
            gen = Gen(rng, abs_ij=True, rformat=False)
            lines = gen.program(12)
            self.assertIn('G90.1', lines)
            with self.subTest(program=k):
                check_program(self, lines)

    def test_absolute_ij_switched_in_the_middle(self):
        rng = random.Random(9)
        for k in range(15):
            gen = Gen(rng, rformat=False)
            gen.prologue()
            for _ in range(4):
                gen.op_arc()
            gen.emit('G90.1')
            for _ in range(4):
                gen.op_arc()
            gen.emit('G91.1')
            gen.emit('G91')
            for _ in range(4):
                gen.op_arc()
            with self.subTest(program=k):
                check_program(self, gen.lines)

    def test_arc_segment_sizes(self):
        for segment in (0.05, 0.3, 2.0, 50.0):
            rng = random.Random(10)
            for k in range(6):
                gen = Gen(rng)
                lines = gen.program(10)
                with self.subTest(segment=segment, program=k):
                    check_program(self, lines, arc_segment=segment)


class TestMixedPrograms(unittest.TestCase):
    def test_retracts_cuts_arcs_and_mode_changes(self):
        rng = random.Random(11)
        for k in range(40):
            gen = Gen(rng)
            lines = gen.program(25)
            with self.subTest(program=k):
                check_program(self, lines)

    def test_retract_in_g90_returns_to_the_original_z(self):
        rng = random.Random(12)
        for k in range(20):
            gen = Gen(rng)
            gen.prologue()
            for _ in range(8):
                gen.op_arc()
                gen.op_cut('G1')
            gen.emit('G90')
            gen.emit('G0 Z%s' % gen.num(7.5))
            with self.subTest(program=k):
                state, orig, lev, pairs = check_program(self, gen.lines)
                self.assertEqual(lev.pos[2], orig.pos[2])
                self.assertAlmostEqual(lev.pos[0], orig.pos[0], delta=4 * 10 ** -DEC)
                self.assertAlmostEqual(lev.pos[1], orig.pos[1], delta=4 * 10 ** -DEC)

    def test_cut_end_carries_the_offset(self):
        rng = random.Random(13)
        for k in range(40):
            gen = Gen(rng)
            gen.prologue()
            for _ in range(6):
                gen.op_arc()
                gen.op_cut('G1')
            if k % 2:
                gen.emit('G91')
            gen.op_arc()
            with self.subTest(program=k):
                state, orig, lev, pairs = check_program(self, gen.lines)
                expected = orig.pos[2] + plane(lev.pos[0], lev.pos[1])
                self.assertAlmostEqual(lev.pos[2], expected, delta=BASE_TOL)

    def test_g91_g0_retract_keeps_the_delta_and_the_next_cut_returns_to_the_surface(self):
        lines = ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', 'G91', 'G0 Z3', 'G0 X5', 'G1 Z-3 X2 Y2',
                 'G3 X0 Y4 I-2 J2', 'G1 X1', 'G90', 'G0 Z6']
        state, orig, lev, pairs = check_program(self, lines)
        self.assertEqual(lev.pos[2], 6.0)

    def test_modes_on_the_arc_line(self):
        lines = ['G90 G0 X0 Y0 Z5', 'G90 G1 X10 Y0 Z-1', 'G91 G3 X-10 Y10 I-10 J0',
                 'G90 G2 X10 Y20 I10 J0', 'G91 G17 G91.1 G2 X10 Y-10 I0 J-10 F200 (cw)']
        state, orig, lev, pairs = check_program(self, lines)
        self.assertEqual(state['arcs_split'], 3)

    def test_1000_small_g91_moves_do_not_drift(self):
        rng = random.Random(14)
        lines = ['G90 G0 X0 Y0 Z5', 'G1 X0 Y0 Z-0.5', 'G91']
        for _ in range(1000):
            kind = rng.random()
            dx, dy = rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3)
            if kind < 0.6:
                lines.append('G1 X%s Y%s' % (f6(dx, 5), f6(dy, 5)))
            elif kind < 0.8:
                lines.append('G1 X%s Y%s Z%s' % (f6(dx, 5), f6(dy, 5), f6(rng.uniform(-0.02, 0.02), 5)))
            else:
                lines.append('G1 Z%s' % f6(rng.uniform(-0.05, 0.05), 5))
        state = new_levelling_state(arc_segment=0.5)
        orig, lev = Sim(), Sim()
        worst = 0.0
        for line in lines:
            out = level_gcode_line(line, state, plane, DEC)
            orig.run(line)
            lev.run(out)
            if line.startswith('G1'):
                worst = max(worst, abs(lev.pos[2] - (orig.pos[2] + plane(lev.pos[0], lev.pos[1]))))
        self.assertLessEqual(worst, 10 ** -DEC)
        self.assertLessEqual(worst, 0.5 * 10 ** -DEC + 1e-6)
        self.assertEqual((state['arcs'], state['relative']), (0, 0))

    def test_1000_equal_g91_moves_do_not_drift(self):
        # the same small step: a rounding that is not compensated would add up
        state = new_levelling_state()
        orig, lev = Sim(), Sim()
        lines = ['G90 G0 X0 Y0 Z5', 'G1 X0 Y0 Z0', 'G91'] + ['G1 X0.0123 Y0.00777'] * 1000
        for line in lines:
            out = level_gcode_line(line, state, plane, DEC)
            orig.run(line)
            lev.run(out)
        self.assertLessEqual(abs(lev.pos[2] - (orig.pos[2] + plane(lev.pos[0], lev.pos[1]))),
                             0.5 * 10 ** -DEC + 1e-6)


class TestUnsplittableAfterSplit(unittest.TestCase):
    SETUP = ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', 'G3 X0 Y10 I-10 J0']

    def test_g18_arc_without_g_word_runs_as_an_arc(self):
        lines = self.SETUP + ['G18', 'X5 Z3 I1 K0']
        state, orig, lev, pairs = check_program(self, lines, expect_all_compensated=False)
        self.assertEqual(state['arcs_split'], 1)
        self.assertEqual(state['arcs'], 1)
        self.assertRegex(pairs[-1][1], r'^\s*G3\b')

    def test_g18_on_the_same_line(self):
        lines = self.SETUP + ['G18 X5 Z3 I1 K0']
        state, orig, lev, pairs = check_program(self, lines, expect_all_compensated=False)
        self.assertIn('G3', pairs[-1][1])

    def test_g19_after_g2_with_line_number_and_block_delete(self):
        for lead in ('N20 ', '/', '/ N20 ', '  N20  '):
            lines = ['G90 G0 X0 Y0 Z5', 'G1 X0 Y10 Z-1', 'G2 X10 Y0 I0 J-10', 'G19',
                     lead + 'Y5 Z3 J1 K0']
            with self.subTest(lead=lead):
                state, orig, lev, pairs = check_program(self, lines, expect_all_compensated=False)
                self.assertRegex(pairs[-1][1], r'^\s*(/\s*)?(N\s*20\s*)?G2 ')

    def test_chain_of_unsplittable_arcs_then_a_splittable_one(self):
        lines = self.SETUP + ['G18', 'X5 Z3 I1 K0', 'X6 Z4 I1 K0', 'G17', 'X-6 Y10 I-6 J0']
        state, orig, lev, pairs = check_program(self, lines, expect_all_compensated=False)
        self.assertEqual(state['arcs_split'], 2)

    def test_bad_radius_arc_after_split_runs_as_an_arc(self):
        lines = self.SETUP + ['X0 Y0 I-9 J0']
        state, orig, lev, pairs = check_program(self, lines, expect_all_compensated=False)
        self.assertEqual(state['arcs'], 1)
        self.assertTrue(pairs[-1][1].startswith('G3 '))

    def test_a_line_move_after_a_split_arc(self):
        lines = self.SETUP + ['G1 X20 Y0']
        check_program(self, lines)


class TestBlockDelete(unittest.TestCase):
    def test_block_deleted_arc_stays_deleted(self):
        lines = ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', '/G3 X0 Y10 I-10 J0', 'G1 X0 Y20']
        for deleted in (True, False):
            with self.subTest(block_delete=deleted):
                check_program(self, lines, block_delete=deleted)

    def test_block_deleted_arc_with_space(self):
        lines = ['G90 G0 X0 Y0 Z5', 'G1 X10 Y0 Z-1', '  / G3 X0 Y10 I-10 J0', 'G1 X0 Y20']
        for deleted in (True, False):
            with self.subTest(block_delete=deleted):
                check_program(self, lines, block_delete=deleted)


if __name__ == '__main__':
    unittest.main()
