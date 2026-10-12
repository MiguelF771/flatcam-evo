#!/usr/bin/env python
# ##########################################################
# FlatCAM Evo: 2D Post-processing for Manufacturing          #
# Pure interpolation helpers for the autolevelling feature.  #
#                                                              #
# No Qt imports, no app imports - standard library only, so   #
# these functions can be unit tested without the GUI stack.   #
# ##########################################################

import bisect
import math
import re


def build_bilinear_grid(points, tol=1e-6):
    """
    Build a rectangular bilinear-interpolation grid out of a cloud of
    probed (x, y, height) points.

    X values within `tol` of each other are grouped into a single grid
    column, and the same is done for Y values into grid rows (probe
    coordinates are rounded by dec_format, so small floating point noise
    like 10.0 vs 10.0000001 must still land in the same column/row).

    :param points: list of (x, y, height) tuples, in any order.
    :param tol: tolerance used to group close X/Y values together.
    :return: a tuple (xs, ys, grid) where xs and ys are sorted ascending
        lists of the grouped X/Y values and grid[j][i] is the height at
        (xs[i], ys[j]); or None if the points do not form a full
        rectangular grid (missing cell, duplicate cell, fewer than 2
        distinct X or Y values, or a point that does not snap to any
        group).
    """
    if not points:
        return None

    xs = _group_values(sorted(set(p[0] for p in points)), tol)
    ys = _group_values(sorted(set(p[1] for p in points)), tol)

    if len(xs) < 2 or len(ys) < 2:
        return None

    grid = [[None] * len(xs) for _ in range(len(ys))]

    for x, y, z in points:
        i = _snap_index(xs, x, tol)
        j = _snap_index(ys, y, tol)
        if i is None or j is None:
            return None
        if grid[j][i] is not None:
            # duplicate cell
            return None
        grid[j][i] = z

    for row in grid:
        for cell in row:
            if cell is None:
                # missing cell
                return None

    return xs, ys, grid


def _group_values(sorted_unique_values, tol):
    """
    Group sorted unique values that lie within `tol` of each other,
    collapsing each group to its mean.

    :param sorted_unique_values: ascending list of unique values.
    :param tol: grouping tolerance.
    :return: ascending list of grouped (mean) values.
    """
    groups = []
    current = []
    for v in sorted_unique_values:
        if current and (v - current[0]) > tol:
            groups.append(sum(current) / len(current))
            current = []
        current.append(v)
    if current:
        groups.append(sum(current) / len(current))
    return groups


def _snap_index(grouped_values, value, tol):
    """
    Find the index of the grouped value that `value` belongs to.

    :param grouped_values: ascending list of grouped values.
    :param value: value to snap.
    :param tol: grouping tolerance.
    :return: index into grouped_values, or None if no group is within tol.
    """
    idx = bisect.bisect_left(grouped_values, value)
    candidates = []
    if 0 <= idx < len(grouped_values):
        candidates.append(idx)
    if 0 <= idx - 1 < len(grouped_values):
        candidates.append(idx - 1)

    best_idx = None
    best_dist = None
    for c in candidates:
        d = abs(grouped_values[c] - value)
        if d <= tol and (best_dist is None or d < best_dist):
            best_idx = c
            best_dist = d
    return best_idx


def bilinear_offset(grid_data, x, y):
    """
    Compute the bilinearly interpolated height offset at (x, y) using a
    grid built by build_bilinear_grid.

    x and y are clamped into the grid's range before interpolation.

    :param grid_data: tuple (xs, ys, grid) as returned by
        build_bilinear_grid.
    :param x: query X coordinate.
    :param y: query Y coordinate.
    :return: interpolated height offset (float).
    """
    xs, ys, grid = grid_data

    x = min(max(x, xs[0]), xs[-1])
    y = min(max(y, ys[0]), ys[-1])

    i = bisect.bisect_right(xs, x) - 1
    i = min(max(i, 0), len(xs) - 2)
    j = bisect.bisect_right(ys, y) - 1
    j = min(max(j, 0), len(ys) - 2)

    x0, x1 = xs[i], xs[i + 1]
    y0, y1 = ys[j], ys[j + 1]

    tx = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
    ty = 0.0 if y1 == y0 else (y - y0) / (y1 - y0)

    z00 = grid[j][i]
    z10 = grid[j][i + 1]
    z01 = grid[j + 1][i]
    z11 = grid[j + 1][i + 1]

    return (
        (1 - tx) * (1 - ty) * z00
        + tx * (1 - ty) * z10
        + (1 - tx) * ty * z01
        + tx * ty * z11
    )


