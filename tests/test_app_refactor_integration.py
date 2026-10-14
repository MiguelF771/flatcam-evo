import inspect
import logging
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import create_autospec, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtGui, QtWidgets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from appHandlers.appCanvasEvents import AppCanvasEvents
from appHandlers.appLifecycle import AppLifecycle
from appHandlers.appObjectOps import AppObjectOps
from appHandlers.appSignalConnector import AppSignalConnector
from appHandlers.appUIActions import AppUIActions
from appMain import App


ARG = object()
SECOND_ARG = object()


def invocation(*args, **kwargs):
    return args, kwargs


FACADE_MANIFEST = {
    'lifecycle': {
        'on_options_value_changed': invocation(key_changed=ARG),
        'on_app_restart': invocation(),
        'on_layout': invocation(lay=ARG, connect_signals=False),
        'final_save': invocation(),
        'quit_application': invocation(silent=True),
        'kill_app': invocation(),
        'on_portable_checked': invocation(state=ARG),
        'on_defaults_dict_change': invocation(field=ARG),
        'on_defaults2options': invocation(),
        'setup_obj_classes': invocation(),
        'version_check': invocation(),
        'start_delayed_quit': invocation(delay=ARG, filename=SECOND_ARG, should_quit=False),
        'check_project_file_size': invocation(filename=ARG, should_quit=False),
        'save_project_auto': invocation(),
        'save_project_auto_update': invocation(),
    },
    'ui_actions': {
        'on_about': invocation(),
        'on_howto': invocation(),
        'install_bookmarks': invocation(book_dict=ARG),
        'on_bookmarks_manager': invocation(),
        'on_backup_site': invocation(),
        'on_workspace_modified': invocation(),
        'on_workspace': invocation(),
        'on_workspace_toggle': invocation(),
        'on_show_log': invocation(),
        'on_cursor_type': invocation(val=ARG, control_cursor=False),
        'on_tool_add_keypress': invocation(),
        'on_toggle_preferences': invocation(),
        'on_preferences': invocation(),
        'on_tools_database': invocation(source=ARG),
        'on_3d_area': invocation(),
        'on_geometry_tool_add_from_db_executed': invocation(ARG),
        'on_plot_area_tab_closed': invocation(ARG),
        'on_plot_area_tab_double_clicked': invocation(),
        'on_notebook_closed': invocation(),
        'on_properties_tab_click': invocation(),
        'on_notebook_tab_changed': invocation(),
        'setup_default_properties_tab': invocation(),
        'grid_status': invocation(),
        'populate_cmenu_grids': invocation(),
        'set_grid': invocation(),
        'on_grid_add': invocation(),
        'on_grid_delete': invocation(),
    },
    'object_ops': {
        'on_flipy': invocation(),
        'on_flipx': invocation(),
        'on_rotate': invocation(silent=True, preset=ARG),
        'on_skewx': invocation(),
        'on_skewy': invocation(),
        'on_set_origin': invocation(),
        'on_set_zero_click': invocation(ARG, location=SECOND_ARG, noplot=True, use_thread=False),
        'on_move2origin': invocation(use_thread=False),
        'on_jump_to': invocation(custom_location=ARG, fit_center=False),
        'on_locate': invocation(ARG, fit_center=False),
        'on_numeric_move': invocation(val=ARG),
        'on_copy_command': invocation(),
        'on_copy_object2': invocation(ARG),
        'on_rename_object': invocation(ARG),
        'on_delete_keypress': invocation(),
        'on_delete': invocation(force_deletion=True),
        'delete_first_selected': invocation(del_obj=ARG),
        'on_selectall': invocation(),
        'on_deselect_all': invocation(),
        'on_copy_name': invocation(),
    },
    'canvas_events': {
        'on_mouse_click_over_plot': invocation(ARG),
        'on_mouse_double_click_over_plot': invocation(ARG),
        'on_mouse_move_over_plot': invocation(ARG, SECOND_ARG),
        'on_mouse_click_release_over_plot': invocation(ARG),
        'on_mouse_and_key_modifiers': invocation(ARG, SECOND_ARG),
        'on_mouse_context_menu': invocation(),
        'selection_area_handler': invocation(ARG, SECOND_ARG, 'contains'),
        'select_objects': invocation(ARG),
        'selected_message': invocation(ARG),
        'on_plugin_mouse_click_release': invocation(ARG),
        'on_plugin_mouse_move': invocation(ARG),
        'delete_hover_shape': invocation(),
        'draw_hover_shape': invocation(ARG, SECOND_ARG),
        'delete_selection_shape': invocation(),
        'draw_selection_shape': invocation(ARG, SECOND_ARG),
        'draw_moving_selection_shape': invocation(ARG, SECOND_ARG, offset=(1, 2)),
    },
    'signal_connector': {
        'connect_filemenu_signals': invocation(),
        'connect_editmenu_signals': invocation(),
        'connect_optionsmenu_signals': invocation(),
        'connect_menuview_signals': invocation(),
        'connect_menuhelp_signals': invocation(),
        'connect_project_context_signals': invocation(),
        'connect_canvas_context_signals': invocation(),
        'connect_tools_signals_to_toolbar': invocation(),
        'connect_editors_toolbar_signals': invocation(),
        'connect_toolbar_signals': invocation(),
    },
}

