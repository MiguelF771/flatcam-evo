"""Characterization of the click-pick rules of ToolAlignObjects.on_mouse_click_release, ToolDblSided.on_mouse_click_release
and ToolPunchGerber.find_pad. They must pass before and after the move to ``feature_access.iter_features``.

The unbound methods run with a fake ``self``; no GUI is needed."""

from unittest.mock import MagicMock, Mock

import pytest
from shapely.geometry import LineString, Point, box

from appPlugins.ToolAlignObjects import AlignObjects
from appPlugins.ToolDblSided import DblSidedTool
from appPlugins.ToolPunchGerber import ToolPunchGerber

from test_unify_characterization import (  # noqa: F401  (the fixtures must be visible in this module)
    _headless_qt, app, excellon, gerber, make_excellon, make_gerber,
)


def _event(pos):
    ev = Mock()
    ev.pos = pos
    ev.button = 1
    return ev


def _fake_app():
    fake_app = MagicMock()
    fake_app.use_3d_engine = True
    fake_app.selection_type = None
    fake_app.dec_format = lambda value, decimals: round(value, decimals)
    return fake_app


def _gerber_like(tools):
    obj = Mock()
    obj.kind = 'gerber'
    obj.tools = tools
    return obj


def _custom_excellon(app, tools):
    exc = make_excellon(app, name='exc_custom')
    exc.tools = tools
    assert exc.create_geometry() is None
    return exc


# ---------------------------------------------------------------------------------------------------------------
# ToolAlignObjects
# ---------------------------------------------------------------------------------------------------------------

def align_click(target, pos):
    """Return the list of clicked points and the number of check_points calls."""
    fake = Mock()
    fake.app = _fake_app()
    fake.canvas.translate_coords = lambda p: p
    fake.target_obj = target
    fake.decimals = 4
    fake.clicked_points = []
    AlignObjects.on_mouse_click_release(fake, _event(pos))
    return fake.clicked_points, fake.check_points.call_count


class TestAlignGerber:
    @pytest.mark.parametrize('click, center', [
        ((5.2, 5.1), [5.0, 5.0]),         # C flash
        ((10.3, 5.1), [10.0, 5.0]),       # R flash
        ((15.4, 5.0), [15.0, 5.0]),       # O flash
        ((20.1, 5.1), [20.0, 5.0]),       # P flash
        ((15.2, 35.1), [15.0, 35.0]),     # flash after the clear track
    ])
    def test_flash_gives_the_solid_centroid(self, gerber, click, center):
        points, calls = align_click(gerber, click)
        assert points == [pytest.approx(center)] and calls == 1

    @pytest.mark.parametrize('click', [(20.0, 10.1), (5.0, 24.0), (8.0, 8.0)])   # track, region, outside
    def test_track_region_outside_give_nothing(self, gerber, click):
        assert align_click(gerber, click) == ([], 0)

    def test_center_is_the_solid_centroid_not_the_follow_point(self):
        obj = _gerber_like({1: {'geometry': [{'solid': box(0, -1, 2, 1), 'follow': Point(0, 0)}]}})
        points, calls = align_click(obj, (0.5, 0.0))
        assert points == [pytest.approx([1.0, 0.0])]

    def test_clear_record_is_ignored(self):
        obj = _gerber_like({1: {'geometry': [{'clear': box(0, 0, 2, 2), 'follow': Point(1, 1)}]}})
        assert align_click(obj, (1.0, 1.0)) == ([], 0)

    def test_two_overlapping_flashes_give_two_points(self):
        obj = _gerber_like({
            1: {'geometry': [{'solid': box(0, 0, 2, 2), 'follow': Point(1, 1)}]},
            2: {'geometry': [{'solid': box(0, 0, 4, 4), 'follow': Point(2, 2)}]},
        })
        points, calls = align_click(obj, (1.0, 1.0))
        assert points == [pytest.approx([1.0, 1.0]), pytest.approx([2.0, 2.0])] and calls == 2

    def test_solid_record_without_follow_does_not_crash(self):
        # the old code raised KeyError on geo_el['follow']
        obj = _gerber_like({1: {'geometry': [{'solid': box(0, 0, 2, 2)}]}})
        assert align_click(obj, (1.0, 1.0)) == ([], 0)


class TestAlignExcellon:
    def test_drill_gives_its_center(self, excellon):
        points, calls = align_click(excellon, (5.1, 12.1))
        assert points == [pytest.approx([5.0, 12.0])] and calls == 1

    def test_slot_gives_its_centroid(self, excellon):
        points, calls = align_click(excellon, (24.0, 12.2))
        assert points == [pytest.approx([24.0, 12.0])]

    def test_vertical_slot(self, excellon):
        points, calls = align_click(excellon, (5.1, 23.0))
        assert points == [pytest.approx([5.0, 23.0])]

    def test_outside_gives_nothing(self, excellon):
        assert align_click(excellon, (8.0, 8.0)) == ([], 0)

    def test_overlapping_drills_of_two_tools_give_two_points(self, app):
        exc = _custom_excellon(app, {
            1: {'tooldia': 1.0, 'drills': [Point(5, 5)], 'slots': [], 'solid_geometry': [], 'data': {}},
            2: {'tooldia': 2.0, 'drills': [Point(5, 5.2)], 'slots': [], 'solid_geometry': [], 'data': {}},
        })
        points, calls = align_click(exc, (5.0, 5.1))
        assert points == [pytest.approx([5.0, 5.0]), pytest.approx([5.0, 5.2])] and calls == 2


