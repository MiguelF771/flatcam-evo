"""
End-to-end Levelling Tool tests, one user journey per controller of the
`al_controller_combo` (MACH3, MACH4, LinuxCNC, GRBL).

Real code: Gerber parser, the CNCjob (camlib) parse of the source G-code,
ToolLevelling (object selection, controller change, probe points, probing
G-code, export, height-map import, GRBL probe run, result publishing,
autolevelling, creation of the '<name>_levelled' CNCJob).
Patched: the widgets (MagicMock UI), the worker dispatch (runs the job
inline), the file dialogs, the serial port (FakeGrbl), the canvas shapes
and the object factory (a registry that runs the real obj_init).

Run: .venv/Scripts/python.exe -m pytest tests/test_levelling_e2e.py -q
"""
import copy
import re
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from shapely.ops import unary_union

from tests import test_levelling_audit as audit
from tests import test_levelling_journey as journey
from tests.test_levelling_audit import FakeGrbl, make_audit_tool, tl_module

PLANE = journey.make_plane(0.01, 0.02, 0.0)       # z = 0.01*x + 0.02*y
TOL = 3e-4
COLS, ROWS = 4, 3

CONTROLLERS = ['GRBL', 'MACH3', 'MACH4', 'LinuxCNC']
FIXTURES = {
    'simple_line': journey.SIMPLE_LINE_GBR,
    'region': journey.REGION_GBR,
    'flash': journey.FLASH_GBR,
}

# controller -> (probe command, open command, close command)
PROBE_WORDS = {
    'MACH3': ('G31', 'M40', 'M41'),
    'MACH4': ('G31', 'M40', 'M41'),
    'LinuxCNC': ('G38.2', '(PROBEOPEN a_probing_points_file.txt)', '(PROBECLOSE)'),
}
HEIGHT_MAP_WRITERS = {
    'MACH3': journey.write_mach3,                    # x,y,z
    'MACH4': journey.write_mach4_extra_columns,      # x, y, z, 0.0, 0.0, 0.0
    'LinuxCNC': journey.write_linuxcnc,              # x y z 0 0 0 0 0 0
}
# (controller, method, fixture): every controller, both methods, all 3 fixtures
FILE_CASES = [
    ('MACH3', 'b', 'simple_line'),
    ('MACH3', 'v', 'region'),
    ('MACH4', 'b', 'region'),
    ('MACH4', 'v', 'flash'),
    ('LinuxCNC', 'b', 'flash'),
    ('LinuxCNC', 'v', 'simple_line'),
]
GRBL_CASES = [('b', 'simple_line'), ('v', 'region')]

_TC = unittest.TestCase()   # reuse the independent G-code verifier of the journey tests


def test_all_ui_controllers_are_covered():
    """The list of the UI combo must be the list this file parametrizes over."""
    import inspect
    src = inspect.getsource(tl_module.LevelUI.__init__)
    m = re.search(r'al_controller_combo\.addItems\(\[([^\]]*)\]\)', src)
    ui_list = re.findall(r'"([^"]+)"', m.group(1))
    assert sorted(ui_list) == sorted(CONTROLLERS)
    assert sorted({c for c, _m, _f in FILE_CASES} | {'GRBL'}) == sorted(CONTROLLERS)


# --------------------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------------------

def make_source_job(gerber_path, name):
    """A real camlib.CNCjob with G-code made from a real Gerber file."""
    paths = journey.gerber_toolpaths(gerber_path)
    gcode = journey.make_gcode(paths, include_arc=False)
    job = journey.make_real_cncjob()
    job.obj_options = {'name': name, 'type': 'Geometry'}
    job.kind = 'cncjob'
    job.units = 'MM'
    job.coords_decimals = 4
    job.fr_decimals = 2
    job.pp_geometry_name = 'default'
    job.pp_excellon_name = 'default'
    job.pp_solderpaste_name = None
    job.exc_tools = {}
    job.used_tools = [1]
    job.gcode = gcode
    parsed = job.gcode_parse(tool_data={})
    job.tools = {1: {'gcode': gcode, 'gcode_parsed': parsed, 'data': {}, 'tooldia': 1.0}}
    job.gcode_parsed = parsed
    job.create_geometry()
    return job


