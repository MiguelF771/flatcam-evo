"""Bug fixes of the Panelize plugin (Stage 5), headless (driver: test_panelize_plugin.run_plugin)."""

import pytest

from test_unify_characterization import _headless_qt, app, make_excellon, make_gerber, polygon_union  # noqa: F401
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
    # The removed block built a tool table on new_obj.tools and deleted the attribute again at once, so the only
    # lasting effects were multigeo = True and the missing 'tools' attribute. Those are kept; this is not a fix of
    # that quirk.
    src = make_gerber(app, name='g')
    created, _ = run_plugin(app, src, panel_type='geometry', rows=2, cols=2)
    pan = created['g_panelized']
    assert pan.multigeo is True
    assert not hasattr(pan, 'tools')
    assert polygon_union(pan.solid_geometry).area == pytest.approx(4 * polygon_union(src.solid_geometry).area)
    assert all(not isinstance(g, (list, tuple)) for g in pan.solid_geometry)