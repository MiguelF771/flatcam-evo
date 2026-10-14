"""Stage 5 item 1 (free parts): plot() reads its state from obj_options, not from UI widgets.

Set the environment variable STAGE5_OLD_CODE=1 to run the batch-equality tests on the code before the change: the
widgets then get real values (Mock widgets) instead of the stub that raises on any access.
"""
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_unify_characterization import make_app, make_excellon, make_gerber, _headless_qt  # noqa: E402,F401

OLD_CODE = os.environ.get('STAGE5_OLD_CODE') == '1'


class RaisingUI:
    """Any access to the UI is a failure: plot() must not need it."""

    def __getattr__(self, name):
        raise AssertionError('plot() touched the UI: %s' % name)


def _widget(value):
    w = Mock()
    w.get_value.return_value = value
    return w


def _ui_for(settings):
    """The UI of the object: a raising stub on the new code, real widget values on the old code."""
    if not OLD_CODE:
        return RaisingUI()
    ui = Mock()
    ui.multicolored_cb = _widget(settings.get('multicolored', False))
    ui.solid_cb = _widget(settings.get('solid', False))
    ui.plot_cb = _widget(settings.get('plot', True))
    ui.follow_cb = _widget(settings.get('follow', False))
    return ui


def _counter_colors():
    state = {'n': 0}

    def random_color():
        state['n'] += 1
        return 'c%d' % state['n']
    return random_color


def _prepare_excellon(settings, with_ui=True):
    obj = make_excellon(make_app())
    obj.obj_options.update(settings)
    obj.ui = _ui_for(settings) if with_ui else None
    obj.random_color = _counter_colors()
    obj.fill_color = '#fill'
    obj.outline_color = '#line'
    obj.add_shapes_batch = Mock(side_effect=lambda batch, **kw: list(range(100, 100 + len(batch))))
    counter = {'n': 0}

    def add_shape(**kw):
        counter['n'] += 1
        return counter['n']
    obj.add_shape = Mock(side_effect=add_shape)
    obj.shapes = Mock(_indexes=[])
    return obj


def _batch_of(obj):
    return [(d['shape'].wkt, d['color'], d['face_color'], d['layer']) for d in obj.add_shapes_batch.call_args.args[0]]


def _expected_excellon_batch(obj, multicolored):
    out = []
    n = 0
    for tool in obj.tools:
        n += 1
        c = 'c%d' % n
        for geo in obj.tools[tool]['solid_geometry']:
            out.append((geo.wkt, c if multicolored else '#line', c if multicolored else '#fill', 2))
    return out


@pytest.mark.parametrize('multicolored', [False, True])
@pytest.mark.parametrize('plot', [True, False])
class TestExcellonPlotSolid:
    def test_solid_batch_and_visibility(self, multicolored, plot):
        settings = {'solid': True, 'multicolored': multicolored, 'plot': plot}
        obj = _prepare_excellon(settings)
        obj.plot()
        assert obj.add_shapes_batch.call_args.kwargs['visible'] is plot
        assert _batch_of(obj) == _expected_excellon_batch(obj, multicolored)
        for tool in obj.tools:
            assert obj.tools[tool]['multicolor'] == ('c%d' % (list(obj.tools).index(tool) + 1) if multicolored else None)
        # the keys are mapped back to the tools
        assert sum(len(v) for v in obj.shape_indexes_dict.values()) == len(_batch_of(obj))

    def test_explicit_visible_wins(self, multicolored, plot):
        obj = _prepare_excellon({'solid': True, 'multicolored': multicolored, 'plot': plot})
        obj.plot(visible=not plot)
        assert obj.add_shapes_batch.call_args.kwargs['visible'] is (not plot)


@pytest.mark.parametrize('plot', [True, False])
def test_excellon_outline_mode(plot):
    obj = _prepare_excellon({'solid': False, 'multicolored': False, 'plot': plot})
    obj.plot()
    assert not obj.add_shapes_batch.called
    calls = [(c.kwargs['shape'].wkt, c.kwargs['color'], c.kwargs['visible']) for c in obj.add_shape.call_args_list]
    expected = []
    for tool in obj.tools:
        for geo in obj.tools[tool]['solid_geometry']:
            expected.append((geo.exterior.wkt, 'red', plot))
            for ints in geo.interiors:
                expected.append((ints.wkt, 'orange', plot))
    assert calls == expected


def test_excellon_plot_without_ui_has_no_exception_and_all_tools_visible():
    obj = _prepare_excellon({'solid': True, 'multicolored': True, 'plot': True}, with_ui=False)
    obj.plot()
    assert obj.add_shapes_batch.call_args.kwargs['visible'] is True
    assert _batch_of(obj) == _expected_excellon_batch(obj, True)
    # every tool got its shape keys; no per-tool hidden state is applied
    assert set(obj.shape_indexes_dict) == set(obj.tools)


def test_excellon_plot_reads_options_at_call_time():
    obj = _prepare_excellon({'solid': True, 'multicolored': False, 'plot': True}, with_ui=False)
    obj.plot()
    first = _batch_of(obj)
    obj.obj_options['multicolored'] = True
    obj.obj_options['plot'] = False
    obj.plot()
    assert _batch_of(obj) != first
    assert obj.add_shapes_batch.call_args.kwargs['visible'] is False


