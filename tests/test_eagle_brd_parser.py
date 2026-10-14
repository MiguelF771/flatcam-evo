import math
import os
import random
import tempfile
import unittest
import warnings

from shapely.ops import unary_union

from appParsers.ParseEagleBrd import EagleBrdParser, EagleBrdParseError


# Small inline Eagle XML boards. Units are mm, Y up. Each helper builds a complete document so a test only
# writes the part it checks: the <plain> items, the <libraries>, the <elements>, the <signals> or the rules.

LAYERS_XML = ('<layers><layer number="1" name="Top"/><layer number="16" name="Bottom"/>'
              '<layer number="20" name="Dimension"/><layer number="21" name="tPlace"/>'
              '<layer number="22" name="bPlace"/><layer number="25" name="tNames"/>'
              '<layer number="26" name="bNames"/><layer number="27" name="tValues"/>'
              '<layer number="29" name="tStop"/><layer number="30" name="bStop"/>'
              '<layer number="31" name="tCream"/><layer number="32" name="bCream"/>'
              '<layer number="45" name="Holes"/><layer number="51" name="tDocu"/></layers>')


def doc(plain='', libs='', rules='', elements='', signals='', version='9.6.2', layers=LAYERS_XML):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<eagle version="%s"><drawing>%s<board>'
            '<designrules>%s</designrules><plain>%s</plain><libraries>%s</libraries>'
            '<elements>%s</elements><signals>%s</signals></board></drawing></eagle>'
            % (version, layers, rules, plain, libs, elements, signals))


def package_lib(items, name='l', package='P'):
    return '<library name="%s"><packages><package name="%s">%s</package></packages></library>' % (
        name, package, items)


def element(x, y, rot=None, package='P', name='R1', library='l', value='', extra=''):
    rot_attr = ' rot="%s"' % rot if rot else ''
    return '<element name="%s" library="%s" package="%s" value="%s" x="%s" y="%s"%s>%s</element>' % (
        name, library, package, value, x, y, rot_attr, extra)


def parse(text):
    return EagleBrdParser().parse_text(text)


def copper(parsed, name):
    return unary_union(parsed.layers[name]['solid_geometry'])


def rounded(values, digits=4):
    return [round(value, digits) for value in values]


