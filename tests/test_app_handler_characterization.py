import inspect
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6 import QtCore, QtGui, QtWidgets
from PyQt6.QtCore import Qt
from shapely.geometry import LineString

import appHandlers.appLifecycle as lifecycle_module
import appHandlers.appUIActions as ui_module
import appHandlers.appObjectOps as object_module
import appHandlers.appCanvasEvents as canvas_module
import appHandlers.appSignalConnector as signal_module
from appHandlers.appLifecycle import AppLifecycle
from appHandlers.appUIActions import AppUIActions
from appHandlers.appObjectOps import AppObjectOps
from appHandlers.appCanvasEvents import AppCanvasEvents
from appHandlers.appSignalConnector import AppSignalConnector


FAMILY_CLASSES = {
    "lifecycle": AppLifecycle,
    "ui_actions": AppUIActions,
    "object_ops": AppObjectOps,
    "canvas_events": AppCanvasEvents,
    "signal_connector": AppSignalConnector,
}

METHOD_MANIFEST = {
    "lifecycle": (
        "on_options_value_changed", "on_app_restart", "on_layout", "final_save", "quit_application",
        "kill_app", "on_portable_checked", "on_defaults_dict_change", "on_defaults2options",
        "setup_obj_classes", "version_check", "start_delayed_quit", "check_project_file_size",
        "save_project_auto", "save_project_auto_update",
    ),
    "ui_actions": (
        "on_about", "on_howto", "install_bookmarks", "on_bookmarks_manager", "on_backup_site",
        "on_workspace_modified", "on_workspace", "on_workspace_toggle", "on_show_log", "on_cursor_type",
        "on_tool_add_keypress", "on_toggle_preferences", "on_preferences", "on_tools_database", "on_3d_area",
        "on_geometry_tool_add_from_db_executed", "on_plot_area_tab_closed",
        "on_plot_area_tab_double_clicked", "on_notebook_closed", "on_properties_tab_click",
        "on_notebook_tab_changed", "setup_default_properties_tab", "grid_status", "populate_cmenu_grids",
        "set_grid", "on_grid_add", "on_grid_delete",
    ),
    "object_ops": (
        "on_flipy", "on_flipx", "on_rotate", "on_skewx", "on_skewy", "on_set_origin",
        "on_set_zero_click", "on_move2origin", "on_jump_to", "on_locate", "on_numeric_move",
        "on_copy_command", "on_copy_object2", "on_rename_object", "on_delete_keypress", "on_delete",
        "delete_first_selected", "on_selectall", "on_deselect_all", "on_copy_name",
    ),
    "canvas_events": (
        "on_mouse_click_over_plot", "on_mouse_double_click_over_plot", "on_mouse_move_over_plot",
        "on_mouse_click_release_over_plot", "on_mouse_and_key_modifiers", "on_mouse_context_menu",
        "selection_area_handler", "select_objects", "selected_message", "on_plugin_mouse_click_release",
        "on_plugin_mouse_move", "delete_hover_shape", "draw_hover_shape", "delete_selection_shape",
        "draw_selection_shape", "draw_moving_selection_shape",
    ),
    "signal_connector": (
        "connect_filemenu_signals", "connect_editmenu_signals", "connect_optionsmenu_signals",
        "connect_menuview_signals", "connect_menuhelp_signals", "connect_project_context_signals",
        "connect_canvas_context_signals", "connect_tools_signals_to_toolbar",
        "connect_editors_toolbar_signals", "connect_toolbar_signals",
    ),
}

INVOCATION_LEDGER = {family: set() for family in METHOD_MANIFEST}

def invoke(family, target, method_name, *args, **kwargs):
    """Invoke the real handler implementation through the declared family surface."""
    if method_name not in METHOD_MANIFEST[family]:
        raise AssertionError("undeclared handler method: %s.%s" % (family, method_name))
    INVOCATION_LEDGER[family].add(method_name)
    return getattr(target, method_name)(*args, **kwargs)


def covers(family, *method_names):
    unknown = set(method_names) - set(METHOD_MANIFEST[family])
    if unknown:
        raise AssertionError("unknown coverage declaration: %s" % sorted(unknown))

    def decorate(test_method):
        test_method.handler_coverage = (family, frozenset(method_names))
        return test_method

    return decorate


class Signal:
    def __init__(self):
        self.slots = []
        self.emissions = []

    def __getitem__(self, _signature):
        return self

    def connect(self, slot, *args, **kwargs):
        self.slots.append(slot)

    def disconnect(self, slot=None):
        if slot is None:
            if not self.slots:
                raise TypeError("not connected")
            self.slots.clear()
        elif slot in self.slots:
            self.slots.remove(slot)
        else:
            raise TypeError("not connected")

    def emit(self, *args):
        self.emissions.append(args)
        for slot in list(self.slots):
            slot(*args)


class Recorder:
    def __init__(self, allowed=(), target=None, sink=None):
        self.allowed = frozenset(allowed)
        self.calls = []
        self.target = target
        self.sink = sink

    def __getattr__(self, name):
        if name not in self.allowed:
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if self.sink is not None:
                self.sink.append((self.target, name, args, kwargs))

        return record


class Value:
    def __init__(self, value=None):
        self.value = value
        self.calls = []

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value
        self.calls.append(("set_value", value))

    def setText(self, value):
        self.value = value
        self.calls.append(("setText", value))

    def text(self):
        return str(self.value)

    def setChecked(self, value):
        self.value = bool(value)
        self.calls.append(("setChecked", bool(value)))

    def isChecked(self):
        return bool(self.value)

    def setVisible(self, value):
        self.calls.append(("setVisible", value))

    def setDisabled(self, value):
        self.calls.append(("setDisabled", value))

    def setStyleSheet(self, value):
        self.calls.append(("setStyleSheet", value))

    def hide(self):
        self.calls.append(("hide",))

    def trigger(self):
        self.calls.append(("trigger",))


