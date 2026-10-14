"""Stage 4 (dispatch attributes): class attributes replace the kind -> constant chains.

The EXPECTED table is copied from the OLD chains:
  AppObject.new_object                      tools_drill_ / tools_iso_ prefix chain, the 'tools_mill_' kind list
  AppObject.on_object_created               status bar color chain, the ['excellon', 'gerber'] color gate
  ObjectCollection.on_list_selection_change status bar color chain
  AppPlotManager.on_set_color_action_triggered  alpha default chain, "Default" color chain, ["excellon", "gerber"] gate
Everything is headless: objects are built with the SimpleNamespace app of the characterization tests.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest
from PyQt6 import QtCore, QtGui, QtWidgets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from appObjects.AppObjectTemplate import FlatCAMObj  # noqa: E402
from appObjects.CNCJobObject import CNCJobObject  # noqa: E402
from appObjects.DocumentObject import DocumentObject  # noqa: E402
from appObjects.ExcellonObject import ExcellonObject  # noqa: E402
from appObjects.GeometryObject import GeometryObject  # noqa: E402
from appObjects.GerberObject import GerberObject  # noqa: E402
from appObjects.ObjectDispatch import dispatch_attr  # noqa: E402
from appObjects.ScriptObject import ScriptObject  # noqa: E402
# fixtures and helpers of the characterization tests (a fixture imported here is collected as ours)
from test_unify_characterization import _headless_qt, make_app  # noqa: E402,F401

CLASSES = {
    'gerber': GerberObject, 'excellon': ExcellonObject, 'geometry': GeometryObject,
    'cncjob': CNCJobObject, 'script': ScriptObject, 'document': DocumentObject,
}

ATTRS = ('status_color', 'tool_option_prefix', 'inherits_mill_options', 'has_plot_colors',
         'plot_default_fill_option', 'plot_default_line_option', 'alpha_fill_option', 'default_alpha_level')


def row(**k):
    base = dict.fromkeys(ATTRS)
    base['inherits_mill_options'] = False
    base['has_plot_colors'] = False
    base.update(k)
    return base


EXPECTED = {
    'gerber': row(status_color='green', tool_option_prefix='tools_iso_', inherits_mill_options=True,
                  has_plot_colors=True, plot_default_fill_option='gerber_plot_fill',
                  plot_default_line_option='gerber_plot_line', alpha_fill_option='gerber_plot_fill'),
    'excellon': row(status_color='brown', tool_option_prefix='tools_drill_', inherits_mill_options=True,
                    has_plot_colors=True, plot_default_fill_option='excellon_plot_fill',
                    plot_default_line_option='excellon_plot_line', alpha_fill_option='excellon_plot_fill'),
    'geometry': row(status_color='red', inherits_mill_options=True, plot_default_fill_option='geometry_plot_line',
                    plot_default_line_option='geometry_plot_line', default_alpha_level='FF'),
    'cncjob': row(status_color='blue', inherits_mill_options=True),
    'script': row(status_color='orange'),
    'document': row(status_color='darkCyan'),
}


class TestAttributeTable:
    @pytest.mark.parametrize('kind', sorted(CLASSES))
    def test_class_attributes_equal_the_old_chain_constants(self, kind):
        cls = CLASSES[kind]
        assert {a: getattr(cls, a) for a in ATTRS} == EXPECTED[kind]

    def test_base_class_default_is_the_old_fallthrough(self):
        # an unknown kind matched no branch of the old chains
        assert {a: getattr(FlatCAMObj, a) for a in ATTRS} == row()

    def test_every_attribute_is_declared_on_the_base_class(self):
        for a in ATTRS:
            assert a in FlatCAMObj.__dict__


class TestDispatchAttr:
    @pytest.mark.parametrize('kind', sorted(CLASSES))
    def test_stand_in_with_a_kind_gets_the_class_value(self, kind):
        stand_in = SimpleNamespace(kind=kind)
        assert {a: dispatch_attr(stand_in, a) for a in ATTRS} == EXPECTED[kind]

    def test_stand_in_with_an_unknown_kind_gets_the_default(self):
        for stand_in in (SimpleNamespace(kind='other'), SimpleNamespace()):
            assert {a: dispatch_attr(stand_in, a) for a in ATTRS} == row()

    def test_the_attribute_of_the_object_wins(self):
        stand_in = SimpleNamespace(kind='gerber', status_color='pink')
        assert dispatch_attr(stand_in, 'status_color') == 'pink'


# ---------------------------------------------------------------------------------------------------------------
# AppObject (new_object option copy, on_object_created status message and colors)
# ---------------------------------------------------------------------------------------------------------------

from collections import defaultdict  # noqa: E402
from appObjects.AppObject import AppObject  # noqa: E402

KINDS = sorted(CLASSES)
# ScriptObject / DocumentObject cannot be constructed at this commit: FlatCAMObj.__init__ passes app= to QObject
# (TypeError, not part of this change), so the tests that need a constructor skip them.
BUILDABLE = [k for k in KINDS if k not in ('script', 'document')]


def make_app_object():
    app = make_app()
    app.on_plots_updated = Mock()
    app.main_thread = QtCore.QThread.currentThread()
    app.edit_class = Mock()
    app.inform_shell = Mock()
    app.preprocessors = defaultdict(Mock)
    app.all_objects_list = []
    app.collection.group_items = {'gerber': Mock(child_count=Mock(return_value=1))}
    return app, AppObject(app)


def build_obj(kind, app, name='nm'):
    cls = CLASSES[kind]
    if kind in BUILDABLE:
        return cls(name, app)
    obj = cls.__new__(cls)
    QtCore.QObject.__init__(obj)
    obj.obj_options = {}
    obj.kind = kind
    return obj


def old_obj_options(kind, base, options):
    """obj_options as the OLD new_object chains filled them (in order), starting from the constructor state."""
    res = dict(base)
    for option in options:
        if option.find(kind + "_") == 0:
            res[option[len(kind) + 1:]] = options[option]
    if kind == 'excellon':
        for option in options:
            if option.find('tools_drill_') == 0:
                res[option] = options[option]
    if kind == 'gerber':
        for option in options:
            if option.find('tools_iso_') == 0:
                res[option] = options[option]
    if kind in ['excellon', 'gerber', 'geometry', 'cncjob']:
        for option in options:
            if option.find('tools_mill_') == 0:
                res[option] = options[option]
        for option in options:
            if option.find('tools_') == 0:
                res[option] = options[option]
    return res


class TestAppObjectNewObject:
    @pytest.mark.parametrize('kind', BUILDABLE)
    def test_options_copied_like_the_old_chains(self, kind, _headless_qt):
        app, app_obj = make_app_object()
        base = build_obj(kind, app, 'n').obj_options
        seen = []

        def initialize(obj, _app):
            seen.append(obj)
            return 'fail'

        assert app_obj.new_object(kind, 'n', initialize) == 'fail'
        got = seen[0].obj_options
        expected = old_obj_options(kind, base, app.options)
        assert got == expected
        assert list(got) == list(expected)
        if kind in ('gerber', 'excellon'):
            prefix = 'tools_iso_' if kind == 'gerber' else 'tools_drill_'
            assert any(k.startswith(prefix) for k in got)


class TestAppObjectOnObjectCreated:
    @pytest.mark.parametrize('kind', KINDS)
    def test_status_message_and_colors(self, kind, _headless_qt):
        app, app_obj = make_app_object()
        obj = build_obj(kind, app)
        obj.obj_options['name'] = 'nm'
        before = getattr(obj, 'fill_color', None)

        app_obj.on_object_created(obj, False, True, None, [])

        calls = [c.args[0] for c in app.inform.emit.call_args_list]
        assert calls == ['[selected] %s created/selected: <span style="color:%s;">nm</span>'
                         % (kind.capitalize(), EXPECTED[kind]['status_color'])]
        app.log.error.assert_not_called()
        if kind in ('excellon', 'gerber'):
            assert obj.fill_color == app.options[kind + '_plot_fill']
            assert obj.outline_color == app.options[kind + '_plot_line']
        else:
            assert getattr(obj, 'fill_color', None) == before


# ---------------------------------------------------------------------------------------------------------------
# ObjectCollection.on_list_selection_change (status message)
# ---------------------------------------------------------------------------------------------------------------

from appObjects.ObjectCollection import ObjectCollection  # noqa: E402


def selection_change(obj):
    """Run the slot unbound: it only touches self.app and self.item_selected."""
    holder = SimpleNamespace(app=Mock(), item_selected=Mock(), get_selected=lambda: [])
    node = SimpleNamespace(obj=obj)
    current = SimpleNamespace(indexes=lambda: [SimpleNamespace(internalPointer=lambda: node)])
    ObjectCollection.on_list_selection_change(holder, current, None)
    return holder


class TestObjectCollectionSelectionMessage:
    @pytest.mark.parametrize('kind', KINDS)
    def test_real_objects(self, kind, _headless_qt):
        app, _ = make_app_object()
        obj = build_obj(kind, app)
        obj.obj_options['name'] = 'nm'
        holder = selection_change(obj)
        holder.item_selected.emit.assert_called_once_with('nm')
        holder.app.inform.emit.assert_called_once_with(
            '[selected]<span style="color:%s;">nm</span> selected' % EXPECTED[kind]['status_color'])

    @pytest.mark.parametrize('kind', KINDS)
    def test_stand_in_objects_keep_the_message(self, kind):
        obj = SimpleNamespace(kind=kind, obj_options={'name': 'nm'})
        holder = selection_change(obj)
        holder.app.inform.emit.assert_called_once_with(
            '[selected]<span style="color:%s;">nm</span> selected' % EXPECTED[kind]['status_color'])

    def test_unknown_kind_emits_no_message(self):
        holder = selection_change(SimpleNamespace(kind='other', obj_options={'name': 'nm'}))
        holder.item_selected.emit.assert_called_once_with('nm')
        holder.app.inform.emit.assert_not_called()
