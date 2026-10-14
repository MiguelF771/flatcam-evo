"""Characterization tests for the Gerber / Excellon unification work (Stage 0A).

These tests document the CURRENT behavior of GerberObject / ExcellonObject (parse, transforms, copy, merge,
project save / load, file export / import). They must pass before and after the refactor stages, unless a stage
changes a documented behavior on purpose. The one class named ``TestRegression...`` holds a strict xfail: it
specifies the corrected behavior and its owning stage removes the marker.

Behavior that Stage 0B changes is NOT encoded here (Excellon scale/convert_units tooldia, Excellon rotate
with point=None, buffer factor handling, Gerber nVertices after scale/buffer, GerberObject.merge, Excellon
create_geometry data, TclCommandScale, double __init__ effects). See tests/test_unify_regressions_0b.py.

Everything runs headless: real GerberObject / ExcellonObject instances are built with a SimpleNamespace app
and the VisPy ShapeCollection patched out.
"""

import json
import sys
import os
import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, patch

import numpy as np
import pytest
import shapely
from shapely import affinity
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPoint, MultiPolygon, \
    Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets  # noqa: E402

from appHandlers.appIO import appIO  # noqa: E402
from appHandlers.appObjectOps import AppObjectOps  # noqa: E402
from appObjects.ExcellonObject import ExcellonObject  # noqa: E402
from appObjects.GerberObject import GerberObject  # noqa: E402
from camlib import dict2obj  # noqa: E402
from defaults import AppDefaults  # noqa: E402

FIXTURES = Path(__file__).parent / 'fixtures' / 'unify'
MIXED_GBR = str(FIXTURES / 'unify_mixed.gbr')
NATIVE_GBR = str(FIXTURES / 'unify_native.gbr')
MIXED_DRL = str(FIXTURES / 'unify_mixed.drl')

TRANSFORM_NAMES = ['offset', 'mirror_x', 'mirror_y', 'rotate', 'skew', 'scale']


# ---------------------------------------------------------------------------------------------------------------
# headless object factory
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope='module', autouse=True)
def _headless_qt():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        yield qapp


def make_app():
    defaults = AppDefaults()
    options = dict(defaults.defaults)
    options.update({
        'gerber_exp_units': 'MM', 'gerber_exp_integer': 4, 'gerber_exp_decimals': 6, 'gerber_exp_zeros': 'L',
        'excellon_exp_units': 'METRIC', 'excellon_exp_integer': 3, 'excellon_exp_decimals': 3,
        'excellon_exp_format': 'dec', 'excellon_exp_slot_type': 'routing', 'excellon_exp_zeros': 'LZ',
        'global_save_compressed': False,
    })
    app = SimpleNamespace(
        log=Mock(), inform=Mock(), splash=None, worker_task=Mock(), options=options, app_units='MM',
        abort_flag=False, app_obj=Mock(), plot_all=Mock(), defaults=defaults, decimals=4, use_3d_engine=True,
        version='test', version_date='test', proc_container=MagicMock(), plotcanvas=Mock(), pool=Mock(),
        collection=Mock(), new_project_signal=Mock(), file_opened=Mock(), file_saved=Mock(), call_source='app', ui=MagicMock(),
        should_we_save=False, block_autosave=False, save_in_progress=False, start_delayed_quit=Mock(),
    )
    return app


def make_gerber(app, path=MIXED_GBR, name='grb'):
    obj = GerberObject(name, app)
    assert obj.parse_file(path) is None
    return obj


def make_excellon(app, path=MIXED_DRL, name='exc'):
    obj = ExcellonObject(name, app)
    assert obj.parse_file(path) is None
    assert obj.create_geometry() is None
    return obj


def make_same_diameter_excellon(app, name='same_dia'):
    """2 tools with the same diameter and different data (cannot live in a file)."""
    obj = ExcellonObject(name, app)
    obj.units = 'MM'
    obj.tools = {
        1: {'tooldia': 1.0, 'drills': [Point(5, 5), Point(9, 9)], 'slots': [], 'solid_geometry': [],
            'data': {}},
        2: {'tooldia': 1.0, 'drills': [Point(15, 5)], 'slots': [(Point(20, 5), Point(26, 5))],
            'solid_geometry': [], 'data': {}},
    }
    assert obj.create_geometry() is None
    # create_geometry() overwrites 'data', so set the distinct data after it
    obj.tools[1]['data'] = {'cutz': -1.7, 'multidepth': True, 'depthperpass': 0.4, 'tag': 'first'}
    obj.tools[2]['data'] = {'cutz': -2.4, 'multidepth': False, 'depthperpass': 0.8, 'tag': 'second'}
    return obj


@pytest.fixture
def app():
    return make_app()


@pytest.fixture
def gerber(app):
    return make_gerber(app)


@pytest.fixture
def excellon(app):
    return make_excellon(app)


# ---------------------------------------------------------------------------------------------------------------
# geometry helpers
# ---------------------------------------------------------------------------------------------------------------

def parts(geo):
    """Flatten lists / Multi* / GeometryCollection into a list of simple shapely geometries (order kept)."""
    out = []
    if geo is None:
        return out
    if isinstance(geo, (list, tuple)):
        for g in geo:
            out.extend(parts(g))
    elif isinstance(geo, (MultiPolygon, MultiLineString, MultiPoint, GeometryCollection)):
        for g in geo.geoms:
            out.extend(parts(g))
    elif isinstance(geo, BaseGeometry):
        out.append(geo)
    return out


