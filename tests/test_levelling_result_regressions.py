"""S4 result ordering and units contracts. Run each pytest node separately."""
import threading
from unittest.mock import patch

import pytest
from PyQt6 import QtWidgets
from shapely import box

from tests import test_levelling_audit as audit
from tests.test_levelling_audit import FakeGrbl, _qapp, run_worker


def result_tool(units='MM'):
    fake = FakeGrbl(heights={(0.0, 0.0): 0.1, (10.0, 0.0): 0.2})
    tool, _ = audit.TestGrblProbeRun()._tool(fake, units=units)
    QtWidgets.QWidget.__init__(tool)
    del tool.al_result_ready_sig
    tool.connect_signals_at_init()
    tool.ui.object_combo.get_value.return_value = 'job'
    # Create the points through production publication, rather than inventing
    # the name of the units metadata that GREEN will introduce.
    tool.al_voronoi_geo_storage.clear()
    tool.al_result_ready_sig.emit({
        'kind': 'import_points', 'context': tool._al_capture_context(),
        'points': [(0.0, 0.0, 0.5), (10.0, 0.0, 0.5)],
    })
    tool.build_al_table_sig.reset_mock()
    tool.show_probing_geo_sig.reset_mock()
    # the setup import applies the map (import now applies); tests count only their own applies
    tool.apply_autolevel.reset_mock()
    return tool, fake


def in_worker(callback):
    errors = []

    def run():
        try:
            callback()
        except BaseException as err:
            errors.append(err)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive(), 'worker did not finish'
    if errors:
        raise errors[0]


def drain_results():
    _qapp.processEvents()
    _qapp.processEvents()


def heights(tool):
    return {key: value['height'] for key, value in tool.al_voronoi_geo_storage.items()}


def start_import(tool, path):
    with patch('appPlugins.ToolLevelling.QtWidgets.QFileDialog.getOpenFileName',
               return_value=(str(path), '')):
        tool.on_import_height_map()
    task = tool.app.worker_task.emit.call_args
    return task.args[0] if task is not None else None


def run_import(task):
    in_worker(lambda: task['fcn'](*task['params']))


@pytest.mark.parametrize('old_kind', ['import', 'failed'])
@pytest.mark.parametrize('complete_b', [False, True], ids=['b_started', 'b_completed'])
def test_old_import_result_cannot_change_replacement(tmp_path, old_kind, complete_b):
    """Expected RED: same-point replacement must retire both success and failure A."""
    tool, _ = result_tool()
    path = tmp_path / 'map.txt'
    path.write_text('0,0,0.3\n10,0,0.4\n')
    a = start_import(tool, path)
    old_context = a['params'][1]
    b = start_import(tool, path)
    if complete_b:
        run_import(b)
        drain_results()
        assert heights(tool) == {1: 0.3, 2: 0.4}
        assert tool.al_heights_valid
    before = heights(tool), tool.al_heights_valid
    publications = tool.build_al_table_sig.emit.call_count
    result = {'kind': old_kind, 'context': old_context, 'heights': {1: 9.0, 2: 9.0}}
    in_worker(lambda: tool.al_result_ready_sig.emit(result))
    drain_results()
    assert (heights(tool), tool.al_heights_valid) == before
    assert tool.build_al_table_sig.emit.call_count == publications


def test_import_invalidates_previous_map_before_scheduling(tmp_path):
    """Expected RED: the scheduling observer must already see an invalid map."""
    tool, _ = result_tool()
    seen = []
    tool.app.worker_task.emit.side_effect = lambda task: seen.append(tool.al_heights_valid)
    start_import(tool, tmp_path / 'map.txt')
    assert seen == [False]


def test_direct_import_acquires_replacement_attempt(tmp_path):
    """Expected RED: the compatibility API must retire an earlier captured result."""
    tool, _ = result_tool()
    old_context = tool._al_capture_context()
    path = tmp_path / 'map.txt'
    path.write_text('0,0,0.3\n10,0,0.4\n')
    tool.import_height_map(str(path))
    assert heights(tool) == {1: 0.3, 2: 0.4}
    in_worker(lambda: tool.al_result_ready_sig.emit({
        'kind': 'failed', 'context': old_context,
    }))
    drain_results()
    assert tool.al_heights_valid
    assert heights(tool) == {1: 0.3, 2: 0.4}


