"""Stage 5, item 2: the 3 editor helpers of App (``_editor_for``, ``_deactivate_editor``, ``_edited_obj_is_empty``).

Headless. The ``legacy_*`` helpers are copies of the expressions that ``App.on_editing_finished`` used before the
helpers existed; ``CASES`` lists what old and new give for each object.
"""

from unittest.mock import Mock

import pytest
from shapely.geometry import Point, Polygon, box

from appObjects.ExcellonObject import ExcellonObject
from appObjects.GerberObject import GerberObject

from test_unify_characterization import (  # noqa: F401  (the fixtures must be visible in this module)
    _headless_qt, app, excellon, gerber, make_excellon, make_gerber,
)


def legacy_gerber_is_empty(edited_obj):
    """The Gerber save branch of on_editing_finished, before the helper."""
    try:
        if len(edited_obj.solid_geometry) == 0:
            return True
    except TypeError:
        # a single Polygon: the len() does not work, the old code passed
        pass
    return False


def legacy_excellon_is_empty(edited_obj):
    """The Excellon save branch of on_editing_finished, before the helper."""
    has_drills = None
    for tt in edited_obj.tools:
        if 'drills' in edited_obj.tools[tt] and edited_obj.tools[tt]['drills']:
            has_drills = True
            break
    has_slots = None
    for tt in edited_obj.tools:
        if 'slots' in edited_obj.tools[tt] and edited_obj.tools[tt]['slots']:
            has_slots = True
            break
    return has_drills is None and has_slots is None


def _single_polygon_gerber(app):
    obj = GerberObject('g', app)
    obj.solid_geometry = box(0, 0, 1, 1)
    return obj


def _gerber_tools_all_empty_geometry(app):
    obj = GerberObject('g', app)
    obj.tools = {10: {'size': 1.0, 'type': 'C', 'geometry': [{'solid': Polygon(), 'follow': Point(0, 0)}]}}
    obj.solid_geometry = [Polygon()]
    return obj


def _gerber_tools_no_geometry_list(app):
    obj = GerberObject('g', app)
    obj.tools = {10: {'size': 1.0, 'type': 'C', 'geometry': []}}
    obj.solid_geometry = []
    return obj


def _gerber_features_but_empty_solid_geometry(app):
    obj = make_gerber(app)
    obj.solid_geometry = []
    return obj


def _excellon_empty_lists(app):
    obj = ExcellonObject('e', app)
    obj.tools = {1: {'tooldia': 1.0, 'drills': [], 'slots': [], 'solid_geometry': [], 'data': {}}}
    return obj


def _excellon_slots_only(app):
    obj = ExcellonObject('e', app)
    obj.tools = {1: {'tooldia': 1.0, 'drills': [], 'slots': [(Point(0, 0), Point(5, 0))], 'solid_geometry': [],
                     'data': {}}}
    assert obj.create_geometry() is None
    return obj


# (id, builder, legacy helper, old result, new result)
CASES = [
    ('gerber_mixed', lambda a: make_gerber(a), legacy_gerber_is_empty, False, False),
    # a new object has an empty Polygon as solid_geometry: len() raises TypeError, the old code passed
    ('gerber_new_object_empty_polygon', lambda a: GerberObject('g', a), legacy_gerber_is_empty, False, True),
    ('gerber_single_polygon', _single_polygon_gerber, legacy_gerber_is_empty, False, False),
    ('gerber_tools_empty_geometry_list', _gerber_tools_no_geometry_list, legacy_gerber_is_empty, True, True),
    # the behavior change: tools exist, every geometry is empty, solid_geometry is a list with an empty Polygon
    ('gerber_tools_all_empty_geometry', _gerber_tools_all_empty_geometry, legacy_gerber_is_empty, False, True),
    # the other direction: records with geometry, an empty composed solid_geometry list
    ('gerber_records_but_empty_solid_geometry', _gerber_features_but_empty_solid_geometry,
     legacy_gerber_is_empty, True, False),
    ('excellon_mixed', lambda a: make_excellon(a), legacy_excellon_is_empty, False, False),
    ('excellon_new_object', lambda a: ExcellonObject('e', a), legacy_excellon_is_empty, True, True),
    ('excellon_empty_lists', _excellon_empty_lists, legacy_excellon_is_empty, True, True),
    ('excellon_slots_only', _excellon_slots_only, legacy_excellon_is_empty, False, False),
]


@pytest.fixture
def App():
    from appMain import App as _App
    return _App


@pytest.mark.parametrize('case', CASES, ids=[c[0] for c in CASES])
class TestEditedObjIsEmpty:
    def test_legacy_expression(self, app, case):
        _id, build, legacy, old, _new = case
        assert legacy(build(app)) is old

    def test_new_helper(self, app, App, case):
        _id, build, _legacy, _old, new = case
        assert App._edited_obj_is_empty(Mock(), build(app)) is new


@pytest.mark.parametrize('kind', ['geometry', 'cncjob'])
def test_other_kinds_are_never_empty(App, kind):
    assert App._edited_obj_is_empty(Mock(), Mock(kind=kind)) is False


def _fake_app():
    """A Mock App with the 4 editors; the helpers are bound to it as the real methods."""
    from appMain import App
    fake = Mock()
    fake.geo_editor, fake.exc_editor, fake.grb_editor, fake.gcode_editor = Mock(), Mock(), Mock(), Mock()
    fake._editor_for = lambda kind: App._editor_for(fake, kind)
    return fake


@pytest.mark.parametrize('kind, attr', [('geometry', 'geo_editor'), ('excellon', 'exc_editor'),
                                        ('gerber', 'grb_editor'), ('cncjob', 'gcode_editor')])
class TestPerKind:
    def test_editor_for(self, App, kind, attr):
        fake = _fake_app()
        assert App._editor_for(fake, kind) is getattr(fake, attr)

    def test_deactivate_calls_only_that_editor(self, App, kind, attr):
        fake = _fake_app()
        App._deactivate_editor(fake, kind)
        getattr(fake, attr).deactivate.assert_called_once_with()
        for other in ('geo_editor', 'exc_editor', 'grb_editor', 'gcode_editor'):
            if other != attr:
                getattr(fake, other).deactivate.assert_not_called()

    def test_missing_editor_is_a_noop(self, App, kind, attr):
        fake = _fake_app()
        setattr(fake, attr, None)
        assert App._editor_for(fake, kind) is None
        App._deactivate_editor(fake, kind)   # no AttributeError


@pytest.mark.parametrize('kind', ['', 'script', 'document', None, 'Gerber'])
def test_unknown_kind(App, kind):
    fake = _fake_app()
    assert App._editor_for(fake, kind) is None
    App._deactivate_editor(fake, kind)
    for name in ('geo_editor', 'exc_editor', 'grb_editor', 'gcode_editor'):
        getattr(fake, name).deactivate.assert_not_called()


def test_gerber_editor_deactivate_is_the_grb_method():
    from appEditors.appGerberEditor import AppGerberEditor
    assert AppGerberEditor.deactivate is AppGerberEditor.deactivate_grb_editor
