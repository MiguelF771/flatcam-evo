"""Stage 4 merge contract: ExcellonObject.merge fuses tools by diameter only if their data is equal.

Rule under test: with ``fuse_tools=True`` two tools fuse when their rounded diameters match AND their ``data``
dicts are equal (the per-object ``'name'`` key is ignored, it differs between source objects). Tools of the same
diameter with different data stay separate, each with its own data, drills and slots. Nothing is lost silently.

Headless: real ExcellonObject instances, ShapeCollection patched out (fixtures come from the characterization
tests).
"""

import copy
from unittest.mock import Mock

import pytest
from shapely.geometry import Point

from appObjects.ExcellonObject import ExcellonObject

from test_unify_characterization import _headless_qt, app  # noqa: F401  (the fixtures must be visible here)

DATA_A = {'cutz': -1.7, 'multidepth': True, 'depthperpass': 0.4, 'tag': 'first'}
DATA_B = {'cutz': -2.4, 'multidepth': False, 'depthperpass': 0.8, 'tag': 'second'}

stage4 = pytest.mark.xfail(strict=True, raises=AssertionError, reason="fixed in Stage 4")


def make_exc(app, name, specs):
    """specs: list of (dia, drill xy list, slot xy-pair list, data dict). Tool ids are 1..n."""
    obj = ExcellonObject(name, app)
    obj.units = 'MM'
    obj.tools = {}
    for idx, (dia, drills, slots, data) in enumerate(specs, start=1):
        obj.tools[idx] = {
            'tooldia': dia,
            'drills': [Point(p) for p in drills],
            'slots': [(Point(a), Point(b)) for a, b in slots],
            'solid_geometry': [],
            'data': dict(data, name=name),  # as in practice, the data holds the per-object 'name'
        }
    assert obj.create_geometry() is None
    return obj


def merge(app, objs, fuse=True):
    final = ExcellonObject('combo', app)
    ExcellonObject.merge(objs, final, decimals=4, fuse_tools=fuse, log=Mock())
    return final


def pts(items):
    return [(p.x, p.y) for p in items]


def slots_xy(slots):
    return [((s[0].x, s[0].y), (s[1].x, s[1].y)) for s in slots]


def strip_name(data):
    return {k: v for k, v in data.items() if k != 'name'}


class TestFuseEqualData:
    def test_same_diameter_equal_data_fuses_into_one_tool(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (1, 0)], [((0, 5), (3, 5))], DATA_A),
                                (0.8, [(9, 9)], [], DATA_B)])
        b = make_exc(app, 'b', [(1.0, [(10, 0)], [((10, 5), (13, 5))], DATA_A)])
        final = merge(app, [a, b])
        assert len(final.tools) == 2
        big = [t for t in final.tools.values() if t['tooldia'] == 1.0]
        assert len(big) == 1
        assert pts(big[0]['drills']) == [(0, 0), (1, 0), (10, 0)]
        assert slots_xy(big[0]['slots']) == [((0, 5), (3, 5)), ((10, 5), (13, 5))]
        assert strip_name(big[0]['data']) == DATA_A
        assert len(big[0]['solid_geometry']) == 5
        small = [t for t in final.tools.values() if t['tooldia'] == 0.8]
        assert len(small) == 1 and strip_name(small[0]['data']) == DATA_B

    def test_two_tools_of_one_object_with_equal_data_fuse(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A), (1.0, [(2, 0)], [((0, 1), (1, 1))], DATA_A)])
        final = merge(app, [a])
        assert list(final.tools) == [1]
        assert pts(final.tools[1]['drills']) == [(0, 0), (2, 0)]
        assert slots_xy(final.tools[1]['slots']) == [((0, 1), (1, 1))]

    def test_diameters_equal_after_rounding_fuse(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.00001, [(5, 0)], [], DATA_A)])
        final = merge(app, [a, b])
        assert len(final.tools) == 1
        assert final.tools[1]['tooldia'] == 1.0
        assert pts(final.tools[1]['drills']) == [(0, 0), (5, 0)]