def test_captured_import_does_not_start_second_attempt(tmp_path):
    """Expected PASS: an already captured worker context remains current."""
    tool, _ = result_tool()
    path = tmp_path / 'map.txt'
    path.write_text('0,0,0.3\n10,0,0.4\n')
    task = start_import(tool, path)
    context = task['params'][1]
    run_import(task)
    assert tool._al_context_current(context)
    drain_results()
    assert tool.al_heights_valid
    assert heights(tool) == {1: 0.3, 2: 0.4}


@pytest.mark.parametrize('replacement', ['import', 'probe'])
def test_queued_probe_is_rejected_after_replacement_starts(tmp_path, replacement):
    """Expected RED: released probe A must not publish after B starts."""
    tool, _ = result_tool()
    tool.on_grbl_autolevel()
    in_worker(lambda: run_worker(tool))
    assert tool._grbl_op is None
    path = tmp_path / 'map.txt'
    path.write_text('0,0,0.3\n10,0,0.4\n')
    if replacement == 'import':
        start_import(tool, path)
    else:
        tool.on_grbl_autolevel()
    before = heights(tool)
    drain_results()
    assert heights(tool) == before
    assert not tool.al_heights_valid
    tool.apply_autolevel.assert_not_called()
    tool.build_al_table_sig.emit.assert_not_called()


@pytest.mark.parametrize('action', ['stop', 'reset', 'cancel'])
def test_released_probe_evidence_is_checked_before_gui_storage_update(action):
    """Expected RED: cancellation/safety evidence survives serial ownership release."""
    tool, _ = result_tool()
    tool.on_grbl_autolevel()
    op = tool._grbl_op
    assert op is not None
    in_worker(lambda: run_worker(tool))
    assert tool._grbl_op is None
    assert heights(tool) == {1: 0.5, 2: 0.5}, 'worker must not publish directly'
    if action == 'cancel':
        op.cancel.set()
    else:
        getattr(tool, 'on_grbl_' + action)()
        assert tool._grbl_safety_generation != op.safety_generation
    drain_results()
    assert heights(tool) == {1: 0.5, 2: 0.5}
    assert not tool.al_heights_valid
    tool.apply_autolevel.assert_not_called()
    tool.build_al_table_sig.emit.assert_not_called()
    tool.show_probing_geo_sig.emit.assert_not_called()


def test_released_current_probe_publishes_atomically_on_gui_thread():
    """Expected PASS: no active operation is required for queued publication."""
    tool, _ = result_tool()
    seen = []
    tool.build_al_table_sig.emit.side_effect = lambda: seen.append(
        (threading.current_thread() is threading.main_thread(), heights(tool)))
    tool.on_grbl_autolevel()
    in_worker(lambda: run_worker(tool))
    assert tool._grbl_op is None
    assert not seen
    assert heights(tool) == {1: 0.5, 2: 0.5}
    drain_results()
    assert seen == [(True, {1: 0.1, 2: 0.2})]
    assert tool.al_heights_valid
    tool.apply_autolevel.assert_called_once_with(target_obj=tool._al_target_obj)


def test_import_worker_uses_captured_units_without_mutating_shared_units(tmp_path):
    """Expected RED: 0.1 mm noise is inside the captured MM cap, not the IN cap."""
    tool, _ = result_tool()
    path = tmp_path / 'map.txt'
    path.write_text('0.1,0,0.3\n10.1,0,0.4\n')
    task = start_import(tool, path)
    tool.app.app_units = 'IN'
    tool.units = 'IN'
    run_import(task)
    assert tool.units == 'IN', 'import worker must not rewrite shared units'
    # Restore the project only after parsing, so publication is eligible again.
    tool.app.app_units = 'MM'
    drain_results()
    assert tool.al_heights_valid
    assert heights(tool) == {1: 0.3, 2: 0.4}


