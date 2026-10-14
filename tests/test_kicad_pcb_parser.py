import unittest

from shapely import Point
from shapely.ops import unary_union

from appParsers.ParseKiCadPcb import KiCadPcbParser, KiCadPcbParseError


# Two copper layers: F.Cu, B.Cu. Tenting front, so only the back via mask is generated.
HEAD = '''(kicad_pcb (version 20240108) (generator "test")
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user "F.Silkscreen") (39 "F.Mask" user)
  (41 "B.Mask" user) (35 "F.Paste" user) (25 "Edge.Cuts" user) (49 "F.Fab" user) (45 "B.Fab" user))
 (setup (pad_to_mask_clearance 0.05) (tenting (front yes) (back no)))
'''

# Four copper layers: F.Cu, In1.Cu, In2.Cu, B.Cu.
HEAD_INNER = HEAD.replace('(31 "B.Cu" signal)',
                          '(1 "In1.Cu" signal) (2 "In2.Cu" signal) (31 "B.Cu" signal)')


def parse(body, head=HEAD):
    return KiCadPcbParser().parse_text(head + body + ')')


def footprint(body, at='(at 0 0)'):
    return '(footprint "T" (layer "F.Cu") %s %s)' % (at, body)


def geo(parsed, layer):
    return unary_union(parsed.layers[layer]['solid_geometry'])


def groups_summary(parsed):
    return {group: {tool['tooldia']: (len(tool['drills']), len(tool['slots']))
                    for tool in tools.values()}
            for group, tools in parsed.drill_groups.items()}


class TestKiCadPcbTracks(unittest.TestCase):
    def test_parse_text_returns_self_and_flips_y(self):
        parser = KiCadPcbParser()
        self.assertIs(parser.parse_text(HEAD + '(segment (start 1 2) (end 11 2) (width 0.5) '
                                        '(layer "F.Cu") (net 1))' + ')'), parser)
        for actual, expected in zip(geo(parser, 'F.Cu').bounds, (0.75, -2.25, 11.25, -1.75)):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(parser.version, 20240108)
        self.assertEqual(parser.copper_layers, ['F.Cu', 'B.Cu'])

    def test_diagonal_segment_centreline_is_y_negated(self):
        parser = parse('(segment (start 0 0) (end 10 5) (width 1) (layer "F.Cu") (net 1))')
        coords = list(parser.layers['F.Cu']['follow_geometry'][0].coords)
        self.assertEqual(len(coords), 2)
        for actual, expected in zip(coords, [(0, 0), (10, -5)]):
            self.assertAlmostEqual(actual[0], expected[0], places=9)
            self.assertAlmostEqual(actual[1], expected[1], places=9)

    def test_segment_has_round_caps(self):
        parser = parse('(segment (start 1 2) (end 11 2) (width 0.5) (layer "F.Cu") (net 1))')
        copper = geo(parser, 'F.Cu')
        self.assertTrue(copper.contains(Point(0.8, -2.0)))
        # A square cap would include the corner (0.76, -2.24); a round cap at radius 0.25 does not.
        self.assertFalse(copper.contains(Point(0.76, -2.24)))
        self.assertAlmostEqual(copper.area, 10 * 0.5 + 3.14159265 * 0.25 ** 2, places=2)

    def test_arc_passes_through_start_mid_and_end(self):
        parser = parse('(arc (start 0 0) (mid 1 1) (end 2 0) (width 0.2) (layer "F.Cu") (net 1))')
        follow = parser.layers['F.Cu']['follow_geometry'][0]
        coords = list(follow.coords)
        self.assertAlmostEqual(coords[0][0], 0, places=6)
        self.assertAlmostEqual(coords[0][1], 0, places=6)
        self.assertAlmostEqual(coords[-1][0], 2, places=6)
        self.assertAlmostEqual(coords[-1][1], 0, places=6)
        # File mid (1, 1) is output (1, -1): the arc bulges to negative output Y.
        self.assertLess(follow.distance(Point(1, -1)), 0.01)
        # The file's mid point (1, 1) lies on the circle's other half, away from the drawn arc.
        self.assertGreater(follow.distance(Point(1, 1)), 1.0)
        bounds = geo(parser, 'F.Cu').bounds
        for actual, expected in zip(bounds, (-0.1, -1.1, 2.1, 0.1)):
            self.assertAlmostEqual(actual, expected, places=2)

    def test_track_without_net_is_still_copper(self):
        parser = parse('(segment (start 0 0) (end 5 0) (width 0.3) (layer "B.Cu"))')
        self.assertIn('B.Cu', parser.layers)
        self.assertFalse(parser.layers['B.Cu']['solid_geometry'][0].is_empty)