class TestEagleParseErrors(unittest.TestCase):
    def assertParseError(self, text, pattern):
        with self.assertRaisesRegex(EagleBrdParseError, pattern):
            parse(text)

    def test_malformed_xml_is_a_parse_error(self):
        self.assertParseError(doc(plain='<wire x1="0" y1="0"'), 'Malformed XML')

    def test_entity_declaration_is_rejected_not_expanded(self):
        bomb = ('<?xml version="1.0"?><!DOCTYPE eagle [<!ENTITY a "aaaaaaaaaa">'
                '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><eagle version="9.0">&b;</eagle>')
        parser = EagleBrdParser()
        with self.assertRaisesRegex(EagleBrdParseError, 'entity declarations are not supported'):
            parser.parse_text(bomb)
        self.assertEqual(parser.layers, {})

    def test_non_eagle_root_is_rejected(self):
        self.assertParseError('<?xml version="1.0"?><foo/>', 'Not an Eagle file')

    def test_schematic_without_board_is_rejected(self):
        self.assertParseError('<?xml version="1.0"?><eagle version="9.0"><drawing><schematic/></drawing></eagle>',
                              'no <board>')

    def test_file_older_than_eagle_6_is_rejected(self):
        self.assertParseError(doc(version='5.0'), 'older than Eagle 6')

    def test_missing_version_is_rejected(self):
        self.assertParseError('<eagle><drawing/></eagle>', 'without a version')

    def test_plain_text_is_rejected_as_not_xml(self):
        self.assertParseError('hello', 'binary format')

    def test_binary_file_bytes_via_parse_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'old.brd')
            with open(path, 'wb') as handle:
                handle.write(b'\x10\x80\x00binary')
            with self.assertRaisesRegex(EagleBrdParseError, 'binary format'):
                EagleBrdParser().parse_file(path)

    def test_unreadable_file_is_a_parse_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EagleBrdParseError, 'Cannot read'):
                EagleBrdParser().parse_file(os.path.join(directory, 'missing.brd'))

    def test_unknown_package_reference_is_rejected(self):
        self.assertParseError(doc(elements=element(0, 0, package='nope')), 'unknown package')

    def test_signal_polygon_is_rejected(self):
        signal = ('<signal name="X"><polygon width="0.2" layer="1"><vertex x="0" y="0"/>'
                  '<vertex x="1" y="0"/><vertex x="1" y="1"/></polygon></signal>')
        self.assertParseError(doc(signals=signal), 'Signal polygon')

    def test_bad_number_is_rejected(self):
        self.assertParseError(doc(plain='<wire x1="a" y1="0" x2="10" y2="0" width="0.5" layer="1"/>'),
                              'Invalid x1')

    def test_missing_required_attribute_is_rejected(self):
        self.assertParseError(doc(plain='<wire y1="0" x2="10" y2="0" width="0.5" layer="1"/>'),
                              'without required attribute')

    def test_non_finite_number_is_rejected(self):
        self.assertParseError(doc(plain='<wire x1="nan" y1="0" x2="10" y2="0" width="0.5" layer="1"/>'),
                              'Non-finite')

    def test_failed_parse_clears_previous_state(self):
        parser = EagleBrdParser()
        parser.parse_text(doc(plain='<wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1"/>'))
        self.assertIn('Top', parser.layers)
        with self.assertRaises(EagleBrdParseError):
            parser.parse_text('<eagle version="9.0"><drawing><schematic/></drawing></eagle>')
        self.assertEqual(parser.layers, {})
        self.assertEqual(parser.drill_groups, {})
        self.assertEqual(parser.default_layers, [])

    def test_parse_methods_return_the_parser(self):
        parser = EagleBrdParser()
        self.assertIs(parser.parse_text(doc()), parser)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'empty.brd')
            with open(path, 'w', encoding='utf-8') as handle:
                handle.write(doc())
            self.assertIs(parser.parse_file(path), parser)

    def test_circle_steps_below_16_is_a_value_error(self):
        for steps in (0, 8, 15):
            with self.subTest(steps=steps):
                with self.assertRaises(ValueError):
                    EagleBrdParser(circle_steps=steps)
        with self.assertRaises(ValueError):
            EagleBrdParser(circle_steps='64')
        self.assertEqual(EagleBrdParser(circle_steps=16).circle_steps, 16)