class Env:
    """The tool with a mocked UI/app around the real logic, plus a project registry."""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp_path = tmp_path
        self.objects = {}
        tool = make_audit_tool(units='MM')
        self.tool = tool
        app = tool.app
        app.use_3d_engine = True
        app.defaults = {}
        app.options = {'cncjob_save_filters': 'Text (*.txt)', 'cncjob_line_ending': False}
        app.version = '9.9'
        app.version_date = '2026/10/12'
        app.get_last_save_folder.return_value = str(tmp_path)
        app.get_last_folder.return_value = str(tmp_path)
        app.dec_format.side_effect = lambda value, dec=4: round(value, dec)
        app.collection.get_by_name.side_effect = lambda n: self.objects.get(n)
        app.collection.get_list.return_value = []
        app.app_obj.new_object.side_effect = self._new_object
        # the worker runs inline
        app.worker_task.emit.side_effect = lambda d: d['fcn'](*d.get('params', []))
        # the apply step is the real one
        tool.apply_autolevel_sig = MagicMock()
        tool.plot_probing_geo = MagicMock()
        tool.on_grbl_search_ports = MagicMock()
        monkeypatch.setattr(tl_module, 'ShapeCollection', MagicMock())

        ui = tool.ui
        ui.al_mode_radio.get_value.return_value = 'grid'
        ui.avoid_exc_holes_cb.get_value.return_value = False
        ui.avoid_exc_holes_size_entry.get_value.return_value = 0.5
        ui.probe_tip_dia_entry.get_value.return_value = 0.2
        ui.al_columns_entry.get_value.return_value = COLS
        ui.al_rows_entry.get_value.return_value = ROWS
        ui.ptravelz_entry.get_value.return_value = 2.0
        ui.feedrate_probe_entry.get_value.return_value = 50.0
        ui.pdepth_entry.get_value.return_value = -1.0

        self.save_path = tmp_path / 'probing.nc'
        monkeypatch.setattr(
            tl_module, 'FCFileSaveDialog',
            SimpleNamespace(get_saved_filename=lambda **kw: (str(self.save_path), '')))

    def _new_object(self, kind, name, obj_init, *args, **kwargs):
        new_obj = journey.make_real_cncjob()
        new_obj.obj_options = {'name': name, 'type': 'Geometry'}
        new_obj.kind = kind
        if obj_init(new_obj, self.tool.app) == 'fail':
            return 'fail'
        self.objects[name] = new_obj
        return 'success'

    # ---- user steps ----
    def load_cncjob(self, fixture, name):
        job = make_source_job(FIXTURES[fixture], name)
        self.objects[name] = job
        self.source_name = name
        self.snapshot = {
            'gcode': job.tools[1]['gcode'],
            'parsed_len': len(job.tools[1]['gcode_parsed']),
            'tools': copy.deepcopy({k: v['gcode'] for k, v in job.tools.items()}),
        }
        return job

    def select(self, name, controller, method):
        ui = self.tool.ui
        ui.object_combo.currentText.return_value = name
        ui.object_combo.get_value.return_value = name
        self.tool.on_object_changed()
        ui.al_method_radio.get_value.return_value = method
        self.tool.on_method_radio(method)
        ui.al_controller_combo.get_value.return_value = controller
        self.tool.on_controller_change()

    def add_points(self):
        assert self.tool.on_add_al_probepoints() != 'fail'
        storage = self.tool.al_voronoi_geo_storage
        assert len(storage) == COLS * ROWS
        return [(v['point'].x, v['point'].y) for v in storage.values()]

    def messages(self):
        return audit.emitted(self.tool)

    def errors(self):
        return [m for m in self.messages() if '[ERROR_NOTCL]' in m]

    def levelled_name(self):
        return self.source_name + '_levelled'

    # ---- checks ----
    def assert_no_levelled(self):
        assert self.levelled_name() not in self.objects
        assert not any(n.endswith('_levelled') for n in self.objects)

    def assert_levelled(self, method, probe_points):
        """New '<source>_levelled' CNCJob; source untouched; Z = source Z + plane height."""
        name = self.levelled_name()
        assert name in self.objects, 'no levelled object; messages: %r' % self.messages()
        new, src = self.objects[name], self.objects[self.source_name]
        assert new is not src and new.obj_options['name'] == name
        assert new.kind == 'cncjob'

        # the source CNCJob is unchanged
        assert src.tools[1]['gcode'] == self.snapshot['gcode']
        assert src.gcode == self.snapshot['gcode']
        assert {k: v['gcode'] for k, v in src.tools.items()} == self.snapshot['tools']
        assert len(src.tools[1]['gcode_parsed']) == self.snapshot['parsed_len']

        if method == 'b':
            offset_fn = PLANE
        else:
            pts = [(x, y, PLANE(x, y)) for x, y in probe_points]

            def offset_fn(x, y):
                return journey.nearest_height_bruteforce(pts, x, y)

        max_dz, cut_points = journey.verify_levelling(
            _TC, self.snapshot['gcode'], new.tools[1]['gcode'], 4, offset_fn, tol=TOL)
        assert cut_points, 'no cut moves verified'
        assert max_dz > 1e-3, 'the levelled G-code is not changed'
        assert new.gcode == new.tools[1]['gcode']

        # parsed geometry exists
        parsed = new.tools[1]['gcode_parsed']
        assert len(parsed) > 0 and len(new.gcode_parsed) == len(parsed)
        assert all(g['geom'].geom_type == 'LineString' for g in parsed)
        assert new.solid_geometry is not None and len(new.solid_geometry) > 0
        assert any('[success]' in m for m in self.messages())
        return new


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


