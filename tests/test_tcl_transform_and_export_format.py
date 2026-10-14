"""Regression tests for the shared bounds in the Tcl mirror / skew commands and the Gerber export number format.

- TclCommandMirror and TclCommandSkew compute the bounds of all named objects 1 time. The bounds refresh of each
  object must not overwrite them, else the next object uses another reference point.
- TclCommandMirror with neither -box nor -origin uses the center of the bounds of all objects.
- GerberObject.export_gerber: with trailing zeros kept ('T') the sign of a negative coordinate stays in front of the
  zero padding; with leading zeros omitted ('L') no padding is added.
"""

from unittest.mock import Mock, patch

import pytest

import test_unify_characterization as char
from appObjects.GerberObject import GerberObject
from tclCommands.TclCommandMirror import TclCommandMirror
from tclCommands.TclCommandSkew import TclCommandSkew
from test_unify_regressions_0b import assert_xy_close, drill_xy, make_app, make_excellon


def _run(cmd_cls, objs, *argv):
    app = make_app()
    app.collection.get_by_name.side_effect = lambda n: objs.get(n)
    cmd = cmd_cls(app)
    args, unnamed = cmd.check_args(tuple(objs) + argv)
    return cmd.execute(args, unnamed)


def _two_excellons():
    a = make_excellon(default_data={}, tooldia=0.2, drills=((0.0, 0.0),), slots=())
    b = make_excellon(default_data={}, tooldia=0.2, drills=((4.0, 2.0),), slots=())
    return a, b


def _expected_mirror(drills, internal_axis, point):
    ref = make_excellon(default_data={}, tooldia=0.2, drills=drills, slots=())
    ref.mirror(internal_axis, point)
    return drill_xy(ref)


class TestTclMirror:
    def test_two_objects_share_one_center(self):
        a, b = _two_excellons()
        _run(TclCommandMirror, {'a': a, 'b': b}, '-axis', 'X', '-origin', 'center')
        # the Tcl command swaps the axis name to work like the GUI: -axis X is mirror('Y', ...)
        assert_xy_close(drill_xy(a), _expected_mirror(((0.0, 0.0),), 'Y', [2.0, 1.0]))
        assert_xy_close(drill_xy(b), _expected_mirror(((4.0, 2.0),), 'Y', [2.0, 1.0]))

    def test_no_box_and_no_origin_uses_the_center_of_all_objects(self):
        a, b = _two_excellons()
        _run(TclCommandMirror, {'a': a, 'b': b}, '-axis', 'Y')
        assert_xy_close(drill_xy(a), _expected_mirror(((0.0, 0.0),), 'X', [2.0, 1.0]))
        assert_xy_close(drill_xy(b), _expected_mirror(((4.0, 2.0),), 'X', [2.0, 1.0]))

    def test_box_path_refreshes_the_bounds_options(self):
        a, _ = _two_excellons()
        box = make_excellon(default_data={}, tooldia=0.2, drills=((10.0, 10.0),), slots=())
        app_objs = {'a': a}

        app = make_app()
        app.collection.get_by_name.side_effect = lambda n: {'a': a, 'box': box}.get(n)
        cmd = TclCommandMirror(app)
        args, unnamed = cmd.check_args(tuple(app_objs) + ('-axis', 'X', '-box', 'box'))
        cmd.execute(args, unnamed)

        assert_xy_close(drill_xy(a), _expected_mirror(((0.0, 0.0),), 'Y', [10.0, 10.0]))
        xmin, ymin, xmax, ymax = a.bounds()
        assert a.obj_options['xmin'] == xmin and a.obj_options['ymin'] == ymin
        assert a.obj_options['xmax'] == xmax and a.obj_options['ymax'] == ymax


class TestTclSkew:
    def test_two_objects_share_one_reference_point(self):
        a, b = _two_excellons()
        _run(TclCommandSkew, {'a': a, 'b': b}, '-x', '10', '-y', '0', '-origin', 'center')

        def expected(drills):
            ref = make_excellon(default_data={}, tooldia=0.2, drills=drills, slots=())
            ref.skew(10.0, 0.0, point=(2.0, 1.0))
            return drill_xy(ref)

        assert_xy_close(drill_xy(a), expected(((0.0, 0.0),)), tol=1e-6)
        assert_xy_close(drill_xy(b), expected(((4.0, 2.0),)), tol=1e-6)


