"""Offset benchmark used for the adjacent Shapely/Clipper2 findings report.

Run with FlatCAM's .venv Python. Optional FLATCAM_BENCH_DEPS points to an isolated
pyclipr installation. This benchmarks offset generation, not Qt, R-tree insertion,
path connection, rest machining or complete Paint/NCC execution. Units are mm.
"""
import argparse
import gc
import json
import math
import os
import platform
import statistics
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if os.environ.get("FLATCAM_BENCH_DEPS"):
    sys.path.insert(0, os.environ["FLATCAM_BENCH_DEPS"])
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pyclipr
import shapely
from shapely import LineString, MultiPolygon, Point, Polygon, box
from shapely.affinity import scale
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

SCALE = 1_000_000
TOOL = 0.3
STEP = TOOL * (1 - 0.2)
REPEATS = 5
SIMPLIFY = 0.0001  # 0.1 micrometre, applied between offsets for BOTH engines


def polygons(geometry):
    if geometry.is_empty:
        return []
    return [geometry] if isinstance(geometry, Polygon) else list(geometry.geoms)


def paths_from_geometry(geometry):
    result = []
    for poly in polygons(geometry):
        poly = orient(poly, sign=1.0)
        result.append(np.asarray(poly.exterior.coords)[:-1])
        result.extend(np.asarray(r.coords)[:-1] for r in poly.interiors)
    return result


def offset(paths, distance, quadrants, tree=False):
    worker = pyclipr.ClipperOffset()
    worker.scaleFactor = SCALE
    # pyclipr exposes ArcTolerance in INTERNAL INTEGER units, unlike distance.
    worker.arcTolerance = abs(distance) * (1 - math.cos(math.pi / (4 * quadrants))) * SCALE
    worker.addPaths(paths, pyclipr.JoinType.Round, pyclipr.EndType.Polygon)
    return worker.execute2(distance) if tree else worker.execute(distance)


def geometry_from_tree(tree):
    result = []

    def walk(node):
        if not node.isHole and len(node.polygon):
            result.append(Polygon(node.polygon, [n.polygon for n in node.children if n.isHole]))
        for child in node.children:
            walk(child)

    walk(tree)
    return MultiPolygon(result) if len(result) != 1 else result[0]


def buffer_sequence(geometry, quadrants, simplify_tol=SIMPLIFY, limit=500):
    current = geometry
    output = []
    for index in range(limit):
        distance = -TOOL / 2 if index == 0 else -STEP
        current = current.buffer(distance, quad_segs=quadrants)
        if current.is_empty:
            return output
        if simplify_tol:
            current = current.simplify(simplify_tol, preserve_topology=True)
        output.append(current)
    if limit == 500:
        raise AssertionError("Shapely sequence failed to terminate")
    return output


def clip_sequence(paths, quadrants, simplify_tol=SIMPLIFY, limit=500):
    output = []
    for index in range(limit):
        distance = -TOOL / 2 if index == 0 else -STEP
        paths = offset(paths, distance, quadrants)
        if not paths:
            return output
        if simplify_tol:
            paths = pyclipr.simplifyPaths(paths, epsilon=simplify_tol * SCALE, scaleFactor=SCALE)
        output.append(paths)
    if limit == 500:
        raise AssertionError("Clipper sequence failed to terminate")
    return output


def clip_sequence_with_conversion(geometry, quadrants):
    paths = paths_from_geometry(geometry)
    output = clip_sequence(paths, quadrants)
    # Return the generated contours as Shapely lines, as a FlatCAM adapter would.
    return [LineString(np.vstack((path, path[0]))) for level in output for path in level]


def shapely_sequence_with_conversion(geometry, quadrants):
    return [ring for level in buffer_sequence(geometry, quadrants)
            for poly in polygons(level) for ring in [poly.exterior, *poly.interiors]]


def timed(fn):
    start = perf_counter()
    fn()  # warm-up, including lazy native initialization
    iterations = min(100, max(1, math.ceil(0.01 / (perf_counter() - start))))
    samples = []
    for _ in range(REPEATS):
        gc.collect()
        start = perf_counter()
        for _ in range(iterations):
            result = fn()
            del result
        samples.append((perf_counter() - start) * 1000 / iterations)
    return round(statistics.median(samples), 3)