# ---------------------------------------------------------------------------------------------------------------
# ToolDblSided
# ---------------------------------------------------------------------------------------------------------------

def dbl_click(exc, pos):
    """Return the list of reference points set in the point entry and the number of on_exit calls."""
    fake = Mock()
    fake.app = _fake_app()
    fake.canvas.translate_coords = lambda p: p
    fake.exc_hole_obj = exc
    fake.decimals = 4
    DblSidedTool.on_mouse_click_release(fake, _event(pos))
    return [c.args[0] for c in fake.ui.point_entry.set_value.call_args_list], fake.on_exit.call_count


class TestDblSided:
    def test_drill_gives_its_center(self, excellon):
        points, exits = dbl_click(excellon, (5.1, 12.1))
        assert points == [pytest.approx((5.0, 12.0))] and exits == 1

    def test_slot_gives_its_centroid(self, excellon):
        points, exits = dbl_click(excellon, (24.0, 12.2))
        assert points == [pytest.approx((24.0, 12.0))]

    def test_outside_gives_nothing(self, excellon):
        assert dbl_click(excellon, (8.0, 8.0)) == ([], 0)

    def test_stops_after_the_first_hit_inside_a_tool(self, app):
        exc = _custom_excellon(app, {
            1: {'tooldia': 2.0, 'drills': [Point(5, 5), Point(5, 5.2)], 'slots': [], 'solid_geometry': [],
                'data': {}},
        })
        points, exits = dbl_click(exc, (5.0, 5.1))
        assert points == [pytest.approx((5.0, 5.0))] and exits == 1

    def test_each_tool_can_still_hit(self, app):
        # the old inner "break" left only the loop of 1 tool
        exc = _custom_excellon(app, {
            1: {'tooldia': 1.0, 'drills': [Point(5, 5)], 'slots': [], 'solid_geometry': [], 'data': {}},
            2: {'tooldia': 2.0, 'drills': [Point(5, 5.2)], 'slots': [], 'solid_geometry': [], 'data': {}},
        })
        points, exits = dbl_click(exc, (5.0, 5.1))
        assert points == [pytest.approx((5.0, 5.0)), pytest.approx((5.0, 5.2))] and exits == 2


# ---------------------------------------------------------------------------------------------------------------
# ToolPunchGerber.find_pad
# ---------------------------------------------------------------------------------------------------------------

def find_pad(grb, point, selected_apids):
    fake = Mock()
    fake.grb_obj = grb
    items = []
    for apid in selected_apids:
        it = Mock()
        it.text.return_value = str(apid)
        items.append(it)
    fake.ui.apertures_table.selectedItems.return_value = items
    return ToolPunchGerber.find_pad(fake, point)


class TestFindPad:
    def test_flash_in_a_selected_aperture(self, gerber):
        assert find_pad(gerber, (5.2, 5.1), [10]) == [{'apid': 10, 'idx': 0}]

    def test_point_object_is_accepted(self, gerber):
        assert find_pad(gerber, Point(10.3, 5.1), [11]) == [{'apid': 11, 'idx': 0}]

    def test_not_selected_aperture_is_skipped(self, gerber):
        assert find_pad(gerber, (5.2, 5.1), [11]) == []

    def test_track_and_region_are_not_pads(self, gerber):
        assert find_pad(gerber, (20.0, 10.1), [10, 11, 0]) == []
        assert find_pad(gerber, (5.0, 24.0), [0, 10]) == []

    def test_flash_found_in_the_fixture_after_the_clear_track(self, gerber):
        found = find_pad(gerber, (15.2, 35.1), sorted(gerber.tools))
        assert len(found) == 1 and found[0]['idx'] == 0

    def test_index_is_the_record_position_after_other_records(self):
        grb = _gerber_like({7: {'geometry': [
            {'solid': box(0, 0, 1, 1), 'follow': LineString([(0, 0), (1, 1)])},
            {'clear': box(5, 5, 6, 6), 'follow': Point(5.5, 5.5)},
            {'solid': box(2, 2, 4, 4), 'follow': Point(3, 3)},
        ]}})
        assert find_pad(grb, (3.0, 3.0), [7]) == [{'apid': 7, 'idx': 2}]
        assert find_pad(grb, (5.5, 5.5), [7]) == []      # clear record
        assert find_pad(grb, (0.5, 0.5), [7]) == []      # route

    def test_two_overlapping_pads_give_both_in_order(self):
        grb = _gerber_like({
            1: {'geometry': [{'solid': box(0, 0, 4, 4), 'follow': Point(2, 2)}]},
            2: {'geometry': [{'solid': box(1, 1, 3, 3), 'follow': Point(2, 2)}]},
        })
        assert find_pad(grb, (2.0, 2.0), [1, 2]) == [{'apid': 1, 'idx': 0}, {'apid': 2, 'idx': 0}]

    def test_record_without_follow_or_solid_is_skipped(self):
        grb = _gerber_like({1: {'geometry': [
            {'solid': box(0, 0, 2, 2)},
            {'follow': Point(1, 1)},
        ]}})
        assert find_pad(grb, (1.0, 1.0), [1]) == []
