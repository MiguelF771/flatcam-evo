# ##########################################################
# FlatCAM EVO: 2D Post-processing for Manufacturing        #
# Read accessors shared by the Gerber and Excellon objects #
# MIT Licence                                              #
# ##########################################################

"""
Read-only access to the features of a Gerber or Excellon object (Stage 2 of the unification).

The functions work on the data of the object, not on ``obj.kind``:

* a Gerber-like tool holds ``tools[ap]['geometry']`` (a list of record dicts with 'solid' or 'clear', 'follow');
* an Excellon-like tool holds ``tools[t]['drills']`` and ``tools[t]['slots']`` and ``tools[t]['tooldia']``.

So ``Gerber``, ``Excellon``, ``GerberObject`` and ``ExcellonObject`` all work. Nothing here changes the object.

Contract
--------
* Order: tool (aperture) iteration order, feature order kept inside each tool. There is NO global order:
  Gerber records hold no sequence number, so a dark -> clear -> dark sequence over several apertures
  cannot be recovered from the records. Use ``fused_solid()`` for the composed copper.
* ``(item_key, feature_index)`` identifies a feature only until the feature list of that item changes
  (an insert, a delete or a re-parse invalidates the indexes).
* ``solid`` is the footprint of the record. For a clear feature it is the 'clear' geometry and
  ``polarity == 'clear'``; it is NOT copper. Consumers that want copper use ``fused_solid()``.
* ``size`` can be None (macro apertures without a 'size' key, region key 0).

Gerber mapping (parser: ``ParseGerber.py``, records appended in ``_add_track_to_buffers`` /
``_add_flash_to_buffers`` / the region end handler):

* A record stores EITHER 'solid' (current polarity dark) OR 'clear' (current polarity clear), never both
  (the parser picks one key from ``self.is_lpc``). So 1 record gives 1 FeatureRecord. If a record built by
  other code holds both keys, 2 FeatureRecords with the same (item_key, feature_index) are yielded:
  the dark one first, then the clear one.
* A record with neither key (the parser keeps the 'follow' only when the solid is empty) gives a
  dark FeatureRecord with an empty Polygon as ``solid``.
* kind: tool key 0 or tool type 'REG' -> 'region'; follow is a Point -> 'flash'; any other record -> 'route'.
* ``follow`` is the 'follow' geometry of the record, None if the record has none.

Excellon mapping: drills first (feature_index 0..nd-1), then slots (nd..nd+ns-1). Drill: kind 'flash',
follow = the drill Point, solid = ``drill.buffer(tooldia / 2.0, int(obj.excellon_circle_steps))``.
Slot: kind 'route', follow = ``LineString([start, stop])``, solid = that line buffered the same way
(identical to ``Excellon.create_geometry``, so the solids equal the parts of ``tools[t]['solid_geometry']``).
polarity is always 'dark', size = tooldia.
"""

from typing import Any, Iterator, NamedTuple, Optional

import shapely
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from camlib import flatten_shapely_geometry


class FeatureRecord(NamedTuple):
    item_key: Any
    feature_index: int
    kind: str  # 'flash' | 'route' | 'region'
    polarity: str  # 'dark' | 'clear'
    solid: BaseGeometry
    follow: Optional[BaseGeometry]
    size: Any


def _tools(obj) -> dict:
    tools = getattr(obj, 'tools', None)
    return tools if isinstance(tools, dict) else {}


def _is_excellon_tool(tool: dict) -> bool:
    return 'drills' in tool or 'slots' in tool


def _iter_gerber_tool(key, tool: dict) -> Iterator[FeatureRecord]:
    size = tool.get('size')
    is_region_tool = key == 0 or tool.get('type') == 'REG'
    for index, record in enumerate(tool.get('geometry') or ()):
        follow = record.get('follow')
        if is_region_tool:
            kind = 'region'
        elif isinstance(follow, Point):
            kind = 'flash'
        else:
            kind = 'route'

        # a value that is not a geometry (older code stored [] for a missing key) counts as missing
        solid = _geometry_or_none(record.get('solid'))
        clear = _geometry_or_none(record.get('clear'))
        if solid is None and clear is None:
            yield FeatureRecord(key, index, kind, 'dark', Polygon(), follow, size)
            continue
        if solid is not None:
            yield FeatureRecord(key, index, kind, 'dark', solid, follow, size)
        if clear is not None:
            yield FeatureRecord(key, index, kind, 'clear', clear, follow, size)


