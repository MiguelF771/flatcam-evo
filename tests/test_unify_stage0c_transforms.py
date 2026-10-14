"""Tests of Stage 0C (part 2) of the Gerber / Excellon unification plan: every Gerber transform keeps the aperture
metadata in agreement with the transformed geometry, or sets ``export_as_regions``.

Each test follows the table of the plan: parse -> transform -> check the flag and the metadata -> export with the
real ``appIO.export_gerber`` -> parse again -> compare the composed geometry.

The expected values below are written from the plan table, not computed with the code under test. Everything is
headless. The helpers come from ``test_unify_characterization`` and ``test_unify_stage0c``.
"""

import math
from dataclasses import dataclass
from typing import Callable

import pytest
from shapely.geometry import LineString, box

from appObjects.GerberObject import GerberObject

from test_unify_characterization import (  # noqa: F401  (the fixtures must be visible in this module)
    FIXTURES, MIXED_GBR, NATIVE_GBR, _headless_qt, app, export_gerber_text, make_gerber, parts, polygon_union,
    reparse_gerber, save_and_load,
)

# Tests that fail on the code before Stage 0C part 2 (the ids are the pytest node ids without the file name).
# They run as strict xfail until the implementation commit, which empties this set.
FIXED_IN_PART_2 = {
    'TestBufferByFactor::test_flag_is_set_when_there_are_records[AM]',
    'TestBufferByFactor::test_flag_is_set_when_there_are_records[C]',
    'TestBufferByFactor::test_flag_is_set_when_there_are_records[RT]',
    'TestBufferByFactor::test_flag_is_set_when_there_are_records[R]',
    'TestBufferByFactor::test_region_records_count_too',
    'TestFlagIsSticky::test_flag_never_goes_back_to_false',
    'TestProjectSaveLoad::test_flag_set_by_a_transform_survives_and_exports',
    'TestProjectSaveLoad::test_p_metadata_survives',
    'TestProjectSaveLoad::test_representable_transform_keeps_false_and_the_new_metadata',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[C-scale_flip_x]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[C-scale_neg]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[C-scale_nonuniform]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[C-scale_nonuniform_neg]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[C-skew_x]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[C-skew_y]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-buffer_bevel]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-buffer_bevel_int]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-buffer_mitre]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-buffer_mitre_int]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-buffer_round]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-buffer_round_int]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-buffer_shrink_mitre]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-rotate_30]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-rotate_450]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-rotate_90]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-rotate_minus_90]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-scale_flip_x]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-scale_neg]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-scale_nonuniform]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-scale_nonuniform_neg]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-skew_x]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[O-skew_y]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-buffer_bevel]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-buffer_bevel_int]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-buffer_mitre]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-buffer_mitre_int]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-buffer_round]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-buffer_round_int]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-buffer_shrink_mitre]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-rotate_30]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-rotate_450]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-rotate_90]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-rotate_minus_90]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-scale_flip_x]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-scale_neg]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-scale_nonuniform]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-scale_nonuniform_neg]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-skew_x]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[R-skew_y]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-buffer_bevel]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-buffer_bevel_int]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-buffer_mitre]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-buffer_mitre_int]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-buffer_round]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-buffer_round_int]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-buffer_shrink_mitre]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-mirror_x]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-mirror_y]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-offset]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-rotate_180]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-rotate_30]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-rotate_450]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-rotate_90]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-rotate_minus_90]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-scale_flip_x]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-scale_flip_y]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-scale_neg]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-scale_nonuniform]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-scale_nonuniform_neg]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-scale_up]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-skew_x]',
    'TestTableExportRoundTrip::test_composed_geometry_survives_the_export[RT-skew_y]',
    'TestTableExportRoundTrip::test_the_regions_export_does_not_change_the_object',
    'TestTableFlagAndMetadata::test_a_buffer_that_removes_the_aperture_sets_the_flag',
    'TestTableFlagAndMetadata::test_a_flagged_aperture_without_records_is_not_flagged',
    'TestTableFlagAndMetadata::test_a_round_join_rounds_the_corners_of_exact_rectangles',
    'TestTableFlagAndMetadata::test_a_track_with_a_c_aperture_follows_the_c_column',
    'TestTableFlagAndMetadata::test_a_track_with_an_r_aperture_follows_the_r_column',
    'TestTableFlagAndMetadata::test_an_o_track_is_drawn_with_the_diagonal_of_the_aperture',
    'TestTableFlagAndMetadata::test_degenerate_scale_factor_sets_the_flag',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-buffer_bevel]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-buffer_bevel_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-buffer_mitre]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-buffer_mitre_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-buffer_round]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-buffer_round_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-buffer_shrink_mitre]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-mirror_x]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-mirror_y]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-rotate_180]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-rotate_30]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-rotate_450]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-rotate_90]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-rotate_minus_90]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-scale_flip_x]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-scale_flip_y]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-scale_neg]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-scale_nonuniform]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-scale_up]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-skew_x]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[AM-skew_y]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[C-scale_nonuniform]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[C-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[C-skew_x]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[C-skew_y]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[O-rotate_30]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[O-scale_nonuniform]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[O-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[O-skew_x]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[O-skew_y]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-buffer_bevel]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-buffer_bevel_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-buffer_mitre]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-buffer_mitre_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-buffer_round]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-buffer_round_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-buffer_shrink_mitre]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-scale_nonuniform]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-skew_x]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P3-skew_y]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-buffer_bevel]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-buffer_bevel_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-buffer_mitre]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-buffer_mitre_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-buffer_round]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-buffer_round_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-buffer_shrink_mitre]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-scale_nonuniform]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-skew_x]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[P7-skew_y]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[R-buffer_bevel]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[R-buffer_bevel_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[R-buffer_mitre]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[R-buffer_mitre_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[R-buffer_round]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[R-buffer_round_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[R-rotate_30]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[R-skew_x]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[R-skew_y]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-buffer_bevel]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-buffer_bevel_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-buffer_mitre]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-buffer_mitre_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-buffer_round]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-buffer_round_int]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-buffer_shrink_mitre]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-rotate_30]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-skew_x]',
    'TestTableFlagAndMetadata::test_flag_is_exactly_the_regions_cells[RT-skew_y]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[C-scale_flip_x]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[C-scale_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[C-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-buffer_bevel]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-buffer_bevel_int]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-buffer_mitre]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-buffer_mitre_int]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-buffer_round]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-buffer_round_int]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-buffer_shrink_mitre]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-rotate_450]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-rotate_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-rotate_minus_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-scale_flip_x]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-scale_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[O-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-mirror_x]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-mirror_y]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-rotate_180]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-rotate_30]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-rotate_450]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-rotate_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-rotate_minus_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-scale_flip_x]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-scale_flip_y]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-scale_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P3-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-mirror_x]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-mirror_y]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-rotate_180]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-rotate_30]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-rotate_450]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-rotate_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-rotate_minus_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-scale_flip_x]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-scale_flip_y]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-scale_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[P7-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[R-buffer_shrink_mitre]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[R-rotate_450]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[R-rotate_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[R-rotate_minus_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[R-scale_flip_x]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[R-scale_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[R-scale_nonuniform]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[R-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[RT-rotate_450]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[RT-rotate_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[RT-rotate_minus_90]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[RT-scale_flip_x]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[RT-scale_neg]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[RT-scale_nonuniform]',
    'TestTableFlagAndMetadata::test_metadata_of_the_representable_cells[RT-scale_nonuniform_neg]',
    'TestTableFlagAndMetadata::test_one_aperture_is_enough_to_set_the_flag',
    'TestTableFlagAndMetadata::test_rotate_angle_tolerance_and_reduction',
    'TestTableFlagAndMetadata::test_rotate_quarter_turn_swaps_only_for_odd_turns',
    'TestUntransformedAndMixedFixtures::test_native_subset_goes_to_regions_when_the_table_says[buffer_mitre]',
    'TestUntransformedAndMixedFixtures::test_native_subset_goes_to_regions_when_the_table_says[buffer_round]',
    'TestUntransformedAndMixedFixtures::test_native_subset_goes_to_regions_when_the_table_says[rotate_30]',
    'TestUntransformedAndMixedFixtures::test_native_subset_goes_to_regions_when_the_table_says[scale_nonuniform]',
    'TestUntransformedAndMixedFixtures::test_native_subset_goes_to_regions_when_the_table_says[skew_x]',
    'TestUntransformedAndMixedFixtures::test_native_subset_stays_native_when_the_table_allows[rotate_90]',
    'TestUntransformedAndMixedFixtures::test_native_subset_stays_native_when_the_table_allows[scale_neg]',
    'TestUntransformedAndMixedFixtures::test_untransformed_r_track_round_trips',
}