def nearest_offset(points, x, y):
    """
    Return the height of the point in `points` nearest to (x, y).

    :param points: list of (x, y, height) tuples.
    :param x: query X coordinate.
    :param y: query Y coordinate.
    :return: height (float) of the nearest point. If two points are at
        equal distance, the first one in the list wins.
    :raises ValueError: if `points` is empty.
    """
    if not points:
        raise ValueError("nearest_offset() requires a non-empty list of points")

    best_z = None
    best_dist = None
    for px, py, pz in points:
        d = math.hypot(px - x, py - y)
        if best_dist is None or d < best_dist:
            best_dist = d
            best_z = pz
    return best_z


def parse_height_map_line(line):
    """
    Parse 1 row of a probe file written by a CNC controller.

    MACH3/MACH4 write comma-separated ``X,Y,Z[,A,B,C]`` rows; LinuxCNC's
    PROBEOPEN log writes space-separated ``X Y Z A B C U V W`` rows. Extra
    columns beyond X, Y, Z are ignored.

    :param line: 1 line of text (no trailing newline required).
    :return: (x, y, z) tuple of floats, or None if the line is blank or
        cannot be parsed.
    """
    stripped = line.strip()
    if not stripped:
        return None

    tokens = [t for t in re.split(r'[,\s]+', stripped) if t != '']
    if len(tokens) < 3:
        return None

    try:
        x = float(tokens[0])
        y = float(tokens[1])
        z = float(tokens[2])
    except ValueError:
        return None

    if not all(math.isfinite(value) for value in (x, y, z)):
        return None

    return x, y, z


_PRB_RE = re.compile(r'\[PRB:\s*([^\]:]+):\s*([01])\s*\]')


def parse_grbl_probe_output(text):
    """
    Parse GRBL 1.1 probe results out of the raw serial answer stream.

    Every ``G38.2`` probe move is answered by GRBL with a line such as
    ``[PRB:10.000,5.000,-1.234:1]``. Other lines (``ok``, status reports
    ``<Idle|...>``, ``[MSG:...]``, blank lines, ...) are ignored. A PRB
    line can carry more than 3 axis values; only the first 3 (X, Y, Z)
    are kept.

    :param text: raw text captured from the controller.
    :return: list of (x, y, z) tuples, in the order the probes occurred.
    :raises ValueError: if a probe line reports flag ``:0`` (the probe
        did not touch the surface).
    """
    results = []
    for line in text.splitlines():
        m = _PRB_RE.search(line)
        if not m:
            continue

        coords_str, flag = m.group(1), m.group(2)
        parts = [p.strip() for p in coords_str.split(',') if p.strip() != '']
        if len(parts) < 3:
            continue

        x = float(parts[0])
        y = float(parts[1])
        z = float(parts[2])

        if not all(math.isfinite(value) for value in (x, y, z)):
            raise ValueError("Probe result contains non-finite coordinates")

        if flag == '0':
            raise ValueError(
                "Probe did not touch the surface at X={0} Y={1}".format(x, y)
            )

        results.append((x, y, z))

    return results


_ACTIVE_WCS_RE = re.compile(r'(?<![A-Z0-9.])G(5[4-9])(?![A-Z0-9.])')
_OFFSET_RE = re.compile(r'\[(G5[4-9]|G92|TLO):\s*([^\]]+)\]')