class Defaults(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.usage = []

    def report_usage(self, name):
        self.usage.append(name)


class Timer:
    def __init__(self, active=False):
        self.active = active
        self.interval = None
        self.stops = 0

    def isActive(self):
        return self.active

    def stop(self):
        self.active = False
        self.stops += 1

    def start(self):
        self.active = True

    def setInterval(self, interval):
        self.interval = interval


class Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class Action:
    def __init__(self, text="", parent=None):
        self.triggered = Signal()
        self.clicked = Signal()
        self._text = text
        self.checked = False

    def text(self):
        return self._text

    def setCheckable(self, _value):
        pass

    def setChecked(self, value):
        self.checked = value

    def setText(self, text):
        self._text = text

    def setIcon(self, _icon):
        pass


class Menu:
    def __init__(self, actions=()):
        self._actions = list(actions)

    def actions(self):
        return list(self._actions)

    def clear(self):
        self._actions.clear()

    def addAction(self, *args):
        action = Action(str(args[-1]))
        self._actions.append(action)
        return action

    def addSeparator(self):
        self._actions.append(Action("separator"))

    def insertAction(self, before, action):
        self._actions.insert(self._actions.index(before), action)

    def removeAction(self, action):
        self._actions.remove(action)


class Tabs:
    def __init__(self, labels=(), widgets=()):
        self.labels = list(labels)
        self.widgets = list(widgets)
        self.current = 0
        self.calls = []

    def count(self):
        return len(self.labels)

    def tabText(self, index):
        return self.labels[index]

    def widget(self, index):
        return self.widgets[index]

    def currentWidget(self):
        return self.widgets[self.current] if self.widgets else None

    def currentIndex(self):
        return self.current

    def addTab(self, widget, label):
        self.widgets.append(widget)
        self.labels.append(label)
        self.calls.append(("addTab", widget, label))

    def removeTab(self, index):
        self.calls.append(("removeTab", index))
        self.labels.pop(index)
        if index < len(self.widgets):
            self.widgets.pop(index)

    def setCurrentWidget(self, widget):
        self.calls.append(("setCurrentWidget", widget))


class ObjectStub:
    def __init__(self, name="obj", kind="geometry", bounds=(0, 0, 2, 2), plotted=True):
        self.kind = kind
        self._bounds = bounds
        self.obj_options = {
            "name": name, "plot": plotted, "xmin": bounds[0], "ymin": bounds[1],
            "xmax": bounds[2], "ymax": bounds[3],
        }
        self.visible = True
        self.selection_shape_drawn = False
        self.isHovering = False
        self.notHovering = False
        self.solid_geometry = [LineString([(bounds[0], bounds[1]), (bounds[2], bounds[3])])]
        self.calls = []

    def bounds(self):
        return self._bounds

    def mirror(self, *args):
        self.calls.append(("mirror", args))

    def rotate(self, *args, **kwargs):
        self.calls.append(("rotate", args, kwargs))

    def skew(self, *args, **kwargs):
        self.calls.append(("skew", args, kwargs))

    def offset(self, value):
        self.calls.append(("offset", value))

    def plot(self):
        self.calls.append(("plot",))

    def set_offset_values(self):
        self.calls.append(("set_offset_values",))

    def on_tool_add(self, **kwargs):
        self.calls.append(("on_tool_add", kwargs))

    def on_tool_delete(self):
        self.calls.append(("on_tool_delete",))

    def build_ui(self):
        self.calls.append(("build_ui",))


class Collection:
    def __init__(self, objects=(), selected=None):
        self.objects = list(objects)
        self.selected = list(self.objects if selected is None else selected)
        self.active = self.selected[0] if self.selected else None
        self.calls = []

    def get_selected(self):
        self.calls.append(("get_selected",))
        return list(self.selected)

    def get_active(self):
        return self.active

    def get_list(self):
        return list(self.objects)

    def get_names(self):
        return [obj.obj_options["name"] for obj in self.objects]

    def get_by_name(self, name):
        return next(obj for obj in self.objects if obj.obj_options["name"] == name)

    def set_active(self, name):
        self.active = self.get_by_name(name)
        if self.active not in self.selected:
            self.selected.append(self.active)
        self.calls.append(("set_active", name))

    def set_inactive(self, name):
        self.selected = [obj for obj in self.selected if obj.obj_options["name"] != name]
        self.active = self.selected[0] if self.selected else None

    def set_all_inactive(self):
        self.selected = []
        self.active = None
        self.calls.append(("set_all_inactive",))

    def on_objects_selection(self, state):
        self.calls.append(("on_objects_selection", state))
        if not state:
            self.set_all_inactive()

    def delete_active(self):
        if self.active in self.objects:
            self.objects.remove(self.active)
        if self.active in self.selected:
            self.selected.remove(self.active)
        self.active = self.selected[0] if self.selected else None
        self.calls.append(("delete_active",))


class Shapes:
    def __init__(self):
        self.calls = []

    def add(self, geometry, **kwargs):
        token = object()
        self.calls.append(("add", geometry, kwargs, token))
        return token

    def clear(self, *args, **kwargs):
        self.calls.append(("clear", args, kwargs))

    def redraw(self):
        self.calls.append(("redraw",))


class MessageBox:
    created = []

    def __init__(self, parent=None):
        self.calls = [("parent", parent)]
        self.buttons = []
        self.response = None
        type(self).created.append(self)

    def setWindowTitle(self, title):
        self.calls.append(("setWindowTitle", (title,)))

    def setWindowIcon(self, icon):
        self.calls.append(("setWindowIcon", (icon,)))

    def setText(self, text):
        self.calls.append(("setText", (text,)))

    def setInformativeText(self, text):
        self.calls.append(("setInformativeText", (text,)))

    def setIconPixmap(self, pixmap):
        self.calls.append(("setIconPixmap", (pixmap,)))

    def setDefaultButton(self, button):
        self.calls.append(("setDefaultButton", (button,)))

    def addButton(self, text, role):
        button = object()
        self.buttons.append((text, role, button))
        if self.response is None:
            self.response = button
        return button

    def exec(self):
        self.calls.append(("exec",))

    def clickedButton(self):
        return self.response


class AppTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestLifecycle(AppTestCase):
    def make_handler(self):
        log = Recorder(("debug", "error", "warning"))
        inform = Signal()
        defaults = Defaults()
        options = {
            "global_gridx": 1.0, "global_gridy": 2.0, "global_snap_max": 0.5,
            "global_autosave": True, "global_autosave_timeout": 321,
            "global_serial": "serial", "global_stats": {},
        }
        app = SimpleNamespace(log=log, inform=inform, defaults=defaults, options=options)
        return app, AppLifecycle(app)

    @covers("lifecycle", *(set(METHOD_MANIFEST["lifecycle"]) - {"quit_application"}))
    def test_lifecycle_non_shutdown_behaviors(self):
        app, handler = self.make_handler()
        properties_click = Recorder(("call",))
        app.on_properties_tab_click = properties_click.call
        invoke("lifecycle", handler, "on_options_value_changed", "global_axis")
        self.assertEqual(1, len(properties_click.calls))

        app.trayIcon = Recorder(("hide",))
        with patch("appTranslation.restart_program") as restart:
            invoke("lifecycle", handler, "on_app_restart")
        restart.assert_called_once_with(app=app)
        self.assertEqual("hide", app.trayIcon.calls[0][0])

        class ToolbarUI(SimpleNamespace):
            def removeToolBar(self, toolbar):
                self.removed.append(toolbar)

            def addToolBar(self, *args, **kwargs):
                self.added.append((args, kwargs))

            def addToolBarBreak(self, *args, **kwargs):
                self.breaks += 1

        old = [object() for _ in range(8)]
        app.ui = ToolbarUI(
            removed=[], added=[], breaks=0, toolbarfile=old[0], toolbaredit=old[1], toolbarview=old[2],
            toolbarshell=old[3], toolbarplugins=old[4], exc_edit_toolbar=old[5], geo_edit_toolbar=old[6],
            grb_edit_toolbar=old[7], populate_toolbars=Recorder(("call",)).call,
            lock_toolbar=Recorder(("call",)).call, grid_snap_btn=Value(), corner_snap_btn=Value(),
            snap_magnet=Value(), grid_gap_x_entry=Value(), grid_gap_y_entry=Value(),
            snap_max_dist_entry=Value(), grid_gap_link_cb=Value(),
        )
        app.connect_editors_toolbar_signals = Recorder(("call",)).call
        invoke("lifecycle", handler, "on_layout", lay="minimal", connect_signals=False)
        self.assertEqual(8, len(app.ui.added))
        self.assertTrue(app.ui.grid_snap_btn.value)
        self.assertEqual("1.0", app.ui.grid_gap_x_entry.value)

        app.save_in_progress = True
        invoke("lifecycle", handler, "final_save")
        self.assertIn("saving", app.inform.emissions[-1][0].lower())

        with self.assertRaises(SystemExit) as raised:
            with patch.object(lifecycle_module.QtCore.QCoreApplication, "instance", return_value=SimpleNamespace(quit=lambda: None)):
                invoke("lifecycle", handler, "kill_app")
        self.assertEqual(0, raised.exception.code)

        prefs = Recorder(("defaults_write_form_field", "defaults_read_form", "save_defaults"))
        app.preferencesUiManager = prefs
        with tempfile.TemporaryDirectory() as temp_dir:
            app_root = Path(temp_dir) / "FlatCAM"
            config_dir = app_root / "config"
            config_dir.mkdir(parents=True)
            config_file = config_dir / "configuration.txt"
            config_file.write_text("portable=False\nother=value\n", encoding="utf-8")
            fake_source = app_root / "appHandlers" / "appLifecycle.py"
            with patch.object(lifecycle_module.sys, "platform", "win32"), \
                    patch.object(lifecycle_module.os.path, "realpath", return_value=str(fake_source)):
                invoke("lifecycle", handler, "on_portable_checked", Qt.CheckState.Checked)
            self.assertEqual("portable=True\nother=value\n", config_file.read_text(encoding="utf-8"))
            self.assertEqual({}, json.loads((config_dir / "current_defaults.FlatConfig").read_text()))
            self.assertEqual({}, json.loads((config_dir / "factory_defaults.FlatConfig").read_text()))
            self.assertEqual([], json.loads((config_dir / "recent.json").read_text()))
            self.assertEqual([], json.loads((config_dir / "recent_projects.json").read_text()))
            self.assertIn(("save_defaults", (), {"silent": True, "data_path": str(config_dir)}), prefs.calls)

        invoke("lifecycle", handler, "on_defaults_dict_change", "units")
        handler.defaults.update({"answer": 42})
        invoke("lifecycle", handler, "on_defaults2options")
        self.assertEqual(42, handler.options["answer"])
        self.assertIn(("defaults_write_form_field", (), {"field": "units"}), prefs.calls)

        from appGUI.preferences.OptionsGroupUI import OptionsGroupUI
        previous_app = OptionsGroupUI.app
        try:
            invoke("lifecycle", handler, "setup_obj_classes")
            self.assertIs(app, OptionsGroupUI.app)
        finally:
            OptionsGroupUI.app = previous_app

        app.ui = SimpleNamespace(
            general_pref_form=SimpleNamespace(
                general_app_group=SimpleNamespace(send_stats_cb=Value(False))
            )
        )
        app.version_url = "https://example.invalid/version"
        app.version = 1
        app.os = "test"
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            invoke("lifecycle", handler, "version_check")
        self.assertIn("Failed checking", app.inform.emissions[-1][0])

        invoke("lifecycle", handler, "start_delayed_quit", 1000, "project.flatprj", True)
        self.assertEqual(1000, app.save_timer.interval())
        app.save_timer.stop()

        app.save_in_progress = True
        app.save_timer = Timer(active=True)
        app.app_quit = Signal()
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(b"project")
            temp_name = temp_file.name
        try:
            invoke("lifecycle", handler, "check_project_file_size", temp_name, True)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        self.assertFalse(app.save_in_progress)
        self.assertEqual([()], app.app_quit.emissions)

        app.block_autosave = False
        app.should_we_save = True
        app.save_in_progress = False
        app.f_handlers = Recorder(("on_file_save_project",))
        invoke("lifecycle", handler, "save_project_auto")
        self.assertEqual("on_file_save_project", app.f_handlers.calls[-1][0])

        app.autosave_timer = Timer(active=True)
        invoke("lifecycle", handler, "save_project_auto_update")
        self.assertTrue(app.autosave_timer.active)
        self.assertEqual(321, app.autosave_timer.interval)

    @covers("lifecycle", "quit_application")
    def test_quit_application_disconnects_2d_and_3d_canvas_paths(self):
        for use_3d in (False, True):
            with self.subTest(use_3d=use_3d):
                app, handler = self.make_handler()
                plot_calls = []

                class PlotCanvas:
                    def graph_event_disconnect(self, *args):
                        plot_calls.append(args)
                        return "disconnected"

                    def close(self):
                        plot_calls.append(("close",))

                app.preferencesUiManager = Recorder(("save_defaults",))
                app.ui = SimpleNamespace(hide=Recorder(("call",)).call)
                app.autosave_timer = Timer(active=True)
                app.geo_editor = app.exc_editor = app.grb_editor = app.gcode_editor = None
                app.use_3d_engine = use_3d
                app.plotcanvas = PlotCanvas()
                app.on_mouse_move_over_plot = object()
                app.on_mouse_click_over_plot = object()
                app.on_mouse_click_release_over_plot = object()
                app.on_mouse_double_click_over_plot = object()
                app.ui.keyPressEvent = object()
                app.mm, app.mp, app.mr, app.mdc, app.kp = "mm", "mp", "mr", "mdc", "kp"
                app.cmd_line_headless = 1
                app.pool = Recorder(("terminate", "join"))
                app.workers = Recorder(("quit",))
                with patch.object(lifecycle_module.sys, "platform", "linux"), \
                        patch.object(QtWidgets.QApplication, "quit"):
                    invoke("lifecycle", handler, "quit_application", silent=True)
                if use_3d:
                    self.assertEqual(["mouse_move", "mouse_press", "mouse_release", "mouse_double_click", "key_press"],
                                     [call[0] for call in plot_calls[:5]])
                else:
                    self.assertEqual([("mm",), ("mp",), ("mr",), ("mdc",), ("kp",)], plot_calls[:5])
                self.assertEqual("quit", app.workers.calls[-1][0])


class TestUIActions(AppTestCase):
    def make_handler(self):
        defaults = Defaults()
        options = {
            "global_bookmarks": {}, "global_bookmarks_limit": 2, "global_workspaceT": "A4",
            "global_pan_button": "2", "global_coords_bar_show": True, "global_delta_coords_bar_show": False,
            "global_grid_context_menu": {"mm": [1.0, 0.5]}, "global_theme": "light",
            "global_grid_lines": True, "global_grid_snap": True, "global_axis": False,
            "global_workspace": False, "global_workspace_orientation": "p", "global_hud": False,
        }
        app = SimpleNamespace(log=Recorder(("debug", "error")), inform=Signal(), defaults=defaults, options=options)
        app.resource_location = ""
        app.version = 1
        app.version_date = "today"
        app.beta = False
        app.ui = SimpleNamespace()
        return app, AppUIActions(app)

    @covers("ui_actions", "on_about", "on_howto", "install_bookmarks", "on_bookmarks_manager", "on_backup_site")
    def test_dialog_and_bookmark_routes(self):
        app, handler = self.make_handler()

        class StopDialog(Exception):
            pass

        class DialogBase:
            def __init__(self, parent=None):
                raise StopDialog(parent)

        app.ui = SimpleNamespace(app_icon=object())
        handler.ui = app.ui
        for method in ("on_about", "on_howto"):
            with self.subTest(method=method), patch.object(ui_module.QtWidgets, "QDialog", DialogBase):
                with self.assertRaises(StopDialog) as raised:
                    invoke("ui_actions", handler, method)
                self.assertIs(app.ui, raised.exception.args[0])

        manager_action = Action("manager")
        app.ui = SimpleNamespace(menuhelp_bookmarks=Menu([manager_action]), menuhelp_bookmarks_manager=manager_action)
        handler.ui = app.ui
        with patch.object(ui_module.webbrowser, "open") as open_url, patch.object(ui_module.QtGui, "QAction", Action):
            invoke("ui_actions", handler, "install_bookmarks", {
                "1": ["One", "https://one.invalid"], "2": ["Two", "https://two.invalid"],
                "3": ["Three", "https://three.invalid"],
            })
            generated = [act for act in app.ui.menuhelp_bookmarks.actions() if act is not manager_action]
            self.assertEqual(["One", "Two"], [act.text() for act in generated])
            generated[0].triggered.emit()
            open_url.assert_called_once_with("https://one.invalid")

        class Bookmark:
            def __init__(self, app, storage, parent):
                self.init = (app, storage, parent)
                self.name = None

            def setObjectName(self, name):
                self.name = name

        app.ui = SimpleNamespace(plot_tab_area=Tabs(), coords_toolbar=Value(), delta_coords_toolbar=Value())
        handler.ui = app.ui
        with patch.object(ui_module, "BookmarkManager", Bookmark):
            invoke("ui_actions", handler, "on_bookmarks_manager")
        self.assertEqual("bookmarks_tab", app.book_dialog_tab.name)
        self.assertEqual("setCurrentWidget", app.ui.plot_tab_area.calls[-1][0])

        MessageBox.created.clear()
        app.ui = object()
        handler.ui = app.ui
        with patch.object(ui_module, "FCMessageBox", MessageBox):
            invoke("ui_actions", handler, "on_backup_site")
        self.assertIn(("exec",), MessageBox.created[-1].calls)
        with self.assertRaises(AttributeError):
            MessageBox.created[-1].setWindwTitle("misspelled")

    @covers(
        "ui_actions", "on_workspace_modified", "on_workspace", "on_workspace_toggle", "on_show_log",
        "on_cursor_type", "on_tool_add_keypress", "on_toggle_preferences", "on_preferences", "on_tools_database",
        "on_3d_area", "on_geometry_tool_add_from_db_executed",
    )
    def test_workspace_cursor_preferences_and_database_routes(self):
        app, handler = self.make_handler()
        workspace_cb = Value(True)
        workspace_cb.stateChanged = Signal()
        cursor_entry, cursor_label = Value(), Value()
        app.ui = SimpleNamespace(
            general_pref_form=SimpleNamespace(general_app_set_group=SimpleNamespace(
                workspace_cb=workspace_cb, cursor_size_entry=cursor_entry, cursor_size_lbl=cursor_label)),
            grid_snap_btn=Value(True),
        )
        handler.ui = app.ui
        app.plotcanvas = Recorder(("delete_workspace", "draw_workspace", "new_cursor"))
        app.preferencesUiManager = Recorder(("defaults_read_form",))
        app.on_workspace = handler.on_workspace
        invoke("ui_actions", handler, "on_workspace_modified")
        invoke("ui_actions", handler, "on_workspace")
        invoke("ui_actions", handler, "on_workspace_toggle")
        self.assertFalse(workspace_cb.value)
        self.assertEqual(2, len([call for call in app.plotcanvas.calls if call[0] == "draw_workspace"]))

        class CursorCanvas:
            def __init__(self):
                self.big = []

            def new_cursor(self, big=False):
                self.big.append(big)
                return SimpleNamespace(enabled=False)

        app.plotcanvas = CursorCanvas()
        app.app_cursor = SimpleNamespace(enabled=True)
        invoke("ui_actions", handler, "on_cursor_type", "big", control_cursor=False)
        self.assertEqual([True], app.plotcanvas.big)
        self.assertTrue(app.app_cursor.enabled)

        app.log_path = lambda: "flatcam.log"
        with patch("sys.platform", "linux"), patch("subprocess.Popen") as popen:
            invoke("ui_actions", handler, "on_show_log")
        popen.assert_called_once_with(["xdg-open", "flatcam.log"])

        app.app_units = "mm"
        app.units = None
        app.ui = SimpleNamespace(notebook=Tabs())
        handler.ui = app.ui
        invoke("ui_actions", handler, "on_tool_add_keypress")
        self.assertEqual("MM", app.units)

        preferences = object()
        app.ui = SimpleNamespace(
            plot_tab_area=Tabs(["Preferences"], [preferences]), pref_status_label=Value(),
            preferences_tab=preferences, coords_toolbar=Value(), delta_coords_toolbar=Value(),
            pref_tab_area=Tabs(),
        )
        handler.ui = app.ui
        app.preferencesUiManager = Recorder(("clear_preferences_gui", "show_preferences_gui", "on_preferences_edited"))
        invoke("ui_actions", handler, "on_toggle_preferences")
        self.assertEqual("clear_preferences_gui", app.preferencesUiManager.calls[-1][0])
        invoke("ui_actions", handler, "on_preferences")
        self.assertEqual("show_preferences_gui", app.preferencesUiManager.calls[-1][0])

        app.tools_database_path = lambda: str(Path(tempfile.gettempdir()) / "missing-flatcam-tools-db")
        app.data_path = "data"
        self.assertEqual("fail", invoke("ui_actions", handler, "on_tools_database"))

        app.use_3d_engine = False
        self.assertIsNone(invoke("ui_actions", handler, "on_3d_area"))
        self.assertIn("Legacy 2D", app.inform.emissions[-1][0])

        app.collection = Collection()
        invoke("ui_actions", handler, "on_geometry_tool_add_from_db_executed", {"data": {"tool_target": 0}})
        self.assertIn("No object", app.inform.emissions[-1][0])

    @covers(
        "ui_actions", "on_plot_area_tab_closed", "on_plot_area_tab_double_clicked", "on_notebook_closed",
        "on_properties_tab_click", "on_notebook_tab_changed", "setup_default_properties_tab", "grid_status",
        "populate_cmenu_grids", "set_grid", "on_grid_add", "on_grid_delete",
    )
    def test_tab_property_and_grid_behaviors(self):
        app, handler = self.make_handler()
        app.preferencesUiManager = Recorder(("on_close_preferences_tab",))
        notebook_toggle = Recorder(("call",))
        app.ui = SimpleNamespace(toggle_coords=Recorder(("call",)).call,
                                 toggle_delta_coords=Recorder(("call",)).call,
                                 on_toggle_notebook=notebook_toggle.call)
        handler.ui = app.ui
        invoke("ui_actions", handler, "on_plot_area_tab_closed", "text_editor_tab")
        self.assertFalse(app.toggle_codeeditor)
        invoke("ui_actions", handler, "on_plot_area_tab_double_clicked")
        self.assertEqual(1, len(notebook_toggle.calls))

        project = QtWidgets.QWidget()
        project.setObjectName("project_tab")
        plugin = QtWidgets.QWidget()
        plugin.setObjectName("plugin_tab")
        properties = QtWidgets.QWidget()
        properties.setObjectName("properties_tab")
        app.ui = SimpleNamespace(
            notebook=Tabs(["Project", "Tool"], [project, plugin]), properties_tab=properties,
        )
        handler.ui = app.ui
        app.proj_selection_changed = Signal()
        app.levelling_tool = SimpleNamespace(probing_shapes=Shapes())
        app.tool_shapes = Shapes()
        app.app_plugins = []

        class Scroll(QtWidgets.QWidget):
            pass

        with patch.object(ui_module, "VerticalScrollArea", Scroll):
            invoke("ui_actions", handler, "on_notebook_closed")
        self.assertEqual("plugin_tab", app.ui.plugin_tab.objectName())

        default_widget = QtWidgets.QWidget()
        default_widget.setObjectName("default_properties")
        setup_properties = Recorder(("call",))
        app.setup_default_properties_tab = setup_properties.call
        app.ui.properties_scroll_area = SimpleNamespace(widget=lambda: default_widget)
        invoke("ui_actions", handler, "on_properties_tab_click")
        self.assertEqual(1, len(setup_properties.calls))

        app.ui.notebook = Tabs(["Project"], [project])
        app.collection = Collection()
        invoke("ui_actions", handler, "on_notebook_tab_changed")
        self.assertEqual(1, len(setup_properties.calls))

        class Tree:
            def __init__(self, columns):
                self.name = None
                self.rows = []

            def setObjectName(self, name):
                self.name = name

            def setSizePolicy(self, *args):
                pass

            def setStyleSheet(self, *args):
                pass

            def invisibleRootItem(self):
                return object()

            def addParent(self, *args, **kwargs):
                return object()

            def addChild(self, *args, **kwargs):
                self.rows.append(kwargs["title"])

        properties_area = SimpleNamespace(placed=None, taken=0)
        properties_area.takeWidget = lambda: setattr(properties_area, "taken", properties_area.taken + 1)
        properties_area.setWidget = lambda widget: setattr(properties_area, "placed", widget)
        app.ui.properties_scroll_area = properties_area
        app.ui.grid_gap_x_entry = Value(1.0)
        app.ui.grid_gap_y_entry = Value(2.0)
        with patch("appGUI.GUIElements.FCTree", Tree):
            invoke("ui_actions", handler, "setup_default_properties_tab")
        # the old widget is taken out before the new one is set, so Qt does not delete an object UI
        self.assertEqual(1, properties_area.taken)
        self.assertEqual("default_properties", properties_area.placed.name)
        self.assertGreaterEqual(len(properties_area.placed.rows), 10)

        app.ui.grid_snap_btn = Value(True)
        self.assertTrue(invoke("ui_actions", handler, "grid_status"))
        app.app_units = "MM"
        app.ui.cmenu_gridmenu = Menu()
        invoke("ui_actions", handler, "populate_cmenu_grids")
        self.assertEqual(["Grid On/Off", "separator", "0.5", "1.0", "separator", "Add", "Delete"],
                         [action.text() for action in app.ui.cmenu_gridmenu.actions()])

        action = QtGui.QAction("2.5")
        app.sender = lambda: action
        app.ui.grid_gap_x_entry = Value()
        app.ui.grid_gap_y_entry = Value()
        invoke("ui_actions", handler, "set_grid")
        self.assertEqual("2.5", app.ui.grid_gap_x_entry.value)

        class Spinner:
            values = [(2.5, True), (0.5, True)]

            def __init__(self, **kwargs):
                pass

            def setWindowIcon(self, icon):
                pass

            def get_value(self):
                return self.values.pop(0)

        app.decimals = 4
        app.ui = object()
        handler.ui = app.ui
        with patch.object(ui_module, "FCInputDoubleSpinner", Spinner):
            invoke("ui_actions", handler, "on_grid_add")
            invoke("ui_actions", handler, "on_grid_delete")
        self.assertIn(2.5, app.options["global_grid_context_menu"]["mm"])
        self.assertNotIn(0.5, app.options["global_grid_context_menu"]["mm"])


class TestObjectOps(AppTestCase):
    def make_handler(self, objects=(), selected=None):
        app = SimpleNamespace(
            log=Recorder(("debug", "error", "warning")), inform=Signal(), defaults=Defaults(),
            options={
                "tools_transform_rotate": 10, "tools_transform_skew_x": 5, "tools_transform_skew_y": 6,
                "global_delete_confirmation": False, "global_selection_shape": True,
                "global_cursor_color_enabled": False, "global_cursor_width": 1, "global_cursor_size": 10,
            },
            ui=SimpleNamespace(), collection=Collection(objects, selected),
        )
        app.resource_location = ""
        return app, AppObjectOps(app)

    @covers(
        "object_ops", "on_flipy", "on_flipx", "on_rotate", "on_skewx", "on_skewy", "on_set_origin",
        "on_set_zero_click", "on_move2origin",
    )
    def test_transforms_and_origin_paths(self):
        first = ObjectStub("first", bounds=(0, 0, 2, 2))
        second = ObjectStub("second", bounds=(2, 2, 4, 4))
        app, handler = self.make_handler([first, second])
        app.app_obj = SimpleNamespace(object_changed=Signal())
        invoke("object_ops", handler, "on_flipy")
        invoke("object_ops", handler, "on_flipx")
        invoke("object_ops", handler, "on_rotate", silent=True, preset=90)
        self.assertIn(("mirror", ("X", [2.0, 2.0])), first.calls)
        self.assertIn(("mirror", ("Y", [2.0, 2.0])), first.calls)
        self.assertIn(("rotate", (-90.0,), {"point": (2.0, 2.0)}), first.calls)

        class RotateSpinner:
            def __init__(self, **kwargs):
                self.initial = kwargs["init_val"]

            def setWindowIcon(self, icon):
                pass

            def get_value(self):
                return 45, True

        with patch("appGUI.GUIElements.FCInputDoubleSpinner", RotateSpinner):
            invoke("object_ops", handler, "on_rotate", silent=False)
        self.assertIn(("rotate", (-45.0,), {"point": (2.0, 2.0)}), first.calls)

        class Spinner:
            values = [(5, True), (6, True)]

            def __init__(self, **kwargs):
                pass

            def setWindowIcon(self, icon):
                pass

            def get_value(self):
                return self.values.pop(0)

        with patch("appGUI.GUIElements.FCInputDoubleSpinner", Spinner):
            invoke("object_ops", handler, "on_skewx")
            invoke("object_ops", handler, "on_skewy")
        self.assertIn(("skew", (5, 0), {"point": (0, 0)}), first.calls)
        self.assertIn(("skew", (0, 6), {"point": (0, 0)}), first.calls)

        app.connect_custom_signal = Recorder(("call",)).call
        app.plotcanvas = Recorder(("graph_event_connect", "graph_event_disconnect", "fit_view"))
        app.replot_signal = Signal()
        app.worker_task = Signal()
        app.custom_signal = Signal()
        app.proc_container = SimpleNamespace(new=lambda *args: Context())
        app.use_3d_engine = True
        app.inhibit_context_menu = False
        invoke("object_ops", handler, "on_set_origin")
        self.assertTrue(app.inhibit_context_menu)
        self.assertEqual("graph_event_connect", app.plotcanvas.calls[-1][0])

        self.assertEqual("fail", invoke("object_ops", handler, "on_set_zero_click", None, location=(1,), use_thread=False))

        empty_app, empty_handler = self.make_handler([], [])
        empty_app.proc_container = SimpleNamespace(new=lambda *args: Context())
        empty_app.worker_task = Signal()
        empty_app.app_obj = SimpleNamespace(object_changed=Signal())
        invoke("object_ops", empty_handler, "on_move2origin", use_thread=False)
        self.assertIn("No object", empty_app.inform.emissions[-1][0])

    @covers(
        "object_ops", "on_jump_to", "on_locate", "on_numeric_move", "on_copy_command", "on_copy_object2",
        "on_rename_object", "on_delete_keypress", "on_delete", "delete_first_selected", "on_selectall",
        "on_deselect_all", "on_copy_name",
    )
    def test_navigation_copy_delete_and_selection_paths(self):
        obj = ObjectStub("board", kind="geometry")
        app, handler = self.make_handler([obj])
        app.clipboard = SimpleNamespace(value="", text=lambda: "", setText=lambda value: setattr(app.clipboard, "value", value))
        app.use_3d_engine = True
        app.plotcanvas = Recorder(("fit_center",))
        app.plotcanvas.native = SimpleNamespace(mapToGlobal=lambda point: QtCore.QPoint(0, 0))
        app.plotcanvas.translate_coords_2 = lambda pos: pos
        app.plotcanvas.cursor_color = "black"
        app.grid_status = lambda: False
        app.jump_signal = Signal()
        app.locate_signal = Signal()
        app.rel_point1 = (0, 0)
        app.ui.update_location_labels = Recorder(("call",)).call
        app.plotcanvas.on_update_text_hud = Recorder(("call",)).call
        app.cursor_color_3D = "black"
        app.app_cursor = Recorder(("set_data",))

        class Cursor:
            positions = []

            def setPos(self, *args):
                self.positions.append(args)

        with patch.object(object_module.QtGui, "QCursor", Cursor):
            result = invoke("object_ops", handler, "on_jump_to", custom_location=(3, 4), fit_center=True)
        self.assertEqual((3, 4), result)
        self.assertEqual([((3, 4),)], app.jump_signal.emissions)

        self.assertEqual("fail", invoke("object_ops", handler, "on_locate", None))
        app.collection.selected = []
        invoke("object_ops", handler, "on_numeric_move", (1, 2))
        self.assertIn("Nothing selected", app.inform.emissions[-1][0])

        cnc = ObjectStub("job", kind="cncjob")
        app.collection = Collection([cnc])
        invoke("object_ops", handler, "on_copy_command")
        self.assertIn("cannot be copied", app.inform.emissions[-1][0])

        app.collection = Collection([], [])
        before_queries = len(app.collection.calls)
        invoke("object_ops", handler, "on_copy_object2", "_copy")
        self.assertEqual(before_queries + 1, len(app.collection.calls))

        app.collection = Collection([obj])
        invoke("object_ops", handler, "on_rename_object", "renamed")
        self.assertEqual("renamed", obj.obj_options["name"])

        properties = QtWidgets.QWidget()
        properties.setObjectName("properties_tab")
        app.ui.notebook = Tabs(["Properties"], [properties])
        invoke("object_ops", handler, "on_delete_keypress")
        self.assertIn(("on_tool_delete",), obj.calls)

        app.call_source = "app"
        app.delete_selection_shape = Recorder(("call",)).call
        app.exc_areas = Recorder(("clear_shapes",))
        app.tool_shapes = None
        app.setup_default_properties_tab = Recorder(("call",)).call
        app.use_3d_engine = True
        invoke("object_ops", handler, "on_delete", force_deletion=True)
        self.assertEqual([], app.collection.objects)

        survivor = ObjectStub("survivor")
        app.collection = Collection([survivor])
        invoke("object_ops", handler, "delete_first_selected", survivor)
        self.assertEqual([], app.collection.objects)

        a, b = ObjectStub("a"), ObjectStub("b")
        app.collection = Collection([a, b], [])
        app.draw_selection_shape = Recorder(("call",)).call
        invoke("object_ops", handler, "on_selectall")
        self.assertEqual(["a", "b"], [call[1] for call in app.collection.calls if call[0] == "set_active"])
        invoke("object_ops", handler, "on_deselect_all")
        self.assertEqual([], app.collection.selected)

        app.collection = Collection([a])
        invoke("object_ops", handler, "on_copy_name")
        self.assertEqual("a", app.clipboard.value)


class TestCanvasEvents(AppTestCase):
    def make_handler(self, objects=()):
        project = QtWidgets.QWidget()
        project.setObjectName("project_tab")
        plugin = QtWidgets.QWidget()
        plugin.setObjectName("plugin_tab")
        notebook = Tabs(["Project"], [project])
        app = SimpleNamespace(
            log=Recorder(("debug", "error")), inform=Signal(),
            options={
                "global_pan_button": "2", "global_cursor_width": 1, "global_cursor_size": 10,
                "global_selection_shape": True, "global_hover_shape": False,
                "global_alt_sel_line": "#112233FF", "global_alt_sel_fill": "#223344FF",
                "global_sel_line": "#334455FF", "global_sel_fill": "#445566FF",
                "global_selection_shape_as_line": False, "global_point_clipboard_format": "%.*f, %.*f",
                "global_mselect_key": "Control",
            },
            ui=SimpleNamespace(
                shell_dock=SimpleNamespace(isVisible=lambda: False),
                popMenu=SimpleNamespace(mouse_is_panning=False, popup_active=False), notebook=notebook,
                plugin_tab=plugin, project_tab=project,
            ),
            collection=Collection(objects), all_objects_list=list(objects), app_plugins=[],
        )
        app.use_3d_engine = False
        app.rel_point1 = None
        app.mouse_down = False
        app.event_is_dragging = False
        app.call_source = "app"
        app.selection_type = None
        app.mouse_click_pos = [0, 0]
        app.mouse_pos = [0, 0]
        app.doubleclick = False
        app.command_active = None
        app.click_noproject = False
        app.objects_under_the_click_list = []
        app.inhibit_context_menu = False
        app.decimals = 2
        app.dec_format = lambda value, decimals: round(value, decimals)
        app.app_units = "MM"
        app.sel_shapes = Shapes()
        app.hover_shapes = Shapes()
        app.sel_objects_list = []
        app.geo_editor = SimpleNamespace(snap=lambda x, y: (x, y))
        app.grid_status = lambda: False
        app.clipboard = SimpleNamespace(value="", text=lambda: app.clipboard.value,
                                        setText=lambda value: setattr(app.clipboard, "value", value))

        class Native:
            def __init__(self):
                self.focus = False

            def setFocus(self):
                self.focus = True

            def hasFocus(self):
                return self.focus

        app.plotcanvas = SimpleNamespace(
            native=Native(), translate_coords=lambda pos: pos, cursor_color="black",
            on_update_text_hud=Recorder(("call",)).call,
        )
        app.ui.update_location_labels = Recorder(("call",)).call
        return app, AppCanvasEvents(app)

    @covers(
        "canvas_events", "on_mouse_click_over_plot", "on_mouse_double_click_over_plot", "on_mouse_move_over_plot",
        "on_mouse_click_release_over_plot", "on_mouse_and_key_modifiers", "on_mouse_context_menu",
        "on_plugin_mouse_click_release", "on_plugin_mouse_move",
    )
    def test_mouse_modifier_context_and_plugin_routes(self):
        app, handler = self.make_handler()
        event = SimpleNamespace(button=1, xdata=2.0, ydata=3.0)
        original_move = handler.on_mouse_move_over_plot
        move_calls = []
        handler.on_mouse_move_over_plot = lambda *args, **kwargs: move_calls.append((args, kwargs))
        invoke("canvas_events", handler, "on_mouse_click_over_plot", event)
        handler.on_mouse_move_over_plot = original_move
        self.assertEqual([2.0, 3.0], app.mouse_click_pos)
        self.assertTrue(app.plotcanvas.native.focus)
        self.assertTrue(move_calls[0][1]["origin_click"])

        invoke("canvas_events", handler, "on_mouse_double_click_over_plot", event)
        self.assertTrue(app.doubleclick)

        app.rel_point1 = None
        invoke("canvas_events", handler, "on_mouse_move_over_plot", event)
        self.assertEqual((2.0, 3.0), app.pos_jump)

        app.call_source = "plugin"
        app.mouse_click_pos = [0, 0]
        with patch.object(QtWidgets.QApplication, "keyboardModifiers", return_value=Qt.KeyboardModifier.NoModifier):
            invoke("canvas_events", handler, "on_mouse_click_release_over_plot", event)
        self.assertEqual([2.0, 3.0], app.mouse_click_pos)

        invoke("canvas_events", handler, "on_mouse_and_key_modifiers", (1.25, 2.5), Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual("1.25, 2.50", app.clipboard.value)
        self.assertTrue(app.click_noproject)

        disabled = []

        class Disable:
            def __init__(self, name):
                self.name = name

            def setDisabled(self, value):
                disabled.append((self.name, value))

        app.populate_cmenu_grids = Recorder(("call",)).call
        app.ui.popMenu.popup = lambda pos: disabled.append(("popup", pos))
        for name in ("pop_menucolor", "popmenu_copy", "popmenu_delete", "popmenu_edit",
                     "popmenu_numeric_move", "popmenu_move2origin", "popmenu_move"):
            setattr(app.ui, name, Disable(name))
        with patch.object(canvas_module.QtGui, "QCursor", lambda: SimpleNamespace(pos=lambda: (0, 0))):
            invoke("canvas_events", handler, "on_mouse_context_menu")
        self.assertIn(("popmenu_copy", True), disabled)

        plugin = SimpleNamespace(pluginName="Active", clicks=[], moves=[])
        plugin.on_plugin_mouse_click_release = lambda pos: plugin.clicks.append(pos)
        plugin.on_plugin_mouse_move = lambda pos: plugin.moves.append(pos)
        app.app_plugins = [plugin]
        app.ui.notebook = Tabs(["Active"], [app.ui.plugin_tab])
        invoke("canvas_events", handler, "on_plugin_mouse_click_release", (5, 6))
        invoke("canvas_events", handler, "on_plugin_mouse_move", (7, 8))
        self.assertEqual([(5, 6)], plugin.clicks)
        self.assertEqual([(7, 8)], plugin.moves)

    @covers(
        "canvas_events", "selection_area_handler", "select_objects", "selected_message", "delete_hover_shape",
        "draw_hover_shape", "delete_selection_shape", "draw_selection_shape", "draw_moving_selection_shape",
    )
    def test_area_overlap_messages_and_shapes(self):
        inside = ObjectStub("inside", bounds=(1, 1, 2, 2))
        touching = ObjectStub("touching", bounds=(3, 3, 5, 5))
        app, handler = self.make_handler([inside, touching])
        invoke("canvas_events", handler, "selection_area_handler", (0, 0), (4, 4), True)
        self.assertEqual(["inside"], [obj.obj_options["name"] for obj in app.collection.selected])
        invoke("canvas_events", handler, "selection_area_handler", (0, 0), (4, 4), False)
        self.assertEqual({"inside", "touching"}, {obj.obj_options["name"] for obj in app.collection.selected})

        app.collection.set_all_inactive()
        app.mouse_click_pos = [1.5, 1.5]
        invoke("canvas_events", handler, "select_objects")
        self.assertEqual("inside", app.collection.active.obj_options["name"])
        app.mouse_click_pos = [3.5, 3.5]
        invoke("canvas_events", handler, "select_objects")
        self.assertEqual("touching", app.collection.active.obj_options["name"])

        overlap_a = ObjectStub("overlap-a", bounds=(0, 0, 2, 2))
        overlap_b = ObjectStub("overlap-b", bounds=(0, 0, 2, 2))
        overlap_app, overlap_handler = self.make_handler([overlap_a, overlap_b])
        overlap_app.collection.set_all_inactive()
        overlap_app.mouse_click_pos = [1, 1]
        invoke("canvas_events", overlap_handler, "select_objects")
        first_active = overlap_app.collection.active.obj_options["name"]
        invoke("canvas_events", overlap_handler, "select_objects")
        second_active = overlap_app.collection.active.obj_options["name"]
        self.assertEqual({"overlap-a", "overlap-b"}, {first_active, second_active})

        for kind, color in (("gerber", "green"), ("excellon", "brown"), ("cncjob", "blue"), ("geometry", "red")):
            invoke("canvas_events", handler, "selected_message", ObjectStub(kind, kind=kind))
            self.assertIn(color, app.inform.emissions[-1][0])

        invoke("canvas_events", handler, "delete_hover_shape")
        invoke("canvas_events", handler, "draw_hover_shape", inside, color="#AABBCCFF")
        hover_adds = [call for call in app.hover_shapes.calls if call[0] == "add"]
        self.assertEqual(1, len(hover_adds))
        self.assertEqual("#AABBCCcc", hover_adds[0][2]["color"])
        self.assertEqual("#AABBCC33", hover_adds[0][2]["face_color"])
        invoke("canvas_events", handler, "delete_selection_shape")
        invoke("canvas_events", handler, "draw_selection_shape", inside)
        invoke("canvas_events", handler, "draw_moving_selection_shape", (0, 0), (2, 3), face_alpha=0.5)
        self.assertGreaterEqual(len([call for call in app.sel_shapes.calls if call[0] == "add"]), 2)
        self.assertTrue(app.sel_objects_list)


SIGNAL_ACTIONS = {
    "connect_filemenu_signals": (
        "menufilenewproject menufilenewgeo menufilenewgrb menufilenewexc menufilenewdoc menufileopengerber "
        "menufileopenexcellon menufileopengcode menufileopenproject menufileopenconfig menufilenewscript "
        "menufileopenscript menufileopenscriptexample menufilerunscript menufileimportsvg "
        "menufileimportsvg_as_gerber menufileimportdxf menufileimportdxf_as_gerber menufileimport_hpgl2_as_geo "
        "menufileimportpcbdoc menufileimportkicad menufileimportsprint "
        "menufileexportsvg menufileexportpng menufileexportexcellon menufileexportgerber menufileexportdxf "
        "menufile_print menufilesaveproject menufilesaveprojectas menufilesavedefaults menufileexportpref "
        "menufileimportpref"
    ).split(),
    "connect_editmenu_signals": (
        "menufile_exit menueditedit menueditok menuedit_join2geo menuedit_join_exc2exc menuedit_join_grb2grb "
        "menuedit_convert_sg2mg menuedit_convert_mg2sg menueditdelete menueditcopyobject menueditconvert_any2geo "
        "menueditconvert_any2gerber menueditconvert_any2excellon menuedit_numeric_move menueditorigin "
        "menuedit_move2origin menuedit_center_in_origin menueditjump menueditlocate menueditselectall menueditpreferences"
    ).split(),
    "connect_optionsmenu_signals": (
        "menuoptions_transform_rotate menuoptions_transform_skewx menuoptions_transform_skewy "
        "menuoptions_transform_flipx menuoptions_transform_flipy menuoptions_view_source menuoptions_tools_db "
        "menuoptions_experimental_3D_area"
    ).split(),
    "connect_menuview_signals": (
        "menuviewenable menuviewdisableall menuviewenableother menuviewdisableother menuview_zoom_fit "
        "menuview_zoom_in menuview_zoom_out menuview_replot menuview_toggle_code_editor menuview_toggle_fscreen "
        "menuview_toggle_parea menuview_toggle_notebook menu_toggle_nb menuview_toggle_grid "
        "menuview_toggle_workspace menuview_toggle_grid_lines menuview_toggle_axis menuview_toggle_hud menuview_show_log"
    ).split(),
    "connect_menuhelp_signals": (
        "menuhelp_about menuhelp_readme menuhelp_donate menuhelp_manual menuhelp_report_bug menuhelp_exc_spec "
        "menuhelp_gerber_spec menuhelp_videohelp menuhelp_shortcut_list"
    ).split(),
    "connect_project_context_signals": (
        "menuprojectenable menuprojectdisable menuprojectviewsource menuprojectcopy menuprojectedit "
        "menuprojectdelete menuprojectsave menuprojectproperties"
    ).split(),
    "connect_canvas_context_signals": (
        "popmenu_disable popmenu_panel_toggle popmenu_new_geo popmenu_new_grb popmenu_new_exc popmenu_new_prj "
        "zoomfit clearplot replot popmenu_copy popmenu_delete popmenu_edit popmenu_save popmenu_numeric_move "
        "popmenu_move popmenu_move2origin popmenu_properties"
    ).split(),
    "connect_tools_signals_to_toolbar": (
        "drill_btn mill_btn level_btn isolation_btn follow_btn ncc_btn paint_btn cutout_btn panelize_btn film_btn "
        "dblsided_btn align_btn copperfill_btn markers_tool_btn punch_btn calculators_btn"
    ).split(),
    "connect_toolbar_signals": (
        "file_open_btn file_save_btn file_open_gerber_btn file_open_excellon_btn clear_plot_btn replot_btn "
        "zoom_fit_btn zoom_in_btn zoom_out_btn editor_start_btn editor_exit_btn copy_btn delete_btn distance_btn "
        "origin_btn jmp_btn locate_btn shell_btn new_script_btn open_script_btn run_script_btn"
    ).split(),
}


def expected_call(target, method, *args, **kwargs):
    return target, method, args, kwargs


SIGNAL_EXPECTED_CALLS = {
    # File menu
    "menufilenewproject": expected_call("f_handlers", "on_file_new_click"),
    "menufilenewgeo": expected_call("app_obj", "new_geometry_object"),
    "menufilenewgrb": expected_call("app_obj", "new_gerber_object"),
    "menufilenewexc": expected_call("app_obj", "new_excellon_object"),
    "menufilenewdoc": expected_call("app_obj", "new_document_object"),
    "menufileopengerber": expected_call("f_handlers", "on_file_open_gerber"),
    "menufileopenexcellon": expected_call("f_handlers", "on_file_open_excellon"),
    "menufileopengcode": expected_call("f_handlers", "on_file_open_gcode"),
    "menufileopenproject": expected_call("f_handlers", "on_file_open_project"),
    "menufileopenconfig": expected_call("f_handlers", "on_file_open_config"),
    "menufilenewscript": expected_call("f_handlers", "on_file_new_script"),
    "menufileopenscript": expected_call("f_handlers", "on_file_open_script"),
    "menufileopenscriptexample": expected_call("f_handlers", "on_file_open_script_example"),
    "menufilerunscript": expected_call("f_handlers", "on_file_run_script"),
    "menufileimportsvg": expected_call("f_handlers", "on_file_import_svg", "geometry"),
    "menufileimportsvg_as_gerber": expected_call("f_handlers", "on_file_import_svg", "gerber"),
    "menufileimportdxf": expected_call("f_handlers", "on_file_import_dxf", "geometry"),
    "menufileimportdxf_as_gerber": expected_call("f_handlers", "on_file_import_dxf", "gerber"),
    "menufileimport_hpgl2_as_geo": expected_call("f_handlers", "on_file_open_hpgl2"),
    "menufileimportpcbdoc": expected_call("f_handlers", "on_file_import_pcbdoc"),
    "menufileimportkicad": expected_call("f_handlers", "on_file_import_kicad_pcb"),
    "menufileimportsprint": expected_call("f_handlers", "on_file_import_sprint_layout"),
    "menufileexportsvg": expected_call("f_handlers", "on_file_export_svg"),
    "menufileexportpng": expected_call("f_handlers", "on_file_export_png"),
    "menufileexportexcellon": expected_call("f_handlers", "on_file_export_excellon"),
    "menufileexportgerber": expected_call("f_handlers", "on_file_export_gerber"),
    "menufileexportdxf": expected_call("f_handlers", "on_file_export_dxf"),
    "menufile_print": expected_call("f_handlers", "on_file_save_objects_pdf", use_thread=True),
    "menufilesaveproject": expected_call("f_handlers", "on_file_save_project"),
    "menufilesaveprojectas": expected_call("f_handlers", "on_file_save_project_as"),
    "menufilesavedefaults": expected_call("f_handlers", "on_file_save_defaults"),
    "menufileexportpref": expected_call("f_handlers", "on_export_preferences"),
    "menufileimportpref": expected_call("f_handlers", "on_import_preferences"),
    # Edit menu
    "menufile_exit": expected_call("app", "final_save"),
    "menueditedit": expected_call("app", "on_editing_start"),
    "menueditok": expected_call("app", "on_editing_finished"),
    "menuedit_join2geo": expected_call("edit_class", "on_edit_join"),
    "menuedit_join_exc2exc": expected_call("edit_class", "on_edit_join_exc"),
    "menuedit_join_grb2grb": expected_call("edit_class", "on_edit_join_grb"),
    "menuedit_convert_sg2mg": expected_call("edit_class", "on_convert_singlegeo_to_multigeo"),
    "menuedit_convert_mg2sg": expected_call("edit_class", "on_convert_multigeo_to_singlegeo"),
    "menueditdelete": expected_call("app", "on_delete"),
    "menueditcopyobject": expected_call("app", "on_copy_command"),
    "menueditconvert_any2geo": expected_call("edit_class", "convert_any2geo"),
    "menueditconvert_any2gerber": expected_call("edit_class", "convert_any2gerber"),
    "menueditconvert_any2excellon": expected_call("edit_class", "convert_any2excellon"),
    "menuedit_numeric_move": expected_call("app", "on_numeric_move"),
    "menueditorigin": expected_call("app", "on_set_origin"),
    "menuedit_move2origin": expected_call("app", "on_move2origin"),
    "menuedit_center_in_origin": expected_call("edit_class", "on_custom_origin"),
    "menueditjump": expected_call("app", "on_jump_to"),
    "menueditlocate": expected_call("app", "on_locate", obj=None),
    "menueditselectall": expected_call("app", "on_selectall"),
    "menueditpreferences": expected_call("app", "on_preferences"),
    # Options menu
    "menuoptions_transform_rotate": expected_call("app", "on_rotate"),
    "menuoptions_transform_skewx": expected_call("app", "on_skewx"),
    "menuoptions_transform_skewy": expected_call("app", "on_skewy"),
    "menuoptions_transform_flipx": expected_call("app", "on_flipx"),
    "menuoptions_transform_flipy": expected_call("app", "on_flipy"),
    "menuoptions_view_source": expected_call("app", "on_view_source"),
    "menuoptions_tools_db": expected_call("app", "on_tools_database", source="app"),
    "menuoptions_experimental_3D_area": expected_call("app", "on_3d_area"),
    # View menu
    "menuviewenable": expected_call("app", "enable_all_plots"),
    "menuviewdisableall": expected_call("app", "disable_all_plots"),
    "menuviewenableother": expected_call("app", "enable_other_plots"),
    "menuviewdisableother": expected_call("app", "disable_other_plots"),
    "menuview_zoom_fit": expected_call("app", "on_zoom_fit"),
    "menuview_zoom_in": expected_call("app", "on_zoom_in"),
    "menuview_zoom_out": expected_call("app", "on_zoom_out"),
    "menuview_replot": expected_call("app", "plot_all"),
    "menuview_toggle_code_editor": expected_call("app", "on_toggle_code_editor"),
    "menuview_toggle_fscreen": expected_call("ui", "on_full_screen_toggled"),
    "menuview_toggle_parea": expected_call("ui", "on_toggle_plotarea"),
    "menuview_toggle_notebook": expected_call("ui", "on_toggle_notebook"),
    "menu_toggle_nb": expected_call("ui", "on_toggle_notebook"),
    "menuview_toggle_grid": expected_call("ui", "on_toggle_grid"),
    "menuview_toggle_workspace": expected_call("app", "on_workspace_toggle"),
    "menuview_toggle_grid_lines": expected_call("plotcanvas", "on_toggle_grid_lines"),
    "menuview_toggle_axis": expected_call("plotcanvas", "on_toggle_axis"),
    "menuview_toggle_hud": expected_call("plotcanvas", "on_toggle_hud"),
    "menuview_show_log": expected_call("app", "on_show_log"),
    # Help menu
    "menuhelp_about": expected_call("app", "on_about"),
    "menuhelp_readme": expected_call("app", "on_howto"),
    "menuhelp_donate": expected_call("webbrowser", "open", "donate"),
    "menuhelp_manual": expected_call("webbrowser", "open", "manual"),
    "menuhelp_report_bug": expected_call("webbrowser", "open", "bugs"),
    "menuhelp_exc_spec": expected_call("webbrowser", "open", "excellon"),
    "menuhelp_gerber_spec": expected_call("webbrowser", "open", "gerber"),
    "menuhelp_videohelp": expected_call("webbrowser", "open", "video"),
    "menuhelp_shortcut_list": expected_call("ui", "on_shortcut_list"),
    # Project context menu
    "menuprojectenable": expected_call("app", "on_enable_sel_plots"),
    "menuprojectdisable": expected_call("app", "on_disable_sel_plots"),
    "menuprojectviewsource": expected_call("app", "on_view_source"),
    "menuprojectcopy": expected_call("app", "on_copy_command"),
    "menuprojectedit": expected_call("app", "on_editing_start"),
    "menuprojectdelete": expected_call("app", "on_delete"),
    "menuprojectsave": expected_call("app", "on_project_context_save"),
    "menuprojectproperties": expected_call("app", "obj_properties"),
    # Canvas context menu
    "popmenu_disable": expected_call("app", "toggle_plots", []),
    "popmenu_panel_toggle": expected_call("ui", "on_toggle_notebook"),
    "popmenu_new_geo": expected_call("app_obj", "new_geometry_object"),
    "popmenu_new_grb": expected_call("app_obj", "new_gerber_object"),
    "popmenu_new_exc": expected_call("app_obj", "new_excellon_object"),
    "popmenu_new_prj": expected_call("f_handlers", "on_file_new_project"),
    "zoomfit": expected_call("app", "on_zoom_fit"),
    "clearplot": expected_call("app", "clear_plots"),
    "replot": expected_call("app", "plot_all"),
    "popmenu_copy": expected_call("app", "on_copy_command"),
    "popmenu_delete": expected_call("app", "on_delete"),
    "popmenu_edit": expected_call("app", "on_editing_start"),
    "popmenu_save": expected_call("app", "on_editing_finished"),
    "popmenu_numeric_move": expected_call("app", "on_numeric_move"),
    "popmenu_move": expected_call("app", "obj_move"),
    "popmenu_move2origin": expected_call("app", "on_move2origin"),
    "popmenu_properties": expected_call("app", "obj_properties"),
    # Plugin toolbar
    "drill_btn": expected_call("drilling_tool", "run", toggle=True),
    "mill_btn": expected_call("milling_tool", "run", toggle=True),
    "level_btn": expected_call("levelling_tool", "run", toggle=True),
    "isolation_btn": expected_call("isolation_tool", "run", toggle=True),
    "follow_btn": expected_call("follow_tool", "run", toggle=True),
    "ncc_btn": expected_call("ncclear_tool", "run", toggle=True),
    "paint_btn": expected_call("paint_tool", "run", toggle=True),
    "cutout_btn": expected_call("cutout_tool", "run", toggle=True),
    "panelize_btn": expected_call("panelize_tool", "run", toggle=True),
    "film_btn": expected_call("film_tool", "run", toggle=True),
    "dblsided_btn": expected_call("dblsidedtool", "run", toggle=True),
    "align_btn": expected_call("align_objects_tool", "run", toggle=True),
    "copperfill_btn": expected_call("copper_thieving_tool", "run", toggle=True),
    "markers_tool_btn": expected_call("markers_tool", "run", toggle=True),
    "punch_btn": expected_call("punch_tool", "run", toggle=True),
    "calculators_btn": expected_call("calculator_tool", "run", toggle=True),
    # Main toolbar
    "file_open_btn": expected_call("f_handlers", "on_file_open_project"),
    "file_save_btn": expected_call("f_handlers", "on_file_save_project"),
    "file_open_gerber_btn": expected_call("f_handlers", "on_file_open_gerber"),
    "file_open_excellon_btn": expected_call("f_handlers", "on_file_open_excellon"),
    "clear_plot_btn": expected_call("app", "clear_plots"),
    "replot_btn": expected_call("app", "plot_all"),
    "zoom_fit_btn": expected_call("app", "on_zoom_fit"),
    "zoom_in_btn": expected_call("plotcanvas", "zoom", 1 / 1.5),
    "zoom_out_btn": expected_call("plotcanvas", "zoom", 1.5),
    "editor_start_btn": expected_call("app", "on_editing_start"),
    "editor_exit_btn": expected_call("app", "on_editing_finished", force_cancel=True),
    "copy_btn": expected_call("app", "on_copy_command"),
    "delete_btn": expected_call("app", "on_delete"),
    "distance_btn": expected_call("distance_tool", "run", toggle=True),
    "origin_btn": expected_call("app", "on_set_origin"),
    "jmp_btn": expected_call("app", "on_jump_to"),
    "locate_btn": expected_call("app", "on_locate", obj=None),
    "shell_btn": expected_call("ui", "toggle_shell_ui"),
    "new_script_btn": expected_call("f_handlers", "on_file_new_script"),
    "open_script_btn": expected_call("f_handlers", "on_file_open_script"),
    "run_script_btn": expected_call("f_handlers", "on_file_run_script"),
}


class TestSignalConnector(AppTestCase):
    APP_METHODS = (
        "final_save on_editing_start on_editing_finished on_delete on_copy_command on_numeric_move on_set_origin "
        "on_move2origin on_jump_to on_locate on_selectall on_preferences on_rotate on_skewx on_skewy on_flipx "
        "on_flipy on_view_source on_tools_database on_3d_area enable_all_plots disable_all_plots enable_other_plots "
        "disable_other_plots on_zoom_fit on_zoom_in on_zoom_out plot_all on_toggle_code_editor on_workspace_toggle "
        "on_show_log on_about on_howto on_enable_sel_plots on_disable_sel_plots on_project_context_save "
        "obj_properties on_set_color_action_triggered toggle_plots clear_plots obj_move"
    ).split()
    FILE_METHODS = (
        "on_file_new_click on_file_open_gerber on_file_open_excellon on_file_open_gcode on_file_open_project "
        "on_file_open_config on_file_new_script on_file_open_script on_file_open_script_example on_file_run_script "
        "on_file_import_svg on_file_import_dxf on_file_open_hpgl2 on_file_export_svg on_file_export_png "
        "on_file_import_pcbdoc on_file_import_kicad_pcb on_file_import_sprint_layout "
        "on_file_export_excellon on_file_export_gerber on_file_export_dxf on_file_save_objects_pdf "
        "on_file_save_project on_file_save_project_as on_file_save_defaults on_export_preferences on_import_preferences "
        "on_file_new_project"
    ).split()
    OBJECT_METHODS = "new_geometry_object new_gerber_object new_excellon_object new_document_object".split()
    EDIT_METHODS = (
        "on_edit_join on_edit_join_exc on_edit_join_grb on_convert_singlegeo_to_multigeo "
        "on_convert_multigeo_to_singlegeo convert_any2geo convert_any2gerber convert_any2excellon on_custom_origin"
    ).split()

    def make_connector(self):
        all_actions = {name for names in SIGNAL_ACTIONS.values() for name in names}
        ui = SimpleNamespace(**{name: Action(name) for name in all_actions})
        ui.menuprojectcolor = Menu([Action("project-color-a"), Action("project-color-b")])
        ui.pop_menucolor = Menu([Action("canvas-color-a"), Action("canvas-color-b")])
        route_log = []

        def ui_route(name):
            recorder = Recorder((name,), target="ui", sink=route_log)
            return getattr(recorder, name)

        ui.on_full_screen_toggled = ui_route("on_full_screen_toggled")
        ui.on_toggle_plotarea = ui_route("on_toggle_plotarea")
        ui.on_toggle_notebook = ui_route("on_toggle_notebook")
        ui.on_toggle_grid = ui_route("on_toggle_grid")
        ui.on_shortcut_list = ui_route("on_shortcut_list")
        ui.toggle_shell_ui = ui_route("toggle_shell_ui")

        app_recorder = Recorder(self.APP_METHODS, target="app", sink=route_log)
        app = SimpleNamespace(log=Recorder(("debug", "error")), ui=ui)
        for name in self.APP_METHODS:
            setattr(app, name, getattr(app_recorder, name))
        app.f_handlers = Recorder(self.FILE_METHODS, target="f_handlers", sink=route_log)
        app.app_obj = Recorder(self.OBJECT_METHODS, target="app_obj", sink=route_log)
        app.edit_class = Recorder(self.EDIT_METHODS, target="edit_class", sink=route_log)
        app.collection = Collection()
        app.plotcanvas = Recorder(
            ("on_toggle_grid_lines", "on_toggle_axis", "on_toggle_hud", "zoom"),
            target="plotcanvas", sink=route_log
        )
        for name in (
            "drilling_tool milling_tool levelling_tool isolation_tool follow_tool ncclear_tool paint_tool cutout_tool "
            "panelize_tool film_tool dblsidedtool align_objects_tool copper_thieving_tool markers_tool punch_tool "
            "calculator_tool distance_tool"
        ).split():
            tool = Recorder(("run",), target=name, sink=route_log)
            setattr(app, name, tool)
        app.geo_editor = Recorder(("connect_geo_toolbar_signals",), target="geo_editor", sink=route_log)
        app.grb_editor = Recorder(("connect_grb_toolbar_signals",), target="grb_editor", sink=route_log)
        app.exc_editor = Recorder(("connect_exc_toolbar_signals",), target="exc_editor", sink=route_log)
        app._route_log = route_log
        app.donate_url = "donate"
        app.manual_url = "manual"
        app.bug_report_url = "bugs"
        app.excellon_spec_url = "excellon"
        app.gerber_spec_url = "gerber"
        app.video_url = "video"
        return app, app_recorder, AppSignalConnector(app)

    @covers("signal_connector", *METHOD_MANIFEST["signal_connector"])
    def test_all_connector_methods_wire_and_route_every_action(self):
        app, app_recorder, connector = self.make_connector()
        all_actions = {name for names in SIGNAL_ACTIONS.values() for name in names}
        self.assertEqual(all_actions, set(SIGNAL_EXPECTED_CALLS))

        def open_url(url):
            app._route_log.append(("webbrowser", "open", (url,), {}))

        with patch.object(signal_module.webbrowser, "open", side_effect=open_url):
            for method_name in METHOD_MANIFEST["signal_connector"]:
                invoke("signal_connector", connector, method_name)

                for action_name in SIGNAL_ACTIONS.get(method_name, ()):
                    action = getattr(app.ui, action_name)
                    self.assertTrue(action.triggered.slots or action.clicked.slots,
                                    "%s left %s disconnected" % (method_name, action_name))
                    before = len(app._route_log)
                    signal = action.clicked if action.clicked.slots else action.triggered
                    signal.emit()
                    self.assertEqual(
                        [SIGNAL_EXPECTED_CALLS[action_name]], app._route_log[before:],
                        "%s routed to the wrong target or arguments" % action_name
                    )

                if method_name == "connect_project_context_signals":
                    color_actions = app.ui.menuprojectcolor.actions()
                elif method_name == "connect_canvas_context_signals":
                    color_actions = app.ui.pop_menucolor.actions()
                else:
                    color_actions = ()
                for action in color_actions:
                    self.assertEqual(1, len(action.triggered.slots))
                    before = len(app._route_log)
                    action.triggered.emit()
                    self.assertEqual(
                        [expected_call("app", "on_set_color_action_triggered")], app._route_log[before:]
                    )

        self.assertIn(("on_tools_database", (), {"source": "app"}), app_recorder.calls)
        self.assertIn(("on_editing_finished", (), {"force_cancel": True}), app_recorder.calls)
        self.assertIn(("zoom", (1 / 1.5,), {}), app.plotcanvas.calls)
        self.assertIn(("zoom", (1.5,), {}), app.plotcanvas.calls)
        for editor, method in ((app.geo_editor, "connect_geo_toolbar_signals"),
                               (app.grb_editor, "connect_grb_toolbar_signals"),
                               (app.exc_editor, "connect_exc_toolbar_signals")):
            self.assertEqual(method, editor.calls[0][0])


class TestZZManifestAndInvocation(unittest.TestCase):
    def test_manifest_exactly_matches_current_public_methods(self):
        for family, cls in FAMILY_CLASSES.items():
            discovered = tuple(
                name for name, function in inspect.getmembers(cls, inspect.isfunction)
                if name != "__init__" and not name.startswith("_")
            )
            self.assertEqual(set(METHOD_MANIFEST[family]), set(discovered), family)
            self.assertEqual(len(METHOD_MANIFEST[family]), len(discovered), family)

    def test_zz_every_manifested_method_was_invoked(self):
        behavior_classes = (TestLifecycle, TestUIActions, TestObjectOps, TestCanvasEvents, TestSignalConnector)
        previous_ledger = {family: set(methods) for family, methods in INVOCATION_LEDGER.items()}
        try:
            for methods in INVOCATION_LEDGER.values():
                methods.clear()

            loader = unittest.TestLoader()
            behavior_suite = unittest.TestSuite(
                loader.loadTestsFromTestCase(test_class) for test_class in behavior_classes
            )
            nested_output = io.StringIO()
            result = unittest.TextTestRunner(stream=nested_output, verbosity=0).run(behavior_suite)
            self.assertTrue(result.wasSuccessful(), nested_output.getvalue())

            for family, methods in METHOD_MANIFEST.items():
                self.assertEqual(set(methods), INVOCATION_LEDGER[family], family)
        finally:
            for family, methods in previous_ledger.items():
                INVOCATION_LEDGER[family].clear()
                INVOCATION_LEDGER[family].update(methods)


if __name__ == "__main__":
    unittest.main(verbosity=2)
