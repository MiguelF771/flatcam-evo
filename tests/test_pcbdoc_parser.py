import math
from pathlib import Path
import re
import struct
import unittest
from unittest.mock import patch

from shapely import LineString, Point, Polygon, box
from shapely.affinity import rotate, translate
from shapely.ops import unary_union

from appParsers.ParsePcbDoc import PcbDocParser, PcbDocParseError


UNIT = 2.54e-6


def raw(value):
    return round(value / UNIT)


def block(data):
    return struct.pack('<I', len(data)) + data


def properties(values):
    return block(('|' + '|'.join('%s=%s' % item for item in values.items()) + '\0').encode('latin1'))


def track(layer=1, start=(1, 2), end=(5, 2), width=0.4, flags=0):
    data = bytearray(45)
    data[0] = layer
    data[1] = flags
    struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
    struct.pack_into('<iiiii', data, 13, *(raw(v) for v in (*start, *end, width)))
    return b'\x04' + block(data)


def region(shell, holes=(), layer=1, kind=0, cutout=False, polygon=65535):
    data = bytearray(18)
    data[0] = layer
    struct.pack_into('<HHH', data, 3, 65535, polygon, 65535)
    struct.pack_into('<H', data, 14, len(holes))
    data += properties({'KIND': kind, 'ISBOARDCUTOUT': str(cutout).upper()})
    for ring in [shell, *holes]:
        data += struct.pack('<I', len(ring))
        for x, y in ring:
            data += struct.pack('<dd', x / UNIT, y / UNIT)
    return b'\x0b' + block(data)


def pad(layer=74, size=(3, 2), shape=1, hole=1, rotation=0, slot=0, slotrotation=0,
        mode=0, alt=0, corner=0, plated=True):
    data = bytearray(171)
    data[0] = layer
    struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
    struct.pack_into('<ii', data, 13, raw(5), raw(5))
    for offset in (21, 29, 37):
        struct.pack_into('<ii', data, offset, *(raw(v) for v in size))
    struct.pack_into('<i', data, 45, raw(hole))
    data[49:52] = bytes([shape] * 3)
    struct.pack_into('<d', data, 52, rotation)
    data[60] = plated
    data[62] = mode
    data[101] = 0
    data[102] = 2
    struct.pack_into('<i', data, 90, raw(0.1))
    stack = bytearray(596)
    stack[262] = 2 if slot else 0
    struct.pack_into('<id', stack, 263, raw(slot), slotrotation)
    stack[532:564] = bytes([alt] * 32)
    stack[564:596] = bytes([corner] * 32)
    return b'\x02' + block(b'\x011') + block(b'') * 3 + block(data) + block(stack)


def board(stack=(1, 32)):
    values = {'ORIGINX': '0mil', 'ORIGINY': '0mil', 'KIND': 'Protel_Advanced_PCB'}
    for i, (x, y) in enumerate([(0, 0), (10, 0), (10, 10), (0, 10)]):
        values['VX%d' % i] = '%smm' % x
        values['VY%d' % i] = '%smm' % y
    for i, layer in enumerate(stack):
        values['LAYER%dNAME' % layer] = 'Layer %d' % layer
        values['LAYER%dNEXT' % layer] = str(stack[i + 1] if i + 1 < len(stack) else 0)
        values['LAYER%dPREV' % layer] = str(stack[i - 1] if i else 0)
    return properties(values)


def text_record(text, position=(1, 2), height=1, width=0.1, angle=0, mirrored=False,
                inverted=False, frame=None, alphabet=1):
    data = bytearray(137)
    data[0] = 1
    struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
    struct.pack_into('<iiiH', data, 13, raw(position[0]), raw(position[1]), raw(height), alphabet)
    struct.pack_into('<d', data, 27, angle)
    data[35] = mirrored
    struct.pack_into('<i', data, 36, raw(width))
    data[110] = inverted
    if frame:
        data[123] = 1
        struct.pack_into('<ii', data, 124, *(raw(v) for v in frame))
        data[132] = 3
    value = text.encode('latin1')
    return b'\x05' + block(data) + block(bytes([len(value)]) + value)


