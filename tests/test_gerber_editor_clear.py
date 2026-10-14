"""The Gerber editor and the clear (LPC) features: an object that holds them is loaded as its composed geometry
(regions), so a save does not lose them. Everything is headless."""

import re
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

from shapely.geometry import Point

from appEditors.appGerberEditor import AppGerberEditor
from appHandlers.appIO import appIO
from appObjects.GerberObject import GerberObject

from test_unify_characterization import (  # noqa: F401  (the fixtures must be visible in this module)
    MIXED_GBR, NATIVE_GBR, _headless_qt, app, assert_poly_equiv, export_gerber_text, make_gerber, polygon_union,
    reparse_gerber, sym_diff_area,
)

SLOT = Point(15, 33)        # on the track of the 6.0 aperture, cut by the clear slot
REFILLED = Point(15, 35)    # the later dark disc fills the middle again


def snapshot(tools):
    """A comparable text of the tools (the addresses of the macro objects are dropped)."""
    return re.sub(r' at 0x[0-9A-Fa-f]+', '', repr(tools))


def editor_save(app, source, tools, loaded_as_regions=False):
    """The real new_edited_gerber() on a stand-in editor, fed with a storage built from ``tools`` (what the editor
    holds after the load)."""
    storage = {}
    for ap, tool in tools.items():
        storage[ap] = {k: v for k, v in tool.items() if k != 'geometry'}
        storage[ap]['geometry'] = [SimpleNamespace(geo=dict(rec)) for rec in tool['geometry']]
    created = {}

    def new_object(kind, name, initialize, *args, **kwargs):
        new_obj = GerberObject(name, app)
        initialize(new_obj, app)
        created[name] = new_obj

    app.app_obj.new_object.side_effect = new_object
    app.app_units = 'MM'
    app.f_handlers = appIO(app)
    app.custom_signal = MagicMock()
    stand_in = SimpleNamespace(app=app, gerber_obj=source, gerber_obj_options=source.obj_options, results=[1],
                               loaded_as_regions=loaded_as_regions)
    AppGerberEditor.new_edited_gerber(stand_in, 'edited', storage)
    return created['edited']


class TestBuildEditTools:
    def test_clear_object_is_loaded_as_its_composed_geometry(self, app):
        source = make_gerber(app, MIXED_GBR)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry)
        assert set(tools) == {0}
        assert tools[0]['type'] == 'REG' and tools[0]['size'] == 0.0
        records = tools[0]['geometry']
        assert records and all('solid' in rec and 'follow' in rec and 'clear' not in rec for rec in records)
        union = polygon_union([rec['solid'] for rec in records])
        assert sym_diff_area(union, source.solid_geometry) < 1e-6
        assert not union.contains(SLOT)
        assert union.contains(REFILLED)

    def test_object_without_clear_is_unchanged(self, app):
        source = make_gerber(app, NATIVE_GBR)
        before = deepcopy(source.tools)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry)
        assert set(tools) == set(before)
        for ap, tool in before.items():
            assert tools[ap]['type'] == tool['type']
            assert [rec['solid'].wkb for rec in tools[ap]['geometry']] == [rec['solid'].wkb for rec in tool['geometry']]

    def test_empty_clear_record_does_not_convert(self, app):
        source = make_gerber(app, NATIVE_GBR)
        source.tools[10]['geometry'][0]['clear'] = None
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry)
        assert tools is source.tools

    def test_source_tools_are_not_changed(self, app):
        for path in (MIXED_GBR, NATIVE_GBR):
            source = make_gerber(app, path)
            before = snapshot(source.tools)
            AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry)
            assert snapshot(source.tools) == before


class TestEditorSaveWithClear:
    def test_loaded_as_regions_keeps_the_composed_geometry(self, app, tmp_path):
        source = make_gerber(app, MIXED_GBR)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry)
        edited = editor_save(app, source, tools, loaded_as_regions=True)
        assert_poly_equiv(edited.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='edited')
        union = polygon_union(edited.solid_geometry)
        assert not union.contains(SLOT) and union.contains(REFILLED)
        assert edited.export_as_regions is True
        again = reparse_gerber(app, edited.source_file, tmp_path)
        assert_poly_equiv(again.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='source text')
        exported = reparse_gerber(app, export_gerber_text(app, edited), tmp_path, name='exported')
        assert_poly_equiv(exported.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='export')

    def test_without_the_flag_the_native_region_writer_is_still_right(self, app, tmp_path):
        source = make_gerber(app, MIXED_GBR)
        tools = AppGerberEditor.build_edit_tools(source.tools, source.solid_geometry)
        edited = editor_save(app, source, tools)
        assert edited.export_as_regions is False
        again = reparse_gerber(app, edited.source_file, tmp_path)
        assert_poly_equiv(again.solid_geometry, source.solid_geometry, rel=1e-4, abs_tol=1e-3, msg='native REG')

    def test_pin_old_load_path_loses_the_clear_features(self, app):
        """The load path before the fix: the source tools as they are. The slot is lost on a save."""
        source = make_gerber(app, MIXED_GBR)
        edited = editor_save(app, source, source.tools)
        union = polygon_union(edited.solid_geometry)
        assert union.contains(SLOT), 'the old path is expected to lose the slot'