@pytest.fixture(autouse=True)
def _expected_failure_before_part_2(request):
    short = request.node.nodeid.split('::', 1)[1]
    if short in FIXED_IN_PART_2:
        request.applymarker(pytest.mark.xfail(strict=True, raises=AssertionError, reason='fixed in Stage 0C part 2'))


def fx(name):
    return str(FIXTURES / ('unify_%s.gbr' % name))


# kind -> (fixture, aperture id, aperture type)
KINDS = {
    'C': ('circle', 10, 'C'),        # a flash and a track drawn with the same C aperture
    'R': ('rect', 11, 'R'),          # non-square rectangle, 2 flashes
    'O': ('oblong', 12, 'O'),        # non-square oblong, 2 flashes
    'RT': ('rtrack', 11, 'R'),       # a track drawn with an R aperture
    'P3': ('p3', 20, 'P'),           # triangle with a rotation
    'P7': ('p7', 21, 'P'),           # odd-sided polygon with a rotation
    'AM': ('macro', 14, 'AM'),       # outline macro
}
COLUMN = {'C': 'C', 'R': 'R', 'RT': 'R', 'O': 'O', 'P3': 'P', 'P7': 'P', 'AM': 'AM'}


@dataclass
class Tr:
    """One row of the plan table. A lambda that returns None is a 'regions export' cell."""
    id: str
    apply: Callable
    c: Callable            # size -> size
    rect: Callable         # (w, h) -> (w, h)
    obl: Callable          # (w, h) -> (w, h)
    poly: Callable         # (diam, rotation, n) -> (diam, rotation)
    am_flagged: bool = True