def test_excellon_pre_ui_object_with_default_options_plots():
    # an object just created (no UI yet): defaults of obj_options (plot True, solid False, multicolored False)
    obj = _prepare_excellon({}, with_ui=False)
    obj.plot()
    assert obj.add_shape.called


# ---------------------------------------------------------------------------------------------------------------
# Gerber
# ---------------------------------------------------------------------------------------------------------------

def _prepare_gerber(settings, line_enable=True, with_ui=True):
    app = make_app()
    app.options['gerber_plot_line_enable'] = line_enable
    obj = make_gerber(app)
    obj.obj_options.update(settings)
    obj.ui = _ui_for(settings) if with_ui else None
    obj.random_color = _counter_colors()
    obj.add_shapes_batch = Mock(side_effect=lambda batch, **kw: list(range(len(batch))))
    obj.add_shape = Mock()
    obj.shapes = Mock(_indexes=[])
    obj.calls = []
    real = obj._app_option

    def counting(key, *a, **kw):
        obj.calls.append(key)
        return real(key, *a, **kw)
    obj._app_option = counting
    return obj


def _gerber_batch(obj):
    return [(d['shape'].wkt, d['color'], d['face_color']) for d in obj.add_shapes_batch.call_args.args[0]]


def _expected_gerber_batch(obj, settings, line_enable):
    from shapely.geometry import LinearRing, LineString, MultiLineString, MultiPolygon, Polygon
    geometry = obj.follow_geometry if settings.get('follow') else obj.solid_geometry
    geoms = geometry.geoms if isinstance(geometry, (MultiPolygon, MultiLineString)) else geometry
    out = []
    n = 0
    for g in geoms:
        if settings['solid']:
            color = '#line'
            if settings['multicolored']:
                n += 1
                face = 'c%d' % n
            else:
                face = '#fill'
        else:
            if settings['multicolored']:
                n += 1
                color = 'c%d' % n
            else:
                color = 'black'
            face = None
        if not line_enable:
            color = None
        if isinstance(g, (Polygon, LineString)):
            out.append((g.wkt, color, face))
        elif isinstance(g, LinearRing):
            out.append((LineString(g).wkt, color, face))
    return out


@pytest.mark.parametrize('line_enable', [True, False])
@pytest.mark.parametrize('follow', [False, True])
@pytest.mark.parametrize('multicolored', [False, True])
@pytest.mark.parametrize('solid', [False, True])
def test_gerber_plot_batch(solid, multicolored, follow, line_enable):
    settings = {'solid': solid, 'multicolored': multicolored, 'follow': follow, 'plot': True}
    obj = _prepare_gerber(settings, line_enable)
    obj.plot(color='#line', face_color='#fill')
    batch = _gerber_batch(obj)
    assert batch
    assert batch == _expected_gerber_batch(obj, settings, line_enable)
    assert obj.add_shapes_batch.call_args.kwargs['visible'] is True


@pytest.mark.skipif(OLD_CODE, reason='counts the new behavior')
def test_gerber_plot_looks_up_the_line_option_once_per_plot():
    settings = {'solid': True, 'multicolored': False, 'follow': False, 'plot': True}
    obj = _prepare_gerber(settings)
    obj.plot(color='#line', face_color='#fill')
    assert len(_gerber_batch(obj)) > 1
    assert obj.calls.count('gerber_plot_line_enable') == 1


@pytest.mark.skipif(OLD_CODE, reason='the old code needs the UI')
def test_gerber_plot_reads_follow_from_the_options_and_works_without_ui():
    settings = {'solid': True, 'multicolored': False, 'follow': True, 'plot': True}
    obj = _prepare_gerber(settings, with_ui=False)
    obj.plot(color='#line', face_color='#fill')
    assert _gerber_batch(obj) == _expected_gerber_batch(obj, settings, True)
    obj.obj_options['follow'] = False
    obj.plot(color='#line', face_color='#fill')
    assert _gerber_batch(obj) == _expected_gerber_batch(obj, dict(settings, follow=False), True)


@pytest.mark.skipif(OLD_CODE, reason='the old code needs the UI')
def test_gerber_plot_with_raising_ui_stub():
    settings = {'solid': False, 'multicolored': True, 'follow': False, 'plot': True}
    obj = _prepare_gerber(settings)
    obj.ui = RaisingUI()
    obj.plot()
    assert _gerber_batch(obj) == _expected_gerber_batch(obj, settings, True)


@pytest.mark.skipif(OLD_CODE, reason='the old handler does not write the option')
def test_gerber_follow_toggle_writes_the_option_before_plot():
    obj = _prepare_gerber({'solid': True, 'multicolored': False, 'follow': False, 'plot': True})
    seen = []
    obj.plot = Mock(side_effect=lambda *a, **k: seen.append(obj.obj_options['follow']))
    ui = Mock()
    ui.follow_cb.get_value.return_value = True
    obj.ui = ui
    obj.form_fields['follow'] = ui.follow_cb
    obj.muted_ui = False
    obj.on_follow_cb_click()
    assert seen == [True]
