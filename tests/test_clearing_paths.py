"""Run: python tests/test_clearing_paths.py"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_saved_argv = sys.argv
sys.argv = [sys.argv[0]]
try:
    import camlib
    from shapely import LineString, box
finally:
    sys.argv = _saved_argv

STEPS = (4, 16, 32)


def make_geometry():
    geometry = camlib.Geometry.__new__(camlib.Geometry)
    geometry.app = SimpleNamespace(abort_flag=False, log=Mock(), inform_no_echo=Mock(), proc_container=Mock())
    return geometry


class TestCircleQuadSegs(unittest.TestCase):
    def test_circle_quad_segs(self):
        self.assertEqual(camlib.circle_quad_segs(0), 1)
        self.assertEqual(camlib.circle_quad_segs(4), 1)
        self.assertEqual(camlib.circle_quad_segs(17), 5)
        self.assertEqual(camlib.circle_quad_segs(64), 16)


class TestNarrowChannel(unittest.TestCase):
    """
    A channel between 2 copper pads that is a little wider than the tool must get a tool path.
    Else copper stays between the 2 nets.
    """

    def setUp(self):
        self.geometry = make_geometry()
        self.tooldia = 0.2

    def area_and_channel(self, ratio):
        gap = self.tooldia * ratio
        area = box(0, 0, 5, 5).difference(box(0, 2, 2, 3)).difference(box(2 + gap, 2, 5, 3))
        return area, box(2, 2, 2 + gap, 3).buffer(-1e-6)

    def check(self, run):
        for steps in (16, 32):
            for ratio in (1.01, 1.02, 1.04, 1.06):
                with self.subTest(steps=steps, ratio=ratio):
                    area, channel = self.area_and_channel(ratio)
                    result = run(area, steps)
                    paths = list(result.get_objects()) if result is not None else []
                    self.assertTrue(any(path.intersects(channel) for path in paths))

    def test_shrink(self):
        self.check(lambda area, steps: camlib.Geometry.clear_polygon_shrink(
            self.geometry, area, self.tooldia, steps, connect=False))

    def test_seed(self):
        self.check(lambda area, steps: camlib.Geometry.clear_polygon_seed(
            self.geometry, area, self.tooldia, steps, connect=False))

    def test_lines(self):
        self.check(lambda area, steps: camlib.Geometry.clear_polygon_lines(
            self.geometry, area, self.tooldia, steps, connect=False))


class TestFillWithLinesLastLine(unittest.TestCase):
    def test_fill_with_lines_keeps_the_last_line(self):
        # The last line is the 1 closest to the edge of the trace shape. The margin must not clip it away.
        geometry = make_geometry()
        line = LineString([(2, 5), (18, 5)])
        aperture = 4.0
        tooldia = 1.0
        for steps in STEPS:
            with self.subTest(steps=steps):
                quad_segs = camlib.circle_quad_segs(steps)
                paths = list(camlib.Geometry.fill_with_lines(
                    geometry, line, aperture, tooldia, steps, connect=False, contour=False).get_objects())
                shape = line.buffer(aperture / 2, quad_segs=quad_segs)
                margin = shape.buffer(-tooldia / 2, quad_segs=quad_segs)
                ys = [c[1] for path in paths for c in path.coords]
                self.assertAlmostEqual(max(ys), margin.bounds[3], delta=1e-6)
                self.assertAlmostEqual(min(ys), margin.bounds[1], delta=1e-6)


class TestScanLinesCoverTheEdgeStrip(unittest.TestCase):
    def setUp(self):
        self.geometry = make_geometry()
        self.tooldia = 1.0
        self.overlap = 0.15

    def run_lines(self, polygon, steps):
        quad_segs = camlib.circle_quad_segs(steps)
        margin = polygon.buffer(-self.tooldia / 1.99999999, quad_segs=quad_segs)
        paths = list(camlib.Geometry.clear_polygon_lines(
            self.geometry, polygon, self.tooldia, steps, overlap=self.overlap, connect=False,
            contour=False).get_objects())
        return margin, paths

    def check_levels(self, levels, low, high):
        levels = sorted(set(round(v, 9) for v in levels))
        # the first and the last scan line lie on the margin
        self.assertAlmostEqual(levels[0], low, delta=1e-6)
        self.assertAlmostEqual(levels[-1], high, delta=1e-6)
        # no gap between 2 scan lines is bigger than the step-over
        gaps = [b - a for a, b in zip(levels, levels[1:])]
        self.assertLessEqual(max(gaps), self.tooldia * (1 - self.overlap) + 1e-6)

    def test_horizontal_lines(self):
        for steps in STEPS:
            with self.subTest(steps=steps):
                margin, paths = self.run_lines(box(0, 0, 20, 10), steps)
                self.check_levels([c[1] for path in paths for c in path.coords], margin.bounds[1], margin.bounds[3])

    def test_vertical_lines(self):
        for steps in STEPS:
            with self.subTest(steps=steps):
                margin, paths = self.run_lines(box(0, 0, 10, 20), steps)
                self.check_levels([c[0] for path in paths for c in path.coords], margin.bounds[0], margin.bounds[2])


if __name__ == '__main__':
    unittest.main()
