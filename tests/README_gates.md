# Test gates

All commands run from the repository root with the project interpreter (`.venv/Scripts/python.exe` on Windows).
Never start the GUI (`flatcam.py`) for a gate. No test needs a display.

## 1. Collection

```
.venv/Scripts/python.exe -m pytest tests --collect-only -q -p no:cacheprovider
```

Baseline before the Stage 0A/0B test files: **1559 tests collected**. The count rises only when a commit adds tests
(re-save the identity lists in the same commit, see section 3).

## 2. Custom runners: nothing is hidden from pytest

48 existing test files under `tests/` (2 of them in `tests/updater/`) end with `if __name__ == '__main__': ...` (a `unittest.main()` or a hand-made runner).
That block is a convenience only. I compared, per file, the `test*` functions found by `ast` (including methods)
with the node ids pytest collects:

- every `test*` function is collected; the only differences are files where pytest collects MORE ids than there are
  functions (`@pytest.mark.parametrize`): `test_defaults_audit_regressions.py`, `test_levelling_controller_regressions.py`,
  `test_levelling_e2e.py`, `test_levelling_lifecycle_regressions.py`, `test_levelling_result_regressions.py`,
  `test_levelling_safety_regressions.py`, `updater/test_ticket3.py`, `updater/test_ticket5.py`.
- `tests/test_tools_database.py` has the classes `LifecycleChecks`, `EditorChecks`, `ConsumerChecks`. They do not start with
  `Test`, but they derive from `unittest.TestCase` (via `DatabaseCase`), so pytest collects all 52 methods.
- `tests/test_gerber_parser.py` (39 tests) uses plain `Test*` classes. `unittest discover` would miss them; pytest collects them.
- Inside `tests/` every `.py` file except `conftest.py` is named `test_*.py`, so default pytest discovery finds all of them.
- No existing test file imports `tclCommands`, so the `tcl` area is empty today.

So `pytest tests` is the complete gate; there is no file that needs a separate command. The `__main__` runners work as a
shortcut for 1 file (`python tests/test_gerber_parser.py`), but they are NOT part of any gate, because they skip the
conftest and the identity diff.

## 3. Identity gate (a count alone is not enough)

`tests/gates/check_gate.py` collects the node ids, sorts every id into exactly 1 area and compares it with
`tests/gates/collected_<area>.txt`.

| Area | Files |
|---|---|
| `tcl` | any test file that imports `tclCommands` (or patches a `tclCommands.` name); evaluated first |
| `plugins` | `tests/plugins/`, `test_paint*`, `test_clearing*`, `test_clipper*` |
| `objects` | `test_unify*`, `test_lifecycle*`, `test_appmain*`, `test_app_refactor*`, `test_app_handler*` |
| `parsers` | `test_*parser*`, `test_*import*`, `test_*e2e*`, `test_gerber*`, `test_pdf*` |
| `other` | everything else |

```
.venv/Scripts/python.exe tests/gates/check_gate.py            # gate: exit 1 if a saved id is no longer collected
.venv/Scripts/python.exe tests/gates/check_gate.py --update   # rewrite the saved lists (only in a commit that adds tests)
```

Rules:
- A **removed** id fails the gate (exit 1). It passes only if the commit message names the removed test and says why.
  Then run `--update` in that commit.
- An **added** id is printed as information. Run `--update` in the commit that adds the test.
- An id that **moved** to another area (the area rules above changed) is information, not a failure.
- Exit code 2 means pytest could not collect; fix that first.

## 4. Gate run

```
.venv/Scripts/python.exe -m pytest tests -q -rA -p no:cacheprovider
```

`-rA` prints the full outcome list: passed / failed / skipped / xfailed / xpassed. Read it like this:
- `xfailed` for tests with `@pytest.mark.xfail(strict=True, reason="fixed in Stage <X>")` where `<X>` is a LATER stage: counts as passing.
- `xpassed` (strict) shows up as a failure: the fix landed, so the commit must remove the marker.
- Characterization tests (`tests/test_unify_characterization.py`) must pass in every stage unless the stage changes the behavior on purpose.
- Regression tests: `tests/test_unify_regressions_0b.py` (Stage 0B) and the `TestRegression...` class in
  `tests/test_unify_characterization.py` (Stage 0C).

Quick subsets (same interpreter, same flags):

```
.venv/Scripts/python.exe -m pytest tests/test_unify_characterization.py tests/test_unify_regressions_0b.py -rA -p no:cacheprovider
.venv/Scripts/python.exe -m pytest tests/test_gerber_parser.py tests/test_pdf_hole_detection.py -rA -p no:cacheprovider
```

## 5. Fixtures for the unification tests

`tests/fixtures/unify/`:
- `unify_mixed.gbr`: C, R, O, P (5 vertices, rotation 30), 2 macro apertures (`OUTL` outline, `RING` with parameters), tracks,
  a G36/G37 region, and a dark -> clear -> dark sequence.
- `unify_native.gbr`: the subset the native Gerber writer represents (C, R, O, tracks, region); used for the file round trip.
- `unify_mixed.drl`: Excellon, METRIC, 3 tools, drills and G85 slots.
- The "2 tools with the same diameter and different data" case lives in Python (`make_same_diameter_excellon`).