def parse_grbl_active_wcs(text):
    """Parse the active G54-G59 work coordinate system from ``$G`` output."""
    match = _ACTIVE_WCS_RE.search(text)
    if not match:
        raise ValueError("Active G54-G59 work offset not found in $G output")
    return 'G' + match.group(1)


def parse_grbl_work_offset(text, active_wcs='G54'):
    """
    Parse the work-coordinate offset to SUBTRACT from a GRBL machine
    position, out of the raw ``$#`` command answer.

    GRBL reports probe/machine positions in MACHINE coordinates, but
    FlatCAM's probed height-map points are in WORK coordinates (the
    coordinate system the G-code itself is written in). The ``$#``
    command answer holds lines like::

        [G54:-100.000,-50.000,-20.000]
        [G55:0.000,0.000,0.000]
        [G92:0.000,0.000,0.000]
        [TLO:0.000]
        [PRB:0.000,0.000,0.000:0]

    The selected G54-G59 work coordinate system is read; the
    ``[PRB:...]`` line in this output is not a probe result and is
    ignored.

    :param text: raw text captured from the controller after ``$#``.
    :param active_wcs: selected work coordinate system, defaulting to G54.
    :return: (x, y, z) offset, computed as selected WCS + G92 per axis,
        with TLO added to Z. Missing G92 or TLO count as 0.
    :raises ValueError: if the selected WCS line is missing.
    """
    work_offset = None
    g92 = (0.0, 0.0, 0.0)
    tlo = 0.0

    for line in text.splitlines():
        m = _OFFSET_RE.search(line)
        if not m:
            continue

        key, val = m.group(1), m.group(2)
        parts = [p.strip() for p in val.split(',') if p.strip() != '']

        if key == active_wcs:
            work_offset = tuple(float(p) for p in parts[:3])
        elif key == 'G92':
            g92 = tuple(float(p) for p in parts[:3])
        elif key == 'TLO':
            tlo = float(parts[0])

    if work_offset is None:
        raise ValueError("%s work offset not found in $# output" % active_wcs)

    x = work_offset[0] + g92[0]
    y = work_offset[1] + g92[1]
    z = work_offset[2] + g92[2] + tlo
    return x, y, z


def match_nearest_point(points, x, y, tol):
    """
    Find the key of the point in `points` nearest to (x, y), within a
    tolerance.

    :param points: dict of {key: (px, py)}.
    :param x: query X coordinate.
    :param y: query Y coordinate.
    :param tol: maximum Euclidean distance to accept a match.
    :return: the key of the nearest point if its distance is <= tol,
        else None. Ties resolve to whichever key comes first in
        `points`'s iteration order.
    """
    best_key = None
    best_dist = None
    for key, (px, py) in points.items():
        d = math.hypot(px - x, py - y)
        if d <= tol and (best_dist is None or d < best_dist):
            best_dist = d
            best_key = key
    return best_key


def match_tolerance(points_xy, cap):
    """
    Compute a tolerance suitable for matching a probed point back to the
    nearest point in `points_xy` (see match_nearest_point()): half of the
    minimum Euclidean distance between any 2 distinct points, capped at
    `cap` so that widely spaced probe points don't produce an overly
    generous tolerance.

    With fewer than 2 points there is no pairwise distance to measure, so
    `cap` is returned directly.

    :param points_xy: iterable of (x, y) tuples.
    :param cap: maximum tolerance to return.
    :return: tolerance (float), always <= cap.
    """
    pts = list(points_xy)
    if len(pts) < 2:
        return cap

    min_dist = None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            if min_dist is None or d < min_dist:
                min_dist = d

    return min(min_dist / 2, cap)


