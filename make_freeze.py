"""Build FlatCAM and its protected updater runtime with cx_Freeze."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import sysconfig
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MAIN_SCRIPT = ROOT / "flatcam.py"
UPDATER_SCRIPT = ROOT / "updater_app.py"
MAIN_EXE_NAME = "FlatCAM.exe"
UPDATER_EXE_NAME = "FlatCAMUpdater.exe"
UPDATER_DIR_NAME = "updater"
UPDATER_RECOVERY_MODULE = "services.updater.recovery"
_UPDATER_RECOVERY_PATH = UPDATER_RECOVERY_MODULE.replace(".", "/")
_MAIN_INCLUDE_DIRS = ("assets", "resources", "locale", "locales", "preprocessors", "config")
# The application resolves its resources relative to the frozen "lib" folder (cx_Freeze layout).
_MAIN_DATA_DIR = "lib"
_ICON_RELATIVE_PATH = Path("assets") / "resources" / "flatcam_icon256.ico"
# Packages imported dynamically (plugins, platform backends) that static analysis misses.
_MAIN_PACKAGES = ("OpenGL", "vispy")

# These remain unset until a build is requested. Tests can replace either
# value without installing cx_Freeze.
Executable = None
Freezer = None


def _load_executable():
    global Executable
    if Executable is None:
        from cx_Freeze import Executable as cx_executable

        Executable = cx_executable
    return Executable


def _load_freezer():
    global Freezer
    if Freezer is None:
        from cx_Freeze.freezer import Freezer as cx_freezer

        Freezer = cx_freezer
    return Freezer


def get_main_include_files(root: Path = ROOT) -> list[tuple[str, str]]:
    """Return existing application asset directories for cx_Freeze."""
    root = Path(root)
    include_files = []
    for directory_name in _MAIN_INCLUDE_DIRS:
        source = root / directory_name
        if source.is_dir():
            include_files.append((str(source), f"{_MAIN_DATA_DIR}/{directory_name}"))
    return include_files


def _icon_kwargs(root: Path) -> dict:
    icon = Path(root) / _ICON_RELATIVE_PATH
    return {"icon": str(icon)} if icon.is_file() else {}


def make_main_executable(root: Path = ROOT):
    """Create the cx_Freeze definition for the GUI application."""
    kwargs = {"target_name": MAIN_EXE_NAME, **_icon_kwargs(root)}
    if sys.platform == "win32":
        kwargs["base"] = "gui"
    return _load_executable()(str(Path(root) / MAIN_SCRIPT.name), **kwargs)


def make_updater_executable(root: Path = ROOT):
    """Create the cx_Freeze definition for the standalone GUI updater."""
    kwargs = {"target_name": UPDATER_EXE_NAME, **_icon_kwargs(root)}
    if sys.platform == "win32":
        kwargs["base"] = "gui"
    return _load_executable()(str(Path(root) / UPDATER_SCRIPT.name), **kwargs)


def create_main_freezer(target_dir: Path, root: Path = ROOT):
    """Create a freezer for the main application stage."""
    return _load_freezer()(
        executables=[make_main_executable(root)],
        target_dir=Path(target_dir),
        include_files=get_main_include_files(root),
        packages=list(_MAIN_PACKAGES),
    )


def create_updater_freezer(target_dir: Path, root: Path = ROOT):
    """Create a freezer for the updater stage and its recovery dependency."""
    return _load_freezer()(
        executables=[make_updater_executable(root)],
        target_dir=Path(target_dir),
        include_files=[],
        packages=[],
        includes=[UPDATER_RECOVERY_MODULE],
    )


def _reset_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _contains_recovery_module(stage_dir: Path) -> bool:
    expected_names = {
        f"{_UPDATER_RECOVERY_PATH}{suffix}".casefold()
        for suffix in (".py", ".pyc", ".pyo")
    }
    for suffix in (".py", ".pyc", ".pyo"):
        for candidate in (
            stage_dir / f"{_UPDATER_RECOVERY_PATH}{suffix}",
            stage_dir / "lib" / f"{_UPDATER_RECOVERY_PATH}{suffix}",
        ):
            if candidate.is_file():
                return True

    for library in stage_dir.rglob("*.zip"):
        try:
            with zipfile.ZipFile(library) as archive:
                names = {name.replace("\\", "/").casefold() for name in archive.namelist()}
        except (OSError, zipfile.BadZipFile):
            continue
        if expected_names & names:
            return True
    return False


def validate_main_stage(stage_dir: Path) -> Path:
    """Reject a main stage that doesn't contain its executable."""
    stage_dir = Path(stage_dir)
    if not (stage_dir / MAIN_EXE_NAME).is_file():
        raise RuntimeError(f"Frozen main application is missing {MAIN_EXE_NAME}")
    return stage_dir