class TestKiCadPcbVias(unittest.TestCase):
    def test_through_via_on_all_copper_layers_with_drill_group(self):
        parser = parse('(via (at 5 5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))')
        self.assertIn('F.Cu', parser.layers)
        self.assertIn('B.Cu', parser.layers)
        for layer in ('F.Cu', 'B.Cu'):
            for actual, expected in zip(geo(parser, layer).bounds, (4.6, -5.4, 5.4, -4.6)):
                self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(list(parser.drill_groups), ['PTH_F.Cu-B.Cu'])
        tool = list(parser.drill_groups['PTH_F.Cu-B.Cu'].values())[0]
        self.assertAlmostEqual(tool['tooldia'], 0.4)
        self.assertEqual((tool['drills'][0].x, tool['drills'][0].y), (5, -5))

    def test_through_via_spans_all_inner_layers(self):
        parser = parse('(via (at 5 5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))', HEAD_INNER)
        self.assertEqual([name for name in ('F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu') if name in parser.layers],
                         ['F.Cu', 'In1.Cu', 'In2.Cu', 'B.Cu'])
        self.assertEqual(list(parser.drill_groups), ['PTH_F.Cu-B.Cu'])

    def test_blind_via_gets_its_own_span_group_and_only_its_layers(self):
        parser = parse('(via blind (at 5 5) (size 0.8) (drill 0.4) (layers "F.Cu" "In1.Cu") (net 1))',
                       HEAD_INNER)
        self.assertIn('F.Cu', parser.layers)
        self.assertIn('In1.Cu', parser.layers)
        self.assertNotIn('B.Cu', parser.layers)
        self.assertNotIn('In2.Cu', parser.layers)
        self.assertEqual(list(parser.drill_groups), ['PTH_F.Cu-In1.Cu'])

    def test_via_mask_is_opened_on_untented_side_only(self):
        parser = parse('(via (at 5 5) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))')
        self.assertNotIn('F.Mask', parser.layers)       # front is tented in the setup
        self.assertIn('B.Mask', parser.layers)
        self.assertAlmostEqual(geo(parser, 'B.Mask').bounds[2], 5 + 0.4 + 0.05, places=6)

    def test_via_drill_larger_than_size_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            parse('(via (at 5 5) (size 0.8) (drill 1.4) (layers "F.Cu" "B.Cu"))')


