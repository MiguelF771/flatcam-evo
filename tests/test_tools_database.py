"""Focused Tools Database regression checks using temporary databases only."""

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6 import QtCore, QtWidgets
from appDatabase import ToolsDB2
from appGUI.GUIElements import FCComboBox2, FCDoubleSpinner, FCDetachableTab2
from defaults import AppDefaults


def record(target=0, diameter=2.4):
    options = AppDefaults().defaults
    data = {key: deepcopy(value) for key, value in options.items()
            if key.startswith('tools_')}
    data.update(tool_target=target, tol_min=0.0, tol_max=0.0)
    return {'name': 'fixture', 'tooldia': diameter, 'data': data}


class Tabs(QtWidgets.QTabWidget):
    def __init__(self):
        super().__init__()
        self.tabBar = super().tabBar()


class DecisionMessageBox:
    response = 'yes'

    def __init__(self, parent=None):
        self.buttons = {}

    def addButton(self, label, role):
        self.buttons[str(label)] = object()
        return self.buttons[str(label)]

    def clickedButton(self):
        return self.buttons[{'yes': 'Yes', 'no': 'No', 'cancel': 'Cancel'}[self.response]]

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class DatabaseCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tools.FlatDB'
        self.path.write_text('{}', encoding='utf-8')
        self.tabs = Tabs()
        self.addCleanup(self.tabs.deleteLater)
        self.app = SimpleNamespace(
            decimals=4, app_units='MM', resource_location=str(ROOT / 'assets/resources'),
            options=AppDefaults().defaults, defaults=MagicMock(), inform=MagicMock(),
            log=MagicMock(), tools_db_changed_flag=False,
            tools_database_path=lambda: str(self.path),
            dec_format=lambda value, decimals: round(value, decimals),
            ui=SimpleNamespace(plot_tab_area=self.tabs))
        self.slot_errors = []
        hook = patch.object(sys, 'excepthook', lambda *error: self.slot_errors.append(error))
        hook.start()
        self.addCleanup(hook.stop)

    def database(self, contents=None):
        if contents is not None:
            self.path.write_text(json.dumps(contents), encoding='utf-8')
        db = ToolsDB2(self.app, callback_on_tool_request=MagicMock())
        self.addCleanup(db.deleteLater)
        self.tabs.addTab(db, 'Tools Database')
        self.app.tools_db_tab = db
        return db

    def detached_database(self, contents=None):
        tabs = FCDetachableTab2()
        self.tabs = tabs
        self.app.ui.plot_tab_area = tabs
        db = self.database(contents)
        tabs.detachTab(0, QtCore.QPoint(1, 1))
        self.addCleanup(tabs.deleteLater)
        self.addCleanup(tabs.closeDetachedTabs)
        return db, tabs, next(iter(tabs.detachedTabs.values()))

    def actions_handler(self, tabs=None):
        tabs = tabs or self.tabs
        tabs.plot_tab_area = tabs
        tabs.coords_toolbar = MagicMock()
        tabs.delta_coords_toolbar = MagicMock()
        return SimpleNamespace(app=self.app, ui=tabs, log=self.app.log,
                               inform=self.app.inform,
                               on_geometry_tool_add_from_db_executed=MagicMock())


class LifecycleChecks(DatabaseCase):
    def shutdown_lifecycle(self, database):
        from appHandlers.appLifecycle import AppLifecycle

        self.app.tools_db_tab = database
        self.app.preferencesUiManager = MagicMock()
        self.app.save_in_progress = False
        self.app.should_we_save = False
        self.app.defaults = MagicMock()
        self.app.ui.hide = MagicMock()
        self.app.autosave_timer = MagicMock()
        self.app.new_launch = SimpleNamespace(thread_exit=False, address=('127.0.0.1', 1))
        self.app.listen_th = MagicMock()
        self.app.geo_editor = None
        self.app.exc_editor = None
        self.app.grb_editor = None
        self.app.gcode_editor = None
        # quit_application looks the editors up through App._editor_for (Stage 5); no editor exists here
        self.app._editor_for = lambda kind: None
        self.app.use_3d_engine = False
        self.app.plotcanvas = MagicMock()
        self.app.mm = MagicMock()
        self.app.mp = MagicMock()
        self.app.mr = MagicMock()
        self.app.mdc = MagicMock()
        self.app.kp = MagicMock()
        self.app.cmd_line_headless = 1
        self.app.pool = MagicMock()
        self.app.workers = MagicMock()
        return AppLifecycle(self.app)

    def test_shutdown_honors_database_cancel_save_failure_and_success(self):
        from PyQt6 import QtWidgets

        for outcome, decision, expected in (
                ('cancel', False, False), ('save failure', False, False),
                ('save success', True, True), ('discard', True, True)):
            with self.subTest(outcome=outcome):
                database = MagicMock()
                database.confirm_close.return_value = decision
                lifecycle = self.shutdown_lifecycle(database)
                with patch('appHandlers.appLifecycle.sys.platform', 'linux'), \
                        patch.object(QtWidgets.QApplication, 'quit') as quit_app:
                    result = lifecycle.quit_application(silent=True)
                self.assertEqual(expected, result)
                database.confirm_close.assert_called_once_with()
                if expected:
                    self.app.ui.hide.assert_called_once_with()
                    quit_app.assert_called_once_with()
                else:
                    self.app.ui.hide.assert_not_called()
                    quit_app.assert_not_called()