def uniform(k, rot=lambda r: r):
    return dict(c=lambda s: s * k, rect=lambda w, h: (w * k, h * k), obl=lambda w, h: (w * k, h * k),
                poly=lambda d, r, n: (d * k, rot(r)))


def nonuniform_scale():
    return dict(c=lambda s: None, rect=lambda w, h: (w * 2.0, h * 1.25), obl=lambda w, h: None,
                poly=lambda d, r, n: None)


def rotation(extra_angle, swap, keep_rect=True):
    """Rotation by extra_angle degrees; swap: width and height swap; keep_rect False: not a quarter turn."""
    if keep_rect:
        rect = (lambda w, h: (h, w)) if swap else (lambda w, h: (w, h))
    else:
        rect = lambda w, h: None  # noqa: E731
    return dict(c=lambda s: s, rect=rect, obl=rect, poly=lambda d, r, n: (d, r + extra_angle))


def mirror(rot):
    return dict(c=lambda s: s, rect=lambda w, h: (w, h), obl=lambda w, h: (w, h), poly=lambda d, r, n: (d, rot(r)))


def skew():
    return dict(c=lambda s: None, rect=lambda w, h: None, obl=lambda w, h: None, poly=lambda d, r, n: None)


def buffer_cells(d, rect_ok):
    return dict(c=lambda s: s + 2 * d,
                rect=(lambda w, h: (w + 2 * d, h + 2 * d)) if rect_ok else (lambda w, h: None),
                obl=lambda w, h: (w + 2 * d, h + 2 * d), poly=lambda d_, r, n: None)


SCALE_ORIGIN = (3.0, 2.0)
ROT_ORIGIN = (12.0, 9.0)
MIR_ORIGIN = (14.0, 11.0)

TRANSFORMS = [
    Tr('scale_up', lambda o: o.scale(1.5, 1.5, SCALE_ORIGIN), **uniform(1.5)),
    Tr('scale_neg', lambda o: o.scale(-1.5, -1.5, SCALE_ORIGIN), **uniform(1.5, lambda r: r + 180)),
    # scale(s, -s) negates Y, like mirror X; scale(-s, s) negates X, like mirror Y
    Tr('scale_flip_y', lambda o: o.scale(1.5, -1.5, SCALE_ORIGIN), **uniform(1.5, lambda r: -r)),
    Tr('scale_flip_x', lambda o: o.scale(-1.5, 1.5, SCALE_ORIGIN), **uniform(1.5, lambda r: 180 - r)),
    Tr('scale_nonuniform', lambda o: o.scale(2.0, 1.25, SCALE_ORIGIN), **nonuniform_scale()),
    Tr('scale_nonuniform_neg', lambda o: o.scale(-2.0, 1.25, SCALE_ORIGIN), **nonuniform_scale()),
    Tr('rotate_90', lambda o: o.rotate(90.0, ROT_ORIGIN), **rotation(90, swap=True)),
    Tr('rotate_minus_90', lambda o: o.rotate(-90.0, ROT_ORIGIN), **rotation(-90, swap=True)),
    Tr('rotate_180', lambda o: o.rotate(180.0, ROT_ORIGIN), **rotation(180, swap=False)),
    Tr('rotate_450', lambda o: o.rotate(450.0, ROT_ORIGIN), **rotation(450, swap=True)),
    Tr('rotate_30', lambda o: o.rotate(30.0, ROT_ORIGIN), **rotation(30, swap=False, keep_rect=False)),
    Tr('mirror_x', lambda o: o.mirror('X', MIR_ORIGIN), **mirror(lambda r: -r)),
    Tr('mirror_y', lambda o: o.mirror('Y', MIR_ORIGIN), **mirror(lambda r: 180 - r)),
    Tr('skew_x', lambda o: o.skew(10.0, 0.0, (4.0, 3.0)), **skew()),
    Tr('skew_y', lambda o: o.skew(0.0, 5.0, (4.0, 3.0)), **skew()),
    Tr('buffer_mitre', lambda o: o.buffer(0.3, 'mitre'), **buffer_cells(0.3, True)),
    Tr('buffer_mitre_int', lambda o: o.buffer(0.3, 2), **buffer_cells(0.3, True)),
    Tr('buffer_round', lambda o: o.buffer(0.3, 'round'), **buffer_cells(0.3, False)),
    Tr('buffer_round_int', lambda o: o.buffer(0.3, 1), **buffer_cells(0.3, False)),
    Tr('buffer_bevel', lambda o: o.buffer(0.3, 'bevel'), **buffer_cells(0.3, False)),
    Tr('buffer_bevel_int', lambda o: o.buffer(0.3, 3), **buffer_cells(0.3, False)),
    Tr('buffer_shrink_mitre', lambda o: o.buffer(-0.2, 'mitre'), **buffer_cells(-0.2, True)),
    Tr('offset', lambda o: o.offset((3.5, -2.25)),
       c=lambda s: s, rect=lambda w, h: (w, h), obl=lambda w, h: (w, h), poly=lambda d, r, n: (d, r),
       am_flagged=False),
]
TR_BY_ID = {t.id: t for t in TRANSFORMS}