class TestKiCadPcbPads(unittest.TestCase):
    def test_pad_shapes_have_expected_outlines(self):
        cases = {
            'circle': ('(size 2 2)', 3.14159265),
            'rect': ('(size 2 1)', 2.0),
            'oval': ('(size 2 1)', 1 + 3.14159265 / 4),
            'roundrect': ('(size 2 1) (roundrect_rratio 0.5)', 1 + 3.14159265 / 4),
        }
        for shape, (size, area) in cases.items():
            with self.subTest(shape=shape):
                parser = parse(footprint('(pad "1" smd %s (at 0 0) %s (layers "F.Cu"))' % (shape, size)))
                self.assertAlmostEqual(geo(parser, 'F.Cu').area, area, places=2)

    def test_roundrect_is_not_a_plain_rectangle(self):
        parser = parse(footprint('(pad "1" smd roundrect (at 0 0) (size 2 1) (roundrect_rratio 0.5) '
                                 '(layers "F.Cu"))'))
        self.assertFalse(geo(parser, 'F.Cu').contains(Point(0.95, 0.45)))  # rectangle corner

    def test_pad_angle_rotates_its_shape(self):
        parser = parse(footprint('(pad "1" smd rect (at 0 0 90) (size 2 1) (layers "F.Cu"))'))
        bounds = geo(parser, 'F.Cu').bounds
        self.assertAlmostEqual(bounds[2] - bounds[0], 1, places=6)
        self.assertAlmostEqual(bounds[3] - bounds[1], 2, places=6)

    def test_footprint_translation_moves_pad(self):
        parser = parse(footprint('(pad "1" smd rect (at 2 0) (size 2 1) (layers "F.Cu"))',
                                 at='(at 10 10)'))
        for actual, expected in zip(geo(parser, 'F.Cu').bounds, (11, -10.5, 13, -9.5)):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_footprint_rotation_moves_pad_centre(self):
        # Footprint at (10, 10) rotated 90 degrees: local (2, 0) lands at board (10, 8) in KiCad
        # coordinates, which is output (10, -8).
        parser = parse(footprint('(pad "1" thru_hole circle (at 2 0) (size 1.6 1.6) (drill 0.8) '
                                 '(layers "*.Cu"))', at='(at 10 10 90)'))
        tool = list(parser.drill_groups['PTH_F.Cu-B.Cu'].values())[0]
        self.assertAlmostEqual(tool['drills'][0].x, 10, places=6)
        self.assertAlmostEqual(tool['drills'][0].y, -8, places=6)

    def test_mask_margin_grows_the_mask_opening(self):
        parser = parse(footprint('(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask"))'))
        for actual, expected in zip(geo(parser, 'F.Mask').bounds, (-0.55, -0.55, 0.55, 0.55)):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_local_solder_mask_margin_overrides_board_setup(self):
        parser = parse(footprint('(pad "1" smd rect (at 0 0) (size 1 1) (solder_mask_margin 0.2) '
                                 '(layers "F.Cu" "F.Mask"))'))
        for actual, expected in zip(geo(parser, 'F.Mask').bounds, (-0.7, -0.7, 0.7, 0.7)):
            self.assertAlmostEqual(actual, expected, places=6)

    def test_wildcard_layers_expand_to_copper_and_mask(self):
        parser = parse(footprint('(pad "1" smd rect (at 0 0) (size 1 1) (layers "*.Cu" "*.Mask"))'),
                       HEAD_INNER)
        self.assertEqual(sorted(name for name in parser.layers if name.endswith('.Cu')),
                         ['B.Cu', 'F.Cu', 'In1.Cu', 'In2.Cu'])
        self.assertIn('F.Mask', parser.layers)
        self.assertIn('B.Mask', parser.layers)

    def test_thru_hole_oval_drill_becomes_slot(self):
        parser = parse(footprint('(pad "6" thru_hole oval (at 0 0) (size 1.6 3) (drill oval 0.8 2) '
                                 '(layers "*.Cu" "*.Mask"))'))
        tools = parser.drill_groups['PTH_F.Cu-B.Cu']
        self.assertEqual(groups_summary(parser), {'PTH_F.Cu-B.Cu': {0.8: (0, 1)}})
        first, last = list(tools.values())[0]['slots'][0]
        self.assertAlmostEqual(first.x, 0, places=6)
        self.assertAlmostEqual(last.x, 0, places=6)
        self.assertAlmostEqual(sorted([first.y, last.y])[0], -0.6, places=6)
        self.assertAlmostEqual(sorted([first.y, last.y])[1], 0.6, places=6)

    def test_non_plated_hole_goes_to_npth_group(self):
        parser = parse(footprint('(pad "" np_thru_hole circle (at 0 0) (size 2 2) (drill 1) '
                                 '(layers "*.Cu" "*.Mask"))'))
        self.assertEqual(groups_summary(parser), {'NPTH_F.Cu-B.Cu': {1.0: (1, 0)}})
        self.assertIn('F.Cu', parser.layers)           # annular ring larger than the hole

    def test_np_thru_hole_without_ring_leaves_no_copper(self):
        parser = parse(footprint('(pad "" np_thru_hole circle (at 0 0) (size 1 1) (drill 1) '
                                 '(layers "*.Cu" "*.Mask"))'))
        self.assertNotIn('F.Cu', parser.layers)
        self.assertNotIn('B.Cu', parser.layers)
        self.assertEqual(list(parser.drill_groups), ['NPTH_F.Cu-B.Cu'])

    def test_unknown_pad_shape_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            parse(footprint('(pad "1" smd weird (at 0 0) (size 2 1) (layers "F.Cu"))'))

    def test_thru_hole_pad_without_drill_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            parse(footprint('(pad "1" thru_hole circle (at 0 0) (size 1.6 1.6) (layers "*.Cu"))'))


