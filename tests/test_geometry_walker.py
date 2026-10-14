"""Tests of camlib.map_geometry, the shared coordinate walker of the Gerber / Excellon transforms."""

import pytest
from shapely import affinity
from shapely.geometry import (GeometryCollection, LineString, MultiLineString, MultiPoint, MultiPolygon, Point,
                              box)

from camlib import map_geometry


def shift(g):
    return affinity.translate(g, 1.0, 2.0)


def test_none_stays_none():
    assert map_geometry(None, shift) is None


def test_none_inside_a_list_stays():
    out = map_geometry([Point(0, 0), None], shift)
    assert out[0].equals(Point(1, 2))
    assert out[1] is None


@pytest.mark.parametrize('geom', [
    Point(0, 0),
    LineString([(0, 0), (1, 1)]),
    box(0, 0, 1, 1),
    MultiPoint([(0, 0), (1, 1)]),
    MultiLineString([[(0, 0), (1, 1)], [(2, 2), (3, 3)]]),
    MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)]),
    GeometryCollection([Point(0, 0), box(0, 0, 1, 1)]),
])
def test_single_geometry_is_one_leaf_and_type_is_kept(geom):
    calls = []

    def fn(g):
        calls.append(g)
        return shift(g)

    out = map_geometry(geom, fn)
    assert len(calls) == 1 and calls[0] is geom
    assert type(out) is type(geom)
    assert out.equals(shift(geom))


def test_list_stays_list_and_nested_lists_keep_shape():
    a, b, c = Point(0, 0), box(0, 0, 1, 1), LineString([(0, 0), (1, 0)])
    out = map_geometry([a, [b, [c]], []], shift)
    assert type(out) is list and len(out) == 3
    assert out[0].equals(shift(a))
    assert type(out[1]) is list and out[1][0].equals(shift(b))
    assert type(out[1][1]) is list and out[1][1][0].equals(shift(c))
    assert out[2] == []


def test_input_is_not_mutated():
    src = [Point(0, 0), [Point(1, 1)]]
    inner = src[1]
    map_geometry(src, shift)
    assert src[1] is inner and src[0].equals(Point(0, 0))


def test_tuple_is_a_leaf_by_default_and_a_container_with_any_iterable():
    pts = (Point(0, 0), Point(1, 1))
    # default: a tuple is a leaf; shift(tuple) raises AttributeError -> returned unchanged
    assert map_geometry(pts, shift) is pts
    out = map_geometry(pts, shift, any_iterable=True)
    assert type(out) is list
    assert out[0].equals(Point(1, 2)) and out[1].equals(Point(2, 3))


def test_any_iterable_keeps_multi_geometry_whole():
    mp = MultiPolygon([box(0, 0, 1, 1), box(2, 2, 3, 3)])
    out = map_geometry([mp], shift, any_iterable=True)
    assert type(out[0]) is MultiPolygon


def test_attribute_error_returns_the_leaf_unchanged():
    def fn(g):
        raise AttributeError('no geometry')

    p = Point(0, 0)
    assert map_geometry(p, fn) is p
    out = map_geometry([p, [p]], fn)
    assert out[0] is p and out[1][0] is p


def test_other_exceptions_propagate():
    def boom(g):
        raise ValueError('boom')

    def bad_type(g):
        raise TypeError('t')

    with pytest.raises(ValueError):
        map_geometry([Point(0, 0)], boom)
    with pytest.raises(TypeError):
        map_geometry(Point(0, 0), bad_type)


def test_on_leaf_runs_once_per_leaf_before_fn():
    order = []

    def fn(g):
        order.append('fn')
        return g

    out = map_geometry([Point(0, 0), [Point(1, 1)], None], fn, on_leaf=lambda g: order.append('leaf'))
    assert order == ['leaf', 'fn'] * 3
    assert len(out) == 3


def test_on_leaf_attribute_error_is_guarded_like_fn():
    def leaf(g):
        raise AttributeError('progress')

    p = Point(0, 0)
    assert map_geometry(p, shift, on_leaf=leaf) is p