def polygon_union(geo):
    polys = [shapely.make_valid(p) for p in parts(geo) if isinstance(p, Polygon) and not p.is_empty]
    return unary_union(polys) if polys else Polygon()


def sym_diff_area(a, b):
    return polygon_union(a).symmetric_difference(polygon_union(b)).area


def assert_poly_equiv(actual, expected, rel=1e-6, abs_tol=1e-6, msg=''):
    exp = polygon_union(expected)
    diff = polygon_union(actual).symmetric_difference(exp).area
    assert diff <= max(abs_tol, rel * exp.area), '%s symmetric difference %.9f (area %.6f)' % (msg, diff, exp.area)


def coords_of(geo):
    """List of coordinate arrays, one per simple geometry (order kept)."""
    return [shapely.get_coordinates(g) for g in parts(geo)]


def assert_coords_close(actual, expected, atol=1e-9, msg=''):
    a = coords_of(actual)
    e = coords_of(expected)
    assert len(a) == len(e), '%s part count %d != %d' % (msg, len(a), len(e))
    for i, (ca, ce) in enumerate(zip(a, e)):
        assert ca.shape == ce.shape, '%s part %d shape %s != %s' % (msg, i, ca.shape, ce.shape)
        assert np.allclose(ca, ce, atol=atol), '%s part %d coords differ' % (msg, i)


def pts(items):
    """Coordinate tuples of a list of Points."""
    return [(p.x, p.y) for p in items]


def slot_coords(slots):
    return [((s[0].x, s[0].y), (s[1].x, s[1].y)) for s in slots]


def slot_flat(slots):
    """Slots as flat (x1, y1, x2, y2) tuples, usable with pytest.approx."""
    return [(s[0].x, s[0].y, s[1].x, s[1].y) for s in slots]


# transform table: name -> (apply to object, shapely function applied to one geometry)
ORIGIN = (6.0, 4.0)
TRANSFORMS = {
    'offset': (lambda o: o.offset((3.5, -2.25)), lambda g: affinity.translate(g, 3.5, -2.25)),
    'mirror_x': (lambda o: o.mirror('X', (12.0, 7.0)), lambda g: affinity.scale(g, 1.0, -1.0, origin=(12.0, 7.0))),
    'mirror_y': (lambda o: o.mirror('Y', (12.0, 7.0)), lambda g: affinity.scale(g, -1.0, 1.0, origin=(12.0, 7.0))),
    'rotate': (lambda o: o.rotate(30.0, (10.0, 8.0)), lambda g: affinity.rotate(g, 30.0, origin=(10.0, 8.0))),
    'skew': (lambda o: o.skew(10.0, 5.0, (4.0, 3.0)), lambda g: affinity.skew(g, 10.0, 5.0, origin=(4.0, 3.0))),
    'scale': (lambda o: o.scale(1.5, 1.5, ORIGIN), lambda g: affinity.scale(g, 1.5, 1.5, origin=ORIGIN)),
}


def apply_to(fn, geo):
    """Apply a shapely function to every simple part, returning a flat list."""
    return [fn(g) for g in parts(geo)]


# ---------------------------------------------------------------------------------------------------------------
# Gerber: parse
# ---------------------------------------------------------------------------------------------------------------