CELLS = [(k, t.id) for k in KINDS for t in TRANSFORMS]


def cell_params(test_name):
    """Parameters of one test: every aperture kind with every transform (the test name is only a label)."""
    return [pytest.param(kind, tid, id='%s-%s' % (kind, tid)) for kind, tid in CELLS]


def load(app, kind):
    fixture, apid, ap_type = KINDS[kind]
    return make_gerber(app, fx(fixture), name=kind), apid


def expected_cell(kind, tid, tool):
    """(flagged, expected metadata dict or None) for one aperture and one transform, from the plan table."""
    tr = TR_BY_ID[tid]
    col = COLUMN[kind]
    if col == 'AM':
        return tr.am_flagged, None
    if col == 'C':
        size = tr.c(tool['size'])
        return size is None, None if size is None else {'size': size}
    if kind == 'R' and tid in ('buffer_mitre', 'buffer_mitre_int'):
        # The plan table says "mitre join: w, h + 2d", but the parser rounds the corners of a flash with a 1e-7
        # buffer and the mitre buffer then rounds them with radius d: the records are not rectangles any more.
        return True, None
    if kind == 'RT' and tid.startswith('buffer'):
        # the record of an R track is a box around the whole move, not a w x h rectangle
        return True, None
    if col in ('R', 'O'):
        wh =(tr.rect if col == 'R' else tr.obl)(tool['width'], tool['height'])
        if wh is None:
            return True, None
        return False, {'width': wh[0], 'height': wh[1], 'size': math.hypot(*wh)}
    res = tr.poly(tool['diam'], tool.get('rotation', 0.0), tool['nVertices'])
    if res is None:
        return True, None
    return False, {'diam': res[0], 'size': res[0], 'rotation': res[1]}


def angle_equal(a, b, n):
    step = 360.0 / n
    delta = (a - b) % step
    return min(delta, step - delta) < 1e-6


def assert_composed_equal(actual, expected, msg):
    exp = polygon_union(expected)
    got = polygon_union(actual)
    assert exp.area > 0.5, msg + ' (empty expectation)'
    assert got.area > 0.5 * exp.area, msg + ' (empty result)'
    diff = got.symmetric_difference(exp).area
    assert diff <= max(1e-3, 2e-3 * exp.area), '%s symmetric difference %.6f (area %.4f)' % (msg, diff, exp.area)


def interior_count(geometry):
    return sum(len(poly.interiors) for poly in parts(polygon_union(geometry)) if poly.geom_type == 'Polygon')


def record_count(obj, apid):
    return len(obj.tools[apid]['geometry'])


# ---------------------------------------------------------------------------------------------------------------
# the table: flag and metadata
# ---------------------------------------------------------------------------------------------------------------