class TestEagleWiresAndVias(unittest.TestCase):
    def test_wire_with_width_and_round_caps_on_top(self):
        parsed = parse(doc(plain='<wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1"/>'))
        self.assertEqual(list(parsed.layers), ['Top'])
        self.assertEqual(parsed.copper_layers, ['Top', 'Bottom'])
        self.assertAlmostEqual(copper(parsed, 'Top').area, 10 * 0.5 + math.pi * 0.25 ** 2, places=3)
        self.assertEqual(rounded(copper(parsed, 'Top').bounds), [-0.25, -0.25, 10.25, 0.25])
        self.assertEqual(parsed.warnings, [])

    def test_curved_wire_is_an_arc(self):
        parsed = parse(doc(plain='<wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1" curve="90"/>'))
        chord = 10.0
        radius = chord / (2 * math.sin(math.radians(45)))
        expected_length = radius * math.pi / 2
        follow = parsed.layers['Top']['follow_geometry']
        self.assertEqual(len(follow), 1)
        self.assertAlmostEqual(follow[0].length, expected_length, delta=0.01)
        minx, miny, maxx, maxy = copper(parsed, 'Top').bounds
        self.assertLess(miny, -2.2)          # the arc bulges to one side of the chord
        self.assertAlmostEqual(maxy, 0.25, delta=0.01)

    def test_via_on_top_and_bottom_is_a_pth_hole(self):
        parsed = parse(doc(signals='<signal name="N"><via x="2" y="3" extent="1-16" drill="0.3" '
                                   'diameter="0.6" shape="round"/></signal>'))
        self.assertEqual(list(parsed.drill_groups), ['PTH'])
        drills = [(point.x, point.y) for tool in parsed.drill_groups['PTH'].values() for point in tool['drills']]
        self.assertEqual(drills, [(2.0, 3.0)])
        # diameter 0.6 is smaller than drill 0.3 plus the minimum restring (0.2032 mm per side), so the
        # restring rule sets the copper: 0.3 + 2 * 0.25 * 0.3 clamped to 0.2032..0.508 -> 0.7064 mm
        for layer in ('Top', 'Bottom'):
            with self.subTest(layer=layer):
                self.assertEqual(rounded(copper(parsed, layer).bounds), [1.6468, 2.6468, 2.3532, 3.3532])
        self.assertEqual(rounded(copper(parsed, 'tStop').bounds, 3), [1.545, 2.545, 2.455, 3.455])

    def test_blind_via_on_inner_layers_gets_its_own_drill_group(self):
        rules = '<param name="layerSetup" value="(1*2*16)"/>'
        parsed = parse(doc(rules=rules, signals='<signal name="N"><via x="2" y="3" extent="1-2" drill="0.3" '
                                                'diameter="0.6" shape="round"/></signal>'))
        self.assertEqual(parsed.copper_layers, ['Top', 'Route2', 'Bottom'])
        self.assertEqual(list(parsed.drill_groups), ['PTH_1-2'])
        self.assertIn('Top', parsed.layers)
        self.assertIn('Route2', parsed.layers)
        self.assertNotIn('Bottom', parsed.layers)

    def test_full_span_via_on_a_multilayer_board_is_pth(self):
        rules = '<param name="layerSetup" value="(1*2*16)"/>'
        parsed = parse(doc(rules=rules, signals='<signal name="N"><via x="2" y="3" extent="1-16" drill="0.3" '
                                                'diameter="0.6" shape="round"/></signal>'))
        self.assertEqual(list(parsed.drill_groups), ['PTH'])
        self.assertIn('Route2', parsed.layers)

    def test_layer_19_wires_are_skipped(self):
        parsed = parse(doc(plain='<wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="19"/>'))
        self.assertEqual(parsed.layers, {})
        self.assertEqual(parsed.warnings, [])


class TestEagleThroughHolePads(unittest.TestCase):
    PAD_LIBS = '<library name="l"><packages><package name="P">%s</package></packages></library>'

    def _pad(self, shape, diameter='0.5', drill='0.8'):
        pad = '<pad name="1" x="0" y="0" drill="%s" diameter="%s" shape="%s"/>' % (drill, diameter, shape)
        return parse(doc(libs=self.PAD_LIBS % pad, elements=element(10, 10)))

    def test_round_pad_uses_restring_when_diameter_is_smaller(self):
        # drill 0.8 + 2 * clamp(0.25 * 0.8, 0.254, 0.508) = 1.308 mm copper
        parsed = self._pad('round')
        self.assertAlmostEqual(copper(parsed, 'Top').area, math.pi * 0.654 ** 2, delta=0.001)
        self.assertEqual(rounded(copper(parsed, 'Top').bounds, 3), [9.346, 9.346, 10.654, 10.654])
        self.assertEqual(rounded(copper(parsed, 'Bottom').bounds, 3), [9.346, 9.346, 10.654, 10.654])
        self.assertNotIn('tCream', parsed.layers)      # THT pads get no paste

    def test_square_pad_uses_restring(self):
        parsed = self._pad('square')
        self.assertAlmostEqual(copper(parsed, 'Top').area, 1.308 ** 2, delta=0.001)

    def test_octagon_pad_uses_restring(self):
        parsed = self._pad('octagon')
        self.assertEqual(rounded(copper(parsed, 'Top').bounds, 3), [9.346, 9.346, 10.654, 10.654])
        self.assertLess(copper(parsed, 'Top').area, 1.308 ** 2)
        self.assertGreater(copper(parsed, 'Top').area, math.pi * 0.654 ** 2)

    def test_long_pad_is_a_stadium_twice_the_diameter(self):
        parsed = self._pad('long')
        self.assertEqual(rounded(copper(parsed, 'Top').bounds, 3), [8.692, 9.346, 11.308, 10.654])

    def test_larger_diameter_in_file_wins_over_restring(self):
        parsed = self._pad('round', diameter='2.0')
        self.assertEqual(rounded(copper(parsed, 'Top').bounds, 3), [9.0, 9.0, 11.0, 11.0])

    def test_pad_drill_and_stop_opening(self):
        parsed = self._pad('round')
        self.assertEqual(list(parsed.drill_groups), ['PTH'])
        drills = [(point.x, point.y) for tool in parsed.drill_groups['PTH'].values() for point in tool['drills']]
        self.assertEqual(drills, [(10.0, 10.0)])
        # stop frame: the minimum 0.1016 mm (the rule mvStopFrame x min side is 0.1016)
        self.assertEqual(rounded(copper(parsed, 'tStop').bounds, 3), [9.244, 9.244, 10.756, 10.756])
        self.assertEqual(rounded(copper(parsed, 'bStop').bounds, 3), [9.244, 9.244, 10.756, 10.756])


