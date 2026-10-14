"""Shared input cases and the LEGACY expressions of the Gerber normalization that the plugins used before they
switched to ``appParsers.feature_access.fused_solid`` (Stage 2). Headless.

Each ``legacy_*`` function is a verbatim copy of the old expression, so the old behavior stays documented and
comparable. ``outcome`` runs a function and returns ('ok', result) or ('raises', exception type name).
"""

import shapely
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union


def bowtie():
    return Polygon([(0, 0), (4, 4), (4, 0), (0, 4)])


def case_geometries(parsed):
    """name -> value for ``solid_geometry`` of a Gerber-like object. ``parsed`` is the solid_geometry of unify_mixed."""
    return {
        'mixed_dark_clear_dark': parsed,
        'overlapping_list': [box(0, 0, 4, 4), box(2, 2, 6, 6), box(5, 0, 8, 3)],
        'self_intersecting': [bowtie(), box(10, 10, 12, 12)],
        'single_polygon': box(0, 0, 3, 3),
        'multipolygon': MultiPolygon([box(0, 0, 1, 1), box(5, 5, 6, 6)]),
    }


def outcome(fn, *args):
    try:
        return 'ok', fn(*args)
    except Exception as err:  # noqa: BLE001 - the characterization records any failure of the old code
        return 'raises', type(err).__name__


def area_diff(a, b):
    a = shapely.make_valid(unary_union(a) if isinstance(a, list) else a)
    b = shapely.make_valid(unary_union(b) if isinstance(b, list) else b)
    return a.symmetric_difference(b).area


# ---- legacy expressions (verbatim from the code before the Stage 2 change) --------------------------------------

def legacy_ncc_clip(obj, buffering_off):
    """NccGen.clear_area_handler, non-isolation branch."""
    fused_solid_geometry = unary_union(obj.solid_geometry)
    if buffering_off:
        return fused_solid_geometry.buffer(0.000000001)
    return fused_solid_geometry


def legacy_ncc_iso(obj, buffering_off):
    """NccGen.clear_area_handler, isolation branch."""
    fused_solid_geometry = unary_union(obj.solid_geometry)
    if buffering_off:
        return fused_solid_geometry.buffer(0)
    return fused_solid_geometry


def legacy_ncc_rest_clip(obj, buffering_off):
    """NccGen rest machining, non-isolation branch."""
    if buffering_off:
        return obj.solid_geometry.buffer(0)
    sol_geo = obj.solid_geometry
    if isinstance(sol_geo, list):
        sol_geo = unary_union(sol_geo)
    return sol_geo


def legacy_ncc_rest_iso(obj, buffering_off):
    """NccGen rest machining, isolation branch."""
    if buffering_off:
        return obj.solid_geometry.buffer(0)
    return obj.solid_geometry