def _geometry_or_none(value):
    """The value if it is a shapely geometry, else None."""
    return value if isinstance(value, BaseGeometry) else None


def _iter_excellon_tool(obj, key, tool: dict) -> Iterator[FeatureRecord]:
    tooldia = tool.get('tooldia')
    if tooldia is None:
        return
    steps = int(obj.excellon_circle_steps)
    radius = tooldia / 2.0
    index = 0
    for drill in tool.get('drills') or ():
        yield FeatureRecord(key, index, 'flash', 'dark', drill.buffer(radius, steps), drill, tooldia)
        index += 1
    for slot in tool.get('slots') or ():
        line = LineString([slot[0], slot[1]])
        yield FeatureRecord(key, index, 'route', 'dark', line.buffer(radius, steps), line, tooldia)
        index += 1


def iter_features(obj) -> Iterator[FeatureRecord]:
    """Yield a FeatureRecord for every feature of the object (see the module docstring for the contract)."""
    for key, tool in _tools(obj).items():
        if _is_excellon_tool(tool):
            yield from _iter_excellon_tool(obj, key, tool)
        else:
            yield from _iter_gerber_tool(key, tool)


def iter_items(obj) -> Iterator[tuple]:
    """Yield ``(item_key, size)`` for every aperture / tool, in iteration order, also for items without features.
    size is ``tools[key].get('size')`` for a Gerber (can be None) and ``tooldia`` for an Excellon."""
    for key, tool in _tools(obj).items():
        if _is_excellon_tool(tool):
            yield key, tool.get('tooldia')
        else:
            yield key, tool.get('size')


def normalize_polygonal(geoms) -> BaseGeometry:
    """
    Flatten ``geoms`` (a geometry, or any nesting of lists / Multi* / GeometryCollection), repair each part with
    ``shapely.make_valid``, keep only the polygonal parts (lines and points are dropped; the Polygon members of a
    GeometryCollection produced by the repair are kept) and ``unary_union`` them.

    The result is always a Polygon or MultiPolygon; for empty input it is an empty ``Polygon()``.
    No tolerance buffer is applied. A failure of the repair or of the union is raised, not swallowed.
    """
    polygons = []
    for geo in flatten_shapely_geometry(geoms if geoms is not None else []):
        if geo is None or geo.is_empty:
            continue
        geo = shapely.make_valid(geo)
        for part in flatten_shapely_geometry(geo):
            if isinstance(part, Polygon) and not part.is_empty:
                polygons.append(part)
    if not polygons:
        return Polygon()
    result = unary_union(polygons)
    if not isinstance(result, (Polygon, MultiPolygon)):
        # cannot happen for polygonal input; keep the "always polygonal" guarantee explicit
        return normalize_polygonal(result)
    return result


def fused_solid(obj) -> BaseGeometry:
    """
    A NEW valid Polygon / MultiPolygon with the copper (Gerber) or the drill and slot solids (Excellon).
    The object is not changed.

    Gerber: ``normalize_polygonal(obj.solid_geometry)``, the composed geometry with the polarity already applied;
    it is never rebuilt from the records. Excellon: ``normalize_polygonal`` of the solids of the drills and slots.
    """
    tools = _tools(obj)
    if any(_is_excellon_tool(tool) for tool in tools.values()):
        return normalize_polygonal([rec.solid for rec in iter_features(obj)])
    return normalize_polygonal(getattr(obj, 'solid_geometry', None))


def is_empty(obj) -> bool:
    """
    True if the object holds no geometry: no feature with a non-empty ``solid`` and, for a Gerber, no non-empty part
    in the composed ``solid_geometry``.

    This matches the checks the app does today: the editor exit test ``len(solid_geometry) == 0``
    (``appMain.on_editing_finished``) and ``not obj.tools`` (``appEdit``): an object without tools or with an empty
    ``solid_geometry`` list is empty; an object with a single Polygon in ``solid_geometry`` is not. The composed
    geometry is checked too because a Gerber can hold geometry without records.
    """
    for rec in iter_features(obj):
        if rec.solid is not None and not rec.solid.is_empty:
            return False
    tools = _tools(obj)
    if any(_is_excellon_tool(tool) for tool in tools.values()):
        return True
    return not flatten_shapely_geometry(getattr(obj, 'solid_geometry', None) or [])