class TestEagleSmdPads(unittest.TestCase):
    LIBS = '<library name="l"><packages><package name="S"><smd name="1" x="0" y="0" dx="1" dy="2" layer="1"%s/>' \
           '</package></packages></library>'

    def test_smd_copper_stop_and_cream(self):
        parsed = parse(doc(libs=self.LIBS % '', elements=element(10, 10, package='S')))
        self.assertEqual(rounded(copper(parsed, 'Top').bounds), [9.5, 9.0, 10.5, 11.0])
        self.assertEqual(rounded(copper(parsed, 'tStop').bounds, 4), [9.3984, 8.8984, 10.6016, 11.1016])
        # cream: the default cream frame is 0, so the paste equals the pad
        self.assertAlmostEqual(copper(parsed, 'tCream').area, 2.0, places=6)
        self.assertEqual(parsed.copper_layers, ['Top', 'Bottom'])
        self.assertNotIn('Bottom', parsed.layers)

    def test_smd_roundness_rounds_the_corners(self):
        parsed = parse(doc(libs=self.LIBS % ' roundness="50"', elements=element(10, 10, package='S')))
        # 1 x 2 mm box with radius 0.25 mm: 2 - (4 - pi) * 0.25 ** 2
        self.assertAlmostEqual(copper(parsed, 'Top').area, 2 - (4 - math.pi) * 0.25 ** 2, delta=0.001)

    def test_smd_cream_and_stop_can_be_turned_off(self):
        parsed = parse(doc(libs=self.LIBS % ' cream="no" stop="no"', elements=element(0, 0, package='S')))
        self.assertEqual(list(parsed.layers), ['Top'])

    def test_smd_without_cream_keeps_stop(self):
        parsed = parse(doc(libs=self.LIBS % ' cream="no"', elements=element(0, 0, package='S')))
        self.assertIn('tStop', parsed.layers)
        self.assertNotIn('tCream', parsed.layers)

    def test_stop_frame_follows_design_rules(self):
        rules = ('<param name="mvStopFrame" value="0"/><param name="mlMinStopFrame" value="0.2"/>'
                 '<param name="mlMaxStopFrame" value="0.2"/>')
        parsed = parse(doc(rules=rules, libs=self.LIBS % '', elements=element(0, 0, package='S')))
        self.assertEqual(rounded(copper(parsed, 'tStop').bounds, 3), [-0.7, -1.2, 0.7, 1.2])


class TestEagleElementPlacement(unittest.TestCase):
    LIBS = ('<library name="l"><packages><package name="S">'
            '<smd name="1" x="2" y="0" dx="1" dy="2" layer="1"/>'
            '<wire x1="0" y1="0" x2="1" y2="0" width="0.1" layer="21"/>'
            '</package></packages></library>')

    def test_rotation_r90_turns_the_pad_and_the_silkscreen(self):
        parsed = parse(doc(libs=self.LIBS, elements=element(10, 10, rot='R90', package='S')))
        self.assertEqual(rounded(copper(parsed, 'Top').bounds), [9.0, 11.5, 11.0, 12.5])
        self.assertEqual(rounded(copper(parsed, 'tPlace').bounds), [9.95, 9.95, 10.05, 11.05])

    def test_mirror_moves_top_objects_to_bottom(self):
        parsed = parse(doc(libs=self.LIBS, elements=element(10, 10, rot='MR0', package='S')))
        self.assertNotIn('Top', parsed.layers)
        self.assertEqual(rounded(copper(parsed, 'Bottom').bounds), [7.5, 9.0, 8.5, 11.0])
        self.assertEqual(rounded(copper(parsed, 'bPlace').bounds), [8.95, 9.95, 10.05, 10.05])
        self.assertNotIn('tPlace', parsed.layers)
        self.assertIn('bStop', parsed.layers)
        self.assertNotIn('tStop', parsed.layers)

    def test_bad_rotation_is_rejected(self):
        with self.assertRaisesRegex(EagleBrdParseError, 'Invalid rotation'):
            parse(doc(libs=self.LIBS, elements=element(10, 10, rot='X', package='S')))


