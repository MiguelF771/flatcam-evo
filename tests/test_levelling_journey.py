#!/usr/bin/env python
"""
End-to-end "journey" tests for the autolevelling feature (Task D):
Gerber -> toolpaths -> G-code -> height map (probed/imported) -> levelled
G-code, checking that the Z compensation applied to each cutting move is
correct.

These tests build their own G-code from real Gerber test files (parsed with
appParsers.ParseGerber.Gerber, the same MockApp pattern as
tests/test_gerber_parser.py) and their own height-map files/text (consumed
by the *real* ToolLevelling.import_height_map()/_al_check_probe_reply()),
then run the *real* ToolLevelling.autolevell_gcode() and independently
verify every line of the result with a small G-code reader written in this
file (it does not call level_gcode_line()/codes_split()).

Run: python tests/test_levelling_journey.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math
import random
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from PyQt6 import QtWidgets

_qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

# appParsers.ParseGerber (transitively) imports appMain, whose module-level
# code parses sys.argv with getopt; running this file as
# `python tests/test_levelling_journey.py -v` would otherwise blow up with a
# GetoptError on our own `-v` flag. Neutralize sys.argv for the duration of
# these FlatCAM imports only.
_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
try:
    from shapely import Point
    from shapely.ops import unary_union

    from appParsers.ParseGerber import Gerber
    from appPlugins.ToolLevelling import ToolLevelling
    from appPlugins.levelling_interp import parse_grbl_work_offset
    import camlib

    # appParsers.ParseGerber.Gerber.__init__() itself does a *lazy*
    # `from appMain import App` the first time a Gerber() is constructed
    # (not at module import time), which would otherwise re-trigger the
    # sys.argv parsing later, well after this block has restored argv.
    # Force that import to happen now, once, while argv is still patched,
    # so it's cached in sys.modules for every Gerber() built by the tests.
    import appMain  # noqa: F401

    from test_levelling_tool import make_tool, make_tool_target  # noqa: E402
finally:
    sys.argv = _saved_argv


TEST_FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'test_files')
SIMPLE_LINE_GBR = os.path.join(TEST_FILES_DIR, 'simple_line.gbr')
REGION_GBR = os.path.join(TEST_FILES_DIR, 'region_test.gbr')
FLASH_GBR = os.path.join(TEST_FILES_DIR, 'flash_test.gbr')
ALL_GERBERS = [SIMPLE_LINE_GBR, REGION_GBR, FLASH_GBR]


# ---------------------------------------------------------------------------
# Gerber -> toolpaths
# ---------------------------------------------------------------------------

class MockGerberLog:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass

    def info(self, msg):
        pass


class MockGerberInform:
    def emit(self, msg):
        pass


class MockGerberApp:
    """Same pattern as tests/test_gerber_parser.py's MockApp."""

    def __init__(self):
        self.options = {
            'gerber_def_units': 'MM',
            'gerber_def_zeros': 'L',
            'gerber_circle_steps': 64,
            'gerber_simp_tolerance': 0.001,
            'gerber_simplification': True,
            'gerber_buffering': True,
            'gerber_extra_buffering': 0.0,
            'gerber_clean_apertures': True,
            'gerber_use_buffer_for_union': True,
            'global_tolerance': 0.01,
        }
        self.decimals = 4
        self.abort_flag = False
        self.log = MockGerberLog()
        self.inform = MockGerberInform()
        self.app_units = 'MM'
        self.use_3d_engine = True

        class MockPlotCanvas:
            class new_shape_collection:
                def __init__(self, layers=1):
                    pass

            def __init__(self):
                pass

        self.plotcanvas = MockPlotCanvas()

        class MockProcContainer:
            def update_view_text(self, text):
                pass

        self.proc_container = MockProcContainer()


def gerber_toolpaths(path, tool_dia=1.0):
    """Parse a Gerber file and build isolation-routing toolpaths: the
    exterior/interior rings of solid_geometry buffered outward by
    tool_dia/2. Returns a list of coordinate paths (list of (x, y)
    tuples, closed rings), always in mm."""
    gbr = Gerber(MockGerberApp())
    gbr.parse_file(path)

    geoms = gbr.solid_geometry
    union = unary_union(geoms) if isinstance(geoms, list) else geoms
    outline = union.buffer(tool_dia / 2.0)

    polys = list(outline.geoms) if hasattr(outline, 'geoms') else [outline]

    paths = []
    for poly in polys:
        paths.append(list(poly.exterior.coords))
        for interior in poly.interiors:
            paths.append(list(interior.coords))

    if gbr.units == 'IN':
        paths = [[(x * 25.4, y * 25.4) for x, y in ring] for ring in paths]

    return paths