def test_import_worker_never_refreshes_shared_units(tmp_path):
    """Expected RED: self.units is not worker scratch storage."""
    tool, _ = result_tool()
    path = tmp_path / 'map.txt'
    path.write_text('0,0,0.3\n10,0,0.4\n')
    task = start_import(tool, path)
    tool.units = 'IN'
    run_import(task)
    drain_results()
    assert tool.units == 'IN'
    assert tool.al_heights_valid


def test_probe_uses_captured_scale_and_tolerance():
    """Expected PASS with S1: changing shared units cannot change worker math."""
    tool, fake = result_tool()
    fake.replies['G38.2'] = ['[PRB:0.1,0,0.25:1]', 'ok']
    tool.al_voronoi_geo_storage.pop(2)
    tool.on_grbl_autolevel()
    tool.units = 'IN'
    in_worker(lambda: run_worker(tool))
    drain_results()
    assert tool.al_heights_valid
    assert heights(tool) == {1: 0.25}


@pytest.mark.parametrize('entry', ['probe', 'import', 'gui_import'])
def test_existing_point_units_cannot_be_reinterpreted(tmp_path, entry):
    """Expected RED: points created in MM cannot be reused as IN coordinates."""
    tool, fake = result_tool()
    tool.app.app_units = 'IN'
    tool.units = 'IN'
    tool.app.worker_task.emit.reset_mock()
    if entry == 'probe':
        tool.on_grbl_autolevel()
        assert tool._grbl_op is None, 'The refused probe must release the port at once'
        scheduled = tool.app.worker_task.emit.call_args
        if scheduled:
            in_worker(lambda: run_worker(tool))
            drain_results()
        assert not any(cmd.startswith(('G0', 'G38.2')) for cmd in fake.commands())
        tool.app.worker_task.emit.assert_not_called()
    else:
        path = tmp_path / 'map.txt'
        path.write_text('0,0,0.3\n10,0,0.4\n')
        if entry == 'gui_import':
            start_import(tool, path)
            tool.app.worker_task.emit.assert_not_called()
        else:
            tool.import_height_map(str(path))
        assert not tool.al_heights_valid
        assert heights(tool) != {1: 0.3, 2: 0.4}


def test_context_retains_point_units_after_project_units_change():
    tool, _ = result_tool()
    assert getattr(tool, '_al_data_units', None) == 'MM'
    tool.app.app_units = 'IN'
    assert tool._al_capture_context().units == 'MM'


@pytest.mark.parametrize('mode', ['grid', 'manual'])
def test_add_captures_units_before_point_creation(mode):
    tool, _ = audit.TestPolygonsAndAdd()._add_tool([{'geom': box(0, 0, 10, 10), 'kind': ['C']}])
    tool._al_data_units = None
    tool.app.app_units = 'IN'
    tool.ui.al_mode_radio.get_value.return_value = mode
    seen = []
    creator = tool.on_add_grid_points if mode == 'grid' else tool.on_add_manual_points
    creator.side_effect = lambda: seen.append(getattr(tool, '_al_data_units', None))
    tool.on_add_al_probepoints()
    assert seen == ['IN']


def test_clear_forgets_point_units():
    tool, _ = result_tool()
    assert getattr(tool, '_al_data_units', None) == 'MM'
    context = tool._al_capture_context()
    tool._clear_probe_data()
    assert tool._al_data_units is None
    assert not tool._al_context_current(context)


def test_import_worker_uses_tolerance_captured_before_scheduling(tmp_path):
    tool, _ = result_tool()
    path = tmp_path / 'map.txt'
    path.write_text('0.1,0,0.3\n10.1,0,0.4\n')
    task = start_import(tool, path)
    with patch.object(tool, '_al_match_tolerance', return_value=0.02):
        run_import(task)
    drain_results()
    assert tool.al_heights_valid
    assert heights(tool) == {1: 0.3, 2: 0.4}


def test_default_tolerance_api_remains_compatible():
    """Expected PASS: adding captured units must preserve direct-call defaults."""
    tool, _ = result_tool()
    assert tool._al_match_tolerance([(0.0, 0.0), (10.0, 0.0)]) == 0.5
    tool.units = 'IN'
    assert tool._al_match_tolerance([(0.0, 0.0), (10.0, 0.0)]) == 0.02
