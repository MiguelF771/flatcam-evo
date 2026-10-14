"""Stage 1: the plugins inherit (Geometry, AppTool), not Gerber / Excellon.

Each plugin is built headless (offscreen Qt, MagicMock app) to prove that the base-class __init__ chain and the
Geometry state that the plugin code relies on are present.
"""

import importlib
import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402
from PyQt6.QtCore import QStringListModel  # noqa: E402

from appParsers.ParseExcellon import Excellon  # noqa: E402
from appParsers.ParseGerber import Gerber  # noqa: E402
from appTool import AppTool  # noqa: E402
from camlib import Geometry  # noqa: E402

PLUGINS = [
    ("appPlugins.ToolFollow", "ToolFollow"),
    ("appPlugins.ToolIsolation", "ToolIsolation"),
    ("appPlugins.ToolNCC.Ncc", "ToolNcc"),
    ("appPlugins.ToolPaint.Paint", "ToolPaint"),
]


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def make_tool_app():
    app = MagicMock()
    app.decimals = 4
    app.use_3d_engine = True
    app.app_units = "MM"
    app.collection = QStringListModel()
    app.resource_location = "assets/resources"
    app.options = {}
    app.defaults = {}
    return app


@pytest.mark.parametrize("module_name, class_name", PLUGINS)
def test_bases_are_geometry_and_apptool(module_name, class_name):
    cls = getattr(importlib.import_module(module_name), class_name)
    mro = cls.__mro__
    assert not issubclass(cls, (Gerber, Excellon))
    assert cls.__bases__ == (Geometry, AppTool)
    assert mro.index(Geometry) < mro.index(AppTool)


@pytest.mark.parametrize("module_name, class_name", PLUGINS)
def test_plugin_builds_headless(qapp, module_name, class_name):
    cls = getattr(importlib.import_module(module_name), class_name)
    tool = cls(make_tool_app())
    assert tool.geo_steps_per_circle is not None
    assert tool.temp_shapes is not None
    assert hasattr(tool, "ui")