def paths_bounds(paths, pad_frac=0.1):
    xs = [x for ring in paths for x, y in ring]
    ys = [y for ring in paths for x, y in ring]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    padx = max((xmax - xmin) * pad_frac, 1.0)
    pady = max((ymax - ymin) * pad_frac, 1.0)
    return xmin - padx, xmax + padx, ymin - pady, ymax + pady


def make_grid_points(bounds, nx=4, ny=4):
    xmin, xmax, ymin, ymax = bounds
    xs = [xmin + i * (xmax - xmin) / (nx - 1) for i in range(nx)]
    ys = [ymin + j * (ymax - ymin) / (ny - 1) for j in range(ny)]
    return xs, ys


# ---------------------------------------------------------------------------
# G-code generation (FlatCAM/GRBL_11-style, segmented)
# ---------------------------------------------------------------------------

def _subdivide_path(points, max_len):
    if not points:
        return points
    out = [points[0]]
    for (x0, y0), (x1, y1) in zip(points[:-1], points[1:]):
        dist = math.hypot(x1 - x0, y1 - y0)
        if dist <= max_len or dist == 0:
            out.append((x1, y1))
            continue
        n = int(math.ceil(dist / max_len))
        for i in range(1, n + 1):
            t = i / n
            out.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return out


def make_gcode(paths, cut_z=-0.1, travel_z=2.0, feed=300.0, segment_len=5.0,
               decimals=4, spindle=10000, include_arc=True):
    """Build FlatCAM/GRBL_11-style segmented G-code for a list of toolpaths.
    Guaranteed: no G01 cutting segment longer than segment_len (is_segmented
    semantics); at least 1 trailing inline comment; 1 G02 arc line (at
    travel height, so it never interferes with Z-levelling)."""

    def f(v):
        return "{0:.{1}f}".format(v, decimals)

    lines = [
        "(FlatCAM Gcode Generator)",
        "(This is a Test)",
        "G21",
        "G90",
        "G94",
        "F" + f(feed),
        "M03 S%d" % spindle,
    ]

    last_x, last_y = 0.0, 0.0
    for idx, ring in enumerate(paths):
        pts = _subdivide_path(ring, segment_len)
        x0, y0 = pts[0]
        comment = " (start path %d)" % idx if idx == 0 else ""
        lines.append("G00 Z" + f(travel_z) + comment)
        lines.append("G00 X" + f(x0) + " Y" + f(y0))
        lines.append("G01 Z" + f(cut_z) + " F" + f(feed))
        for x, y in pts[1:]:
            lines.append("G01 X" + f(x) + " Y" + f(y))
        lines.append("G00 Z" + f(travel_z))
        last_x, last_y = x0, y0

    if include_arc:
        # Semicircle at travel height (Z already lifted above 0): must
        # survive untouched (arcs are never height-compensated).
        cx, cy = last_x + 1.0, last_y
        x2, y2 = last_x + 2.0, last_y
        lines.append("G02 X" + f(x2) + " Y" + f(y2) + " I1.0 J0.0 (return arc)")

    # A rapid at or below Z0: rapids are never height-compensated regardless
    # of their Z value, only cutting (G1) moves are. Without at least 1 rapid
    # sitting at Z<=0 a mutant that also levels G0 moves would go unnoticed
    # (every other G0 in this file is at the travel height, which is > 0).
    lines.append("G00 Z-0.0500 (rapid at Z<=0 - must never be levelled)")
    lines.append("G00 X0.0000 Y0.0000")

    lines += ["M05", "G00 X0.0000 Y0.0000", "M2"]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------

def make_plane(a, b, c):
    def fn(x, y):
        return a * x + b * y + c
    return fn


def make_saddle(k, x0, y0):
    def fn(x, y):
        return k * (x - x0) * (y - y0)
    return fn