FAMILY_CLASSES = {
    'lifecycle': AppLifecycle,
    'ui_actions': AppUIActions,
    'object_ops': AppObjectOps,
    'canvas_events': AppCanvasEvents,
    'signal_connector': AppSignalConnector,
}

RETURN_FACADES = {
    ('ui_actions', 'on_tools_database'),
    ('ui_actions', 'on_3d_area'),
    ('ui_actions', 'on_geometry_tool_add_from_db_executed'),
    ('ui_actions', 'grid_status'),
    ('object_ops', 'on_set_zero_click'),
    ('object_ops', 'on_jump_to'),
    ('object_ops', 'on_locate'),
    ('object_ops', 'on_copy_object2'),
}


def public_methods(handler_class):
    return {
        name
        for name, member in handler_class.__dict__.items()
        if not name.startswith('_')
        and (inspect.isfunction(member) or isinstance(member, staticmethod))
    }


def normalized_signature(owner, method_name):
    descriptor = inspect.getattr_static(owner, method_name)
    is_static = isinstance(descriptor, staticmethod)
    if isinstance(descriptor, (staticmethod, classmethod)):
        method = descriptor.__func__
    else:
        method = descriptor

    signature = inspect.signature(method)
    parameters = list(signature.parameters.values())
    if parameters and not is_static:
        parameters[0] = parameters[0].replace(name='receiver')
    return signature.replace(parameters=parameters)


