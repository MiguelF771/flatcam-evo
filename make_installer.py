"""Build the per-user Windows installer with Inno Setup."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_MAIN = ROOT / "appMain.py"
ISS_SCRIPT = ROOT / "installer" / "flatcam-evo.iss"
DEFAULT_OUTPUT_DIR = ROOT / "build" / "installer"
ISCC_NAME = "ISCC.exe"
FALLBACK_NUMERIC_VERSION = "0.0.0"

_VERSION_RE = re.compile(r"^\s*version\s*=\s*([\"'])(?P<version>[^\"']+)\1", re.MULTILINE)
_NUMERIC_VERSION_RE = re.compile(r"^\d+(\.\d+){0,3}$")


def read_app_version(app_main: Path = APP_MAIN) -> str:
    """Return ``App.version`` from appMain.py without importing the application."""
    text = Path(app_main).read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if not match:
        raise RuntimeError(f"Could not find a string 'version' assignment in {app_main}")
    return match.group("version").strip()


def numeric_version(version: str) -> str:
    """Return a numeric version usable by Inno's VersionInfoVersion."""
    version = version.lstrip("vV")
    return version if _NUMERIC_VERSION_RE.match(version) else FALLBACK_NUMERIC_VERSION


def find_iscc(env: dict | None = None, which=shutil.which) -> Path | None:
    """Locate ISCC: PATH first, then the per-user and system Inno Setup installs."""
    env = os.environ if env is None else env
    found = which("ISCC")
    if found:
        return Path(found)
    candidates = []
    if env.get("LOCALAPPDATA"):
        candidates.append(Path(env["LOCALAPPDATA"]) / "Programs" / "Inno Setup 6" / ISCC_NAME)
    for key in ("ProgramFiles(x86)", "ProgramFiles"):
        if env.get(key):
            candidates.append(Path(env[key]) / "Inno Setup 6" / ISCC_NAME)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def installer_path(version: str, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Path:
    return Path(output_dir) / f"FlatCAM-Evo-Setup-{version}.exe"


def build_command(iscc: Path, version: str, source_dir: Path, output_dir: Path,
                  script: Path = ISS_SCRIPT) -> list[str]:
    """Return the ISCC command line for the given build inputs."""
    return [
        str(iscc),
        f"/DAppVersion={version}",
        f"/DNumericVersion={numeric_version(version)}",
        f"/DSourceDir={Path(source_dir)}",
        f"/DOutputDir={Path(output_dir)}",
        str(script),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the FlatCAM Evo Windows installer.")
    parser.add_argument("--skip-freeze", action="store_true",
                        help="reuse the existing frozen build instead of rebuilding it")
    parser.add_argument("--build-dir", metavar="DIR", help="frozen build directory")
    parser.add_argument("--output-dir", metavar="DIR", help="installer output directory")
    parser.add_argument("--version", help="override the version read from appMain.py")
    args = parser.parse_args(argv)

    iscc = find_iscc()
    if iscc is None:
        print("error: Inno Setup compiler (ISCC.exe) not found. Install Inno Setup 6 "
              "or add ISCC to PATH.", file=sys.stderr)
        return 1

    import make_freeze

    build_dir = Path(args.build_dir) if args.build_dir else make_freeze.default_build_dir()
    if not args.skip_freeze:
        build_dir = make_freeze.build(build_dir)
    try:
        make_freeze.validate_main_stage(build_dir)
    except RuntimeError as exc:
        print(f"error: {exc} (in {build_dir}); run without --skip-freeze", file=sys.stderr)
        return 1

    version = args.version or read_app_version()
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_OUTPUT_DIR
    result = subprocess.run(build_command(iscc, version, build_dir, output_dir), check=False)
    if result.returncode != 0:
        print(f"error: ISCC failed with exit code {result.returncode}", file=sys.stderr)
        return result.returncode
    print(f"Installer created at: {installer_path(version, output_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