def validate_updater_stage(stage_dir: Path) -> Path:
    """Reject an updater stage missing its executable or recovery runtime."""
    stage_dir = Path(stage_dir)
    if not (stage_dir / UPDATER_EXE_NAME).is_file():
        raise RuntimeError(f"Frozen updater is missing {UPDATER_EXE_NAME}")
    if not _contains_recovery_module(stage_dir):
        raise RuntimeError(f"Frozen updater is missing {UPDATER_RECOVERY_MODULE}")
    return stage_dir


def build_main_stage(target_dir: Path, root: Path = ROOT) -> Path:
    """Freeze the main application into an isolated staging directory."""
    target_dir = Path(target_dir)
    _reset_directory(target_dir)
    create_main_freezer(target_dir, root).freeze()
    return validate_main_stage(target_dir)


def build_updater_stage(target_dir: Path, root: Path = ROOT) -> Path:
    """Freeze and validate the updater in its own staging directory."""
    target_dir = Path(target_dir)
    _reset_directory(target_dir)
    create_updater_freezer(target_dir, root).freeze()
    return validate_updater_stage(target_dir)


def _replace_directory(staged_dir: Path, destination: Path) -> None:
    """Publish a complete directory, restoring the old one if publication fails."""
    staged_dir = Path(staged_dir)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_dir = destination.with_name(f".{destination.name}.previous")
    if backup_dir.exists():
        shutil.rmtree(backup_dir)

    had_destination = destination.exists()
    if had_destination:
        os.replace(destination, backup_dir)
    try:
        os.replace(staged_dir, destination)
    except Exception:
        if had_destination and backup_dir.exists() and not destination.exists():
            os.replace(backup_dir, destination)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir)


def assemble_final_build(main_stage_dir: Path, updater_stage_dir: Path, build_dir: Path) -> Path:
    """Assemble validated stages without exposing an incomplete final tree."""
    main_stage_dir = validate_main_stage(main_stage_dir)
    updater_stage_dir = validate_updater_stage(updater_stage_dir)
    build_dir = Path(build_dir)
    if (main_stage_dir / UPDATER_DIR_NAME).exists():
        raise RuntimeError("Main stage must not contain the stable updater runtime")

    assembled_dir = build_dir.parent / f".{build_dir.name}.assemble"
    _reset_directory(assembled_dir)
    try:
        shutil.copytree(main_stage_dir, assembled_dir, dirs_exist_ok=True)
        shutil.copytree(updater_stage_dir, assembled_dir / UPDATER_DIR_NAME)
        validate_main_stage(assembled_dir)
        validate_updater_stage(assembled_dir / UPDATER_DIR_NAME)
        _replace_directory(assembled_dir, build_dir)
    finally:
        if assembled_dir.exists():
            shutil.rmtree(assembled_dir, ignore_errors=True)
    return build_dir


def default_build_dir() -> Path:
    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    return ROOT / "build" / f"exe.{sysconfig.get_platform()}-{version}"


def build(build_dir: Path | None = None) -> Path:
    """Build both isolated stages and publish their complete final tree."""
    build_dir = Path(build_dir or default_build_dir())
    stage_root = build_dir.parent / f".{build_dir.name}.freeze-stage"
    main_stage_dir = stage_root / "main"
    updater_stage_dir = stage_root / "updater"
    _reset_directory(stage_root)
    try:
        build_main_stage(main_stage_dir)
        build_updater_stage(updater_stage_dir)
        return assemble_final_build(main_stage_dir, updater_stage_dir, build_dir)
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze FlatCAM and its updater.")
    parser.add_argument("--build-dir", metavar="DIR")
    args = parser.parse_args(argv)
    output_dir = build(Path(args.build_dir) if args.build_dir else None)
    print(f"Frozen build created at: {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
