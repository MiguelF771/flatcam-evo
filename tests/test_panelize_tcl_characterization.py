"""Characterization of the Tcl ``panelize`` command (Stage 5 item 3), captured BEFORE the panelize core refactor.

TclCommandPanelize.execute is driven headless: the mock app's ``new_object`` runs the init callback on a real, empty
GerberObject / ExcellonObject (same approach as test_stage4_join_gerber_tcl.py). The numbers below were captured from
the pre-refactor code (2 rows x 3 columns, spacing 5 / 5 for Gerber, default spacing for Excellon).

Only the nesting of a Geometry panel's solid_geometry may change in the refactor (not covered here: no Geometry
fixture); everything asserted here must stay the same.
"""

from unittest.mock import Mock

import pytest

from appHandlers.appIO import appIO
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from tclCommands.TclCommandPanelize import TclCommandPanelize

from test_unify_characterization import (  # noqa: F401  (fixtures)
    NATIVE_GBR, _headless_qt, app, make_excellon, make_gerber, parts, polygon_union)

ROWS, COLS = 2, 3


def run_panelize(app, src, *argv):
    """Runs the command. Returns (result, created) where created = {name: object built by the init callback}."""
    app.f_handlers = appIO(app)
    created = {}

    def new_object(kind, name, initialize, plot=True, **kwargs):
        cls = {'gerber': GerberObject, 'excellon': ExcellonObject, 'geometry': GeometryObject}[kind]
        obj = cls(name, app)
        initialize(obj, app)
        created[name] = obj
        return 'ok'

    app.collection.get_by_name = Mock(side_effect=lambda n: src if n == src.obj_options['name'] else None)
    app.app_obj.new_object = Mock(side_effect=new_object)
    cmd = TclCommandPanelize(app)
    args, unnamed = cmd.check_args(tuple(argv))
    return cmd.execute(args, unnamed), created


GERBER_CASES = {
    # path: (source bounds, panel bounds, source union area, panel union area, solid parts, records per aperture)
    'mixed': (None,
              (-0.5002501250625312, 4.0, 31.0, 38.001500750375186),
              (-0.5002501250625312, 4.0, 104.00050025012507, 77.00300150075037),
              271.27037912815194, 1627.6222747689121, 54,
              {10: 18, 11: 6, 12: 6, 13: 6, 14: 6, 15: 6, 16: 6, 17: 6, 0: 6}),
    'native': (NATIVE_GBR,
               (-0.500250125, 4.4999999, 30.500250125, 38.00150075),
               (-0.500250125, 4.4999999, 102.501250625, 76.5030016),
               268.6339022546699, 1611.8034135280186, 36,
               {10: 12, 11: 6, 12: 6, 16: 6, 0: 6}),
}


@pytest.mark.parametrize('case', sorted(GERBER_CASES))
class TestGerberPanel:
    def build(self, app, case):
        path = GERBER_CASES[case][0]
        src = make_gerber(app, name='g') if path is None else make_gerber(app, path, name='g')
        res, created = run_panelize(app, src, 'g', '-rows', str(ROWS), '-columns', str(COLS),
                                    '-spacing_columns', '5', '-spacing_rows', '5', '-outname', 'p')
        assert res is None and list(created) == ['p']
        return src, created['p']

    def test_bounds_and_area(self, app, case):
        _, src_b, pan_b, src_area, pan_area, n_parts, _ = GERBER_CASES[case]
        src, pan = self.build(app, case)
        assert src.bounds() == pytest.approx(src_b)
        assert pan.bounds() == pytest.approx(pan_b)
        assert polygon_union(src.solid_geometry).area == pytest.approx(src_area)
        assert polygon_union(pan.solid_geometry).area == pytest.approx(pan_area)
        assert pan_area == pytest.approx(ROWS * COLS * src_area)
        assert len(parts(pan.solid_geometry)) == n_parts

    def test_records_per_aperture(self, app, case):
        counts = GERBER_CASES[case][6]
        src, pan = self.build(app, case)
        assert {k: len(v['geometry']) for k, v in pan.tools.items()} == counts
        assert list(pan.tools) == list(src.tools)
        for apid, tool in src.tools.items():
            assert len(pan.tools[apid]['geometry']) == ROWS * COLS * len(tool['geometry'])

    def test_aperture_metadata(self, app, case):
        src, pan = self.build(app, case)
        for apid, tool in src.tools.items():
            for key, value in tool.items():
                if key == 'geometry':
                    continue
                assert key in pan.tools[apid]
                if key == 'macro':
                    assert type(pan.tools[apid][key]) is type(value)
                else:
                    assert pan.tools[apid][key] == value
        # the panel owns its own copy of the apertures
        assert pan.tools is not src.tools
        assert pan.tools[10] is not src.tools[10]

    def test_object_attributes(self, app, case):
        src, pan = self.build(app, case)
        assert pan.obj_options['name'] == 'p'
        assert pan.kind == 'gerber'
        assert pan.export_as_regions is False
        assert pan.source_file.startswith('G04*')
        # the source is not touched
        assert polygon_union(src.solid_geometry).area == pytest.approx(GERBER_CASES[case][3])
        assert {k: len(v['geometry']) for k, v in src.tools.items()} != {}

    def test_clear_records_are_kept_per_copy(self, app, case):
        src, pan = self.build(app, case)
        src_clear = sum(1 for t in src.tools.values() for r in t['geometry'] if 'clear' in r)
        pan_clear = sum(1 for t in pan.tools.values() for r in t['geometry'] if 'clear' in r)
        assert pan_clear == ROWS * COLS * src_clear
        # every record keeps its follow geometry
        assert all('follow' in r for t in pan.tools.values() for r in t['geometry'])


