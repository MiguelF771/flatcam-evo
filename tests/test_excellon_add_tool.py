"""Excellon.add_tool: the contract, and the sites that build Excellon tools.

Sections:
- ``TestAddToolContract``: ids, keys, copies, data handling.
- ``TestTclSites``: the Tcl commands that create tools (add_drill, add_slot, aligndrillgrid). They compare the tools dict
  with literal expected values and do NOT use add_tool, so the same tests pass before and after a site is moved to
  add_tool (that is the before/after proof).
- ``TestPluginPatterns``: the plugin sites (Markers, CutOut, PDF import, 2-sided alignment drills) cannot run headless
  (they sit inside GUI handlers). These tests build the tools the way the site did before (a hand-made dict, assigned
  and then ``create_geometry``) and the way it does now (``add_tool``), and compare the resulting tools dict.
"""

from copy import deepcopy
from unittest.mock import Mock

import pytest
from shapely.geometry import Point

from appParsers.ParseExcellon import Excellon
from tclCommands.TclCommandAddDrill import TclCommandAddDrill
from tclCommands.TclCommandAddSlot import TclCommandAddSlot
from tclCommands.TclCommandAlignDrillGrid import TclCommandAlignDrillGrid
from test_unify_regressions_0b import make_app, make_excellon


def fresh(default_data=None):
    ex = Excellon(make_app())
    if default_data is not None:
        ex.default_data = default_data
    return ex


def snap(tools):
    """A comparable snapshot of a tools dict, including the order of the tool ids."""
    out = []
    for tid, tool in tools.items():
        out.append((tid, {
            key: ([g.wkt for g in val] if key == 'solid_geometry' else
                  [(a.wkt, b.wkt) for a, b in val] if key == 'slots' else
                  [p.wkt for p in val] if key == 'drills' else deepcopy(val))
            for key, val in tool.items()}))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------------------------------------------------

class TestAddToolContract:
    def test_first_id_is_1_and_ids_grow(self):
        ex = fresh()
        assert ex.add_tool(0.8) == 1
        assert ex.add_tool(1.0) == 2
        assert list(ex.tools) == [1, 2]

    def test_next_id_is_max_plus_1_not_len_plus_1(self):
        ex = fresh()
        ex.tools = {1: {}, 5: {}}
        assert ex.add_tool(0.8) == 6

    def test_next_id_reads_string_ids(self):
        ex = fresh()
        ex.tools = {'1': {}, '3': {}}
        assert ex.add_tool(0.8) == 4

    def test_explicit_id_is_used_as_given(self):
        ex = fresh()
        assert ex.add_tool(0.8, tool_id='1') == '1'
        assert list(ex.tools) == ['1']

    def test_existing_id_raises_and_keeps_the_tool(self):
        ex = fresh()
        ex.add_tool(0.8)
        with pytest.raises(ValueError):
            ex.add_tool(1.0, tool_id=1)
        assert ex.tools[1]['tooldia'] == 0.8

    def test_exact_key_set_and_types(self):
        ex = fresh()
        tid = ex.add_tool(1, drills=[Point(1, 2)], slots=[(Point(0, 0), Point(1, 0))])
        tool = ex.tools[tid]
        assert set(tool) == {'tooldia', 'drills', 'slots', 'solid_geometry', 'data'}
        assert type(tool['tooldia']) is float and tool['tooldia'] == 1.0
        assert isinstance(tool['drills'], list) and isinstance(tool['slots'], list)
        assert tool['solid_geometry'] == []
        assert tool['data'] == {}

    def test_slots_are_stored_as_tuples(self):
        ex = fresh()
        tid = ex.add_tool(1.0, slots=[[Point(0, 0), Point(1, 0)]])
        slot = ex.tools[tid]['slots'][0]
        assert type(slot) is tuple and slot[0].equals(Point(0, 0)) and slot[1].equals(Point(1, 0))

    def test_drills_and_slots_are_copied_lists(self):
        ex = fresh()
        drills = [Point(1, 1)]
        slots = [(Point(0, 0), Point(1, 0))]
        tid = ex.add_tool(1.0, drills=drills, slots=slots)
        drills.append(Point(2, 2))
        slots.clear()
        assert len(ex.tools[tid]['drills']) == 1 and len(ex.tools[tid]['slots']) == 1

    def test_generators_are_accepted(self):
        ex = fresh()
        tid = ex.add_tool(1.0, drills=(Point(i, 0) for i in range(3)))
        assert len(ex.tools[tid]['drills']) == 3

    def test_data_none_uses_a_copy_of_default_data(self):
        ex = fresh(default_data={'a': 1, 'nested': {'b': 2}})
        tid = ex.add_tool(1.0)
        assert ex.tools[tid]['data'] == {'a': 1, 'nested': {'b': 2}}
        ex.tools[tid]['data']['nested']['b'] = 99
        assert ex.default_data['nested']['b'] == 2

    def test_data_none_without_default_data_attribute_is_empty(self):
        ex = fresh()
        assert not hasattr(ex, 'default_data')
        assert ex.tools[ex.add_tool(1.0)]['data'] == {}

    def test_data_argument_is_deep_copied(self):
        ex = fresh(default_data={'from_default': 1})
        data = {'x': {'y': 1}}
        tid = ex.add_tool(1.0, data=data)
        assert ex.tools[tid]['data'] == {'x': {'y': 1}}
        data['x']['y'] = 2
        assert ex.tools[tid]['data']['x']['y'] == 1

    def test_two_tools_do_not_share_data(self):
        ex = fresh(default_data={'a': [1]})
        t1, t2 = ex.add_tool(1.0), ex.add_tool(2.0)
        ex.tools[t1]['data']['a'].append(2)
        assert ex.tools[t2]['data']['a'] == [1]

    def test_empty_data_is_filled_by_create_geometry_and_a_given_one_is_kept(self):
        ex = fresh(default_data={'from_default': 1})
        t_empty = ex.add_tool(1.0, drills=[Point(0, 0)], data={})
        t_given = ex.add_tool(2.0, drills=[Point(5, 5)], data={'mine': 2})
        t_default = ex.add_tool(3.0, drills=[Point(9, 9)])
        assert ex.create_geometry() != 'fail'
        assert ex.tools[t_empty]['data'] == {'from_default': 1}
        assert ex.tools[t_given]['data'] == {'mine': 2}
        assert ex.tools[t_default]['data'] == {'from_default': 1}

    def test_solid_geometry_stays_empty_until_create_geometry(self):
        ex = fresh()
        tid = ex.add_tool(1.0, drills=[Point(0, 0)], slots=[(Point(2, 0), Point(4, 0))])
        assert ex.tools[tid]['solid_geometry'] == []
        ex.create_geometry()
        assert len(ex.tools[tid]['solid_geometry']) == 2


