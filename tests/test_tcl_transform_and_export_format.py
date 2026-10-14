"""Regression tests for the shared bounds in the Tcl mirror / skew commands and the Gerber export number format.

- TclCommandMirror and TclCommandSkew compute the bounds of all named objects 1 time. The bounds refresh of each
  object must not overwrite them, else the next object uses another reference point.
- TclCommandMirror with neither -box nor -origin uses the center of the bounds of all objects.
- GerberObject.export_gerber: with trailing zeros kept ('T') the sign of a negative coordinate stays in front of the
  zero padding; with leading zeros omitted ('L') no padding is added.
"""

from unittest.mock import Mock, patch

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