_MOTION_G_CODES = (0.0, 1.0, 2.0, 3.0)
# Other members of the motion modal group: probing (G38.2/.3/.4/.5,
# G31 - MACH3/MACH4) and G80 (cancel motion). Selecting 1 of these puts
# the motion modal group in a state that is not G0/G1/G2/G3, so a
# following bare "X.. Y.." line must NOT be treated as a levelled G1
# move until a real G0/G1/G2/G3 is seen again.
_MOTION_NO_LEVEL_G_CODES = (31.0, 38.2, 38.3, 38.4, 38.5, 73.0, 76.0, 80.0,
                            81.0, 82.0, 83.0, 84.0, 85.0, 86.0, 87.0, 88.0, 89.0)
# Non-modal (one-shot) codes: their X/Y/Z words are not moves to work
# coordinates (machine-coordinate move, coordinate-system offset, ...),
# so they must not update the modal X/Y/Z state, and the line itself is
# never height-compensated.
_NON_MODAL_G_CODES = (10.0, 28.0, 30.0, 53.0, 92.0, 92.1, 92.2, 92.3)
# Modal settings that can stand on the same line as a move (plane, units,
# cutter compensation off, tool length off, work coordinate system, path
# mode, distance mode, feed mode). They do not make the line a non-motion
# command: "G90 X1 Y1" under a modal G1 is still a G1 move.
_MODAL_SETTING_G_CODES = (17.0, 18.0, 19.0, 20.0, 21.0, 40.0, 49.0, 54.0, 55.0, 56.0, 57.0,
                          58.0, 59.0, 61.0, 64.0, 90.0, 91.0, 93.0, 94.0)
_WORD_RE = re.compile(r'([A-Z])\s*([+\-]?(?:\d+\.?\d*|\.\d+))')


def new_levelling_state():
    """
    Create a fresh modal G-code state for use with level_gcode_line.

    :return: dict with modal X/Y/Z position, modal motion G code, and an
        `arcs` counter (arc moves are never height-compensated, so the
        caller can warn/report on them).
    """
    # abs: True for G90 (absolute), False for G91 (relative)
    # relative: count of G1 moves that were not compensated because of G91
    return {'X': 0.0, 'Y': 0.0, 'Z': 0.0, 'G': 0, 'arcs': 0, 'abs': True, 'relative': 0}


def _mask_comments(line):
    """
    Replace comment text with spaces of the same length, so word
    positions in the masked string still line up with the original
    line (needed to locate/replace the Z word's number in-place).

    :param line: raw G-code line.
    :return: line with `(...)` and trailing `;...` comments blanked out.
    """
    masked = re.sub(r'\([^)]*\)', lambda m: ' ' * len(m.group(0)), line)
    idx = masked.find(';')
    if idx != -1:
        masked = masked[:idx] + ' ' * (len(masked) - idx)
    return masked


def _insert_z_word(line, new_z_str, z_match):
    """
    Write `new_z_str` into `line`: replace the existing Z word's number
    if there is one, else append a new " Z<value>" word before any
    inline comment, or at the end of the line if there is none.

    :param line: original line text (with its original comment, if
        any, untouched).
    :param new_z_str: formatted new Z value.
    :param z_match: the `_WORD_RE` match object for the line's Z word
        (matched against the comment-masked line, so its span lines up
        with `line`), or None if the line has no Z word.
    :return: the line with the Z value inserted/replaced.
    """
    if z_match is not None:
        start, end = z_match.span(2)
        return line[:start] + new_z_str + line[end:]

    paren_idx = line.find('(')
    semi_idx = line.find(';')
    candidates = [i for i in (paren_idx, semi_idx) if i != -1]
    comment_idx = min(candidates) if candidates else None

    if comment_idx is not None:
        prefix = line[:comment_idx].rstrip()
        suffix = line[comment_idx:]
        return prefix + " Z" + new_z_str + " " + suffix

    return line.rstrip() + " Z" + new_z_str


