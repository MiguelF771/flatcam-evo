"""ExcellonObject.on_plot_cb_click_table: update_visibility returns None; a tool without shapes is skipped."""
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_unify_characterization import make_app, make_excellon, _headless_qt  # noqa: E402,F401


class FakeShapes:
    def __init__(self):
        self.visibility_calls = []
        self.redraws = 0

    def update_visibility(self, state, indexes=None):
        self.visibility_calls.append((state, indexes))
        return None     # like the real implementations

    def redraw(self):
        self.redraws += 1


def _prepare(checked_by_tool, without_row=()):
    obj = make_excellon(make_app())
    tool_keys = list(obj.tools)
    obj.ui_connect = Mock()
    obj.ui_disconnect = Mock()
    obj.shapes = FakeShapes()

    items = {}
    cells = {}
    row = 0
    for key in tool_keys:
        if key in without_row:
            continue
        items[row] = Mock(text=Mock(return_value=str(key)))
        cells[row] = Mock(isChecked=Mock(return_value=checked_by_tool[key]))
        row += 1
    # like the real table: 2 total rows (drills, slots) with an empty id cell and no plot checkbox
    for _ in range(2):
        items[row] = Mock(text=Mock(return_value=''))
        cells[row] = None
        row += 1
    table = Mock()
    table.rowCount.return_value = row
    table.item.side_effect = lambda row, col: items[row]
    table.cellWidget.side_effect = lambda row, col: cells[row]
    obj.ui = Mock(tools_table=table)
    return obj, tool_keys


def test_visibility_is_set_per_tool_with_its_indexes():
    keys = list(make_excellon(make_app()).tools)
    assert len(keys) >= 2
    obj, keys = _prepare({k: (i % 2 == 0) for i, k in enumerate(keys)})
    obj.shape_indexes_dict = {k: [100 + i] for i, k in enumerate(keys)}

    obj.on_plot_cb_click_table()

    assert obj.shapes.visibility_calls == [(i % 2 == 0, [100 + i]) for i in range(len(keys))]
    assert obj.shapes.redraws == 1
    obj.ui_connect.assert_called_once()


def test_tool_without_plotted_shapes_is_skipped_without_error():
    obj = make_excellon(make_app())
    keys = list(obj.tools)
    obj, keys = _prepare({k: True for k in keys})
    obj.shape_indexes_dict = {keys[0]: [7]}     # the other tools have no shapes

    obj.on_plot_cb_click_table()

    assert obj.shapes.visibility_calls == [(True, [7])]
    assert obj.shapes.redraws == 1


def test_tool_without_a_table_row_keeps_its_visibility_and_reads_no_other_row():
    keys = list(make_excellon(make_app()).tools)
    assert len(keys) >= 3
    # the 2nd tool has no row; the 1st is checked, the 3rd is not
    checked = {k: (i == 0) for i, k in enumerate(keys)}
    obj, keys = _prepare(checked, without_row=(keys[1],))
    obj.shape_indexes_dict = {k: [100 + i] for i, k in enumerate(keys)}

    obj.on_plot_cb_click_table()

    expected = [(checked[k], [100 + i]) for i, k in enumerate(keys) if i != 1]
    assert obj.shapes.visibility_calls == expected
    assert obj.shapes.redraws == 1
    obj.ui_connect.assert_called_once()


def test_other_errors_are_not_hidden():
    obj = make_excellon(make_app())
    keys = list(obj.tools)
    obj, keys = _prepare({k: True for k in keys})
    obj.shape_indexes_dict = {k: [1] for k in keys}
    obj.shapes.update_visibility = Mock(side_effect=RuntimeError('boom'))

    with pytest.raises(RuntimeError):
        obj.on_plot_cb_click_table()