def make_bump(amplitude, cx, cy, sigma):
    def fn(x, y):
        return amplitude * math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (sigma ** 2))
    return fn


def nearest_height_bruteforce(points, x, y):
    """points: list of (x, y, height). Independent re-implementation (does
    not call levelling_interp.nearest_offset)."""
    best = None
    best_d = None
    for px, py, pz in points:
        d = math.hypot(px - x, py - y)
        if best_d is None or d < best_d:
            best_d = d
            best = pz
    return best


# ---------------------------------------------------------------------------
# Height-map file writers (real files consumed by the real import_height_map)
# ---------------------------------------------------------------------------

def write_mach3(path, points, decimals=5):
    with open(path, 'w') as f:
        for x, y, z in points:
            f.write(("%.{0}f,%.{0}f,%.{0}f\n".format(decimals)) % (x, y, z))


def write_mach4_extra_columns(path, points, decimals=5):
    with open(path, 'w') as f:
        for x, y, z in points:
            f.write(("%.{0}f, %.{0}f, %.{0}f, 0.0, 0.0, 0.0\n".format(decimals)) % (x, y, z))


def write_linuxcnc(path, points, decimals=5):
    with open(path, 'w') as f:
        for x, y, z in points:
            f.write(("%.{0}f %.{0}f %.{0}f 0 0 0 0 0 0\n".format(decimals)) % (x, y, z))


def prefill_storage(grid_points):
    """Mimic on_add_grid_points(): storage pre-filled with the probe grid
    points (height 0.0, not yet probed) before the height-map file/probe
    result comes back. Used so import_height_map() must go through its
    "match existing storage points by nearest coordinate" branch instead of
    the "storage is empty, create points from file rows" branch."""
    return {
        i: {'point': Point(x, y), 'geo': None, 'height': 0.0}
        for i, (x, y, _z) in enumerate(grid_points)
    }


def shuffled_rows(points, seed=1234):
    """Return `points` in a different order than the canonical grid order,
    as a real probe-log file would not necessarily be written back in the
    same order the storage dict was built in."""
    rows = list(points)
    random.Random(seed).shuffle(rows)
    return rows


def make_grbl_offset_text(g54=(-100.0, -50.0, -20.0), g92=(0.0, 0.0, 0.0), tlo=0.0):
    return (
        "[G54:%.3f,%.3f,%.3f]\n" % g54 +
        "[G92:%.3f,%.3f,%.3f]\n" % g92 +
        "[TLO:%.3f]\n" % tlo +
        "ok\n"
    )


def make_grbl_probe_text(points, offset, noise=0.0125):
    ox, oy, oz = offset
    lines = []
    for x, y, z in points:
        mx = x + noise + ox
        my = y - noise + oy
        mz = z + oz
        lines.append("[PRB:%.4f,%.4f,%.4f:1]" % (mx, my, mz))
        lines.append("ok")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Independent G-code reader/verifier (does NOT call level_gcode_line/codes_split)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r'([A-Z])([+\-]?\d*\.?\d+)')


def _line_words(line):
    code = line.split('(')[0]
    return {m.group(1): float(m.group(2)) for m in _WORD_RE.finditer(code)}


