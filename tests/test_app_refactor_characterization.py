#!/usr/bin/env python
"""Behavior characterization shared by pre- and post-refactor App layouts."""

import importlib
import ast
import inspect
import os
import sys
import textwrap
import unittest
from itertools import product
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, call, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtWidgets

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_original_argv = sys.argv
try:
    sys.argv = [sys.argv[0]]
    from appMain import App
finally:
    sys.argv = _original_argv


FAMILIES = {
    'lifecycle': (
        'appHandlers.appLifecycle',
        'AppLifecycle',
        (
            'on_options_value_changed', 'on_defaults_dict_change', 'on_defaults2options',
            'save_project_auto', 'save_project_auto_update', 'on_portable_checked'
        )
    ),
    'ui': (
        'appHandlers.appUIActions',
        'AppUIActions',
        ('install_bookmarks', 'on_workspace_modified', 'on_workspace', 'grid_status', 'on_3d_area')
    ),
    'objects': (
        'appHandlers.appObjectOps',
        'AppObjectOps',
        (
            'on_rotate', 'on_rename_object', 'on_copy_name', 'on_deselect_all',
            'on_set_zero_click', 'on_locate'
        )
    ),
    'canvas': (
        'appHandlers.appCanvasEvents',
        'AppCanvasEvents',
        ('on_mouse_double_click_over_plot', 'delete_hover_shape', 'delete_selection_shape', 'selected_message')
    ),
    'signals': (
        'appHandlers.appSignalConnector',
        'AppSignalConnector',
        ('connect_optionsmenu_signals', 'connect_menuhelp_signals')
    )
}


class RecordingSignal:
    def __init__(self):
        self.emissions = []
        self.slots = []

    def __getitem__(self, _signature):
        return self

    def emit(self, *args):
        self.emissions.append(args)

    def connect(self, slot, *args, **kwargs):
        self.slots.append(slot)

    def disconnect(self, slot=None):
        if slot is None:
            self.slots.clear()
        elif slot in self.slots:
            self.slots.remove(slot)
        else:
            raise TypeError


class EmittingSignal(RecordingSignal):
    def emit(self, *args):
        super().emit(*args)
        for slot in list(self.slots):
            slot(*args)


class CheckBox:
    def __init__(self, value=False):
        self.value = value
        self.stateChanged = EmittingSignal()

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value
        self.stateChanged.emit(value)


