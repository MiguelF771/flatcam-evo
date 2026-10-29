"""Deterministic full-archive release preparation."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .manifest import (
    ArchiveInfo,
    Manifest,
    ManifestError,
    build_manifest,
    manifest_to_json,
    sha256_file,
    validate_relative_path,
)
from .transport import github_asset_name


_EXCLUDED_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "cache",
        "dist",
        "docs",
        "doc",
        "freeze",
        "settings",
        "tests",
        "user_data",
        "user_config",
        "user-settings",
        "user_settings",
        "userdata",
        "venv",
    }
)
_EXCLUDED_FILES = frozenset(
    {
        "build.py",
        "build_freeze.py",
        "freeze.py",
        "make_freeze.py",
        "make_freezed.py",
        "setup_freeze.py",
    }
)
_EXCLUDED_SUFFIXES = (".flatconfig", ".flatprj")
_PROTECTED_ROOT_NAMES = frozenset({"config", "updater"})
_CHANNELS = ("windows", "linux")
_ASSET_LAYOUTS = ("digi", "github")


@dataclass(frozen=True)
class ExclusionPolicy:
    """Explicit file and directory policy for managed release payloads."""

    excluded_names: frozenset[str] = _EXCLUDED_NAMES
    excluded_files: frozenset[str] = _EXCLUDED_FILES
    excluded_suffixes: tuple[str, ...] = _EXCLUDED_SUFFIXES

    def excludes(self, relative_path: str) -> bool:
        path = validate_relative_path(relative_path)
        parts = path.lower().split("/")
        if parts[0] in _PROTECTED_ROOT_NAMES:
            return True
        if any(part in self.excluded_names for part in parts[:-1]):
            return True
        filename = parts[-1]
        if filename in self.excluded_names or filename in self.excluded_files:
            return True
        return filename.endswith(self.excluded_suffixes)


DEFAULT_EXCLUSION_POLICY = ExclusionPolicy()


def _resolved_path(path: Path) -> str:
    """Return an absolute, case-normalized path for safe relationship checks."""
    try:
        return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))
    except (OSError, RuntimeError) as exc:
        raise ManifestError("Could not resolve release path safely: %s" % path) from exc


def _paths_overlap(left: str, right: str) -> bool:
    try:
        common = os.path.commonpath((left, right))
    except ValueError:
        return False
    return common == left or common == right


def validate_release_roots(windows_root: Path, source_root: Path) -> None:
    """Validate the completed frozen build and the source release root."""
    windows_root = Path(windows_root)
    source_root = Path(source_root)
    required_windows = (
        windows_root / "FlatCAM.exe",
        windows_root / "updater" / "FlatCAMUpdater.exe",
    )
    required_source = (
        source_root / "flatcam.py",
        source_root / "updater_app.py",
        source_root / "services" / "updater",
    )
    missing = [str(path.relative_to(windows_root)) for path in required_windows if not path.is_file()]
    missing.extend(
        str(path.relative_to(source_root))
        for path in required_source
        if not path.is_file() and not path.is_dir()
    )
    if missing:
        raise ManifestError("Release input is missing required paths: %s" % ", ".join(missing))


@dataclass(frozen=True)
class ReleaseArtifact:
    channel: str
    archive_path: Path
    manifest_path: Path
    manifest: Manifest


@dataclass(frozen=True)
class ReleasePreparation:
    artifacts: dict[str, ReleaseArtifact]
    upload_paths: tuple[Path, ...]


def managed_files(root: Path, policy=DEFAULT_EXCLUSION_POLICY) -> tuple[tuple[str, Path], ...]:
    """Return sorted payload files after applying the explicit exclusion policy."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ManifestError(f"Release root {str(root)!r} is not a directory.")
    result = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative_path = path.relative_to(root).as_posix()
        if not policy.excludes(relative_path):
            result.append((relative_path, path))
    return tuple(result)


