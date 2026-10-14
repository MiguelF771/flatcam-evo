"""
Optional fast fusion of Gerber polygons with Clipper2 (pyclipr).

``fuse_parts()`` replaces the GEOS closing ``MultiPolygon(parts).buffer(e).buffer(-e)``.
It returns ``None`` whenever the result could be unsafe, so the caller can use the GEOS code.
"""

from typing import Sequence

import numpy as np
import shapely
from shapely import MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

try:
    import pyclipr
except Exception:  # missing wheel, wrong platform, broken install
    pyclipr = None

# Clipper2 works with integers: coordinates are multiplied by SCALE and rounded.
SCALE: int = 10 ** 9

# Largest scaled coordinate that is safe for the 64-bit integer arithmetic of Clipper2.
_MAX_SCALED: float = 1e14

_POLYGON_ID = 3
_MULTIPOLYGON_ID = 6


def fuse_parts(parts: Sequence[BaseGeometry], closing: float = 1e-8) -> list[Polygon] | None:
    """
    Fuse polygons into one set of polygons: union, then a closing by ``closing`` (offset +/- with round joins).

    :param parts: Polygons or MultiPolygons in file units.
    :param closing: Closing distance in file units, must be greater than 0.
    :return: List of Polygons, or None when GEOS must be used (no pyclipr, unsafe input or unsafe result).
    """
    try:
        return _fuse_parts(parts, closing)
    except Exception:
        return None


def _fuse_parts(parts: Sequence[BaseGeometry], closing: float) -> list[Polygon] | None:
    """Do the work of fuse_parts(); any exception is handled by the caller."""
    geoms = np.empty(len(parts), dtype=object)
    geoms[:] = list(parts)

    if geoms.size == 0:
        return []
    type_ids = shapely.get_type_id(geoms)
    if not np.all((type_ids == _POLYGON_ID) | (type_ids == _MULTIPOLYGON_ID)):
        return None

    polys = shapely.get_parts(geoms)
    polys = polys[~shapely.is_empty(polys)]
    if polys.size == 0:
        return []

    if not closing > 0:
        return None
    if not shapely.is_valid(geoms).all():
        return None

    paths = _prepare_paths(polys)
    if paths is None or pyclipr is None:
        return None

    has_area = bool(np.any(shapely.area(polys) > 0))

    clipper = pyclipr.Clipper()
    clipper.scaleFactor = SCALE
    clipper.addPaths(paths, pyclipr.Subject)
    united = clipper.execute(pyclipr.Union, pyclipr.FillRule.NonZero)
    if len(united) == 0:
        return None if has_area else []

    grown = pyclipr.ClipperOffset()
    grown.scaleFactor = SCALE
    grown.addPaths(united, pyclipr.JoinType.Round, pyclipr.EndType.Polygon)
    expanded = grown.execute(closing)
    if len(expanded) == 0:
        return None

    shrunk = pyclipr.ClipperOffset()
    shrunk.scaleFactor = SCALE
    shrunk.addPaths(expanded, pyclipr.JoinType.Round, pyclipr.EndType.Polygon)
    tree = shrunk.execute2(-closing)

    result = _tree_to_polygons(tree)
    if len(result) == 0:
        return None if has_area else []
    if not MultiPolygon(result).is_valid:
        return None
    return result


def _prepare_paths(polys: np.ndarray) -> list[np.ndarray] | None:
    """
    Make the Clipper subject paths of the polygons: exterior CCW, holes CW, closing point removed.

    :param polys: Object array of non-empty, valid Polygons.
    :return: List of (n, 2) arrays, or None if a coordinate is unsafe or a ring collapses at scale SCALE.
    """
    rings, poly_idx = shapely.get_rings(polys, return_index=True)
    coords, ring_idx = shapely.get_coordinates(rings, return_index=True)

    if not np.isfinite(coords).all():
        return None
    if coords.size and np.abs(coords).max() * SCALE > _MAX_SCALED:
        return None

    counts = np.bincount(ring_idx, minlength=len(rings))
    ends = np.cumsum(counts)
    starts = ends - counts

    # the first ring of each polygon is the exterior, the others are holes
    is_exterior = np.ones(len(rings), dtype=bool)
    is_exterior[1:] = poly_idx[1:] != poly_idx[:-1]
    flip = shapely.is_ccw(rings) != is_exterior

    if _has_collapsed_ring(coords, counts, starts, ends):
        return None

    paths = []
    for s, e, f in zip(starts.tolist(), (ends - 1).tolist(), flip.tolist()):
        path = coords[s:e]
        paths.append(np.ascontiguousarray(path[::-1]) if f else path)
    return paths


def _quantize(coords: np.ndarray) -> np.ndarray:
    """Round scaled coordinates like pyclipr does: half away from zero."""
    scaled = coords * SCALE
    return np.copysign(np.floor(np.abs(scaled) + 0.5), scaled)


def _has_collapsed_ring(coords: np.ndarray, counts: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> bool:
    """
    Check if a ring has zero area after rounding to the integer grid of Clipper.

    The area is computed in float64 on coordinates relative to the first point of each ring. A ring whose area is
    not clearly above the float rounding error is checked again with Python integers (exact).
    """
    q = _quantize(coords)
    rel = q - np.repeat(q[starts], counts, axis=0)

    x = rel[:, 0]
    y = rel[:, 1]
    x_next = np.roll(x, -1)
    y_next = np.roll(y, -1)
    cross = x * y_next - x_next * y
    magnitude = np.abs(x * y_next) + np.abs(x_next * y)
    # the pair (last point of a ring, first point of the next ring) is not an edge
    cross[ends - 1] = 0.0
    magnitude[ends - 1] = 0.0

    area2 = np.add.reduceat(cross, starts)
    bound = np.add.reduceat(magnitude, starts)
    uncertain = np.nonzero(np.abs(area2) <= 1e-6 * bound)[0]

    for k in uncertain.tolist():
        ring = q[starts[k]:ends[k]].astype(np.int64).tolist()
        x0, y0 = ring[0]
        total = 0
        for (xa, ya), (xb, yb) in zip(ring[:-1], ring[1:]):
            total += (xa - x0) * (yb - y0) - (xb - x0) * (ya - y0)
        if total == 0:
            return True
    return False


def _tree_to_polygons(tree) -> list[Polygon]:
    """Convert a Clipper PolyTree into Shapely Polygons: holes are the children, islands in holes are new polygons."""
    result = []
    stack = list(tree.children)
    while stack:
        node = stack.pop()
        holes = []
        for hole in node.children:
            holes.append(hole.polygon)
            stack.extend(hole.children)
        result.append(Polygon(node.polygon, holes))
    return result