def level_gcode_line(line, state, offset_fn, decimals=4):
    """
    Apply height-map compensation to 1 line of G-code, modally tracking
    X/Y/Z/G state across calls.

    Every linear move (modal G1), at any Z, gets its Z
    height-compensated. Left untouched: rapids (G0); arcs (G2/G3,
    counted in `state['arcs']`); other members of the motion modal
    group that are never cutting moves (G31, G38.2/.3/.4/.5, G80) -
    selecting 1 of these sets `state['G']` to None so a following bare
    "X.. Y.." line is not mistaken for a continuing G1 move until a
    real G0/G1/G2/G3 reappears; non-modal one-shot codes (G10, G28,
    G30, G53, G92) whose X/Y/Z words are not work-coordinate moves, so
    they also leave `state['X']`/`['Y']`/`['Z']`/`['G']` untouched;
    non-motion modal codes (G4, G17, G20, G21, G90, G94, ...); and
    comment-only/blank lines.

    Words are matched with upper-case letters only (`[A-Z]`), matching
    what FlatCAM's preprocessors emit; a line using lower-case letters
    (e.g. `g01 x10 y5 z-0.1`) has no recognized words and passes
    through unchanged.

    :param line: 1 line of G-code text, no trailing newline.
    :param state: modal state dict, as created by new_levelling_state();
        mutated in place.
    :param offset_fn: callable (x, y) -> height offset (float) to add
        to Z.
    :param decimals: number of decimals used to format the new Z value.
    :return: the (possibly modified) line, without a trailing newline.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith('(') or stripped.startswith(';'):
        return line

    masked = _mask_comments(line)

    x_val = y_val = z_val = None
    z_match = None
    g_words = []

    for m in _WORD_RE.finditer(masked):
        letter, num = m.group(1), m.group(2)
        if letter == 'X':
            x_val = float(num)
        elif letter == 'Y':
            y_val = float(num)
        elif letter == 'Z':
            z_val = float(num)
            z_match = m
        elif letter == 'G':
            g_words.append(float(num))

    has_xyz = x_val is not None or y_val is not None or z_val is not None

    # G90/G91 change the distance mode also on a line with a non-modal
    # code (for example "G91 G28 Z0"), so handle them first
    if 91.0 in g_words:
        state['abs'] = False
    if 90.0 in g_words:
        state['abs'] = True

    non_modal_gs = [g for g in g_words if g in _NON_MODAL_G_CODES]
    if non_modal_gs:
        # One-shot code: X/Y/Z here are not work-coordinate moves.
        # Leave all modal state untouched.
        return line

    motion_gs = [g for g in g_words if g in _MOTION_G_CODES]
    no_level_gs = [g for g in g_words if g in _MOTION_NO_LEVEL_G_CODES]
    other_gs = [
        g for g in g_words
        if g not in _MOTION_G_CODES and g not in _MOTION_NO_LEVEL_G_CODES
        and g not in _MODAL_SETTING_G_CODES
    ]

    is_non_motion_cmd = False
    if motion_gs:
        state['G'] = int(motion_gs[-1])
    elif no_level_gs:
        state['G'] = None
        is_non_motion_cmd = True
    elif other_gs:
        is_non_motion_cmd = True
    # else: no G word at all - keep the previous modal G unchanged.

    absolute = state.get('abs', True)
    if x_val is not None:
        state['X'] = x_val if absolute else state['X'] + x_val
    if y_val is not None:
        state['Y'] = y_val if absolute else state['Y'] + y_val
    if z_val is not None:
        state['Z'] = z_val if absolute else state['Z'] + z_val

    if not has_xyz:
        return line

    if is_non_motion_cmd:
        return line

    if not absolute:
        # a G91 Z word is a delta, so an absolute compensated Z can not
        # be written; count the move so that the caller can warn
        if state['G'] == 1:
            state['relative'] = state.get('relative', 0) + 1
        return line

    if state['G'] in (2, 3):
        state['arcs'] += 1
        return line

    if state['G'] != 1:
        return line

    new_z = state['Z'] + offset_fn(state['X'], state['Y'])
    new_z_str = "{0:.{1}f}".format(new_z, decimals)

    return _insert_z_word(line, new_z_str, z_match)
