#!/usr/bin/env python
"""Identity gate for the test suite.

Collects the pytest node ids of ``tests/``, sorts each one into exactly 1 area and compares the result with
the lists saved in ``tests/gates/collected_<area>.txt``.

    python tests/gates/check_gate.py --update    # rewrite the saved lists (do this in a commit that adds tests)
    python tests/gates/check_gate.py             # gate: exit 1 when a saved id is no longer collected

A removed id fails the gate. Added ids are printed as information. An id that moved from one area to another
(because of a change in the area rules below) is printed as information, not as a failure.

Exit codes: 0 gate passed / lists updated, 1 a saved id was removed or a saved list is missing,
2 pytest could not collect the tests.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATES = Path(__file__).resolve().parent
AREAS = ['tcl', 'plugins', 'objects', 'parsers', 'other']   # evaluation order: first match wins

# file name patterns (matched against the path relative to the repository root, forward slashes)
PLUGIN_PATTERNS = [r'^tests/plugins/', r'^tests/test_paint', r'^tests/test_clearing', r'^tests/test_clipper']
OBJECT_PATTERNS = [r'^tests/test_unify', r'^tests/test_lifecycle', r'^tests/test_appmain',
                   r'^tests/test_app_refactor', r'^tests/test_app_handler']
PARSER_PATTERNS = [r'^tests/test_[^/]*parser', r'^tests/test_[^/]*import', r'^tests/test_[^/]*e2e',
                   r'^tests/test_gerber', r'^tests/test_pdf']
# a test file touches Tcl when it imports tclCommands or names a tclCommands module in a string (mock.patch)
TCL_RE = re.compile(r'''(?m)(?:^\s*(?:from|import)\s+tclCommands)|['"]tclCommands\.''')


def _matches(path, patterns):
    return any(re.search(p, path) for p in patterns)


def _touches_tcl(path):
    try:
        text = (ROOT / path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False
    return TCL_RE.search(text) is not None


def area_of(node_id):
    path = node_id.split('::', 1)[0].replace('\\', '/')
    if _touches_tcl(path):
        return 'tcl'
    if _matches(path, PLUGIN_PATTERNS):
        return 'plugins'
    if _matches(path, OBJECT_PATTERNS):
        return 'objects'
    if _matches(path, PARSER_PATTERNS):
        return 'parsers'
    return 'other'


def collect():
    cmd = [sys.executable, '-m', 'pytest', 'tests', '--collect-only', '-q', '-p', 'no:cacheprovider']
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    ids = [line.strip().replace('\\', '/') for line in proc.stdout.splitlines() if '::' in line]
    if proc.returncode != 0 or not ids:
        sys.stderr.write(proc.stdout[-3000:] + proc.stderr[-3000:])
        sys.stderr.write('\ncollection failed (exit code %s)\n' % proc.returncode)
        sys.exit(2)
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        sys.stderr.write('duplicate node ids: %s\n' % dup[:5])
        sys.exit(2)
    return ids


def group(ids):
    areas = {a: [] for a in AREAS}
    for node_id in ids:
        areas[area_of(node_id)].append(node_id)
    for a in AREAS:
        areas[a].sort()
    assert sum(len(v) for v in areas.values()) == len(ids)
    return areas


def saved_path(area):
    return GATES / ('collected_%s.txt' % area)


def update(areas):
    for area in AREAS:
        saved_path(area).write_text(''.join(i + '\n' for i in areas[area]), encoding='utf-8', newline='\n')
        print('%-8s %5d ids -> %s' % (area, len(areas[area]), saved_path(area).relative_to(ROOT)))
    print('total    %5d ids' % sum(len(v) for v in areas.values()))
    return 0


def check(areas):
    current_all = {i: a for a, ids in areas.items() for i in ids}
    failed = False
    for area in AREAS:
        path = saved_path(area)
        if not path.exists():
            print('FAIL %s: %s is missing (run with --update)' % (area, path.name))
            failed = True
            continue
        saved = [line.strip() for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        current = set(areas[area])
        removed = [i for i in saved if i not in current]
        moved = [i for i in removed if i in current_all]
        gone = [i for i in removed if i not in current_all]
        added = sorted(current - set(saved))
        print('%-8s saved %5d  now %5d  removed %d  added %d  moved %d' %
              (area, len(saved), len(current), len(gone), len(added), len(moved)))
        for i in gone:
            print('  REMOVED  %s' % i)
        for i in moved:
            print('  moved    %s  (now in area %s)' % (i, current_all[i]))
        for i in added:
            print('  added    %s' % i)
        if gone:
            failed = True
    print('GATE FAILED: a saved test id is no longer collected' if failed else 'GATE PASSED')
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--update', action='store_true', help='rewrite tests/gates/collected_<area>.txt')
    args = parser.parse_args()
    areas = group(collect())
    sys.exit(update(areas) if args.update else check(areas))


if __name__ == '__main__':
    main()