class TestTableFlagAndMetadata:
    @pytest.mark.parametrize('kind,tid', cell_params('flag'))
    def test_flag_is_exactly_the_regions_cells(self, app, kind, tid):
        obj, apid = load(app, kind)
        before = dict(obj.tools[apid])
        flagged, _ = expected_cell(kind, tid, before)
        assert obj.export_as_regions is False
        TR_BY_ID[tid].apply(obj)
        assert obj.export_as_regions is flagged, '%s %s' % (kind, tid)

    @pytest.mark.parametrize('kind,tid', cell_params('meta'))
    def test_metadata_of_the_representable_cells(self, app, kind, tid):
        obj, apid = load(app, kind)
        before = dict(obj.tools[apid])
        flagged, want = expected_cell(kind, tid, before)
        TR_BY_ID[tid].apply(obj)
        now = obj.tools[apid]
        assert now['type'] == before['type']
        assert record_count(obj, apid) == len(before['geometry'])
        if want is not None:
            for key, value in want.items():
                if key == 'rotation':
                    assert angle_equal(now.get('rotation', 0.0), value, before['nVertices']), \
                        (kind, tid, now.get('rotation'), value)
                else:
                    assert now[key] == pytest.approx(value, rel=1e-9, abs=1e-12), (kind, tid, key)
            if before['type'] == 'P':
                assert now['nVertices'] == before['nVertices']
        if tid not in ('buffer_shrink_mitre',):
            # the sizes never go negative (a negative scale factor must not make a negative width)
            for key in ('size', 'width', 'height', 'diam'):
                if key in now:
                    assert now[key] >= 0, (kind, tid, key, now[key])

    def test_offset_changes_no_metadata(self, app):
        for kind in KINDS:
            obj, apid = load(app, kind)
            before = {k: (v.name if k == 'macro' else v) for k, v in obj.tools[apid].items() if k != 'geometry'}
            obj.offset((3.5, -2.25))
            after = {k: (v.name if k == 'macro' else v) for k, v in obj.tools[apid].items() if k != 'geometry'}
            assert after == before
            assert obj.export_as_regions is False

    def test_the_region_aperture_never_sets_the_flag(self, app):
        for tid in TR_BY_ID:
            obj = make_gerber(app, fx('region_hole'), name='hole')
            assert set(obj.tools) == {0}
            TR_BY_ID[tid].apply(obj)
            assert obj.export_as_regions is False, tid

    def test_a_flagged_aperture_without_records_is_not_flagged(self, app):
        obj, apid = load(app, 'R')
        obj.tools[99] = {'type': 'AM', 'macro': None, 'modifiers': [], 'geometry': []}
        obj.tools[98] = {'type': 'P', 'diam': 1.0, 'nVertices': 3, 'size': 1.0}
        obj.rotate(30.0, ROT_ORIGIN)       # R at 30 degrees: regions
        assert obj.export_as_regions is True
        obj2, _ = load(app, 'R')
        obj2.tools[99] = {'type': 'AM', 'macro': None, 'modifiers': [], 'geometry': []}
        obj2.tools[98] = {'type': 'P', 'diam': 1.0, 'nVertices': 3, 'size': 1.0}
        obj2.mirror('X', MIR_ORIGIN)       # R is fine; the empty AM and P apertures must not set the flag
        assert obj2.export_as_regions is False

    def test_one_aperture_is_enough_to_set_the_flag(self, app):
        obj = make_gerber(app, MIXED_GBR)          # has C, R, O, P, AM and the region aperture
        obj.mirror('X', MIR_ORIGIN)
        assert obj.export_as_regions is True       # the macro aperture
        native = make_gerber(app, NATIVE_GBR)
        native.mirror('X', MIR_ORIGIN)
        native.rotate(90.0, ROT_ORIGIN)
        assert native.export_as_regions is False
        native.rotate(30.0, ROT_ORIGIN)            # R and the circle tracks: R at 30 degrees
        assert native.export_as_regions is True

    def test_rotate_angle_tolerance_and_reduction(self, app):
        for angle, flagged in ((90.0, False), (90.0 + 1e-12, False), (-270.0, False), (720.0, False),
                               (630.0, False), (90.001, True), (45.0, True), (359.5, True)):
            obj, _ = load(app, 'R')
            obj.rotate(angle, ROT_ORIGIN)
            assert obj.export_as_regions is flagged, angle

    def test_rotate_quarter_turn_swaps_only_for_odd_turns(self, app):
        for angle, swapped in ((90.0, True), (270.0, True), (-90.0, True), (450.0, True),
                               (180.0, False), (360.0, False), (-180.0, False), (720.0, False)):
            obj, apid = load(app, 'R')
            obj.rotate(angle, ROT_ORIGIN)
            expect = (1.2, 3.0) if swapped else (3.0, 1.2)
            assert (obj.tools[apid]['width'], obj.tools[apid]['height']) == pytest.approx(expect), angle

    def test_p_rotation_is_taken_modulo_the_vertex_angle(self, app):
        obj, apid = load(app, 'P3')       # rotation 20, 3 vertices: the same triangle every 120 degrees
        obj.rotate(120.0, ROT_ORIGIN)
        assert angle_equal(obj.tools[apid]['rotation'], 20.0, 3)
        assert obj.export_as_regions is False

    def test_degenerate_scale_factor_sets_the_flag(self, app):
        obj, apid = load(app, 'R')
        obj.scale(2.0, 0.0, SCALE_ORIGIN)
        assert obj.export_as_regions is True

    def test_scale_with_both_factors_zero_changes_nothing(self, app):
        obj, apid = load(app, 'R')
        obj.scale(0.0, 0.0, SCALE_ORIGIN)
        assert obj.export_as_regions is False
        assert obj.tools[apid]['width'] == 3.0

    def test_a_track_with_a_c_aperture_follows_the_c_column(self, app):
        """The circle fixture has a flash and a track on the same aperture."""
        obj, apid = load(app, 'C')
        assert any(isinstance(rec['follow'], LineString) for rec in obj.tools[apid]['geometry'])
        obj.buffer(0.3, 'round')
        assert obj.export_as_regions is False
        assert obj.tools[apid]['size'] == pytest.approx(2.1)
        obj.scale(2.0, 1.0, SCALE_ORIGIN)
        assert obj.export_as_regions is True

    def test_a_track_with_an_r_aperture_follows_the_r_column(self, app):
        obj, apid = load(app, 'RT')
        obj.rotate(30.0, ROT_ORIGIN)
        assert obj.export_as_regions is True
        obj, apid = load(app, 'RT')
        obj.rotate(90.0, ROT_ORIGIN)
        assert obj.export_as_regions is False
        assert (obj.tools[apid]['width'], obj.tools[apid]['height']) == pytest.approx((0.6, 0.8))

    def test_an_o_track_is_drawn_with_the_diagonal_of_the_aperture(self, app, tmp_path):
        """The parser draws a track of an O aperture with 'size' (the diagonal of the aperture) as width. After a
        buffer, 'size' + 2d is not the diagonal of the buffered width and height, so only the regions export
        keeps the track. A flash-only O aperture stays native."""
        text = ('%FSLAX46Y46*%\n%MOMM*%\n%ADD12O,2.0X1.0*%\nG01*\n%LPD*%\nD12*\n'
                'X5000000Y5000000D02*\nX15000000Y5000000D01*\nM02*\n')
        track = reparse_gerber(app, text, tmp_path, name='otrack')
        assert any(isinstance(rec['follow'], LineString) for rec in track.tools[12]['geometry'])
        track.buffer(0.3, 'mitre')
        assert track.export_as_regions is True
        flash, _ = load(app, 'O')
        flash.buffer(0.3, 'mitre')
        assert flash.export_as_regions is False
        # a uniform scale keeps the track representable
        track2 = reparse_gerber(app, text, tmp_path, name='otrack2')
        track2.scale(2.0, 2.0, SCALE_ORIGIN)
        assert track2.export_as_regions is False
        assert track2.tools[12]['size'] == pytest.approx(2 * math.hypot(2.0, 1.0))

    def test_exact_rectangles_stay_representable_under_a_mitre_buffer(self, app, tmp_path):
        """A mitre buffer keeps the corners sharp: with exact rectangle records the table cell holds."""
        obj, apid = load(app, 'R')
        for rec in obj.tools[apid]['geometry']:
            x, y = rec['follow'].x, rec['follow'].y
            rec['solid'] = box(x - 1.5, y - 0.6, x + 1.5, y + 0.6)
        obj.solid_geometry = [rec['solid'] for rec in obj.tools[apid]['geometry']]
        obj.buffer(0.3, 'mitre')
        assert obj.export_as_regions is False
        assert (obj.tools[apid]['width'], obj.tools[apid]['height']) == pytest.approx((3.6, 1.8))
        text = export_gerber_text(app, obj)
        assert '%ADD11R,3.6X1.79' in text or '%ADD11R,3.6X1.8' in text
        assert_composed_equal(reparse_gerber(app, text, tmp_path).solid_geometry, obj.solid_geometry, 'exact')

    def test_a_round_join_rounds_the_corners_of_exact_rectangles(self, app):
        obj, apid = load(app, 'R')
        for rec in obj.tools[apid]['geometry']:
            x, y = rec['follow'].x, rec['follow'].y
            rec['solid'] = box(x - 1.5, y - 0.6, x + 1.5, y + 0.6)
        obj.solid_geometry = [rec['solid'] for rec in obj.tools[apid]['geometry']]
        obj.buffer(0.3, 'round')
        assert obj.export_as_regions is True

    def test_a_buffer_that_removes_the_aperture_sets_the_flag(self, app):
        circle, _ = load(app, 'C')
        circle.buffer(-0.8, 'mitre')       # 1.5 - 1.6 < 0
        assert circle.export_as_regions is True
        rect, _ = load(app, 'R')
        rect.buffer(-0.6, 'mitre')         # the height 1.2 - 1.2 = 0
        assert rect.export_as_regions is True