def workloads():
    cases: list[tuple[str, BaseGeometry, str]] = [("rectangle", box(0, 0, 80, 50), "synthetic 80x50 mm")]
    holes = [Point(4 + x * 6, 4 + y * 6).buffer(0.65, quad_segs=32)
             for x in range(12) for y in range(7)]
    cases.append(("board_84_holes", Polygon(box(0, 0, 76, 46).exterior.coords,
                  [h.exterior.coords for h in holes]), "synthetic 76x46 mm, 84 circular holes"))
    holes = [Point(3 + x * 5, 3 + y * 5).buffer(0.6, quad_segs=32)
             for x in range(20) for y in range(12)]
    cases.append(("board_240_holes", Polygon(box(0, 0, 102, 62).exterior.coords,
                  [h.exterior.coords for h in holes]), "synthetic 102x62 mm, 240 circular holes"))
    coords = [(30 + (20 if i % 2 == 0 else 14) * math.cos(i * math.pi / 250),
               30 + (20 if i % 2 == 0 else 14) * math.sin(i * math.pi / 250))
              for i in range(500)]
    cases.append(("concave_500_vertices", Polygon(coords), "synthetic 500-vertex star"))

    import test_levelling_journey as journey
    for filename in journey.ALL_GERBERS:
        assert Path(filename).exists(), filename
        gerber = journey.Gerber(journey.MockGerberApp())
        assert gerber.parse_file(filename) != "fail"
        copper = unary_union(gerber.solid_geometry) if isinstance(gerber.solid_geometry, list) else gerber.solid_geometry
        if gerber.units == "IN":
            copper = scale(copper, xfact=25.4, yfact=25.4, origin=(0, 0))
        x0, y0, x1, y1 = copper.bounds
        factor = 40 / max(x1 - x0, y1 - y0) if max(x1 - x0, y1 - y0) > 150 else 1
        if factor != 1:
            copper = scale(copper, xfact=factor, yfact=factor, origin=(0, 0))
        x0, y0, x1, y1 = copper.bounds
        empty = box(x0 - 2, y0 - 2, x1 + 2, y1 + 2).difference(copper)
        cases.append((Path(filename).stem + "_ncc", empty,
                      "parsed Gerber complement, 2 mm margin; coordinate scale=%g" % factor))
    return cases


