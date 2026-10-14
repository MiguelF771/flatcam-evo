"""PaintGen: the progressive-plotting buffer(0) of the Gerber geometry (3 places) now goes through fused_solid().

TestLegacy pins the old expressions (it passes on the code before the change too); TestNew checks
``gerber_copper_for_paint`` against them.
"""

import pytest
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from fused_consumer_cases import area_diff, case_geometries, outcome
from test_unify_characterization import (  # noqa: F401  (the fixtures must be visible in this module)
    _headless_qt, app, gerber,
)


def legacy_polygon_and_all(obj):
    """Paint 'normal' and 'all' workers, tools_paint_plotting == 'progressive' (the result was stored on the object)."""
    if isinstance(obj.solid_geometry, list):
        return MultiPolygon(obj.solid_geometry).buffer(0)
    return obj.solid_geometry.buffer(0)


def legacy_area(obj):
    """Paint 'area' worker, progressive."""
    return unary_union(obj.solid_geometry).buffer(0)


def obj_with(gerber, name):
    gerber.solid_geometry = case_geometries(gerber.solid_geometry)[name]
    return gerber


# 'multipolygon' and 'single_polygon' only: MultiPolygon(list_of_overlapping_polygons) is itself invalid input
VALID = ['mixed_dark_clear_dark', 'single_polygon', 'multipolygon']


class TestLegacy:
    @pytest.mark.parametrize('name', VALID)
    def test_valid_inputs(self, gerber, name):
        obj = obj_with(gerber, name)
        for fn in (legacy_polygon_and_all, legacy_area):
            kind, geo = outcome(fn, obj)
            assert kind == 'ok' and geo.is_valid

    def test_area_branch_on_an_overlapping_list(self, gerber):
        kind, geo = outcome(legacy_area, obj_with(gerber, 'overlapping_list'))
        assert kind == 'ok' and geo.is_valid

    def test_polygon_branch_on_an_overlapping_list_builds_a_multipolygon_of_overlapping_parts(self, gerber):
        # the old branch relies on buffer(0) to dissolve the overlap of the list members
        kind, geo = outcome(legacy_polygon_and_all, obj_with(gerber, 'overlapping_list'))
        assert kind == 'ok' and geo.is_valid

    def test_self_intersecting_outcome(self, gerber):
        obj = obj_with(gerber, 'self_intersecting')
        # area branch: unary_union of the invalid polygon raises before buffer(0) is reached
        assert outcome(legacy_area, obj) == ('raises', 'GEOSException')
        # polygon branch: buffer(0) of the MultiPolygon repairs it, but a bowtie loses one lobe
        kind, geo = outcome(legacy_polygon_and_all, obj)
        assert kind == 'ok' and geo.area == pytest.approx(8.0)      # one lobe of the bowtie (4) + the square (4)


@pytest.fixture
def new_fn():
    from appPlugins.ToolPaint.PaintGen import gerber_copper_for_paint
    return gerber_copper_for_paint


class TestNew:
    @pytest.mark.parametrize('name', VALID + ['overlapping_list', 'self_intersecting'])
    def test_valid_polygonal(self, gerber, new_fn, name):
        geo = new_fn(obj_with(gerber, name))
        assert isinstance(geo, (Polygon, MultiPolygon)) and geo.is_valid

    @pytest.mark.parametrize('name', VALID)
    def test_same_copper_as_old(self, gerber, new_fn, name):
        obj = obj_with(gerber, name)
        assert area_diff(legacy_polygon_and_all(obj), new_fn(obj)) < 1e-6
        assert area_diff(legacy_area(obj), new_fn(obj)) < 1e-6

    def test_overlapping_list_matches_the_area_branch(self, gerber, new_fn):
        obj = obj_with(gerber, 'overlapping_list')
        assert area_diff(legacy_area(obj), new_fn(obj)) < 1e-6
        assert area_diff(legacy_polygon_and_all(obj), new_fn(obj)) < 1e-6

    def test_self_intersecting_no_longer_raises_and_keeps_both_lobes(self, gerber, new_fn):
        geo = new_fn(obj_with(gerber, 'self_intersecting'))
        assert geo.area == pytest.approx(8.0 + 4.0)      # two bowtie triangles (4 each) + the 2 x 2 square

    def test_does_not_mutate_the_object(self, gerber, new_fn):
        obj = obj_with(gerber, 'overlapping_list')
        new_fn(obj)
        assert isinstance(obj.solid_geometry, list)
