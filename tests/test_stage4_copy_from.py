"""Stage 4: one ``copy_from(src)`` per object kind, used by both copy paths of appHandlers/appObjectOps.py
(``on_copy_command`` and ``on_copy_object2``).

The 2 real paths run headless with a factory that creates the object and calls ``initialize`` (as in
test_unify_characterization.py). For each kind and path, every attribute that copy_from specifies must equal the
source, and the copy must not share data with the source (mutate either one, the other stays the same).

XFAIL_OLD lists the (kind, path, attribute) combinations the OLD per-path code loses; the Stage 4 commit that adds
copy_from empties it.
"""

import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QObject
from shapely.geometry import LineString, Point, box
from shapely.geometry.base import BaseGeometry

from appHandlers.appObjectOps import AppObjectOps
from appObjects.DocumentObject import DocumentObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appObjects.ScriptObject import ScriptObject

from test_unify_characterization import (  # noqa: F401  (the fixtures must be visible in this module)
    _headless_qt, app, make_excellon, make_gerber, make_same_diameter_excellon,
)

CLASSES = {'gerber': GerberObject, 'excellon': ExcellonObject, 'geometry': GeometryObject,
           'script': ScriptObject, 'document': DocumentObject}

# (kind, path, attribute) lost by the old code (found by running this file on the old code)
XFAIL_OLD = {
    ('gerber', 'command', 'aperture_macros'),
    ('gerber', 'command', 'multigeo'),
    ('gerber', 'command', 'int_digits'),
    ('gerber', 'command', 'frac_digits'),
    ('excellon', 'command', 'zeros'),
    ('excellon', 'command', 'excellon_format_upper_mm'),
    ('excellon', 'command', 'excellon_format_lower_mm'),
    ('excellon', 'command', 'excellon_format_upper_in'),
    ('excellon', 'command', 'excellon_format_lower_in'),
    ('excellon', 'command', 'excellon_units'),
    ('geometry', 'command', 'multigeo'),
    ('geometry', 'command', 'tooluid'),
    ('gerber', 'object2', 'follow_geometry'),
    ('gerber', 'object2', 'source_file'),
    ('gerber', 'object2', 'multigeo'),
    ('gerber', 'object2', 'int_digits'),
    ('gerber', 'object2', 'frac_digits'),
    ('excellon', 'object2', 'zeros'),
    ('excellon', 'object2', 'excellon_format_upper_mm'),
    ('excellon', 'object2', 'excellon_format_lower_mm'),
    ('excellon', 'object2', 'excellon_format_upper_in'),
    ('excellon', 'object2', 'excellon_format_lower_in'),
    ('excellon', 'object2', 'excellon_units'),
    ('geometry', 'object2', 'multigeo'),
    ('geometry', 'object2', 'tooluid'),
    ('geometry', 'object2', 'source_file'),
}


# ---------------------------------------------------------------------------------------------------------------
# running the 2 real paths headless
# ---------------------------------------------------------------------------------------------------------------

def make_new(kind, name, app):
    if kind in ('script', 'document'):
        return bare(CLASSES[kind], name, app, kind)
    return CLASSES[kind](name, app)


def run_path(app, source, kind, path):
    """Run on_copy_command() / on_copy_object2() headless; returns the new object (or 'fail')."""
    created = {}

    def new_object(obj_kind, name, initialize, *args, **kwargs):
        assert obj_kind == kind
        new_obj = make_new(kind, name, app)
        if initialize(new_obj, app) == 'fail':
            return 'fail'
        created[name] = new_obj
        return new_obj

    app.app_obj.new_object.side_effect = new_object
    app.f_handlers = SimpleNamespace(export_excellon=Mock(return_value='REGENERATED\n'))
    app.collection.get_selected.return_value = [source]
    ops = AppObjectOps(app)
    if path == 'command':
        ops.on_copy_command()
        name = source.obj_options['name'] + '_copy'
    else:
        ops.on_copy_object2('_c2')
        name = source.obj_options['name'] + '_c2'
    assert list(created) == [name]
    return created[name]


