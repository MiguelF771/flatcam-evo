import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtCore, QtWidgets
from shapely.ops import unary_union
from shapely.geometry import Polygon, Point, LineString
from shapely.affinity import translate
from appHandlers.appIO import appIO


class TestPcbDocImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.app = SimpleNamespace(log=Mock(), inform=Mock(), splash=None, worker_task=Mock(),
                                   options={}, app_units='MM', new_project_signal=Mock(),
                                   abort_flag=False, app_obj=Mock(), plot_all=Mock(), file_opened=Mock())
        self.io = appIO(self.app)
        polygon = Polygon([(-25.4, 0), (25.4, 0), (25.4, 25.4), (-25.4, 25.4)],
                          [[(-5, 5), (5, 5), (5, 10), (-5, 10)]])
        self.parsed = SimpleNamespace(layers={1: dict(name='Top', solid_geometry=[polygon],
                                      follow_geometry=[LineString([(0, 0), (25.4, 0)])],
                                      tools={10: dict(type='C', size=2.54, geometry=[])}),
                                      32: dict(name='Mechanical', solid_geometry=[polygon],
                                               follow_geometry=[], tools={})},
                                      drill_groups={'PTH': {1: dict(tooldia=2.54,
                                          drills=[Point(25.4, 0)],
                                          slots=[(Point(0, 0), Point(25.4, 0))])}}, warnings=[])
        self.objects = []

        def create(kind, name, initialize, **kwargs):
            obj = SimpleNamespace(obj_options={'name': name}, create_geometry=Mock())
            if initialize(obj, self.app) == 'fail':
                return 'fail'
            self.objects.append((kind, obj))
            return obj

        self.app.app_obj.new_object.side_effect = create
        self.io.export_gerber = Mock(return_value='G04*\nM02*\n')
        self.io.export_excellon = Mock(return_value='M48\nM30\n')

    def tearDown(self):
        self.qapp.processEvents()

    def test_current_units_tools_geometry_drills_slots_and_selection(self):
        self.app.app_units = 'IN'
        self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], ['PTH'])
        self.assertEqual([kind for kind, obj in self.objects], ['gerber', 'excellon'])
        gerber, drill = [obj for kind, obj in self.objects]
        self.assertEqual(gerber.units, 'IN')
        self.assertAlmostEqual(gerber.tools[10]['size'], 0.1)
        for actual, expected in zip(gerber.solid_geometry[0].bounds, (-1, 0, 1, 1)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(len(gerber.solid_geometry[0].interiors), 1)
        for actual, expected in zip(gerber.follow_geometry[0].bounds, (0, 0, 1, 0)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(drill.tools[1]['tooldia'], 0.1)
        self.assertAlmostEqual(drill.tools[1]['drills'][0].x, 1)
        self.assertAlmostEqual(drill.tools[1]['slots'][0][1].x, 1)
        drill.create_geometry.assert_called_once()
        self.assertEqual(self.parsed.layers[1]['tools'][10]['size'], 2.54)
        self.assertEqual(self.app.worker_task.emit.call_args.args[0]['priority'], 'low')

    def test_empty_selection_and_abort_create_nothing(self):
        self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [], [])
        self.app.abort_flag = True
        self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], ['PTH'])
        self.assertEqual(self.objects, [])
        self.app.worker_task.emit.assert_not_called()

    def test_export_failure_does_not_publish_corrupt_object(self):
        self.io.export_gerber.return_value = 'fail'
        self.assertEqual(self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], []), 'fail')
        self.assertEqual(self.objects, [])
        self.qapp.processEvents()
        self.app.file_opened.emit.assert_not_called()

    def test_empty_layer_is_not_created(self):
        self.parsed.layers[1]['solid_geometry'] = []
        self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], [])
        self.assertEqual(self.objects, [])

    def test_dialog_default_selection_cancel_and_deselection(self):
        self.app.ui = QtWidgets.QWidget()
        self.parsed.warnings = ['Stroke glyph approximation used.']

        def accepted(dialog):
            boxes = dialog.findChildren(QtWidgets.QCheckBox)
            self.assertEqual(len(boxes), 3)
            self.assertTrue(all(box.isChecked() for box in boxes))
            self.assertIn(self.parsed.warnings[0], [label.text() for label in dialog.findChildren(QtWidgets.QLabel)])
            boxes[0].setChecked(False)
            return QtWidgets.QDialog.DialogCode.Accepted

        with patch.object(QtWidgets.QDialog, 'exec', accepted):
            self.io.on_pcbdoc_parsed('board.PcbDoc', self.parsed)
        self.assertEqual(self.app.worker_task.emit.call_args.args[0]['params'][2:], [[32], ['PTH']])
        self.app.worker_task.reset_mock()
        with patch.object(QtWidgets.QDialog, 'exec', return_value=QtWidgets.QDialog.DialogCode.Rejected):
            self.io.on_pcbdoc_parsed('board.PcbDoc', self.parsed)
        self.app.worker_task.emit.assert_not_called()

    def test_region_export_reimport_preserves_negative_holes_and_islands(self):
        from appObjects.GerberObject import GerberObject
        from appParsers.ParseGerber import Gerber
        from defaults import AppDefaults
        defaults = AppDefaults()
        self.app.defaults = defaults
        self.app.options = dict(defaults.defaults)
        self.app.options['gerber_exp_units'] = 'MM'
        self.app.decimals = 4
        self.app.use_3d_engine = True
        self.app.plotcanvas = Mock()
        self.app.version = 'test'
        self.app.version_date = 'test'
        self.app.proc_container = Mock()
        self.app.proc_container.view = Mock()
        island = Point(0, 7).buffer(1)
        self.parsed.layers[1]['solid_geometry'].append(island)
        self.parsed.layers[1]['solid_geometry'].append(Polygon(
            [(-10, -5), (0, -5), (0, 5), (-10, 5)],
            [[(-8, -2), (-6, -2), (-6, 2), (-8, 2)]]))

        def exported(name, filename, local_use, use_thread):
            local_use.app = self.app
            local_use.export_gerber = lambda *args, **kwargs: GerberObject.export_gerber(local_use, *args, **kwargs)
            return appIO.export_gerber(self.io, name, filename, local_use, use_thread)

        self.io.export_gerber.side_effect = exported
        self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], [])
        self.assertEqual(len(self.objects), 1)
        obj = self.objects[0][1]
        parser = Gerber(self.app)
        parser.parse_lines(obj.source_file.splitlines())
        expected = unary_union(self.parsed.layers[1]['solid_geometry'])
        actual = unary_union(parser.solid_geometry)
        self.assertLess(expected.symmetric_difference(actual).area, 0.01)
        # A later user export must preserve the same geometry, not only source_file.
        later = appIO.export_gerber(self.io, 'Top', None, local_use=obj, use_thread=False)
        parser = Gerber(self.app)
        parser.parse_lines(later.splitlines())
        self.assertLess(expected.symmetric_difference(unary_union(parser.solid_geometry)).area, 0.01)
        obj.solid_geometry = [translate(geo, xoff=-7, yoff=2) for geo in obj.solid_geometry]
        obj.source_file = 'obsolete source cache'
        original_tools = obj.tools
        edited = appIO.export_gerber(self.io, 'Top', None, local_use=obj, use_thread=False)
        parser = Gerber(self.app)
        self.assertNotEqual(parser.parse_lines(edited.splitlines()), 'fail')
        self.assertLess(unary_union(obj.solid_geometry).symmetric_difference(
            unary_union(parser.solid_geometry)).area, 0.01)
        self.assertIs(obj.tools, original_tools)
        self.assertNotIn('obsolete source cache', edited)
        self.assertEqual(obj.source_file, edited)
        self.app.options['gerber_exp_zeros'] = 'T'
        trailing = appIO.export_gerber(self.io, 'Top', None, local_use=obj, use_thread=False)
        parser = Gerber(self.app)
        self.assertNotEqual(parser.parse_lines(trailing.splitlines()), 'fail')
        self.assertLess(unary_union(obj.solid_geometry).symmetric_difference(
            unary_union(parser.solid_geometry)).area, 0.01)

    def test_creation_failure_preserves_existing_objects(self):
        original = object()
        self.objects.append(('original', original))
        self.app.app_obj.new_object.side_effect = None
        self.app.app_obj.new_object.return_value = 'fail'
        self.assertEqual(self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], []), 'fail')
        self.assertEqual(self.objects, [('original', original)])

    def test_thin_rings_export_before_larger_nested_islands(self):
        from shapely.geometry import box
        from appObjects.GerberObject import GerberObject
        from appParsers.ParseGerber import Gerber
        from defaults import AppDefaults

        self.app.defaults = AppDefaults()
        self.app.options = dict(self.app.defaults.defaults)
        self.app.options['gerber_exp_units'] = 'MM'
        self.app.decimals = 4
        self.app.use_3d_engine = True
        self.app.plotcanvas = Mock()
        self.app.version = self.app.version_date = 'test'
        self.app.proc_container = Mock()

        def ring(low, high):
            return Polygon(box(low, low, high, high).exterior.coords,
                           [box(low + 0.1, low + 0.1, high - 0.1, high - 0.1).exterior.coords])

        cases = ([ring(0, 10), box(2, 2, 8, 8)],
                 [ring(0, 10), ring(2, 8), box(3, 3, 7, 7)],
                 [ring(0, 10), ring(2, 8), ring(3, 7), box(4, 4, 6, 6)])
        self.assertAlmostEqual(unary_union(cases[0]).area, 39.96)
        for depth, geometries in enumerate(cases, 1):
            self.assertLess(geometries[0].area, geometries[-1].area)
            geometries = [translate(geo, xoff=-5, yoff=-5) for geo in geometries]
            expected = unary_union(geometries)
            obj = SimpleNamespace(app=self.app, units='MM', obj_options={'pcbdoc_import': True},
                                  tools={0: {'type': 'REG', 'size': 0}}, solid_geometry=geometries)
            obj.export_gerber = lambda *args, **kwargs: GerberObject.export_gerber(obj, *args, **kwargs)
            tools = obj.tools
            for zeros in ('L', 'T'):
                with self.subTest(depth=depth, zeros=zeros):
                    self.app.options['gerber_exp_zeros'] = zeros
                    code = appIO.export_gerber(self.io, 'thin_ring', None, local_use=obj, use_thread=False)
                    parser = Gerber(self.app)
                    self.assertNotEqual(parser.parse_lines(code.splitlines()), 'fail')
                    actual = unary_union(parser.solid_geometry)
                    self.assertAlmostEqual(actual.area, expected.area, places=4)
                    self.assertLess(actual.symmetric_difference(expected).area, 0.001)
                    self.assertIs(obj.tools, tools)

    def test_parse_result_is_queued_to_gui_thread_and_parse_errors_are_clean(self):
        self.app.ui = QtWidgets.QWidget()
        parser = Mock()
        parser.return_value.parse_file.return_value = self.parsed
        module = SimpleNamespace(PcbDocParser=parser, PcbDocParseError=ValueError)
        threads = []

        def rejected(dialog):
            threads.append(QtCore.QThread.currentThread())
            return QtWidgets.QDialog.DialogCode.Rejected

        with patch.dict('sys.modules', {'appParsers.ParsePcbDoc': module}), \
                patch.object(QtWidgets.QDialog, 'exec', rejected):
            worker = threading.Thread(target=self.io.parse_pcbdoc, args=('board.PcbDoc',))
            worker.start()
            worker.join()
            self.assertEqual(threads, [])
            self.qapp.processEvents()
            self.assertEqual(threads, [self.qapp.thread()])
            parser.return_value.parse_file.side_effect = ValueError('invalid PCB')
            self.assertEqual(self.io.parse_pcbdoc('bad.PcbDoc'), 'fail')
        self.assertEqual(self.objects, [])
        self.app.worker_task.emit.assert_not_called()

    def test_recent_registration_uses_original_source_on_gui_thread_without_writes(self):
        threads, results = [], []
        self.app.file_opened.emit.side_effect = lambda *args: threads.append(QtCore.QThread.currentThread())

        def register(source):
            results.append(self.io.import_pcbdoc_objects(source, self.parsed, [1, 32], ['PTH']))

        with tempfile.TemporaryDirectory() as directory:
            self.app.data_path = directory
            source = os.path.join(directory, 'project', 'board.PcbDoc')
            with patch('builtins.open', side_effect=AssertionError('Import must not write files')), \
                    patch('appHandlers.appIO.os.makedirs', side_effect=AssertionError('Import must not create folders')):
                worker = threading.Thread(target=register, args=(source,))
                worker.start()
                worker.join()
            self.assertEqual(results, [None])
            self.assertEqual(threads, [])
            self.qapp.processEvents()
            self.assertEqual(threads, [self.qapp.thread()])
            self.app.file_opened.emit.assert_called_once_with('pcbdoc', source)
            self.assertEqual(os.listdir(directory), [])
            self.assertFalse(os.path.exists(os.path.dirname(source)))

    def test_recent_registration_after_partial_success_is_once(self):
        self.io.export_gerber.side_effect = ['G04*\nM02*\n', 'fail']
        self.assertEqual(self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1, 32], []), 'fail')
        self.assertEqual(len(self.objects), 1)
        self.qapp.processEvents()
        self.app.file_opened.emit.assert_called_once_with('pcbdoc', 'board.PcbDoc')
        self.app.worker_task.emit.assert_called_once()
        self.assertEqual(self.app.worker_task.emit.call_args.args[0]['priority'], 'low')

    def test_abort_after_parse_before_gui_delivery_suppresses_dialog(self):
        self.app.ui = QtWidgets.QWidget()
        self.io.pcbdoc_parsed.emit('board.PcbDoc', self.parsed)
        self.app.abort_flag = True
        with patch.object(QtWidgets.QDialog, 'exec') as dialog:
            self.qapp.processEvents()
        dialog.assert_not_called()
        self.app.worker_task.emit.assert_not_called()

    def test_old_gerber_export_retains_legacy_tools_units_and_body(self):
        from defaults import AppDefaults
        self.app.defaults = AppDefaults()
        self.app.options = dict(self.app.defaults.defaults)
        self.app.options['gerber_exp_units'] = 'MM'
        self.app.version = self.app.version_date = 'test'
        tools = {10: {'type': 'C', 'size': 2.54, 'geometry': []}}
        obj = SimpleNamespace(units='IN', obj_options={}, tools=tools, source_file='original',
                              solid_geometry=[object()], export_gerber=Mock(return_value='D10*\n'))
        code = appIO.export_gerber(self.io, 'legacy', None, local_use=obj, use_thread=False)
        self.assertIn('D10*\nM02*', code)
        self.assertIs(obj.tools, tools)
        self.assertEqual(obj.export_gerber.call_args.kwargs['factor'], 1)
        self.assertEqual(obj.source_file, 'original')
        # The PCB path must not impose a new empty-body policy on ordinary Gerbers.
        obj.export_gerber.return_value = ''
        self.assertIn('M02*', appIO.export_gerber(self.io, 'legacy', None, local_use=obj, use_thread=False))

    def test_actual_qt_dialog_selection_and_worker_object_creation(self):
        self.app.ui = QtWidgets.QWidget()
        parser = Mock()
        parser.return_value.parse_file.return_value = self.parsed
        module = SimpleNamespace(PcbDocParser=parser, PcbDocParseError=ValueError)
        gui_threads, creation_threads, parser_threads = [], [], []
        errors = []
        base_create = self.app.app_obj.new_object.side_effect

        def create(*args, **kwargs):
            creation_threads.append(QtCore.QThread.currentThread())
            return base_create(*args, **kwargs)

        def parsed(filename):
            parser_threads.append(QtCore.QThread.currentThread())
            return self.parsed

        parser.return_value.parse_file.side_effect = parsed
        self.app.app_obj.new_object.side_effect = create

        class Task(QtCore.QThread):
            def __init__(task, function, params):
                super().__init__()
                task.function, task.params = function, params

            def run(task):
                try:
                    task.function(*task.params)
                except Exception as error:
                    errors.append(error)

        def choose():
            dialog = self.qapp.activeModalWidget()
            if not isinstance(dialog, QtWidgets.QDialog):
                QtCore.QTimer.singleShot(1, choose)
                return
            gui_threads.append(QtCore.QThread.currentThread())
            for box in dialog.findChildren(QtWidgets.QCheckBox):
                if box.text().startswith('Top'):
                    box.setChecked(False)
            self.app.app_units = 'IN'
            dialog.findChild(QtWidgets.QDialogButtonBox).button(
                QtWidgets.QDialogButtonBox.StandardButton.Ok).click()

        with patch.dict('sys.modules', {'appParsers.ParsePcbDoc': module}):
            task = Task(self.io.parse_pcbdoc, ['board.PcbDoc'])
            task.start()
            self.assertTrue(task.wait(5000))
            QtCore.QTimer.singleShot(0, choose)
            self.qapp.processEvents()
        self.assertEqual(errors, [])
        self.assertEqual(gui_threads, [self.qapp.thread()])
        self.assertNotEqual(parser_threads[0], self.qapp.thread())
        job = self.app.worker_task.emit.call_args.args[0]
        self.assertEqual(job['params'][2:], [[32], ['PTH']])
        render = Task(job['fcn'], job['params'])
        render.start()
        self.assertTrue(render.wait(5000))
        self.assertEqual(errors, [])
        self.assertTrue(all(thread != self.qapp.thread() for thread in creation_threads))
        self.assertEqual([obj.units for kind, obj in self.objects], ['IN', 'IN'])

    def test_abort_during_export_does_not_publish_or_register(self):
        def export(*args, **kwargs):
            self.app.abort_flag = True
            return 'G04*\nM02*\n'

        self.io.export_gerber.side_effect = export
        self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [1], ['PTH'])
        self.assertEqual(self.objects, [])
        self.qapp.processEvents()
        self.app.file_opened.emit.assert_not_called()
        self.app.worker_task.emit.assert_not_called()

    def test_real_excellon_export_drills_and_slots_in_current_units(self):
        import appHandlers.appIO as io_module
        from appObjects.ExcellonObject import ExcellonObject
        from appParsers.ParseExcellon import Excellon
        from defaults import AppDefaults
        self.app.defaults = AppDefaults()
        self.app.options = dict(self.app.defaults.defaults)
        self.app.options['excellon_exp_units'] = 'METRIC'
        self.app.options['excellon_exp_format'] = 'dec'
        self.app.decimals = 4
        self.app.use_3d_engine = True
        self.app.plotcanvas = Mock()
        self.app.version = 'test'
        self.app.version_date = 'test'
        self.app.proc_container = Mock()
        self.app.app_units = 'IN'

        class DrillObject(SimpleNamespace):
            def create_geometry(obj):
                return Excellon.create_geometry(obj)

            def export_excellon(obj, *args, **kwargs):
                return ExcellonObject.export_excellon(obj, *args, **kwargs)

        def create(kind, name, initialize, **kwargs):
            obj = DrillObject(app=self.app, obj_options={'name': name},
                              excellon_circle_steps=64, default_data={})
            if initialize(obj, self.app) == 'fail':
                return 'fail'
            self.objects.append((kind, obj))
            return obj

        self.app.app_obj.new_object.side_effect = create
        self.io.export_excellon.side_effect = lambda *args, **kwargs: appIO.export_excellon(self.io, *args, **kwargs)
        with patch.object(io_module, 'ExcellonObject', DrillObject):
            self.io.import_pcbdoc_objects('board.PcbDoc', self.parsed, [], ['PTH'])
        self.assertEqual(len(self.objects), 1)
        obj = self.objects[0][1]
        self.assertEqual(len(obj.solid_geometry), 2)
        parser = Excellon(self.app)
        self.assertNotEqual(parser.parse_lines(obj.source_file.splitlines()), 'fail')
        tool = parser.tools[1]
        self.assertAlmostEqual(tool['tooldia'], 2.54)
        self.assertAlmostEqual(tool['drills'][0].x, 25.4)
        self.assertAlmostEqual(tool['slots'][0][1].x, 25.4)
        obj.tools[1]['drills'] = [Point(-0.1, 0.2)]
        obj.source_file = 'obsolete drill cache'
        with patch.object(io_module, 'ExcellonObject', DrillObject):
            updated = appIO.export_excellon(self.io, 'PTH', None, local_use=obj, use_thread=False)
        parser = Excellon(self.app)
        self.assertNotEqual(parser.parse_lines(updated.splitlines()), 'fail')
        self.assertAlmostEqual(parser.tools[1]['drills'][0].x, -2.54)
        self.assertEqual(obj.source_file, updated)


if __name__ == '__main__':
    unittest.main()