class EditorChecks(DatabaseCase):
    def test_normalization_preserves_input_and_repairs_aliases(self):
        import appDatabase
        normalize = getattr(appDatabase, 'normalize_tools_database', None)
        self.assertTrue(callable(normalize))
        if not callable(normalize):
            return

        source = {'7': record(4)}
        source['7']['data']['tool_target'] = 'Paint'
        source['7']['data']['job'] = 'Finishing'
        source['7']['tools_mill_offset_value'] = 0.75
        source['7']['data']['tools_cutout_gap_type'] = 'mb'
        source['7']['vendor_note'] = {'keep': True}
        before = deepcopy(source)

        result = normalize(source, self.app.options)

        self.assertEqual(before, source)
        self.assertEqual(4, result['7']['data']['tool_target'])
        self.assertEqual(1, result['7']['data']['tools_mill_job_type'])
        self.assertEqual(0.75, result['7']['data']['tools_mill_offset_value'])
        self.assertEqual(2, result['7']['data']['tools_cutout_gap_type'])
        self.assertEqual({'keep': True}, result['7']['vendor_note'])

    def test_normalization_rejects_invalid_records(self):
        import appDatabase
        normalize = getattr(appDatabase, 'normalize_tools_database', None)
        self.assertTrue(callable(normalize))
        if not callable(normalize):
            return

        with self.assertRaises(ValueError):
            normalize([], self.app.options)

        invalid = {'01': record()}
        with self.assertRaises(ValueError):
            normalize(invalid, self.app.options)

        invalid = {'7': record()}
        invalid['7']['data']['tool_target'] = 'Unknown target'
        before = deepcopy(invalid)
        with self.assertRaises(ValueError):
            normalize(invalid, self.app.options)
        self.assertEqual(before, invalid)

    def test_load_accepts_utf8_bom_and_rejects_import_transactionally(self):
        import appDatabase

        valid = {'7': record(4)}
        self.path.write_bytes(b'\xef\xbb\xbf' + json.dumps(valid).encode('utf-8'))
        loaded = appDatabase.load_tools_database(self.path, self.app.options)
        self.assertEqual(4, loaded['7']['data']['tool_target'])

        db = self.database({'1': record()})
        before_state = deepcopy(db.db_tool_dict)
        before_bytes = self.path.read_bytes()
        self.app.tools_db_changed_flag = True
        invalid_path = self.path.parent / 'invalid.FlatDB'
        invalid_path.write_text('[]', encoding='utf-8')
        with patch('appDatabase.QtWidgets.QFileDialog.getOpenFileName',
                   return_value=(str(invalid_path), '')):
            self.assertFalse(db.on_import_tools_db_file())
        self.assertEqual(before_state, db.db_tool_dict)
        self.assertTrue(self.app.tools_db_changed_flag)
        self.assertEqual(before_bytes, self.path.read_bytes())

    def test_normalization_rejects_nonfinite_bounds_and_known_bad_types(self):
        import appDatabase

        cases = []
        nan_diameter = record()
        nan_diameter['tooldia'] = float('nan')
        cases.append(nan_diameter)
        inverted = record()
        inverted['data']['tol_min'], inverted['data']['tol_max'] = (2.0, 1.0)
        cases.append(inverted)
        bad_option = record()
        bad_option['data']['tools_mill_feedrate'] = {'not': 'a number'}
        cases.append(bad_option)
        for value in cases:
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'record 7'):
                    appDatabase.normalize_tools_database({'7': value}, self.app.options)

    def test_invalid_initial_load_keeps_import_as_recovery_path(self):
        try:
            db = self.database([])
        except Exception as error:
            self.fail('invalid initial load raised %r' % error)

        self.assertFalse(getattr(db, '_db_load_valid', True))
        self.assertFalse(db.ui.save_db_btn.isEnabled())
        self.assertFalse(db.ui.export_db_btn.isEnabled())
        self.assertFalse(db.ui.add_tool_from_db.isEnabled())
        self.assertTrue(db.ui.import_db_btn.isEnabled())

    def test_cutout_gap_type_roundtrip(self):
        db = self.database()
        db.ui.cutout_gaptype_radio.blockSignals(True)
        for value in (0, 1, 2):
            with self.subTest(value=value):
                db.ui.cutout_gaptype_radio.set_value(value)
                self.assertEqual(value, db.ui.cutout_gaptype_radio.get_value())

    def test_numeric_milling_dropdowns_roundtrip(self):
        db = self.database()
        for widget in (db.ui.mill_shape_combo, db.ui.job_type_combo,
                       db.ui.mill_tooloffset_type_combo):
            with self.subTest(widget=widget.objectName()):
                widget.blockSignals(True)
                widget.set_value(2)
                self.assertEqual(2, widget.get_value())

    def test_v_tool_diameter_calculation_uses_numeric_shape_id(self):
        db = self.database()
        db.ui.mill_shape_combo.set_value(5)
        db.ui.mill_vdia_entry.set_value(0.2)
        db.ui.mill_vangle_entry.set_value(60)
        for cut_z in (-0.1, 0.1):
            with self.subTest(cut_z=cut_z):
                db.ui.mill_cutz_entry.set_value(cut_z)
                db.on_calculate_tooldia()
                self.assertAlmostEqual(0.3155, db.ui.dia_entry.get_value(), places=4)

        db.ui.mill_shape_combo.set_value(0)
        diameter = db.ui.dia_entry.get_value()
        db.ui.mill_cutz_entry.set_value(-4.0)
        db.on_calculate_tooldia()
        self.assertEqual(diameter, db.ui.dia_entry.get_value())

    def test_job_and_offset_write_canonical_keys(self):
        db = self.database()
        for name, value, key, widget in (
                ('gdb_job', 2, 'tools_mill_job_type', FCComboBox2()),
                ('gdb_custom_offset', 0.75, 'tools_mill_offset_value', FCDoubleSpinner())):
            with self.subTest(name=name):
                if isinstance(widget, FCComboBox2):
                    widget.addItems(['a', 'b', 'c'])
                widget.setObjectName(name)
                widget.set_value(value)
                target = record()
                item = MagicMock()
                item.data.return_value = '1'
                stub = SimpleNamespace(
                    current_toolid=1, sender=lambda: widget, app=self.app,
                    db_tool_dict={'1': target}, name2option=db.name2option,
                    ui=SimpleNamespace(tree_widget=SimpleNamespace(selectedItems=lambda: [item])),
                    on_tools_db_edited=MagicMock())
                ToolsDB2.update_storage(stub)
                self.assertEqual(value, target['data'][key])
                self.assertNotIn('job', target['data'])
                self.assertNotIn('tools_mill_offset_value', target)

    def test_update_storage_ignores_missing_or_unmapped_sender(self):
        target = record()
        edited = MagicMock()
        stub = SimpleNamespace(current_toolid=1, sender=lambda: None, app=self.app,
                               db_tool_dict={'1': target}, on_tools_db_edited=edited)
        ToolsDB2.update_storage(stub)
        edited.assert_not_called()
        stub.sender = lambda: object()
        ToolsDB2.update_storage(stub)
        edited.assert_not_called()

    def test_tree_rename_updates_the_row_that_was_edited(self):
        db = self.database({'7': record(), '42': record(3, 0.8)})
        item = db.ui.tree_widget.topLevelItem(1)
        item.setText(1, 'renamed')
        self.assertEqual('renamed', db.db_tool_dict['42']['name'])
        self.assertEqual(42, db.current_toolid)

    def test_sparse_ids_render_actual_keys(self):
        db = self.database({'7': record(), '42': record(3, 0.8)})
        self.assertEqual(['7', '42'], [db.ui.tree_widget.topLevelItem(i).text(0)
                                     for i in range(2)])
        self.assertFalse(self.slot_errors)

    def test_sparse_ids_survive_copy_sort_delete_and_request(self):
        db = self.database({'7': record(), '42': record(3, 0.8)})
        db.ui.tree_widget.clearSelection()
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        db.on_tool_copy()
        self.assertEqual({'7', '42', '43'}, set(db.db_tool_dict))

        db.on_sort_dia()
        db.on_sort_target()
        self.assertEqual({'7', '42', '43'}, set(db.db_tool_dict))

        db.ui.tree_widget.clearSelection()
        for row in range(db.ui.tree_widget.topLevelItemCount()):
            item = db.ui.tree_widget.topLevelItem(row)
            if item.text(0) == '42':
                item.setSelected(True)
                break
        db.on_tool_delete()
        self.assertEqual({'7', '43'}, set(db.db_tool_dict))

        db.ui.tree_widget.clearSelection()
        for row in range(db.ui.tree_widget.topLevelItemCount()):
            item = db.ui.tree_widget.topLevelItem(row)
            if item.text(0) == '43':
                item.setSelected(True)
                break
        db.confirm_close = MagicMock(return_value=True)
        db.on_tool_requested_from_app()
        self.assertEqual('fixture', db.on_tool_request.call_args.kwargs['tool']['name'])

    def test_empty_database_can_add_delete_last_and_add_again(self):
        db = self.database()
        db.on_tool_add()
        self.assertEqual({'1'}, set(db.db_tool_dict))
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        db.on_tool_delete()
        self.assertEqual({}, db.db_tool_dict)
        db.on_tool_add()
        self.assertEqual({'1'}, set(db.db_tool_dict))

        db.db_tool_dict['1']['name'] = 'new_tool_custom'
        db.on_tool_add()
        self.assertEqual({'1', '2'}, set(db.db_tool_dict))

    def test_database_request_delivers_an_independent_copy(self):
        db = self.database({'1': record()})
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        original = deepcopy(db.db_tool_dict['1'])

        def mutate(tool):
            tool['data']['tools_mill_feedrate'] = 321.0

        db.on_tool_request = mutate
        db.on_tool_requested_from_app()
        self.assertEqual(original, db.db_tool_dict['1'])

    def test_new_tool_has_drill_dwelltime(self):
        db = self.database()
        db.ui.add_entry_btn.click()
        self.assertFalse(self.slot_errors)
        self.assertEqual(self.app.options['tools_drill_dwelltime'],
                         db.db_tool_dict['1']['data'].get('tools_drill_dwelltime'))

    def test_failed_save_keeps_dirty_flag_and_bytes(self):
        db = self.database({'1': record()})
        before = self.path.read_bytes()
        db.db_tool_dict['1']['name'] = 'edited'
        db.on_tools_db_edited(silent=True)
        with patch('appDatabase.write_tools_database',
                   side_effect=PermissionError('injected failure'), create=True):
            db.on_save_db_btn_click()
        self.assertTrue(self.app.tools_db_changed_flag)
        self.assertEqual(before, self.path.read_bytes())
        self.assertNotEqual(db.ui.save_db_btn.default_stylesheet, db.ui.save_db_btn.styleSheet())

    def test_save_without_visible_tab_writes_database(self):
        db = self.database({'1': record()})
        db.db_tool_dict['1']['name'] = 'saved after tab removal'
        db.on_tools_db_edited(silent=True)
        self.tabs.removeTab(0)
        self.assertTrue(db.on_save_tools_db())
        self.assertEqual('saved after tab removal', json.loads(self.path.read_text())['1']['name'])
        self.assertEqual(db.ui.save_db_btn.default_stylesheet, db.ui.save_db_btn.styleSheet())

    def test_atomic_writer_saves_non_ascii_records(self):
        import appDatabase

        writer = getattr(appDatabase, 'write_tools_database', None)
        self.assertTrue(callable(writer))
        if not callable(writer):
            return
        writer(self.path, {'7': {'name': 'фреза', 'tooldia': 1.0, 'data': {}}})
        self.assertEqual('фреза', json.loads(self.path.read_text(encoding='utf-8'))['7']['name'])

    def test_atomic_writer_cancels_failed_write_and_commit(self):
        import appDatabase

        class FakeSaveFile:
            mode = 'write'
            cancelled = False

            def __init__(self, filename):
                self.filename = filename

            def setDirectWriteFallback(self, value):
                pass

            def open(self, mode):
                return True

            def write(self, payload):
                return 0 if self.mode == 'write' else len(payload)

            def commit(self):
                return self.mode != 'commit'

            def errorString(self):
                return 'injected failure'

            def cancelWriting(self):
                self.cancelled = True

        for mode in ('write', 'commit'):
            with self.subTest(mode=mode), patch.object(appDatabase.QtCore, 'QSaveFile', FakeSaveFile):
                FakeSaveFile.mode = mode
                with self.assertRaises(OSError):
                    appDatabase.write_tools_database(self.path, {'1': {}})

        with (patch.object(appDatabase.json, 'dumps', side_effect=TypeError('serialize')),
              patch.object(appDatabase.QtCore, 'QSaveFile') as save_file):
            with self.assertRaises(TypeError):
                appDatabase.write_tools_database(self.path, {'1': {}})
            save_file.assert_not_called()

    def test_save_preserves_unknown_fields_and_clears_dirty_after_commit(self):
        db_record = record(4)
        db_record['vendor_note'] = {'keep': True}
        db_record['data']['future_option'] = ['keep']
        db = self.database({'7': db_record})
        db.db_tool_dict['7']['data']['tools_paint_overlap'] = 23.0
        self.app.tools_db_changed_flag = True

        self.assertTrue(db.on_save_tools_db(silent=True))
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual({'keep': True}, saved['7']['vendor_note'])
        self.assertEqual(['keep'], saved['7']['data']['future_option'])
        self.assertEqual(23.0, saved['7']['data']['tools_paint_overlap'])
        self.assertFalse(self.app.tools_db_changed_flag)

    def test_export_uses_atomic_writer_without_clearing_dirty_state(self):
        import appDatabase

        db = self.database({'1': record()})
        export_path = self.path.parent / 'export.FlatDB'
        self.app.get_last_save_folder = lambda: str(self.path.parent)
        self.app.tools_db_changed_flag = True
        with patch.object(appDatabase.FCFileSaveDialog, 'get_saved_filename',
                          return_value=(str(export_path), '')):
            self.assertTrue(db.on_export_tools_db_file())
        self.assertTrue(self.app.tools_db_changed_flag)
        self.assertEqual('fixture', json.loads(export_path.read_text(encoding='utf-8'))['1']['name'])

    def test_successful_request_closes_database_container_once(self):
        db = self.database({'1': record()})
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        db.on_tool_request = MagicMock(return_value=True)
        self.tabs.closeTab = MagicMock(return_value=True)

        db.on_tool_requested_from_app()

        self.tabs.closeTab.assert_called_once_with(0)

    def test_request_failure_keeps_database_open(self):
        db = self.database({'1': record()})
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        db.on_tool_request = MagicMock(return_value='fail')
        self.tabs.closeTab = MagicMock(return_value=True)

        self.assertFalse(db.on_tool_requested_from_app())
        self.tabs.closeTab.assert_not_called()

    def test_discard_failed_request_restores_dirty_state(self):
        import appDatabase

        db = self.database({'1': record()})
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        db.on_tool_request = MagicMock(return_value='fail')
        self.app.tools_db_changed_flag = True
        DecisionMessageBox.response = 'no'

        with patch.object(appDatabase, 'FCMessageBox', DecisionMessageBox):
            self.assertFalse(db.on_tool_requested_from_app())

        self.assertTrue(self.app.tools_db_changed_flag)
        self.assertEqual(1, self.tabs.count())

    def test_discard_successful_request_closes_and_clears_dirty_state(self):
        import appDatabase

        db = self.database({'1': record()})
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        db.on_tool_request = MagicMock(return_value=True)
        self.app.tools_db_changed_flag = True
        self.tabs.closeTab = MagicMock(
            side_effect=lambda index: setattr(self.app, 'tools_db_changed_flag', False) or True)
        DecisionMessageBox.response = 'no'

        with patch.object(appDatabase, 'FCMessageBox', DecisionMessageBox):
            self.assertTrue(db.on_tool_requested_from_app())

        self.assertFalse(self.app.tools_db_changed_flag)
        self.tabs.closeTab.assert_called_once_with(0)

    def test_save_failed_request_keeps_successfully_saved_database_clean(self):
        import appDatabase

        db = self.database({'1': record()})
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        db.on_tool_request = MagicMock(return_value='fail')
        self.app.tools_db_changed_flag = True

        def save():
            self.app.tools_db_changed_flag = False
            return True

        db.on_save_tools_db = save
        DecisionMessageBox.response = 'yes'
        with patch.object(appDatabase, 'FCMessageBox', DecisionMessageBox):
            self.assertFalse(db.on_tool_requested_from_app())

        self.assertFalse(self.app.tools_db_changed_flag)
        self.assertEqual(1, self.tabs.count())

    def test_partial_request_failure_keeps_database_open_without_rollback(self):
        db = self.database({'1': record(), '2': record(1)})
        db.ui.tree_widget.clearSelection()
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        db.ui.tree_widget.topLevelItem(1).setSelected(True)
        db.on_tool_request = MagicMock(side_effect=[True, False])
        self.tabs.closeTab = MagicMock(return_value=True)

        self.assertFalse(db.on_tool_requested_from_app())

        self.assertEqual(2, db.on_tool_request.call_count)
        self.tabs.closeTab.assert_not_called()
        self.assertEqual(1, self.tabs.count())

    def test_confirm_close_save_discard_cancel(self):
        import appDatabase

        class FakeMessageBox:
            response = 'yes'

            def __init__(self, parent=None):
                self.buttons = {}

            def addButton(self, label, role):
                self.buttons[str(label)] = object()
                return self.buttons[str(label)]

            def clickedButton(self):
                return self.buttons[{'yes': 'Yes', 'no': 'No', 'cancel': 'Cancel'}[self.response]]

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        for response, expected, dirty in (('yes', True, True), ('no', True, False),
                                          ('cancel', False, True)):
            with self.subTest(response=response), patch.object(appDatabase, 'FCMessageBox', FakeMessageBox):
                FakeMessageBox.response = response
                db = self.database({'1': record()})
                db.app.tools_db_changed_flag = True
                db.on_save_tools_db = MagicMock(return_value=True)
                self.assertEqual(expected, db.confirm_close())
                self.assertEqual(dirty, db.app.tools_db_changed_flag)

        db = self.database({'1': record()})
        db._close_preapproved = True
        self.assertTrue(db.confirm_close())
        self.assertFalse(db._close_preapproved)

    def test_confirm_close_keeps_dirty_state_when_save_fails(self):
        import appDatabase

        class SaveFailureMessageBox:
            def __init__(self, parent=None):
                self.yes = object()

            def addButton(self, label, role):
                return self.yes if str(label) == 'Yes' else object()

            def clickedButton(self):
                return self.yes

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        with patch.object(appDatabase, 'FCMessageBox', SaveFailureMessageBox):
            db = self.database({'1': record()})
            db.app.tools_db_changed_flag = True
            db.on_save_tools_db = MagicMock(return_value=False)
            self.assertFalse(db.confirm_close())
            self.assertTrue(db.app.tools_db_changed_flag)

    def test_detachable_database_tab_honors_close_decision(self):
        tabs = FCDetachableTab2()
        self.addCleanup(tabs.deleteLater)
        widget = QtWidgets.QWidget()
        widget.setObjectName('database_tab')
        widget.confirm_close = MagicMock(return_value=False)
        tabs.addTab(widget, 'Tools Database')
        closed = MagicMock()
        tabs.tab_closed_signal.connect(closed)

        self.assertFalse(tabs.closeTab(0))
        self.assertEqual(1, tabs.count())
        closed.assert_not_called()

        widget.confirm_close.return_value = True
        self.assertTrue(tabs.closeTab(0))
        self.assertEqual(0, tabs.count())
        closed.assert_called_once_with('database_tab', 0)

    def test_manual_detached_close_reattaches_without_confirming_or_callback(self):
        db, tabs, detached = self.detached_database({'1': record()})
        db.app.tools_db_changed_flag = True
        db.confirm_close = MagicMock(return_value=False)
        db.on_tool_request = MagicMock()

        self.assertTrue(detached.close())

        db.confirm_close.assert_not_called()
        db.on_tool_request.assert_not_called()
        self.assertEqual(1, tabs.count())
        self.assertIs(db, tabs.widget(0))
        self.assertTrue(db.app.tools_db_changed_flag)
        if sys.platform == 'win32':
            self.assertEqual(0, len(tabs.detachedTabs))

    def test_detached_picker_cancel_honors_close_decision(self):
        import appDatabase

        db, tabs, detached = self.detached_database({'1': record()})
        db.app.tools_db_changed_flag = True
        box = MagicMock(side_effect=DecisionMessageBox)
        DecisionMessageBox.response = 'cancel'

        with patch.object(appDatabase, 'FCMessageBox', box):
            self.assertFalse(db.on_cancel_tool())
        self.assertEqual(0, tabs.count())
        self.assertEqual(1, len(tabs.detachedTabs))

        box.reset_mock()
        DecisionMessageBox.response = 'no'
        with patch.object(appDatabase, 'FCMessageBox', box):
            self.assertTrue(db.on_cancel_tool())
        self.assertEqual(1, box.call_count)
        self.assertEqual(0, tabs.count())
        self.assertEqual(0, len(tabs.detachedTabs))

    def test_detached_picker_cancel_saves_before_closing(self):
        import appDatabase

        db, tabs, detached = self.detached_database({'1': record()})
        db.app.tools_db_changed_flag = True
        db.on_save_tools_db = MagicMock(
            side_effect=lambda: setattr(db.app, 'tools_db_changed_flag', False) or True)
        box = MagicMock(side_effect=DecisionMessageBox)
        DecisionMessageBox.response = 'yes'

        with patch.object(appDatabase, 'FCMessageBox', box):
            self.assertTrue(db.on_cancel_tool())

        db.on_save_tools_db.assert_called_once_with()
        self.assertEqual(1, box.call_count)
        self.assertFalse(db.app.tools_db_changed_flag)
        self.assertEqual(0, tabs.count())
        self.assertEqual(0, len(tabs.detachedTabs))


