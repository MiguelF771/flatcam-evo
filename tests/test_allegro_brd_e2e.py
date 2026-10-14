import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6 import QtWidgets
from shapely.affinity import scale
from shapely.geometry import GeometryCollection, box
from shapely.ops import unary_union

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appParsers.ParseAllegroBrd import AllegroBrdParser
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from defaults import AppDefaults


FIXTURES = Path(__file__).parent / 'fixtures' / 'orcad_17.2'
BOARD_A = FIXTURES / 'proiectCAD.brd'
BOARD_B = FIXTURES / '2nd project' / 'TI28027.brd'
FILMS_B_DIR = FIXTURES / '2nd project' / 'Gerbers'

# Allegro 17.2 two-layer board: the films were plotted from this board (photoplot.log, Cadence Allegro 17.2-S025)
FILMS_A = {'TOP': 'Copper_TOP.art', 'BOTTOM': 'Copper_Bottom.art',
           'SOLDERMASK_TOP': 'SolderMask_TOP.art', 'SOLDERMASK_BOTTOM': 'SolderMask_Bottom.art',
           'SILKSCREEN_TOP': 'SilkScreen_TOP.art', 'ASSEMBLY_TOP': 'Assembly_Drawing_TOP.art'}
# Six-layer 18.x/24.1 board. TOP, BOTTOM and the two inner films carry the board's copper. The other six films
# (names PASTEMASK, SILKSCREEN, SOLDERMASK) do not hold what their names say: their headers list copper layers
# and their flashes sit at other places than the vias and pads of this board, so they cannot be used as references.
FILMS_B = {'TOP': 'TOP.art', 'BOTTOM': 'BOTTOM.art', 'INNER 1': 'TI28027_INNER1_COPPER_GND.art',
           'INNER 2': 'TI28027_INNER2_COPPER_POWER.art'}


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


def _app(units='MM'):
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
        log=Mock(), inform=Mock(), splash=None, worker_task=Mock(), options=options, app_units=units,
        new_project_signal=Mock(), abort_flag=False, app_obj=Mock(), plot_all=Mock(), register_folder=Mock(),
        defaults=defaults, decimals=4, use_3d_engine=True, version='test', version_date='test',
        proc_container=Mock(), plotcanvas=plotcanvas, pool=Mock(), collection=Mock(), file_opened=Mock(),
        file_saved=Mock())


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


def read_flashes(path):
    """(x, y) in mm of every D03 flash of an Allegro RS-274X film (leading zeros suppressed, modal coordinates)."""
    text = Path(path).read_text(encoding='latin-1')
    match = re.search(r'%FSLAX(\d)(\d)Y(\d)(\d)', text)
    x_decimals, y_decimals = int(match.group(2)), int(match.group(4))
    x = y = 0.0
    flashes = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or line.startswith(('%', 'G04', 'M02')):
            continue
        coords = dict(re.findall(r'([XY])(-?\d+)', line))
        if 'X' in coords:
            x = int(coords['X']) / 10 ** x_decimals
        if 'Y' in coords:
            y = int(coords['Y']) / 10 ** y_decimals
        if line.endswith('D03*'):
            flashes.append((x, y))
    return flashes


class FixtureCase(unittest.TestCase):
    @classmethod
    def read_film(cls, path):
        parser = Gerber(cls.app)
        assert parser.parse_file(str(path)) != 'fail', path
        geometry = _union(parser.solid_geometry)
        if parser.units == 'IN':
            geometry = scale(geometry, 25.4, 25.4, origin=(0, 0))
        return geometry

    def mine(self, name):
        return _union(self.parsed.layers[name]['solid_geometry']) if name in self.parsed.layers else None

    def ratio(self, name, reference, extra=None):
        mine = self.mine(name)
        if extra is not None:
            mine = unary_union([mine, extra])
        return mine.symmetric_difference(reference).area / reference.area

    def holes(self, group):
        return [(tool['tooldia'], point) for tool in self.parsed.drill_groups[group].values()
                for point in tool['drills']]

    def assert_holes_on_flashes(self, top_film, bottom_film):
        top, bottom = read_flashes(top_film), read_flashes(bottom_film)
        worst = 0.0
        for group in self.parsed.drill_groups:
            for _diameter, point in self.holes(group):
                for flashes in (top, bottom):
                    distance = min(((fx - point.x) ** 2 + (fy - point.y) ** 2) ** 0.5 for fx, fy in flashes)
                    worst = max(worst, distance)
        self.assertLessEqual(worst, 0.01)
        return worst


