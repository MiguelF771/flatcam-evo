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

# ---------------------------------------------------------------------------------------------------------------
# a missing -box object raised AttributeError
# ---------------------------------------------------------------------------------------------------------------

def test_missing_box_object_fails_with_a_message_naming_it(app):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-rows', '1', '-columns', '2', '-box', 'no_such_box')
    assert res == 'fail' and not created
    messages = [str(c.args[0]) for c in app.log.error.call_args_list]
    assert any('no_such_box' in m for m in messages)
    app.app_obj.new_object.assert_not_called()


def test_existing_box_object_sets_the_pitch(app):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-rows', '1', '-columns', '2', '-spacing_columns', '5',
                                '-box', 'g', '-outname', 'p')
    assert res is None
    w = src.bounds()[2] - src.bounds()[0]
    assert created['p'].bounds()[2] == pytest.approx(src.bounds()[2] + w + 5.0)

# ---------------------------------------------------------------------------------------------------------------
# only -rows (or only -columns) gave 0 for the other one: an empty panel
# ---------------------------------------------------------------------------------------------------------------

def copies(pan):
    # the aperture 11 of the mixed fixture holds 1 record
    return len(pan.tools[11]['geometry'])


def test_only_rows_means_one_column(app):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-rows', '3', '-outname', 'p')
    assert res is None and copies(created['p']) == 3
    assert created['p'].bounds()[2] == pytest.approx(src.bounds()[2])
    assert created['p'].bounds()[3] > src.bounds()[3] + 2 * 5.0


def test_only_columns_means_one_row(app):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-columns', '4', '-outname', 'p')
    assert res is None and copies(created['p']) == 4
    assert created['p'].bounds()[3] == pytest.approx(src.bounds()[3])


def test_neither_rows_nor_columns_fails(app):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', '-outname', 'p')
    assert res == 'fail' and not created


@pytest.mark.parametrize('argv', [('-rows', '0', '-columns', '2'), ('-rows', '2', '-columns', '0'),
                                  ('-rows', '-1'), ('-columns', '0')])
def test_zero_or_negative_counts_fail_instead_of_an_empty_panel(app, argv):
    src = make_gerber(app, name='g')
    res, created = run_panelize(app, src, 'g', *argv)
    assert res == 'fail' and not created


def test_help_states_the_default():
    help_text = TclCommandPanelize.help['args']
    assert '1 if not given' in help_text['rows'] and '1 if not given' in help_text['columns']