class TestKiCadPcbZones(unittest.TestCase):
    SQUARE = '(polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))'

    def test_zone_uses_saved_filled_polygon(self):
        parser = parse('(zone (net 1) (net_name "GND") (layer "F.Cu") (fill yes (thermal_gap 0.2)) %s '
                       '(filled_polygon (layer "F.Cu") (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10))))'
                       % self.SQUARE)
        self.assertAlmostEqual(geo(parser, 'F.Cu').area, 100, places=6)
        for actual, expected in zip(geo(parser, 'F.Cu').bounds, (0, -10, 10, 0)):
            self.assertAlmostEqual(actual, expected, places=6)
        self.assertEqual(parser.warnings, [])

    def test_zone_keeps_a_hole_from_a_keyhole_fill(self):
        # Outer square 0..10 with a 2x2 hole at 4..6, joined to the outline by a zero-width bridge.
        ring = ('(xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10) (xy 0 5) (xy 4 5) (xy 4 6) (xy 6 6) '
                '(xy 6 4) (xy 4 4) (xy 4 5) (xy 0 5)')
        parser = parse('(zone (net 1) (net_name "GND") (layer "F.Cu") (fill yes) %s '
                       '(filled_polygon (layer "F.Cu") (pts %s)))' % (self.SQUARE, ring))
        copper = geo(parser, 'F.Cu')
        self.assertAlmostEqual(copper.area, 96, places=6)
        self.assertFalse(copper.contains(Point(5, -5)))  # inside the hole (output Y negated)
        self.assertTrue(copper.contains(Point(1, -1)))

    def test_filled_zone_without_saved_fill_adds_no_copper_and_warns(self):
        parser = parse('(zone (net 1) (net_name "GND") (layer "F.Cu") (fill yes) %s)' % self.SQUARE)
        self.assertNotIn('F.Cu', parser.layers)
        self.assertEqual(len(parser.warnings), 1)
        self.assertIn('not refilled', parser.warnings[0])

    def test_rule_area_adds_no_copper(self):
        parser = parse('(zone (net 0) (net_name "") (layers "F.Cu" "B.Cu") (keepout (tracks not_allowed)) %s)'
                       % self.SQUARE)
        self.assertNotIn('F.Cu', parser.layers)
        self.assertNotIn('B.Cu', parser.layers)