def verify_levelling(tc, original_text, levelled_text, decimals, offset_fn, tol=None):
    """Walk `original_text` and `levelled_text` line-by-line with an
    independent modal-state tracker and check:
      - same number of lines
      - every non-cut-move line is byte-identical
      - X/Y never change anywhere
      - every G1 cut line (any modal Z) got Z = original modal Z + offset_fn(x, y)
      - a line with no Z word originally now has one after levelling

    Returns (max_abs_dz, cut_points) where cut_points is the list of
    (x, y) at every cut line encountered (for range/sanity checks).
    """
    if tol is None:
        tol = 2 * 10 ** (-decimals)
    orig_lines = original_text.splitlines()
    lev_lines = levelled_text.splitlines()
    tc.assertEqual(len(orig_lines), len(lev_lines), "levelled line count differs from source")

    modal_g = None
    modal_x = 0.0
    modal_y = 0.0
    modal_z = 0.0
    max_abs_dz = 0.0
    cut_points = []

    for lineno, (orig_line, lev_line) in enumerate(zip(orig_lines, lev_lines)):
        ow = _line_words(orig_line)
        lw = _line_words(lev_line)

        has_xyz = ('X' in ow) or ('Y' in ow) or ('Z' in ow)

        if 'G' in ow and ow['G'] in (0.0, 1.0, 2.0, 3.0):
            modal_g = ow['G']

        if 'X' in ow:
            modal_x = ow['X']
        if 'Y' in ow:
            modal_y = ow['Y']
        if 'Z' in ow:
            modal_z = ow['Z']

        tc.assertEqual(ow.get('X'), lw.get('X'), "line %d: X changed (%r vs %r)" % (lineno, orig_line, lev_line))
        tc.assertEqual(ow.get('Y'), lw.get('Y'), "line %d: Y changed (%r vs %r)" % (lineno, orig_line, lev_line))

        # A G0 word on this exact line always means "rapid, never a cutting
        # move" regardless of Z, even if a bug elsewhere left modal_g stale;
        # checked explicitly (not just via modal_g == 1.0) so a mutant that
        # widens the cutting-move check to include G0 is caught even at a
        # rapid whose Z happens to be <= 0.
        is_g0_line = 'G' in ow and ow['G'] == 0.0
        is_cut_line = has_xyz and modal_g == 1.0 and not is_g0_line

        if not is_cut_line:
            tc.assertEqual(orig_line, lev_line, "line %d should be untouched: %r vs %r" % (lineno, orig_line, lev_line))
            continue

        tc.assertIn('Z', lw, "line %d: missing Z after levelling: %r" % (lineno, lev_line))
        expected_z = modal_z + offset_fn(modal_x, modal_y)
        tc.assertAlmostEqual(lw['Z'], expected_z, delta=tol,
                              msg="line %d: bad levelled Z (%r vs %r)" % (lineno, lev_line, orig_line))

        dz = lw['Z'] - modal_z
        max_abs_dz = max(max_abs_dz, abs(dz))
        cut_points.append((modal_x, modal_y))

    return max_abs_dz, cut_points


class LevellingJourneyTestCase(unittest.TestCase):
    """Common assertions shared by every journey test."""

    def assert_journey(self, tool, target, tooluid, al_method, offset_fn, expect_warning=None, tol=None):
        original = target.tools[tooluid]['gcode']
        result = tool.autolevell_gcode(target, al_method)
        self.assertIsNotNone(result, "autolevell_gcode() returned None")

        # source untouched
        self.assertEqual(target.tools[tooluid]['gcode'], original)

        max_abs_dz, cut_points = verify_levelling(
            self, original, result[tooluid], target.coords_decimals, offset_fn, tol=tol
        )

        # sanity check: levelling really happened (not a no-op)
        self.assertGreater(len(cut_points), 0, "no cut lines found to verify")
        expected_max_abs = max(abs(offset_fn(x, y)) for x, y in cut_points)
        range_tol = tol if tol is not None else 2 * 10 ** (-target.coords_decimals)
        self.assertAlmostEqual(max_abs_dz, expected_max_abs, delta=range_tol)
        self.assertGreater(max_abs_dz, 1e-6, "levelling produced no change at all")

        if expect_warning is not None:
            warning_calls = [
                c for c in tool.app.inform.emit.call_args_list
                if '[WARNING_NOTCL]' in c.args[0] and expect_warning in c.args[0]
            ]
            self.assertEqual(len(warning_calls), 1)

        return result


# ---------------------------------------------------------------------------
# Journey 1: each Gerber x each height-map source, tilted plane, method 'b'
# ---------------------------------------------------------------------------

