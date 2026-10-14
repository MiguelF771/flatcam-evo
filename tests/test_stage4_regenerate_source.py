"""Stage 4: FlatCAMObj.regenerate_source() replaces the repeated
``obj.source_file = f_handlers.export_xxx(..., local_use=obj, use_thread=False)`` call. Headless."""

from unittest.mock import patch

import pytest
from PyQt6 import QtWidgets

from appHandlers.appIO import appIO
from appObjects.AppObjectTemplate import FlatCAMObj
from appObjects.CNCJobObject import CNCJobObject
from appObjects.DocumentObject import DocumentObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appObjects.ScriptObject import ScriptObject
from tests.test_unify_characterization import (assert_poly_equiv, make_app, make_excellon, make_gerber, pts,
                                               reparse_excellon, reparse_gerber)


@pytest.fixture(scope='module', autouse=True)
def _headless_qt():
    qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    with patch('appObjects.AppObjectTemplate.ShapeCollection'):
        yield qapp


@pytest.fixture
def app():
    app = make_app()
    app.f_handlers = appIO(app)
    return app


def test_exporter_per_kind():
    assert GerberObject.source_exporter == 'export_gerber'
    assert ExcellonObject.source_exporter == 'export_excellon'
    assert GeometryObject.source_exporter == 'export_dxf'
    for cls in (CNCJobObject, ScriptObject, DocumentObject, FlatCAMObj):
        assert cls.source_exporter is None


def test_gerber_regenerated_text_parses_back_to_the_same_geometry(app, tmp_path):
    grb = make_gerber(app)
    grb.offset((2.0, 1.0))
    grb.source_file = 'stale'
    assert grb.regenerate_source() is True
    assert grb.source_file != 'stale' and grb.source_file.startswith('G04*')
    again = reparse_gerber(app, grb.source_file, tmp_path)
    assert_poly_equiv(again.solid_geometry, grb.solid_geometry, rel=1e-6, abs_tol=1e-4, msg='regenerated gerber')


def test_excellon_regenerated_text_parses_back_to_the_same_geometry(app, tmp_path):
    exc = make_excellon(app)
    exc.offset((3.5, 2.25))
    exc.source_file = 'stale'
    assert exc.regenerate_source() is True
    assert exc.source_file.startswith('M48')
    again = reparse_excellon(app, exc.source_file, tmp_path)
    for t, tool in exc.tools.items():
        assert pts(again.tools[t]['drills']) == pytest.approx(pts(tool['drills']), abs=1e-3)


def test_local_use_export_runs_without_a_thread_and_writes_no_file(app):
    grb = make_gerber(app)
    app.f_handlers.export_gerber = lambda **kw: calls.append(kw) or 'G04*'
    calls = []
    assert grb.regenerate_source(name='other') is True
    assert calls == [dict(obj_name='other', filename=None, local_use=grb, use_thread=False)]
    assert grb.source_file == 'G04*'
    app.file_saved.emit.assert_not_called()


def test_name_defaults_to_the_object_name(app):
    exc = make_excellon(app, name='my_exc')
    seen = []
    app.f_handlers.export_excellon = lambda **kw: seen.append(kw['obj_name']) or 'M48'
    exc.regenerate_source()
    assert seen == ['my_exc']


@pytest.mark.parametrize('bad', ['fail', None])
def test_failed_export_keeps_the_old_source(app, bad):
    grb = make_gerber(app)
    grb.source_file = 'old text'
    app.f_handlers.export_gerber = lambda **kw: bad
    assert grb.regenerate_source() is False
    assert grb.source_file == 'old text'
    assert 'old source_file is kept' in app.log.warning.call_args[0][0]


def test_real_export_failure_keeps_the_old_source(app):
    """An exception inside make_excellon() makes the real export return 'fail'."""
    exc = make_excellon(app)
    exc.source_file = 'old text'
    exc.tools = None   # iterating the tools raises inside the export
    assert exc.regenerate_source() is False
    assert exc.source_file == 'old text'


@pytest.mark.parametrize('cls', [ScriptObject, DocumentObject])
def test_kinds_without_an_exporter_do_nothing(app, cls):
    obj = cls('o', app)
    obj.source_file = 'keep'
    assert obj.regenerate_source() is False
    assert obj.source_file == 'keep'


def test_geometry_stores_the_dxf_export(app):
    geo = GeometryObject('geo', app)
    doc = object()
    app.f_handlers.export_dxf = lambda **kw: doc
    assert geo.regenerate_source() is True
    assert geo.source_file is doc
