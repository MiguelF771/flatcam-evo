# Windows installer and GitHub Releases updates

## Objective
Ship the fork (github.com/MiguelF771/flatcam-evo) as a per-user Windows installer whose built-in updater pulls new versions from this repository's GitHub Releases instead of the upstream author's DIGI public share.

## Problem
- `make_freeze.py` (cx_Freeze) produces a portable folder only; there is no installer.
- The updater only knows `DigiPublicShareTransport` and `global_update_url` points to the upstream share (`https://s.go.ro/zihniipc`), so an installed fork would update itself to upstream builds.
- `App.version` is `"Unstable"`; `parse_version` accepts only dotted integers, so every update check would fail. `beta=True` also disables the startup check.
- The app stores settings in `%APPDATA%\FlatCAM`, shared with FlatCAM 8.994 (the two versions overwrite each other's preferences).

## Why
User request: an installable build that receives updates from their own public repository.

## Scope / authorized
- Version `1.0.0`, `beta=False` (user decision 2026-10-23).
- Own data folder `%APPDATA%\FlatCAM-Evo` (user decision 2026-10-23).
- GitHub Releases transport for the updater, selected from `global_update_url`; default to this repo.
- Windows-only release preparation (no linux channel required).
- Working cx_Freeze build and an Inno Setup per-user installer.

## Constraints
- Install location must be user-writable (updater rewrites the install dir): `%LOCALAPPDATA%\Programs\FlatCAM Evo`, no admin.
- Keep the Digi transport working (upstream compatibility).
- Publishing a GitHub release (tag + assets) is outward-facing: confirm with the user before doing it.
- Keep `Beta_1.0` clean for `git pull upstream Beta_1.0`; work on `feat/windows-installer-github-updates`.

## TDD / checks
- TDD: off (no project or session configuration; not chosen by the user). Ordinary functional checks.
- Runner: `.venv\Scripts\python -m pytest` (pytest installed only in the local venv).
- Applicable checks: `pytest tests/updater tests/test_make_freeze.py`, plus the full suite at task closure; manual launch of the frozen exe and of the installed app.

## Delivery
- Forecast: ~500-700 authored changed lines in total. Strategy: ask-on-risk (default). Push and PR stay the user's decision.

## Tasks
- [x] T1 - Version 1.0.0, beta=False, data folder FlatCAM-Evo (appMain.py). Route: inline (1 file, mechanical). Commit ed8e0b32. Checks: pytest tests/updater tests/test_make_freeze.py tests/test_app_refactor_characterization.py -> 143 passed; launch -> `.appdata/FlatCAM-Evo` created, log "Checking for updates in background (this is version 1.0.0)". RDD: assessed high (process_boundary in appMain.py), review declined by user for this candidate.
- [ ] T2 - GitHubReleasesTransport + transport selection in checker + default URL; tests. Route: delegated writer (2+ non-trivial files).
- [ ] T3 - Windows-only `prepare_releases` and asset naming that matches the GitHub transport; tests. Route: delegated writer (can be merged with T2).
- [ ] T4 - Working cx_Freeze build: install cx_Freeze, run `make_freeze.py`, fix missing includes/data/icon until the frozen exe starts. Route: delegated (iterative build work).
- [ ] T5 - Inno Setup script (per-user) and installer build; verify install, launch, uninstall. Route: delegated writer.
- [ ] T6 - Release flow doc + first release v1.0.0 on GitHub (after user confirmation); verify the installed app reports "up to date". Route: inline.

## Acceptance criteria
- Installed app starts from the Start menu without Python installed, title shows 1.0.0, settings in `%APPDATA%\FlatCAM-Evo`.
- Help -> Check for Updates queries GitHub Releases of MiguelF771/flatcam-evo and reports correctly.
- Digi transport still selected for a DIGI share URL; existing updater tests still pass.

## Progress
- 2026-10-23: feature doc created; branch created from Beta_1.0 (e653b92f).
- 2026-10-23: T1 done. Interim note: until T2 lands, the startup check still queries the upstream DIGI share.
- Review boundary: ed8e0b32 (T1 declined by user; next assessment uses it as base).

## Next step
T2 + T3 (one delegated writer).
