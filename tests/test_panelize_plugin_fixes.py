"""Bug fixes of the Panelize plugin (Stage 5), headless (driver: test_panelize_plugin.run_plugin)."""

import pytest
from shapely.geometry import LineString, box

from appHandlers.appEdit import appEditor
from appObjects.GeometryObject import GeometryObject
from test_unify_characterization import (  # noqa: F401
    _headless_qt, app, make_excellon, make_gerber, polygon_union, reparse_gerber)
from test_panelize_plugin import run_plugin


# ---------------------------------------------------------------------------------------------------------------
# messages printed the object (None) instead of its name
# ---------------------------------------------------------------------------------------------------------------

def test_object_not_found_message_names_the_object(app):
    src = make_gerber(app, name='g')
    created, informs = run_plugin(app, src, name='missing_object')
    assert not created
    assert any('Object not found' in m and m.endswith(': missing_object') for m in informs)
    assert not any('None' in m for m in informs)


def test_no_box_message_names_the_source_object(app):
    src = make_gerber(app, name='g')
    created, informs = run_plugin(app, src, box_name='missing_box', rows=1, cols=2)
    # no box: the plugin falls back to the bounding box of the source and goes on
    assert 'g_panelized' in created
    warnings = [m for m in informs if 'No object Box' in m]
    assert warnings and all(m.endswith(': g') for m in warnings) and not any('None' in m for m in warnings)


# ---------------------------------------------------------------------------------------------------------------
# the source Excellon got empty 'drills' / 'slots' keys
# ---------------------------------------------------------------------------------------------------------------

def test_source_excellon_without_slots_or_drills_keys_is_not_changed(app):
    src = make_excellon(app, name='e')
    for tool in src.tools.values():
        del tool['slots']
    del src.tools[2]['drills']
    keys_before = {tid: sorted(tool) for tid, tool in src.tools.items()}
    created, _ = run_plugin(app, src)
    assert {tid: sorted(tool) for tid, tool in src.tools.items()} == keys_before
    # the panel still has both keys everywhere
    assert all('drills' in t and 'slots' in t for t in created['e_panelized'].tools.values())


# ---------------------------------------------------------------------------------------------------------------
# 'Done' was shown before the worker ran
# ---------------------------------------------------------------------------------------------------------------

def test_done_message_comes_from_the_job_not_from_the_gui_thread(app):
    src = make_gerber(app, name='g')
    before = []
    created, informs = run_plugin(app, src, rows=1, cols=2, before=before)
    assert 'g_panelized' in created
    assert not any('Done' in m or 'successfully' in m for m in before)
    assert informs[-1] == '[success] Panel created successfully.'


def test_no_success_message_when_the_job_fails(app):
    src = make_gerber(app, name='g')
    created, informs = run_plugin(app, src, rows=1, cols=2, fail_job=True)
    assert not created
    assert not any('Done' in m or 'successfully' in m for m in informs)
    assert any('boom' in str(c) for c in app.log.error.call_args_list)

def test_constrain_warning_comes_from_the_job_with_the_final_grid(app):
    src = make_gerber(app, name='g')
    xmin, ymin, xmax, ymax = src.bounds()
    w, h = xmax - xmin, ymax - ymin
    before = []
    created, informs = run_plugin(app, src, rows=3, cols=4, constrain=(2 * w + 5.5, h + 1.0), before=before)
    assert not any('constrain' in m for m in before)
    assert informs[-1].startswith('[WARNING]') and '2 columns and 1 rows' in informs[-1]

# ---------------------------------------------------------------------------------------------------------------
# the dead tool-table block of the Gerber -> Geometry panel
# ---------------------------------------------------------------------------------------------------------------

def test_gerber_to_geometry_panel_result_is_the_translated_copper_only(app):
    # The panel gets the tool table of the Geometry (like convert_any2geo); every tool holds the translated copper.
    src = make_gerber(app, name='g')
    created, _ = run_plugin(app, src, panel_type='geometry', rows=2, cols=2)
    pan = created['g_panelized']
    src_area = polygon_union(src.solid_geometry).area
    assert pan.multigeo is True
    assert pan.tools
    for tool in pan.tools.values():
        assert polygon_union(tool['solid_geometry']).area == pytest.approx(4 * src_area)
    assert polygon_union(pan.solid_geometry).area == pytest.approx(4 * src_area)
    assert all(not isinstance(g, (list, tuple)) for g in pan.solid_geometry)
    assert all(not isinstance(g, (list, tuple)) for tool in pan.tools.values() for g in tool['solid_geometry'])


def test_gerber_to_geometry_panel_has_tools_options_and_source(app):
    src = make_gerber(app, name='g')
    created, _ = run_plugin(app, src, panel_type='geometry', rows=1, cols=2)
    pan = created['g_panelized']
    assert pan.tools
    for tool in pan.tools.values():
        assert 'tooldia' in tool and isinstance(tool['data'], dict) and tool['data']
    first = pan.tools[min(pan.tools)]['tooldia']
    assert pan.obj_options['tools_mill_tooldia'] == first
    # a Geometry keeps its source as a DXF document
    assert pan.source_file and len(pan.source_file.modelspace()) > 0


