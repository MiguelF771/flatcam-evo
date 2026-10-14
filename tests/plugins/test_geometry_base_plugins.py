"""Headless construction of the plugins that must inherit Geometry (not Gerber / Excellon)."""
import os
import sys
from unittest.mock import Mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))

from PyQt6 import QtGui, QtWidgets

from camlib import Geometry
from appTool import AppTool
from test_unify_regressions_0b import make_app


@pytest.fixture(scope='module')
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _build(cls, qapp):
    app = make_app()
    app.ui = Mock()
    app.collection = QtGui.QStandardItemModel()
    app.dec_format = lambda value, dec=4: value
    app.resource_location = os.path.join(ROOT, 'assets', 'resources')
    plugin = cls(app)
    return plugin


@pytest.mark.parametrize('module, name', [
    ('appPlugins.ToolPunchGerber', 'ToolPunchGerber'),
    ('appPlugins.ToolDrilling', 'ToolDrilling'),
])
def test_plugin_bases_and_construction(qapp, module, name):
    import importlib
    cls = getattr(importlib.import_module(module), name)
    print(sys.modules[module].__file__)
    assert cls.__bases__ == (Geometry, AppTool)
    plugin = _build(cls, qapp)
    assert isinstance(plugin, Geometry)
    assert plugin.geo_steps_per_circle == (
        plugin.app.options['geometry_circle_steps'] if name == 'ToolPunchGerber'
        else plugin.app.options['excellon_circle_steps'])
