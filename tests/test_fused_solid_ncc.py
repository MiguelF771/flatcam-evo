"""NccGen: the Gerber normalization (4 places) now goes through fused_solid(); old versus new on the unify fixtures.

TestLegacy pins what the old expressions did (it passes on the code before the change too). TestNew checks the
new helper ``ncc_gerber_copper`` against them.
"""

import pytest
import shapely
from shapely.geometry import MultiPolygon, Polygon

from fused_consumer_cases import (
    area_diff, case_geometries, legacy_ncc_clip, legacy_ncc_iso, legacy_ncc_rest_clip, legacy_ncc_rest_iso, outcome,
)
from test_unify_characterization import (  # noqa: F401  (the fixtures must be visible in this module)
    _headless_qt, app, gerber,
)

CASES = ['mixed_dark_clear_dark', 'overlapping_list', 'self_intersecting', 'single_polygon', 'multipolygon']


def obj_with(gerber, name):
    gerber.solid_geometry = case_geometries(gerber.solid_geometry)[name]
    return gerber


class TestLegacy:
    @pytest.mark.parametrize('name', ['mixed_dark_clear_dark', 'overlapping_list', 'single_polygon', 'multipolygon'])
    def test_valid_inputs_give_a_valid_union(self, gerber, name):
        obj = obj_with(gerber, name)
        for fn in (legacy_ncc_clip, legacy_ncc_iso):
            for off in (False, True):
                kind, geo = outcome(fn, obj, off)
                assert kind == 'ok' and geo.is_valid

    def test_rest_machining_with_buffering_off_fails_on_a_list(self, gerber):
        # old bug: a list has no .buffer; only a single geometry worked
        obj = obj_with(gerber, 'overlapping_list')
        assert outcome(legacy_ncc_rest_clip, obj, True) == ('raises', 'AttributeError')
        assert outcome(legacy_ncc_rest_iso, obj, True) == ('raises', 'AttributeError')

    def test_rest_machining_with_buffering_on_does_not_union_the_iso_branch(self, gerber):
        obj = obj_with(gerber, 'overlapping_list')
        assert legacy_ncc_rest_iso(obj, False) is obj.solid_geometry     # a raw list, overlapping parts

    def test_self_intersecting_union_outcome_is_recorded(self, gerber):
        obj = obj_with(gerber, 'self_intersecting')
        kind, res = outcome(legacy_ncc_iso, obj, False)
    
        assert (kind, res) == ('raises', 'GEOSException')


@pytest.fixture
def new_fn():
    from appPlugins.ToolNCC.NccGen import ncc_gerber_copper
    return ncc_gerber_copper


class TestNew:
    @pytest.mark.parametrize('name', CASES)
    def test_result_is_valid_polygonal(self, gerber, new_fn, name):
        geo = new_fn(obj_with(gerber, name))
        assert isinstance(geo, (Polygon, MultiPolygon)) and geo.is_valid

    @pytest.mark.parametrize('name', ['mixed_dark_clear_dark', 'overlapping_list', 'single_polygon', 'multipolygon'])
    @pytest.mark.parametrize('off', [False, True])
    def test_same_copper_as_old_on_valid_input(self, gerber, new_fn, name, off):
        obj = obj_with(gerber, name)
        tol = 1e-9 if off else None
        assert area_diff(legacy_ncc_clip(obj, off), new_fn(obj, tol)) < 1e-6
        assert area_diff(legacy_ncc_iso(obj, off), new_fn(obj)) < 1e-6
        if not (off and isinstance(obj.solid_geometry, list)):
            assert area_diff(legacy_ncc_rest_clip(obj, off), new_fn(obj)) < 1e-6

    def test_clear_polarity_is_kept(self, gerber, new_fn):
        # the composed geometry already has the clear area removed; the union of it must not fill it again
        geo = new_fn(gerber)
        assert area_diff(geo, gerber.solid_geometry) < 1e-6

    def test_repair_buffer_is_kept_after_the_fuse(self, gerber, new_fn):
        obj = obj_with(gerber, 'single_polygon')
        assert new_fn(obj, 1e-9).area > new_fn(obj).area
        assert new_fn(obj, 1e-9).area == pytest.approx(legacy_ncc_clip(obj, True).area)

    def test_self_intersecting_is_repaired(self, gerber, new_fn):
        obj = obj_with(gerber, 'self_intersecting')
        geo = new_fn(obj)
        assert geo.is_valid
        # both lobes of the bowtie survive (buffer(0) would keep only one of them)
        assert geo.area == pytest.approx(shapely.make_valid(Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])).area + 4.0)

    def test_does_not_mutate_the_object(self, gerber, new_fn):
        obj = obj_with(gerber, 'overlapping_list')
        before = list(obj.solid_geometry)
        new_fn(obj)
        assert obj.solid_geometry == before
