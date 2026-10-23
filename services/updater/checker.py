"""Background update checks for full-archive FlatCAM releases."""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from .manifest import (
    ManifestError,
    compare_versions,
    is_newer,
    parse_manifest,
    parse_version,
    select_channel,
)
from .recovery import is_release_blocked, restore_dir_for_install


_log = logging.getLogger(__name__)


def _app_log(app, level: str, message: str) -> None:
    logger = getattr(app, "log", None)
    method = getattr(logger, level, None)
    if callable(method):
        try:
            method(message)
            return
        except Exception:
            pass
    method = getattr(_log, level, None)
    if callable(method):
        method(message)


def _inform(app, message: str, level: str = "warning") -> None:
    _app_log(app, level, message)
    signal = getattr(app, "inform", None)
    emit = getattr(signal, "emit", None)
    if callable(emit):
        try:
            emit(f"[{level.upper()}] {message}")
        except Exception:
            pass


def _emit(app, signal_name: str, payload: dict) -> None:
    signal = getattr(app, signal_name, None)
    emit = getattr(signal, "emit", None)
    if callable(emit):
        try:
            emit(payload)
        except Exception:
            _app_log(app, "debug", f"UpdateChecker: {signal_name}.emit() failed.")


def _local_build(app) -> str:
    for name in ("build_string", "build", "version_date"):
        try:
            value = getattr(app, name, None)
        except RuntimeError:
            value = None
        if value:
            return str(value)
    return ""


def _install_root(app) -> Path:
    configured = getattr(app, "install_root", None)
    if configured:
        return Path(configured)
    try:
        from .launcher import _install_root

        return Path(_install_root())
    except Exception:
        return Path(sys.executable).resolve().parent


class UpdateChecker:
    """Fetch, validate, and compare one release manifest off the GUI thread."""

    def __init__(
        self,
        app=None,
        *,
        transport_factory=None,
        platform=None,
        install_root=None,
        share_url=None,
    ):
        self.app = app
        self.transport_factory = transport_factory
        self.platform = platform
        self.install_root = install_root
        self.share_url = share_url

    def create_transport(self, app=None, *, share_url=None):
        """Create the configured release transport for checks and downloads."""
        app = app or self.app
        if self.transport_factory is not None:
            return self.transport_factory(app)
        from . import transport as transport_module

        share_url = self.share_url if share_url is None else share_url
        if not share_url:
            raise ValueError("Update share URL is not configured.")
        host = (urlsplit(share_url).hostname or "").lower()
        if host in transport_module.GITHUB_HOSTS:
            return transport_module.GitHubReleasesTransport(share_url)
        return transport_module.DigiPublicShareTransport(short_url=share_url)

    def _transport(self, app=None, *, share_url=None):
        """Compatibility alias for older checker callers."""
        return self.create_transport(app, share_url=share_url)

    def _payload(self, status, channel, app, manifest=None, *, mandatory=False, error=""):
        current = {
            "version": str(getattr(app, "version", "")),
            "build": _local_build(app),
        }
        payload = {
            "status": status,
            "manifest": manifest,
            "channel": channel,
            "mandatory": bool(mandatory),
            "current": current,
            "current_version": current["version"],
            "current_build": current["build"],
        }
        if error:
            payload["error"] = error
        return payload

    def run_check(self, app=None, *, forced=False, share_url=None):
        """Run one check and return a result safe for a worker task."""
        app = app or self.app
        try:
            return self._check(app, forced=forced, share_url=share_url)
        except Exception as exc:
            _app_log(app, "error", f"UpdateChecker failed:\n{traceback.format_exc()}")
            channel = self.platform or getattr(app, "os", sys.platform)
            try:
                channel = select_channel(channel)
            except ManifestError:
                channel = "linux"
            result = self._payload("error", channel, app, error=str(exc))
            _inform(app, "Failed checking for the latest version safely.")
            _emit(app, "update_check_result", result)
            return result

    def _check(self, app, *, forced=False, share_url=None):
        preference = getattr(app, "options", None)
        if preference is None:
            preference = getattr(app, "defaults", {})
        defaults = getattr(app, "defaults", {})
        if not forced and not preference.get("global_version_check", defaults.get("global_version_check")):
            channel = select_channel(self.platform or getattr(app, "os", sys.platform))
            result = self._payload("disabled", channel, app)
            _emit(app, "update_check_result", result)
            return result

        channel = select_channel(self.platform or getattr(app, "os", sys.platform))
        transport = self._transport(app, share_url=share_url)
        if transport is None:
            result = self._payload("error", channel, app, error="No update transport is available.")
            _inform(app, "Failed checking for the latest version. No update transport is available.")
            _emit(app, "update_check_result", result)
            return result

        try:
            raw_manifest = transport.download_bytes(channel, "manifest.json")
            manifest = parse_manifest(raw_manifest)
            parse_version(manifest.minimum_required_version)
            if manifest.channel != channel:
                raise ManifestError(
                    f"Manifest channel {manifest.channel!r} does not match {channel!r}."
                )
        except (OSError, ManifestError, RuntimeError, TypeError, ValueError) as exc:
            result = self._payload("error", channel, app, error=str(exc))
            _inform(app, "Failed checking for the latest version. Could not retrieve a valid manifest.")
            _emit(app, "update_check_result", result)
            return result

        local_version = getattr(app, "version", "0")
        local_build = _local_build(app)
        if not is_newer(manifest.version, local_version, manifest.build_string, local_build):
            result = self._payload("up_to_date", channel, app, manifest=manifest)
            _app_log(app, "debug", "The application is up to date.")
            _emit(app, "update_check_result", result)
            return result

        restore_dir = restore_dir_for_install(
            getattr(app, "data_path", Path.home() / ".FlatCAM"),
            self.install_root or _install_root(app),
        )
        if is_release_blocked(restore_dir, manifest.version, manifest.build_string):
            result = self._payload("blocked", channel, app, manifest=manifest)
            _inform(app, "The available release was previously reverted and remains blocked.")
            _emit(app, "update_check_result", result)
            return result

        mandatory = compare_versions(
            manifest.minimum_required_version,
            local_version,
            "",
            local_build,
        ) > 0
        result = self._payload(
            "update_available",
            channel,
            app,
            manifest=manifest,
            mandatory=mandatory,
        )
        _emit(app, "update_check_result", result)
        _emit(app, "update_available", result)
        return result
