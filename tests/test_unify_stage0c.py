"""Tests of Stage 0C (part 1) of the Gerber / Excellon unification plan: the region export of the composed
Gerber geometry, its routing, and the lifecycle of ``export_as_regions``.

Everything is headless. The helpers come from ``test_unify_characterization``.
"""

import pytest
from shapely.geometry import MultiPolygon, Point, Polygon, box

from appObjects.GerberObject import GerberObject

from test_unify_characterization import (  # noqa: F401  (the fixtures must be visible in this module)
    _headless_qt, app, assert_poly_equiv, export_gerber_text, make_gerber, polygon_union, reparse_gerber,
)


def region_gerber(app, geometry, name='reg'):
    """A Gerber with only a composed solid_geometry, routed through the region export (PCB document import)."""
    obj = GerberObject(name, app)
    obj.units = 'MM'
    obj.solid_geometry = geometry
    obj.tools = {}
    obj.obj_options['pcbdoc_import'] = True
    return obj


def square_with_hole():
    return Polygon(box(0, 0, 10, 10).exterior.coords, [box(3, 3, 7, 7).exterior.coords])


class TestRegionExportKeepsHolesAndIslands:
    def test_polygon_with_a_hole(self, app, tmp_path):
        geometry = square_with_hole()
        again = reparse_gerber(app, export_gerber_text(app, region_gerber(app, [geometry])), tmp_path)
        assert_poly_equiv(again.solid_geometry, geometry, rel=1e-6, abs_tol=1e-4, msg='polygon with hole')
        assert not polygon_union(again.solid_geometry).contains(Point(5, 5))
        assert polygon_union(again.solid_geometry).contains(Point(1, 1))

    def test_island_inside_a_hole(self, app, tmp_path):
        geometry = MultiPolygon([square_with_hole(), box(4, 4, 6, 6)])
        again = reparse_gerber(app, export_gerber_text(app, region_gerber(app, [geometry])), tmp_path)
        assert_poly_equiv(again.solid_geometry, geometry, rel=1e-6, abs_tol=1e-4, msg='island in hole')
        union = polygon_union(again.solid_geometry)
        assert union.contains(Point(5, 5))          # the island
        assert not union.contains(Point(3.5, 5))    # the hole around it
        assert union.contains(Point(1, 5))          # the ring

    def test_island_listed_before_its_host(self, app, tmp_path):
        """The order of the input list must not matter."""
        geometry = [box(4, 4, 6, 6), square_with_hole()]
        again = reparse_gerber(app, export_gerber_text(app, region_gerber(app, geometry)), tmp_path)
        assert_poly_equiv(again.solid_geometry, geometry, rel=1e-6, abs_tol=1e-4, msg='island first')

    def test_nested_two_levels(self, app, tmp_path):
        ring2 = Polygon(box(4.4, 4.4, 5.6, 5.6).exterior.coords, [box(4.8, 4.8, 5.2, 5.2).exterior.coords])
        geometry = [square_with_hole(), box(4, 4, 6, 6).difference(box(4.2, 4.2, 5.8, 5.8)), ring2.buffer(0)]
        again = reparse_gerber(app, export_gerber_text(app, region_gerber(app, geometry)), tmp_path)
        assert_poly_equiv(again.solid_geometry, geometry, rel=1e-6, abs_tol=1e-4, msg='nested')

    def test_two_separate_polygons(self, app, tmp_path):
        geometry = [box(0, 0, 4, 4), box(10, 10, 12, 13)]
        again = reparse_gerber(app, export_gerber_text(app, region_gerber(app, geometry)), tmp_path)
        assert_poly_equiv(again.solid_geometry, geometry, rel=1e-6, abs_tol=1e-4, msg='two polygons')
        assert len(again.tools[0]['geometry']) == 2

    def test_overlapping_polygons_do_not_clear_each_other(self, app, tmp_path):
        geometry = [square_with_hole(), box(2, 2, 5, 5)]   # the 2nd one overlaps the hole of the 1st
        again = reparse_gerber(app, export_gerber_text(app, region_gerber(app, geometry)), tmp_path)
        assert_poly_equiv(again.solid_geometry, geometry, rel=1e-6, abs_tol=1e-4, msg='overlap')

    def test_the_export_does_not_change_the_object(self, app):
        obj = region_gerber(app, [square_with_hole()])
        tools = obj.tools
        export_gerber_text(app, obj)
        assert obj.tools is tools
        assert obj.tools == {}

    def test_region_tools_are_sorted_largest_first(self, app):
        obj = region_gerber(app, [box(4, 4, 6, 6), square_with_hole(), box(20, 20, 21, 21)])
        areas = [Polygon(rec['solid'].exterior).area for rec in obj.region_export_tools()[0]['geometry']]
        assert areas == sorted(areas, reverse=True)
        assert len(areas) == 3

    def test_empty_geometry_gives_no_records(self, app):
        assert region_gerber(app, []).region_export_tools()[0]['geometry'] == []


