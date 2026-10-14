"""Regression tests for Stage 0B of the Gerber / Excellon unification plan.

Each test class specifies the CORRECTED behavior of one Stage 0B item. The class carries a strict xfail marker
that names its owning sub-item. The fix of that sub-item removes the marker of that class only.

``raises=AssertionError`` is deliberate: a setup error (AttributeError, ImportError, ...) is not an expected
failure and shows up as a real FAILED, so the marker cannot hide a broken test.

``TestGuardsAlreadyPassing_0B`` holds checks of Stage 0B behavior that is already right today. They have no marker.
They must keep passing while the 0B fixes land (for example the signed Excellon scale, or the bounds center that
TclCommandScale shares between several objects).

Everything is headless: no GUI is built, ShapeCollection is patched where an object is created.
"""

import inspect
import math
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, NonCallableMock, patch

import pytest
from shapely.geometry import LineString, Point, box

from appCommon.Common import LoudDict
from appHandlers.appEdit import appEditor
from appHandlers.appIO import appIO
from appObjects.CNCJobObject import CNCJobObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject
from appObjects.GeometryObject import GeometryObject
from appParsers.ParseExcellon import Excellon
from appParsers.ParseGerber import Gerber
from defaults import AppDefaults
from tclCommands.TclCommandBuffer import TclCommandBuffer
from tclCommands.TclCommandScale import TclCommandScale


# ---------------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------------

def make_app(**option_overrides):
    """A minimal headless app: enough for the camlib classes, the FlatCAMObj classes and the Tcl commands."""
    defaults = AppDefaults()
    options = dict(defaults.defaults)
    options.update(option_overrides)

    plotcanvas = Mock()
    # count the temp_shapes that Geometry.__init__ creates
    plotcanvas.new_shape_collection.side_effect = lambda **kwargs: Mock()
    return SimpleNamespace(
        log=Mock(), inform=Mock(), options=options, defaults=defaults, app_units='MM', decimals=4,
        use_3d_engine=True, plotcanvas=plotcanvas, pool=Mock(), abort_flag=False,
        proc_container=MagicMock(), shell=Mock(), collection=Mock(), call_source='app',
        preprocessors=defaultdict(Mock), splash=None, worker_task=Mock(), app_obj=Mock(),
    )


@pytest.fixture
def no_shape_collection():
    """FlatCAMObj.__init__ builds a VisPy ShapeCollection; replace it."""
    with patch('appObjects.AppObjectTemplate.ShapeCollection', Mock()):
        yield


def make_excellon(app=None, tooldia=1.0, drills=((0.0, 0.0), (4.0, 0.0)), slots=(((0.0, 2.0), (4.0, 2.0)),),
                  default_data=None, units='MM'):
    """A plain camlib Excellon with 1 tool. ``default_data`` mimics ExcellonObject (which always has it)."""
    app = app or make_app()
    ex = Excellon(app)
    ex.units = units
    if default_data is not None:
        ex.default_data = default_data
    ex.tools = {1: {
        'tooldia': tooldia,
        'drills': [Point(p) for p in drills],
        'slots': [(Point(a), Point(b)) for a, b in slots],
        'solid_geometry': [],
        'data': {},
    }}
    ex.create_geometry()
    ex.obj_options = {'name': 'ex'}
    ex.kind = 'excellon'
    return ex


def drill_xy(ex, tool=1):
    return [(p.x, p.y) for p in ex.tools[tool]['drills']]


def slot_xy(ex, tool=1):
    return [((a.x, a.y), (b.x, b.y)) for a, b in ex.tools[tool]['slots']]


def assert_xy_close(actual, expected, tol=1e-9):
    assert len(actual) == len(expected), "%r != %r" % (actual, expected)
    for (ax, ay), (ex_, ey) in zip(actual, expected):
        assert math.isclose(ax, ex_, abs_tol=tol) and math.isclose(ay, ey, abs_tol=tol), \
            "%r != %r" % (actual, expected)


GERBER_C_FLASH = ["%FSLAX24Y24*%", "%MOMM*%", "%ADD10C,1.0*%", "D10*", "X10000Y10000D03*", "M02*"]
GERBER_P_FLASH = ["%FSLAX24Y24*%", "%MOMM*%", "%ADD11P,1.0X6*%", "D11*", "X10000Y10000D03*", "M02*"]