class Defaults(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.report_usage = Mock()


class Timer:
    def __init__(self, active=False):
        self.active = active
        self.calls = []

    def isActive(self):
        self.calls.append(('isActive',))
        return self.active

    def stop(self):
        self.calls.append(('stop',))
        self.active = False

    def setInterval(self, interval):
        self.calls.append(('setInterval', interval))

    def start(self):
        self.calls.append(('start',))
        self.active = True


def action():
    return SimpleNamespace(triggered=RecordingSignal())


def base_app():
    return SimpleNamespace(
        log=Mock(),
        inform=RecordingSignal(),
        defaults=Defaults(),
        options={},
        ui=SimpleNamespace()
    )


def target_for(app, family):
    module_name, class_name, method_names = FAMILIES[family]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        module = importlib.import_module(App.__module__)
        for method_name in method_names:
            setattr(app, method_name, MethodType(getattr(App, method_name), app))
        return app, module

    return getattr(module, class_name)(app), module


def implementation_for(family, method_name):
    module_name, class_name, _method_names = FAMILIES[family]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return getattr(App, method_name)
    return getattr(getattr(module, class_name), method_name)


class AppCharacterizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class TestLifecycleBehavior(AppCharacterizationTest):
    def test_property_tab_refresh_keys(self):
        refresh_keys = {
            'global_grid_lines', 'global_grid_snap', 'global_axis', 'global_workspace',
            'global_workspaceT', 'global_workspace_orientation', 'global_hud'
        }
        for key in refresh_keys | {'unrelated'}:
            with self.subTest(key=key):
                app = base_app()
                app.on_properties_tab_click = Mock()
                target, _ = target_for(app, 'lifecycle')

                target.on_options_value_changed(key)

                self.assertEqual(app.on_properties_tab_click.call_count, int(key in refresh_keys))

    def test_defaults_field_is_forwarded(self):
        app = base_app()
        app.preferencesUiManager = SimpleNamespace(defaults_write_form_field=Mock())
        target, _ = target_for(app, 'lifecycle')

        target.on_defaults_dict_change('global_hud')

        app.preferencesUiManager.defaults_write_form_field.assert_called_once_with(field='global_hud')

    def test_defaults_are_read_before_project_options_update(self):
        events = []
        app = base_app()
        app.defaults.update({'units': 'IN', 'new_key': 7})
        app.options.update({'units': 'MM', 'project_only': 3})
        app.preferencesUiManager = SimpleNamespace(
            defaults_read_form=Mock(side_effect=lambda: events.append('read'))
        )
        target, _ = target_for(app, 'lifecycle')

        target.on_defaults2options()
        events.append('updated')

        self.assertEqual(events, ['read', 'updated'])
        self.assertEqual(app.options['units'], 'IN')
        self.assertEqual(app.options['new_key'], 7)
        self.assertEqual(app.options['project_only'], 3)

    def test_autosave_guard_truth_table(self):
        for block_autosave, should_save, save_in_progress in product((False, True), repeat=3):
            with self.subTest(
                    block_autosave=block_autosave,
                    should_save=should_save,
                    save_in_progress=save_in_progress):
                app = base_app()
                app.block_autosave = block_autosave
                app.should_we_save = should_save
                app.save_in_progress = save_in_progress
                save = Mock()
                app.f_handlers = SimpleNamespace(on_file_save_project=save)
                target, _ = target_for(app, 'lifecycle')

                target.save_project_auto()

                expected = not block_autosave and should_save and not save_in_progress
                self.assertEqual(save.call_count, int(expected))

    def test_autosave_timer_update(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                app = base_app()
                app.options.update({'global_autosave': enabled, 'global_autosave_timeout': '2500'})
                app.autosave_timer = Timer(active=True)
                target, _ = target_for(app, 'lifecycle')

                target.save_project_auto_update()

                self.assertEqual(app.autosave_timer.calls[:2], [('isActive',), ('stop',)])
                if enabled:
                    self.assertEqual(
                        app.autosave_timer.calls[2:],
                        [('setInterval', 2500), ('start',)]
                    )
                else:
                    self.assertEqual(app.autosave_timer.calls[2:], [])

    def test_portable_configuration_uses_project_config_directory(self):
        app = base_app()
        app.preferencesUiManager = Mock()
        target, module = target_for(app, 'lifecycle')
        opened_paths = []

        def record_open(path, *args, **kwargs):
            opened_paths.append(os.path.normpath(path))
            raise FileNotFoundError

        with patch.object(module.sys, 'platform', 'win32'), patch(
                'builtins.open', side_effect=record_open):
            target.on_portable_checked(False)

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        expected = os.path.normpath(os.path.join(project_root, 'config', 'configuration.txt'))
        self.assertEqual(opened_paths, [expected])

    def test_frozen_portable_configuration_uses_install_config_directory(self):
        app = base_app()
        app.preferencesUiManager = Mock()
        target, module = target_for(app, 'lifecycle')
        install_root = os.path.join(os.path.abspath(os.sep), 'FlatCAM')
        if module.__name__.endswith('appLifecycle'):
            module_file = os.path.join(install_root, 'lib', 'appHandlers', 'appLifecycle.py')
        else:
            module_file = os.path.join(install_root, 'lib', 'appMain.py')
        opened_paths = []

        def record_open(path, *args, **kwargs):
            opened_paths.append(os.path.normpath(path))
            raise FileNotFoundError

        with patch.object(module.sys, 'platform', 'win32'), \
                patch.object(module.sys, 'frozen', True, create=True), \
                patch.object(module, '__file__', module_file), \
                patch('builtins.open', side_effect=record_open):
            target.on_portable_checked(False)

        expected = os.path.normpath(os.path.join(install_root, 'config', 'configuration.txt'))
        self.assertEqual(opened_paths, [expected])


class TestUIBehavior(AppCharacterizationTest):
    def test_workspace_modified_call_order(self):
        events = []
        app = base_app()
        app.options['global_workspaceT'] = 'A4'
        app.plotcanvas = SimpleNamespace(
            delete_workspace=Mock(side_effect=lambda: events.append('delete')),
            draw_workspace=Mock(side_effect=lambda **kwargs: events.append(('draw', kwargs)))
        )
        app.preferencesUiManager = SimpleNamespace(
            defaults_read_form=Mock(side_effect=lambda: events.append('read'))
        )
        target, _ = target_for(app, 'ui')

        target.on_workspace_modified()

        self.assertEqual(events, ['delete', 'read', ('draw', {'workspace_size': 'A4'})])

    def test_workspace_toggle_state(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                app = base_app()
                app.options['global_workspaceT'] = 'A3'
                app.ui.general_pref_form = SimpleNamespace(
                    general_app_set_group=SimpleNamespace(
                        workspace_cb=SimpleNamespace(get_value=Mock(return_value=enabled))
                    )
                )
                app.plotcanvas = SimpleNamespace(delete_workspace=Mock(), draw_workspace=Mock())
                app.preferencesUiManager = SimpleNamespace(defaults_read_form=Mock())
                target, _ = target_for(app, 'ui')

                target.on_workspace()

                self.assertEqual(app.plotcanvas.draw_workspace.call_count, int(enabled))
                self.assertEqual(app.plotcanvas.delete_workspace.call_count, int(not enabled))
                self.assertEqual(app.inform.emissions[-1][1], False)
                self.assertIn('enabled' if enabled else 'disabled', app.inform.emissions[-1][0].lower())
                app.preferencesUiManager.defaults_read_form.assert_called_once()

    def test_workspace_toggle_runs_workspace_update_once(self):
        app = base_app()
        app.options['global_workspaceT'] = 'A3'
        workspace_cb = CheckBox(value=False)
        app.ui.general_pref_form = SimpleNamespace(
            general_app_set_group=SimpleNamespace(workspace_cb=workspace_cb)
        )
        app.plotcanvas = SimpleNamespace(delete_workspace=Mock(), draw_workspace=Mock())
        app.preferencesUiManager = SimpleNamespace(defaults_read_form=Mock())
        target, _ = target_for(app, 'ui')

        if target is app:
            workspace_slot = app.on_workspace
        else:
            app.on_workspace = MethodType(lambda _app, *_args: target.on_workspace(), app)
            workspace_slot = app.on_workspace
        workspace_cb.stateChanged.connect(workspace_slot)

        target.on_workspace_toggle()

        app.plotcanvas.draw_workspace.assert_called_once_with(workspace_size='A3')
        app.plotcanvas.delete_workspace.assert_not_called()
        app.preferencesUiManager.defaults_read_form.assert_called_once()
        self.assertEqual(workspace_cb.stateChanged.slots, [app.on_workspace])

    def test_grid_status_matches_button(self):
        for checked in (False, True):
            with self.subTest(checked=checked):
                app = base_app()
                app.ui.grid_snap_btn = SimpleNamespace(isChecked=Mock(return_value=checked))
                target, _ = target_for(app, 'ui')
                self.assertIs(target.grid_status(), checked)

    def test_3d_area_rejects_legacy_engine(self):
        app = base_app()
        app.use_3d_engine = False
        app.ui.plot_tab_area = Mock()
        target, _ = target_for(app, 'ui')

        self.assertIsNone(target.on_3d_area())
        self.assertEqual(len(app.inform.emissions), 1)
        self.assertIn('Legacy 2D', app.inform.emissions[0][0])
        app.ui.plot_tab_area.addTab.assert_not_called()

    def test_bookmark_menu_rebuild_and_limit(self):
        app = base_app()
        app.resource_location = ''
        app.options.update({'global_bookmarks': {}, 'global_bookmarks_limit': 2})
        app.ui.menuhelp_bookmarks = QtWidgets.QMenu()
        app.ui.menuhelp_bookmarks_manager = app.ui.menuhelp_bookmarks.addAction('Manager')
        app.on_backup_site = Mock()
        app.on_bookmarks_manager = Mock()
        target, _ = target_for(app, 'ui')

        target.install_bookmarks({
            '2': ['Second', 'https://two.example'],
            '1': ['First', 'https://one.example'],
            '3': ['Third', 'https://three.example']
        })

        self.assertEqual(
            [item.text() for item in app.ui.menuhelp_bookmarks.actions()],
            ['First', 'Second', 'Manager']
        )
        self.assertEqual(
            app.ui.menuhelp_bookmarks_manager.receivers(
                app.ui.menuhelp_bookmarks_manager.triggered
            ),
            1
        )

    def test_howto_format_arguments_match_placeholders(self):
        source = textwrap.dedent(inspect.getsource(implementation_for('ui', 'on_howto')))
        tree = ast.parse(source)
        format_operations = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and isinstance(node.left, ast.Constant)
            and isinstance(node.left.value, str)
            and isinstance(node.right, ast.Tuple)
        ]

        self.assertTrue(format_operations)
        for operation in format_operations:
            self.assertEqual(operation.left.value.count('%s'), len(operation.right.elts))


class TestObjectBehavior(AppCharacterizationTest):
    def test_rename_active_object(self):
        app = base_app()
        obj = SimpleNamespace(obj_options={'name': 'old'})
        app.collection = SimpleNamespace(get_active=Mock(return_value=obj))
        target, _ = target_for(app, 'objects')

        target.on_rename_object('new')

        self.assertEqual(obj.obj_options['name'], 'new')
        app.defaults.report_usage.assert_called_once_with('on_rename_object()')

    def test_copy_name_selected_and_missing(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                app = base_app()
                obj = SimpleNamespace(obj_options={'name': 'board'}) if selected else None
                app.collection = SimpleNamespace(get_active=Mock(return_value=obj))
                app.clipboard = SimpleNamespace(setText=Mock())
                target, _ = target_for(app, 'objects')

                target.on_copy_name()

                self.assertEqual(app.clipboard.setText.call_count, int(selected))
                if selected:
                    app.clipboard.setText.assert_called_once_with('board')
                    self.assertIn('copied', app.inform.emissions[-1][0].lower())
                else:
                    self.assertIn('No object', app.inform.emissions[-1][0])

    def test_deselect_all_clears_collection_and_shape(self):
        app = base_app()
        app.collection = SimpleNamespace(set_all_inactive=Mock())
        app.delete_selection_shape = Mock()
        target, _ = target_for(app, 'objects')

        target.on_deselect_all()

        app.collection.set_all_inactive.assert_called_once()
        app.delete_selection_shape.assert_called_once()

    def test_silent_rotation_uses_combined_center(self):
        app = base_app()
        first = SimpleNamespace(
            bounds=Mock(return_value=(0, 0, 10, 10)), rotate=Mock(), plot=Mock()
        )
        second = SimpleNamespace(
            bounds=Mock(return_value=(10, -10, 20, 20)), rotate=Mock(), plot=Mock()
        )
        app.collection = SimpleNamespace(get_selected=Mock(return_value=[first, second]))
        app.app_obj = SimpleNamespace(object_changed=RecordingSignal())
        target, _ = target_for(app, 'objects')

        target.on_rotate(silent=True, preset=30)

        for obj in (first, second):
            obj.rotate.assert_called_once_with(-30.0, point=(10.0, 5.0))
            obj.plot.assert_called_once()
        self.assertEqual(app.app_obj.object_changed.emissions, [(first,), (second,)])
        self.assertIn('Rotation done', app.inform.emissions[-1][0])

    def test_invalid_origin_coordinates_return_fail(self):
        app = base_app()
        app.use_3d_engine = False
        target, _ = target_for(app, 'objects')

        result = target.on_set_zero_click(None, location=[1], use_thread=False)

        self.assertEqual(result, 'fail')
        self.assertIn('incomplete', app.inform.emissions[-1][0].lower())

    def test_missing_locate_object_returns_fail(self):
        app = base_app()
        target, _ = target_for(app, 'objects')

        result = target.on_locate(None)

        self.assertEqual(result, 'fail')
        self.assertIn('No object', app.inform.emissions[-1][0])

    def test_origin_exports_remain_inside_process_context(self):
        app = base_app()
        app.use_3d_engine = False
        state = SimpleNamespace(active=False)

        class ProcessContext:
            def __enter__(self):
                state.active = True

            def __exit__(self, exc_type, exc_val, exc_tb):
                state.active = False

        app.proc_container = SimpleNamespace(new=Mock(return_value=ProcessContext()))
        obj = SimpleNamespace(
            kind='gerber',
            obj_options={'name': 'board'},
            offset=Mock(),
            bounds=Mock(return_value=(1, 2, 3, 4)),
            set_offset_values=Mock(),
            source_file=None,
            app=app,
            source_exporter='export_gerber'
        )
        # the stand-in carries the real method of the objects
        from appObjects.AppObjectTemplate import FlatCAMObj
        obj.regenerate_source = MethodType(FlatCAMObj.regenerate_source, obj)
        app.collection = SimpleNamespace(get_list=Mock(return_value=[obj]))
        app.app_obj = SimpleNamespace(object_changed=RecordingSignal())

        def export_gerber(**kwargs):
            self.assertTrue(state.active)
            return 'updated-source'

        app.f_handlers = SimpleNamespace(
            export_gerber=Mock(side_effect=export_gerber),
            export_excellon=Mock(),
            export_dxf=Mock()
        )
        app.replot_signal = RecordingSignal()
        app.should_we_save = False
        target, _ = target_for(app, 'objects')

        target.on_set_zero_click(None, location=[5, 7], use_thread=False)

        obj.offset.assert_called_once_with((5, 7))
        self.assertEqual(obj.source_file, 'updated-source')
        self.assertFalse(state.active)
        self.assertEqual(app.replot_signal.emissions, [([], )])
        self.assertTrue(app.should_we_save)


class TestCanvasBehavior(AppCharacterizationTest):
    def test_double_click_only_marks_left_button(self):
        for button, initial, expected in ((1, False, True), (2, False, False), (3, True, True)):
            with self.subTest(button=button, initial=initial):
                app = base_app()
                app.doubleclick = initial
                target, _ = target_for(app, 'canvas')

                target.on_mouse_double_click_over_plot(SimpleNamespace(button=button))

                self.assertIs(app.doubleclick, expected)

    def test_shape_deletion_clears_and_redraws(self):
        app = base_app()
        app.hover_shapes = SimpleNamespace(clear=Mock(), redraw=Mock())
        app.sel_shapes = SimpleNamespace(clear=Mock(), redraw=Mock())
        target, _ = target_for(app, 'canvas')

        target.delete_hover_shape()
        target.delete_selection_shape()

        app.hover_shapes.clear.assert_called_once()
        app.hover_shapes.redraw.assert_called_once()
        app.sel_shapes.clear.assert_called_once()
        app.sel_shapes.redraw.assert_called_once()

    def test_selected_message_color_by_object_kind(self):
        colors = {'gerber': 'green', 'excellon': 'brown', 'cncjob': 'blue', 'geometry': 'red'}
        for kind, color in colors.items():
            with self.subTest(kind=kind):
                app = base_app()
                target, _ = target_for(app, 'canvas')

                target.selected_message(SimpleNamespace(kind=kind, obj_options={'name': 'sample'}))

                message = app.inform.emissions[-1][0]
                self.assertIn(color, message)
                self.assertIn('sample', message)
                self.assertIn('selected', message)


class TestSignalBehavior(AppCharacterizationTest):
    def test_options_menu_connections_and_database_source(self):
        app = base_app()
        names = (
            'menuoptions_transform_rotate', 'menuoptions_transform_skewx',
            'menuoptions_transform_skewy', 'menuoptions_transform_flipx',
            'menuoptions_transform_flipy', 'menuoptions_view_source',
            'menuoptions_tools_db', 'menuoptions_experimental_3D_area'
        )
        for name in names:
            setattr(app.ui, name, action())
        for name in (
            'on_rotate', 'on_skewx', 'on_skewy', 'on_flipx', 'on_flipy',
            'on_view_source', 'on_tools_database', 'on_3d_area'
        ):
            setattr(app, name, Mock())
        target, _ = target_for(app, 'signals')

        target.connect_optionsmenu_signals()

        for name in names:
            self.assertEqual(len(getattr(app.ui, name).triggered.slots), 1)
        app.ui.menuoptions_tools_db.triggered.slots[0]()
        app.on_tools_database.assert_called_once_with(source='app')

    def test_help_menu_urls_and_direct_targets(self):
        app = base_app()
        names = (
            'menuhelp_about', 'menuhelp_readme', 'menuhelp_donate', 'menuhelp_manual',
            'menuhelp_report_bug', 'menuhelp_exc_spec', 'menuhelp_gerber_spec',
            'menuhelp_videohelp', 'menuhelp_shortcut_list'
        )
        for name in names:
            setattr(app.ui, name, action())
        app.on_about = Mock()
        app.on_howto = Mock()
        app.ui.on_shortcut_list = Mock()
        urls = {
            'menuhelp_donate': 'https://donate.example',
            'menuhelp_manual': 'https://manual.example',
            'menuhelp_report_bug': 'https://bug.example',
            'menuhelp_exc_spec': 'https://exc.example',
            'menuhelp_gerber_spec': 'https://gerber.example',
            'menuhelp_videohelp': 'https://video.example'
        }
        app.donate_url = urls['menuhelp_donate']
        app.manual_url = urls['menuhelp_manual']
        app.bug_report_url = urls['menuhelp_report_bug']
        app.excellon_spec_url = urls['menuhelp_exc_spec']
        app.gerber_spec_url = urls['menuhelp_gerber_spec']
        app.video_url = urls['menuhelp_videohelp']
        target, module = target_for(app, 'signals')

        with patch.object(module.webbrowser, 'open') as open_url:
            target.connect_menuhelp_signals()
            for action_name, expected_url in urls.items():
                getattr(app.ui, action_name).triggered.slots[0]()
                open_url.assert_any_call(expected_url)

        for name in names:
            self.assertEqual(len(getattr(app.ui, name).triggered.slots), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