class TestAllegro172Board(FixtureCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app = _app()
        cls.parsed = AllegroBrdParser(app=cls.app).parse_file(BOARD_A)
        cls.films = {name: cls.read_film(FIXTURES / film) for name, film in FILMS_A.items()}
        cls.films['older TOP'] = cls.read_film(FIXTURES / 'TOP.art')
        cls.films['older BOTTOM'] = cls.read_film(FIXTURES / 'BOTTOM.art')
        cls.outline_film = cls.read_film(FIXTURES / 'SilkScreen_Bottom.art')    # holds only the board outline

    def test_layer_set_and_order(self):
        self.assertEqual(list(self.parsed.layers), ['TOP', 'BOTTOM', 'SOLDERMASK_TOP', 'SOLDERMASK_BOTTOM',
                                                    'SILKSCREEN_TOP', 'ASSEMBLY_TOP', 'OUTLINE'])
        self.assertEqual(self.parsed.default_layers, [n for n in self.parsed.layers if n != 'ASSEMBLY_TOP'])
        self.assertEqual(self.parsed.version, '17.2')

    def test_copper_matches_the_copper_films(self):
        for name in ('TOP', 'BOTTOM'):
            with self.subTest(layer=name):
                self.assertLessEqual(self.ratio(name, self.films[name]), 0.01)
                self.assertLessEqual(self.ratio(name, self.films['older ' + name]), 0.01)
                self.assertEqual([round(v, 2) for v in self.mine(name).bounds],
                                 [round(v, 2) for v in self.films[name].bounds])

    def test_solder_mask_matches_the_mask_films(self):
        for name in ('SOLDERMASK_TOP', 'SOLDERMASK_BOTTOM'):
            with self.subTest(layer=name):
                self.assertLessEqual(self.ratio(name, self.films[name]), 0.02)

    def test_outline_bounds_match_the_outline_film(self):
        reference = self.outline_film.bounds
        for mine, theirs in zip(self.mine('OUTLINE').bounds, reference):
            self.assertAlmostEqual(mine, theirs, delta=0.01)
        self.assertEqual([round(v, 3) for v in self.parsed.board_outline.bounds], [0.0, 0.0, 70.0, 55.0])

    def test_silkscreen_and_assembly_bounds_within_one_millimetre(self):
        # the films also hold BOARD GEOMETRY/DESIGN_OUTLINE, which is the OUTLINE layer here, so it is added
        outline = self.mine('OUTLINE')
        for name in ('SILKSCREEN_TOP', 'ASSEMBLY_TOP'):
            with self.subTest(layer=name):
                bounds = unary_union([self.mine(name), outline]).bounds
                for mine, theirs in zip(bounds, self.films[name].bounds):
                    self.assertAlmostEqual(mine, theirs, delta=1.0)
                # text is approximate (the films draw it with hairlines), so only a loose upper limit on the area
                self.assertLess(self.ratio(name, self.films[name], outline), 1.5)
        self.assertNotIn('SILKSCREEN_BOTTOM', self.parsed.layers)      # nothing on the bottom silkscreen

    def test_board_text_follows_the_character_pitch_and_anchor_of_the_film(self):
        # six lines of board text (the largest is 40 characters): same left end, baseline and line length, which
        # shows the character pitch (width plus character space) and the anchor are right
        window = box(25, 43, 68, 52)
        mine = self.mine('SILKSCREEN_TOP').intersection(window).bounds
        reference = self.films['SILKSCREEN_TOP'].intersection(window).bounds
        for actual, target in zip(mine, reference):
            self.assertAlmostEqual(actual, target, delta=0.15)

    def test_drills_sit_on_pad_flashes_in_both_outer_films(self):
        self.assert_holes_on_flashes(FIXTURES / 'Copper_TOP.art', FIXTURES / 'Copper_Bottom.art')
        # 26 plated pins (16 round 36 mil, 6 transistor, 4 round 42 mil); no vias on this board
        counts = {}
        for diameter, _point in self.holes('PTH'):
            counts[round(diameter, 3)] = counts.get(round(diameter, 3), 0) + 1
        self.assertEqual(counts, {0.787: 6, 0.914: 16, 1.067: 4})
        self.assertEqual([(round(d, 3)) for d, _p in self.holes('NPTH')], [3.2] * 4)
        self.assertEqual(list(self.parsed.drill_groups), ['PTH', 'NPTH'])

    def test_every_plated_hole_is_inside_both_mask_openings_and_mounting_holes_are_not(self):
        top, bottom = self.mine('SOLDERMASK_TOP'), self.mine('SOLDERMASK_BOTTOM')
        for _d, point in self.holes('PTH'):
            self.assertTrue(top.contains(point) and bottom.contains(point))
        for _d, point in self.holes('NPTH'):
            self.assertFalse(top.contains(point) or bottom.contains(point))
            # the reference mask films have no opening there either
            self.assertFalse(self.films['SOLDERMASK_TOP'].contains(point))
        # the mounting holes keep their pads (the padstack has them and the copper films show them)
        for _d, point in self.holes('NPTH'):
            self.assertTrue(self.mine('TOP').contains(point) and self.films['TOP'].contains(point))


class TestAllegro181Board(FixtureCase):
    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.app = _app()
        cls.parsed = AllegroBrdParser(app=cls.app).parse_file(BOARD_B)
        cls.films = {name: cls.read_film(FILMS_B_DIR / film) for name, film in FILMS_B.items()}
        cls.all_films = {path.name: cls.read_film(path) for path in sorted(FILMS_B_DIR.glob('*.art'))}

    def test_layers(self):
        self.assertEqual(self.parsed.version, '18.1/24.1')
        self.assertEqual(self.parsed.copper_layers, ['TOP', 'INNER 1', 'INNER1_1', 'INNER 2', 'INNER2_1', 'BOTTOM'])
        self.assertEqual(list(self.parsed.layers), self.parsed.copper_layers + [
            'SOLDERMASK_TOP', 'SOLDERMASK_BOTTOM', 'PASTEMASK_TOP', 'PASTEMASK_BOTTOM', 'SILKSCREEN_TOP',
            'SILKSCREEN_BOTTOM', 'ASSEMBLY_TOP', 'ASSEMBLY_BOTTOM', 'OUTLINE'])

    def test_copper_matches_the_four_copper_films(self):
        for name in FILMS_B:
            with self.subTest(layer=name):
                self.assertLessEqual(self.ratio(name, self.films[name]), 0.01)

    def test_other_copper_layers_match_no_film_but_their_tracks_are_in_a_film(self):
        # TOP_/BOTTOM_ PASTEMASK, SILKSCREEN and SOLDERMASK films are not made from the stored copper of this
        # board, none of them matches a layer. The film BOTTOM_PASTEMASK does hold all six tracks of INNER2_1.
        for name in ('INNER1_1', 'INNER2_1'):
            self.assertGreater(len(self.parsed.layers[name]['solid_geometry']), 100)
        lines = unary_union(self.parsed.layers['INNER2_1']['follow_geometry'])
        track_film = self.all_films['BOTTOM_PASTEMASK.art']
        self.assertGreaterEqual(lines.intersection(track_film.buffer(0.01)).length / lines.length, 0.99)

    def test_drills_sit_on_pad_flashes_in_both_outer_films(self):
        self.assert_holes_on_flashes(FILMS_B_DIR / 'TOP.art', FILMS_B_DIR / 'BOTTOM.art')
        counts = {}
        for group in self.parsed.drill_groups:
            for diameter, _point in self.holes(group):
                counts[(group, round(diameter, 3))] = counts.get((group, round(diameter, 3)), 0) + 1
        self.assertEqual(counts, {('PTH', 0.33): 250, ('PTH', 0.914): 206, ('PTH', 1.143): 2, ('PTH', 1.245): 4,
                                  ('PTH', 1.397): 2, ('NPTH', 3.861): 1})

    def test_plated_holes_equal_pins_plus_vias(self):
        plated = sum(len(tool['drills']) for tool in self.parsed.drill_groups['PTH'].values())
        self.assertEqual(plated, 464)           # 214 through pins and 250 vias
        self.assertEqual(list(self.parsed.drill_groups), ['PTH', 'NPTH'])

    def test_mask_and_paste_are_consistent_with_pads(self):
        holes = [point for group in self.parsed.drill_groups for _d, point in self.holes(group)]
        for name in ('SOLDERMASK_TOP', 'SOLDERMASK_BOTTOM'):
            mask = self.mine(name)
            self.assertTrue(all(mask.contains(point) for point in holes))
        for side in ('TOP', 'BOTTOM'):
            paste, copper = self.mine('PASTEMASK_' + side), self.mine(side)
            self.assertLess(paste.difference(copper).area, 1e-6)
        self.assertGreater(len(self.parsed.layers['PASTEMASK_BOTTOM']['solid_geometry']), 100)

    def test_outline_is_the_board_shape(self):
        self.assertEqual([round(v, 2) for v in self.parsed.board_outline.bounds], [132.08, 0.0, 403.86, 144.78])


class TestAllegroRoundTrip(unittest.TestCase):
    LAYERS = ['TOP', 'BOTTOM', 'SOLDERMASK_TOP', 'OUTLINE']
    GROUPS = ['PTH', 'NPTH']

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.parsed = AllegroBrdParser(app=_app('MM')).parse_file(BOARD_A)

    def _import(self, units):
        app = _app(units)
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
        app.collection.get_by_name.side_effect = objects.__getitem__
        self.assertIsNone(io.import_pcbdoc_objects(str(BOARD_A), self.parsed, self.LAYERS, self.GROUPS))
        return app, io, objects

    def test_layers_and_drills_survive_import_export_reimport(self):
        for units in ('MM', 'IN'):
            with self.subTest(units=units):
                app, io, objects = self._import(units)
                factor = 1 if units == 'MM' else 1 / 25.4
                self.assertEqual(sorted(objects), sorted('proiectCAD_%s' % key for key in self.LAYERS + self.GROUPS))
                with tempfile.TemporaryDirectory() as directory:
                    for layer in self.LAYERS:
                        name = 'proiectCAD_%s' % layer
                        filename = Path(directory) / ('%s.gbr' % layer)
                        io.export_gerber(name, str(filename), local_use=None, use_thread=False)
                        parser = Gerber(app)
                        self.assertNotEqual(parser.parse_file(str(filename)), 'fail', name)
                        self.assertEqual(parser.units, units)
                        expected = scale(_union(self.parsed.layers[layer]['solid_geometry']), xfact=factor,
                                         yfact=factor, origin=(0, 0))
                        difference = expected.symmetric_difference(_union(parser.solid_geometry)).area
                        # the Gerber export rounds each vertex to 4 decimals of the file unit
                        tolerance = max(0.01 * factor, expected.area * 0.0001, expected.length * 0.5e-4)
                        self.assertLessEqual(difference, tolerance, 'layer %s differs by %s' % (layer, difference))
                    for group, count in (('PTH', 26), ('NPTH', 4)):
                        filename = Path(directory) / ('%s.drl' % group)
                        io.export_excellon('proiectCAD_%s' % group, str(filename), local_use=None, use_thread=False)
                        parser = Excellon(app)
                        self.assertNotEqual(parser.parse_file(str(filename)), 'fail')
                        self.assertEqual(parser.units, units)
                        self.assertEqual(sum(len(tool.get('drills', [])) for tool in parser.tools.values()), count)


if __name__ == '__main__':
    unittest.main()