class TestBufferByFactor:
    """The multiplicative buffer is owned by Stage 3: its geometry is not part of this stage. Its metadata is not
    guaranteed, so the flag is set whenever the object has any record."""

    # O and P are left out: the multiplicative buffer raises a KeyError on them today (it does not rebuild their
    # 'solid'); that is Stage 3's to fix
    @pytest.mark.parametrize('kind', ['C', 'R', 'RT', 'AM'])
    def test_flag_is_set_when_there_are_records(self, app, kind):
        obj, _ = load(app, kind)
        obj.buffer(1.2, 'mitre', True)
        assert obj.export_as_regions is True

    def test_region_records_count_too(self, app):
        obj = make_gerber(app, fx('region_hole'), name='hole')
        obj.buffer(1.1, 'mitre', True)
        assert obj.export_as_regions is True

    def test_an_object_without_records_stays_false(self, app):
        obj = GerberObject('empty', app)
        obj.tools = {}
        obj.solid_geometry = []
        obj.follow_geometry = []
        obj.buffer(1.2, 'mitre', True)
        assert obj.export_as_regions is False

    def test_a_zero_factor_is_rejected_without_a_change(self, app):
        obj, _ = load(app, 'R')
        obj.buffer(0, 'mitre', True)
        assert obj.export_as_regions is False


