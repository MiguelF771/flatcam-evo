"""Stage 4, item 1: methods that were copied into every object class now live once on FlatCAMObj (or on a small
mixin). Headless: ShapeCollection is patched, the ObjectUI is a Mock."""

import re
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pytest

from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.CNCJobObject import CNCJobObject
from appObjects.DocumentObject import DocumentObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appObjects.ScriptObject import ScriptObject
from tests.test_unify_regressions_0b import make_app, no_shape_collection  # noqa: F401  (fixture)

ALL_CLASSES = [GerberObject, ExcellonObject, GeometryObject, CNCJobObject, ScriptObject, DocumentObject]


def defining_class(cls, name):
    """The first class in the MRO whose own __dict__ defines `name`."""
    for klass in cls.__mro__:
        if name in vars(klass):
            return klass
    return None


def make_obj(cls):
    return cls('obj', make_app())


# ---------------------------------------------------------------- random_color

@pytest.mark.parametrize('cls', ALL_CLASSES)
def test_random_color_resolves_to_flatcamobj(cls):
    assert defining_class(cls, 'random_color') is FlatCAMObj


def test_random_color_3d_engine_is_an_opaque_rgba_array(no_shape_collection):  # noqa: F811
    obj = make_obj(GerberObject)
    obj.app.use_3d_engine = True
    for _ in range(20):
        color = obj.random_color()
        assert isinstance(color, np.ndarray)
        assert color.shape == (4,)
        assert color[3] == 1
        assert np.all((color >= 0) & (color <= 1))


def test_random_color_2d_engine_is_a_9_char_hex_string(no_shape_collection):  # noqa: F811
    obj = make_obj(GeometryObject)
    obj.app.use_3d_engine = False
    for _ in range(50):
        color = obj.random_color()
        assert isinstance(color, str)
        assert len(color) == 9
        assert re.fullmatch(r'#[0-9a-f]{8}', color)