class TestJourneyPlaneAllSources(LevellingJourneyTestCase):
    def test_plane_bilinear_all_gerbers_all_sources(self):
        # 'mach3_empty_storage' keeps import_height_map()'s "storage is empty,
        # create points straight from the file rows" branch covered. The
        # other 3 file-based sources pre-fill storage (as on_add_grid_points()
        # does before a real probe/import) and write rows out of order and
        # rounded to 3 decimals (as a controller log would), so
        # import_height_map() is forced through its "match an existing
        # storage point by nearest coordinate" branch instead.
        sources = ['mach3', 'mach3_empty_storage', 'mach4', 'linuxcnc', 'grbl']
        for gerber_path in ALL_GERBERS:
            paths = gerber_toolpaths(gerber_path)
            gcode_text = make_gcode(paths)
            bounds = paths_bounds(paths)
            xs, ys = make_grid_points(bounds, nx=4, ny=4)
            plane_fn = make_plane(0.0003, -0.0002, 0.05)
            grid_points = [(x, y, plane_fn(x, y)) for x in xs for y in ys]

            # Rows written with only 3 decimals of height precision (as a
            # controller log would) can be off from the exact plane value by
            # up to half a thousandth of a mm; the verifier's tolerance is
            # widened accordingly for those 3 sources.
            rounded_height_tol = 6e-4

            for source in sources:
                with self.subTest(gerber=os.path.basename(gerber_path), source=source):
                    with tempfile.TemporaryDirectory() as tmpdir:
                        target = make_tool_target({1: gcode_text}, units='MM')
                        tol = None

                        if source == 'mach3':
                            storage = prefill_storage(grid_points)
                            tool = make_tool(units='MM', storage=storage, heights_valid=False)
                            path = os.path.join(tmpdir, 'hm.txt')
                            write_mach3(path, shuffled_rows(grid_points), decimals=3)
                            tool.import_height_map(path)
                            tol = rounded_height_tol
                        elif source == 'mach3_empty_storage':
                            tool = make_tool(units='MM', storage={}, heights_valid=False)
                            path = os.path.join(tmpdir, 'hm.txt')
                            write_mach3(path, grid_points)
                            tool.import_height_map(path)
                        elif source == 'mach4':
                            storage = prefill_storage(grid_points)
                            tool = make_tool(units='MM', storage=storage, heights_valid=False)
                            path = os.path.join(tmpdir, 'hm.txt')
                            write_mach4_extra_columns(path, shuffled_rows(grid_points, seed=42), decimals=3)
                            tool.import_height_map(path)
                            tol = rounded_height_tol
                        elif source == 'linuxcnc':
                            storage = prefill_storage(grid_points)
                            tool = make_tool(units='MM', storage=storage, heights_valid=False)
                            path = os.path.join(tmpdir, 'hm.txt')
                            write_linuxcnc(path, shuffled_rows(grid_points, seed=99), decimals=3)
                            tool.import_height_map(path)
                            tol = rounded_height_tol
                        else:  # grbl
                            storage = prefill_storage(grid_points)
                            tool = make_tool(units='MM', storage=storage, heights_valid=False)
                            offset_text = make_grbl_offset_text()
                            tool.grbl_work_offset = parse_grbl_work_offset(offset_text)
                            # each G38.2 reply goes through the real per-probe check, then the
                            # complete result is published through the real GUI-thread slot
                            probe_text = make_grbl_probe_text(grid_points, tool.grbl_work_offset)
                            replies = [r for r in probe_text.split('ok\n') if r.strip()]
                            points = {k: (v['point'].x, v['point'].y) for k, v in storage.items()}
                            tol = tool._al_match_tolerance(list(points.values()))
                            heights = {
                                key: tool._al_check_probe_reply('G38.2', reply, key, points, tol,
                                                                tool.grbl_work_offset, 1.0)
                                for key, reply in zip(sorted(points), replies)
                            }
                            tool._on_al_result_ready({'kind': 'import', 'context': tool._al_capture_context(),
                                                      'heights': heights})

                        self.assertTrue(tool.al_heights_valid)
                        self.assert_journey(tool, target, 1, 'b', plane_fn, tol=tol)


# ---------------------------------------------------------------------------
# Journey 2: saddle surface, method 'b'
# ---------------------------------------------------------------------------

class TestJourneySaddleBilinear(LevellingJourneyTestCase):
    def test_saddle_bilinear_all_gerbers(self):
        for gerber_path in ALL_GERBERS:
            with self.subTest(gerber=os.path.basename(gerber_path)):
                paths = gerber_toolpaths(gerber_path)
                gcode_text = make_gcode(paths)
                bounds = paths_bounds(paths)
                xs, ys = make_grid_points(bounds, nx=4, ny=4)
                xmin, xmax, ymin, ymax = bounds
                cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
                half_dx = max((xmax - xmin) / 2.0, 1e-6)
                half_dy = max((ymax - ymin) / 2.0, 1e-6)
                k = 0.05 / (half_dx * half_dy)  # ~0.05mm swing at the grid corners
                saddle_fn = make_saddle(k, cx, cy)
                grid_points = [(x, y, saddle_fn(x, y)) for x in xs for y in ys]

                with tempfile.TemporaryDirectory() as tmpdir:
                    tool = make_tool(units='MM', storage={}, heights_valid=False)
                    path = os.path.join(tmpdir, 'hm.txt')
                    write_mach3(path, grid_points)
                    tool.import_height_map(path)
                    self.assertTrue(tool.al_heights_valid)

                    target = make_tool_target({1: gcode_text}, units='MM')
                    self.assert_journey(tool, target, 1, 'b', saddle_fn)


