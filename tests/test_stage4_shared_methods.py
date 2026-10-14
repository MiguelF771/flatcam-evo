"""Stage 4, item 1: methods that were copied into every object class now live once on FlatCAMObj (or on a small
mixin). Headless: ShapeCollection is patched, the ObjectUI is a Mock."""

import re
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pytest
from PyQt6 import QtCore

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


# ------------------------------------------------- on_properties / on_properties_expanded

PROPERTIES_CLASSES = [GerberObject, ExcellonObject, GeometryObject, CNCJobObject]


@pytest.mark.parametrize('cls', PROPERTIES_CLASSES)
def test_on_properties_expanded_has_one_definition(cls):
    owner = defining_class(cls, 'on_properties_expanded')
    assert owner is not cls
    assert owner.__name__ == 'PropertiesPanelMixin'


@pytest.mark.parametrize('cls', [ExcellonObject, GeometryObject, CNCJobObject])
def test_on_properties_has_one_definition(cls):
    assert defining_class(cls, 'on_properties').__name__ == 'PropertiesPanelMixin'


def test_gerber_keeps_its_own_on_properties():
    # GerberObject.on_properties does not set the size policy of the tree, the others do: not identical, not moved
    assert defining_class(GerberObject, 'on_properties') is GerberObject


@pytest.mark.parametrize('cls', [ExcellonObject, GeometryObject, CNCJobObject])
def test_on_properties_checked(cls, no_shape_collection):  # noqa: F811
    obj = make_obj(cls)
    obj.ui = MagicMock()
    with patch.object(FlatCAMObj, 'add_properties_items') as add_items:
        obj.on_properties(True)
    obj.ui.info_frame.show.assert_called_once_with()
    obj.ui.info_frame.hide.assert_not_called()
    obj.ui.treeWidget.clear.assert_called_once_with()
    add_items.assert_called_once_with(obj=obj, treeWidget=obj.ui.treeWidget)
    obj.ui.treeWidget.setSizePolicy.assert_called_once()
    obj.ui.treeWidget.resize_sig.emit.assert_called_once_with()


def test_gerber_on_properties_checked(no_shape_collection):  # noqa: F811
    obj = make_obj(GerberObject)
    obj.ui = MagicMock()
    with patch.object(FlatCAMObj, 'add_properties_items') as add_items:
        obj.on_properties(True)
    obj.ui.info_frame.show.assert_called_once_with()
    obj.ui.treeWidget.clear.assert_called_once_with()
    add_items.assert_called_once_with(obj=obj, treeWidget=obj.ui.treeWidget)
    obj.ui.treeWidget.setSizePolicy.assert_not_called()
    obj.ui.treeWidget.resize_sig.emit.assert_called_once_with()


@pytest.mark.parametrize('cls', PROPERTIES_CLASSES)
def test_on_properties_unchecked_hides_and_stops(cls, no_shape_collection):  # noqa: F811
    obj = make_obj(cls)
    obj.ui = MagicMock()
    with patch.object(FlatCAMObj, 'add_properties_items') as add_items:
        obj.on_properties(False)
    obj.ui.info_frame.hide.assert_called_once_with()
    obj.ui.info_frame.show.assert_not_called()
    obj.ui.treeWidget.clear.assert_not_called()
    add_items.assert_not_called()


@pytest.mark.parametrize('cls', PROPERTIES_CLASSES)
def test_on_properties_expanded_resizes_every_column(cls, no_shape_collection):  # noqa: F811
    obj = make_obj(cls)
    obj.ui = MagicMock()
    # the method reads the count from self.treeWidget (not self.ui.treeWidget): same as before the move
    obj.treeWidget = Mock()
    obj.treeWidget.columnCount.return_value = 3
    obj.on_properties_expanded()
    assert [c.args for c in obj.ui.treeWidget.resizeColumnToContents.call_args_list] == [(0,), (1,), (2,)]


# ------------------------------------------------------------- change_level

@pytest.mark.parametrize('cls', ALL_CLASSES)
def test_change_level_resolves_to_flatcamobj(cls):
    assert defining_class(cls, 'change_level') is FlatCAMObj


@pytest.mark.parametrize('cls', ALL_CLASSES)
@pytest.mark.parametrize('level, checked', [('a', True), ('b', False), ('anything else', False)])
def test_change_level_sets_the_toggle_then_notifies(cls, level, checked):
    # ScriptObject and DocumentObject cannot be built with the headless app, so the function found through the class
    # is called with a stand-in for self (it only uses self.ui.level and self.on_level_changed)
    me = SimpleNamespace(ui=MagicMock(), on_level_changed=Mock())
    me.ui.level.isChecked.return_value = checked
    cls.change_level(me, level)
    me.ui.level.setChecked.assert_called_once_with(checked)
    me.on_level_changed.assert_called_once_with(checked)


# --------------------------------------------------------- set_offset_values

OFFSET_CLASSES = [GerberObject, ExcellonObject, GeometryObject]


@pytest.mark.parametrize('cls', OFFSET_CLASSES)
def test_set_offset_values_has_one_definition(cls):
    assert defining_class(cls, 'set_offset_values').__name__ == 'OffsetUiMixin'


@pytest.mark.parametrize('cls', [CNCJobObject, ScriptObject, DocumentObject])
def test_other_classes_do_not_get_set_offset_values(cls):
    # no bounds-centre offset entry in their UI: the method must not be added to them
    assert defining_class(cls, 'set_offset_values') is None


@pytest.mark.parametrize('cls', OFFSET_CLASSES)
def test_set_offset_values_writes_the_bounds_centre(cls, no_shape_collection):  # noqa: F811
    obj = make_obj(cls)
    obj.ui = MagicMock()
    obj.app.dec_format = lambda value, decimals: round(value, decimals)
    with patch.object(cls, 'bounds', return_value=(1.0, 2.0, 5.0, 3.0)):
        obj.set_offset_values()
    obj.ui.offsetvector_entry.set_value.assert_called_once_with('(3.0, 2.5)')


# ------------------------------------------------------------ optionChanged

@pytest.mark.parametrize('cls', ALL_CLASSES)
def test_option_changed_is_declared_once_on_flatcamobj(cls):
    assert 'optionChanged' not in vars(cls)
    assert defining_class(cls, 'optionChanged') is FlatCAMObj


@pytest.mark.parametrize('cls', [GerberObject, ExcellonObject, GeometryObject, CNCJobObject])
def test_option_changed_emits_the_key_for_built_objects(cls, no_shape_collection):  # noqa: F811
    obj = make_obj(cls)
    slot = Mock()
    obj.optionChanged.connect(slot)
    with patch.object(cls, 'set_form_item'):
        obj.on_options_change('multicolored')
    slot.assert_called_once_with('multicolored')


@pytest.mark.parametrize('cls', [ScriptObject, DocumentObject])
def test_option_changed_emits_the_key_for_script_and_document(cls):
    # these 2 cannot be built with the headless app (their __init__ passes app= to QObject), so only the QObject part
    # is initialized: enough to bind and emit the inherited signal
    obj = cls.__new__(cls)
    QtCore.QObject.__init__(obj)
    slot = Mock()
    obj.optionChanged.connect(slot)
    obj.optionChanged.emit('plot')
    slot.assert_called_once_with('plot')


def test_option_changed_signature_is_a_single_str(no_shape_collection):  # noqa: F811
    # emitting anything but a str is rejected, as with the per-class declarations before
    obj = make_obj(GerberObject)
    with pytest.raises(TypeError):
        obj.optionChanged.emit(1.5)
