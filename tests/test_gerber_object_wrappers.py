"""GerberObject transform wrappers: each calls the Gerber method once, then the _after_transform hook, which emits
replotApertures once. Headless: ShapeCollection is patched, the signal is replaced by a Mock."""

from unittest.mock import Mock, patch

import pytest

from appObjects.GerberObject import GerberObject
from appParsers.ParseGerber import Gerber
from tests.test_unify_regressions_0b import make_app, no_shape_collection  # noqa: F401  (fixture)

# name -> (arguments of the wrapper, keyword arguments the wrapper passes to the Gerber method)
CASES = {
    'mirror': (('X', (1.0, 2.0)), {'axis': 'X', 'point': (1.0, 2.0)}),
    'offset': (((1.0, 2.0),), {'vect': (1.0, 2.0)}),
    'rotate': ((90.0, (1.0, 2.0)), {'angle': 90.0, 'point': (1.0, 2.0)}),
    'scale': ((2.0, 3.0, (1.0, 2.0)), {'xfactor': 2.0, 'yfactor': 3.0, 'point': (1.0, 2.0)}),
    'skew': ((5.0, 6.0, (1.0, 2.0)), {'angle_x': 5.0, 'angle_y': 6.0, 'point': (1.0, 2.0)}),
    'buffer': ((1.5, 'round', None, True), {'distance': 1.5, 'join': 'round', 'factor': None, 'only_exterior': True}),
}


@pytest.fixture
def grb(no_shape_collection):  # noqa: F811
    obj = GerberObject('grb', make_app())
    with patch.object(GerberObject, 'replotApertures', Mock()) as signal:
        obj.signal = signal
        yield obj


@pytest.mark.parametrize('name', sorted(CASES))
def test_wrapper_emits_replot_once_after_the_parent_call(grb, name):
    args, parent_kwargs = CASES[name]
    order = []
    parent = Mock(side_effect=lambda *a, **k: order.append('parent') or 'fail')
    grb.signal.emit.side_effect = lambda: order.append('emit')

    with patch.object(Gerber, name, parent):
        result = getattr(grb, name)(*args)

    parent.assert_called_once_with(grb, **parent_kwargs)
    grb.signal.emit.assert_called_once_with()
    assert order == ['parent', 'emit']
    # the wrapper returns the parent result, so a caller can see 'fail' (for example a rejected buffer)
    assert result == 'fail'


@pytest.mark.parametrize('name', sorted(CASES))
def test_wrapper_goes_through_the_hook(grb, name):
    args, _ = CASES[name]
    with patch.object(Gerber, name, Mock(return_value=None)), \
            patch.object(GerberObject, '_after_transform', Mock()) as hook:
        getattr(grb, name)(*args)
    hook.assert_called_once_with()
    grb.signal.emit.assert_not_called()