# ---------------------------------------------------------------------------
# Journey 3: irregular grid falls back to nearest, method 'b' (1 warning)
# ---------------------------------------------------------------------------

class TestJourneyIrregularGridFallback(LevellingJourneyTestCase):
    def test_irregular_grid_falls_back_to_nearest(self):
        for gerber_path in ALL_GERBERS:
            with self.subTest(gerber=os.path.basename(gerber_path)):
                paths = gerber_toolpaths(gerber_path)
                gcode_text = make_gcode(paths)
                bounds = paths_bounds(paths)
                xs, ys = make_grid_points(bounds, nx=3, ny=3)
                plane_fn = make_plane(0.0004, 0.0001, 0.03)
                grid_points = [(x, y, plane_fn(x, y)) for x in xs for y in ys]

                # Break the regular grid: nudge 1 point off its column/row.
                bad_x, bad_y, bad_z = grid_points[-1]
                grid_points[-1] = (bad_x + 7.0, bad_y + 3.0, bad_z)

                with tempfile.TemporaryDirectory() as tmpdir:
                    tool = make_tool(units='MM', storage={}, heights_valid=False)
                    path = os.path.join(tmpdir, 'hm.txt')
                    write_mach3(path, grid_points)
                    tool.import_height_map(path)
                    self.assertTrue(tool.al_heights_valid)

                    target = make_tool_target({1: gcode_text}, units='MM')

                    def nearest_fn(x, y):
                        return nearest_height_bruteforce(grid_points, x, y)

                    self.assert_journey(
                        tool, target, 1, 'b', nearest_fn,
                        expect_warning="Probe points do not form a regular grid"
                    )


# ---------------------------------------------------------------------------
# Journey 4: bump map, method 'v' (nearest point, no grid requirement)
# ---------------------------------------------------------------------------

class TestJourneyBumpNearest(LevellingJourneyTestCase):
    def test_bump_nearest_all_gerbers(self):
        for gerber_path in ALL_GERBERS:
            with self.subTest(gerber=os.path.basename(gerber_path)):
                paths = gerber_toolpaths(gerber_path)
                gcode_text = make_gcode(paths)
                bounds = paths_bounds(paths)
                xmin, xmax, ymin, ymax = bounds
                cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
                sigma = max((xmax - xmin), (ymax - ymin), 1.0)
                bump_fn = make_bump(0.4, cx, cy, sigma)

                # Scattered (non-grid) probe points.
                scatter = [
                    (xmin, ymin), (xmax, ymin), (xmin, ymax), (xmax, ymax),
                    (cx, cy), (xmin, cy), (cx, ymin),
                ]
                grid_points = [(x, y, bump_fn(x, y)) for x, y in scatter]

                with tempfile.TemporaryDirectory() as tmpdir:
                    tool = make_tool(units='MM', storage={}, heights_valid=False)
                    path = os.path.join(tmpdir, 'hm.txt')
                    write_mach3(path, grid_points)
                    tool.import_height_map(path)
                    self.assertTrue(tool.al_heights_valid)

                    target = make_tool_target({1: gcode_text}, units='MM')

                    def nearest_fn(x, y):
                        return nearest_height_bruteforce(grid_points, x, y)

                    self.assert_journey(tool, target, 1, 'v', nearest_fn)


# ---------------------------------------------------------------------------
# Journey 5: multi-tool, modal state carries across tools in dict order
# ---------------------------------------------------------------------------