def test_build_geometry_tool_table_matches_the_convert_any2geo_rules(app):
    editor = appEditor(app)
    app.options['tools_mill_tooldia'] = '0.5, 0.2'
    app.options['geometry_cutz'] = -1.25
    default_data, tools, dias = editor.build_geometry_tool_table()
    assert dias == [0.5, 0.2] and list(tools) == [1, 2]
    assert default_data['cutz'] == -1.25 and 'geometry_cutz' not in default_data
    for tid, dia in zip(tools, dias):
        tool = tools[tid]
        assert tool['tooldia'] == dia and tool['solid_geometry'] == []
        assert (tool['offset'], tool['offset_value'], tool['type'], tool['tool_type']) == ('Path', 0.0, 'Rough', 'C1')
        assert tool['data'] == default_data and tool['data'] is not default_data
    app.options['tools_mill_tooldia'] = 0.8
    assert editor.build_geometry_tool_table()[2] == [0.8]
    app.options['tools_mill_tooldia'] = 'not a number'
    assert editor.build_geometry_tool_table() is None


# ---------------------------------------------------------------------------------------------------------------
# Geometry -> Gerber panel keeps its tools and a source file
# ---------------------------------------------------------------------------------------------------------------

def make_poly_geometry(app, multigeo):
    """A Geometry made of polygons only: 3 elements."""
    src = GeometryObject('src', app)
    src.multigeo = multigeo
    src.units = 'MM'
    polys = [box(0, 0, 4, 4), box(6, 0, 8, 2), box(10, 0, 12, 2)]
    src.solid_geometry = list(polys)
    if multigeo:
        src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': polys[:2]},
                     2: {'tooldia': 2.0, 'data': {}, 'solid_geometry': polys[2:]}}
    else:
        src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': []}}
    return src


@pytest.mark.parametrize('multigeo', [False, True], ids=['single-geo', 'multigeo'])
def test_geometry_to_gerber_panel_keeps_the_records_and_has_a_source(app, multigeo):
    src = make_poly_geometry(app, multigeo)
    created, _ = run_plugin(app, src, panel_type='gerber', rows=2, cols=2)
    pan = created['src_panelized']
    assert pan.tools[0]['type'] == 'REG'
    records = pan.tools[0]['geometry']
    assert len(records) == 4 * 3
    assert polygon_union([r['solid'] for r in records]).symmetric_difference(
        polygon_union(pan.solid_geometry)).area < 1e-6
    assert pan.multigeo is True and pan.multitool is False and pan.follow is False
    assert pan.export_as_regions is False
    assert isinstance(pan.source_file, str) and pan.source_file.strip()


@pytest.mark.parametrize('multigeo', [False, True], ids=['single-geo', 'multigeo'])
def test_geometry_to_gerber_panel_source_round_trips(app, tmp_path, multigeo):
    src = make_poly_geometry(app, multigeo)
    created, _ = run_plugin(app, src, panel_type='gerber', rows=2, cols=2)
    pan = created['src_panelized']
    again = reparse_gerber(app, pan.source_file, tmp_path)
    assert polygon_union(again.solid_geometry).symmetric_difference(polygon_union(pan.solid_geometry)).area < 1e-3
    assert polygon_union(pan.solid_geometry).area == pytest.approx(4 * (16 + 4 + 4))


def test_geometry_to_gerber_panel_with_a_line_exports_as_regions(app, tmp_path):
    src = make_poly_geometry(app, False)
    src.solid_geometry = [box(0, 0, 4, 4), LineString([(6, 0), (9, 3)])]
    created, _ = run_plugin(app, src, panel_type='gerber', rows=1, cols=2)
    pan = created['src_panelized']
    assert pan.export_as_regions is True
    assert isinstance(pan.source_file, str) and pan.source_file.strip()
    again = reparse_gerber(app, pan.source_file, tmp_path)
    assert polygon_union(again.solid_geometry).area == pytest.approx(2 * 16)


def test_geometry_to_gerber_panel_does_not_fill_an_open_line_of_a_single_geo_source(app, tmp_path):
    # Polygon() accepts an open line of 3 points; the record must still hold the line that the solid holds
    src = make_poly_geometry(app, False)
    src.solid_geometry = [box(0, 0, 4, 4), LineString([(6, 0), (9, 0), (9, 3)])]
    created, _ = run_plugin(app, src, panel_type='gerber', rows=1, cols=2)
    pan = created['src_panelized']
    assert sum(isinstance(r['solid'], LineString) for r in pan.tools[0]['geometry']) == 2
    assert pan.export_as_regions is True
    again = reparse_gerber(app, pan.source_file, tmp_path)
    assert polygon_union(again.solid_geometry).area == pytest.approx(2 * 16)