class TestKiCadPcbText(unittest.TestCase):
    def test_hidden_property_is_not_drawn(self):
        parser = parse(footprint(
            '(property "Reference" "R1" (at 0 -2 0) (layer "F.SilkS") (hide yes) '
            '(effects (font (size 1 1) (thickness 0.15))))'))
        self.assertNotIn('F.SilkS', parser.layers)

    def test_visible_property_is_drawn(self):
        parser = parse(footprint(
            '(property "Reference" "R1" (at 0 -2 0) (layer "F.SilkS") '
            '(effects (font (size 1 1) (thickness 0.15))))'))
        self.assertIn('F.SilkS', parser.layers)

    def test_hidden_effects_flag_text_is_not_drawn_kicad_6_7_form(self):
        # KiCad 6/7 write hidden text as a bare "hide" token inside (effects ...).
        # Expected: nothing is drawn on F.SilkS.
        parser = parse('(gr_text "HIDDEN" (at 5 5 0) (layer "F.SilkS") '
                       '(effects (font (size 1 1) (thickness 0.15)) hide))')
        self.assertNotIn('F.SilkS', parser.layers)

    def test_hidden_effects_yes_text_is_not_drawn(self):
        parser = parse('(gr_text "HIDDEN" (at 5 5 0) (layer "F.SilkS") '
                       '(effects (font (size 1 1)) (hide yes)))')
        self.assertNotIn('F.SilkS', parser.layers)

    def test_text_is_drawn_with_approximate_font_warning(self):
        parser = parse('(gr_text "HELLO" (at 5 5 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))')
        self.assertIn('F.SilkS', parser.layers)
        self.assertTrue(any('approximate stroke font' in warning for warning in parser.warnings))

    def test_dimension_text_is_imported_and_lines_warned(self):
        parser = parse('(dimension (type aligned) (layer "F.SilkS") (pts (xy 0 0) (xy 10 0)) '
                       '(gr_text "10 mm" (at 5 -2 0) (layer "F.SilkS") '
                       '(effects (font (size 1 1) (thickness 0.15)))))')
        self.assertIn('F.SilkS', parser.layers)
        self.assertTrue(any('Dimension lines' in warning for warning in parser.warnings))


class TestKiCadPcbOutline(unittest.TestCase):
    def test_closed_edge_cuts_rectangle_becomes_board_outline(self):
        parser = parse('(gr_rect (start 0 0) (end 20 10) (stroke (width 0.1) (type default)) '
                       '(fill none) (layer "Edge.Cuts"))')
        self.assertAlmostEqual(parser.board_outline.area, 200, places=1)
        for actual, expected in zip(parser.board_outline.bounds, (0, -10, 20, 0)):
            self.assertAlmostEqual(actual, expected, places=3)

    def test_open_edge_cuts_line_gives_no_outline_and_warns(self):
        parser = parse('(gr_line (start 0 0) (end 20 0) (stroke (width 0.1) (type default)) '
                       '(layer "Edge.Cuts"))')
        self.assertTrue(parser.board_outline.is_empty)
        self.assertTrue(any('closed outline' in warning for warning in parser.warnings))


class TestKiCadPcbErrors(unittest.TestCase):
    def test_not_a_kicad_pcb_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            KiCadPcbParser().parse_text('(foo)')

    def test_empty_text_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            KiCadPcbParser().parse_text('')

    def test_format_older_than_kicad_6_raises(self):
        with self.assertRaises(KiCadPcbParseError) as context:
            KiCadPcbParser().parse_text('(kicad_pcb (version 20210101) (layers (0 "F.Cu" signal)))')
        self.assertIn('older than KiCad 6', str(context.exception))

    def test_missing_version_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            KiCadPcbParser().parse_text('(kicad_pcb (generator x) (layers (0 "F.Cu" signal)))')

    def test_truncated_file_raises(self):
        with self.assertRaises(KiCadPcbParseError) as context:
            KiCadPcbParser().parse_text(HEAD)
        self.assertIn('truncated', str(context.exception))

    def test_extra_closing_paren_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            KiCadPcbParser().parse_text(HEAD + ')))')

    def test_unterminated_string_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            KiCadPcbParser().parse_text('(kicad_pcb (version 20240108) "abc)')

    def test_unknown_top_level_item_warns_and_continues(self):
        parser = parse('(frobnicate 1) (segment (start 0 0) (end 5 0) (width 0.3) (layer "F.Cu") (net 1))')
        self.assertIn('F.Cu', parser.layers)
        self.assertTrue(any('frobnicate' in warning for warning in parser.warnings))

    def test_unknown_copper_layer_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            parse('(segment (start 0 0) (end 1 0) (width 0.2) (layer "In3.Cu"))')

    def test_zero_width_track_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            parse('(segment (start 0 0) (end 1 0) (width 0) (layer "F.Cu"))')

    def test_non_numeric_coordinate_raises(self):
        with self.assertRaises(KiCadPcbParseError):
            parse('(segment (start 0 0) (end 1 x) (width 0.2) (layer "F.Cu"))')


if __name__ == '__main__':
    unittest.main()
