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
                          58.0, 59.0, 59.1, 59.2, 59.3, 61.0, 64.0, 90.0, 90.1, 91.0, 91.1,
                          93.0, 94.0, 41.0, 41.1, 42.0, 42.1)
# The units words (G20 inch, G21 mm) and the work coordinate system words
# (G54..G59.3). The height map is valid only in the frame of the probing
# (the units and the coordinate system), see _note_frame_words().
_UNITS_G_CODES = (20.0, 21.0)
_WCS_G_CODES = (54.0, 55.0, 56.0, 57.0, 58.0, 59.0, 59.1, 59.2, 59.3)
# Frame value that no word can ever equal: the frame of the probing is not known
# by name and an optional line made the current frame unknown.
_FRAME_UNKNOWN = 'unknown'
_WORD_RE = re.compile(r'([A-Z])\s*([+\-]?(?:\d+\.?\d*|\.\d+))')
# Start of a line that must stay in front of any G word: whitespace, a block
# delete "/", leading comments and a line number "N<digits>" (RS274NGC wants N first).
_LINE_LEAD_RE = re.compile(r'\s*(?:/\s*)?(?:\([^)]*\)\s*)*(?:N\s*\d+\s*)?')
# A block delete line: "/" is the first character that is not a space (a "/" in
# a comment is not one).
_BLOCK_DELETE_RE = re.compile(r'\s*/')
# Letters an arc line may carry and still be split into line moves.
_ARC_SPLIT_LETTERS = frozenset('GXYZIJKRFP')
# Largest angle of 1 arc segment, and the largest number of segments of 1 arc.
_ARC_MAX_ANGLE = math.pi / 18.0
_ARC_MAX_SEGMENTS = 10000
# A sweep below this angle (radians) is a full circle (GRBL ARC_ANGULAR_TRAVEL_EPSILON).
_ARC_FULL_CIRCLE_EPS = 5e-7
# Modal keys that an optional (block delete) line can change; after such a line
# each changed key is unknown (None), because the controller may skip the line.
_BLOCK_DELETE_MODAL_KEYS = ('abs', 'plane', 'arc_abs', 'inverse_feed', 'G', 'units', 'wcs',
                            'cutter_comp')
# Cutter radius compensation on (G41/G42, dynamic G41.1/G42.1): the tool moves
# off the programmed X/Y by its radius, so the map is read at the wrong point.
_CUTTER_COMP_G_CODES = (41.0, 41.1, 42.0, 42.1)
# A subprogram call or flow control: a LinuxCNC O-word with a keyword ("O100 call",
# "O<name> sub", "O101 while", ...) or Mach3/Fanuc "M98". The lines of the file do not
# then run once in their order. A plain program number ("O1234") is not one.
_SUBPROGRAM_CALL_RE = re.compile(
    r'(?:\bO\s*(?:\d+|<[^>]*>)\s*(?:CALL|SUB|ENDSUB|RETURN|WHILE|ENDWHILE|DO|IF|ELSEIF|ELSE|ENDIF'
    r'|REPEAT|ENDREPEAT|BREAK|CONTINUE)\b|(?<![A-Z])M\s*0*98(?![\d.]))')


def new_levelling_state(arc_segment=1.0, units=None):
    """
    Create a fresh modal G-code state for use with level_gcode_line.

    The position is not known at the start: X, Y and Z are None until
    absolute words set them, so a cutting move is compensated only when
    X, Y and Z are all set (before the line or on it).

    The height map is valid only in the frame of the probing: the units and
    the work coordinate system (WCS). `home_units`/`home_wcs` are the frame
    of the map (None: not known by name), `units`/`wcs` the frame now in
    effect. A cutting move in another frame is not compensated.

    :param arc_segment: largest chord length of 1 segment of a split arc
        (G-code units), must be > 0.
    :param units: the units of the job and of the height map: 'MM', 'IN'
        (any case) or None if they are not known. The program is taken to run
        in these units until a G20/G21 word says otherwise.
    :return: dict with modal X/Y/Z position, modal motion G code, and the
        counters `arcs` (arc moves that were not compensated),
        `relative` (G91 G1 moves that were not compensated),
        `arcs_split` (arcs that were split into G1 segments),
        `unparsed` (lines with words that are not understood),
        `unknown` (cutting moves that were not compensated because the
        position, the distance mode or the motion mode was unknown) and
        `frame` (G1 moves that were not compensated because they are in
        another WCS or in other units), so the caller can warn/report on them.
    :raises ValueError: if `arc_segment` is not > 0.
    """
    if not arc_segment > 0:
        raise ValueError("arc_segment must be > 0")
    home_units = {'MM': 21, 'IN': 20}.get(str(units).upper()) if units is not None else None
    # abs: True for G90 (absolute), False for G91 (relative)
    # plane: 17/18/19 (G17/G18/G19)
    # arc_abs: True for G90.1 (absolute I/J), False for G91.1 (incremental I/J)
    # inverse_feed: True for G93
    # each of abs/plane/arc_abs/inverse_feed/G is None when it is unknown (after
    #   an optional block delete line that changes it)
    # applied: the Z offset now in effect, (machine Z that the emitted code
    #   reaches) minus (programmed Z, state['Z'])
    # known: the axes whose work position was set by an absolute G0..G3 word
    # g_unknown: True when G is None because the motion mode is unknown (after an
    #   optional or a not understood line), not because of a probe/canned cycle code
    # units: 21 (G21, mm) or 20 (G20, inch); wcs: the G number of the WCS word (54..59.3)
    # home_units/home_wcs: the units and the WCS of the height map; None when not known
    #   by name (then the first word before any move declares it, see _note_frame_words)
    # units/wcs: now in effect; None when unknown (after an optional line that changes them)
    # moved: True after the first line that can move the machine; after it no word
    #   can declare the frame of the map
    # shifted: True after a line that moves the X/Y work origin (G92, G10 L2/L20, ...),
    #   a G word that is not read or a subprogram call; the rest of the program is out
    #   of the frame of the map
    # cutter_comp: True under G41/G42 (None when unknown); cutter: G1 moves not
    #   compensated because of it
    return {'X': None, 'Y': None, 'Z': None, 'G': 0, 'arcs': 0, 'abs': True, 'relative': 0,
            'plane': 17, 'arc_abs': False, 'inverse_feed': False, 'applied': 0.0,
            'known': set(), 'arc_segment': float(arc_segment), 'arcs_split': 0, 'unparsed': 0,
            'unknown': 0, 'g_unknown': False, 'frame': 0,
            'home_units': home_units, 'units': home_units, 'home_wcs': None, 'wcs': None,
            'moved': False, 'shifted': False, 'cutter_comp': False, 'cutter': 0}


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


