"""S6 manual probe lifecycle regressions; stop at RED before production edits."""
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from shapely import Point, box

from test_levelling_audit import make_audit_tool


def make_lifecycle_tool(use_3d_engine):
    tool = make_audit_tool()
    tool.app.use_3d_engine = use_3d_engine
    tool.app.options = {'global_selection_shape': True, 'global_grid_snap': True}
    tool.app.kp, tool.app.mp, tool.app.mr = 101, 102, 103
    tool.app.plotcanvas.graph_event_connect.side_effect = iter(range(201, 301))
    tool.app.plotcanvas.translate_coords.return_value = (2.2, 3.3)
    tool.app.geo_editor.snap.return_value = (2.0, 3.0)
    tool.ui.avoid_exc_holes_cb.get_value.return_value = False
    tool.ui.probe_tip_dia_entry.get_value.return_value = 0.5
    tool.ui.al_mode_radio.get_value.return_value = 'manual'
    tool.ui.object_combo.get_value.return_value = 'job'
    tool.ui.object_combo.currentText.return_value = 'job'
    tool.probing_shapes = MagicMock()
    tool.build_al_table = MagicMock()
    tool.plot_probing_geo = MagicMock()
    tool._new_probing_shapes = MagicMock()
    tool.solid_geo = box(0.0, 0.0, 10.0, 10.0)
    target = SimpleNamespace(
        is_segmented_gcode=True,
        obj_options={'type': 'Geometry'},
        gcode_parsed=[{'kind': ['C'], 'geom': tool.solid_geo}],
    )
    tool.app.collection.get_by_name.return_value = target
    tool._al_target_name, tool._al_target_obj = 'job', target
    return tool


def left_click(tool):
    return SimpleNamespace(pos=(2.2, 3.3), xdata=2.2, ydata=3.3, button=1)


def assert_released(tool, handles):
    canvas = tool.app.plotcanvas
    expected = [
        call('key_press', tool.on_key_press),
        call('mouse_release', tool.on_mouse_click_release),
        call('mouse_move', tool.on_mouse_move),
    ] if tool.app.use_3d_engine else [call(handle) for handle in handles]
    canvas.graph_event_disconnect.assert_has_calls(expected)
    canvas.graph_event_connect.assert_has_calls([
        call('key_press', tool.app.ui.keyPressEvent),
        call('mouse_press', tool.app.on_mouse_click_over_plot),
        call('mouse_release', tool.app.on_mouse_click_release_over_plot),
    ])
    assert not tool.mouse_events_connected
    assert (tool.kp, tool.mr, tool.mm) == (None, None, None)
    assert tool.app.options['global_selection_shape'] is True
    assert tool.app.options['global_grid_snap'] is True


@pytest.mark.parametrize('mode', ['grid', 'manual'])
def test_mode_change_clears_complete_probe_data(mode):
    tool = make_lifecycle_tool(False)
    tool.al_voronoi_geo_storage = {1: {'point': Point(2, 3), 'height': 0.1}}
    tool.al_bilinear_geo_storage = [(2.0, 3.0, 0.1)]
    tool._al_data_units = 'MM'
    tool.al_heights_valid = True
    tool.probing_gcode_text = 'OLD PROBE GCODE'
    generation = tool._al_generation

    tool.on_mode_radio(mode)

    assert tool.probing_gcode_text == ''
    assert tool.al_bilinear_geo_storage == []
    assert tool.al_voronoi_geo_storage == {}
    assert not tool.al_heights_valid
    assert tool._al_data_units is None
    assert tool.solid_geo is None
    assert tool._al_target_name is None
    assert tool._al_target_obj is None
    assert tool._al_generation == generation + 1
    tool.build_al_table.assert_called_once()
    tool.probing_shapes.clear.assert_called_once_with(update=True)