class TestGerberParse:
    def test_header(self, gerber):
        assert gerber.units == 'MM'
        assert gerber.int_digits == 4
        assert gerber.frac_digits == 6
        assert set(gerber.aperture_macros) == {'OUTL', 'RING'}

    def test_aperture_keys_and_record_counts(self, gerber):
        counts = {ap: len(t['geometry']) for ap, t in gerber.tools.items()}
        # 10: flash + track + clear track; 0 is the region 'aperture'
        assert counts == {10: 3, 11: 1, 12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1, 0: 1}

    def test_record_kinds_per_aperture(self, gerber):
        kinds = {}
        for ap, t in gerber.tools.items():
            kinds[ap] = [tuple(sorted((k, v.geom_type) for k, v in rec.items())) for rec in t['geometry']]
        assert kinds[10] == [
            (('follow', 'Point'), ('solid', 'Polygon')),
            (('follow', 'LineString'), ('solid', 'Polygon')),
            (('clear', 'Polygon'), ('follow', 'LineString')),
        ]
        assert kinds[16] == [(('follow', 'LineString'), ('solid', 'Polygon'))]
        assert kinds[0] == [(('follow', 'LinearRing'), ('solid', 'Polygon'))]
        for ap in (11, 12, 13, 14, 15, 17):
            assert kinds[ap] == [(('follow', 'Point'), ('solid', 'Polygon'))]

    def test_aperture_metadata(self, gerber):
        t = gerber.tools
        assert (t[10]['type'], t[10]['size']) == ('C', 1.0)
        assert (t[16]['type'], t[16]['size']) == ('C', 6.0)
        assert (t[17]['type'], t[17]['size']) == ('C', 2.0)
        for ap, kind in ((11, 'R'), (12, 'O')):
            assert t[ap]['type'] == kind
            assert t[ap]['width'] == 2.0 and t[ap]['height'] == 1.0
            assert t[ap]['size'] == pytest.approx(np.hypot(2.0, 1.0))
        assert t[13]['type'] == 'P'
        assert t[13]['diam'] == 2.0
        assert t[13]['nVertices'] == 5
        assert t[13]['rotation'] == 30.0
        assert t[13]['size'] == 2.0
        assert t[14]['type'] == 'AM' and t[14]['macro'].name == 'OUTL' and t[14]['modifiers'] == []
        assert t[15]['type'] == 'AM' and t[15]['macro'].name == 'RING' and t[15]['modifiers'] == ['2.0', '1.0']
        assert 'size' not in t[14] and 'size' not in t[15]
        assert t[0]['type'] == 'REG' and t[0]['size'] == 0.0

    def test_flash_footprints(self, gerber):
        t = gerber.tools
        assert t[10]['geometry'][0]['follow'].equals(Point(5, 5))
        assert t[11]['geometry'][0]['solid'].area == pytest.approx(2.0)
        assert t[11]['geometry'][0]['solid'].bounds == pytest.approx((9.0, 4.5, 11.0, 5.5))
        assert t[12]['geometry'][0]['solid'].bounds == pytest.approx((14.0, 4.5, 16.0, 5.5))
        pentagon = t[13]['geometry'][0]['solid']
        assert len(pentagon.exterior.coords) - 1 == 5
        assert pentagon.area == pytest.approx(2.37764, rel=1e-4)   # regular pentagon, circumradius 1
        assert t[14]['geometry'][0]['solid'].area == pytest.approx(2.0)   # 2 x 1 outline macro
        assert t[15]['geometry'][0]['solid'].area == pytest.approx(np.pi * 0.75, rel=2e-3)   # ring 2.0 / 1.0

    def test_follow_geometry(self, gerber):
        follow = gerber.follow_geometry
        assert [f.geom_type for f in follow] == ['Point'] * 6 + ['LineString', 'LinearRing', 'LineString',
                                                                'LineString', 'Point']
        assert list(follow[6].coords) == [(0.0, 10.0), (30.0, 10.0), (30.0, 15.0)]

    def test_composed_solid_geometry_applies_polarity(self, gerber):
        union = polygon_union(gerber.solid_geometry)
        assert union.area == pytest.approx(271.2704, rel=1e-4)
        # dark -> clear -> dark: the clear track cuts the wide dark track, the later flash fills part of the cut
        assert union.contains(Point(5, 35))
        assert union.contains(Point(24, 35))
        assert union.contains(Point(15, 35))            # covered by the later dark flash
        assert not union.contains(Point(15, 33))        # inside the cut, outside the later flash
        assert not union.contains(Point(15, 37.2))      # inside the cut, outside the later flash
        assert union.contains(Point(5, 24))             # region

    def test_ring_macro_centre_is_a_hole(self, gerber):
        union = polygon_union(gerber.solid_geometry)
        assert union.contains(Point(30.75, 5.0))
        assert not union.contains(Point(30.0, 5.0))

    def test_bounds(self, gerber):
        assert gerber.bounds() == pytest.approx((-0.5002501250625312, 4.0, 31.0, 38.001500750375186))


# ---------------------------------------------------------------------------------------------------------------
# Excellon: parse
# ---------------------------------------------------------------------------------------------------------------

class TestExcellonParse:
    def test_units_and_tools(self, excellon):
        assert excellon.units == 'MM'
        assert sorted(excellon.tools) == [1, 2, 3]
        assert [excellon.tools[t]['tooldia'] for t in (1, 2, 3)] == [0.8, 1.0, 1.5]

    def test_feature_counts_per_tool(self, excellon):
        counts = {t: (len(d['drills']), len(d['slots'])) for t, d in excellon.tools.items()}
        assert counts == {1: (3, 0), 2: (2, 1), 3: (1, 2)}

    def test_coordinates(self, excellon):
        tools = excellon.tools
        assert pts(tools[1]['drills']) == [(5, 5), (10, 5), (15, 5)]
        assert pts(tools[2]['drills']) == [(5, 12), (10, 12)]
        assert slot_coords(tools[2]['slots']) == [((20, 12), (28, 12))]
        assert pts(tools[3]['drills']) == [(25, 22)]
        assert slot_coords(tools[3]['slots']) == [((5, 20), (5, 26)), ((12, 20), (18, 20))]

    def test_solid_geometry_per_tool(self, excellon):
        for t, d in excellon.tools.items():
            assert len(d['solid_geometry']) == len(d['drills']) + len(d['slots'])
        assert len(excellon.solid_geometry) == 9
        # a 6 mm long routed slot of 1.5 mm: area of a stadium
        slot = excellon.tools[3]['solid_geometry'][-2]
        assert slot.bounds[1] == pytest.approx(20 - 0.75, abs=0.01)
        assert slot.bounds[3] == pytest.approx(26 + 0.75, abs=0.01)

    def test_data_is_empty_dict_with_empty_default_data(self, excellon):
        assert all(d['data'] == {} for d in excellon.tools.values())


# ---------------------------------------------------------------------------------------------------------------
# Gerber: transforms
# ---------------------------------------------------------------------------------------------------------------

def snapshot_gerber(obj):
    return {
        'solid': copy.deepcopy(obj.solid_geometry),
        'follow': copy.deepcopy(obj.follow_geometry),
        'tools': copy.deepcopy(obj.tools),
    }