def _comment_text(line):
    """
    Collect the comments of a line: every `(...)` comment before a `;`
    and the `;...` tail, joined with a space.

    :param line: raw G-code line.
    :return: the comment text, or '' if the line has no comment.
    """
    no_parens = re.sub(r'\([^)]*\)', lambda m: ' ' * len(m.group(0)), line)
    idx = no_parens.find(';')
    head = line if idx == -1 else line[:idx]
    parts = re.findall(r'\([^)]*\)', head)
    if idx != -1:
        parts.append(line[idx:].rstrip())
    return ' '.join(parts)


def _fmt(value, decimals):
    """
    Format a number with `decimals` decimals, never as a negative zero.

    :param value: number to format.
    :param decimals: number of decimals.
    :return: the formatted text.
    """
    text = "{0:.{1}f}".format(value, decimals)
    if float(text) == 0.0:
        text = text.lstrip('-')
    return text


def _insert_z_word(line, new_z_str, z_match):
    """
    Write `new_z_str` into `line`: replace the existing Z word's number
    if there is one, else append a new " Z<value>" word after the last word
    of the line (before a trailing comment, after a leading comment).

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

    # after the last word, before a trailing comment; a leading comment stays in front
    end = len(_mask_comments(line).rstrip())
    suffix = line[end:]
    if suffix.strip():
        return line[:end] + " Z" + new_z_str + " " + suffix.lstrip()
    return line[:end] + " Z" + new_z_str


def _note_modal_settings(state, g_words):
    """
    Update the distance mode, the plane, the arc distance mode and the
    feed mode of `state` from the G words of a line.

    :param state: modal state dict; mutated in place.
    :param g_words: the G numbers of the line, in order.
    """
    # G90/G91 change the distance mode also on a line with a non-modal
    # code (for example "G91 G28 Z0"), so this runs before anything else
    if 91.0 in g_words:
        state['abs'] = False
    if 90.0 in g_words:
        state['abs'] = True
    for g in g_words:
        if g in (17.0, 18.0, 19.0):
            state['plane'] = int(g)
        elif g == 90.1:
            state['arc_abs'] = True
        elif g == 91.1:
            state['arc_abs'] = False
        elif g == 93.0:
            state['inverse_feed'] = True
        elif g == 94.0:
            state['inverse_feed'] = False


def _lose_axes(state, axes):
    """
    Mark work axes as unknown (None) and no longer known.

    :param state: modal state dict; mutated in place.
    :param axes: iterable of 'X'/'Y'/'Z'.
    """
    for axis in axes:
        state[axis] = None
        state['known'].discard(axis)


def _all_known(state):
    """
    :return: True if X, Y and Z were all set by an absolute word and are not None.
    """
    return all(axis in state['known'] and state[axis] is not None for axis in ('X', 'Y', 'Z'))


def _note_frame_word(state, key, value):
    """
    Apply 1 units word or WCS word to the frame of `state`.

    A first word before any move, when the frame of the map is not known by
    name, declares the frame of the map. A word after a move never declares
    it, also when the position is lost (G28, G53, ...): the moves before the
    word were in the frame that is active at the start. A word that changes
    the frame makes all work positions unknown.

    :param state: modal state dict; mutated in place.
    :param key: 'units' or 'wcs'.
    :param value: the new units (20/21) or WCS (G number) in effect.
    """
    home_key = 'home_' + key
    if state[home_key] is None and state[key] is None and not state['moved']:
        state[home_key] = value
    if value != state[key]:
        _lose_axes(state, ['X', 'Y', 'Z'])
    state[key] = value


def _note_frame_words(state, g_words):
    """
    Update the units and the work coordinate system of `state` from the
    G words of a line.

    :param state: modal state dict; mutated in place.
    :param g_words: the G numbers of the line, in order.
    """
    for g in g_words:
        if g in _UNITS_G_CODES:
            _note_frame_word(state, 'units', int(g))
        elif g in _WCS_G_CODES:
            _note_frame_word(state, 'wcs', g)


def _in_frame(state):
    """
    :return: True if the units and the WCS in effect are those of the height map
        and no line moved the X/Y work origin.
    """
    return (state['units'] == state['home_units'] and state['wcs'] == state['home_wcs']
            and not state['shifted'])


def _arc_centre(start, end, words, clockwise, arc_abs, decimals):
    """
    Centre of an arc in the XY plane.

    :param start: (x, y, z) start point.
    :param end: (x, y, z) end point.
    :param words: dict letter -> value of the arc line.
    :param clockwise: True for G2.
    :param arc_abs: True if I/J are absolute coordinates (G90.1).
    :param decimals: number of decimals of the output.
    :return: (cx, cy), or None if the line gives no valid centre (no I/J
        and no R, both of them, an R arc with equal ends, or an R too
        small for the chord).
    """
    has_ij = 'I' in words or 'J' in words
    if has_ij and 'R' in words:
        return None
    if has_ij:
        i_val, j_val = words.get('I', 0.0), words.get('J', 0.0)
        if arc_abs:
            return i_val, j_val
        return start[0] + i_val, start[1] + j_val
    if 'R' not in words:
        return None

    radius = words['R']
    dx, dy = end[0] - start[0], end[1] - start[1]
    chord = math.hypot(dx, dy)
    if chord <= 10 ** -decimals or radius == 0.0:
        return None
    if chord > 2.0 * abs(radius) + max(0.001 * abs(radius), 5 * 10 ** -decimals):
        return None
    height = math.sqrt(max(0.0, radius * radius - chord * chord / 4.0))
    # the centre of the short arc is to the left of the chord for G3 and to
    # the right for G2; a negative R selects the long arc (the other side)
    side = -1.0 if clockwise else 1.0
    if radius < 0.0:
        side = -side
    return (start[0] + dx / 2.0 - side * height * dy / chord,
            start[1] + dy / 2.0 + side * height * dx / chord)


def _arc_sweep(start, end, centre, clockwise, decimals):
    """
    Radius and signed sweep angle of an arc.

    :param start: (x, y, z) start point.
    :param end: (x, y, z) end point.
    :param centre: (cx, cy).
    :param clockwise: True for G2.
    :param decimals: number of decimals of the output.
    :return: (radius, sweep) with sweep < 0 for G2, or None if the start
        radius is about 0 or differs from the end radius by more than
        max(0.1 %, 5 * 10**-decimals). An arc whose sweep is below
        _ARC_FULL_CIRCLE_EPS (equal start and end angles) is a full circle,
        as in GRBL; the output resolution never decides this, because ends
        1 output unit apart are a short arc.
    """
    r_start = math.hypot(start[0] - centre[0], start[1] - centre[1])
    r_end = math.hypot(end[0] - centre[0], end[1] - centre[1])
    if r_start < 10 ** -decimals:
        return None
    if abs(r_start - r_end) > max(0.001 * r_start, 5 * 10 ** -decimals):
        return None
    a_start = math.atan2(start[1] - centre[1], start[0] - centre[0])
    a_end = math.atan2(end[1] - centre[1], end[0] - centre[0])
    full = 2.0 * math.pi
    if clockwise:
        sweep = (a_start - a_end) % full
    else:
        sweep = (a_end - a_start) % full
    if sweep < _ARC_FULL_CIRCLE_EPS:
        sweep = full
    return r_start, -sweep if clockwise else sweep


def _arc_points(start, end, centre, radius, sweep, arc_segment):
    """
    The points of an arc cut into segments of at most `arc_segment` length
    and at most 10 degrees (and at most 10000 segments).

    :param start: (x, y, z) start point.
    :param end: (x, y, z) end point; Z is interpolated linearly (helix).
    :param centre: (cx, cy).
    :param radius: radius of the circle through the start point.
    :param sweep: signed sweep angle, negative for clockwise.
    :param arc_segment: largest chord length of 1 segment.
    :return: list of (x, y, z), the last one is exactly `end`.
    """
    count = max(1,
                math.ceil(radius * abs(sweep) / arc_segment - 1e-9),
                math.ceil(abs(sweep) / _ARC_MAX_ANGLE - 1e-9))
    count = min(count, _ARC_MAX_SEGMENTS)
    a_start = math.atan2(start[1] - centre[1], start[0] - centre[0])
    points = []
    for i in range(1, count):
        angle = a_start + sweep * i / count
        points.append((centre[0] + radius * math.cos(angle),
                       centre[1] + radius * math.sin(angle),
                       start[2] + (end[2] - start[2]) * i / count))
    points.append((end[0], end[1], end[2]))
    return points


def _arc_lines(points, start, z_actual, absolute, offset_fn, decimals):
    """
    Format the points of an arc as G1 lines with height compensation.

    In G91 the deltas are taken between the ROUNDED positions, so the
    rounding errors do not accumulate; the Z delta of each segment brings
    the machine to the compensated height of its point.

    :param points: list of (x, y, z) from _arc_points().
    :param start: (x, y, z) start point (programmed).
    :param z_actual: machine Z at the start.
    :param absolute: True for G90.
    :param offset_fn: callable (x, y) -> height offset.
    :param decimals: number of decimals of the output.
    :return: (list of "G1 X.. Y.. Z.." texts, machine Z after the last one).
    """
    lines = []
    prev_x, prev_y = float(_fmt(start[0], decimals)), float(_fmt(start[1], decimals))
    for x, y, z in points:
        desired = z + offset_fn(x, y)
        if absolute:
            x_str, y_str, z_str = _fmt(x, decimals), _fmt(y, decimals), _fmt(desired, decimals)
            z_actual = float(z_str)
        else:
            cur_x, cur_y = float(_fmt(x, decimals)), float(_fmt(y, decimals))
            x_str, y_str = _fmt(cur_x - prev_x, decimals), _fmt(cur_y - prev_y, decimals)
            z_step = float(_fmt(desired - z_actual, decimals))
            z_str = _fmt(z_step, decimals)
            z_actual += z_step
            prev_x, prev_y = cur_x, cur_y
        lines.append("G1 X%s Y%s Z%s" % (x_str, y_str, z_str))
    return lines, z_actual


def _arc_splittable(info, state):
    """
    Check the conditions of the line and of the state that allow splitting
    an arc: the frame of the height map (units and WCS), G17 plane, no inverse
    feed, only the words G X Y Z I J K R F P (P only as P1) and G words that
    are 2/3 or modal settings.

    :param info: parsed words of the line, as made by level_gcode_line.
    :param state: modal state dict.
    :return: True if the arc may be split.
    """
    if not _in_frame(state) or state['cutter_comp'] is not False:
        return False
    # an unknown (None) arc distance mode or feed mode blocks the split too
    if (state['plane'] != 17 or state['inverse_feed'] is not False or state['arc_abs'] is None
            or state['abs'] is None):
        return False
    if not info['letters'] <= _ARC_SPLIT_LETTERS:
        return False
    if 'P' in info['words'] and info['words']['P'] != 1.0:
        return False
    return all(g in (2.0, 3.0) or g in _MODAL_SETTING_G_CODES for g in info['g_words'])


def _level_arc(line, info, state, offset_fn, decimals, start, was_known, absolute):
    """
    Split a G2/G3 arc line into height-compensated G1 lines, or leave it
    unchanged (counted in `state['arcs']`) if that is not possible.

    The X/Y/Z of `state` already hold the end point. The program keeps its
    modal G2/G3, but the machine is left in G1 by a split arc, so an
    unchanged arc line without a G2/G3 word gets the modal word in front.

    :param line: original line.
    :param info: parsed words of the line, as made by level_gcode_line.
    :param state: modal state dict; mutated in place.
    :param offset_fn: callable (x, y) -> height offset.
    :param decimals: number of decimals of the output.
    :param start: (x, y, z) before the line.
    :param was_known: True if the start point was known before the line.
    :param absolute: True for G90.
    :return: the new text (lines joined with a newline) or the original line.
    """
    points = None
    # in G91 the Z deltas need the Z offset in effect
    if was_known and _arc_splittable(info, state) and (absolute or state['applied'] is not None):
        end = (state['X'], state['Y'], state['Z'])
        clockwise = state['G'] == 2
        centre = _arc_centre(start, end, info['words'], clockwise, state['arc_abs'], decimals)
        geometry = None if centre is None else _arc_sweep(start, end, centre, clockwise, decimals)
        if geometry is not None:
            points = _arc_points(start, end, centre, geometry[0], geometry[1], state['arc_segment'])

    if points is None:
        state['arcs'] += 1
        if absolute and 'Z' in info['words']:
            state['applied'] = 0.0
        if 2.0 not in info['g_words'] and 3.0 not in info['g_words']:
            # the G word goes after a block delete and a line number
            lead = _LINE_LEAD_RE.match(line).end()
            return '%sG%d %s' % (line[:lead], state['G'], line[lead:])
        return line

    # G90 output does not use the start Z of the machine, so an unknown offset counts as 0
    lines, z_actual = _arc_lines(points, start, start[2] + (state['applied'] or 0.0), absolute,
                                 offset_fn, decimals)
    state['applied'] = z_actual - state['Z']
    state['arcs_split'] += 1

    head = [text for g, text in zip(info['g_words'], info['g_texts']) if g not in (2.0, 3.0)]
    head.append(lines[0])
    if info['f_text']:
        head.append(info['f_text'])
    lines[0] = ' '.join(head)
    comment = _comment_text(line)
    if comment:
        lines[0] += ' ' + comment
    if _BLOCK_DELETE_RE.match(line):
        # a block deleted arc: all of its segments are block deleted
        lines = ['/' + text for text in lines]
    return '\n'.join(lines)


def _level_relative(line, z_match, state, offset_fn, decimals, prev_z, was_known):
    """
    Height-compensate a G91 G1 move: the Z word becomes the delta that
    brings the machine to the programmed Z plus the offset of the new X/Y.

    :param line: original line.
    :param z_match: `_WORD_RE` match of the line's Z word, or None.
    :param state: modal state dict, already holding the new X/Y/Z; mutated.
    :param offset_fn: callable (x, y) -> height offset.
    :param decimals: number of decimals of the output.
    :param prev_z: programmed Z before the line.
    :param was_known: True if X, Y and Z were all known before the line.
    :return: the new line, or the original line if the position or the Z
        offset in effect was unknown (counted in `state['relative']`) or
        nothing has to change.
    """
    if not was_known or state['applied'] is None:
        state['relative'] += 1
        return line
    prev_actual = prev_z + state['applied']
    desired = state['Z'] + offset_fn(state['X'], state['Y'])
    z_step = round(desired - prev_actual, decimals)
    if z_step == 0:
        z_step = 0.0
    state['applied'] = prev_actual + z_step - state['Z']
    if z_match is None and z_step == 0:
        return line
    return _insert_z_word(line, _fmt(z_step, decimals), z_match)


def _unparsed_text(masked):
    """
    The text of a comment-masked line that is not a recognized word: an
    expression (`X[1+2]`), a parameter (`X#1`, `#1=5`), a lower-case word,
    an O-word keyword, ... A leading block delete "/" is not part of it.

    :param masked: line with its comments blanked by _mask_comments().
    :return: the text left after removing all words, '' if every part of
        the line is understood.
    """
    rest = _WORD_RE.sub('', masked).strip()
    if rest.startswith('/'):
        rest = rest[1:].strip()
    return rest


def level_gcode_line(line, state, offset_fn, decimals=4):
    """
    Apply height-map compensation to 1 line of G-code, modally tracking
    X/Y/Z/G state across calls. The rules for 1 line are in _level_line().

    An optional line (block delete "/") is levelled as if it runs, so its
    output is right when the controller runs it. But the controller can
    skip it, so after it every modal setting that it changed (distance
    mode, plane, arc distance mode, feed mode, motion mode, units, work
    coordinate system) is unknown (None; with units or WCS unknown the
    moves are out of the frame of the height map and are not compensated
    until a units or WCS word sets them again), each axis whose programmed value or known status it changes
    is unknown, it never clears an unknown motion mode, and if it changes
    the Z, its text or the applied offset the Z offset in effect
    (`state['applied']`) is unknown (None). Start-dependent moves (arcs,
    G91 moves) are then not compensated until absolute words set the
    position again; an absolute compensated G1 or an absolute Z word sets
    the offset again.

    :param line: 1 line of G-code text, no trailing newline.
    :param state: modal state dict, as created by new_levelling_state();
        mutated in place.
    :param offset_fn: callable (x, y) -> height offset (float) to add
        to Z.
    :param decimals: number of decimals used to format the new Z value.
    :return: the (possibly modified) line, without a trailing newline; a
        split arc is several lines joined with a newline.
    """
    if not _BLOCK_DELETE_RE.match(line):
        return _level_line(line, state, offset_fn, decimals)

    modes_before = {key: state[key] for key in _BLOCK_DELETE_MODAL_KEYS}
    axes_before = {axis: state[axis] for axis in ('X', 'Y', 'Z')}
    known_before = set(state['known'])
    applied_before = state['applied']
    g_unknown_before = state['g_unknown']
    out = _level_line(line, state, offset_fn, decimals)
    for key, value in modes_before.items():
        if state[key] != value:
            state[key] = None
            if key == 'G':
                state['g_unknown'] = True
            elif key in ('units', 'wcs') and state['home_' + key] is None:
                # an unknown frame must not look like the frame of a map that is not known by name
                state['home_' + key] = _FRAME_UNKNOWN
    # a skipped line can not clear an unknown motion mode
    state['g_unknown'] = state['g_unknown'] or g_unknown_before
    # an axis is uncertain if the line changes its programmed value, or makes it
    # known with the same value (only the run line would set it)
    _lose_axes(state, [axis for axis, value in axes_before.items()
                       if state[axis] != value or (axis in state['known']) != (axis in known_before)])
    if out != line or state['Z'] != axes_before['Z'] or state['applied'] != applied_before:
        # the machine Z of a run line and of a skipped line can differ
        state['applied'] = None
    return out


def _level_line(line, state, offset_fn, decimals):
    """
    Apply height-map compensation to 1 line of G-code, modally tracking
    X/Y/Z/G state across calls.

    Every linear move (modal G1), at any Z with known work X/Y/Z, gets
    its Z height-compensated, if it is in the frame of the height map: the
    units and the work coordinate system (WCS) of the probing. The frame
    follows the G20/G21 and G54..G59.3 words (see _note_frame_words()); each
    word that changes it makes all 3 axes unknown. A line that moves the X/Y
    work origin (G92 or G10 L20 with an X/Y word, G10 L2 with an X/Y/R word,
    G92.1/G92.2/G92.3) puts the rest of the program out of the frame
    (`state['shifted']`), also with unresolved expressions. G10 only
    invalidates the map if its P target is the map WCS or is unknown;
    a Z-only shift (a tool length re-zero) does not. G52 with an X/Y word,
    G51 and G68 shift the frame too, and so do a not understood line with a
    G word that is not read (lower case, expression: it can be any G code)
    and a subprogram call or flow control (O-word keyword, M98, G65). Under cutter radius
    compensation (G41/G42 until G40) a G1 move is not changed and is counted
    in `state['cutter']`, and an arc is not split. A G1 move with X/Y/Z
    words in another frame (absolute or relative) is not changed and is
    counted in `state['frame']`; an absolute one with a Z word sets the Z
    offset in effect (`state['applied']`) to 0, the machine goes to the
    programmed Z. An arc in another frame is not split (counted in
    `state['arcs']`). The axes are tracked as usual. X/Y/Z are None at the
    start, so a G1 move needs all 3 axes set (before the line or on it);
    else it is not changed and is counted in `state['unknown']`. In G91 a G1 move is compensated when X/Y/Z
    were all set by absolute words before: its Z word becomes the delta
    to the compensated height (`state['applied']` is the offset in effect).
    An arc (modal G2/G3) in the G17 plane with a known start point is split
    into G1 segments (returned as 1 string, lines joined with a newline);
    other arcs are left untouched and counted in `state['arcs']`. Left
    untouched: rapids (G0); other members of the motion modal
    group that are never cutting moves (G31, G38.2/.3/.4/.5, G80) -
    selecting 1 of these sets `state['G']` to None so a following bare
    "X.. Y.." line is not mistaken for a continuing G1 move until a
    real G0/G1/G2/G3 reappears; non-modal one-shot codes (G10, G28,
    G30, G92) whose X/Y/Z words are not work-coordinate moves, so
    they also leave `state['G']` untouched. They change `state['X']`/
    `['Y']`/`['Z']` only by marking axes unknown (None, and no longer in
    `state['known']`): G28 and G30 the axes they move (all 3 if none is
    named); G92 and G10 L20 the axes named on the line; G92.1, G92.2,
    G92.3 and G10 L2 all 3 axes (they change the work coordinate system,
    so the tracked work positions are no longer valid). Other G10 forms
    (L1, L10, ...) change nothing. The line itself is never changed;
    G53 machine-coordinate moves, which track explicit G0/G1 but mark
    moved work axes unknown (None) until restored by absolute work words;
    non-motion modal codes (G4, G17, G20, G21, G90, G94, ...); and
    comment-only/blank lines.

    Words are matched with upper-case letters and plain numbers only
    (`[A-Z]` and `_WORD_RE`), matching what FlatCAM's preprocessors emit.
    A line with any other text (an expression `X[1]`, a parameter `X#1`,
    lower-case words such as `g01 x10`, ...) is not understood: it passes
    through unchanged, it is counted in `state['unparsed']`, all 3 axes
    and the motion mode become unknown, and the modal settings of its
    recognized G words still apply. A `%` line is understood. If the
    distance mode is unknown (None), the axes that a line names become
    unknown and the line is not changed.

    :param line: 1 line of G-code text, no trailing newline.
    :param state: modal state dict, as created by new_levelling_state();
        mutated in place.
    :param offset_fn: callable (x, y) -> height offset (float) to add
        to Z.
    :param decimals: number of decimals used to format the new Z value.
    :return: the (possibly modified) line, without a trailing newline; a
        split arc is several lines joined with a newline.
    """
    stripped = line.strip()
    if not stripped or stripped == '%':
        return line

    masked = _mask_comments(line)
    if not masked.strip():
        # a comment line; a leading comment can not hide commands ("(c) G41 D1")
        return line

    x_val = y_val = z_val = None
    z_match = None
    g_words = []
    info = {'words': {}, 'letters': set(), 'g_words': g_words, 'g_texts': [], 'f_text': None}

    for m in _WORD_RE.finditer(masked):
        letter, num = m.group(1), m.group(2)
        info['letters'].add(letter)
        info['words'][letter] = float(num)
        if letter == 'X':
            x_val = float(num)
        elif letter == 'Y':
            y_val = float(num)
        elif letter == 'Z':
            z_val = float(num)
            z_match = m
        elif letter == 'G':
            g_words.append(float(num))
            info['g_texts'].append('G' + num)
        elif letter == 'F':
            info['f_text'] = 'F' + num

    has_xyz = x_val is not None or y_val is not None or z_val is not None

    _note_modal_settings(state, g_words)
    # also on a line that is not understood: its G words still apply
    _note_frame_words(state, g_words)
    unparsed = _unparsed_text(masked)
    non_modal_gs = [g for g in g_words if g in _NON_MODAL_G_CODES]
    setup_gs = (4.0, 10.0, 92.0, 92.1, 92.2, 92.3)
    is_setup = (any(g in setup_gs for g in g_words)
                and all(g in setup_gs or g in _MODAL_SETTING_G_CODES for g in g_words))
    # after the frame words: a WCS word on the first move still declares the frame
    if not is_setup and (has_xyz or any(letter in info['words'] for letter in 'IJKR')
                         or 28.0 in g_words or 30.0 in g_words or unparsed):
        state['moved'] = True

    # Inspect origin changes before the unparsed return: restoring absolute
    # positions cannot restore the original map after an unresolved shift.
    xy_word = any(letter in masked.upper() for letter in 'XY')
    if any(g in non_modal_gs for g in (92.1, 92.2, 92.3)) or (92.0 in non_modal_gs and xy_word):
        state['shifted'] = True
    if 10.0 in non_modal_gs:
        setup_words = {}
        for m in _WORD_RE.finditer(masked):
            letter = m.group(1)
            if letter not in 'LP' or masked.upper().count(letter) != 1:
                continue
            tail = masked[m.end():].lstrip()
            # A numeric prefix of an expression is not a literal L/P value.
            if not tail or 'A' <= tail[0] <= 'Z':
                setup_words[letter] = float(m.group(2))
        l_word = setup_words.get('L')
        p_word = setup_words.get('P')
        target_wcs = None
        if p_word == 0.0:
            target_wcs = state['wcs']
        elif p_word is not None and p_word.is_integer() and 1 <= p_word <= len(_WCS_G_CODES):
            target_wcs = _WCS_G_CODES[int(p_word) - 1]
        if (l_word in (None, 2.0, 20.0)
                and (xy_word or (l_word in (None, 2.0) and 'R' in masked.upper()))
                and (target_wcs is None or state['home_wcs'] not in _WCS_G_CODES
                     or target_wcs == state['home_wcs'])):
            state['shifted'] = True
    # G52 (local offset), G51 (scale) and G68 (rotation) also move the X/Y work frame
    if (52.0 in g_words and xy_word) or 51.0 in g_words or 68.0 in g_words:
        state['shifted'] = True
    if unparsed and masked.upper().count('G') > len(g_words):
        # A G word that is not read (lower case "g92.1", "G[92]", "G#1") can be any
        # G code: a units or WCS change, or an origin reset without an X/Y word
        # (G92.1/.2/.3). A later explicit word can not undo it.
        state['shifted'] = True
    if 65.0 in g_words or _SUBPROGRAM_CALL_RE.search(masked.upper()):
        # The tool does not see the codes of a subprogram (O-word call, M98, G65), and
        # the lines of an O-word block (sub, while, if, repeat) run 0 or many times.
        state['shifted'] = True
    for g in g_words:
        if g in _CUTTER_COMP_G_CODES:
            state['cutter_comp'] = True
        elif g == 40.0:
            state['cutter_comp'] = False

    if unparsed:
        # Part of the line is not understood (it can hold an axis word), so
        # nothing after it can trust the tracked position or motion mode.
        state['unparsed'] += 1
        state['G'] = None
        state['g_unknown'] = True
        _lose_axes(state, ['X', 'Y', 'Z'])
        return line

    if non_modal_gs:
        # One-shot code: X/Y/Z here are not work-coordinate moves.
        moved = [axis for axis, value in [('X', x_val), ('Y', y_val), ('Z', z_val)]
                 if value is not None]
        if 53.0 in non_modal_gs:
            motion_gs = [g for g in g_words if g in (0.0, 1.0)]
            if motion_gs:
                state['G'] = int(motion_gs[-1])
            # The work offset is unknown, so machine positions cannot
            # establish work coordinates for the moved axes.
            _lose_axes(state, moved)
        elif 28.0 in non_modal_gs or 30.0 in non_modal_gs:
            # Go to the predefined position: the axes it moves are unknown
            # after it (all 3 if none is named).
            _lose_axes(state, moved or ['X', 'Y', 'Z'])
        elif any(g in non_modal_gs for g in (92.1, 92.2, 92.3)):
            # The axis offsets are cleared or restored: every work position changes.
            _lose_axes(state, ['X', 'Y', 'Z'])
        elif 92.0 in non_modal_gs:
            # G92 makes the current position the given values: the work
            # coordinates of the named axes are no longer the tracked ones.
            _lose_axes(state, moved)
        elif 10.0 in non_modal_gs:
            l_word = info['words'].get('L')
            if l_word == 2.0:
                # Keep position tracking conservative for coordinate-system writes.
                _lose_axes(state, ['X', 'Y', 'Z'])
            elif l_word == 20.0:
                _lose_axes(state, moved)
        return line

    motion_gs = [g for g in g_words if g in _MOTION_G_CODES]
    no_level_gs = [g for g in g_words if g in _MOTION_NO_LEVEL_G_CODES]
    other_gs = [
        g for g in g_words
        if g not in _MOTION_G_CODES and g not in _MOTION_NO_LEVEL_G_CODES
        and g not in _MODAL_SETTING_G_CODES
    ]

    is_non_motion_cmd = False
    if motion_gs or no_level_gs:
        state['g_unknown'] = False
    if motion_gs:
        state['G'] = int(motion_gs[-1])
    elif no_level_gs:
        state['G'] = None
        is_non_motion_cmd = True
    elif other_gs:
        is_non_motion_cmd = True
    # else: no G word at all - keep the previous modal G unchanged.

    absolute = state.get('abs', True)
    start = (state['X'], state['Y'], state['Z'])
    was_known = _all_known(state)
    is_motion = state['G'] in _MOTION_G_CODES and not is_non_motion_cmd
    for axis, value in [('X', x_val), ('Y', y_val), ('Z', z_val)]:
        if value is not None:
            if absolute is None:
                # unknown distance mode: the word can be a position or a delta
                _lose_axes(state, [axis])
                continue
            if absolute:
                state[axis] = value
            elif state[axis] is not None:
                state[axis] += value
            if not is_motion:
                state['known'].discard(axis)
            elif absolute:
                state['known'].add(axis)

    if (state['G'] in (2, 3) and not is_non_motion_cmd
            and (has_xyz or any(letter in info['words'] for letter in 'IJR'))):
        return _level_arc(line, info, state, offset_fn, decimals, start, was_known, absolute)

    if not has_xyz:
        return line

    if is_non_motion_cmd:
        if absolute and z_val is not None:
            state['applied'] = 0.0
        return line

    if state['G'] is None:
        if state['g_unknown']:
            # a bare move under an unknown motion mode may be a cut
            state['unknown'] += 1
        if absolute and z_val is not None:
            state['applied'] = 0.0
        return line

    if absolute is None:
        if state['G'] == 1:
            state['unknown'] += 1
        return line

    if state['G'] == 1 and not _in_frame(state):
        # another WCS or other units than those of the height map
        state['frame'] += 1
        if absolute and z_val is not None:
            state['applied'] = 0.0
        return line

    if state['G'] == 1 and state['cutter_comp'] is not False:
        # G41/G42: the tool is off the programmed X/Y, the map would be read at the wrong point
        state['cutter'] += 1
        if absolute and z_val is not None:
            state['applied'] = 0.0
        return line

    if not absolute:
        if state['G'] == 1:
            return _level_relative(line, z_match, state, offset_fn, decimals, start[2], was_known)
        return line

    if state['G'] == 1 and all(state[axis] is not None for axis in ('X', 'Y', 'Z')):
        new_z = state['Z'] + offset_fn(state['X'], state['Y'])
        new_z_str = "{0:.{1}f}".format(new_z, decimals)
        state['applied'] = float(new_z_str) - state['Z']
        return _insert_z_word(line, new_z_str, z_match)

    if state['G'] == 1:
        # a cut at an unknown work position
        state['unknown'] += 1
    if z_val is not None:
        state['applied'] = 0.0
    return line


_GRBL_ERROR_RE = re.compile(r'^\s*((?:error|ALARM)\s*:\s*\d+)', re.IGNORECASE | re.MULTILINE)
_SETTING_RE = re.compile(r'^\s*\$(\d+)\s*=\s*([+\-]?\d*\.?\d+)', re.MULTILINE)
_STATUS_RE = re.compile(r'^\s*<\s*([A-Za-z]+)')
_GC_RE = re.compile(r'\[GC:([^\]]*)\]')


def grbl_reply_error(text):
    """
    Find the first GRBL error or alarm line in a controller reply.

    :param text: decoded reply text; it can hold more than 1 line.
    :return: the text of the first 'error:N' or 'ALARM:N' line, or None.
    """
    if not text:
        return None
    m = _GRBL_ERROR_RE.search(text)
    return m.group(1) if m else None


def probe_timeout_s(travel_z, depth, feedrate, margin_s=5.0):
    """
    Time limit for 1 G38.2 probe move: the time to move from travel_z to
    depth at the probe feedrate (units per minute, G94), times 1.5, plus a margin.

    :return: seconds (float), never less than 10.0.
    """
    try:
        distance = abs(float(travel_z) - float(depth))
        feed = float(feedrate)
    except (TypeError, ValueError):
        return 10.0
    if not math.isfinite(distance) or not math.isfinite(feed) or feed <= 0:
        return 10.0
    return max(10.0, distance / feed * 60.0 * 1.5 + margin_s)


def grbl_report_scale(app_units, report_inches):
    """
    Factor that changes GRBL reported positions ([PRB:], $# offsets) into
    the application units.

    :param app_units: 'MM' or 'IN'.
    :param report_inches: True if the GRBL setting $13 is 1 (reports in inches).
    :return: 1.0, 25.4 or 1 / 25.4.
    """
    app_in = str(app_units).upper() == 'IN'
    if app_in == bool(report_inches):
        return 1.0
    return 1.0 / 25.4 if app_in else 25.4


def parse_grbl_setting(text, number):
    """
    :param text: the reply of the GRBL '$$' command.
    :param number: the setting number (int), for example 13.
    :return: the setting value as float, or None if it is not in the text.
    """
    if not text:
        return None
    for m in _SETTING_RE.finditer(text):
        if int(m.group(1)) == int(number):
            return float(m.group(2))
    return None


def parse_grbl_status_state(line):
    """
    :param line: a GRBL status report, for example '<Idle|MPos:0.000,0.000,0.000|FS:0,0>'.
    :return: the machine state word ('Idle', 'Run', 'Hold', 'Jog', 'Alarm',
        'Door', 'Check', 'Home', 'Sleep'), or None.
    """
    if not line:
        return None
    m = _STATUS_RE.match(line)
    return m.group(1) if m else None


def grbl_modes_confirmed(gc_text, units_cmd):
    """
    :param gc_text: the reply of the GRBL '$G' command.
    :param units_cmd: 'G20' or 'G21'.
    :return: True if the active modes include units_cmd, G90 and G94.
    """
    if not gc_text:
        return False
    m = _GC_RE.search(gc_text)
    if not m:
        return False
    words = m.group(1).split()
    return units_cmd in words and 'G90' in words and 'G94' in words
