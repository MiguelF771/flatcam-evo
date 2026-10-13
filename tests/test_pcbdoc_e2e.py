import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PyQt6 import QtWidgets
from shapely.affinity import scale
from shapely.geometry import GeometryCollection, Polygon
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from appParsers.ParsePcbDoc import PcbDocParser
from defaults import AppDefaults


class NativeGerberObject(GerberObject):
    def __init__(self, name, app):
        self.app = app
        self.obj_options = {'name': name, 'pcbdoc_import': False}
        self.units = app.app_units
        self.tools = {}
        self.solid_geometry = []
        self.follow_geometry = []
        self.source_file = ''


class NativeExcellonObject(ExcellonObject):
    def __init__(self, name, app):
        self.app = app
        self.obj_options = {'name': name, 'pcbdoc_import': False}
        self.units = app.app_units
        self.tools = {}
        self.solid_geometry = []
        self.source_file = ''
        self.excellon_circle_steps = 64
        self.default_data = {}


class TestPcbDocE2E(unittest.TestCase):
    FIXTURE = Path(__file__).parent / 'fixtures' / 'altium' / 'I2C_level_shifter.PcbDoc'
    LAYERS = [1, *range(32, 39), 56, 57, *range(69, 72)]
    GERBER_REFERENCES = ('GBL', 'GBO', 'GBS', 'GKO', 'GTL', 'GTO', 'GTS', 'TXT')

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    @staticmethod
    def _app(units):
        defaults = AppDefaults()
        options = dict(defaults.defaults)
        plotcanvas = Mock()
        plotcanvas.new_shape_collection.return_value = Mock()
        options.update({
            'gerber_exp_units': units,
            'gerber_exp_integer': 2,
            'gerber_exp_decimals': 4,
            'gerber_exp_zeros': 'L',
            'excellon_exp_units': 'METRIC' if units == 'MM' else 'INCH',
            'excellon_exp_integer': 2,
            'excellon_exp_decimals': 4,
            'excellon_exp_format': 'dec',
            'excellon_exp_slot_type': 'routing',
        })
        return SimpleNamespace(
            log=Mock(), inform=Mock(), splash=None, worker_task=Mock(),
            options=options, app_units=units, new_project_signal=Mock(),
            abort_flag=False, app_obj=Mock(), plot_all=Mock(),
            register_folder=Mock(), defaults=defaults, decimals=4,
            use_3d_engine=True, version='test', version_date='test',
            proc_container=Mock(), plotcanvas=plotcanvas, pool=Mock(),
            collection=Mock(), file_opened=Mock(), file_saved=Mock(),
        )

    @staticmethod
    def _union(value):
        if isinstance(value, list):
            geometries = [geo for geo in value if not geo.is_empty]
        elif value is None or value.is_empty:
            geometries = []
        elif isinstance(value, GeometryCollection):
            geometries = list(value.geoms)
        else:
            geometries = [value]
        return unary_union(geometries) if geometries else GeometryCollection()

    @staticmethod
    def _inches(geometry, units):
        factor = 1 / 25.4 if units == 'MM' else 1
        return scale(geometry, xfact=factor, yfact=factor, origin=(0, 0))

    @staticmethod
    def _nested_island(geometry):
        polygons = [geometry] if geometry.geom_type == 'Polygon' else list(geometry.geoms)
        for outer in polygons:
            for ring in outer.interiors:
                hole = Polygon(ring)
                for candidate in polygons:
                    if candidate is not outer and hole.contains(candidate.representative_point()):
                        void = hole.difference(candidate)
                        return void.representative_point(), candidate.representative_point()
        raise AssertionError('fixture has no nested island to verify')

    @staticmethod
    def _hole_point(geometry):
        polygons = [geometry] if geometry.geom_type == 'Polygon' else list(geometry.geoms)
        for polygon in polygons:
            if polygon.interiors:
                return Polygon(polygon.interiors[0]).representative_point()
        raise AssertionError('fixture has no hole to verify')

    def _import_fixture(self, units):
        app = self._app(units)
        io = appIO(app)
        io._register_pcbdoc_outputs = Mock()
        objects = {}

        def create(kind, name, initialize, **kwargs):
            obj = NativeGerberObject(name, app) if kind == 'gerber' else NativeExcellonObject(name, app)
            if initialize(obj, app) == 'fail':
                return 'fail'
            objects[name] = obj
            return obj

        app.app_obj.new_object.side_effect = create
        parsed = PcbDocParser().parse_file(self.FIXTURE)
        app.collection.get_by_name.side_effect = objects.__getitem__
        result = io.import_pcbdoc_objects(
            str(self.FIXTURE), parsed, self.LAYERS, sorted(parsed.drill_groups)
        )
        self.assertIsNone(result)
        return app, io, parsed, objects

    def test_real_fixture_import_export_reimport_preserves_layers_drills_and_polarity(self):
        gerber_dir = self.FIXTURE.parent / 'Gerber'
        self.assertTrue(self.FIXTURE.is_file())
        self.assertTrue(all((gerber_dir / ('I2C_level_shifter.' + ext)).is_file()
                            for ext in self.GERBER_REFERENCES))

        for units in ('MM', 'IN'):
            with self.subTest(units=units):
                app, io, parsed, objects = self._import_fixture(units)
                self.assertEqual(sorted(parsed.layers), self.LAYERS)
                self.assertEqual(sum(len(tool.get('drills', []))
                                     for group in parsed.drill_groups.values()
                                     for tool in group.values()), 8)
                self.assertTrue(any('0CDGNU' in warning and 'unverified' in warning
                                    for warning in parsed.warnings))
                self.assertEqual(len(objects), len(self.LAYERS) + 1)

                reimported = {}
                with tempfile.TemporaryDirectory() as directory:
                    for layer, (name, obj) in zip(
                            self.LAYERS,
                            [(name, obj) for name, obj in objects.items()
                             if isinstance(obj, NativeGerberObject)]):
                        self.assertEqual(obj.units, units)
                        filename = Path(directory) / ('layer_%s.gbr' % layer)
                        io.export_gerber(name, str(filename), local_use=None, use_thread=False)
                        self.assertTrue(filename.is_file())
                        parser = Gerber(app)
                        self.assertNotEqual(parser.parse_file(str(filename)), 'fail', name)
                        self.assertEqual(parser.units, units)
                        reimported[layer] = self._union(parser.solid_geometry)

                    drill_name, drill_obj = next(
                        (name, obj) for name, obj in objects.items()
                        if isinstance(obj, NativeExcellonObject)
                    )
                    self.assertEqual(drill_obj.units, units)
                    filename = Path(directory) / 'drills.drl'
                    io.export_excellon(drill_name, str(filename), local_use=None, use_thread=False)
                    self.assertTrue(filename.is_file())
                    parser = Excellon(app)
                    self.assertNotEqual(parser.parse_file(str(filename)), 'fail')
                    self.assertEqual(parser.units, units)
                    self.assertEqual(sum(len(tool.get('drills', []))
                                         for tool in parser.tools.values()), 8)
                    self.assertEqual(sum(len(tool.get('slots', []))
                                         for tool in parser.tools.values()), 0)
                    expected_dia = 1.0 if units == 'MM' else 1 / 25.4
                    self.assertAlmostEqual(parser.tools[1]['tooldia'], expected_dia, delta=0.0002)

                factor = 1 if units == 'MM' else 1 / 25.4
                for layer in self.LAYERS:
                    expected = scale(
                        self._union(parsed.layers[layer]['solid_geometry']),
                        xfact=factor, yfact=factor, origin=(0, 0)
                    )
                    actual = reimported[layer]
                    tolerance = max(0.01 * factor, expected.area * 0.0001)
                    self.assertLessEqual(
                        expected.symmetric_difference(actual).area,
                        tolerance,
                        'layer %s geometry differs by %s' % (
                            layer, expected.symmetric_difference(actual).area
                        )
                    )

                top_geometry = scale(
                    self._union(parsed.layers[1]['solid_geometry']),
                    xfact=factor, yfact=factor, origin=(0, 0)
                )
                hole_point = self._hole_point(top_geometry)
                self.assertFalse(reimported[1].covers(hole_point))

                hole_point, island_point = self._nested_island(
                    scale(self._union(parsed.layers[32]['solid_geometry']),
                          xfact=factor, yfact=factor, origin=(0, 0))
                )
                self.assertFalse(reimported[32].covers(hole_point))
                self.assertTrue(reimported[32].covers(island_point))

                if units == 'MM':
                    reference = Gerber(app)
                    reference.parse_file(str(gerber_dir / 'I2C_level_shifter.GKO'))
                    gko = self._union(reference.solid_geometry)
                    generated = self._inches(reimported[56], units)
                    tolerance = 0.002
                    for actual, expected in zip(generated.bounds, gko.bounds):
                        self.assertAlmostEqual(actual, expected, delta=tolerance)
                    self.assertAlmostEqual(generated.centroid.x, gko.centroid.x, delta=tolerance)
                    self.assertAlmostEqual(generated.centroid.y, gko.centroid.y, delta=tolerance)
                    self.assertAlmostEqual(generated.bounds[2] - generated.bounds[0],
                                           gko.bounds[2] - gko.bounds[0], delta=tolerance)
                    self.assertAlmostEqual(generated.bounds[3] - generated.bounds[1],
                                           gko.bounds[3] - gko.bounds[1], delta=tolerance)

                    reference = Gerber(app)
                    reference.parse_file(str(gerber_dir / 'I2C_level_shifter.GTS'))
                    gts = self._union(reference.solid_geometry)
                    top_mask = self._inches(reimported[37], units)
                    self.assertGreaterEqual(gts.intersection(top_mask).area / gts.area, 0.99)


if __name__ == '__main__':
    unittest.main()