class TestFlagIsSticky:
    def test_flag_never_goes_back_to_false(self, app, tmp_path):
        obj, apid = load(app, 'R')
        obj.rotate(30.0, ROT_ORIGIN)
        assert obj.export_as_regions is True
        for step in (lambda o: o.rotate(-30.0, ROT_ORIGIN),      # back to the original angle
                     lambda o: o.rotate(90.0, ROT_ORIGIN),
                     lambda o: o.mirror('X', MIR_ORIGIN),
                     lambda o: o.scale(1.5, 1.5, SCALE_ORIGIN),
                     lambda o: o.offset((1.0, 1.0)),
                     lambda o: o.buffer(0.1, 'mitre')):
            step(obj)
            assert obj.export_as_regions is True
        text = export_gerber_text(app, obj)
        assert '%ADD' not in text and 'G36*' in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'sticky')

    def test_a_representable_transform_keeps_a_flag_set_by_hand(self, app):
        obj, apid = load(app, 'C')
        obj.export_as_regions = True
        obj.scale(2.0, 2.0, SCALE_ORIGIN)
        assert obj.export_as_regions is True


# ---------------------------------------------------------------------------------------------------------------
# the table: export and parse again
# ---------------------------------------------------------------------------------------------------------------

class TestTableExportRoundTrip:
    @pytest.mark.parametrize('kind,tid', cell_params('export'))
    def test_composed_geometry_survives_the_export(self, app, tmp_path, kind, tid):
        obj, apid = load(app, kind)
        before_counts = {ap: record_count(obj, ap) for ap in obj.tools}
        flagged, _ = expected_cell(kind, tid, dict(obj.tools[apid]))
        TR_BY_ID[tid].apply(obj)
        text = export_gerber_text(app, obj)
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, '%s %s' % (kind, tid))

        goes_native = COLUMN[kind] in ('C', 'R', 'O') and not flagged and kind != 'RT'
        if goes_native:
            # native export: the aperture declarations come from the metadata and the feature counts are kept
            assert '%%ADD%d' % apid in text
            assert sorted(again.tools) == sorted(before_counts)
            for ap, count in before_counts.items():
                assert record_count(again, ap) == count, (kind, tid, ap)
            new_tool = again.tools[apid]
            old_tool = obj.tools[apid]
            assert new_tool['type'] == old_tool['type']
            for key in ('size', 'width', 'height'):
                if key in old_tool:
                    assert new_tool[key] == pytest.approx(old_tool[key], abs=1e-5), (kind, tid, key)
        else:
            assert '%ADD' not in text and 'G36*' in text, (kind, tid)
            assert set(again.tools) == {0}

    def test_the_regions_export_does_not_change_the_object(self, app):
        obj, apid = load(app, 'R')
        obj.rotate(30.0, ROT_ORIGIN)
        tools = repr(obj.tools)
        export_gerber_text(app, obj)
        assert repr(obj.tools) == tools and obj.export_as_regions is True