class TestExcellonPanel:
    def build(self, app):
        src = make_excellon(app, name='e')
        res, created = run_panelize(app, src, 'e', '-rows', str(ROWS), '-columns', str(COLS), '-outname', 'q')
        assert res is None and list(created) == ['q']
        return src, created['q']

    def test_bounds_area_counts(self, app):
        src, pan = self.build(app)
        assert src.bounds() == pytest.approx((4.25, 4.6, 28.5, 26.75))
        assert pan.bounds() == pytest.approx((4.25, 4.6, 87.0, 53.9))
        assert polygon_union(pan.solid_geometry).area == pytest.approx(210.90528132700658)
        assert len(parts(pan.solid_geometry)) == 54
        assert {k: (len(v['drills']), len(v['slots']), len(parts(v['solid_geometry'])))
                for k, v in pan.tools.items()} == {1: (18, 0, 18), 2: (12, 6, 18), 3: (6, 12, 18)}

    def test_positions_row_major(self, app):
        _, pan = self.build(app)
        drills_1 = [(p.x, p.y) for p in pan.tools[1]['drills']]
        assert drills_1[:8] == pytest.approx([(5.0, 5.0), (10.0, 5.0), (15.0, 5.0), (34.25, 5.0), (39.25, 5.0),
                                              (44.25, 5.0), (63.5, 5.0), (68.5, 5.0)])
        slots_2 = [((a.x, a.y), (b.x, b.y)) for a, b in pan.tools[2]['slots']]
        assert slots_2[:4] == pytest.approx([((20.0, 12.0), (28.0, 12.0)), ((49.25, 12.0), (57.25, 12.0)),
                                             ((78.5, 12.0), (86.5, 12.0)), ((20.0, 39.15), (28.0, 39.15))])
        # the 2nd row starts (box height + 5) = 27.15 above the first
        assert pan.tools[3]['drills'][3].y == pytest.approx(22.0 + 27.15)

    def test_metadata_and_independence(self, app):
        src, pan = self.build(app)
        assert pan.kind == 'excellon' and pan.multitool is True
        assert pan.units == src.units and pan.zeros == src.zeros
        assert pan.obj_options['name'] == 'q'
        assert pan.source_file.startswith('M48')
        for tid, tool in src.tools.items():
            assert pan.tools[tid]['tooldia'] == tool['tooldia']
        assert pan.tools is not src.tools
        assert {k: (len(v['drills']), len(v['slots'])) for k, v in src.tools.items()} == \
            {1: (3, 0), 2: (2, 1), 3: (1, 2)}
        assert all(isinstance(s, tuple) and len(s) == 2 for t in pan.tools.values() for s in t['slots'])

# ---------------------------------------------------------------------------------------------------------------
# Changed on purpose by the panelize core (Stage 5): not part of the pre-refactor capture above
# ---------------------------------------------------------------------------------------------------------------

def test_gerber_panel_inherits_export_as_regions_and_macros(app):
    src = make_gerber(app, name='g')
    src.export_as_regions = True
    res, created = run_panelize(app, src, 'g', '-rows', '1', '-columns', '2', '-outname', 'p')
    pan = created['p']
    assert pan.export_as_regions is True
    assert set(pan.aperture_macros) == set(src.aperture_macros) and pan.aperture_macros


def make_geometry(app, multigeo):
    from shapely.geometry import LineString, box
    src = GeometryObject('src', app)
    src.multigeo = multigeo
    src.units = 'MM'
    src.solid_geometry = [box(0, 0, 10, 10), [LineString([(0, 0), (10, 10)]), [box(2, 2, 3, 3)]]]
    src.tools = {1: {'tooldia': 1.0, 'data': {}, 'solid_geometry': [LineString([(0, 0), (10, 0)])]}}
    return src


@pytest.mark.parametrize('multigeo', [False, True])
def test_geometry_panel_solid_geometry_is_flat(app, multigeo):
    # the old Tcl output nested the lists (one list per copy / per source list); the plugin always flattened
    src = make_geometry(app, multigeo)
    res, created = run_panelize(app, src, 'src', '-rows', '2', '-columns', '2', '-outname', 'p')
    pan = created['p']
    assert res is None
    assert isinstance(pan.solid_geometry, list)
    assert not any(isinstance(g, (list, tuple)) for g in pan.solid_geometry)
    assert len(pan.solid_geometry) == 4 * 3
    if multigeo:
        # bounds() of a multigeo object looks at the tools only: a 10 x 0 box, pitch (15, 5)
        assert pan.bounds() == pytest.approx((0, 0, 25, 5))
        assert len(pan.tools[1]['solid_geometry']) == 4
    else:
        assert pan.bounds() == pytest.approx((0, 0, 25, 25))
        assert len(src.tools[1]['solid_geometry']) == 1