def make_gerber(lines, app=None):
    g = Gerber(app or make_app())
    g.parse_lines(lines)
    return g


# ---------------------------------------------------------------------------------------------------------------------
# 0B.1  GerberObject.merge
# ---------------------------------------------------------------------------------------------------------------------

class _BadOptions(dict):
    """obj_options whose 'bad' option raises KeyError on read (merge catches that and must log)."""

    def __getitem__(self, key):
        if key == 'bad':
            raise KeyError(key)
        return dict.__getitem__(self, key)


def _merge_source(name, solid=None, follow=None, tool_id=10, options=None):
    solid = solid if solid is not None else box(0, 0, 1, 1)
    follow = follow if follow is not None else LineString([(0, 0), (1, 1)])
    return SimpleNamespace(
        obj_options=options if options is not None else {'name': name, 'plot': True},
        solid_geometry=[solid],
        follow_geometry=[follow],
        tools={tool_id: {'type': 'C', 'size': 0.2, 'geometry': [{'solid': solid, 'follow': follow}]}},
    )


def _merge_final():
    return SimpleNamespace(obj_options={'name': 'Combo'}, solid_geometry=None, follow_geometry=None, tools=None)


def _call_merge(grb_list, grb_final, log=None):
    """Call merge with the new signature if it exists, else with the old one (app=...)."""
    params = inspect.signature(GerberObject.merge).parameters
    if 'log' in params:
        return GerberObject.merge(grb_list=grb_list, grb_final=grb_final, log=log)
    return GerberObject.merge(grb_list=grb_list, grb_final=grb_final, app=SimpleNamespace(log=log))


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="fixed in Stage 0B.1")
class TestMerge_0B1:
    def test_signature_is_list_final_optional_log(self):
        params = inspect.signature(GerberObject.merge).parameters
        assert list(params) == ['grb_list', 'grb_final', 'log']
        assert params['log'].default is None

    def test_nested_list_input_merges_every_source(self):
        a = _merge_source('a', solid=box(0, 0, 1, 1))
        b = _merge_source('b', solid=box(5, 0, 6, 1), tool_id=11)
        final = _merge_final()
        try:
            _call_merge([[a, b]], final, log=Mock())
        except TypeError as e:
            # the recursion drops a required argument today
            raise AssertionError("merge() of a nested list raised TypeError: %s" % e)
        assert final.solid_geometry.bounds == (0.0, 0.0, 6.0, 1.0)
        assert set(final.tools) == {10, 11}

    def test_recursion_passes_log(self):
        bad = _merge_source('bad', options=_BadOptions({'name': 'bad', 'bad': 1}))
        log = Mock()
        final = _merge_final()
        try:
            _call_merge([[bad]], final, log=log)
        except TypeError as e:
            raise AssertionError("merge() of a nested list raised TypeError: %s" % e)
        assert log.warning.called, "the option-copy failure was not logged through the recursive call"

    def test_tools_are_deep_copied(self):
        src = _merge_source('a')
        final = _merge_final()
        _call_merge([src], final, log=Mock())
        assert 10 in final.tools
        src.tools[10]['size'] = 9.9
        src.tools[10]['geometry'].append({'solid': box(9, 9, 10, 10)})
        assert final.tools[10]['size'] == 0.2
        assert len(final.tools[10]['geometry']) == 1

    def test_follow_geometry_comes_from_source_follow(self):
        line = LineString([(0, 0), (3, 3)])
        src = _merge_source('a', solid=box(0, 0, 5, 5), follow=line)
        final = _merge_final()
        _call_merge([src], final, log=Mock())
        assert final.follow_geometry.geom_type == 'LineString', final.follow_geometry.geom_type
        assert final.follow_geometry.equals(line)
        assert final.solid_geometry.geom_type == 'Polygon'


# ---------------------------------------------------------------------------------------------------------------------
# 0B.2  double init
# ---------------------------------------------------------------------------------------------------------------------

def _temp_shapes_count(app):
    return app.plotcanvas.new_shape_collection.call_count