# --------------------------------------------------------------------------------------
# G-code and height-map helpers
# --------------------------------------------------------------------------------------

WORD_LINE = re.compile(r'([A-Z]\s*[-+]?\d+(\.\d+)?\s*)+')


def check_probing_file(path, controller, probe_points):
    text = path.read_text()
    probe_cmd, open_cmd, close_cmd = PROBE_WORDS[controller]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    for ln in lines:
        if ln.startswith('('):
            assert ln.endswith(')'), 'broken comment: %r' % ln
        else:
            assert WORD_LINE.fullmatch(ln) or ln == '#', 'plain prose / not G-code: %r' % ln
    assert lines.count(open_cmd) == 1 and lines.count(close_cmd) == 1
    assert lines[-1] == 'M2'
    for other_cmd in {'G31', 'G38.2'} - {probe_cmd}:
        assert not any(ln.startswith(other_cmd + ' ') for ln in lines)

    # a probe move for every point, in order, right after the move to X/Y
    moves, probes = [], 0
    for i, ln in enumerate(lines):
        m = re.fullmatch(r'G0 X(-?[\d.]+)Y(-?[\d.]+)', ln)
        if m:
            nxt = lines[i + 1]
            assert re.fullmatch(re.escape(probe_cmd) + r' Z-[\d.]+ F[\d.]+', nxt), nxt
            moves.append((float(m.group(1)), float(m.group(2))))
            probes += 1
    assert probes == len(probe_points)
    for (mx, my), (px, py) in zip(moves, probe_points):
        assert mx == pytest.approx(px, abs=1e-4) and my == pytest.approx(py, abs=1e-4)
    return text


def write_height_map(path, controller, probe_points, drop_last=False, shuffle=False):
    rows = [(x, y, PLANE(x, y)) for x, y in probe_points]
    if shuffle:
        rows = journey.shuffled_rows(rows, seed=7)
    if drop_last:
        rows = rows[:-1]
    HEIGHT_MAP_WRITERS[controller](str(path), rows, decimals=5)


def import_through_button(env, path):
    with patch.object(tl_module.QtWidgets.QFileDialog, 'getOpenFileName', return_value=(str(path), '')):
        env.tool.on_import_height_map()


# --------------------------------------------------------------------------------------
# MACH3 / MACH4 / LinuxCNC
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize('controller,method,fixture', FILE_CASES)
def test_file_controller_full_journey(env, controller, method, fixture):
    """Gerber -> CNCJob -> select -> probing G-code -> export -> height map -> import -> levelled job."""
    env.load_cncjob(fixture, 'job_' + fixture)
    env.select('job_' + fixture, controller, method)
    points = env.add_points()

    # probing G-code through the real export path (dialog patched to a tmp file)
    assert env.tool.probing_gcode_text
    env.tool.on_save_probing_gcode()
    text = check_probing_file(env.save_path, controller, points)
    assert text == env.tool.probing_gcode_text

    # the controller writes a height map; the import button reads it
    hm = env.tmp_path / 'heights.txt'
    write_height_map(hm, controller, points, shuffle=(method == 'v'))
    env.assert_no_levelled()
    import_through_button(env, hm)

    assert env.tool.al_heights_valid
    assert not env.errors(), env.errors()
    env.assert_levelled(method, points)