def reference_linear_strokes(filename, diameter):
    """Read original Gerber line strokes for comparison, not production rendering."""
    data = filename.read_bytes().decode('ascii')
    factor = 25.4 if '%MOIN' in data else 1.0
    format_spec = re.search(r'%FSLAX\d(\d)Y', data)
    if not format_spec:
        raise ValueError('Reference has no supported absolute coordinate format')
    decimals = int(format_spec[1])
    coordinate_factor = factor * 10 ** -decimals
    tools = {int(number) for number, size in re.findall(r'%ADD(\d+)C,([\d.]+)\*%', data)
             if abs(float(size) * factor - diameter) < 1e-9}
    x = y = 0.0
    operation = 2
    tool = 0
    interpolation = 1
    current = []
    paths = []
    for token in data.split('*'):
        token = token.strip().strip('%').strip()
        if re.fullmatch(r'D[1-9]\d+', token):
            if len(current) > 1 and tool in tools:
                paths.append(LineString(current))
            current = []
            tool = int(token[1:])
            continue
        if token in ('G01', 'G02', 'G03'):
            interpolation = int(token[-1])
            continue
        if not token or not re.fullmatch(r'(?:X-?\d+)?(?:Y-?\d+)?(?:I-?\d+)?(?:J-?\d+)?(?:D0?[123])?', token):
            continue
        code = re.search(r'D0?([123])$', token)
        if code:
            operation = int(code[1])
        coord_x = re.search(r'X(-?\d+)', token)
        coord_y = re.search(r'Y(-?\d+)', token)
        x = int(coord_x[1]) * coordinate_factor if coord_x else x
        y = int(coord_y[1]) * coordinate_factor if coord_y else y
        if operation == 2:
            if len(current) > 1 and tool in tools:
                paths.append(LineString(current))
            current = [(x, y)]
        elif operation == 1 and interpolation == 1:
            current.append((x, y))
    if len(current) > 1 and tool in tools:
        paths.append(LineString(current))
    return paths, coordinate_factor