def build_deterministic_zip(root: Path, destination: Path, policy=DEFAULT_EXCLUSION_POLICY) -> Path:
    """Write a sorted, timestamp-independent ZIP archive and replace atomically."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.part")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for relative_path, source in managed_files(root, policy):
                info = zipfile.ZipInfo(relative_path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                info.external_attr = 0
                with source.open("rb") as input_stream, archive.open(info, "w") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def prepare_releases(
    roots: Mapping[str, Path],
    output_dir: Path,
    *,
    version,
    build_string: str = "",
    version_date: str = "",
    minimum_required_version: str = "0",
    release_notes: str = "",
    deletions=(),
    policy=DEFAULT_EXCLUSION_POLICY,
    progress_cb=None,
    asset_layout: str = "digi",
) -> ReleasePreparation:
    """Build full ZIP/manifest pairs for the given channels for manual upload.

    ``roots`` may hold any non-empty subset of the Windows and Linux channels.
    The ``digi`` layout writes ``<channel>/manifest.json`` per channel folder;
    the ``github`` layout writes every file flat into ``<output>/github`` with
    the asset names GitHubReleasesTransport expects (``manifest-<channel>.json``).
    """
    channels = tuple(channel for channel in _CHANNELS if channel in roots)
    if not channels or set(roots) - set(_CHANNELS):
        raise ManifestError("Release roots must be a non-empty subset of 'windows' and 'linux'.")
    if asset_layout not in _ASSET_LAYOUTS:
        raise ManifestError("Unknown release asset layout %r." % (asset_layout,))
    resolved_output = _resolved_path(output_dir)
    for channel in channels:
        resolved_root = _resolved_path(roots[channel])
        if _paths_overlap(resolved_output, resolved_root):
            raise ManifestError(
                "Release output directory must not overlap the %s release root." % channel
            )
    deletions = tuple(deletions)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_names = {
        "windows": "flatcam-windows.zip",
        "linux": "flatcam-source.zip",
    }
    if asset_layout == "github":
        manifest_names = {channel: github_asset_name(channel, "manifest.json") for channel in channels}
    else:
        manifest_names = {channel: "manifest.json" for channel in channels}
    staging_dir = Path(tempfile.mkdtemp(prefix=".release-", dir=output_dir))
    staged_artifacts = {}
    try:
        for index, channel in enumerate(channels, start=1):
            channel_dir = staging_dir / channel
            archive_path = channel_dir / archive_names[channel]
            manifest_path = channel_dir / manifest_names[channel]
            build_deterministic_zip(roots[channel], archive_path, policy)
            archive = ArchiveInfo(
                filename=archive_path.name,
                size=archive_path.stat().st_size,
                sha256=sha256_file(archive_path),
            )
            manifest = build_manifest(
                roots[channel],
                version,
                channel=channel,
                archive=archive,
                build_string=build_string,
                version_date=version_date,
                minimum_required_version=minimum_required_version,
                release_notes=release_notes,
                deletions=deletions,
                exclusions=policy,
            )
            manifest_path.write_text(manifest_to_json(manifest), encoding="utf-8", newline="")
            staged_artifacts[channel] = (archive_path, manifest_path, manifest)
            if progress_cb:
                progress_cb(index * 100 // len(channels), "Prepared %s release files." % channel)

        backups = []
        replaced = []
        try:
            artifacts = {}
            upload_paths = []
            backup_dir = staging_dir / ".backup"
            for channel in channels:
                final_channel_dir = output_dir / ("github" if asset_layout == "github" else channel)
                final_channel_dir.mkdir(parents=True, exist_ok=True)
                staged_archive, staged_manifest, manifest = staged_artifacts[channel]
                for staged_path, final_path in (
                    (staged_archive, final_channel_dir / archive_names[channel]),
                    (staged_manifest, final_channel_dir / manifest_names[channel]),
                ):
                    backup_path = backup_dir / channel / final_path.name
                    if final_path.exists():
                        backup_path.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(final_path, backup_path)
                        backups.append((final_path, backup_path))
                    os.replace(staged_path, final_path)
                    replaced.append(final_path)
                artifact = ReleaseArtifact(
                    channel,
                    final_channel_dir / archive_names[channel],
                    final_channel_dir / manifest_names[channel],
                    manifest,
                )
                artifacts[channel] = artifact
                upload_paths.extend((artifact.archive_path, artifact.manifest_path))
        except Exception:
            for final_path in reversed(replaced):
                final_path.unlink(missing_ok=True)
            for final_path, backup_path in reversed(backups):
                if backup_path.exists():
                    os.replace(backup_path, final_path)
            raise
        return ReleasePreparation(artifacts=artifacts, upload_paths=tuple(upload_paths))
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