class ConsumerChecks(DatabaseCase):
    def test_new_database_add_tab_failure_returns_fail_without_picker_success(self):
        from appHandlers.appUIActions import AppUIActions

        handler = self.actions_handler()
        self.tabs.addTab = MagicMock(side_effect=RuntimeError('injected addTab failure'))

        result = AppUIActions.on_tools_database(handler, source='app')

        self.assertEqual('fail', result)
        self.assertIsInstance(self.app.tools_db_tab, ToolsDB2)
        self.assertEqual(0, self.tabs.count())
        handler.ui.coords_toolbar.hide.assert_not_called()
        handler.ui.delta_coords_toolbar.hide.assert_not_called()

    def test_existing_picker_rebinds_callback(self):
        from appHandlers.appUIActions import AppUIActions
        db = self.database()
        paint_callback = MagicMock()
        self.app.paint_tool = SimpleNamespace(on_paint_tool_add_from_db_executed=paint_callback)
        handler = SimpleNamespace(app=self.app, ui=self.app.ui, log=self.app.log,
                                  inform=self.app.inform)
        AppUIActions.on_tools_database(handler, source='paint')
        self.assertIs(paint_callback, db.on_tool_request)

    def test_detached_picker_reuses_editor_rebinds_and_focuses_window(self):
        from appHandlers.appUIActions import AppUIActions

        db, tabs, detached = self.detached_database({'1': record()})
        db.db_tool_dict['1']['name'] = 'edited while detached'
        self.app.tools_db_changed_flag = True
        callback = MagicMock()
        self.app.paint_tool = SimpleNamespace(on_paint_tool_add_from_db_executed=callback)
        handler = self.actions_handler(tabs)

        with patch.object(detached, 'raise_') as raise_window, \
                patch.object(detached, 'activateWindow') as activate_window:
            self.assertEqual('success', AppUIActions.on_tools_database(handler, source='paint'))

        self.assertIs(db, self.app.tools_db_tab)
        self.assertIs(callback, db.on_tool_request)
        self.assertEqual('edited while detached', db.db_tool_dict['1']['name'])
        self.assertTrue(self.app.tools_db_changed_flag)
        self.assertEqual(0, tabs.count())
        self.assertEqual(1, len(tabs.detachedTabs))
        raise_window.assert_called_once_with()
        activate_window.assert_called_once_with()

    def test_missing_database_opens_recovery_editor_without_creating_file(self):
        from appHandlers.appUIActions import AppUIActions

        self.path.unlink()
        handler = self.actions_handler()

        self.assertEqual('fail', AppUIActions.on_tools_database(handler, source='app'))
        self.assertIsInstance(self.app.tools_db_tab, ToolsDB2)
        self.assertFalse(self.app.tools_db_tab._db_load_valid)
        self.assertTrue(self.app.tools_db_tab.ui.import_db_btn.isEnabled())
        self.assertFalse(self.path.exists())

    def test_unreadable_database_opens_recovery_editor_without_overwriting(self):
        from appHandlers.appUIActions import AppUIActions

        self.path.unlink()
        self.path.mkdir()
        handler = self.actions_handler()

        self.assertEqual('fail', AppUIActions.on_tools_database(handler, source='app'))
        self.assertIsInstance(self.app.tools_db_tab, ToolsDB2)
        self.assertFalse(self.app.tools_db_tab._db_load_valid)
        self.assertTrue(self.app.tools_db_tab.ui.import_db_btn.isEnabled())
        self.assertTrue(self.path.is_dir())

    def test_existing_picker_rebinds_every_supported_callback(self):
        from appHandlers.appUIActions import AppUIActions

        db = self.database()
        callbacks = {
            'app': MagicMock(),
            'paint': MagicMock(),
            'ncc': MagicMock(),
            'iso': MagicMock(),
            'cutout': MagicMock(),
        }
        self.app.paint_tool = SimpleNamespace(on_paint_tool_add_from_db_executed=callbacks['paint'])
        self.app.ncclear_tool = SimpleNamespace(on_ncc_tool_add_from_db_executed=callbacks['ncc'])
        self.app.isolation_tool = SimpleNamespace(on_iso_tool_add_from_db_executed=callbacks['iso'])
        self.app.cutout_tool = SimpleNamespace(on_cutout_tool_add_from_db_executed=callbacks['cutout'])
        handler = SimpleNamespace(app=self.app, ui=self.app.ui, log=self.app.log,
                                  inform=self.app.inform,
                                  on_geometry_tool_add_from_db_executed=callbacks['app'])

        for source, callback in callbacks.items():
            with self.subTest(source=source):
                self.assertEqual('success', AppUIActions.on_tools_database(handler, source=source))
                self.assertIs(callback, db.on_tool_request)
        self.assertEqual('fail', AppUIActions.on_tools_database(handler, source='unknown'))

    def test_picker_callback_leaves_database_closure_to_editor(self):
        from appPlugins.ToolPaint.Paint import ToolPaint

        tab = QtWidgets.QWidget()
        tab.setObjectName('database_tab')
        self.tabs.addTab(tab, 'Tools Database')
        table = MagicMock()
        table.rowCount.return_value = 0
        stub = SimpleNamespace(
            app=self.app, decimals=4,
            on_paint_tool_from_db_inserted=MagicMock(return_value=1),
            on_row_selection_change=MagicMock(),
            ui=SimpleNamespace(tools_table=table))

        result = ToolPaint.on_paint_tool_add_from_db_executed(stub, record(4))

        self.assertEqual(1, result)
        self.assertEqual(1, self.tabs.count())

    def test_successful_detached_request_closes_detached_database_once(self):
        db, tabs, detached = self.detached_database({'1': record()})
        db.ui.tree_widget.topLevelItem(0).setSelected(True)
        db.on_tool_request = MagicMock(return_value=True)

        self.assertTrue(db.on_tool_requested_from_app())

        db.on_tool_request.assert_called_once()
        self.assertEqual(0, tabs.count())
        self.assertEqual(0, len(tabs.detachedTabs))

    def test_geometry_callback_propagates_insertion_failure(self):
        from appHandlers.appUIActions import AppUIActions

        tool = record(1)
        self.app.collection = SimpleNamespace(get_active=lambda: SimpleNamespace(kind='geometry'))
        self.app.milling_tool = SimpleNamespace(on_tool_from_db_inserted=MagicMock(return_value=False))
        handler = SimpleNamespace(app=self.app, ui=self.app.ui, inform=self.app.inform)

        self.assertEqual('fail', AppUIActions.on_geometry_tool_add_from_db_executed(handler, tool))
        self.app.milling_tool.on_tool_from_db_inserted.assert_called_once()

    def test_milling_numeric_target_baseline(self):
        from appPlugins.ToolMilling import ToolMilling
        tool = record(1)
        tool['data']['tools_mill_feedrate'] = 321.0
        self.path.write_text(json.dumps({'1': tool}), encoding='utf-8')
        obj = SimpleNamespace(tools={}, solid_geometry=[], build_ui=MagicMock())
        table = MagicMock()
        table.rowCount.return_value = 0
        stub = SimpleNamespace(
            app=self.app, target_obj=obj, decimals=4, default_data=deepcopy(record()['data']),
            ui_disconnect=MagicMock(), ui_connect=MagicMock(), build_ui=MagicMock(),
            update_ui=MagicMock(), on_tool_default_add=MagicMock(),
            ui=SimpleNamespace(tools_table_mill_geo=table, param_frame=MagicMock()))
        ToolMilling.on_tool_add(stub, dia=2.4)
        stub.on_tool_default_add.assert_not_called()
        self.assertEqual(321.0, obj.tools[1]['data']['tools_mill_feedrate'])

    def test_numeric_targets_automatic_lookup(self):
        cases = [
            ('appPlugins.ToolPaint.Paint', 'ToolPaint', 4, 'paint_tools', 'tools_paint_overlap'),
            ('appPlugins.ToolNCC.Ncc', 'ToolNcc', 5, 'ncc_tools', 'tools_ncc_overlap'),
            ('appPlugins.ToolIsolation', 'ToolIsolation', 3, 'iso_tools', 'tools_iso_overlap'),
            ('appPlugins.ToolCutOut', 'CutOut', 6, 'cut_tool_dict', 'tools_cutout_margin')]
        for module, class_name, target, storage, parameter in cases:
            with self.subTest(tool=class_name):
                tool = record(target)
                tool['data'][parameter] = 23.0
                tool['data']['tools_mill_feedrate'] = 321.0
                self.path.write_text(json.dumps({'1': tool}), encoding='utf-8')
                cls = getattr(importlib.import_module(module), class_name)
                table = MagicMock()
                table.rowCount.return_value = 0
                stub = SimpleNamespace(
                    app=self.app, decimals=4, default_data=deepcopy(record()['data']),
                    blockSignals=MagicMock(), ui_disconnect=MagicMock(), ui_connect=MagicMock(),
                    build_ui=MagicMock(), update_ui=MagicMock(), on_tool_default_add=MagicMock(),
                    ui=SimpleNamespace(tools_table=table))
                setattr(stub, storage, {})
                cls.on_tool_add(stub, custom_dia=2.4)
                stub.on_tool_default_add.assert_not_called()
                result = getattr(stub, storage)
                data = result['data'] if target == 6 else result[1]['data']
                self.assertEqual(23.0, data[parameter])
                self.assertEqual(321.0, data['tools_mill_feedrate'])

    def test_paint_offset_comes_from_the_matching_record(self):
        from appPlugins.ToolPaint.Paint import ToolPaint

        matching = record(4)
        matching['data']['tools_mill_offset_type'] = 3
        matching['data']['tools_mill_offset_value'] = 0.75
        trailing = record(1)
        trailing['data']['tools_mill_offset_type'] = 0
        trailing['data']['tools_mill_offset_value'] = 0.0
        self.path.write_text(json.dumps({'1': matching, '2': trailing}), encoding='utf-8')
        table = MagicMock()
        table.rowCount.return_value = 0
        stub = SimpleNamespace(
            app=self.app, decimals=4, default_data=deepcopy(record()['data']), paint_tools={},
            blockSignals=MagicMock(), build_ui=MagicMock(), update_ui=MagicMock(),
            on_tool_default_add=MagicMock(), ui=SimpleNamespace(
                new_tooldia_entry=MagicMock(), tools_table=table),
            on_row_selection_change=MagicMock())
        stub.ui.new_tooldia_entry.get_value.return_value = 2.4

        ToolPaint.on_tool_add(stub)

        self.assertEqual(3, stub.paint_tools[1]['offset'])
        self.assertEqual(0.75, stub.paint_tools[1]['offset_value'])

    def test_paint_tolerance_boundaries_no_match_and_ambiguity(self):
        from appPlugins.ToolPaint.Paint import ToolPaint

        def run(records):
            self.path.write_text(json.dumps(records), encoding='utf-8')
            table = MagicMock()
            table.rowCount.return_value = 0
            stub = SimpleNamespace(
                app=self.app, decimals=4, default_data=deepcopy(record()['data']), paint_tools={},
                blockSignals=MagicMock(), build_ui=MagicMock(), update_ui=MagicMock(),
                on_tool_default_add=MagicMock(), ui=SimpleNamespace(
                    new_tooldia_entry=MagicMock(), tools_table=table),
                on_row_selection_change=MagicMock())
            stub.ui.new_tooldia_entry.get_value.return_value = 2.4
            ToolPaint.on_tool_add(stub, custom_dia=2.4)
            return stub

        for bounds in ((2.4, 2.6), (2.2, 2.4)):
            with self.subTest(bounds=bounds):
                tool = record(4, 2.5)
                tool['data']['tol_min'], tool['data']['tol_max'] = bounds
                result = run({'1': tool})
                result.on_tool_default_add.assert_not_called()
                self.assertIn(1, result.paint_tools)

        tool = record(4, 2.5)
        tool['data']['tol_min'], tool['data']['tol_max'] = (2.5, 2.6)
        result = run({'1': tool})
        result.on_tool_default_add.assert_called_once()
        self.assertEqual({}, result.paint_tools)

        first = record(4, 2.5)
        second = record(4, 2.6)
        for tool in (first, second):
            tool['data']['tol_min'], tool['data']['tol_max'] = (2.2, 2.6)
        result = run({'1': first, '2': second})
        result.on_tool_default_add.assert_not_called()
        self.assertEqual({}, result.paint_tools)

    def test_drilling_numeric_target_updates_parameters(self):
        from appPlugins.ToolDrilling import ToolDrilling
        tool = record(2)
        tool['data']['tools_drill_feedrate_z'] = 321.0
        stub = SimpleNamespace(
            app=self.app, excellon_obj=object(),
            excellon_tools={1: {'tooldia': 2.4, 'data': {'tools_drill_feedrate_z': 99.0}}},
            tools_db_dict={'1': tool}, build_tool_ui=MagicMock(), blockSignals=MagicMock())
        ToolDrilling.replace_tools(stub)
        self.assertEqual(321.0, stub.excellon_tools[1]['data']['tools_drill_feedrate_z'])
        stub.excellon_tools[1]['data']['tools_drill_feedrate_z'] = 99.0
        self.assertEqual(321.0, tool['data']['tools_drill_feedrate_z'])

    def test_drilling_tolerance_merges_tools_with_the_same_db_diameter(self):
        from shapely import Point
        from appPlugins.ToolDrilling import ToolDrilling
        tool = record(2, 0.8)
        tool['data']['tol_min'], tool['data']['tol_max'] = (0.75, 0.85)
        stub = SimpleNamespace(
            app=self.app, excellon_obj=object(),
            excellon_tools={
                1: {'tooldia': 0.8, 'drills': [Point(0, 0)], 'slots': [],
                    'solid_geometry': [Point(0, 0).buffer(0.4)], 'data': {}},
                2: {'tooldia': 1.0, 'drills': [Point(5, 5)], 'slots': [],
                    'solid_geometry': [Point(5, 5).buffer(0.5)], 'data': {}},
                3: {'tooldia': 0.82, 'drills': [Point(1, 1), Point(2, 2)], 'slots': [((3, 3), (4, 3))],
                    'solid_geometry': [Point(1, 1).buffer(0.41), Point(2, 2).buffer(0.41)], 'data': {}},
            },
            tools_db_dict={'1': tool}, build_tool_ui=MagicMock(), blockSignals=MagicMock())
        object_tools = stub.excellon_tools

        ToolDrilling.replace_tools(stub)

        self.assertEqual([1, 2], sorted(stub.excellon_tools))
        merged, other = stub.excellon_tools[1], stub.excellon_tools[2]
        self.assertEqual(0.8, merged['tooldia'])
        self.assertEqual([(0, 0), (1, 1), (2, 2)], [(p.x, p.y) for p in merged['drills']])
        self.assertEqual([((3, 3), (4, 3))], merged['slots'])
        self.assertEqual(3, len(merged['solid_geometry']))
        self.assertEqual(tool['data']['tools_drill_feedrate_z'], merged['data']['tools_drill_feedrate_z'])
        self.assertEqual(1.0, other['tooldia'])
        self.assertEqual(1, len(other['drills']))
        stub.build_tool_ui.assert_called_once()
        # the tools of the Excellon object must not change
        self.assertEqual([0.8, 1.0, 0.82], [t['tooldia'] for t in object_tools.values()])
        self.assertEqual([1, 1, 2], [len(t['drills']) for t in object_tools.values()])

    def test_drilling_merge_keeps_tools_without_repeated_diameter(self):
        from appPlugins.ToolDrilling import merge_same_diameter_tools
        tools = {3: {'tooldia': 0.8}, 7: {'tooldia': 1.0}}
        self.assertIs(tools, merge_same_diameter_tools(tools))

    def test_cutout_picker_does_not_mutate_database_record(self):
        from appPlugins.ToolCutOut import CutOut
        tool = record(6)
        before = deepcopy(tool)
        stub = SimpleNamespace(app=self.app, default_data=deepcopy(tool['data']),
                               cut_tool_dict={}, update_ui=MagicMock(),
                               ui=SimpleNamespace(dia=MagicMock()))
        CutOut.on_cutout_tool_add_from_db_executed(stub, tool)
        self.assertEqual(before, tool)


if __name__ == '__main__':
    unittest.main(verbosity=2)