class TestJourneyMultiTool(LevellingJourneyTestCase):
    def test_modal_state_carries_across_tools_from_two_gerbers(self):
        paths_a = gerber_toolpaths(SIMPLE_LINE_GBR)
        paths_b = gerber_toolpaths(REGION_GBR)
        ax0, ay0 = paths_a[0][0]
        bx0, by0 = paths_b[0][0]

        # Tool 9: a normal cut move that sets modal Z = -0.15 and leaves the
        # machine there (no rapid lift at the end).
        gcode_1 = (
            "G21\nG90\nG94\n"
            "G00 X{0:.4f} Y{1:.4f}\n"
            "G01 Z-0.1500 F300\n"
        ).format(ax0, ay0)

        # Tool 3: no G/Z word at all on its only line - if the modal state
        # were reset between tools, this would not be recognized as a
        # cutting move and would be left untouched.
        gcode_2 = "X{0:.4f} Y{1:.4f}\n".format(bx0, by0)

        target = SimpleNamespace(
            is_segmented_gcode=True, coords_decimals=4, units='MM',
            tools={
                9: {'gcode': gcode_1, 'data': {}},
                3: {'gcode': gcode_2, 'data': {}},
            },
        )

        all_paths = paths_a + paths_b
        bounds = paths_bounds(all_paths)
        xs, ys = make_grid_points(bounds, nx=4, ny=4)
        plane_fn = make_plane(0.0002, -0.00015, 0.02)
        grid_points = [(x, y, plane_fn(x, y)) for x in xs for y in ys]

        with tempfile.TemporaryDirectory() as tmpdir:
            tool = make_tool(units='MM', storage={}, heights_valid=False)
            path = os.path.join(tmpdir, 'hm.txt')
            write_mach3(path, grid_points)
            tool.import_height_map(path)
            self.assertTrue(tool.al_heights_valid)

            result = tool.autolevell_gcode(target, 'b')
            self.assertIsNotNone(result)

            # tool 9's cut line got compensated
            z9 = _line_words(result[9].splitlines()[-1])['Z']
            self.assertAlmostEqual(z9, -0.15 + plane_fn(ax0, ay0), delta=1e-3)

            # tool 3's bare "X.. Y.." line inherited tool 9's modal G1/Z=-0.15
            # and got compensated too - proof the state carried across tools.
            lev_line_3 = result[3].strip()
            self.assertIn('Z', _line_words(lev_line_3))
            z3 = _line_words(lev_line_3)['Z']
            self.assertAlmostEqual(z3, -0.15 + plane_fn(bx0, by0), delta=1e-3)

            # source objects unchanged
            self.assertEqual(target.tools[9]['gcode'], gcode_1)
            self.assertEqual(target.tools[3]['gcode'], gcode_2)


# ---------------------------------------------------------------------------
# Real-object test (reviewer request): run apply_autolevel()'s obj_init
# against a REAL camlib.CNCjob-based object, not a fake, so that attribute
# bugs in obj_init are not hidden.
#
# A real appObjects.CNCJobObject.CNCJobObject is not used here: its
# __init__ (appObjects/CNCJobObject.py) calls build_ui()/build_cnc_tools_table()
# and friends, which need a live app.ui / FlatCAMObj plumbing far beyond what
# a headless unit test should stand up. camlib.CNCjob (the pure geometry/CNC
# base class CNCJobObject also inherits from) is used instead, with a
# minimal app double satisfying only what CNCjob.__init__()/gcode_parse()/
# create_geometry() actually read (see camlib.py Geometry.__init__/CNCjob.__init__).
# ---------------------------------------------------------------------------

def make_real_cncjob_app():
    return SimpleNamespace(
        decimals=4,
        app_units='MM',
        use_3d_engine=True,
        plotcanvas=SimpleNamespace(new_shape_collection=lambda layers=1: None),
        options={
            'cncjob_steps_per_circle': 64,
            'cncjob_coords_type': 'G90',
            'cncjob_bed_max_x': 0,
            'cncjob_bed_max_y': 0,
            'cncjob_bed_offset_x': 0,
            'cncjob_bed_offset_y': 0,
            'cncjob_bed_skew_x': 0,
            'cncjob_bed_skew_y': 0,
        },
        preprocessors={'default': object()},
        inform=MagicMock(),
        log=MagicMock(),
    )