# ---------------------------------------------------------------------------------------------------------------------
# Tcl sites: literal expectations, valid before and after the move to add_tool
# ---------------------------------------------------------------------------------------------------------------------

def _tcl_app():
    app = make_app()
    app.dec_format = lambda value, digits=None: round(float(value), 4 if digits is None else digits)
    return app


def _tcl_obj(app, tools_spec):
    """An Excellon with the given {id: (dia, drills, slots)}; its data is the marker ``{'seed': id}``."""
    ex = Excellon(app)
    ex.kind = 'excellon'
    ex.units = 'MM'
    ex.obj_options = {'name': 'ex'}
    ex.tools = {}
    for tid, (dia, drills, slots) in tools_spec.items():
        ex.tools[tid] = {'tooldia': dia, 'drills': [Point(p) for p in drills],
                         'slots': [(Point(a), Point(b)) for a, b in slots],
                         'solid_geometry': [], 'data': {'seed': tid}}
    ex.create_geometry()
    return ex


def _run_tcl(cmd_cls, app, obj, *argv):
    app.collection.get_by_name.side_effect = lambda n: obj if n == 'ex' else None
    cmd = cmd_cls(app)
    args, unnamed = cmd.check_args(('ex',) + argv)
    return cmd.execute(args, unnamed)


def _geo(dia, drills=(), slots=(), steps=16):
    out = [Point(p).buffer(dia / 2.0, steps).wkt for p in drills]
    from shapely.geometry import LineString
    out += [LineString([a, b]).buffer(dia / 2.0, steps).wkt for a, b in slots]
    return out


def _option_data(app):
    """What add_drill / add_slot put in the data of a new tool."""
    data = {}
    for option in app.options:
        if option.find('excellon_') == 0:
            data[option[len('excellon_'):]] = app.options[option]
        if option.find('tools_drill_') == 0:
            data[option] = app.options[option]
    return data


