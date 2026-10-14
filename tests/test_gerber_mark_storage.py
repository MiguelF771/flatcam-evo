"""Marking an aperture that has no key in mark_shapes_storage must not fail (aperture added after build_ui)."""
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_unify_characterization import make_app, make_gerber, _headless_qt  # noqa: E402,F401


def _gerber():
    app = make_app()
    obj = make_gerber(app)
    obj.mark_shapes = Mock()
    counter = iter(range(1000))
    obj.add_mark_shape = Mock(side_effect=lambda **kw: next(counter))
    return app, obj


def _aperture_with_solid(obj):
    for ap, tool in obj.tools.items():
        if any('solid' in el for el in tool['geometry']):
            return ap
    raise AssertionError('no aperture with solid geometry')


def test_plot_aperture_without_storage_key_stores_shape_keys():
    app, obj = _gerber()
    ap = _aperture_with_solid(obj)
    obj.mark_shapes_storage.clear()

    obj.plot_aperture(run_thread=False, marked_aperture=ap, color='#ff0000')

    assert obj.mark_shapes_storage[ap]
    assert len(obj.mark_shapes_storage[ap]) == obj.add_mark_shape.call_count
    app.log.error.assert_not_called()
    obj.mark_shapes.redraw.assert_called_once()


def test_plot_aperture_keeps_existing_storage_key():
    app, obj = _gerber()
    ap = _aperture_with_solid(obj)
    obj.mark_shapes_storage[ap] = ['old']

    obj.plot_aperture(run_thread=False, marked_aperture=ap, color='#ff0000')

    assert obj.mark_shapes_storage[ap][0] == 'old'
    assert len(obj.mark_shapes_storage[ap]) > 1
    app.log.error.assert_not_called()


def test_clear_plot_apertures_with_unknown_aperture_does_not_raise():
    app, obj = _gerber()
    obj.mark_shapes_storage['other'] = [1]

    obj.clear_plot_apertures('missing')

    assert obj.mark_shapes_storage['missing'] == []
    assert obj.mark_shapes_storage['other'] == [1]
    obj.mark_shapes.redraw.assert_called_once()