class TestPcbDocParser(unittest.TestCase):
    def parser(self, streams=None, stack=(1, 32)):
        self.assertIsNotNone(PcbDocParser, 'native PcbDoc parser is not implemented')
        parser = PcbDocParser()
        data = {'Board6': board(stack)}
        data.update(streams or {})
        parser._parse_streams(data)
        return parser

    def test_regions_preserve_holes_and_nested_islands_for_export(self):
        outer = list(box(0, 0, 9, 9).exterior.coords)
        inner = list(box(2, 2, 7, 7).exterior.coords)
        island = list(box(3, 3, 4, 4).exterior.coords)
        parser = self.parser({'Regions6': region(outer, [inner]) + region(island)})
        layer = parser.layers[1]
        self.assertAlmostEqual(unary_union(layer['solid_geometry']).area, 57)
        exported = Polygon()
        for item in layer['tools'][0]['geometry']:
            solid = item['solid']
            exported = exported.union(Polygon(solid.exterior)).difference(
                unary_union([Polygon(r) for r in solid.interiors]))
        self.assertAlmostEqual(exported.area, 57)
        self.assertTrue(exported.contains(Point(3.5, 3.5)))
        self.assertFalse(exported.contains(Point(5, 5)))

    def test_cutout_removes_board_and_copper_not_keepout_tracks(self):
        hole = list(box(4, 4, 6, 6).exterior.coords)
        parser = self.parser({'Regions6': region(list(box(0, 0, 10, 10).exterior.coords)) +
                              region(hole, cutout=True), 'Tracks6': track(56)})
        self.assertAlmostEqual(parser.board_outline.area, 96)
        self.assertAlmostEqual(unary_union(parser.layers[1]['solid_geometry']).area, 96)
        self.assertGreater(unary_union(parser.layers[56]['solid_geometry']).area, 0)

    def test_negative_plane_is_bounded_and_not_positive_artwork(self):
        parser = self.parser({'Tracks6': track(39)}, stack=(1, 39, 32))
        plane = unary_union(parser.layers[39]['solid_geometry'])
        self.assertFalse(plane.contains(Point(3, 2)))
        self.assertTrue(plane.contains(Point(8, 8)))
        self.assertTrue(box(0, 0, 10, 10).covers(plane))

    def test_multilayer_pad_uses_only_linked_copper_layers(self):
        parser = self.parser({'Pads6': pad()}, stack=(1, 2, 32))
        self.assertEqual(set(parser.layers), {1, 2, 32, 37, 38})
        self.assertEqual(sum(len(t['drills']) for g in parser.drill_groups.values() for t in g.values()), 1)
        self.assertAlmostEqual(next(iter(next(iter(parser.drill_groups.values())).values()))['tooldia'], 1, places=5)

    def test_rounded_rectangle_and_oblong_are_not_ellipses(self):
        oblong = self.parser({'Pads6': pad(layer=1, hole=0)})
        geo = unary_union(oblong.layers[1]['solid_geometry'])
        self.assertAlmostEqual(geo.area, 2 + math.pi, delta=0.01)
        rounded = self.parser({'Pads6': pad(layer=1, shape=2, hole=0, alt=9, corner=50)})
        geo = unary_union(rounded.layers[1]['solid_geometry'])
        self.assertAlmostEqual(geo.area, 6 - (4 - math.pi) * 0.25, delta=0.01)

    def test_true_slot_endpoints_include_pad_and_slot_rotation(self):
        parser = self.parser({'Pads6': pad(slot=3, rotation=30, slotrotation=15, plated=False)})
        group = next(iter(parser.drill_groups))
        self.assertIn('NPTH', group)
        tool = next(iter(parser.drill_groups[group].values()))
        self.assertEqual(tool['drills'], [])
        first, last = tool['slots'][0]
        self.assertAlmostEqual(first.distance(last), 2, places=5)
        self.assertAlmostEqual(last.x - first.x, math.sqrt(2), places=5)
        self.assertAlmostEqual(last.y - first.y, math.sqrt(2), places=5)

    def test_tracks_arcs_and_optional_tails(self):
        arc = bytearray(56)
        arc[0] = 1
        struct.pack_into('<HHH', arc, 3, 65535, 65535, 65535)
        struct.pack_into('<iii', arc, 13, raw(5), raw(5), raw(2))
        struct.pack_into('<dd', arc, 25, 0, 90)
        struct.pack_into('<i', arc, 41, raw(0.2))
        parser = self.parser({'Tracks6': track() * 2, 'Arcs6': b'\x01' + block(arc)})
        follows = parser.layers[1]['follow_geometry']
        self.assertEqual(len(follows), 3)
        self.assertAlmostEqual(follows[-1].length, math.pi, delta=0.001)
        self.assertAlmostEqual(follows[-1].coords[-1][1], 7, places=5)

    def test_bad_lengths_tags_and_header_counts_raise(self):
        self.assertIsNotNone(PcbDocParser, 'native PcbDoc parser is not implemented')
        for stream in (track()[:-1], b'\x04\xff\xff\xff\x7f', b'\x09' + block(b'abc'), pad()[:-1]):
            with self.subTest(stream=stream[:8]):
                with self.assertRaises(PcbDocParseError):
                    self.parser({'Tracks6' if stream[0] != 2 else 'Pads6': stream})
        with self.assertRaises(PcbDocParseError):
            PcbDocParser()._parse_streams({'Board6': board(), 'Tracks6': track()}, {'Tracks6': 2})

    def test_unknown_pad_shape_raises_instead_of_wrong_copper(self):
        self.assertIsNotNone(PcbDocParser, 'native PcbDoc parser is not implemented')
        with self.assertRaises(PcbDocParseError):
            self.parser({'Pads6': pad(shape=99)})

    def test_v8_pad_layer_marker_is_not_a_blind_drill_span(self):
        record = bytearray(pad())
        reader_offset = 1
        for _ in range(4):
            reader_offset += 4 + struct.unpack_from('<I', record, reader_offset)[0]
        struct.pack_into('<I', record, reader_offset + 4 + 114, 0x0103000f)
        parser = self.parser({'Pads6': bytes(record)})
        self.assertEqual(set(parser.drill_groups), {'PTH_1-32'})

    def test_full_pad_stack_uses_inner_size_and_positive_copper_offset(self):
        record = bytearray(pad(mode=2, shape=2, rotation=90))
        offset = 1
        parts = []
        for _ in range(6):
            length = struct.unpack_from('<I', record, offset)[0]
            parts.append(offset + 4)
            offset += 4 + length
        struct.pack_into('<ii', record, parts[4] + 29, raw(1.5), raw(0.8))
        struct.pack_into('<i', record, parts[5], raw(0.9))
        struct.pack_into('<i', record, parts[5] + 116, raw(0.5))
        record[parts[5] + 232] = 2
        struct.pack_into('<i', record, parts[5] + 275, raw(0.5))
        parser = self.parser({'Pads6': bytes(record)}, stack=(1, 2, 3, 32))
        top = unary_union(parser.layers[1]['solid_geometry'])
        inner = unary_union(parser.layers[2]['solid_geometry'])
        self.assertAlmostEqual(top.centroid.y, 5.5, places=5)
        self.assertAlmostEqual(inner.area, 1.2, places=5)
        mid2 = unary_union(parser.layers[3]['solid_geometry'])
        self.assertAlmostEqual(mid2.area, 0.45, places=5)
        drill = next(iter(parser.drill_groups['PTH_1-32'].values()))['drills'][0]
        self.assertAlmostEqual(drill.y, 5, places=5)

    def test_scoped_mask_rule_and_tenting(self):
        record = bytearray(pad(layer=1, hole=0))
        offset = 1
        for _ in range(4):
            offset += 4 + struct.unpack_from('<I', record, offset)[0]
        record[offset + 4 + 102] = 1
        rules = b'\0\0' + properties({'RULEKIND': 'SolderMaskExpansion', 'PRIORITY': 1,
            'SCOPE1EXPRESSION': "IsPad AND (IsSMDPad OR InNet('GND'))", 'EXPANSION': '0.2mm'})
        parser = self.parser({'Pads6': bytes(record), 'Rules6': rules})
        opening = unary_union(parser.layers[37]['solid_geometry'])
        self.assertAlmostEqual(opening.bounds[0], 3.3, delta=0.0001)
        record[offset + 4 + 1] |= 0x20
        parser = self.parser({'Pads6': bytes(record), 'Rules6': rules})
        self.assertNotIn(37, parser.layers)

    def test_boolean_rule_scope_respects_and_before_or(self):
        parser = self.parser()
        context = {'kind': 'Pad', 'multilayer': True, 'net': '', 'component': '', 'layer': 1}
        self.assertFalse(parser._scope('IsPad AND IsSMDPad OR IsVia', context))
        self.assertTrue(parser._scope('IsPad OR IsSMDPad AND IsVia', context))
        self.assertTrue(parser._scope('IsVia AND IsPad OR IsPad', context))

    def test_shape_based_duplicate_does_not_fill_plain_region_hole(self):
        shell = list(box(0, 0, 9, 9).exterior.coords)
        hole = list(box(2, 2, 7, 7).exterior.coords)
        data = bytearray(18)
        data[0] = 1
        struct.pack_into('<HHH', data, 3, 65535, 0, 65535)
        data += properties({'KIND': 0, 'SUBPOLYINDEX': 65535})
        data += struct.pack('<I', len(shell) - 1)
        for x, y in shell:
            data += struct.pack('<Biiiiidd', 0, raw(x), raw(y), 0, 0, 0, 0, 0)
        parser = self.parser({'Regions6': region(shell, [hole], polygon=0),
                              'ShapeBasedRegions6': b'\x0b' + block(data)})
        self.assertAlmostEqual(unary_union(parser.layers[1]['solid_geometry']).area, 56)

    def test_polygon_cutout_erases_pour_but_not_independent_tracks(self):
        parser = self.parser({'Regions6': region(list(box(0, 0, 9, 9).exterior.coords), polygon=0) +
                              region(list(box(2, 2, 7, 7).exterior.coords), kind=1),
                              'Tracks6': track(start=(3, 4), end=(6, 4))})
        geo = unary_union(parser.layers[1]['solid_geometry'])
        self.assertTrue(geo.contains(Point(4, 4)))
        self.assertFalse(geo.contains(Point(4, 5)))

    def test_missing_saved_pour_and_bad_rule_scope_fail_closed(self):
        self.assertIsNotNone(PcbDocParser)
        with self.assertRaisesRegex(PcbDocParseError, 'no saved pour'):
            self.parser({'Polygons6': properties({'LAYER': 'TOP', 'HATCHSTYLE': 'Solid'})})
        record = bytearray(pad(layer=1, hole=0))
        offset = 1
        for _ in range(4):
            offset += 4 + struct.unpack_from('<I', record, offset)[0]
        record[offset + 4 + 102] = 1
        with self.assertRaisesRegex(PcbDocParseError, 'rule scope'):
            self.parser({'Pads6': bytes(record), 'Rules6': b'\0\0' + properties({
                'RULEKIND': 'SolderMaskExpansion', 'SCOPE1EXPRESSION': 'UnknownFunction()', 'EXPANSION': '4mil'})})

    def test_plane_clearance_and_connected_pad_relief(self):
        data = bytearray(pad(size=(2, 2)))
        offset = 1
        for _ in range(4):
            offset += 4 + struct.unpack_from('<I', data, offset)[0]
        struct.pack_into('<H', data, offset + 4 + 3, 0)
        board_data = board((1, 39, 32))
        values = PcbDocParser._props(board_data[4:])
        values['LAYER39NETNAME'] = 'GND'
        rules = b'\0\0' + properties({'RULEKIND': 'PlaneClearance', 'CLEARANCE': '0.5mm'})
        rules += b'\0\0' + properties({'RULEKIND': 'PlaneConnect', 'CONNECTSTYLE': 'Relief',
            'AIRGAPWIDTH': '0.5mm', 'RELIEFCONDUCTORWIDTH': '0.3mm', 'RELIEFENTRIES': 4})
        parser = PcbDocParser()._parse_streams({'Board6': properties(values), 'Pads6': bytes(data),
                                              'Nets6': properties({'NAME': 'GND'}), 'Rules6': rules})
        copper = unary_union(parser.layers[39]['solid_geometry'])
        self.assertTrue(copper.contains(Point(6.3, 5)))
        self.assertFalse(copper.contains(Point(5.9, 5.9)))
        values['LAYER39NETNAME'] = 'VCC'
        parser = PcbDocParser()._parse_streams({'Board6': properties(values), 'Pads6': bytes(data),
                                              'Nets6': properties({'NAME': 'GND'}), 'Rules6': rules})
        self.assertFalse(unary_union(parser.layers[39]['solid_geometry']).contains(Point(6.3, 5)))

    def test_true_type_font_counters_remain_holes(self):
        parser = self.parser()
        geo = parser._font_geometry('OB', 'Arial', 'Regular', 1)
        self.assertEqual(sum(len(p.interiors) for p in parser._polygons_of(geo)), 3)

    def test_unsupported_stroke_alphabet_is_not_silently_substituted(self):
        with self.assertRaisesRegex(PcbDocParseError, 'stroke.*alphabet'):
            self.parser({'Texts6': text_record('O', alphabet=7)})

    def test_default_copper_strokes_do_not_call_filled_font_renderer(self):
        with patch.object(PcbDocParser, '_font_geometry', side_effect=AssertionError('filled font substitution')):
            parser = self.parser({'Texts6': text_record('CDGND')})
        self.assertIn(1, parser.layers)
        self.assertGreater(len(parser.layers[1]['follow_geometry']), 4)
        self.assertTrue(any('extrapolat' in warning.lower() for warning in parser.warnings))

    def test_stroke_width_counters_and_variable_spacing(self):
        parser = self.parser()
        solid, follows = parser._stroke_geometry('OB', 1, 0.1)
        self.assertEqual(sum(len(p.interiors) for p in parser._polygons_of(solid)), 3)
        h, _ = parser._stroke_geometry('H', 1, 0.1)
        self.assertAlmostEqual(h.bounds[2] - h.bounds[0], 2 / 3 + 0.1)
        self.assertAlmostEqual(h.bounds[3] - h.bounds[1], 1.1)
        _, digits = parser._stroke_geometry('11', 1, 0.1)
        self.assertAlmostEqual(digits[1].bounds[0] - digits[0].bounds[0], 2 / 3)
        self.assertGreater(len(follows), 1)

    def test_stroke_text_rotation_mirroring_and_inverse_counters(self):
        parser = self.parser({'Texts6': text_record('R1', angle=90, mirrored=True)})
        original, _ = PcbDocParser()._stroke_geometry('R1', raw(1) * UNIT, raw(0.1) * UNIT)
        from shapely.affinity import scale
        expected = translate(rotate(scale(original, xfact=-1, yfact=1, origin=(0, 0)), 90, origin=(0, 0)),
                             xoff=raw(1) * UNIT, yoff=raw(2) * UNIT)
        actual = unary_union(parser.layers[1]['solid_geometry'])
        self.assertLess(actual.symmetric_difference(expected).area, 1e-10)
        inverted = self.parser({'Texts6': text_record('O', inverted=True, frame=(1, 1.2))})
        solid = unary_union(inverted.layers[1]['solid_geometry'])
        self.assertFalse(solid.contains(Point(1, 2.5)))
        self.assertTrue(solid.contains(Point(1 + 1 / 3, 2.5)))
        self.assertTrue(solid.contains(Point(1.9, 3.1)))

    def test_reconstructed_strokes_match_original_gto_not_filled_glyphs(self):
        import olefile
        from appParsers.ParsePcbDoc import _Reader

        fixture = Path(__file__).parent / 'fixtures' / 'altium'
        references, quantum = reference_linear_strokes(fixture / 'Gerber' / 'I2C_level_shifter.GTO', 0.127)
        parser = PcbDocParser().parse_file(fixture / 'I2C_level_shifter.PcbDoc')
        with olefile.OleFileIO(str(fixture / 'I2C_level_shifter.PcbDoc')) as container:
            reader = _Reader(container.openstream('Texts6/Data').read(), 'Texts6')
        checked = set()
        while reader.remaining():
            reader.take(1)
            data = reader.block()
            text = reader.block()[1:].decode('latin1')
            if data[0] != 33 or text not in ('H1', 'H2', 'R1', 'R2', 'R3', 'R4', 'T1', 'T2', '3.3V'):
                continue
            height = struct.unpack_from('<i', data, 21)[0] * UNIT
            width = struct.unpack_from('<i', data, 36)[0] * UNIT
            position = parser._xy(*struct.unpack_from('<ii', data, 13))
            angle = struct.unpack_from('<d', data, 27)[0]
            _, follows = parser._stroke_geometry(text, height, width)
            expected = unary_union([translate(rotate(line, angle, origin=(0, 0)),
                                              xoff=position[0], yoff=position[1]) for line in follows])
            window = box(*expected.bounds).buffer(quantum * (len(text) + 1))
            original = unary_union([line for line in references if window.covers(line)])
            self.assertFalse(original.is_empty, text)
            # Altium's export accumulates integer coordinate quantization per glyph.
            self.assertLessEqual(expected.hausdorff_distance(original), quantum * (len(text) + 1), text)
            checked.add(text)
        self.assertEqual(checked, {'H1', 'H2', 'R1', 'R2', 'R3', 'R4', 'T1', 'T2', '3.3V'})

    def test_connected_plane_without_net_assignment_fails_closed(self):
        record = bytearray(pad())
        offset = 1
        for _ in range(4):
            offset += 4 + struct.unpack_from('<I', record, offset)[0]
        struct.pack_into('<H', record, offset + 4 + 3, 0)
        rule = b'\0\0' + properties({'RULEKIND': 'PlaneClearance', 'CLEARANCE': '0.2mm'})
        with self.assertRaisesRegex(PcbDocParseError, 'plane.*net assignment'):
            self.parser({'Pads6': bytes(record), 'Rules6': rule, 'Nets6': properties({'NAME': 'GND'})},
                        stack=(1, 39, 32))

    def test_unhandled_text_border_flags_and_unknown_glyphs_fail_closed(self):
        record = text_record('R1')
        for flag in (230, 231):
            data = bytearray(record[5:142]) + bytearray(103)
            data[flag] = 1
            with self.subTest(flag=flag):
                with self.assertRaisesRegex(PcbDocParseError, 'text.*border'):
                    self.parser({'Texts6': b'\x05' + block(data) + record[142:]})
        with self.assertRaisesRegex(PcbDocParseError, 'stroke glyph'):
            self.parser({'Texts6': text_record('a')})

    def test_missing_font_names_and_invalid_net_references_are_not_guessed(self):
        record = text_record('O')
        data = bytearray(record[5:142])
        data[43] = 1
        with self.assertRaisesRegex(PcbDocParseError, 'source font name'):
            self.parser({'Texts6': b'\x05' + block(data) + record[142:]})
        record = bytearray(pad())
        offset = 1
        for _ in range(4):
            offset += 4 + struct.unpack_from('<I', record, offset)[0]
        struct.pack_into('<H', record, offset + 4 + 3, 4)
        with self.assertRaisesRegex(PcbDocParseError, 'net reference'):
            self.parser({'Pads6': bytes(record)})

    def test_board_stack_region_is_not_a_board_cutout(self):
        parser = self.parser({'BoardRegions': region(list(box(0, 0, 10, 10).exterior.coords),
                                                    layer=56, cutout=True)})
        self.assertAlmostEqual(parser.board_outline.area, 100)
        with self.assertRaises(PcbDocParseError):
            self.parser({'BoardRegions': region(list(box(0, 0, 5, 5).exterior.coords),
                                                layer=56, cutout=True)})

    def test_dimension_annotations_render_on_native_layer(self):
        dim = {'DIMENSIONKIND': 1, 'LAYER': 'MECHANICAL1', 'ANGLE': 0,
               'X1': '0mm', 'Y1': '-2mm', 'REFERENCES_COUNT': 2,
               'REFERENCE0POINTX': '0mm', 'REFERENCE0POINTY': '0mm',
               'REFERENCE1POINTX': '10mm', 'REFERENCE1POINTY': '0mm',
               'LINEWIDTH': '0.1mm', 'TEXTHEIGHT': '1mm', 'TEXT1X': '4mm', 'TEXT1Y': '-3mm',
               'TEXT1ANGLE': 0, 'TEXTDIMENSIONUNIT': 'Millimeters', 'TEXTPRECISION': 2,
               'FONTNAME': 'Arial', 'USETTFONTS': 'TRUE', 'ARROWSIZE': '0.3mm',
               'ARROWLENGTH': '0.5mm', 'EXTENSIONPICKGAP': '0.1mm', 'EXTENSIONOFFSET': '0.2mm'}
        parser = self.parser({'Dimensions6': b'\x01\0' + properties(dim)})
        self.assertIn(57, parser.layers)
        self.assertGreater(len(parser.layers[57]['follow_geometry']), 2)

    def test_hidden_component_comments_do_not_create_artwork(self):
        data = bytearray(123)
        data[0] = 1
        struct.pack_into('<H', data, 7, 0)
        struct.pack_into('<i', data, 21, raw(1))
        data[40] = 1
        data[43] = 1
        data[46:56] = 'Arial'.encode('utf-16le')
        parser = self.parser({'Texts6': b'\x05' + block(data) + block(b'\x01O'),
                              'Components6': properties({'SOURCEDESIGNATOR': 'R1', 'COMMENTON': 'FALSE'})})
        self.assertEqual(parser.layers, {})

    def test_blind_via_uses_stack_span_and_only_reached_mask_side(self):
        data = bytearray(203)
        struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
        struct.pack_into('<iiii', data, 13, raw(4), raw(4), raw(2), raw(0.6))
        data[29:31] = bytes((2, 32))
        data[66] = 2
        struct.pack_into('<i', data, 54, raw(0.1))
        parser = self.parser({'Vias6': b'\x03' + block(data)}, stack=(1, 2, 32))
        self.assertEqual(set(parser.layers), {2, 32, 38})
        self.assertEqual(set(parser.drill_groups), {'PTH_2-32'})
        self.assertEqual(len(parser.drill_groups['PTH_2-32'][1]['drills']), 1)

    def test_fill_rotation_and_extended_mechanical_layer(self):
        data = bytearray(46)
        data[0] = 57
        struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
        struct.pack_into('<iiii', data, 13, raw(2), raw(2), raw(4), raw(3))
        struct.pack_into('<d', data, 29, 90)
        struct.pack_into('<I', data, 42, 0x01020011)
        parser = self.parser({'Fills6': b'\x06' + block(data)})
        self.assertEqual(set(parser.layers), {0x01020011})
        geo = unary_union(parser.layers[0x01020011]['solid_geometry'])
        expected_area = (raw(4) - raw(2)) * (raw(3) - raw(2)) * UNIT ** 2
        self.assertAlmostEqual(geo.area, expected_area, places=10)
        self.assertAlmostEqual(geo.bounds[2] - geo.bounds[0], 1, places=5)

    def test_extended_mask_override_applies_to_pad(self):
        override = properties({'TYPE': 'Mask', 'PRIMITIVEOBJECTID': 'Pad', 'PRIMITIVEINDEX': 0,
            'SOLDERMASKEXPANSIONMODE': 'Manual', 'SOLDERMASKEXPANSION_MANUAL': '0.3mm',
            'PASTEMASKEXPANSIONMODE': 'None'})
        parser = self.parser({'Pads6': pad(layer=1, shape=2, hole=0),
                              'ExtendedPrimitiveInformation': override})
        opening = unary_union(parser.layers[37]['solid_geometry'])
        self.assertAlmostEqual(opening.bounds[0], 3.2, delta=0.00001)

    def test_missing_plane_clearance_rule_does_not_guess_clearance(self):
        with self.assertRaisesRegex(PcbDocParseError, 'PlaneClearance'):
            self.parser({'Pads6': pad()}, stack=(1, 39, 32))

    def test_named_layer_scope_matches_board_layer_names(self):
        parser = self.parser()
        context = {'kind': 'Pad', 'multilayer': False, 'net': '', 'component': '', 'layer': 1}
        self.assertTrue(parser._scope("OnLayer('Top Layer')", context))

    def test_unconnected_plane_pad_uses_layer_specific_stack_geometry(self):
        # A Top/Middle/Bottom pad has a smaller inner land. The plane clearance
        # follows that land, not the top pad nor an inferred drill circle.
        record = bytearray(pad(mode=1, size=(3, 3)))
        offset = 1
        for _ in range(4):
            offset += 4 + struct.unpack_from('<I', record, offset)[0]
        struct.pack_into('<ii', record, offset + 4 + 29, raw(1.2), raw(1.2))
        rule = b'\0\0' + properties({'RULEKIND': 'PlaneClearance', 'CLEARANCE': '0.2mm'})
        parser = self.parser({'Pads6': bytes(record), 'Rules6': rule}, stack=(1, 39, 32))
        copper = unary_union(parser.layers[39]['solid_geometry'])
        self.assertFalse(copper.contains(Point(5.7, 5)))
        self.assertTrue(copper.contains(Point(6, 5)))

    def test_full_stack_plane_uses_nominal_middle_land(self):
        rule = b'\0\0' + properties({'RULEKIND': 'PlaneClearance', 'CLEARANCE': '0.2mm'})
        parser = self.parser({'Pads6': pad(mode=2, size=(1.2, 1.2)), 'Rules6': rule}, stack=(1, 39, 32))
        copper = unary_union(parser.layers[39]['solid_geometry'])
        self.assertFalse(copper.contains(Point(5.7, 5)))
        self.assertTrue(copper.contains(Point(6, 5)))

    def test_non_finite_hole_rotation_is_malformed(self):
        record = bytearray(pad(slot=3))
        offset = 1
        for _ in range(4):
            offset += 4 + struct.unpack_from('<I', record, offset)[0]
        struct.pack_into('<d', record, offset + 4 + 106, float('nan'))
        with self.assertRaises(PcbDocParseError):
            self.parser({'Pads6': bytes(record)})

    def test_top_middle_bottom_via_uses_middle_land_for_all_internal_layers(self):
        data = bytearray(203)
        struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
        struct.pack_into('<iiii', data, 13, raw(4), raw(4), raw(2), raw(0.6))
        data[29:31] = bytes((2, 3))
        data[74] = 1
        struct.pack_into('<i', data, 75 + 4, raw(1.2))
        struct.pack_into('<i', data, 75 + 8, raw(2))
        parser = self.parser({'Vias6': b'\x03' + block(data)}, stack=(1, 2, 3, 32))
        first = unary_union(parser.layers[2]['solid_geometry'])
        second = unary_union(parser.layers[3]['solid_geometry'])
        self.assertAlmostEqual(first.area, second.area, places=10)

    def test_manual_via_mask_from_hole_is_tented_when_inside_land(self):
        data = bytearray(203)
        struct.pack_into('<HHH', data, 3, 65535, 65535, 65535)
        struct.pack_into('<iiii', data, 13, raw(4), raw(4), raw(2), raw(0.6))
        data[29:31] = bytes((1, 32))
        data[66] = 2
        data[67] = 1
        struct.pack_into('<i', data, 54, raw(0.1))
        parser = self.parser({'Vias6': b'\x03' + block(data)})
        self.assertEqual(set(parser.layers), {1, 32})

    def test_wide_string_header_counts_and_duplicate_indices_are_validated(self):
        entry = struct.pack('<I', 7) + block('O\0'.encode('utf-16le'))
        parser = PcbDocParser()._parse_streams({'Board6': board(), 'WideStrings6': entry}, {'WideStrings6': 1})
        self.assertEqual(parser.record_counts['WideStrings6'], 1)
        self.assertEqual(parser._wide_strings[7], 'O')
        with self.assertRaises(PcbDocParseError):
            self.parser({'WideStrings6': entry * 2})

    def test_unresolved_split_plane_net_domains_fail_closed(self):
        definition = properties({'LAYER': 'PLANE1', 'HATCHSTYLE': 'Solid', 'NET': 0})
        saved = region(list(box(2, 2, 7, 7).exterior.coords), layer=39, polygon=0)
        with self.assertRaisesRegex(PcbDocParseError, 'plane.*net'):
            self.parser({'Polygons6': definition, 'Regions6': saved}, stack=(1, 39, 32))

    def test_fixture_native_content_origin_and_drills(self):
        self.assertIsNotNone(PcbDocParser, 'native PcbDoc parser is not implemented')
        filename = Path(__file__).parent / 'fixtures' / 'altium' / 'I2C_level_shifter.PcbDoc'
        parser = PcbDocParser().parse_file(filename)
        self.assertEqual(parser.record_counts['Tracks6'], 284)
        self.assertEqual(parser.record_counts['Pads6'], 34)
        self.assertEqual(parser.record_counts['Texts6'], 32)
        self.assertEqual(parser.record_counts['Regions6'], 33)
        self.assertAlmostEqual(parser.origin[0], 57.15)
        self.assertAlmostEqual(parser.origin[1], 71.374)
        for actual, expected in zip(parser.board_outline.bounds, (0, 0, 20.32, 12.446)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(set(parser.layers), {1, 32, 33, 34, 35, 36, 37, 38, 56, 57, 69, 70, 71})
        self.assertGreater(unary_union(parser.layers[32]['solid_geometry']).area, 35)
        self.assertGreater(len(parser.layers[32]['follow_geometry']), 10)
        self.assertGreater(sum(len(p.interiors) for p in parser.layers[1]['solid_geometry']), 0)
        self.assertEqual(sum(len(t['drills']) for g in parser.drill_groups.values() for t in g.values()), 8)
        for layer in parser.layers.values():
            self.assertTrue(all(isinstance(p, Polygon) and p.is_valid for p in layer['solid_geometry']))


if __name__ == '__main__':
    unittest.main()