ACTION_NAMES = """
    menufilenewproject menufilenewgeo menufilenewgrb menufilenewexc menufilenewdoc
    menufileopengerber menufileopenexcellon menufileopengcode menufileopenproject menufileopenconfig
    menufilenewscript menufileopenscript menufileopenscriptexample menufilerunscript
    menufileimportsvg menufileimportsvg_as_gerber menufileimportdxf menufileimportdxf_as_gerber
    menufileimportpcbdoc menufileimportkicad menufileimportsprint menufileimporteagle
    menufileimportallegro
    menufileimport_hpgl2_as_geo menufileexportsvg menufileexportpng menufileexportexcellon
    menufileexportgerber menufileexportdxf menufile_print menufilesaveproject menufilesaveprojectas
    menufilesavedefaults menufileexportpref menufileimportpref menufile_exit
    menueditedit menueditok menuedit_join2geo menuedit_join_exc2exc menuedit_join_grb2grb
    menuedit_convert_sg2mg menuedit_convert_mg2sg menueditdelete menueditcopyobject
    menueditconvert_any2geo menueditconvert_any2gerber menueditconvert_any2excellon menuedit_numeric_move
    menueditorigin menuedit_move2origin menuedit_center_in_origin menueditjump menueditlocate
    menueditselectall menueditpreferences
    menuoptions_transform_rotate menuoptions_transform_skewx menuoptions_transform_skewy
    menuoptions_transform_flipx menuoptions_transform_flipy menuoptions_view_source menuoptions_tools_db
    menuoptions_experimental_3D_area
    menuviewenable menuviewdisableall menuviewenableother menuviewdisableother menuview_zoom_fit
    menuview_zoom_in menuview_zoom_out menuview_replot menuview_toggle_code_editor menuview_toggle_fscreen
    menuview_toggle_parea menuview_toggle_notebook menu_toggle_nb menuview_toggle_grid
    menuview_toggle_workspace menuview_toggle_grid_lines menuview_toggle_axis menuview_toggle_hud
    menuview_show_log
    menuhelp_about menuhelp_readme menuhelp_donate menuhelp_manual menuhelp_report_bug menuhelp_exc_spec
    menuhelp_gerber_spec menuhelp_videohelp menuhelp_shortcut_list
    menuprojectenable menuprojectdisable menuprojectviewsource menuprojectcopy menuprojectedit
    menuprojectdelete menuprojectsave menuprojectproperties
    popmenu_disable popmenu_panel_toggle popmenu_new_geo popmenu_new_grb popmenu_new_exc popmenu_new_prj
    zoomfit clearplot replot popmenu_copy popmenu_delete popmenu_edit popmenu_save popmenu_numeric_move
    popmenu_move popmenu_move2origin popmenu_properties
    drill_btn mill_btn level_btn isolation_btn follow_btn ncc_btn paint_btn cutout_btn panelize_btn film_btn
    dblsided_btn align_btn copperfill_btn markers_tool_btn punch_btn calculators_btn
    file_open_btn file_save_btn file_open_gerber_btn file_open_excellon_btn clear_plot_btn replot_btn
    zoom_fit_btn zoom_in_btn zoom_out_btn editor_start_btn copy_btn delete_btn distance_btn origin_btn jmp_btn
    locate_btn shell_btn new_script_btn open_script_btn run_script_btn menu_plugins_shell
""".split()


class UiSignals(QtCore.QObject):
    clicked = QtCore.pyqtSignal()
    currentChanged = QtCore.pyqtSignal(int)
    tabBarDoubleClicked = QtCore.pyqtSignal(int)
    tab_closed_signal = QtCore.pyqtSignal(str)
    currentIndexChanged = QtCore.pyqtSignal(int)
    activated_custom = QtCore.pyqtSignal(object)
    stateChanged = QtCore.pyqtSignal(int)


class StrictUI:

    def __init__(self):
        for action_name in ACTION_NAMES:
            setattr(self, action_name, QtGui.QAction())

        self.editor_exit_btn = QtWidgets.QPushButton()
        self.menuprojectcolor = QtWidgets.QMenu()
        self.menuprojectcolor.addAction('project color')
        self.pop_menucolor = QtWidgets.QMenu()
        self.pop_menucolor.addAction('canvas color')
        self.shell_dock = QtWidgets.QDockWidget()
        self.notebook = UiSignals()
        self.plot_tab_area = UiSignals()
        self.hud_label = UiSignals()
        self.axis_status_label = UiSignals()
        self.pref_status_label = UiSignals()
        self.general_pref_form = SimpleNamespace(
            general_app_set_group=SimpleNamespace(
                wk_cb=UiSignals(),
                wk_orientation_radio=UiSignals(),
                workspace_cb=UiSignals(),
                cursor_radio=UiSignals()
            ),
            general_app_group=SimpleNamespace(
                portability_cb=UiSignals()
            )
        )
        self.title = None

    def set_ui_title(self, name):
        self.title = name

    def on_full_screen_toggled(self):
        pass

    def on_toggle_plotarea(self):
        pass

    def on_toggle_notebook(self):
        pass

    def on_toggle_grid(self):
        pass

    def on_shortcut_list(self):
        pass

    def toggle_shell_ui(self):
        pass

    def dispose(self):
        for action_name in ACTION_NAMES:
            getattr(self, action_name).deleteLater()

        qt_objects = [
            self.editor_exit_btn,
            self.menuprojectcolor,
            self.pop_menucolor,
            self.shell_dock,
            self.notebook,
            self.plot_tab_area,
            self.hud_label,
            self.axis_status_label,
            self.pref_status_label,
            self.general_pref_form.general_app_set_group.wk_cb,
            self.general_pref_form.general_app_set_group.wk_orientation_radio,
            self.general_pref_form.general_app_set_group.workspace_cb,
            self.general_pref_form.general_app_set_group.cursor_radio,
            self.general_pref_form.general_app_group.portability_cb,
        ]
        for qt_object in qt_objects:
            qt_object.deleteLater()