class TestEagleHolesAndDimensions(unittest.TestCase):
    def test_hole_is_npth_with_a_mask_opening(self):
        parsed = parse(doc(plain='<hole x="5" y="5" drill="3.2"/>'))
        self.assertEqual(list(parsed.drill_groups), ['NPTH'])
        drills = [(point.x, point.y, tool['tooldia']) for tool in parsed.drill_groups['NPTH'].values()
                  for point in tool['drills']]
        self.assertEqual(drills, [(5.0, 5.0, 3.2)])
        for layer in ('tStop', 'bStop'):
            with self.subTest(layer=layer):
                self.assertEqual(rounded(copper(parsed, layer).bounds, 3), [3.298, 3.298, 6.702, 6.702])
        self.assertIn('Holes', parsed.layers)

    def test_dimension_wires_form_the_board_outline(self):
        outline = ('<wire x1="0" y1="0" x2="10" y2="0" width="0" layer="20"/>'
                   '<wire x1="10" y1="0" x2="10" y2="5" width="0" layer="20"/>'
                   '<wire x1="10" y1="5" x2="0" y2="5" width="0" layer="20"/>'
                   '<wire x1="0" y1="5" x2="0" y2="0" width="0" layer="20"/>')
        parsed = parse(doc(plain=outline))
        self.assertEqual(rounded(parsed.board_outline.bounds), [0.0, 0.0, 10.0, 5.0])
        self.assertAlmostEqual(parsed.board_outline.area, 50.0, places=3)
        self.assertIn('Dimension', parsed.layers)
        self.assertEqual(len(parsed.warnings), 1)
        self.assertIn('Zero-width Dimension/Milling lines', parsed.warnings[0])

    def test_open_dimension_path_leaves_outline_empty_with_warning(self):
        parsed = parse(doc(plain='<wire x1="0" y1="0" x2="10" y2="0" width="0.1" layer="20"/>'))
        self.assertTrue(parsed.board_outline.is_empty)
        self.assertTrue(any('closed outline' in warning for warning in parsed.warnings))

    def test_dimension_annotation_is_warned_not_drawn(self):
        parsed = parse(doc(plain='<dimension x1="0" y1="0" x2="10" y2="0" x3="5" y3="2" layer="20" '
                                 'dtype="parallel" width="0.1"/>'))
        self.assertNotIn('Dimension', parsed.layers)
        self.assertEqual(len(parsed.warnings), 1)
        self.assertIn('Dimension annotations', parsed.warnings[0])