@pytest.mark.parametrize('name', TRANSFORM_NAMES)
class TestGerberTransforms:
    def test_composed_solid_geometry_matches_affine_copy(self, app, name):
        obj = make_gerber(app)
        before = snapshot_gerber(obj)
        TRANSFORMS[name][0](obj)
        expected = apply_to(TRANSFORMS[name][1], polygon_union(before['solid']))
        assert_poly_equiv(obj.solid_geometry, expected, rel=1e-6, msg=name)

    def test_follow_geometry_coordinates(self, app, name):
        obj = make_gerber(app)
        before = snapshot_gerber(obj)
        TRANSFORMS[name][0](obj)
        assert_coords_close(obj.follow_geometry, apply_to(TRANSFORMS[name][1], before['follow']), msg=name)

    def test_records_keep_aperture_and_counts_and_transform(self, app, name):
        obj = make_gerber(app)
        before = snapshot_gerber(obj)
        TRANSFORMS[name][0](obj)
        assert sorted(obj.tools) == sorted(before['tools'])
        for ap, tool in before['tools'].items():
            now = obj.tools[ap]['geometry']
            assert len(now) == len(tool['geometry']), (name, ap)
            assert obj.tools[ap]['type'] == tool['type']
            for rec_before, rec_now in zip(tool['geometry'], now):
                assert set(rec_before) == set(rec_now), (name, ap)
                for key in rec_before:
                    assert_coords_close(rec_now[key], apply_to(TRANSFORMS[name][1], rec_before[key]),
                                        msg='%s %s %s' % (name, ap, key))

    def test_follow_points_stay_at_flash_centres(self, app, name):
        obj = make_gerber(app)
        TRANSFORMS[name][0](obj)
        for ap in (11, 12, 13, 14, 15):
            solid = obj.tools[ap]['geometry'][0]['solid']
            follow = obj.tools[ap]['geometry'][0]['follow']
            assert isinstance(follow, Point)
            assert solid.buffer(1e-6).contains(follow) or ap == 15   # the ring macro has a hole at its centre


def test_gerber_offset_leaves_all_metadata_unchanged(app):
    obj = make_gerber(app)
    before = {ap: {k: v for k, v in t.items() if k != 'geometry'} for ap, t in copy.deepcopy(obj.tools).items()}
    obj.offset((3.5, -2.25))
    after = {ap: {k: v for k, v in t.items() if k != 'geometry'} for ap, t in obj.tools.items()}
    assert set(before) == set(after)
    for ap in before:
        for key, value in before[ap].items():
            if key == 'macro':
                assert after[ap][key].name == value.name and after[ap][key].raw == value.raw
            else:
                assert after[ap][key] == value, (ap, key)


def test_gerber_uniform_positive_scale_metadata(app):
    obj = make_gerber(app)
    obj.scale(1.5, 1.5, (6.0, 4.0))
    t = obj.tools
    assert t[10]['size'] == pytest.approx(1.5)
    assert t[16]['size'] == pytest.approx(9.0)
    for ap in (11, 12):
        assert t[ap]['width'] == pytest.approx(3.0)
        assert t[ap]['height'] == pytest.approx(1.5)
    assert t[13]['diam'] == pytest.approx(3.0)
    assert t[13]['rotation'] == 30.0
    assert t[14]['type'] == 'AM' and t[15]['type'] == 'AM'
    assert t[14]['modifiers'] == [] and t[15]['modifiers'] == ['2.0', '1.0']


def test_gerber_scale_defaults_to_uniform_and_origin_zero(app):
    obj = make_gerber(app)
    before = polygon_union(obj.solid_geometry)
    obj.scale(2.0)
    assert_poly_equiv(obj.solid_geometry, affinity.scale(before, 2.0, 2.0, origin=(0, 0)), msg='scale default')


# ---------------------------------------------------------------------------------------------------------------
# Excellon: transforms
# ---------------------------------------------------------------------------------------------------------------

def snapshot_excellon(obj):
    return copy.deepcopy(obj.tools)


@pytest.mark.parametrize('name', TRANSFORM_NAMES)
class TestExcellonTransforms:
    def test_drill_and_slot_coordinates(self, app, name):
        obj = make_excellon(app)
        before = snapshot_excellon(obj)
        TRANSFORMS[name][0](obj)
        fn = TRANSFORMS[name][1]
        assert sorted(obj.tools) == sorted(before)
        for t, tool in before.items():
            now = obj.tools[t]
            assert len(now['drills']) == len(tool['drills'])
            assert len(now['slots']) == len(tool['slots'])
            for p_before, p_now in zip(tool['drills'], now['drills']):
                expected = fn(p_before)
                assert (p_now.x, p_now.y) == pytest.approx((expected.x, expected.y), abs=1e-9), (name, t)
            for s_before, s_now in zip(tool['slots'], now['slots']):
                for end_before, end_now in zip(s_before, s_now):
                    expected = fn(end_before)
                    assert (end_now.x, end_now.y) == pytest.approx((expected.x, expected.y), abs=1e-9), (name, t)

    def test_solid_geometry_is_rebuilt_at_the_transformed_positions(self, app, name):
        obj = make_excellon(app)
        before = snapshot_excellon(obj)
        TRANSFORMS[name][0](obj)
        fn = TRANSFORMS[name][1]
        for t, tool in before.items():
            solids = obj.tools[t]['solid_geometry']
            assert len(solids) == len(tool['drills']) + len(tool['slots'])
            centres = [fn(p) for p in tool['drills']] + \
                      [fn(LineString([s[0], s[1]]).interpolate(0.5, normalized=True)) for s in tool['slots']]
            for solid, centre in zip(solids, centres):
                assert (solid.centroid.x, solid.centroid.y) == pytest.approx((centre.x, centre.y), abs=1e-6), \
                    (name, t)
        assert len(obj.solid_geometry) == 9

    def test_tool_count_and_diameter_where_the_transform_keeps_it(self, app, name):
        obj = make_excellon(app)
        TRANSFORMS[name][0](obj)
        assert sorted(obj.tools) == [1, 2, 3]
        if name != 'scale':   # tooldia under scale is a Stage 0B item
            assert [obj.tools[t]['tooldia'] for t in (1, 2, 3)] == [0.8, 1.0, 1.5]


