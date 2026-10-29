# Releasing FlatCAM Evo (Windows)

Installed copies update themselves from the **latest GitHub Release** of
`MiguelF771/flatcam-evo`. A release therefore needs the installer for new users
plus two updater files for existing installs.

Development branches keep `version = "Unstable"` and `beta = True`; the updater
cannot compare that label, so only release builds update.

## Prerequisites

- Python venv with the app requirements plus `requirements-build.txt` (cx_Freeze).
- Inno Setup 6 (`ISCC` on PATH, or the default per-user/system install folder).
- `gh` logged in with push access to the repository.

## Steps

1. **Release branch.** From the branch that holds the release code:
   ```
   git switch -c release/X.Y.Z
   ```
   In `appMain.py` set `version = "X.Y.Z"` (dotted integers only),
   `version_date = "YYYY/MM/DD"` and `beta = False`. Commit
   `chore(release): version X.Y.Z`.

2. **Build and installer.**
   ```
   .venv/Scripts/python make_installer.py
   ```
   Freezes the app into `build/exe.win-amd64-<py>` and writes
   `build/installer/FlatCAM-Evo-Setup-X.Y.Z.exe`. Compression is slow (minutes).
   Use `--skip-freeze` to reuse an existing frozen build.

3. **Smoke test.** Install silently into a scratch folder, start the app, check
   the title shows `X.Y.Z`, then uninstall. From Git Bash prefix the command
   with `MSYS_NO_PATHCONV=1`, otherwise `/VERYSILENT` is mangled into a path:
   ```
   MSYS_NO_PATHCONV=1 build/installer/FlatCAM-Evo-Setup-X.Y.Z.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER "/DIR=<scratch>" /TASKS=
   MSYS_NO_PATHCONV=1 "<scratch>/unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
   ```

4. **Updater files.**
   ```
   .venv/Scripts/python - <<'EOF'
   from pathlib import Path
   from services.updater.release import prepare_releases
   prepare_releases({"windows": Path("build/exe.win-amd64-3.13")}, Path("build/release"),
       version="X.Y.Z", build_string="YYYY/MM/DD", version_date="YYYY/MM/DD",
       release_notes="...", asset_layout="github")
   EOF
   ```
   `build_string` must equal the app's `version_date`: the installed app compares
   it with the manifest to decide it is up to date. Output:
   `build/release/github/flatcam-windows.zip` and `manifest-windows.json`.

5. **Tag, push, publish.**
   ```
   git tag -a vX.Y.Z -m "FlatCAM Evo X.Y.Z"
   git push -u origin release/X.Y.Z
   git push origin vX.Y.Z
   gh release create vX.Y.Z build/installer/FlatCAM-Evo-Setup-X.Y.Z.exe \
       build/release/github/flatcam-windows.zip build/release/github/manifest-windows.json \
       --title "FlatCAM Evo X.Y.Z" --notes-file notes.md --latest
   ```
   The updater reads `releases/latest`, so drafts and pre-releases are ignored.

6. **Verify.** An installed older version reports the update in
   Help → Check for Updates; the new version reports it is up to date.

## Notes

- Keep the Inno Setup `AppId` in `installer/flatcam-evo.iss` unchanged forever;
  upgrades over an existing install depend on it.
- The installer is not code-signed; SmartScreen may warn on first run.