class TestEagleSilkscreenAndSmashed(unittest.TestCase):
    PKG = ('<library name="l"><packages><package name="S">'
           '<wire x1="0" y1="0" x2="1" y2="0" width="0.1" layer="21"/>'
           '<text x="0" y="3" size="1" layer="25">&gt;NAME</text>'
           '<smd name="1" x="0" y="0" dx="1" dy="2" layer="1"/>'
           '</package></packages></library>')

    def test_smashed_element_hides_package_name_text_and_draws_attributes(self):
        attributes = ('<attribute name="NAME" x="1" y="1" size="1" layer="25"/>'
                      '<attribute name="VALUE" x="1" y="2" size="1" layer="27"/>')
        parsed = parse(doc(libs=self.PKG, elements=element(0, 0, package='S', value='10k', extra=attributes)
                           .replace('value="10k"', 'value="10k" smashed="yes"')))
        self.assertIn('tNames', parsed.layers)
        self.assertIn('tValues', parsed.layers)
        # the package's own >NAME text sits at y = 3; only the attribute text at y = 1 is drawn
        self.assertLess(copper(parsed, 'tNames').bounds[3], 2.5)
        self.assertTrue(any('approximate' in warning for warning in parsed.warnings))

    def test_hidden_attribute_is_not_drawn(self):
        attributes = '<attribute name="NAME" x="1" y="1" size="1" layer="25" display="off"/>'
        parsed = parse(doc(libs=self.PKG.replace('<text x="0" y="3" size="1" layer="25">&gt;NAME</text>', ''),
                           elements=element(0, 0, package='S', extra=attributes)))
        self.assertNotIn('tNames', parsed.layers)

    def test_package_name_text_is_drawn_when_not_smashed(self):
        parsed = parse(doc(libs=self.PKG, elements=element(0, 0, package='S', value='10k')))
        self.assertIn('tNames', parsed.layers)
        self.assertGreater(copper(parsed, 'tNames').bounds[3], 2.5)

    def test_text_on_copper_is_warned_as_approximate(self):
        parsed = parse(doc(plain='<text x="1" y="1" size="1" layer="1">HELLO</text>'))
        self.assertIn('Top', parsed.layers)
        self.assertTrue(any('copper, stop mask or cream' in warning for warning in parsed.warnings))


class TestEagleMutationFuzz(unittest.TestCase):
    """Seeded character mutations of one valid board: only EagleBrdParseError may escape."""

    BASE = doc(
        plain=('<wire x1="0" y1="0" x2="10" y2="0" width="0.5" layer="1" curve="20"/>'
               '<wire x1="0" y1="0" x2="0" y2="5" width="0.2" layer="20"/>'
               '<wire x1="0" y1="5" x2="10" y2="5" width="0.2" layer="20"/>'
               '<wire x1="10" y1="5" x2="10" y2="0" width="0.2" layer="20"/>'
               '<hole x="5" y="2" drill="3.2"/>'),
        libs=('<library name="l"><packages><package name="P">'
              '<pad name="1" x="0" y="0" drill="0.8" diameter="1.0" shape="long"/>'
              '<smd name="2" x="3" y="0" dx="1" dy="2" layer="1" roundness="50"/>'
              '<wire x1="0" y1="0" x2="1" y2="1" width="0.1" layer="21"/>'
              '<text x="0" y="2" size="1" layer="25">&gt;NAME</text>'
              '</package></packages></library>'),
        elements=element(20, 20, rot='R90', package='P', extra='<attribute name="VALUE" x="1" y="1" size="1" '
                                                               'layer="27"/>'),
        signals='<signal name="N"><via x="2" y="3" extent="1-16" drill="0.3" diameter="0.6" shape="round"/>'
                '<wire x1="0" y1="0" x2="4" y2="0" width="0.2" layer="1"/></signal>')

    ALPHABET = '<>/"=&;!?-.0123456789abcdefgxyz \n'

    def test_mutated_files_never_leak_internal_errors(self):
        self.assertEqual(parse(self.BASE).default_layers[:1], ['Top'])
        rng = random.Random(20260614)
        leaked = []
        parsed_ok = 0
        with warnings.catch_warnings():
            warnings.simplefilter('error', RuntimeWarning)
            for _ in range(250):
                text = list(self.BASE)
                for _ in range(rng.randint(1, 4)):
                    text[rng.randrange(len(text))] = rng.choice(self.ALPHABET)
                mutated = ''.join(text)
                try:
                    EagleBrdParser().parse_text(mutated)
                    parsed_ok += 1
                except EagleBrdParseError as error:
                    if isinstance(error.__cause__, RuntimeWarning):
                        leaked.append(('RuntimeWarning', str(error), mutated))
                except Exception as error:      # anything else is a leak
                    leaked.append((type(error).__name__, str(error), mutated))
        self.assertEqual(leaked, [], 'first leak: %r' % (leaked[:1],))
        self.assertGreater(parsed_ok, 0)


if __name__ == '__main__':
    unittest.main()