def make_real_cncjob():
    app = make_real_cncjob_app()
    obj = camlib.CNCjob(
        app=app, units='MM', pp_geometry_name='default', pp_excellon_name='default',
        steps_per_circle=64,
    )
    obj.obj_options = {'type': 'Geometry', 'name': 'job1'}
    obj.tools = {}
    obj.multitool = True
    obj.used_tools = []
    obj.gc_start = ''
    obj.prepend_snippet = ''
    obj.append_snippet = ''
    obj.is_segmented_gcode = True
    return obj


class TestApplyAutolevelRealObject(unittest.TestCase):
    def test_obj_init_against_real_cncjob(self):
        paths = gerber_toolpaths(SIMPLE_LINE_GBR)
        gcode_1 = make_gcode(paths, include_arc=False)

        paths_2 = gerber_toolpaths(FLASH_GBR)
        gcode_2 = make_gcode(paths_2, include_arc=False)

        all_paths = paths + paths_2
        bounds = paths_bounds(all_paths)
        xs, ys = make_grid_points(bounds, nx=4, ny=4)
        plane_fn = make_plane(0.0002, -0.0001, 0.02)
        grid_points = [(x, y, plane_fn(x, y)) for x in xs for y in ys]
        storage = {
            i: {'point': Point(x, y), 'geo': None, 'height': z}
            for i, (x, y, z) in enumerate(grid_points)
        }

        target = SimpleNamespace(
            kind='cncjob',
            obj_options={'name': 'job1', 'type': 'Geometry'},
            units='MM',
            multitool=True,
            used_tools=[1, 2],
            gc_start='G21\nG90\n',
            prepend_snippet='',
            append_snippet='',
            is_segmented_gcode=True,
            coords_decimals=4,
            pp_geometry_name='default',
            pp_excellon_name='default',
            pp_solderpaste_name=None,
            exc_tools={},
            tools={
                1: {'gcode': gcode_1, 'gcode_parsed': [], 'data': {}, 'tooldia': 1.0},
                2: {'gcode': gcode_2, 'gcode_parsed': [], 'data': {}, 'tooldia': 1.0},
            },
        )

        tool = make_tool(units='MM', storage=storage, heights_valid=True)
        tool.app.collection.get_by_name.return_value = target
        tool.ui.al_method_radio.get_value.return_value = 'b'

        captured = {}

        def fake_new_object(kind, name, obj_init, *args, **kwargs):
            captured['kind'] = kind
            captured['name'] = name
            new_obj = make_real_cncjob()
            obj_init(new_obj, tool.app)
            captured['new_obj'] = new_obj
            return 'success'

        tool.app.app_obj.new_object.side_effect = fake_new_object

        tool.apply_autolevel()
        worker_dict = tool.app.worker_task.emit.call_args.args[0]

        # No exception must propagate from obj_init() running against the
        # real CNCjob object.
        worker_dict['fcn']()

        self.assertEqual(captured['kind'], 'cncjob')
        new_obj = captured['new_obj']

        # levelled per-tool gcode/gcode_parsed
        self.assertNotEqual(new_obj.tools[1]['gcode'], gcode_1)
        self.assertNotEqual(new_obj.tools[2]['gcode'], gcode_2)
        self.assertTrue(len(new_obj.tools[1]['gcode_parsed']) > 0)
        self.assertTrue(len(new_obj.tools[2]['gcode_parsed']) > 0)
        for entry in new_obj.tools[1]['gcode_parsed']:
            self.assertIn('geom', entry)
            self.assertEqual(entry['geom'].geom_type, 'LineString')

        # object-level gcode_parsed holds both tools' geometry
        self.assertEqual(
            len(new_obj.gcode_parsed),
            len(new_obj.tools[1]['gcode_parsed']) + len(new_obj.tools[2]['gcode_parsed'])
        )

        # create_geometry() produced a non-empty solid_geometry
        self.assertTrue(new_obj.geometry_created if hasattr(new_obj, 'geometry_created') else True)
        self.assertIsNotNone(new_obj.solid_geometry)
        self.assertGreater(len(new_obj.solid_geometry), 0)

        # pp names / exc_tools copied
        self.assertEqual(new_obj.pp_geometry_name, 'default')
        self.assertEqual(new_obj.pp_excellon_name, 'default')
        self.assertIsNone(new_obj.pp_solderpaste_name)
        self.assertEqual(new_obj.exc_tools, {})

        self.assertIn('[success]', tool.app.inform.emit.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
