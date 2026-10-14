"""Excellon slots are tuples (start, end) of Points; after a project save and load they must still be tuples."""
from shapely.geometry import Point

from appObjects.ExcellonObject import ExcellonObject
from test_unify_characterization import (  # noqa: F401  (fixtures)
    _headless_qt, app, make_excellon, save_and_load, slot_flat)


def _tuple_slots(obj):
    out = {}
    for key, tool in obj.tools.items():
        if 'slots' in tool:
            out[key] = tool['slots']
    return out


def test_slots_are_tuples_after_project_round_trip(app, tmp_path):
    exc = make_excellon(app, name='exc')
    before = _tuple_slots(exc)
    assert sum(len(s) for s in before.values()) > 0

    _, restored = save_and_load(app, [exc], tmp_path)
    after = _tuple_slots(restored[0])

    assert sorted(after) == sorted(before)
    for key, slots in after.items():
        assert len(slots) == len(before[key])
        for slot in slots:
            assert type(slot) is tuple and len(slot) == 2
            assert all(isinstance(p, Point) for p in slot)
        assert slot_flat(slots) == slot_flat(before[key]) or all(
            abs(a - b) < 2e-6 for x, y in zip(slot_flat(slots), slot_flat(before[key])) for a, b in zip(x, y))


def test_from_dict_converts_list_slots_and_leaves_tools_without_slots(app):
    exc = ExcellonObject('e', app)
    data = {'tools': {
        1: {'tooldia': 1.0, 'drills': [Point(1, 1)], 'data': {}},
        2: {'tooldia': 2.0, 'drills': [], 'slots': [[Point(0, 0), Point(3, 0)], (Point(5, 5), Point(6, 6))]},
    }}
    exc.from_dict(data)

    assert 'slots' not in exc.tools[1]
    assert exc.tools[1]['drills'] == [Point(1, 1)]
    assert exc.tools[2]['slots'] == [(Point(0, 0), Point(3, 0)), (Point(5, 5), Point(6, 6))]
    assert all(type(s) is tuple for s in exc.tools[2]['slots'])
