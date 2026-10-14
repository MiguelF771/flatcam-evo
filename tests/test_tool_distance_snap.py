"""Characterization of the 'app' snapping branch of ``ToolDistance.Distance.snap_handler``: the center of the Gerber
pad (flash) or of the Excellon drill / slot under the cursor. It must pass before and after the move to
``feature_access.iter_features``.
"""

from unittest.mock import MagicMock, Mock

import pytest

from appPlugins.ToolDistance import Distance

from test_unify_characterization import (  # noqa: F401  (the fixtures must be visible in this module)
    _headless_qt, app, excellon, gerber, make_excellon, make_gerber,
)


def snap(app_objects, pos):
    """Run the unbound snap_handler with a fake tool; return (returned position, jump location or None)."""
    fake_app = MagicMock()
    fake_app.collection.get_list.return_value = app_objects
    fake_app.options = {}
    fake_app.defaults = {}
    fake_tool = Mock()
    fake_tool.original_call_source = 'app'
    fake_tool.app = fake_app
    fake_tool.make_storage = Mock()
    fake_tool.ui.big_cursor_cb.get_value.return_value = False
    result = Distance.snap_handler(fake_tool, pos)
    jumps = fake_app.on_jump_to.call_args_list
    assert len(jumps) <= 1
    return result, (jumps[0].kwargs['custom_location'] if jumps else None)


@pytest.fixture
def objs(gerber, excellon):
    return [gerber, excellon]


class TestGerberPads:
    @pytest.mark.parametrize('click, center', [
        ((5.2, 5.1), (5.0, 5.0)),         # C flash
        ((10.3, 5.1), (10.0, 5.0)),       # R flash
        ((15.4, 5.0), (15.0, 5.0)),       # O flash (also near drill T1 at 15,5: same center)
        ((20.1, 5.1), (20.0, 5.0)),       # P flash
    ])
    def test_click_inside_a_pad_snaps_to_its_center(self, objs, click, center):
        result, jump = snap([objs[0]], click)
        assert result == pytest.approx(center)
        assert jump == pytest.approx(center)

    def test_flash_after_a_clear_feature(self, objs):
        result, jump = snap([objs[0]], (15.2, 35.1))      # C2 flash, drawn after the clear track
        assert result == pytest.approx((15.0, 35.0))

    def test_click_on_a_track_does_not_snap(self, objs):
        result, jump = snap([objs[0]], (20.0, 10.1))      # inside the C1 track, not a flash
        assert result == (20.0, 10.1) and jump is None

    def test_click_on_a_region_does_not_snap(self, objs):
        result, jump = snap([objs[0]], (5.0, 24.0))
        assert result == (5.0, 24.0) and jump is None

    def test_click_outside_does_not_snap(self, objs):
        result, jump = snap([objs[0]], (8.0, 8.0))
        assert result == (8.0, 8.0) and jump is None


class TestExcellon:
    @pytest.mark.parametrize('click, center', [
        ((5.1, 12.1), (5.0, 12.0)),       # T2 drill
    ])
    def test_click_inside_a_drill(self, objs, click, center):
        result, jump = snap([objs[1]], click)
        assert result == pytest.approx(center)
        assert jump == pytest.approx(center)

    def test_click_inside_a_slot_snaps_to_the_slot_centroid(self, objs):
        result, jump = snap([objs[1]], (24.0, 12.2))      # T2 slot (20,12)-(28,12)
        assert result == pytest.approx((24.0, 12.0))
        assert jump == pytest.approx((24.0, 12.0))

    def test_click_inside_a_vertical_slot(self, objs):
        result, jump = snap([objs[1]], (5.1, 23.0))       # T3 slot (5,20)-(5,26)
        assert result == pytest.approx((5.0, 23.0))

    def test_click_outside_does_not_snap(self, objs):
        result, jump = snap([objs[1]], (8.0, 8.0))
        assert result == (8.0, 8.0) and jump is None


class TestBothObjects:
    def test_overlapping_pad_and_drill_snap_to_the_same_center(self, objs):
        result, jump = snap(objs, (5.2, 5.1))             # C flash and T1 drill both at (5, 5)
        assert result == pytest.approx((5.0, 5.0))

    def test_the_closest_center_wins(self, app, objs):
        # a second Excellon drill whose disc covers the click but whose center is further away
        exc = make_excellon(app, name='exc2')
        from shapely.geometry import Point
        exc.tools = {1: {'tooldia': 1.0, 'drills': [Point(5.3, 5.0)], 'slots': [], 'solid_geometry': [], 'data': {}}}
        assert exc.create_geometry() is None
        result, jump = snap([objs[0], exc], (5.1, 5.0))   # gerber pad center (5, 5) is closer than (5.3, 5)
        assert result == pytest.approx((5.0, 5.0))
        result, jump = snap([objs[0], exc], (5.25, 5.0))  # now the drill center (5.3, 5) is closer
        assert result == pytest.approx((5.3, 5.0))

    def test_non_snapable_objects_only(self):
        other = Mock()
        other.kind = 'geometry'
        result, jump = snap([other], (1.0, 1.0))
        assert result == (1.0, 1.0) and jump is None

    def test_empty_collection(self):
        result, jump = snap([], (1.0, 1.0))
        assert result == (1.0, 1.0) and jump is None
