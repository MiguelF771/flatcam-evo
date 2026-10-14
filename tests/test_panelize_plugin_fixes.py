"""Bug fixes of the Panelize plugin (Stage 5), headless (driver: test_panelize_plugin.run_plugin)."""

from test_unify_characterization import _headless_qt, app, make_excellon, make_gerber  # noqa: F401
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