# ---------------------------------------------------------------------------------------------------------------
# routing: Gerber.needs_region_export() and the export that follows it
# ---------------------------------------------------------------------------------------------------------------

from test_unify_characterization import MIXED_GBR_NO_POLARITY, NATIVE_GBR  # noqa: E402


def make_p_am_gerber(app, tmp_path):
    path = tmp_path / 'pam.gbr'
    path.write_text(MIXED_GBR_NO_POLARITY)
    obj = GerberObject('pam', app)
    assert obj.parse_file(str(path)) is None
    return obj


class TestFlagAttribute:
    def test_new_gerber_starts_with_false(self, app):
        assert GerberObject('new', app).export_as_regions is False

    def test_flag_is_serialized(self, app):
        obj = GerberObject('new', app)
        assert 'export_as_regions' in obj.ser_attrs
        obj.export_as_regions = True
        assert obj.to_dict()['export_as_regions'] is True


class TestNeedsRegionExport:
    def test_native_fixture_is_exported_natively(self, app):
        grb = make_gerber(app, NATIVE_GBR)
        assert grb.needs_region_export() is False

    def test_flag_forces_the_region_export(self, app):
        grb = make_gerber(app, NATIVE_GBR)
        grb.export_as_regions = True
        assert grb.needs_region_export() is True

    def test_pcbdoc_import_forces_the_region_export(self, app):
        grb = make_gerber(app, NATIVE_GBR)
        grb.obj_options['pcbdoc_import'] = True
        assert grb.needs_region_export() is True

    def test_p_and_am_apertures_force_the_region_export(self, app, tmp_path):
        assert make_p_am_gerber(app, tmp_path).needs_region_export() is True

    def test_p_alone_and_am_alone(self, app):
        for kind in ('P', 'AM'):
            grb = make_gerber(app, NATIVE_GBR)
            grb.tools[99] = {'type': kind, 'size': 1.0, 'geometry': [{'solid': box(0, 0, 1, 1)}]}
            assert grb.needs_region_export() is True, kind

    def test_a_clear_record_forces_the_region_export(self, app):
        grb = make_gerber(app)   # the mixed fixture has a dark -> clear -> dark sequence on aperture 10
        assert grb.needs_region_export() is True
        native = make_gerber(app, NATIVE_GBR)
        native.tools[10]['geometry'].append({'clear': box(0, 0, 1, 1)})
        assert native.needs_region_export() is True

    def test_an_empty_clear_record_does_not(self, app):
        grb = make_gerber(app, NATIVE_GBR)
        grb.tools[10]['geometry'].append({'clear': Polygon()})
        assert grb.needs_region_export() is False

    def test_the_region_aperture_alone_is_native(self, app):
        grb = make_gerber(app, NATIVE_GBR)
        assert grb.tools[0]['type'] == 'REG'
        assert grb.needs_region_export() is False

    def test_the_decision_does_not_change_the_object(self, app):
        grb = make_gerber(app)
        tools_before = repr(grb.tools)
        grb.needs_region_export()
        assert repr(grb.tools) == tools_before and grb.export_as_regions is False


class TestExportRouting:
    def test_native_object_keeps_its_apertures(self, app, tmp_path):
        grb = make_gerber(app, NATIVE_GBR)
        text = export_gerber_text(app, grb)
        assert '%ADD10C,' in text and 'D03*' in text
        again = reparse_gerber(app, text, tmp_path)
        assert {t['type'] for t in again.tools.values()} >= {'C', 'R', 'O'}
        assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='native')

    def test_flag_exports_regions_and_keeps_the_composed_geometry(self, app, tmp_path):
        grb = make_gerber(app, NATIVE_GBR)
        grb.export_as_regions = True
        text = export_gerber_text(app, grb)
        assert '%ADD' not in text and 'G36*' in text
        again = reparse_gerber(app, text, tmp_path)
        assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='flag')
        assert set(again.tools) == {0}

    def test_p_and_am_object_is_exported_as_regions(self, app, tmp_path):
        grb = make_p_am_gerber(app, tmp_path)
        again = reparse_gerber(app, export_gerber_text(app, grb), tmp_path)
        assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='P/AM')
        assert set(again.tools) == {0}

    def test_export_changes_neither_records_nor_flag(self, app):
        grb = make_gerber(app)
        grb.export_as_regions = True
        tools_before = repr(grb.tools)
        export_gerber_text(app, grb)
        assert repr(grb.tools) == tools_before
        assert grb.export_as_regions is True

    def test_dark_clear_dark_sequence_is_kept(self, app, tmp_path):
        grb = make_gerber(app)
        again = reparse_gerber(app, export_gerber_text(app, grb), tmp_path)
        assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='sequence')