@pytest.mark.parametrize('name', ['offset', 'mirror_x', 'mirror_y', 'rotate'])
def test_excellon_rigid_transforms_match_affine_copy_of_the_drill_polygons(app, name):
    obj = make_excellon(app)
    before = polygon_union(obj.solid_geometry)
    TRANSFORMS[name][0](obj)
    expected = apply_to(TRANSFORMS[name][1], before)
    # drills are rebuilt as polygons around the transformed centres; the circle approximation is not
    # rotated with the centre, so allow the vertex-phase difference of a 16-sided circle
    assert_poly_equiv(obj.solid_geometry, expected, rel=0.06, msg=name)


def test_excellon_skew_rebuilds_round_holes_at_skewed_centres(app):
    """Characterization: skew moves the hole centres, it does not shear the hole shape."""
    obj = make_excellon(app)
    before = polygon_union(obj.solid_geometry)
    TRANSFORMS['skew'][0](obj)
    sheared = polygon_union(apply_to(TRANSFORMS['skew'][1], before))
    # the rebuilt geometry keeps round holes of the same size: its area is unchanged, the sheared copy differs
    assert abs(polygon_union(obj.solid_geometry).area - before.area) < 1e-6 * before.area + 0.5
    assert abs(sheared.area - before.area) > 0.1   # the sheared copy has another area, the rebuilt holes do not


# ---------------------------------------------------------------------------------------------------------------
# copy and merge: source independence
# ---------------------------------------------------------------------------------------------------------------

def run_copy(app, source, kind):
    """Run AppObjectOps.on_copy_command() headless; returns the new object."""
    created = {}

    def new_object(obj_kind, name, initialize, *args, **kwargs):
        assert obj_kind == kind
        cls = GerberObject if kind == 'gerber' else ExcellonObject
        new_obj = cls(name, app)
        if initialize(new_obj, app) == 'fail':
            return 'fail'
        created[name] = new_obj
        return new_obj

    app.app_obj.new_object.side_effect = new_object
    app.collection.get_selected.return_value = [source]
    AppObjectOps(app).on_copy_command()
    assert list(created) == [source.obj_options['name'] + '_copy']
    return created[source.obj_options['name'] + '_copy']


class TestCopyIndependence:
    def test_gerber_copy_matches_and_is_independent(self, app, gerber):
        before = snapshot_gerber(gerber)
        clone = run_copy(app, gerber, 'gerber')
        assert_poly_equiv(clone.solid_geometry, before['solid'], msg='copy')
        assert_coords_close(clone.follow_geometry, before['follow'])
        assert sorted(clone.tools) == sorted(before['tools'])
        assert {ap: len(t['geometry']) for ap, t in clone.tools.items()} == \
               {ap: len(t['geometry']) for ap, t in before['tools'].items()}
        assert clone.source_file == gerber.source_file
        # mutate the source in several places
        gerber.solid_geometry.append(Point(100, 100).buffer(3))
        gerber.follow_geometry.append(Point(100, 100))
        gerber.tools[10]['size'] = 99.0
        gerber.tools[10]['geometry'][0]['solid'] = Point(100, 100).buffer(3)
        gerber.tools[10]['geometry'].append({'solid': Point(7, 7).buffer(1)})
        assert_poly_equiv(clone.solid_geometry, before['solid'], msg='copy after source mutation')
        assert len(clone.follow_geometry) == len(before['follow'])
        assert clone.tools[10]['size'] == 1.0
        assert len(clone.tools[10]['geometry']) == 3
        assert clone.tools[10]['geometry'][0]['solid'].bounds == pytest.approx(
            before['tools'][10]['geometry'][0]['solid'].bounds)
        # and the other way round
        clone.tools[11]['geometry'][0]['solid'] = Point(0, 0).buffer(1)
        assert gerber.tools[11]['geometry'][0]['solid'].bounds == pytest.approx((9.0, 4.5, 11.0, 5.5))

    def test_excellon_copy_matches_and_is_independent(self, app, excellon):
        before = snapshot_excellon(excellon)
        clone = run_copy(app, excellon, 'excellon')
        assert sorted(clone.tools) == [1, 2, 3]
        for t in before:
            assert pts(clone.tools[t]['drills']) == pts(before[t]['drills'])
            assert slot_coords(clone.tools[t]['slots']) == slot_coords(before[t]['slots'])
            assert clone.tools[t]['tooldia'] == before[t]['tooldia']
        assert len(clone.solid_geometry) == 9
        excellon.tools[1]['drills'].append(Point(99, 99))
        excellon.tools[1]['tooldia'] = 9.0
        excellon.tools[2]['slots'][0] = (Point(0, 0), Point(1, 1))
        assert len(clone.tools[1]['drills']) == 3
        assert clone.tools[1]['tooldia'] == 0.8
        assert slot_coords(clone.tools[2]['slots']) == [((20, 12), (28, 12))]
        clone.tools[3]['drills'].append(Point(1, 1))
        assert len(excellon.tools[3]['drills']) == 1

    def test_excellon_same_diameter_tools_survive_the_copy(self, app):
        source = make_same_diameter_excellon(app)
        clone = run_copy(app, source, 'excellon')
        assert sorted(clone.tools) == [1, 2]
        assert [clone.tools[t]['tooldia'] for t in (1, 2)] == [1.0, 1.0]
        assert len(clone.tools[1]['drills']) == 2 and len(clone.tools[2]['drills']) == 1
        assert slot_coords(clone.tools[2]['slots']) == [((20, 5), (26, 5))]