GERBER_NEGATIVE_FLASH = ["%FSLAX24Y24*%", "%MOMM*%", "%ADD10C,1.0*%", "D10*", "X15000Y-22500D03*", "M02*"]


def _exported_body(g_zeros):
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        obj = GerberObject('g', make_app())
    obj.parse_lines(GERBER_NEGATIVE_FLASH)
    return obj.export_gerber(whole=2, fract=4, g_zeros=g_zeros)


class TestGerberExportNumberFormat:
    def test_trailing_zeros_keep_the_sign_in_front_of_the_padding(self):
        assert 'X015000Y-022500D03' in _exported_body('T')

    def test_leading_zeros_omitted_adds_no_padding(self):
        assert 'X15000Y-22500D03' in _exported_body('L')

    @pytest.mark.parametrize('g_zeros', ['L', 'T'])
    @pytest.mark.parametrize('whole, fract', [(2, 4), (3, 6), (4, 6)])
    def test_real_export_round_trips_negative_and_small_coordinates(self, g_zeros, whole, fract, tmp_path):
        """appIO.export_gerber -> parser: the Gerber spec 4.2.2 (L) and 8.2.1.1 (T, deprecated) formats."""
        app = char.make_app()
        app.options.update({'gerber_exp_zeros': g_zeros, 'gerber_exp_integer': whole, 'gerber_exp_decimals': fract})
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            grb = char.make_gerber(app, char.NATIVE_GBR)
            # negative coordinates on both axes and a fraction with leading decimal zeros
            grb.offset((-20.05, -20.003))
            text = char.export_gerber_text(app, grb)
            assert '%%FS%sAX%d%dY%d%d*%%' % (g_zeros, whole, fract, whole, fract) in text
            again = char.reparse_gerber(app, text, tmp_path)
        assert again.bounds()[0] < 0 and again.bounds()[1] < 0
        char.assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=10 ** -fract * 50,
                               msg='%s %d.%d' % (g_zeros, whole, fract))


def _flat(items):
    return [c for item in items for c in item]


class TestExcellonExportNumberFormat:
    """appIO.export_excellon -> parser, without the decimal point (Excellon CNC-7 rules):
    LZ: the leading zeros are written, the reader adds the trailing zeros, so the whole part has 'whole' digits;
    TZ: the trailing zeros are written, the reader counts the decimals from the right, so no padding."""

    @pytest.mark.parametrize('slot_type', ['routing', 'drilling'])
    @pytest.mark.parametrize('e_zeros', ['LZ', 'TZ'])
    @pytest.mark.parametrize('whole, fract', [(3, 3), (2, 4)])
    def test_real_export_round_trips_negative_and_small_coordinates(self, e_zeros, whole, fract, slot_type,
                                                                     tmp_path):
        app = char.make_app()
        app.options.update({'excellon_exp_format': 'ndec', 'excellon_exp_zeros': e_zeros,
                            'excellon_exp_integer': whole, 'excellon_exp_decimals': fract,
                            'excellon_exp_units': 'METRIC', 'excellon_exp_slot_type': slot_type})
        with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
            exc = char.make_excellon(app)
            # negative coordinates on both axes, values below 1 and a fraction with leading decimal zeros
            exc.offset((-9.95, -10.05))
            text = char.export_excellon_text(app, exc)
            assert 'METRIC,%s' % e_zeros in text
            again = char.reparse_excellon(app, text, tmp_path)

        tol = 10 ** -fract
        assert sorted(again.tools) == sorted(exc.tools)
        for t, tool in exc.tools.items():
            msg = '%s %d.%d %s T%s' % (e_zeros, whole, fract, slot_type, t)
            # compare the numbers directly (a drill has zero area); the counts must match first
            assert len(again.tools[t]['drills']) == len(tool['drills']), msg
            assert _flat(char.pts(again.tools[t]['drills'])) == pytest.approx(_flat(char.pts(tool['drills'])),
                                                                              abs=tol), msg
            assert len(again.tools[t].get('slots', [])) == len(tool['slots']), msg
            assert _flat(char.slot_flat(again.tools[t].get('slots', []))) == \
                pytest.approx(_flat(char.slot_flat(tool['slots'])), abs=tol), msg