class TestKeepDifferentData:
    @stage4
    def test_same_diameter_different_data_stays_two_tools(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0), (1, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(10, 0)], [((10, 5), (13, 5))], DATA_B)])
        final = merge(app, [a, b])
        assert len(final.tools) == 2
        first, second = final.tools[1], final.tools[2]
        assert strip_name(first['data']) == DATA_A and strip_name(second['data']) == DATA_B
        assert pts(first['drills']) == [(0, 0), (1, 0)] and first['slots'] == []
        assert pts(second['drills']) == [(10, 0)]
        assert slots_xy(second['slots']) == [((10, 5), (13, 5))]
        assert len(final.solid_geometry) == 4

    @stage4
    def test_same_object_tools_with_different_data_stay_separate(self, app):
        a = make_exc(app, 'a', [(1.0, [(5, 5), (9, 9)], [], DATA_A),
                                (1.0, [(15, 5)], [((20, 5), (26, 5))], DATA_B)])
        final = merge(app, [a])
        assert sorted(final.tools) == [1, 2]
        assert strip_name(final.tools[1]['data']) == DATA_A and strip_name(final.tools[2]['data']) == DATA_B
        assert pts(final.tools[1]['drills']) == [(5, 5), (9, 9)]
        assert slots_xy(final.tools[2]['slots']) == [((20, 5), (26, 5))]

    @stage4
    def test_mixed_groups_fuse_per_data(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A), (1.0, [(1, 0)], [], DATA_B)])
        b = make_exc(app, 'b', [(1.0, [(2, 0)], [], DATA_A), (1.0, [(3, 0)], [], DATA_B)])
        final = merge(app, [a, b])
        assert len(final.tools) == 2
        by_tag = {t['data']['tag']: t for t in final.tools.values()}
        assert pts(by_tag['first']['drills']) == [(0, 0), (2, 0)]
        assert pts(by_tag['second']['drills']) == [(1, 0), (3, 0)]

    @stage4
    def test_tool_ids_are_contiguous_in_order_of_first_appearance(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(1, 0)], [], DATA_A)])
        c = make_exc(app, 'c', [(0.8, [(2, 0)], [], DATA_A)])
        final = merge(app, [a, b, c])
        assert sorted(final.tools) == [1, 2]
        assert [final.tools[t]['tooldia'] for t in (1, 2)] == [1.0, 0.8]
        assert pts(final.tools[1]['drills']) == [(0, 0), (1, 0)]


class TestIndependenceAndTypes:
    @pytest.mark.parametrize('fuse', [True, False])
    def test_result_does_not_share_data_with_the_sources(self, app, fuse):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [((0, 1), (1, 1))], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(5, 0)], [], DATA_A)])
        snap_a, snap_b = copy.deepcopy(a.tools), copy.deepcopy(b.tools)
        final = merge(app, [a, b], fuse=fuse)
        before = copy.deepcopy(final.tools)

        # change the sources after the merge: the result stays
        a.tools[1]['drills'].append(Point(99, 99))
        a.tools[1]['slots'].append((Point(8, 8), Point(9, 9)))
        a.tools[1]['data']['cutz'] = 5.0
        b.tools[1]['drills'][0] = Point(77, 77)
        b.tools[1]['data']['tag'] = 'changed'
        assert pts(sum((t['drills'] for t in final.tools.values()), [])) == \
            pts(sum((t['drills'] for t in before.values()), []))
        assert all(t['data'].get('cutz') == -1.7 and t['data'].get('tag') == 'first' for t in final.tools.values())

        # change the result: the sources stay (compare against the snapshot taken before the merge)
        a2 = make_exc(app, 'a', [(1.0, [(0, 0)], [((0, 1), (1, 1))], DATA_A)])
        final2 = merge(app, [a2, make_exc(app, 'b', [(1.0, [(5, 0)], [], DATA_A)])], fuse=fuse)
        final2.tools[1]['drills'].append(Point(1, 1))
        final2.tools[1]['data']['cutz'] = 9.9
        assert pts(a2.tools[1]['drills']) == [(0, 0)]
        assert a2.tools[1]['data']['cutz'] == -1.7
        assert pts(snap_a[1]['drills']) == [(0, 0)] and pts(snap_b[1]['drills']) == [(5, 0)]

    @pytest.mark.parametrize('fuse', [True, False])
    def test_slots_stay_tuples_of_two_points(self, app, fuse):
        a = make_exc(app, 'a', [(1.0, [], [((0, 1), (1, 1))], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [], [((5, 1), (6, 1))], DATA_A)])
        final = merge(app, [a, b], fuse=fuse)
        slots = [s for t in final.tools.values() for s in t['slots']]
        assert len(slots) == 2
        for slot in slots:
            assert type(slot) is tuple and len(slot) == 2
            assert all(isinstance(p, Point) for p in slot)

    def test_unfused_merge_keeps_ids_and_all_tools(self, app):
        a = make_exc(app, 'a', [(1.0, [(0, 0)], [], DATA_A), (1.0, [(1, 0)], [], DATA_A)])
        b = make_exc(app, 'b', [(1.0, [(2, 0)], [], DATA_A)])
        final = merge(app, [a, b], fuse=False)
        assert sorted(final.tools) == [1, 2, 3]