class TestExcellonMerge:
    def test_merge_without_fusing_keeps_every_tool_and_is_independent(self, app, excellon):
        other = make_excellon(app, name='exc_b')
        other.offset((100.0, 0.0))
        src_a, src_b = snapshot_excellon(excellon), snapshot_excellon(other)
        final = ExcellonObject('combo', app)
        ExcellonObject.merge([excellon, other], final, decimals=4, fuse_tools=False, log=Mock())
        assert sorted(final.tools) == [1, 2, 3, 4, 5, 6]
        assert sum(len(t['drills']) for t in final.tools.values()) == 12
        assert sum(len(t['slots']) for t in final.tools.values()) == 6
        assert len(final.solid_geometry) == 18
        assert pts(final.tools[4]['drills']) == [(105, 5), (110, 5), (115, 5)]
        # sources unchanged
        assert pts(excellon.tools[1]['drills']) == pts(src_a[1]['drills'])
        assert pts(other.tools[1]['drills']) == pts(src_b[1]['drills'])
        # the result owns its data
        final.tools[1]['drills'].append(Point(1, 1))
        final.tools[1]['tooldia'] = 7.0
        assert len(excellon.tools[1]['drills']) == 3 and excellon.tools[1]['tooldia'] == 0.8

    def test_merge_fusing_tools_of_the_same_diameter(self, app, excellon):
        other = make_excellon(app, name='exc_b')
        other.offset((100.0, 0.0))
        final = ExcellonObject('combo', app)
        ExcellonObject.merge([excellon, other], final, decimals=4, fuse_tools=True, log=Mock())
        assert sorted(round(t['tooldia'], 4) for t in final.tools.values()) == [0.8, 1.0, 1.5]
        assert sum(len(t['drills']) for t in final.tools.values()) == 12
        assert sum(len(t['slots']) for t in final.tools.values()) == 6
        assert len(excellon.tools[1]['drills']) == 3
        assert len(other.tools[1]['drills']) == 3


# ---------------------------------------------------------------------------------------------------------------
# project round trip: real save_project / restore_project_objects code, headless
# ---------------------------------------------------------------------------------------------------------------

def save_and_load(app, objects, tmp_path):
    """appIO.save_project() into a JSON file, then the parse step of open_project() and
    appIO.restore_project_objects(). Not covered: restore_project_handler (message boxes), the LZMA branch,
    and AppObject.new_object (replaced by a factory that calls initialize())."""
    io = appIO(app)
    app.collection.get_list.return_value = objects
    app.collection.get_active.return_value = None
    path = str(tmp_path / 'project.FlatPrj')
    io.save_project(path, silent=True)
    assert os.path.getsize(path) > 0

    with open(path, 'r') as f:
        proj = json.load(f, object_hook=dict2obj)

    restored = []

    def new_object(kind, name, initialize, *args, **kwargs):
        new_obj = (GerberObject if kind == 'gerber' else ExcellonObject)(name, app)
        if initialize(new_obj, app) == 'fail':
            return 'fail'
        restored.append(new_obj)
        return new_obj

    app.app_obj.new_object.side_effect = new_object
    app.worker_task.emit.side_effect = lambda task: task['fcn'](*task['params'])
    io.restore_project_objects(proj, path, True, False)
    return proj, restored


def assert_same_tools(old, new, kind):
    assert sorted(old) == sorted(new)
    for key, tool in old.items():
        other = new[key]
        assert set(tool) == set(other), (key, set(tool) ^ set(other))
        for field, value in tool.items():
            if field == 'macro':
                assert other[field].name == value.name and other[field].raw == value.raw
            elif field == 'geometry':
                assert len(value) == len(other[field]), key
                for rec, rec2 in zip(value, other[field]):
                    assert set(rec) == set(rec2)
                    for part in rec:
                        assert_coords_close(rec2[part], rec[part], atol=2e-6, msg='%s %s' % (key, part))
            elif field == 'solid_geometry':
                assert_coords_close(other[field], value, atol=2e-6, msg='%s solid' % key)
            elif field == 'drills':
                assert pts(other[field]) == pytest.approx(pts(value), abs=2e-6)
            elif field == 'slots':
                assert slot_flat(other[field]) == pytest.approx(slot_flat(value), abs=2e-6)
            elif isinstance(value, float):
                assert other[field] == pytest.approx(value), (key, field)
            else:
                assert other[field] == value, (key, field)


