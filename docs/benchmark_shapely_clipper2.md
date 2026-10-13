# Paint/NCC offset benchmark: Shapely versus Clipper2

Date: 2026-10-13

Benchmark script: [benchmark_shapely_clipper2.py](benchmark_shapely_clipper2.py)

## Application follow-up

After these measurements, the shared Paint/NCC clearing and trace-filling routines
were corrected to convert full-circle steps to Shapely quadrant segments:
`max(1, ceil(steps_per_circle / 4))`. Thus 64 circle steps now means 16 quadrant
segments. `paint_connect()` now accumulates coordinates and builds each completed
path once. Its connection check also verifies the real tool-radius clearance, so
a coarser polygonal buffer cannot admit a connector that overhangs a boundary or hole.
The observations below describe the pre-fix code. The recorded timings and standalone
benchmark script are unchanged; its `quad_segs` values explicitly count quadrant segments.

## Conclusion

Clipper2 through `pyclipr` is worth prototyping for the shared Paint/NCC
shrinking algorithm, provided redundant vertices are removed between offsets.
It isn't a consistently faster drop-in replacement for `Polygon.buffer()`.

In the cleaned contour-generation workloads, Clipper2 was approximately
3-13 times faster, including conversion of its generated contours back to
Shapely lines. In the unmodified first-five-offset comparison on the two
synthetic holed boards, Clipper2 was approximately 1.8-1.9 times slower.

These measurements compare offset-generation pipelines. They aren't measured
speedups for the complete Paint or NCC tools. Different simplification
algorithms contribute to the cleaned-pipeline results.

## Application context

Paint and NCC dispatch their Standard, Seed and Lines clearing methods to shared
`camlib.Geometry` routines:

- `appPlugins/ToolPaint/PaintGen.py`: `paint_polygon_worker()`.
- `appPlugins/ToolNCC/NccGen.py`: `clear_polygon_worker()`.
- `camlib.py`: `clear_polygon_shrink()`, `clear_polygon_seed()` and
  `clear_polygon_lines()`.

Standard clearing repeatedly applies a negative buffer, collects the resulting
contours and continues until the polygon disappears. This benchmark models that
repeated-offset workload, but doesn't invoke these application methods directly.
In particular, it offsets the whole current Polygon/MultiPolygon together rather
than reproducing every per-polygon branch in the application.

Shapely already delegates buffering to compiled GEOS code. A Rust translation
that invokes the same GEOS operations wouldn't automatically accelerate them.
Clipper2 changes the underlying offset implementation instead.

## Environment and method

| Setting | Value |
| --- | --- |
| OS | Windows 11, build 26200, x64 |
| Python | CPython 3.14.3 |
| Shapely | 2.1.2 |
| GEOS | 3.13.1 |
| NumPy | 2.4.3 |
| Clipper2 wrapper | `pyclipr` 0.1.8 |
| Tool diameter | 0.3 mm |
| Overlap | 20% |
| Initial inset | 0.15 mm |
| Subsequent inset | 0.24 mm |
| Clipper coordinate scale | 1,000,000; grid spacing 0.000001 mm |
| Measurements | Median of five timed batches after warm-up |

Geometry construction, Gerber parsing and correctness comparisons happen outside
the timed regions. Fast operations are batched to reduce timer noise. CPU model,
background load and power state weren't recorded or controlled. Separate runs
showed timing variation, so ratios should be treated as workload-specific evidence
rather than fixed performance guarantees.

### Arc approximation

Both engines use rounded joins. For a Shapely `quad_segs` value `q` and an offset
radius `r`, Clipper's arc tolerance is selected using:

```text
arc_error = abs(r) * (1 - cos(pi / (4 * q)))
```

This matches the nominal circular-chord error, not the exact placement or count
of vertices. Clipper and GEOS can produce different approximations and topology
transitions near vanishing features.

The benchmark found that `pyclipr` 0.1.8 exposes `arcTolerance` and the
`simplifyPaths()` epsilon in internally scaled units. Offset distances are
supplied in the original coordinate units. The script applies that distinction.

### Between-pass cleanup

The cleaned pipeline applies a nominal 0.0001 mm (0.1 micrometre) simplification
setting after each successful offset:

- Shapely: `simplify(..., preserve_topology=True)`.
- Clipper: `pyclipr.simplifyPaths(...)`.

These are different algorithms. Equal numeric settings don't establish equal
global error bounds or identical topology-preservation guarantees. Cleanup is
included in the timings and contributes to the measured advantage.

This cleaned Shapely pipeline isn't the current unmodified Standard clearing
implementation. The figures below therefore mustn't be presented as direct
before/after timings for the application.

## Workloads