class TestTclSites:
    def test_add_drill_to_an_object_without_tools(self):
        app = _tcl_app()
        obj = _tcl_obj(app, {})
        _run_tcl(TclCommandAddDrill, app, obj, '-dia', '0.8', '-x', '1', '-y', '2')
        assert snap(obj.tools) == [(1, {
            'tooldia': 0.8, 'drills': [Point(1, 2).wkt], 'slots': [], 'data': _option_data(app),
            'solid_geometry': _geo(0.8, [(1, 2)])})]

    def test_add_drill_new_diameter_renumbers_by_diameter(self):
        app = _tcl_app()
        obj = _tcl_obj(app, {1: (1.0, [(0, 0)], []), 2: (0.5, [(3, 3)], [])})
        _run_tcl(TclCommandAddDrill, app, obj, '-dia', '0.8', '-x', '1', '-y', '2')
        got = snap(obj.tools)
        assert [tid for tid, _ in got] == [1, 2, 3]
        assert [t['tooldia'] for _, t in got] == [0.5, 0.8, 1.0]
        new = got[1][1]
        assert new['drills'] == [Point(1, 2).wkt] and new['slots'] == []
        assert new['data'] == _option_data(app)
        assert new['solid_geometry'] == _geo(0.8, [(1, 2)])
        assert set(new) == {'tooldia', 'drills', 'slots', 'data', 'solid_geometry'}
        # the old tools keep their own data
        assert got[0][1]['data'] == {'seed': 2} and got[2][1]['data'] == {'seed': 1}

    def test_add_drill_existing_diameter_appends_and_rejects_a_duplicate(self):
        app = _tcl_app()
        obj = _tcl_obj(app, {1: (0.8, [(0, 0)], [])})
        _run_tcl(TclCommandAddDrill, app, obj, '-dia', '0.8', '-x', '1', '-y', '2')
        assert [p.wkt for p in obj.tools[1]['drills']] == [Point(0, 0).wkt, Point(1, 2).wkt]
        assert obj.tools[1]['data'] == {'seed': 1}
        msg = _run_tcl(TclCommandAddDrill, app, obj, '-dia', '0.8', '-x', '1', '-y', '2')
        assert 'already' in msg and len(obj.tools[1]['drills']) == 2

    def test_add_slot_to_an_object_without_tools(self):
        app = _tcl_app()
        obj = _tcl_obj(app, {})
        # add_slot reads the start / stop points straight from args; the command has no option_types for them,
        # so call execute with the parsed dict
        args = {'name': 'ex', 'dia': 0.8, 'startx': 0.0, 'starty': 1.0, 'stopx': 4.0, 'stopy': 1.0}
        app.collection.get_by_name.side_effect = lambda n: obj if n == 'ex' else None
        TclCommandAddSlot(app).execute(args, [])
        assert snap(obj.tools) == [(1, {
            'tooldia': 0.8, 'drills': [], 'slots': [(Point(0, 1).wkt, Point(4, 1).wkt)], 'data': _option_data(app),
            'solid_geometry': _geo(0.8, slots=[((0, 1), (4, 1))])})]

    def test_add_slot_new_diameter_renumbers_by_diameter(self):
        app = _tcl_app()
        obj = _tcl_obj(app, {1: (1.0, [(0, 0)], []), 2: (0.5, [], [((0, 5), (2, 5))])})
        args = {'name': 'ex', 'dia': 0.8, 'startx': 0.0, 'starty': 1.0, 'stopx': 4.0, 'stopy': 1.0}
        app.collection.get_by_name.side_effect = lambda n: obj if n == 'ex' else None
        TclCommandAddSlot(app).execute(args, [])
        got = snap(obj.tools)
        assert [t['tooldia'] for _, t in got] == [0.5, 0.8, 1.0]
        new = got[1][1]
        assert new['drills'] == [] and new['slots'] == [(Point(0, 1).wkt, Point(4, 1).wkt)]
        assert new['data'] == _option_data(app)
        assert set(new) == {'tooldia', 'drills', 'slots', 'data', 'solid_geometry'}

    def test_aligndrillgrid_creates_one_tool_with_string_id(self):
        app = _tcl_app()
        created = []

        def new_object(kind, name, init, plot=False):
            obj = Excellon(app)
            obj.units = 'MM'
            obj.obj_options = {'name': name}
            app.f_handlers = Mock()
            init(obj, app)
            created.append(obj)

        app.app_obj.new_object.side_effect = new_object
        cmd = TclCommandAlignDrillGrid(app)
        args, unnamed = cmd.check_args(('-dia', '1.5', '-rows', '2', '-columns', '3', '-gridx', '2', '-gridy', '3',
                                        '-gridoffsetx', '1', '-gridoffsety', '4'))
        cmd.execute(args, unnamed)
        obj, = created
        drills = [(1, 4), (3, 4), (5, 4), (1, 7), (3, 7), (5, 7)]
        got = snap(obj.tools)
        assert [tid for tid, _ in got] == ['1']
        tool = got[0][1]
        assert tool['tooldia'] == 1.5 and tool['drills'] == [Point(p).wkt for p in drills]
        assert tool['solid_geometry'] == _geo(1.5, drills)
        assert tool['data'] == {}
        assert set(tool).issuperset({'tooldia', 'drills', 'solid_geometry', 'data'})
        assert set(tool) - {'tooldia', 'drills', 'solid_geometry', 'data', 'slots'} == set()