class TestUntransformedAndMixedFixtures:
    @pytest.mark.parametrize('kind', ['P3', 'P7', 'AM'])
    def test_untransformed_p_and_macro_round_trip_through_the_regions(self, app, tmp_path, kind):
        """The writer cannot declare P and AM apertures: the object goes through the region export before any
        transform, without the flag. This separates the writer limit from a transform regression."""
        obj, apid = load(app, kind)
        assert obj.export_as_regions is False
        assert obj.needs_region_export() is True
        text = export_gerber_text(app, obj)
        assert '%ADD' not in text and 'G36*' in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, kind)
        assert record_count(again, 0) == record_count(obj, apid)

    def test_untransformed_r_track_round_trips(self, app, tmp_path):
        """The parser makes a box record for a track of an R aperture. The native writer would flash the aperture
        at the end point instead, so such an object uses the region export."""
        obj, apid = load(app, 'RT')
        assert obj.needs_region_export() is True
        again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'R track')

    def test_an_r_flash_is_still_exported_natively(self, app):
        obj, apid = load(app, 'R')
        assert obj.needs_region_export() is False

    @pytest.mark.parametrize('tid', ['scale_up', 'scale_nonuniform', 'rotate_90', 'rotate_30', 'mirror_x',
                                     'skew_x', 'buffer_mitre', 'buffer_round', 'offset'])
    def test_region_with_a_hole(self, app, tmp_path, tid):
        obj = make_gerber(app, fx('region_hole'), name='hole')
        TR_BY_ID[tid].apply(obj)
        again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'hole ' + tid)
        # the hole is still a hole
        assert interior_count(obj.solid_geometry) == 1
        assert interior_count(again.solid_geometry) == 1

    @pytest.mark.parametrize('tid', ['scale_up', 'scale_nonuniform', 'rotate_90', 'rotate_30', 'mirror_x',
                                     'mirror_y', 'skew_x', 'buffer_mitre', 'offset'])
    def test_dark_clear_dark_fixture(self, app, tmp_path, tid):
        obj = make_gerber(app, MIXED_GBR)
        TR_BY_ID[tid].apply(obj)
        again = reparse_gerber(app, export_gerber_text(app, obj), tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'mixed ' + tid)

    @pytest.mark.parametrize('tid', ['scale_up', 'scale_neg', 'rotate_90', 'rotate_180', 'mirror_x', 'mirror_y',
                                     'scale_flip_y', 'offset', 'buffer_shrink_mitre'])
    def test_native_subset_stays_native_when_the_table_allows(self, app, tmp_path, tid):
        obj = make_gerber(app, NATIVE_GBR)
        TR_BY_ID[tid].apply(obj)
        assert obj.export_as_regions is False
        text = export_gerber_text(app, obj)
        assert '%ADD11R,' in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'native ' + tid)
        assert {ap: record_count(again, ap) for ap in again.tools} == {ap: record_count(obj, ap) for ap in obj.tools}

    @pytest.mark.parametrize('tid', ['rotate_30', 'skew_x', 'scale_nonuniform', 'buffer_round', 'buffer_mitre'])
    def test_native_subset_goes_to_regions_when_the_table_says(self, app, tmp_path, tid):
        obj = make_gerber(app, NATIVE_GBR)
        TR_BY_ID[tid].apply(obj)
        assert obj.export_as_regions is True
        text = export_gerber_text(app, obj)
        assert '%ADD' not in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'native regions ' + tid)


# ---------------------------------------------------------------------------------------------------------------
# project save and load
# ---------------------------------------------------------------------------------------------------------------

class TestProjectSaveLoad:
    def test_flag_set_by_a_transform_survives_and_exports(self, app, tmp_path):
        obj, apid = load(app, 'R')
        obj.rotate(30.0, ROT_ORIGIN)
        _, restored = save_and_load(app, [obj], tmp_path)
        assert restored[0].export_as_regions is True
        text = export_gerber_text(app, restored[0])
        assert '%ADD' not in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'project flag')

    def test_representable_transform_keeps_false_and_the_new_metadata(self, app, tmp_path):
        obj, apid = load(app, 'R')
        obj.rotate(90.0, ROT_ORIGIN)
        _, restored = save_and_load(app, [obj], tmp_path)
        grb = restored[0]
        assert grb.export_as_regions is False
        assert (grb.tools[apid]['width'], grb.tools[apid]['height']) == pytest.approx((1.2, 3.0))
        text = export_gerber_text(app, grb)
        assert '%ADD11R,1.2X3.0' in text
        again = reparse_gerber(app, text, tmp_path)
        assert_composed_equal(again.solid_geometry, obj.solid_geometry, 'project false')

    def test_p_metadata_survives(self, app, tmp_path):
        obj, apid = load(app, 'P3')
        obj.mirror('X', MIR_ORIGIN)
        _, restored = save_and_load(app, [obj], tmp_path)
        assert angle_equal(restored[0].tools[apid]['rotation'], -20.0, 3)
        assert restored[0].export_as_regions is False
