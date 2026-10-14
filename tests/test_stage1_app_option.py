"""Stage 1 (reduce unnecessary inheritance): the shared app_option function and the ser_attrs duplicates.

Everything is headless: objects are built with a SimpleNamespace app and the VisPy ShapeCollection patched out.
"""

import os
import sys
from collections import Counter, defaultdict
from types import SimpleNamespace
from unittest.mock import Mock


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import camlib  # noqa: E402
from appObjects.AppObjectTemplate import FlatCAMObj  # noqa: E402
from appObjects.CNCJobObject import CNCJobObject  # noqa: E402
from appObjects.DocumentObject import DocumentObject  # noqa: E402
from appObjects.GeometryObject import GeometryObject  # noqa: E402
from appObjects.ScriptObject import ScriptObject  # noqa: E402
from appParsers.ParseExcellon import Excellon  # noqa: E402
from appParsers.ParseGerber import Gerber  # noqa: E402
from camlib import CNCjob, Geometry, app_option  # noqa: E402
# fixtures and helpers of the characterization tests (a fixture imported here is collected as ours)
from test_unify_characterization import (  # noqa: E402,F401
    _headless_qt, app, assert_same_tools, make_excellon, make_gerber, save_and_load,
)


class Defaults(dict):
    def __init__(self, data=None, factory=None):
        super().__init__(data or {})
        self.factory_defaults = factory or {}


def fake_app(options=None, defaults=None):
    return SimpleNamespace(options=options or {}, defaults=defaults)


class TestAppOptionFallbackOrder:
    def test_options_win_over_defaults_and_factory(self):
        a = fake_app({'k': 1}, Defaults({'k': 2}, {'k': 3}))
        assert app_option(a, 'k', 4) == 1

    def test_defaults_win_over_factory(self):
        a = fake_app({}, Defaults({'k': 2}, {'k': 3}))
        assert app_option(a, 'k', 4) == 2

    def test_factory_wins_over_fallback(self):
        a = fake_app({}, Defaults({}, {'k': 3}))
        assert app_option(a, 'k', 4) == 3

    def test_fallback_last(self):
        a = fake_app({}, Defaults())
        assert app_option(a, 'k', 4) == 4
        assert app_option(a, 'k') is None

    def test_no_defaults_object_uses_options_then_fallback(self):
        a = fake_app({'k': 1}, None)
        assert app_option(a, 'k', 4) == 1
        assert app_option(a, 'other', 4) == 4

    def test_defaults_without_factory_attribute(self):
        d = {'k': 2}  # a plain dict has no factory_defaults
        assert app_option(fake_app({}, d), 'k', 4) == 2
        assert app_option(fake_app({}, d), 'zz', 4) == 4

    def test_falsy_option_value_is_kept(self):
        a = fake_app({'k': 0}, Defaults({'k': 2}))
        assert app_option(a, 'k', 4) == 0


class TestSharedByBothClasses:
    def test_geometry_and_flatcamobj_delegate_to_the_function(self):
        a = fake_app({}, Defaults({'k': 2}, {'z': 3}))
        for cls in (Geometry, FlatCAMObj):
            holder = cls.__new__(cls)
            holder.__dict__['app'] = a
            assert holder._app_option('k', 9) == 2
            assert holder._app_option('z', 9) == 3
            assert holder._app_option('none', 9) == 9
        assert camlib.app_option is app_option

    def test_gerber_has_no_get_app_option(self):
        assert not hasattr(Gerber, '_get_app_option')
        assert Gerber._app_option is Geometry._app_option

    def test_script_and_document_objects_keep_their_app_option(self):
        assert hasattr(ScriptObject, '_app_option') and hasattr(DocumentObject, '_app_option')
        assert not issubclass(ScriptObject, Geometry) and not issubclass(DocumentObject, Geometry)


def build_objects(app):
    app.preprocessors = defaultdict(Mock)
    return {
        'Geometry': Geometry(app), 'Gerber': Gerber(app), 'Excellon': Excellon(app), 'CNCjob': CNCjob(app),
        'GerberObject': make_gerber(app), 'ExcellonObject': make_excellon(app),
        'GeometryObject': GeometryObject('geo', app), 'CNCJobObject': CNCJobObject('job', app),
    }


class TestSerAttrsHaveNoDuplicates:
    def test_no_duplicates(self, app):
        for name, obj in build_objects(app).items():
            dups = {a: n for a, n in Counter(obj.ser_attrs).items() if n > 1}
            assert not dups, (name, dups)

    def test_required_keys_still_serialized(self, app):
        objs = build_objects(app)
        for name in ('Gerber', 'GerberObject'):
            assert {'tools', 'solid_geometry', 'follow_geometry', 'units', 'int_digits', 'frac_digits',
                    'aperture_macros', 'source_file', 'export_as_regions'} <= set(objs[name].ser_attrs)
        for name in ('CNCjob', 'CNCJobObject'):
            assert {'kind', 'tools', 'gcode'} <= set(objs[name].ser_attrs)
        assert {'obj_options', 'kind'} <= set(objs['GerberObject'].ser_attrs)

    def test_old_layout_dict_loads_into_new_layout(self, app):
        """A project saved with the old (duplicated) ser_attrs has the same keys: it must load."""
        grb = make_gerber(app)
        new_keys = set(grb.to_dict())
        old_layout = list(grb.ser_attrs) + ['tools', 'solid_geometry']
        grb.ser_attrs = old_layout
        old_dict = grb.to_dict()
        assert set(old_dict) == new_keys

        other = make_gerber(app, name='other')
        other.from_dict(old_dict)
        assert other.units == grb.units
        assert sorted(other.tools) == sorted(grb.tools)

        app.preprocessors = defaultdict(Mock)
        job = CNCJobObject('job', app)
        job_keys = set(job.to_dict())
        job.ser_attrs = list(job.ser_attrs) + ['kind', 'tools']
        assert set(job.to_dict()) == job_keys


class TestProjectRoundTripStillWorks:
    def test_gerber_and_excellon_round_trip(self, app, tmp_path):
        grb = make_gerber(app, name='grb')
        exc = make_excellon(app, name='exc')
        proj, restored = save_and_load(app, [grb, exc], tmp_path)
        assert len(restored) == 2
        grb2, exc2 = restored
        assert grb2.units == grb.units and exc2.units == exc.units
        assert_same_tools(grb.tools, grb2.tools, 'gerber')
        assert_same_tools(exc.tools, exc2.tools, 'excellon')
        saved_keys = set(proj['objs'][0])
        assert {'tools', 'solid_geometry', 'kind', 'obj_options'} <= saved_keys