class TestDoubleInit_0B2:
    def test_excellon_object_keeps_circle_steps_preference(self, no_shape_collection):
        app = make_app(excellon_circle_steps=21)
        obj = ExcellonObject('exc', app)
        assert obj.excellon_circle_steps == 21
        assert obj.geo_steps_per_circle == 21

    @pytest.mark.parametrize('cls', [ExcellonObject, GerberObject, GeometryObject, CNCJobObject],
                             ids=lambda c: c.__name__)
    def test_camlib_init_runs_once(self, cls, no_shape_collection):
        app = make_app()
        cls('obj', app)
        # Geometry.__init__ creates 1 temp_shapes collection per run
        assert _temp_shapes_count(app) == 1

    def test_cncjob_object_keeps_constructor_state(self, no_shape_collection):
        app = make_app()
        obj = CNCJobObject('job', app, units='mm', z_cut=-1.5, feedrate=7.0, tooldia=0.5)
        assert obj.feedrate == 7.0
        assert obj.z_cut == -1.5
        assert obj.tooldia == 0.5


# ---------------------------------------------------------------------------------------------------------------------
# 0B.3  Excellon scale and unit conversion
# ---------------------------------------------------------------------------------------------------------------------

class TestExcellonScaleUnits_0B3:
    def test_uniform_scale_doubles_tooldia_and_geometry(self):
        ex = make_excellon(default_data={})
        ex.scale(2.0, 2.0, point=(0, 0))
        assert ex.tools[1]['tooldia'] == pytest.approx(2.0)
        minx, _, maxx, _ = ex.tools[1]['solid_geometry'][0].bounds
        assert maxx - minx == pytest.approx(2.0, abs=1e-6)

    def test_negative_uniform_scale_uses_abs_for_tooldia(self):
        ex = make_excellon(default_data={})
        ex.scale(-2.0, -2.0, point=(0, 0))
        assert ex.tools[1]['tooldia'] == pytest.approx(2.0)

    def test_gerber_scale_keeps_p_aperture_nvertices(self):
        g = make_gerber(GERBER_P_FLASH)
        assert g.tools[11]['nVertices'] == 6
        g.scale(2.0)
        assert g.tools[11]['nVertices'] == 6
        assert g.tools[11]['diam'] == pytest.approx(2.0)


# ---------------------------------------------------------------------------------------------------------------------
# 0B.4  other local transform bugs
# ---------------------------------------------------------------------------------------------------------------------

def _tcl_buffer(ex, *argv):
    app = make_app()
    app.collection.get_by_name.return_value = ex
    cmd = TclCommandBuffer(app)
    args, unnamed = cmd.check_args(('ex',) + argv)
    cmd.execute(args, unnamed)


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="fixed in Stage 0B.4")
class TestTransformBugs_0B4:
    def test_excellon_rotate_without_point_uses_bounds_center(self):
        ex = make_excellon(default_data={})
        minx, miny, maxx, maxy = ex.bounds()
        cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
        old_drills = drill_xy(ex)
        old_slots = slot_xy(ex)

        def rot90(p):
            # 90 degrees counter-clockwise about (cx, cy)
            return cx - (p[1] - cy), cy + (p[0] - cx)

        ex.rotate(90)
        assert_xy_close(drill_xy(ex), [rot90(p) for p in old_drills], tol=1e-6)
        assert_xy_close([s[0] for s in slot_xy(ex)], [rot90(s[0]) for s in old_slots], tol=1e-6)
        assert_xy_close([s[1] for s in slot_xy(ex)], [rot90(s[1]) for s in old_slots], tol=1e-6)

    def test_excellon_buffer_factor_false_is_distance_mode(self):
        ex = make_excellon(default_data={}, tooldia=1.0)
        ex.buffer(0.25, 'round', False)
        assert ex.tools[1]['tooldia'] == pytest.approx(1.5)

    def test_excellon_multiplicative_buffer_multiplies_diameter(self):
        ex = make_excellon(default_data={}, tooldia=1.0)
        ex.buffer(2, 'round', True)
        assert ex.tools[1]['tooldia'] == pytest.approx(2.0)

    def test_gerber_buffer_factor_false_is_distance_mode(self):
        g = make_gerber(GERBER_C_FLASH)
        g.buffer(0.1, 'round', False)
        assert g.tools[10]['size'] == pytest.approx(1.2)

    def test_gerber_distance_buffer_keeps_p_aperture_nvertices(self):
        g = make_gerber(GERBER_P_FLASH)
        g.buffer(0.1, 'round', None)
        assert g.tools[11]['nVertices'] == 6

    def test_excellon_create_geometry_keeps_existing_tool_data(self):
        ex = make_excellon(default_data={'from_default': 1})
        ex.tools[1]['data'] = {'kept': 5}
        ex.create_geometry()
        assert ex.tools[1]['data'] == {'kept': 5}

    def test_excellon_create_geometry_works_without_default_data_attribute(self):
        ex = make_excellon()
        assert not hasattr(ex, 'default_data')
        ex.tools[1]['data'] = {'kept': 5}
        assert ex.create_geometry() != 'fail'
        assert ex.tools[1]['data'] == {'kept': 5}
        assert len(ex.solid_geometry) == 3

    def test_tcl_buffer_factor_true_multiplies_diameter(self):
        ex = make_excellon(default_data={}, tooldia=1.0)
        _tcl_buffer(ex, '-dist', '2', '-factor', 'True')
        assert ex.tools[1]['tooldia'] == pytest.approx(2.0)

    def test_tcl_buffer_factor_false_is_distance_mode(self):
        ex = make_excellon(default_data={}, tooldia=1.0)
        _tcl_buffer(ex, '-dist', '0.25', '-factor', 'False')
        assert ex.tools[1]['tooldia'] == pytest.approx(1.5)