class TestProjectRoundTrip:
    def test_gerber_and_excellon_survive_save_and_restore(self, app, tmp_path):
        grb = make_gerber(app, name='grb')
        exc = make_excellon(app, name='exc')
        for t, data in ((1, {'cutz': -1.7, 'tag': 'a'}), (2, {'cutz': -2.0}), (3, {'cutz': -0.5, 'multidepth': True})):
            exc.tools[t]['data'] = data
        proj, restored = save_and_load(app, [grb, exc], tmp_path)
        assert [o['kind'] for o in proj['objs']] == ['gerber', 'excellon']
        assert len(restored) == 2
        grb2, exc2 = restored
        assert (grb2.kind, exc2.kind) == ('gerber', 'excellon')

        # Gerber
        assert grb2.units == grb.units == 'MM'
        assert (grb2.int_digits, grb2.frac_digits) == (4, 6)
        assert grb2.source_file == grb.source_file
        assert grb2.obj_options['name'] == 'grb'
        assert_poly_equiv(grb2.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='gerber project')
        assert_coords_close(grb2.follow_geometry, grb.follow_geometry, atol=2e-6)
        assert set(grb2.aperture_macros) == {'OUTL', 'RING'}
        for name, macro in grb.aperture_macros.items():
            assert (grb2.aperture_macros[name].name, grb2.aperture_macros[name].raw) == (macro.name, macro.raw)
        assert_same_tools(grb.tools, grb2.tools, 'gerber')
        assert all(isinstance(k, int) for k in grb2.tools)

        # Excellon
        assert exc2.units == exc.units == 'MM'
        assert exc2.zeros == exc.zeros
        assert exc2.obj_options['name'] == 'exc'
        assert_same_tools(exc.tools, exc2.tools, 'excellon')
        assert exc2.tools[3]['data'] == {'cutz': -0.5, 'multidepth': True}
        assert exc2.tools[1]['data'] == {'cutz': -1.7, 'tag': 'a'}
        assert_poly_equiv(exc2.solid_geometry, exc.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='excellon project')

    def test_excellon_tools_with_equal_diameter_keep_their_own_data(self, app, tmp_path):
        exc = make_same_diameter_excellon(app)
        _, restored = save_and_load(app, [exc], tmp_path)
        exc2 = restored[0]
        assert sorted(exc2.tools) == [1, 2]
        assert exc2.tools[1]['tooldia'] == exc2.tools[2]['tooldia'] == 1.0
        assert exc2.tools[1]['data'] == {'cutz': -1.7, 'multidepth': True, 'depthperpass': 0.4, 'tag': 'first'}
        assert exc2.tools[2]['data'] == {'cutz': -2.4, 'multidepth': False, 'depthperpass': 0.8, 'tag': 'second'}
        assert pts(exc2.tools[1]['drills']) == [(5, 5), (9, 9)]
        assert pts(exc2.tools[2]['drills']) == [(15, 5)]
        assert slot_coords(exc2.tools[2]['slots']) == [((20, 5), (26, 5))]

    def test_transformed_objects_survive_save_and_restore(self, app, tmp_path):
        grb = make_gerber(app, name='grb')
        exc = make_excellon(app, name='exc')
        grb.rotate(30.0, (10.0, 8.0))
        exc.rotate(30.0, (10.0, 8.0))
        _, restored = save_and_load(app, [grb, exc], tmp_path)
        assert_poly_equiv(restored[0].solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='gerber')
        assert_same_tools(grb.tools, restored[0].tools, 'gerber')
        assert_same_tools(exc.tools, restored[1].tools, 'excellon')


# ---------------------------------------------------------------------------------------------------------------
# file round trip: real appIO.export_gerber / export_excellon (local_use) + parse
# ---------------------------------------------------------------------------------------------------------------

def export_gerber_text(app, obj):
    out = appIO(app).export_gerber(obj.obj_options['name'], None, local_use=obj, use_thread=False)
    assert isinstance(out, str) and out != 'fail'
    return out


def export_excellon_text(app, obj):
    out = appIO(app).export_excellon(obj.obj_options['name'], None, local_use=obj, use_thread=False)
    assert isinstance(out, str) and out != 'fail'
    return out


def reparse_gerber(app, text, tmp_path, name='again'):
    path = tmp_path / (name + '.gbr')
    path.write_text(text)
    obj = GerberObject(name, app)
    assert obj.parse_file(str(path)) is None
    return obj


def reparse_excellon(app, text, tmp_path, name='again'):
    path = tmp_path / (name + '.drl')
    path.write_text(text)
    obj = ExcellonObject(name, app)
    assert obj.parse_file(str(path)) is None
    assert obj.create_geometry() is None
    return obj


