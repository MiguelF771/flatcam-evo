"""Bug fixes of TclCommandPanelize (Stage 5), headless (same driver as test_panelize_tcl_characterization.py)."""

from unittest.mock import MagicMock, Mock

import pytest

from tclCommands.TclCommandPanelize import TclCommandPanelize

from test_unify_characterization import _headless_qt, app, make_gerber  # noqa: F401  (fixtures)
from test_panelize_tcl_characterization import run_panelize


def tcl_args(app, *argv):
    cmd = TclCommandPanelize(app)
    return cmd, cmd.check_args(tuple(argv))


# ---------------------------------------------------------------------------------------------------------------
# use_thread went through eval()
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('value', ['1', 'True', 'true', 'TRUE', 'yes', 'on'])
def test_use_thread_true_values_start_a_worker_task(app, value):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-columns', '2', '-rows', '1', '-use_thread', value)
    assert res is None and not created
    app.worker_task.emit.assert_called_once()
    app.collection.promise.assert_called_once_with('g_panelized')
    # the queued job builds the panel
    task = app.worker_task.emit.call_args[0][0]
    task['fcn'](*task['params'])
    assert list(created) == ['g_panelized']


@pytest.mark.parametrize('value', ['0', 'False', 'false', 'no', 'off'])
def test_use_thread_false_values_run_at_once(app, value):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-columns', '2', '-rows', '1', '-use_thread', value)
    assert res is None and list(created) == ['g_panelized']
    app.worker_task.emit.assert_not_called()


@pytest.mark.parametrize('value', ['maybe', '__import__("os").getcwd()', 'print(1)', '2 > 1'])
def test_use_thread_is_never_evaluated(app, value):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-columns', '2', '-rows', '1', '-use_thread', value)
    assert res == 'fail' and not created
    app.worker_task.emit.assert_not_called()
    assert any('use_thread' in str(c) for c in app.log.error.call_args_list)