def validate_sequence(geometry, quadrants):
    reference = buffer_sequence(geometry, quadrants)
    clipper_output = clip_sequence(paths_from_geometry(geometry), quadrants)
    comparisons = []
    for index, paths in enumerate(clipper_output):
        distance = -TOOL / 2 if index == 0 else -STEP
        worker = pyclipr.Clipper()
        worker.scaleFactor = SCALE
        worker.addPaths(paths, pyclipr.Subject)
        actual = geometry_from_tree(worker.execute2(pyclipr.Union, pyclipr.FillRule.NonZero))
        assert actual.is_valid, "invalid Clipper offset"
        if index >= len(reference):
            comparisons.append({"pass": index, "extra_clipper_pass": True})
            continue
        expected = reference[index]
        arc_error = abs(distance) * (1 - math.cos(math.pi / (4 * quadrants)))
        allowance = 4 * (index + 1) * (SIMPLIFY + max(arc_error, TOOL / 2 *
                     (1 - math.cos(math.pi / (4 * quadrants)))) + 2 / SCALE)
        # Validation only: trim dense redundant arc vertices before overlays.
        # This doesn't change either engine's timed input or generated output.
        comparison_expected = expected.simplify(allowance / 8, preserve_topology=True)
        comparison_actual = actual.simplify(allowance / 8, preserve_topology=True)
        difference = comparison_actual.symmetric_difference(comparison_expected)
        # Compare filled regions, not ring order or point-for-point identity.
        outside_reference_band = difference.difference(comparison_expected.boundary.buffer(allowance))
        assert outside_reference_band.area < 1e-7, (index, outside_reference_band.area)
        assert actual.difference(geometry.buffer(allowance)).area < 1e-7
        comparisons.append({"pass": index,
                            "symmetric_difference_mm2": difference.area,
                            "polygons_shapely": len(polygons(expected)),
                            "polygons_clipper": len(polygons(actual)),
                            "allowed_boundary_band_mm": allowance})
    return {"passes_shapely": len(reference), "passes_clipper": len(comparisons),
            "max_symmetric_difference_mm2": round(max((r.get("symmetric_difference_mm2", 0)
                                                       for r in comparisons), default=0), 8),
            "topology_count_mismatches": sum(r.get("polygons_shapely") != r.get("polygons_clipper")
                                             for r in comparisons),
            "extra_clipper_passes": sum(r.get("extra_clipper_pass", False) for r in comparisons)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", nargs="*", help="Case names; omit to run all seven cases")
    parser.add_argument("--q16", action="store_true", help="Only run quad_segs=16 instead of 16 and 64")
    parser.add_argument("--diagnose", action="store_true", help="Reproduce unsimplified vertex growth (slow)")
    parser.add_argument("--output-dir", type=Path,
                        default=Path(tempfile.gettempdir()) / "flatcam-offset-benchmark")
    args = parser.parse_args()
    cases = workloads()
    unknown = set(args.cases) - {name for name, _, _ in cases}
    if unknown:
        parser.error("Unknown cases: " + ", ".join(sorted(unknown)))
    if args.diagnose:
        _, current, _ = cases[1]
        paths = paths_from_geometry(current)
        for index in range(30):
            start = perf_counter()
            paths = offset(paths, -TOOL / 2 if index == 0 else -STEP, 16)
            print(json.dumps({"engine": "clipper", "pass": index,
                              "vertices": sum(len(p) for p in paths),
                              "buffer_ms": (perf_counter() - start) * 1000}), flush=True)
            if not paths:
                break
        for index in range(30):
            start = perf_counter()
            current = current.buffer(-TOOL / 2 if index == 0 else -STEP, quad_segs=16)
            print(json.dumps({"pass": index, "vertices": int(shapely.get_num_coordinates(current)),
                              "buffer_ms": (perf_counter() - start) * 1000}), flush=True)
            if current.is_empty:
                break
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"python": sys.version, "platform": platform.platform(),
                      "shapely": shapely.__version__, "geos": shapely.geos_version_string,
                      "numpy": np.__version__, "pyclipr": version("pyclipr"), "repeats": REPEATS,
                      "tool_mm": TOOL, "overlap": 0.2, "integer_grid_mm": 1 / SCALE,
                      "between_pass_simplification_mm": SIMPLIFY}), flush=True)
    rows = []
    for name, geometry, description in cases:
        if args.cases and name not in args.cases:
            continue
        assert geometry.is_valid and not geometry.is_empty, name
        paths = paths_from_geometry(geometry)
        for quadrants in ((16,) if args.q16 else (16, 64)):
            print("START " + name + " quad_segs=" + str(quadrants), flush=True)
            validation = validate_sequence(geometry, quadrants)
            row = {"case": name, "description": description, "quad_segs": quadrants,
                   "input_vertices": sum(len(p) for p in paths), **validation}
            row["single_shapely_ms"] = timed(lambda: geometry.buffer(-TOOL / 2, quad_segs=quadrants))
            row["single_clipper_native_ms"] = timed(lambda: offset(paths, -TOOL / 2, quadrants))
            row["single_clipper_converted_ms"] = timed(lambda: geometry_from_tree(
                offset(paths_from_geometry(geometry), -TOOL / 2, quadrants, tree=True)))
            row["sequence_shapely_native_ms"] = timed(lambda: buffer_sequence(geometry, quadrants))
            row["sequence_clipper_native_ms"] = timed(lambda: clip_sequence(paths, quadrants))
            row["sequence_shapely_contours_ms"] = timed(lambda: shapely_sequence_with_conversion(geometry, quadrants))
            row["sequence_clipper_contours_ms"] = timed(lambda: clip_sequence_with_conversion(geometry, quadrants))
            row["max_vertices_shapely_cleaned"] = max((int(shapely.get_num_coordinates(g))
                                                       for g in buffer_sequence(geometry, quadrants)), default=0)
            row["max_vertices_clipper_cleaned"] = max((sum(len(p) for p in level)
                                                       for level in clip_sequence(paths, quadrants)), default=0)
            if quadrants == 16:
                row["raw_first_5_shapely_ms"] = timed(lambda: buffer_sequence(geometry, quadrants, 0, 5))
                row["raw_first_5_clipper_ms"] = timed(lambda: clip_sequence(paths, quadrants, 0, 5))
            rows.append(row)
            print(json.dumps(row), flush=True)
            (args.output_dir / ("benchmark_shapely_clipper2_" + name + ".json")).write_text(
                json.dumps([r for r in rows if r["case"] == name], indent=2), encoding="utf-8")
    output = args.output_dir / "benchmark_shapely_clipper2.json"
    output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("RESULTS " + str(output), flush=True)


if __name__ == "__main__":
    main()