class TestFileRoundTrip:
    def test_gerber_native_apertures_round_trip(self, app, tmp_path):
        """C, R, O flashes, tracks and a region: composed geometry and aperture metadata survive."""
        grb = make_gerber(app, NATIVE_GBR)
        again = reparse_gerber(app, export_gerber_text(app, grb), tmp_path)
        assert again.units == 'MM'
        assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='native gerber')
        assert sorted(again.tools) == sorted(grb.tools)
        for ap, tool in grb.tools.items():
            assert again.tools[ap]['type'] == tool['type']
            assert len(again.tools[ap]['geometry']) == len(tool['geometry']), ap
            for key in ('size', 'width', 'height'):
                if key in tool:
                    assert again.tools[ap][key] == pytest.approx(tool[key], abs=1e-6), (ap, key)

    def test_gerber_p_and_macro_flashes_come_back_as_regions(self, app, tmp_path):
        """Characterization of the writer: P and AM are not declared, their footprints are written as
        regions, so the composed geometry is kept but the aperture metadata is not."""
        text = MIXED_GBR_NO_POLARITY
        path = tmp_path / 'pam.gbr'
        path.write_text(text)
        grb = GerberObject('pam', app)
        assert grb.parse_file(str(path)) is None
        assert {t['type'] for t in grb.tools.values()} >= {'P', 'AM', 'C', 'R', 'O'}
        again = reparse_gerber(app, export_gerber_text(app, grb), tmp_path)
        assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='P/AM')
        types = {t['type'] for ap, t in again.tools.items() if ap != 0}
        assert 'P' not in types and 'AM' not in types
        assert len(again.tools[0]['geometry']) >= 2   # the P and AM footprints (plus the region)

    def test_excellon_round_trip(self, app, tmp_path):
        exc = make_excellon(app)
        again = reparse_excellon(app, export_excellon_text(app, exc), tmp_path)
        assert again.units == 'MM'
        assert sorted(again.tools) == [1, 2, 3]
        for t, tool in exc.tools.items():
            assert again.tools[t]['tooldia'] == pytest.approx(tool['tooldia'], abs=1e-9)
            assert pts(again.tools[t]['drills']) == pytest.approx(pts(tool['drills']), abs=1e-9)
            assert slot_flat(again.tools[t]['slots']) == pytest.approx(slot_flat(tool['slots']), abs=1e-9)
        assert_poly_equiv(again.solid_geometry, exc.solid_geometry, rel=1e-6, abs_tol=1e-6, msg='excellon file')

    def test_excellon_transformed_then_exported_round_trips(self, app, tmp_path):
        exc = make_excellon(app)
        exc.offset((3.5, 2.25))
        again = reparse_excellon(app, export_excellon_text(app, exc), tmp_path)
        for t, tool in exc.tools.items():
            assert pts(again.tools[t]['drills']) == pytest.approx(pts(tool['drills']), abs=1e-3)
            assert slot_flat(again.tools[t]['slots']) == pytest.approx(slot_flat(tool['slots']), abs=1e-3)

    def test_excellon_equal_diameter_tools_stay_separate_in_the_file(self, app, tmp_path):
        exc = make_same_diameter_excellon(app)
        again = reparse_excellon(app, export_excellon_text(app, exc), tmp_path)
        assert sorted(again.tools) == [1, 2]
        assert [again.tools[t]['tooldia'] for t in (1, 2)] == [1.0, 1.0]
        assert pts(again.tools[1]['drills']) == [(5, 5), (9, 9)]
        assert pts(again.tools[2]['drills']) == [(15, 5)]
        assert slot_coords(again.tools[2]['slots']) == [((20, 5), (26, 5))]


# The mixed fixture without the polarity sequence: C, R, O, P, macro flashes, tracks and the region.
MIXED_GBR_NO_POLARITY = '''G04 P and macro flashes without the polarity sequence*
%FSLAX46Y46*%
%MOMM*%
%AMOUTL*
4,1,4,0,0,2,0,2,1,0,1,0,0,0*%
%AMRING*
1,1,$1,0,0*
1,0,$2,0,0*%
%ADD10C,1.0*%
%ADD11R,2.0X1.0*%
%ADD12O,2.0X1.0*%
%ADD13P,2.0X5X30*%
%ADD14OUTL*%
%ADD15RING,2.0X1.0*%
G01*
%LPD*%
D10*
X5000000Y5000000D03*
D11*
X10000000Y5000000D03*
D12*
X15000000Y5000000D03*
D13*
X20000000Y5000000D03*
D14*
X25000000Y5000000D03*
D15*
X30000000Y5000000D03*
D10*
X0Y10000000D02*
X30000000Y10000000D01*
X30000000Y15000000D01*
G36*
X0Y20000000D02*
X10000000Y20000000D01*
X10000000Y28000000D01*
X0Y28000000D01*
X0Y20000000D01*
G37*
M02*
'''


class TestRegressionGerberFileRoundTripFixedInStage0C:
    """Regression tests: they specify the corrected behavior and fail today (strict xfail).
    The owning stage (0C) removes the marker."""

    def test_dark_clear_dark_sequence_survives_the_gerber_export(self, app, tmp_path):
        grb = make_gerber(app, MIXED_GBR)
        again = reparse_gerber(app, export_gerber_text(app, grb), tmp_path)
        # the writer groups records per aperture, so the later dark flash and the dark track are written
        # in a different order than they were drawn and the cut lands on different copper
        assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4,
                          msg='composed geometry after export')
