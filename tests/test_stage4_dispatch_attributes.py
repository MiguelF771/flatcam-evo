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