@pytest.mark.parametrize('use_3d_engine', [False, True])
@pytest.mark.parametrize('reset', ['mode', 'target', 'clear'])
def test_destructive_reset_releases_before_clearing(use_3d_engine, reset):
    tool = make_lifecycle_tool(use_3d_engine)
    tool.on_add_manual_points()
    handles = (tool.kp, tool.mr, tool.mm)
    tool.app.plotcanvas.reset_mock()
    released_at_clear = []
    tool.probing_shapes.clear.side_effect = lambda **kwargs: released_at_clear.append(
        tool.mouse_events_connected)

    if reset == 'mode':
        tool.on_mode_radio('grid')
    elif reset == 'target':
        tool.app.collection.get_by_name.return_value = SimpleNamespace(
            is_segmented_gcode=False, obj_options={})
        tool.on_object_changed()
    else:
        tool._clear_probe_data()

    assert released_at_clear == [False]
    assert_released(tool, handles)
    tool.on_mouse_click_release(left_click(tool))
    assert tool.al_voronoi_geo_storage == {}


@pytest.mark.parametrize('use_3d_engine', [False, True])
@pytest.mark.parametrize('active,has_geometry', [(False, True), (False, False), (True, False)])
def test_stale_left_click_is_ignored(use_3d_engine, active, has_geometry):
    tool = make_lifecycle_tool(use_3d_engine)
    tool.mouse_events_connected = active
    if not has_geometry:
        tool.solid_geo = None
    generation = tool._al_generation

    tool.on_mouse_click_release(left_click(tool))

    assert tool.al_voronoi_geo_storage == {}
    assert tool._al_generation == generation
    tool.build_al_table_sig.emit.assert_not_called()
    tool.app.geo_editor.snap.assert_not_called()


@pytest.mark.parametrize('use_3d_engine', [False, True])
def test_second_add_releases_before_geometry_replacement(use_3d_engine):
    tool = make_lifecycle_tool(use_3d_engine)
    tool.on_add_al_probepoints()
    handles = (tool.kp, tool.mr, tool.mm)
    tool.app.plotcanvas.reset_mock()
    active_at_lookup = []
    target = tool._al_target_obj
    tool.app.collection.get_by_name.side_effect = lambda name: (
        active_at_lookup.append(tool.mouse_events_connected) or target)

    tool.on_add_al_probepoints()

    assert active_at_lookup == [False]
    assert tool.mouse_events_connected
    assert tool.old_selection_state is True
    assert tool.app.options['global_selection_shape'] is False
    assert tool.app.plotcanvas.graph_event_connect.call_args_list == [
        call('key_press', tool.app.ui.keyPressEvent),
        call('mouse_press', tool.app.on_mouse_click_over_plot),
        call('mouse_release', tool.app.on_mouse_click_release_over_plot),
        call('key_press', tool.on_key_press),
        call('mouse_release', tool.on_mouse_click_release),
        call('mouse_move', tool.on_mouse_move),
    ]
    if not use_3d_engine:
        tool.app.plotcanvas.graph_event_disconnect.assert_has_calls(
            [call(handle) for handle in handles])
    assert tool._al_data_units == 'MM'


@pytest.mark.parametrize('use_3d_engine', [False, True])
def test_release_is_idempotent_and_preserves_grid_snap(use_3d_engine):
    tool = make_lifecycle_tool(use_3d_engine)
    tool.on_add_manual_points()
    handles = (tool.kp, tool.mr, tool.mm)
    tool.app.plotcanvas.reset_mock()

    tool._release_manual_mouse_events()
    assert_released(tool, handles)
    tool.app.plotcanvas.reset_mock()
    tool._release_manual_mouse_events()

    tool.app.plotcanvas.graph_event_disconnect.assert_not_called()
    tool.app.plotcanvas.graph_event_connect.assert_not_called()


@pytest.mark.parametrize('use_3d_engine', [False, True])
def test_active_manual_click_keeps_existing_snapping(use_3d_engine):
    tool = make_lifecycle_tool(use_3d_engine)
    tool.on_add_manual_points()
    tool.build_al_table_sig.emit.reset_mock()

    tool.on_mouse_click_release(left_click(tool))

    assert tool.al_voronoi_geo_storage[1]['point'].equals(Point(2, 3))
    assert not tool.al_heights_valid
    tool.app.geo_editor.snap.assert_called_once_with(2.2, 3.3)
    tool.build_al_table_sig.emit.assert_called_once()
    assert tool.app.options['global_grid_snap'] is True