class CollectionStub:

    def __init__(self):
        self.updates = []

    def on_collection_updated(self, *args):
        self.updates.append(args)

    @staticmethod
    def get_active():
        return None

    @staticmethod
    def get_selected():
        return []


class PlotCanvasStub:

    def __init__(self):
        self.hud_toggles = 0
        self.axis_toggles = 0

    def on_toggle_hud(self):
        self.hud_toggles += 1

    def on_toggle_axis(self):
        self.axis_toggles += 1

    @staticmethod
    def on_toggle_grid_lines():
        pass

    @staticmethod
    def zoom(_factor):
        pass


class ProcessContainerStub(QtCore.QObject):
    idle_flag = QtCore.pyqtSignal()


class WorkerStub:

    def __init__(self):
        self.tasks = []

    def add_task(self, task):
        self.tasks.append(task)


class TrayStub:

    def __init__(self):
        self.show_count = 0

    def show(self):
        self.show_count += 1


class TestAppFacadeIntegration(unittest.TestCase):

    def test_manifest_exhaustively_covers_public_handler_methods_and_app_facades(self):
        for family, handler_class in FAMILY_CLASSES.items():
            with self.subTest(family=family):
                manifest_methods = set(FACADE_MANIFEST[family])
                self.assertEqual(public_methods(handler_class), manifest_methods)
                for method_name in manifest_methods:
                    facade = inspect.getattr_static(App, method_name, None)
                    self.assertIsNotNone(facade, f'App.{method_name} is missing')
                    self.assertTrue(callable(facade), f'App.{method_name} is not callable')

    def test_every_facade_signature_matches_its_handler(self):
        for family, methods in FACADE_MANIFEST.items():
            handler_class = FAMILY_CLASSES[family]
            for method_name in methods:
                with self.subTest(family=family, method=method_name):
                    self.assertEqual(
                        normalized_signature(App, method_name),
                        normalized_signature(handler_class, method_name)
                    )

    def test_every_instance_facade_delegates_exact_arguments_to_strict_handler(self):
        sentinel = object()
        for family, methods in FACADE_MANIFEST.items():
            handler_class = FAMILY_CLASSES[family]
            for method_name, (args, kwargs) in methods.items():
                if (family, method_name) == ('lifecycle', 'kill_app'):
                    continue
                with self.subTest(family=family, method=method_name):
                    handler = create_autospec(handler_class, instance=True, spec_set=True)
                    target_method = getattr(handler, method_name)
                    target_method.return_value = sentinel
                    app = SimpleNamespace(**{family: handler})

                    result = getattr(App, method_name)(app, *args, **kwargs)

                    target_method.assert_called_once_with(*args, **kwargs)
                    if (family, method_name) in RETURN_FACADES:
                        self.assertIs(result, sentinel)
                    else:
                        self.assertIsNone(result)

    def test_static_kill_app_delegates_without_an_app_instance(self):
        with patch.object(AppLifecycle, 'kill_app', autospec=True) as kill_app:
            self.assertIsNone(App.kill_app())

        kill_app.assert_called_once_with()