# ---------------------------------------------------------------------------------------------------------------------
# plugin sites: the old hand-made dict against add_tool
# ---------------------------------------------------------------------------------------------------------------------

def _old_way(default_data, tools):
    """What the sites did: assign a (deep copied) dict, then create_geometry."""
    ex = fresh(default_data)
    ex.tools = deepcopy(tools)
    ex.create_geometry()
    return ex


def _drop_empty_slots(tools):
    """The old dicts of some sites had no 'slots' key; add_tool always has the (empty) key."""
    return [(tid, {k: v for k, v in tool.items() if not (k == 'slots' and v == [])}) for tid, tool in snap(tools)]


@pytest.mark.parametrize('default_data', [{}, {'tools_drill_ppname_e': 'default', 'x': 1}])
class TestPluginPatterns:
    def test_markers_corner_drills(self, default_data):
        pts = [Point(1, 1), Point(5, 1)]
        old = _old_way(default_data, {1: {'tooldia': 0.5, 'drills': pts, 'slots': [], 'data': {},
                                          'solid_geometry': []}})
        new = fresh(default_data)
        new.add_tool(0.5, drills=pts, data={})
        new.create_geometry()
        assert snap(new.tools) == snap(old.tools)

    def test_markers_check_points_set_ppname_before_create_geometry(self, default_data):
        pts = [Point(1, 1)]
        old = fresh(default_data)
        old.tools = {1: {'tooldia': 0.5, 'drills': pts, 'slots': [], 'data': {}, 'solid_geometry': []}}
        old.tools[1]['data']['tools_drill_ppname_e'] = 'Check_points'
        old.create_geometry()

        new = fresh(default_data)
        new.add_tool(0.5, drills=pts, data={})
        new.tools[1]['data']['tools_drill_ppname_e'] = 'Check_points'
        new.create_geometry()
        assert snap(new.tools) == snap(old.tools)
        assert new.tools[1]['data'] == {'tools_drill_ppname_e': 'Check_points'}

    def test_cutout_holes_without_slots_key(self, default_data):
        holes = [Point(0, 0), Point(3, 0), Point(6, 0)]
        old = _old_way(default_data, {1: {'tooldia': 2.0, 'drills': holes, 'solid_geometry': []}})
        new = fresh(default_data)
        new.add_tool(2.0, drills=holes)
        new.create_geometry()
        assert snap(new.tools) != snap(old.tools)  # the new tool has the empty 'slots' key
        assert _drop_empty_slots(new.tools) == _drop_empty_slots(old.tools)
        assert new.solid_geometry and [g.wkt for g in new.solid_geometry] == [g.wkt for g in old.solid_geometry]

    def test_cutout_drill_cut_with_slots_key(self, default_data):
        holes = [Point(0, 0), Point(3, 0)]
        old = _old_way(default_data, {1: {'tooldia': 1.2, 'drills': holes, 'slots': [], 'solid_geometry': []}})
        new = fresh(default_data)
        new.add_tool(1.2, drills=holes)
        new.create_geometry()
        assert snap(new.tools) == snap(old.tools)

    def test_pdf_import_string_ids_by_sorted_diameter(self, default_data):
        points = {0.8: [(0, 0), (1, 1)], 0.3: [(5, 5)], 1.0: [(9, 9)]}
        old_tools = {}
        for n, dia in enumerate(sorted(points), 1):
            old_tools[str(n)] = {'tooldia': dia, 'drills': [Point(pt) for pt in points[dia]],
                                 'solid_geometry': []}
        old = _old_way(default_data, old_tools)
        new = fresh(default_data)
        for n, dia in enumerate(sorted(points), 1):
            new.add_tool(dia, drills=[Point(pt) for pt in points[dia]], tool_id=str(n))
        new.create_geometry()
        assert list(new.tools) == ['1', '2', '3'] == list(old.tools)
        assert _drop_empty_slots(new.tools) == _drop_empty_slots(old.tools)

    def test_two_sided_alignment_drills(self, default_data):
        pts = [Point(1, 2), Point(1, -2)]
        old = _old_way(default_data, {1: {'tooldia': 3.0, 'drills': pts, 'solid_geometry': [p for p in pts]}})
        new = fresh(default_data)
        new.add_tool(3.0, drills=pts)
        new.create_geometry()
        assert _drop_empty_slots(new.tools) == _drop_empty_slots(old.tools)