| Case name | Geometry |
| --- | --- |
| `rectangle` | Synthetic 80 x 50 mm rectangle |
| `board_84_holes` | Synthetic 76 x 46 mm region with 84 circular holes; 10,756 input vertices |
| `board_240_holes` | Synthetic 102 x 62 mm region with 240 circular holes; 30,724 input vertices |
| `concave_500_vertices` | Synthetic star-shaped polygon with 500 vertices |
| `simple_line_ncc` | Complement of the parsed `simple_line.gbr` fixture, with a 2 mm bounding-box margin |
| `region_test_ncc` | Complement of the parsed `region_test.gbr` fixture, with a 2 mm bounding-box margin |
| `flash_test_ncc` | Complement of the parsed `flash_test.gbr` fixture, with a 2 mm bounding-box margin |

The Gerbers are actual repository test files, not production board designs. The
line and region fixtures span approximately 2,540 mm after converting their inch
coordinates. They were rescaled to a 40 mm extent before constructing the NCC
region. The flash fixture wasn't rescaled. Accordingly, these are fixture-derived
geometry workloads, not benchmarks of those Gerbers at their original size.

## Results

### Complete cleaned contour generation

The main comparison uses `quad_segs=16` (nominally 64 segments per full circle).
Shapely returns its generated rings. Clipper timings include input conversion,
all offset/cleanup passes and construction of closed Shapely LineStrings for all
generated contours. R-tree insertion and path connection are excluded.

| Workload | Nonempty passes | Shapely, ms | Clipper2, ms | Shapely / Clipper2 |
| --- | ---: | ---: | ---: | ---: |
| 84-hole region | 15 | 1,632.863 | 131.056 | 12.46x |
| 240-hole region | 12 | 7,641.122 | 573.171 | 13.33x |
| 500-vertex concave polygon | 58 | 174.777 | 44.735 | 3.91x |
| Line Gerber NCC fixture | 54 | 5.405 | 1.798 | 3.01x |
| Region Gerber NCC fixture | 5 | 1.899 | 0.505 | 3.76x |
| Flash Gerber NCC fixture | 5 | 1.903 | 0.487 | 3.91x |

The two holed-board rows use the final completed run of those cases. The star and
fixture rows use the separately completed run of those cases.

### Raw first five offsets: no between-pass cleanup

This is a bounded prefix, not a complete clearing operation. Clipper input paths
are prepared before timing, so this comparison doesn't charge Clipper for initial
conversion. Both engines retain the successive results.

| Workload | Shapely, ms | Clipper2, ms | Result |
| --- | ---: | ---: | --- |
| 84-hole region | 175.018 | 313.195 | Clipper2 about 1.79x slower |
| 240-hole region | 486.241 | 942.875 | Clipper2 about 1.94x slower |
| 500-vertex concave polygon | 26.252 | 23.092 | Clipper2 about 1.14x faster |

### Single inset, including Clipper conversion

This compares one Shapely inset against conversion to Clipper paths, one Clipper
inset and conversion of the result back to Shapely polygons. No between-pass
cleanup is involved.

| Workload | Shapely, ms | Clipper2 with conversions, ms |
| --- | ---: | ---: |
| 84-hole region | 4.624 | 5.272 |
| 240-hole region | 29.666 | 29.898 |
| 500-vertex concave polygon | 1.288 | 4.294 |

The single-operation comparison doesn't support replacing individual buffer calls
on the assumption that each call will become faster.

### Higher arc resolution

Completed runs at `quad_segs=64` also showed a cleaned-pipeline advantage. For
example, the star case measured 302.905 ms for Shapely and 69.283 ms for Clipper
with contour conversion. These measurements use the same 0.0001 mm cleanup
setting, so cleanup can dominate the finer arc-approximation error.

The combined run stopped while processing the 240-hole high-resolution case.
There's no completed `quad_segs=64` result for that case. Completed individual
rows from an interrupted run aren't evidence that the entire run completed.

## Why the initial benchmark took so long

Feeding every densely tessellated offset directly into the next pass caused
vertex growth in both engines. Diagnostic measurements on the 84-hole region at
`quad_segs=16` found:

- Shapely grew from 10,756 input vertices to about 688,000 coordinates after six
  offsets. Shapely coordinate counts include closing ring coordinates.
- Clipper grew to about 1.74 million vertices after eight offsets. Its eighth
  offset alone took approximately 100.9 seconds.
- Geometry comparisons of the dense intermediate results added substantial
  cost. The benchmark's first combined runs exceeded their process time limits.

After these observations, the main comparison added between-pass cleanup for
both engines. Geometry validation also simplifies its comparison copies before
overlay operations. That validation-only step doesn't change timed generation.

Clipper2's documentation explicitly recommends removing redundant segments
before and between offsets. Omitting this step produced a misleadingly poor
pipeline, but adding it doesn't make Clipper a universally faster offsetter.

## Correctness checks and limits

For the completed cleaned workloads, the benchmark checked:

1. Clipper results form valid Polygon/MultiPolygon geometry.
2. Filled-region differences stay within a conservative, pass-dependent boundary
   band around the Shapely reference, subject to a 0.0000001 mm2 residual-area
   threshold.
3. Results stay inside the original region expanded by that same band.
4. Nonempty pass counts and polygon-component counts are recorded. They matched
   for the completed cases reported above.

The comparison band is:

```text
4 * (pass_index + 1) *
    (cleanup_setting + max(current_arc_error, initial_arc_error) + 2 / scale)
```

For the 84-hole case at `quad_segs=16`, this reaches approximately 0.0235 mm at
the last nonempty pass. It isn't a verified 0.0001 mm global machining tolerance.
The overlay comparisons additionally simplify their copies at one eighth of the
band width. The recorded symmetric differences are therefore differences between
those comparison copies, not exact differences of the untouched generated paths.

The maximum recorded per-pass symmetric-difference areas at `quad_segs=16` were
approximately 0.5440 mm2 for the 84-hole case and 0.9296 mm2 for the 240-hole case.
The outputs weren't identical. Matching component counts isn't a full topology
proof: hole counts, tool-swept coverage, residual uncut regions, path direction,
cutting order and machine execution weren't verified here.

Before adopting a backend, validate the actual tool-swept area against copper
keep-outs and required clearance, plus narrow channels, disappearing islands and
holes, unit conversion and precision scaling.

## Additional existing-code observation

`clear_polygon_shrink()` passes `steps_per_circle` directly as the buffer
resolution. Shapely interprets that parameter as segments per quarter circle.
A local check with Shapely 2.1.2 confirmed:

| Buffer resolution | Segments around a buffered point |
| --- | ---: |
| 64 | 256 |
| 16 | 64 |

If the application's setting is intended to describe a full circle, its conversion
at these call sites needs checking. This observation doesn't establish a measured
fourfold speedup, and the benchmark didn't change the application's setting.

`paint_connect()` also repeatedly reconstructs an accumulated LineString when
joining paths. That remains a separate candidate for profiling. It was excluded
from these timings.

## Reproduction

Use the project's existing virtual environment and dependencies. `pyclipr` 0.1.8
was installed into a temporary directory for the original measurements; it wasn't
added to FlatCAM's requirements or virtual environment.

An isolated Windows setup from the repository root is:

```powershell
$env:FLATCAM_BENCH_DEPS = Join-Path $env:TEMP "flatcam-clipper2-deps"
& ".venv\Scripts\python.exe" -m pip install --target "$env:FLATCAM_BENCH_DEPS" --no-deps --only-binary=:all: pyclipr==0.1.8
```

The script accepts case names and an optional `--q16` flag. Start with a small
case to verify the environment:

```powershell
& ".venv\Scripts\python.exe" "docs\benchmark_shapely_clipper2.py" rectangle --q16
```

Run the main holed-board cases:

```powershell
& ".venv\Scripts\python.exe" "docs\benchmark_shapely_clipper2.py" board_84_holes board_240_holes --q16
```

Run the star and fixture cases:

```powershell
& ".venv\Scripts\python.exe" "docs\benchmark_shapely_clipper2.py" concave_500_vertices simple_line_ncc region_test_ncc flash_test_ncc
```

Without case names, all cases are selected. Without `--q16`, both 16 and 64 are
used. Complete runs can take several minutes. The `--diagnose` mode intentionally
runs the unsimplified vertex-growth reproduction and can be much slower.

By default, JSON measurements are written under the system temporary directory
in `flatcam-offset-benchmark`. Use `--output-dir` to choose another directory.
Each finished case is checkpointed separately, so an interruption doesn't discard
all earlier completed measurements.

The adjacent script preserves the measured algorithm and timing/validation
functions. The saved version replaces machine-specific paths with repository
discovery and an optional dependency-directory environment variable. It also
adds argument parsing and configurable output placement.

## Recommended next step

Prototype only the shared Standard shrinking kernel. Keep intermediate paths in
Clipper representation, remove redundant segments between passes, then convert
the finished contours once. Compare that prototype against the actual current
Paint/NCC path on production boards, including connection, geometry publication,
memory usage and required clearance checks.

The measurements justify that experiment. They don't justify an application-wide
Shapely replacement or a Rust port by themselves.

## Sources

- [Clipper2 overview](https://www.angusj.com/clipper2/Docs/Overview.htm)
- [ClipperOffset requirements and redundant-segment guidance](https://www.angusj.com/clipper2/Docs/Units/Clipper.Offset/Classes/ClipperOffset/_Body.htm)
- [pyclipr source and usage](https://github.com/drlukeparry/pyclipr)
- [pyclipr 0.1.8 release](https://pypi.org/project/pyclipr/0.1.8/)
- [Shapely buffer parameters](https://shapely.readthedocs.io/en/stable/reference/shapely.buffer.html)