class TestControlledAppStartup(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @staticmethod
    def disconnect_signal(signal):
        try:
            signal.disconnect()
        except (TypeError, RuntimeError):
            pass

    @classmethod
    def dispose_app(cls, app, original_log_handlers, original_log_level, original_stylesheet):
        app.autosave_timer.stop()
        app.autosave_timer.deleteLater()

        app_signals = [
            app.inform[str],
            app.inform[str, bool],
            app.inform_no_echo[str],
            app.restore_project,
            app.restore_project_objects_sig,
            app.app_quit,
            app.message,
            app.file_opened,
            app.file_saved,
            app.post_edit_sig,
            app.object_status_changed,
            app.args_at_startup[list],
            app.close_app_signal,
            app.run_script,
            app.worker_task,
        ]
        for signal in app_signals:
            cls.disconnect_signal(signal)

        for handler_name in FAMILY_CLASSES:
            handler = getattr(app, handler_name, None)
            if isinstance(handler, QtCore.QObject):
                handler.deleteLater()

        app.proc_container.deleteLater()
        app.ui.dispose()
        app.parent_w.close()
        app.parent_w.deleteLater()
        app.deleteLater()

        base_logger = logging.getLogger('base')
        for handler in list(base_logger.handlers):
            if handler not in original_log_handlers:
                base_logger.removeHandler(handler)
                handler.close()
        base_logger.setLevel(original_log_level)

        cls.qapp.setStyleSheet(original_stylesheet)
        QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        cls.qapp.processEvents()

    def test_real_constructor_builds_handlers_and_wires_initialized_components(self):
        """Run App orchestration while replacing only external or heavyweight phases."""
        setup_calls = []
        production_canvas_setup = App._setup_canvas_and_plotting
        base_logger = logging.getLogger('base')
        original_log_handlers = tuple(base_logger.handlers)
        original_log_level = base_logger.level
        original_stylesheet = self.qapp.styleSheet()

        def setup_paths(app):
            setup_calls.append('_setup_paths_and_config')
            app.cmd_line_headless = 1
            app.data_path = temporary_directory.name
            app.preprocessorpaths = temporary_directory.name
            app.os = 'windows'
            app.app_home = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def setup_gui(app):
            setup_calls.append('_setup_gui')
            app.ui = StrictUI()
            app.shell = SimpleNamespace()
            app.preferencesUiManager = SimpleNamespace()
            app.collection = CollectionStub()
            app.plotcanvas = PlotCanvasStub()
            app.proc_container = ProcessContainerStub()
            app.autosave_timer = QtCore.QTimer(app)
            app.splash = None
            app._show_splash = False

        def setup_workers(app):
            setup_calls.append('_setup_workers_crew')
            app.workers = WorkerStub()
            app.worker_task.connect(app.workers.add_task)

        def setup_components(app):
            setup_calls.append('_setup_canvas_and_plotting')
            production_canvas_setup(app)

        def setup_tools(app):
            setup_calls.append('_setup_tools_and_editors')

        def setup_system(app):
            setup_calls.append('_setup_system_integration')
            app.parent_w = QtWidgets.QWidget()
            app.trayIcon = TrayStub()

        with tempfile.TemporaryDirectory() as temp_dir:
            temporary_directory = SimpleNamespace(name=temp_dir)
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(App, '_setup_paths_and_config', autospec=True, side_effect=setup_paths)
                )
                stack.enter_context(patch.object(App, '_setup_gui', autospec=True, side_effect=setup_gui))
                stack.enter_context(
                    patch.object(App, '_setup_workers_crew', autospec=True, side_effect=setup_workers)
                )
                stack.enter_context(
                    patch.object(App, '_setup_canvas_and_plotting', autospec=True, side_effect=setup_components)
                )
                stack.enter_context(
                    patch.object(App, '_setup_tools_and_editors', autospec=True, side_effect=setup_tools)
                )
                stack.enter_context(
                    patch.object(App, '_setup_system_integration', autospec=True, side_effect=setup_system)
                )
                stack.enter_context(patch.object(App, 'install_tools', autospec=True))
                stack.enter_context(patch.object(App, 'install_bookmarks', autospec=True))
                stack.enter_context(
                    patch.object(AppUIActions, 'setup_default_properties_tab', autospec=True)
                )
                stack.enter_context(patch.object(AppLifecycle, 'setup_obj_classes', autospec=True))
                info_target = stack.enter_context(patch.object(App, 'info', autospec=True))
                stack.enter_context(patch('appMain.BookmarkManager', autospec=True, return_value=object()))
                stack.enter_context(patch('appMain.AppGeoEditor', autospec=True, return_value=object()))
                stack.enter_context(patch('appMain.AppExcEditor', autospec=True, return_value=object()))
                stack.enter_context(patch('appMain.AppGerberEditor', autospec=True, return_value=object()))
                stack.enter_context(patch('appMain.AppGCodeEditor', autospec=True, return_value=object()))
                stack.enter_context(patch('appMain.ExclusionAreas', autospec=True, return_value=object()))
                stack.enter_context(patch('appMain.appIO', autospec=True, spec_set=True))
                stack.enter_context(patch('appMain.appEditor', autospec=True, spec_set=True))
                stack.enter_context(patch('appMain.AppPlotManager', autospec=True, spec_set=True))
                stack.enter_context(patch('appMain.Pool', return_value=object()))
                stack.enter_context(patch('appMain.darkdetect.isDark', return_value=False))
                stack.enter_context(patch.object(App, 'args', []))
                app = App(self.qapp, user_defaults=False)
                self.addCleanup(
                    self.dispose_app,
                    app,
                    original_log_handlers,
                    original_log_level,
                    original_stylesheet
                )

        self.assertEqual(
            setup_calls,
            [
                '_setup_paths_and_config',
                '_setup_gui',
                '_setup_workers_crew',
                '_setup_canvas_and_plotting',
                '_setup_tools_and_editors',
                '_setup_system_integration',
            ]
        )
        expected_handlers = {
            'lifecycle': AppLifecycle,
            'ui_actions': AppUIActions,
            'object_ops': AppObjectOps,
            'canvas_events': AppCanvasEvents,
            'signal_connector': AppSignalConnector,
        }
        for attribute, handler_class in expected_handlers.items():
            with self.subTest(handler=attribute):
                handler = getattr(app, attribute)
                self.assertIsInstance(handler, handler_class)
                self.assertIs(handler.app, app)

        self.assertIsNotNone(app.preferencesUiManager)
        self.assertIsNotNone(app.collection)
        self.assertIsNotNone(app.plotcanvas)
        with self.assertRaises(AttributeError):
            _ = app.ui.menufile_opne
        with self.assertRaises(AttributeError):
            _ = app.plotcanvas.on_toggle_hudd

        info_message = '[INFO] controlled startup'
        app.inform[str].emit(info_message)
        info_target.assert_called_once_with(app, info_message)

        project = {'options': {}}
        restore_arguments = (project, 'controlled.FlatPrj', True, False, True, False)
        app.restore_project.emit(*restore_arguments)
        app.f_handlers.restore_project_handler.assert_called_once_with(*restore_arguments)

        app.ui.hud_label.clicked.emit()
        self.assertEqual(app.plotcanvas.hud_toggles, 1)
        app.object_status_changed.emit(object(), 'append', 'object')
        self.assertEqual(len(app.collection.updates), 1)
        self.assertEqual(app.trayIcon.show_count, 1)
        app.worker_task.emit({'fcn': ARG, 'params': []})
        self.assertEqual(app.workers.tasks, [{'fcn': ARG, 'params': []}])


if __name__ == '__main__':
    unittest.main(verbosity=2)