# ---------------------------------------------------------------------------------------------------------------------
# 0B.5  TclCommandScale
# ---------------------------------------------------------------------------------------------------------------------

def _tcl_scale(objs, *argv):
    """objs: dict name -> object. Runs ``scale <names> <argv>``."""
    app = make_app()
    app.collection.get_by_name.side_effect = lambda n: objs.get(n)
    cmd = TclCommandScale(app)
    args, unnamed = cmd.check_args(tuple(objs) + argv)
    return cmd.execute(args, unnamed)


def _scale_target():
    return make_excellon(default_data={}, tooldia=0.2, drills=((1.0, 1.0), (3.0, 2.0)), slots=())


class TestTclScale_0B5:
    def test_x_alone_scales_x_only(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-x', '2', '-origin', 'origin')
        assert_xy_close(drill_xy(ex), [(2.0, 1.0), (6.0, 2.0)])

    def test_y_alone_scales_y_only(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-y', '3', '-origin', 'origin')
        assert_xy_close(drill_xy(ex), [(1.0, 3.0), (3.0, 6.0)])

    def test_x_and_y_scale_both_axes(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-x', '2', '-y', '3', '-origin', 'origin')
        assert_xy_close(drill_xy(ex), [(2.0, 3.0), (6.0, 6.0)])

    def test_axis_path_refreshes_bounds_options(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-x', '2', '-y', '2', '-origin', 'origin')
        assert 'xmax' in ex.obj_options
        assert ex.obj_options['xmax'] == pytest.approx(ex.bounds()[2])

    def test_factor_path_refreshes_bounds_options(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-factor', '2', '-origin', 'origin')
        assert 'xmin' in ex.obj_options and 'ymax' in ex.obj_options
        xmin, ymin, xmax, ymax = ex.bounds()
        assert ex.obj_options['xmin'] == pytest.approx(xmin)
        assert ex.obj_options['ymin'] == pytest.approx(ymin)
        assert ex.obj_options['xmax'] == pytest.approx(xmax)
        assert ex.obj_options['ymax'] == pytest.approx(ymax)

    def test_two_objects_axis_path_share_one_center(self):
        # default origin = center of the bounds of ALL named objects, computed once
        a = make_excellon(default_data={}, tooldia=0.2, drills=((0.0, 0.0),), slots=())
        b = make_excellon(default_data={}, tooldia=0.2, drills=((4.0, 0.0),), slots=())
        _tcl_scale({'a': a, 'b': b}, '-x', '2', '-y', '2')
        assert_xy_close(drill_xy(a), [(-2.0, 0.0)])
        assert_xy_close(drill_xy(b), [(6.0, 0.0)])


# ---------------------------------------------------------------------------------------------------------------------
# 0B.6  plot, project load, small bugs
# ---------------------------------------------------------------------------------------------------------------------

def _plot_ready_excellon_object():
    app = make_app()
    obj = ExcellonObject('exc', app)
    ui = Mock()
    ui.multicolored_cb.get_value.return_value = False
    ui.solid_cb.get_value.return_value = True
    ui.plot_cb.get_value.return_value = True
    obj.ui = ui
    obj.tools = {1: {'tooldia': 1.0, 'solid_geometry': [Point(0, 0).buffer(0.5)]}}
    obj.solid_geometry = [Point(0, 0).buffer(0.5)]
    obj.add_shapes_batch = Mock(return_value=[11])
    obj.shapes = Mock(_indexes=[])
    return obj


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="fixed in Stage 0B.6")
class TestSmallBugs_0B6:
    def test_excellon_plot_respects_explicit_visible_false(self, no_shape_collection):
        obj = _plot_ready_excellon_object()
        obj.plot(visible=False)
        assert obj.add_shapes_batch.called
        assert obj.add_shapes_batch.call_args.kwargs['visible'] is False

    def test_excellon_plot_clears_stale_shape_indexes(self, no_shape_collection):
        obj = _plot_ready_excellon_object()
        obj.shape_indexes_dict = {99: [1, 2, 3]}
        obj.plot()
        assert obj.add_shapes_batch.called
        assert obj.shape_indexes_dict == {1: [11]}

    def test_restore_project_objects_sets_change_callback(self):
        app = MagicMock()  # appIO.__init__ reads many app attributes
        app.options = {}
        worker = []
        app.worker_task = Mock()
        app.worker_task.emit.side_effect = worker.append
        app.should_we_save = True
        app.file_opened = Mock()
        app.ui = Mock()
        new_objs = []

        class FakeObj:
            kind = 'geometry'
            tools = {}

            def __init__(self):
                self.obj_options = {'name': 'g', 'plot': True}
                self.changed = []

            def from_dict(self, d):
                pass

            def on_options_change(self, key):
                self.changed.append(key)

        def new_object(kind, name, initialize, plot=True):
            obj = FakeObj()
            new_objs.append(obj)
            return initialize(obj, app)

        app.app_obj = Mock()
        app.app_obj.new_object.side_effect = new_object

        io = appIO(app)
        proj = {'objs': [{'kind': 'geometry', 'obj_options': {'name': 'g', 'plot': True}}], 'options': {}}
        io.restore_project_objects(proj, 'x.FlatPrj', True, False)
        assert len(worker) == 1
        worker[0]['fcn']()

        assert len(new_objs) == 1, "the project object was not recreated"
        obj = new_objs[0]
        assert isinstance(obj.obj_options, LoudDict)
        obj.obj_options['plot'] = False
        assert obj.changed == ['plot'], "LoudDict of the restored object has no change callback"

    def test_convert_any2geo_failure_uses_log_warning(self):
        app = make_app()
        app.splash = None
        app.worker_task = Mock()
        app.app_obj = Mock()
        app.defaults.report_usage = Mock()
        src = SimpleNamespace(kind='excellon', obj_options={'name': 'e'}, tools={}, solid_geometry=[])
        app.collection.get_selected.return_value = [src]

        # a logger that is not callable, like the real logging.Logger
        app_obj_log = NonCallableMock(spec=['debug', 'warning', 'error', 'info'])
        inner_app = SimpleNamespace(log=app_obj_log, inform=Mock())
        app.app_obj.new_object.side_effect = lambda kind, name, init: init(SimpleNamespace(obj_options={}),
                                                                           inner_app)

        appEditor(app).convert_any2geo()
        assert app_obj_log.warning.called


# ---------------------------------------------------------------------------------------------------------------------
# already right today: must keep passing during Stage 0B (no marker)
# ---------------------------------------------------------------------------------------------------------------------

class TestGuardsAlreadyPassing_0B:
    def test_excellon_scale_negative_x_uses_signed_factor(self):
        ex = make_excellon(default_data={}, drills=((2.0, 1.0), (4.0, 3.0)), slots=(((2.0, 5.0), (6.0, 5.0)),))
        ex.scale(-1, 1, point=(1, 1))
        assert_xy_close(drill_xy(ex), [(0.0, 1.0), (-2.0, 3.0)])
        assert_xy_close([s[0] for s in slot_xy(ex)], [(0.0, 5.0)])
        assert_xy_close([s[1] for s in slot_xy(ex)], [(-4.0, 5.0)])

    def test_excellon_scale_negative_both_uses_signed_factors(self):
        ex = make_excellon(default_data={}, drills=((2.0, 1.0), (4.0, 3.0)), slots=(((2.0, 5.0), (6.0, 5.0)),))
        ex.scale(-1, -1, point=(1, 1))
        assert_xy_close(drill_xy(ex), [(0.0, 1.0), (-2.0, -1.0)])
        assert_xy_close([s[0] for s in slot_xy(ex)], [(0.0, -3.0)])
        assert_xy_close([s[1] for s in slot_xy(ex)], [(-4.0, -3.0)])

    def test_excellon_nonuniform_scale_keeps_tooldia(self):
        ex = make_excellon(default_data={})
        ex.scale(2.0, 1.0, point=(0, 0))
        assert ex.tools[1]['tooldia'] == pytest.approx(1.0)
        assert_xy_close(drill_xy(ex), [(0.0, 0.0), (8.0, 0.0)])

    def test_excellon_convert_units_round_trip_scales_once(self):
        ex = make_excellon(default_data={}, units='IN', tooldia=1.0, drills=((1.0, 2.0),),
                           slots=(((0.0, 0.0), (1.0, 0.0)),))
        ex.convert_units('MM')
        assert ex.tools[1]['tooldia'] == pytest.approx(25.4, abs=1e-9)
        assert_xy_close(drill_xy(ex), [(25.4, 50.8)], tol=1e-9)
        assert_xy_close([s[1] for s in slot_xy(ex)], [(25.4, 0.0)], tol=1e-9)
        ex.convert_units('IN')
        assert ex.tools[1]['tooldia'] == pytest.approx(1.0, abs=1e-9)
        assert_xy_close(drill_xy(ex), [(1.0, 2.0)], tol=1e-9)
        assert_xy_close([s[1] for s in slot_xy(ex)], [(1.0, 0.0)], tol=1e-9)

    def test_excellon_buffer_factor_none_is_distance_mode(self):
        ex = make_excellon(default_data={}, tooldia=1.0)
        ex.buffer(0.25, 'round', None)
        assert ex.tools[1]['tooldia'] == pytest.approx(1.5)

    def test_gerber_buffer_factor_none_is_distance_mode(self):
        g = make_gerber(GERBER_C_FLASH)
        g.buffer(0.1, 'round', None)
        assert g.tools[10]['size'] == pytest.approx(1.2)

    def test_gerber_buffer_factor_true_is_multiplicative_mode(self):
        g = make_gerber(GERBER_C_FLASH)
        g.buffer(2, 'round', True)
        assert g.tools[10]['size'] == pytest.approx(2.0)

    def test_tcl_buffer_without_factor_is_distance_mode(self):
        ex = make_excellon(default_data={}, tooldia=1.0)
        _tcl_buffer(ex, '-dist', '0.25')
        assert ex.tools[1]['tooldia'] == pytest.approx(1.5)

    def test_tcl_scale_factor_scales_positions(self):
        ex = _scale_target()
        _tcl_scale({'ex': ex}, '-factor', '2', '-origin', 'origin')
        assert_xy_close(drill_xy(ex), [(2.0, 2.0), (6.0, 4.0)])

    def test_tcl_scale_factor_two_objects_share_one_center(self):
        # Removing the `continue` of the factor path must not recompute the shared center per object.
        a = make_excellon(default_data={}, tooldia=0.2, drills=((0.0, 0.0),), slots=())
        b = make_excellon(default_data={}, tooldia=0.2, drills=((4.0, 0.0),), slots=())
        _tcl_scale({'a': a, 'b': b}, '-factor', '2')
        assert_xy_close(drill_xy(a), [(-2.0, 0.0)])
        assert_xy_close(drill_xy(b), [(6.0, 0.0)])

    def test_excellon_plot_visible_none_uses_ui_value(self, no_shape_collection):
        obj = _plot_ready_excellon_object()
        obj.ui.plot_cb.get_value.return_value = False
        obj.plot(visible=None)
        assert obj.add_shapes_batch.call_args.kwargs['visible'] is False

    def test_geometry_object_keeps_circle_steps_preference(self, no_shape_collection):
        app = make_app(geometry_circle_steps=21)
        obj = GeometryObject('geo', app)
        assert obj.geo_steps_per_circle == 21