def norm(value):
    """A comparable, deep snapshot of a value (shapely geometries as WKB)."""
    if isinstance(value, BaseGeometry):
        return value.wkb_hex
    if isinstance(value, dict):
        return {k: norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [norm(v) for v in value]
    if hasattr(value, '__dict__') and not isinstance(value, type):
        return (type(value).__name__, norm(vars(value)))
    return value


# ---------------------------------------------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------------------------------------------

def gerber_source(app):
    obj = make_gerber(app)
    assert obj.aperture_macros and obj.follow_geometry
    obj.export_as_regions = True
    obj.multigeo = True
    obj.int_digits = 2
    obj.frac_digits = 5
    obj.units = 'MM'
    obj.obj_options['name'] = 'grb'
    return obj


def excellon_source(app):
    obj = make_same_diameter_excellon(app)
    obj.source_file = 'M48\nMETRIC\nT01C1.0\n%\nM30\n'
    obj.zeros = 'T'
    obj.excellon_format_upper_mm = 2
    obj.excellon_format_lower_mm = 5
    obj.excellon_format_upper_in = 1
    obj.excellon_format_lower_in = 6
    obj.excellon_units = 'MM'
    obj.obj_options['name'] = 'exc'
    return obj


def geometry_source(app):
    obj = GeometryObject('geo', app)
    obj.units = 'MM'
    obj.multigeo = True
    obj.tooluid = 2
    shape_a = [box(0, 0, 4, 4), box(10, 10, 12, 13)]
    shape_b = [LineString([(0, 0), (5, 5)]).buffer(0.5)]
    obj.tools = {
        1: {'tooldia': 1.0, 'data': {'cutz': -0.5, 'name': 'first'}, 'solid_geometry': shape_a},
        2: {'tooldia': 2.0, 'data': {'cutz': -1.5, 'name': 'second'}, 'solid_geometry': shape_b},
    }
    obj.solid_geometry = shape_a + shape_b
    obj.follow_geometry = [LineString([(0, 0), (5, 5)])]
    obj.source_file = 'geometry source text'
    obj.obj_options['name'] = 'geo'
    return obj


def bare(cls, name, app, kind):
    """ScriptObject / DocumentObject cannot be constructed headless: FlatCAMObj.__init__ calls
    super().__init__(app=app), which reaches QObject for the classes without a camlib base (PyQt6 raises
    TypeError). The copy paths only need obj_options['name'], kind and source_file."""
    obj = cls.__new__(cls)
    QObject.__init__(obj)
    obj.app = app
    obj.kind = kind
    obj.obj_options = {'name': name}
    obj.source_file = ''
    return obj


def script_source(app):
    obj = bare(ScriptObject, 'scr', app, 'script')
    obj.source_file = 'puts "hello"\nnew_geometry g\n'
    return obj


def document_source(app):
    obj = bare(DocumentObject, 'doc', app, 'document')
    obj.source_file = 'Some document text\nsecond line\n'
    return obj


SOURCES = {'gerber': gerber_source, 'excellon': excellon_source, 'geometry': geometry_source,
           'script': script_source, 'document': document_source}

# the attributes copy_from specifies, per kind
ATTRS = {
    'gerber': ['solid_geometry', 'follow_geometry', 'tools', 'aperture_macros', 'source_file', 'export_as_regions',
               'multigeo', 'int_digits', 'frac_digits', 'units'],
    'excellon': ['tools', 'solid_geometry', 'source_file', 'units', 'zeros', 'excellon_format_upper_mm',
                 'excellon_format_lower_mm', 'excellon_format_upper_in', 'excellon_format_lower_in',
                 'excellon_units'],
    'geometry': ['solid_geometry', 'follow_geometry', 'tools', 'multigeo', 'tooluid', 'source_file', 'units'],
    'script': ['source_file'],
    'document': ['source_file'],
}

# (kind, path) pairs that exist: on_copy_object2 handles only Gerber, Excellon and Geometry
COMBOS = [(k, 'command') for k in SOURCES] + [(k, 'object2') for k in ('gerber', 'excellon', 'geometry')]


def attr_cases():
    cases = []
    for kind, path in COMBOS:
        for attr in ATTRS[kind]:
            if (kind, path, attr) == ('excellon', 'object2', 'source_file'):
                continue   # that path deliberately re-exports the file text under the new name
            marks = []
            if (kind, path, attr) in XFAIL_OLD:
                marks = [pytest.mark.xfail(strict=True, raises=AssertionError, reason='fixed in Stage 4')]
            cases.append(pytest.param(kind, path, attr, id='%s-%s-%s' % (kind, path, attr), marks=marks))
    return cases


# ---------------------------------------------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize('kind,path,attr', attr_cases())
def test_copy_equals_source_on_attribute(app, kind, path, attr):
    source = SOURCES[kind](app)
    before = norm(getattr(source, attr))
    clone = run_path(app, source, kind, path)
    assert norm(getattr(clone, attr, None)) == before
    assert norm(getattr(source, attr)) == before   # the copy did not touch the source


@pytest.mark.parametrize('kind,path', [pytest.param(k, p, id='%s-%s' % (k, p)) for k, p in COMBOS
                                       if k in ('gerber', 'excellon', 'geometry')])
def test_copy_is_independent(app, kind, path):
    """Mutate the source deeply, the copy stays; mutate the copy deeply, the source stays."""
    attrs = [a for a in ATTRS[kind] if a not in ('source_file', 'units')]
    source = SOURCES[kind](app)
    clone = run_path(app, source, kind, path)
    snap_clone = {a: norm(getattr(clone, a, None)) for a in attrs}
    snap_source = {a: norm(getattr(source, a, None)) for a in attrs}

    def mutate(obj):
        obj.solid_geometry.append(Point(100, 100).buffer(3))
        for tool in obj.tools.values():
            if 'geometry' in tool:
                tool['geometry'].append({'solid': Point(7, 7).buffer(1)})
            if 'drills' in tool:
                tool['drills'].append(Point(99, 99))
            if 'solid_geometry' in tool and isinstance(tool['solid_geometry'], list):
                tool['solid_geometry'].append(Point(98, 98).buffer(1))
            if 'data' in tool and isinstance(tool['data'], dict):
                tool['data']['mutated'] = True
            tool['mutated'] = True
        if getattr(obj, 'follow_geometry', None) is not None:
            obj.follow_geometry.append(Point(100, 100))
        if getattr(obj, 'aperture_macros', None):
            obj.aperture_macros[next(iter(obj.aperture_macros))].raw = 'MUTATED'
            obj.aperture_macros['EXTRA'] = 'x'

    # a copy that lost an attribute (None / empty) is covered by the equality test; skip the missing parts here
    mutate(source)
    assert {a: norm(getattr(clone, a, None)) for a in attrs} == snap_clone
    mutate(clone)
    # the source only gets its own mutation: re-apply to a fresh source and compare
    reference = SOURCES[kind](app)
    mutate(reference)
    assert {a: norm(getattr(source, a, None)) for a in attrs} == {a: norm(getattr(reference, a, None)) for a in attrs}
    assert snap_source != {a: norm(getattr(source, a, None)) for a in attrs}   # the mutation was effective


def test_gerber_without_apertures_fails_in_object2(app):
    source = gerber_source(app)
    source.tools = {}
    assert run_path_or_fail(app, source, 'gerber', 'object2') == 'fail'


def test_excellon_without_tools_fails_in_both_paths(app):
    for path in ('command', 'object2'):
        source = excellon_source(app)
        source.tools = {}
        assert run_path_or_fail(app, source, 'excellon', path) == 'fail'


def run_path_or_fail(app, source, kind, path):
    results = []

    def new_object(obj_kind, name, initialize, *args, **kwargs):
        new_obj = make_new(kind, name, app)
        res ='fail' if initialize(new_obj, app) == 'fail' else new_obj
        results.append(res)
        return res

    app.app_obj.new_object.side_effect = new_object
    app.f_handlers = SimpleNamespace(export_excellon=Mock(return_value='REGENERATED\n'))
    app.collection.get_selected.return_value = [source]
    ops = AppObjectOps(app)
    ops.on_copy_command() if path == 'command' else ops.on_copy_object2('_c2')
    return results[0]


def test_excellon_object2_regenerates_the_source_file_under_the_new_name(app):
    source = excellon_source(app)
    clone = run_path(app, source, 'excellon', 'object2')
    assert clone.source_file == 'REGENERATED\n'
    assert app.f_handlers.export_excellon.call_args.kwargs['obj_name'] == 'exc_c2'


def test_excellon_same_diameter_data_is_kept_by_both_paths(app):
    for path in ('command', 'object2'):
        clone = run_path(app, excellon_source(app), 'excellon', path)
        assert clone.tools[1]['data']['tag'] == 'first' and clone.tools[2]['data']['tag'] == 'second'
        assert [clone.tools[t]['tooldia'] for t in (1, 2)] == [1.0, 1.0]


def test_cncjob_cannot_be_copied(app):
    job = SimpleNamespace(kind='cncjob', obj_options={'name': 'job'})
    app.collection.get_selected.return_value = [job]
    ops = AppObjectOps(app)
    ops.on_copy_command()
    app.app_obj.new_object.assert_not_called()


@pytest.mark.xfail(strict=True, raises=AssertionError, reason='fixed in Stage 4')
def test_copy_from_exists_on_every_copyable_kind(app):
    for kind, cls in CLASSES.items():
        assert callable(getattr(cls, 'copy_from', None)), kind
