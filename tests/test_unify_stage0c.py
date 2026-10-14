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
