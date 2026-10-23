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
- Development branches keep `version = "Unstable"`, `beta = True` (commit c25b83c0). The release is cut from branch `release/1.0.0` with `version = "1.0.0"`, `beta = False`, tagged `v1.0.0`; that tag is what gets published as the GitHub Release (user decision 2026-10-23).
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
- [x] T2 - GitHubReleasesTransport + transport selection in checker + default URL; tests. Route: delegated writer (2+ non-trivial files). Design: same interface as DigiPublicShareTransport; reads `api.github.com/repos/<owner>/<repo>/releases/latest`; asset name = `manifest-<channel>.json` for `manifest.json`, otherwise the filename unchanged; selected when `global_update_url` host is github.com; default `https://github.com/MiguelF771/flatcam-evo`. Prep commit b4b61535 (transport base class).
- [x] T3 - Windows-only `prepare_releases` and asset naming that matches the GitHub transport; tests. Route: same delegated writer as T2.
  - Commits 4767de0e (transport + selection + default URL), 0073eb63 (`prepare_releases` any subset of channels, `asset_layout="github"` -> flat `<out>/github/` with `flatcam-windows.zip` + `manifest-windows.json`). appMain `_prepare_releases_worker` still uses the digi layout; T4/T6 must call it with `asset_layout="github"` and a windows-only root.
  - Checks: pytest tests/updater tests/test_make_freeze.py -> 138 passed (parent spot check reproduced); full suite -> 2995 passed, 6 skipped.
  - RDD: assessed high (process_boundary appMain.py), granted by user, 4-lens review approved and acknowledged (lineage review-c6aa303e2cf8dffe). Advisory follow-ups (non-blocking): `list_files` docstring / cross-channel manifest filtering, unvalidated asset `digest` format, base-class label still says Digi, default URL returns 404 until the first release exists, dev `Unstable` version cannot be compared by the updater (expected: only release builds update).
- [x] T4 - Working cx_Freeze build: install cx_Freeze, run `make_freeze.py`, fix missing includes/data/icon until the frozen exe starts. Route: delegated (iterative build work).
  - Commits 43d09385 (`requirements-build.txt`: cx_Freeze==8.7.3), 0f0a846d (data dirs incl. `config` shipped under `lib/` because frozen modules resolve paths from `lib/library.zip`; `packages=("OpenGL","vispy")`; exe icons).
  - Checks: `.venv/Scripts/python make_freeze.py` -> exit 0, `build/exe.win-amd64-3.13` (~565 MB); frozen FlatCAM.exe alive after 30-35 s, title "FlatCAM Evo Unstable BETA - 64bit - [3D] New Project - Not saved", no traceback; FlatCAMUpdater.exe prints usage. pytest tests/updater tests/test_make_freeze.py -> 139 passed. Venv is Python 3.13.
  - Not exercised: plugins, Gerber/Excellon open, G-code, ortools/rtree/ezdxf/svglib in the frozen app (smoke test before release).
  - RDD: high (process_boundary tests/test_make_freeze.py), granted, approved and acknowledged (lineage review-df8c07b8635836b2). Advisory: tests do not cover `config` inclusion or vispy in packages; silent icon fallback; `lib/` layout only proven by manual launch. `config/` holds only `configuration.txt` (portable flag), nothing sensitive.
- [x] T5 - Inno Setup script (per-user) and installer build; verify install, launch, uninstall. Route: delegated writer. Compiler: `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe` (installed by the user 2026-10-23, per-user, not on PATH).
  - Commits 89d1a73c (installer/flatcam-evo.iss, make_installer.py, tests; AppId {F817F45A-2C0C-489B-B9E0-3109ED524040}, never change), bc0ef109 (uninstall deletes only lib/share/updater/*.exe/*.dll/license then dirifempty; [InstallDelete] lib+share on upgrade; UsePreviousAppDir/DirExistsWarning/DisableDirPage=auto; version via ast from class App before freezing; absolute paths; single OutputBaseFilename source).
  - Build: `.venv/Scripts/python make_installer.py [--skip-freeze]` -> `build/installer/FlatCAM-Evo-Setup-<version>.exe` (~147 MB). lzma2/max compile is slow (2.5-30 min).
  - Checks: tests/test_make_installer.py + test_make_freeze.py -> 25 passed; silent per-user install (HKCU uninstall key, Start menu shortcut), installed app alive with title after 30 s, silent uninstall removes app, shortcut, key; foreign file in install dir survives; stale lib/share files removed on upgrade; %APPDATA%\FlatCAM-Evo kept. Git Bash gotcha: run Setup with `MSYS_NO_PATHCONV=1` or `/VERYSILENT` gets path-mangled.
  - RDD: 89d1a73c high, granted, approved + acknowledged (lineage review-ef004e84ca7b9d74); its whole-{app} uninstall finding fixed in bc0ef109 (review granted, running).
- [ ] T6 - Release flow doc; branch `release/1.0.0` (version 1.0.0, beta False), tag `v1.0.0`, build installer from it, publish GitHub Release v1.0.0 (confirm with user right before push/publish); verify the installed app reports "up to date". Route: inline.

## Acceptance criteria
- Installed app starts from the Start menu without Python installed, title shows 1.0.0, settings in `%APPDATA%\FlatCAM-Evo`.
- Help -> Check for Updates queries GitHub Releases of MiguelF771/flatcam-evo and reports correctly.
- Digi transport still selected for a DIGI share URL; existing updater tests still pass.

## Progress
- 2026-10-23: feature doc created; branch created from Beta_1.0 (e653b92f).
- 2026-10-23: T1 done. Interim note: until T2 lands, the startup check still queries the upstream DIGI share.
- Review boundary: 0f0a846d (T4 approved).

- 2026-10-23: c25b83c0 restored `Unstable`/beta on the dev branch; release versioning moved to `release/1.0.0` + tag `v1.0.0` (user decision).

## Next step
T6: release/1.0.0 from this feature branch (user decision 2026-10-23), version 1.0.0/beta False, build installer + github assets, tag v1.0.0, confirm before push/publish.