@pytest.mark.parametrize('controller', ['MACH3', 'MACH4', 'LinuxCNC'])
def test_file_controller_incomplete_height_map_makes_no_levelled_job(env, controller):
    env.load_cncjob('simple_line', 'job_neg')
    env.select('job_neg', controller, 'b')
    points = env.add_points()
    hm = env.tmp_path / 'heights_incomplete.txt'
    write_height_map(hm, controller, points, drop_last=True)
    import_through_button(env, hm)

    env.assert_no_levelled()
    env.tool.app.app_obj.new_object.assert_not_called()
    assert not env.tool.al_heights_valid
    assert any('no matching row' in m for m in env.errors()), env.messages()


@pytest.mark.parametrize('controller,fixture', [('MACH4', 'region'), ('LinuxCNC', 'flash')])
def test_file_controller_import_points_path(env, controller, fixture):
    """No probe points yet: the height map file itself defines the points (import_points)."""
    job = env.load_cncjob(fixture, 'job_ip')
    env.select('job_ip', controller, 'b')
    assert env.tool.al_voronoi_geo_storage == {}
    xmin, ymin, xmax, ymax = job.solid_geometry.bounds if not isinstance(job.solid_geometry, list) \
        else __import__('shapely').ops.unary_union(job.solid_geometry).bounds
    xs = [round(xmin + i * (xmax - xmin) / 3, 4) for i in range(4)]
    ys = [round(ymin + j * (ymax - ymin) / 2, 4) for j in range(3)]
    points = [(x, y) for x in xs for y in ys]
    hm = env.tmp_path / 'heights_points.txt'
    write_height_map(hm, controller, points)

    import_through_button(env, hm)

    assert len(env.tool.al_voronoi_geo_storage) == len(points)
    assert env.tool.al_heights_valid and not env.errors(), env.messages()
    env.assert_levelled('b', points)


def test_file_controller_import_points_without_rows_is_an_error(env):
    env.load_cncjob('flash', 'job_empty')
    env.select('job_empty', 'MACH3', 'b')
    hm = env.tmp_path / 'empty.txt'
    hm.write_text('this is not a height map\n')
    import_through_button(env, hm)
    env.assert_no_levelled()
    assert any('no valid rows' in m for m in env.errors()), env.messages()


# --------------------------------------------------------------------------------------
# GRBL
# --------------------------------------------------------------------------------------

class PlaneHeights(dict):
    """FakeGrbl.heights: the probe height of any (x, y) from the known plane."""

    def __bool__(self):
        return True     # FakeGrbl does `heights or {}`

    def get(self, key, default=None):
        return PLANE(*key)


def grbl_env(env):
    fake = FakeGrbl(heights=PlaneHeights())
    tool = env.tool
    tool.grbl_ser_port = fake
    tool._grbl_connected = True
    return fake


@pytest.mark.parametrize('method,fixture', GRBL_CASES)
def test_grbl_full_journey(env, method, fixture):
    fake = grbl_env(env)
    env.load_cncjob(fixture, 'job_grbl_' + fixture)
    env.select('job_grbl_' + fixture, 'GRBL', method)
    points = env.add_points()
    # GRBL has no probing G-code file: it probes through the serial port
    storage = env.tool.al_voronoi_geo_storage if method == 'v' else env.tool.al_bilinear_geo_storage
    assert env.tool.probing_gcode(storage) is None
    env.assert_no_levelled()

    env.tool.on_grbl_autolevel()

    cmds = fake.commands()
    probes = [c for c in cmds if c.startswith('G38.2')]
    assert len(probes) == len(points)
    assert not env.errors(), env.messages()
    assert env.tool.al_heights_valid
    # each stored height is what the fake reported for that point
    for v in env.tool.al_voronoi_geo_storage.values():
        assert v['height'] == pytest.approx(PLANE(v['point'].x, v['point'].y), abs=1e-4)
    assert env.tool._grbl_op is None
    env.assert_levelled(method, points)


def test_grbl_failed_probe_makes_no_levelled_job(env):
    fake = grbl_env(env)
    fake.fail_probe_at = len(range(COLS * ROWS))     # the last probe answers ALARM:4
    env.load_cncjob('flash', 'job_grbl_neg')
    env.select('job_grbl_neg', 'GRBL', 'b')
    env.add_points()

    env.tool.on_grbl_autolevel()

    env.assert_no_levelled()
    env.tool.app.app_obj.new_object.assert_not_called()
    assert not env.tool.al_heights_valid
    assert env.errors(), env.messages()
