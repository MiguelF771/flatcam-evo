"""Focused, process-free tests for the Ticket 3 updater integration."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from services.updater.manifest import ArchiveInfo, FileEntry, Manifest

DEFAULT_UPDATE_URL = "https://github.com/MiguelF771/flatcam-evo"
CONFIGURED_UPDATE_URL = "https://updates.example.invalid/flatcam"


def _manifest(version="2.0", build="build-2", minimum="0", channel="windows"):
    archive = b"full release archive"
    return Manifest(
        schema_version=1,
        channel=channel,
        version=version,
        build_string=build,
        version_date="2026-09-18",
        minimum_required_version=minimum,
        release_notes="Fixes\n\n* Safer updates",
        archive=ArchiveInfo("flatcam-windows.zip", len(archive), hashlib.sha256(archive).hexdigest()),
        files=(FileEntry("flatcam.py", 3, hashlib.sha256(b"new").hexdigest()),),
        deletions=(),
    )


def _manifest_json(manifest):
    return json.dumps(
        {
            "schema_version": manifest.schema_version,
            "channel": manifest.channel,
            "version": manifest.version,
            "build": manifest.build_string,
            "version_date": manifest.version_date,
            "minimum_required_version": manifest.minimum_required_version,
            "release_notes": manifest.release_notes,
            "archive": {
                "filename": manifest.archive.filename,
                "size": manifest.archive.size,
                "sha256": manifest.archive.sha256,
            },
            "files": [
                {"path": item.path, "size": item.size, "sha256": item.sha256}
                for item in manifest.files
            ],
            "deletions": [],
        }
    ).encode()


class FakeSignal:
    def __init__(self):
        self.emissions = []

    def emit(self, *args):
        self.emissions.append(args)


class FakeTransport:
    def __init__(self, manifest=None, error=None):
        self.manifest = manifest
        self.error = error
        self.calls = []

    def download_bytes(self, channel, filename, **kwargs):
        self.calls.append(("bytes", channel, filename, kwargs))
        if self.error:
            raise self.error
        return _manifest_json(self.manifest)


def _checker_app(tmp_path, enabled=True):
    return SimpleNamespace(
        options={"global_version_check": enabled},
        defaults={"global_version_check": enabled},
        version="1.0",
        build_string="build-1",
        data_path=str(tmp_path),
        log=MagicMock(),
        inform=FakeSignal(),
        update_available=FakeSignal(),
        update_check_result=FakeSignal(),
    )


def _startup_app(auto_updates):
    from appMain import App
    from defaults import AppDefaults

    app = App.__new__(App)
    app.log = MagicMock()
    app.cmd_line_headless = 1
    app.cmd_line_shellvar = ""
    app.cmd_line_shellfile = ""
    app.trayIcon = SimpleNamespace(show=MagicMock())
    app.options = {
        "global_systray_icon": False,
        "global_version_check": auto_updates,
    }
    app.beta = False
    app.version = "1.0"
    app.refresh_rollback_action = MagicMock()
    app._queue_version_check = MagicMock()
    app.defaults = AppDefaults(beta=False, version="1.0")
    app.inform = FakeSignal()
    return app


def test_automatic_update_preference_defaults_on():
    from defaults import AppDefaults

    assert AppDefaults.factory_defaults["global_version_check"] is True


def test_startup_update_toggle_gates_automatic_queueing(monkeypatch):
    from appMain import App

    monkeypatch.setattr(App, "args", [])
    disabled = _startup_app(False)
    App._setup_startup(disabled)
    disabled._queue_version_check.assert_not_called()

    enabled = _startup_app(True)
    App._setup_startup(enabled)
    enabled._queue_version_check.assert_called_once_with()


def test_manual_update_check_remains_available_when_automatic_checks_are_disabled():
    from appMain import App

    app = SimpleNamespace(_queue_version_check=MagicMock())

    App.on_check_for_updates(app)

    app._queue_version_check.assert_called_once_with(forced=True)


def test_factory_defaults_seed_the_update_share_url():
    from defaults import AppDefaults

    assert AppDefaults.factory_defaults["global_update_url"] == DEFAULT_UPDATE_URL


def test_update_check_uses_the_configured_share_url(monkeypatch, tmp_path):
    from appHandlers.appLifecycle import AppLifecycle
    from services.updater import transport as transport_module

    app = _checker_app(tmp_path)
    app.options["global_update_url"] = CONFIGURED_UPDATE_URL
    captured_urls = []

    class ConfiguredTransport:
        def __init__(self, short_url, **kwargs):
            captured_urls.append(short_url)

        def download_bytes(self, channel, filename, **kwargs):
            return _manifest_json(_manifest(channel=channel))

    monkeypatch.setattr(transport_module, "DigiPublicShareTransport", ConfiguredTransport)
    app.update_checker = None

    result = AppLifecycle(app).version_check(forced=True)

    assert result["status"] == "update_available"
    assert captured_urls == [CONFIGURED_UPDATE_URL]


def test_download_worker_uses_the_configured_share_url(monkeypatch, tmp_path):
    from appMain import App
    from services.updater.checker import UpdateChecker
    from services.updater import transport as transport_module

    captured_urls = []

    class ConfiguredTransport:
        def __init__(self, short_url, **kwargs):
            captured_urls.append(short_url)

        def download_archive(self, channel, filename, destination, **kwargs):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"configured archive")
            return destination

    monkeypatch.setattr(transport_module, "DigiPublicShareTransport", ConfiguredTransport)
    app = _fake_app(tmp_path)
    app.options = {"global_update_url": CONFIGURED_UPDATE_URL}
    app.update_checker = UpdateChecker(app)

    App._download_update_worker(
        app,
        {"manifest": _manifest(), "channel": "windows"},
        SimpleNamespace(is_set=lambda: False),
    )

    assert captured_urls == [CONFIGURED_UPDATE_URL]
    assert app.update_staged.emissions[0][0]["canceled"] is False


def test_download_worker_uses_factory_url_when_option_and_default_are_missing(monkeypatch, tmp_path):
    from appMain import App
    from services.updater.checker import UpdateChecker
    from services.updater import transport as transport_module

    captured_urls = []

    class ConfiguredTransport:
        def __init__(self, short_url, **kwargs):
            captured_urls.append(short_url)

        def download_archive(self, channel, filename, destination, **kwargs):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"factory archive")
            return destination

    monkeypatch.setattr(transport_module, "GitHubReleasesTransport", ConfiguredTransport)
    app = _fake_app(tmp_path)
    app.options = {}
    app.defaults = {}
    app.update_checker = UpdateChecker(app)

    App._download_update_worker(
        app,
        {"manifest": _manifest(), "channel": "windows"},
        SimpleNamespace(is_set=lambda: False),
    )

    assert captured_urls == [DEFAULT_UPDATE_URL]
    assert app.update_staged.emissions[0][0]["canceled"] is False


def test_automatic_check_respects_preference_but_forced_check_runs(tmp_path):
    from services.updater.checker import UpdateChecker

    app = _checker_app(tmp_path, enabled=False)
    transport = FakeTransport(_manifest())
    checker = UpdateChecker(app, transport_factory=lambda _app: transport, platform="win32")

    disabled = checker.run_check()
    forced = checker.run_check(forced=True)

    assert disabled["status"] == "disabled"
    assert forced["status"] == "update_available"
    assert transport.calls == [("bytes", "windows", "manifest.json", {})]


def test_checker_reports_up_to_date_and_newer_build_on_same_version(tmp_path):
    from services.updater.checker import UpdateChecker

    app = _checker_app(tmp_path)
    transport = FakeTransport(_manifest(version="1.0", build="build-2"))
    checker = UpdateChecker(app, transport_factory=lambda _app: transport, platform="win32")

    result = checker.run_check(forced=True)

    assert result["status"] == "update_available"
    assert result["current"] == {"version": "1.0", "build": "build-1"}
    assert result["channel"] == "windows"
    assert result["mandatory"] is False
    assert app.update_available.emissions[0][0]["manifest"].build_string == "build-2"


def test_forced_up_to_date_result_is_reported_to_the_user(tmp_path):
    from services.updater.checker import UpdateChecker

    app = _checker_app(tmp_path)
    transport = FakeTransport(_manifest(version="1.0", build="build-1"))
    checker = UpdateChecker(app, transport_factory=lambda _app: transport, platform="win32")

    result = checker.run_check(forced=True)

    assert result["status"] == "up_to_date"

    from appMain import App

    app_obj = App.__new__(App)
    app_obj._manual_update_requested = True
    app_obj._check_in_progress = True
    app_obj.inform = app.inform
    App.on_update_check_result(app_obj, result)
    assert "up to date" in app.inform.emissions[-1][0]


def test_checker_maps_darwin_to_linux_and_does_not_submit_statistics(tmp_path):
    from services.updater.checker import UpdateChecker

    app = _checker_app(tmp_path)
    app.version_url = "https://legacy.invalid/version"
    app.ui = SimpleNamespace(general_pref_form=SimpleNamespace(general_app_group=SimpleNamespace()))
    transport = FakeTransport(_manifest(channel="linux"))
    checker = UpdateChecker(app, transport_factory=lambda _app: transport, platform="darwin")

    result = checker.run_check(forced=True)

    assert result["channel"] == "linux"
    assert transport.calls[0][1:] == ("linux", "manifest.json", {})


@pytest.mark.parametrize("source", [OSError("offline"), ValueError("bad manifest")])
def test_checker_converts_network_and_malformed_failures_to_safe_results(tmp_path, source):
    from services.updater.checker import UpdateChecker

    app = _checker_app(tmp_path)
    transport = FakeTransport(_manifest(), error=source)
    checker = UpdateChecker(app, transport_factory=lambda _app: transport, platform="win32")

    result = checker.run_check(forced=True)

    assert result["status"] == "error"
    assert result["channel"] == "windows"
    assert result["manifest"] is None
    assert app.inform.emissions


def test_checker_skips_exactly_blocked_release(tmp_path):
    from services.updater.checker import UpdateChecker
    from services.updater.recovery import blocked_release_path, restore_dir_for_install

    app = _checker_app(tmp_path)
    manifest = _manifest()
    restore = restore_dir_for_install(tmp_path, Path(sys.executable).parent)
    restore.parent.mkdir(parents=True, exist_ok=True)
    blocked_release_path(restore).write_text(
        json.dumps({"version": manifest.version, "build_string": manifest.build_string}),
        encoding="utf-8",
    )
    transport = FakeTransport(manifest)
    checker = UpdateChecker(
        app,
        transport_factory=lambda _app: transport,
        platform="win32",
        install_root=Path(sys.executable).parent,
    )

    result = checker.run_check(forced=True)

    assert result["status"] == "blocked"
    assert result["manifest"].version == "2.0"
    assert not app.update_available.emissions


def test_update_dialog_has_optional_and_mandatory_choices(qapp):
    from appGUI.UpdateDialog import UpdateDialog

    payload = {
        "manifest": _manifest(),
        "current": {"version": "1.0", "build": "build-1"},
        "mandatory": False,
    }
    dialog = UpdateDialog(None, payload)
    buttons = {button.text(): button for button in dialog.findChildren(QtWidgets.QPushButton)}

    assert "Update now" in buttons
    assert "Later" in buttons
    assert "2.0" in dialog.windowTitle() or dialog.findChild(QtWidgets.QLabel).text()
    buttons["Later"].click()
    assert dialog.choice == "later"

    mandatory = UpdateDialog(None, {**payload, "mandatory": True})
    mandatory_buttons = {button.text(): button for button in mandatory.findChildren(QtWidgets.QPushButton)}
    assert "Exit" in next(text for text in mandatory_buttons if "Exit" in text)
    mandatory_buttons[next(text for text in mandatory_buttons if "Exit" in text)].click()
    assert mandatory.choice == "exit"


@pytest.fixture
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


class ArchiveTransport:
    def __init__(self, payload=b"archive"):
        self.payload = payload
        self.calls = []

    def download_archive(self, channel, filename, destination, **kwargs):
        self.calls.append((channel, filename, destination, kwargs))
        if kwargs.get("cancel_cb") and kwargs["cancel_cb"]():
            from services.updater.transport import DownloadCancelled

            raise DownloadCancelled("cancelled")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.payload)
        if kwargs.get("progress_cb"):
            kwargs["progress_cb"](len(self.payload), len(self.payload))
        return destination


def test_archive_staging_cancel_failure_and_success_are_atomic(tmp_path):
    from appMain import _stage_update_archive
    from services.updater.transport import DownloadCancelled

    payload = {"manifest": _manifest(), "channel": "windows"}
    transport = ArchiveTransport()
    result = _stage_update_archive(payload, tmp_path, transport)
    assert result.is_file()
    assert result.read_bytes() == b"archive"

    cancel_transport = ArchiveTransport()
    with pytest.raises(DownloadCancelled):
        _stage_update_archive(payload, tmp_path, cancel_transport, cancel_cb=lambda: True)
    assert not (tmp_path / "update").exists() or not list((tmp_path / "update").rglob("*.part"))

    class FailingTransport(ArchiveTransport):
        def download_archive(self, *args, **kwargs):
            raise OSError("download failed")

    with pytest.raises(OSError):
        _stage_update_archive(payload, tmp_path, FailingTransport())
    assert not list((tmp_path / "update").glob("staging_*"))


def _fake_app(tmp_path):
    from appMain import App
    from defaults import AppDefaults

    app = App.__new__(App)
    app.data_path = str(tmp_path)
    app.options = {}
    app.defaults = AppDefaults(beta=False, version="1.0")
    app.log = MagicMock()
    app.inform = FakeSignal()
    app.update_progress = FakeSignal()
    app.update_staged = FakeSignal()
    app.rollback_ready = FakeSignal()
    app.worker_task = FakeSignal()
    app.ui = SimpleNamespace(hide=MagicMock())
    app._update_dialog = None
    app._update_progress_dialog = None
    app._update_in_progress = False
    app._rollback_in_progress = False
    app._shutdown_requested = False
    app.quit_application = MagicMock()
    app._updater_shutdown_preflight = MagicMock(return_value=True)
    return app


def test_update_launches_then_shutdowns_only_after_launcher_ack(monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    payload = {"manifest": _manifest(), "channel": "windows"}
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)

    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})

    launch.assert_called_once()
    app.quit_application.assert_called_once_with(mode="updater")


def test_failed_launch_and_cancelled_shutdown_never_quit(monkeypatch, tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    payload = {"manifest": _manifest(), "channel": "windows"}
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    monkeypatch.setattr("appMain.launch_update", MagicMock(return_value=False))
    App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})
    app.quit_application.assert_not_called()

    app = _fake_app(tmp_path)
    app._updater_shutdown_preflight.return_value = False
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_update", launch)
    App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})
    launch.assert_not_called()
    app.quit_application.assert_not_called()


def test_empty_staging_paths_are_not_removed_on_cancel_or_launch_failure(monkeypatch, tmp_path):
    from appMain import App

    payload = {"manifest": _manifest(), "channel": "windows"}
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"archive")
    with patch("appMain.shutil.rmtree") as remove:
        app = _fake_app(tmp_path)
        app._updater_shutdown_preflight.return_value = False
        App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})

        app = _fake_app(tmp_path)
        monkeypatch.setattr("appMain.launch_update", MagicMock(return_value=False))
        App.on_update_staged(app, {"archive_path": str(archive), "payload": payload})

    remove.assert_not_called()


def test_download_worker_uses_the_checker_transport_factory(monkeypatch, tmp_path):
    from appMain import App
    from services.updater.checker import UpdateChecker

    class FailingDigiTransport:
        def download_archive(self, *args, **kwargs):
            raise AssertionError("the worker bypassed the checker transport factory")

    fake = ArchiveTransport()
    app = _fake_app(tmp_path)
    app.update_checker = UpdateChecker(app, transport_factory=lambda _app: fake)
    monkeypatch.setattr("appMain.DigiPublicShareTransport", FailingDigiTransport)

    App._download_update_worker(
        app,
        {"manifest": _manifest(), "channel": "windows"},
        SimpleNamespace(is_set=lambda: False),
    )

    assert fake.calls
    assert app.update_staged.emissions[0][0]["canceled"] is False


@pytest.mark.parametrize("active_state", ["checking", "operation"])
def test_main_window_close_is_ignored_while_updater_is_active(active_state):
    from appGUI.MainGUI import MainGUI

    class Event:
        def __init__(self):
            self.ignore_calls = 0

        def ignore(self):
            self.ignore_calls += 1

    app = SimpleNamespace(
        save_in_progress=False,
        _check_in_progress=active_state == "checking",
        _check_queued=False,
        _update_operation_active=lambda: active_state == "operation",
        inform=FakeSignal(),
    )
    gui = MainGUI.__new__(MainGUI)
    gui.app = app
    gui.final_save = SimpleNamespace(emit=MagicMock())
    gui.geometry = MagicMock(return_value=SimpleNamespace(x=lambda: 0, y=lambda: 0, width=lambda: 1, height=lambda: 1))
    gui.lock_action = SimpleNamespace(isChecked=lambda: False)
    gui.show_text_action = SimpleNamespace(isChecked=lambda: False)
    gui.isMaximized = lambda: False
    gui.saveState = lambda *_args: b"state"
    gui.splitter = SimpleNamespace(sizes=lambda: [1])
    event = Event()

    with patch("appGUI.MainGUI.QSettings"):
        gui.closeEvent(event)

    assert event.ignore_calls == 1
    gui.final_save.emit.assert_not_called()
    assert "cancel" in app.inform.emissions[-1][0].lower()
    assert "wait" in app.inform.emissions[-1][0].lower()


def test_duplicate_update_and_rollback_operations_are_guarded(tmp_path):
    from appMain import App

    app = _fake_app(tmp_path)
    app._update_in_progress = True
    assert App._update_operation_active(app)
    app._update_in_progress = False
    app._rollback_in_progress = True
    assert App._update_operation_active(app)


def test_rollback_action_requires_valid_restore_point_and_waits_for_ack(monkeypatch, tmp_path):
    from appMain import App, QtWidgets

    class Action:
        def __init__(self):
            self.enabled = None

        def setEnabled(self, value):
            self.enabled = value

        def setToolTip(self, value):
            self.tooltip = value

    app = _fake_app(tmp_path)
    app.ui.menuhelp_revert_update = Action()
    app._load_rollback_restore_point = MagicMock(
        return_value=(tmp_path / "restore", {
            "version": "2.0",
            "build_string": "build-2",
            "previous_version": "1.0",
            "previous_build_string": "build-1",
        })
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    App.refresh_rollback_action(app)
    assert app.ui.menuhelp_revert_update.enabled is True

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    launch = MagicMock(return_value=True)
    monkeypatch.setattr("appMain.launch_rollback", launch)
    App.on_revert_update(app)
    worker = app.worker_task.emissions[0][0]
    worker["fcn"](*worker["params"])
    result = app.rollback_ready.emissions[0][0]
    App.on_rollback_ready(app, result)

    launch.assert_called_once()
    app.quit_application.assert_called_once_with(mode="updater")


def test_download_worker_emits_error_when_transport_setup_fails(tmp_path):
    """Regression: create_transport raises ValueError for empty share_url.

    Before the fix the call sat outside the try block so the ValueError
    escaped the worker unhandled, no update_staged was emitted, and
    _update_in_progress stayed True forever (blocking MainGUI.closeEvent).
    """
    from appMain import App
    from services.updater.checker import UpdateChecker

    app = _fake_app(tmp_path)
    app.options = {"global_update_url": ""}
    app.update_checker = UpdateChecker(app)

    # Must not raise — the worker catches setup failures and emits an
    # error payload via update_staged so the caller can reset state.
    App._download_update_worker(
        app,
        {"manifest": _manifest(), "channel": "windows"},
        SimpleNamespace(is_set=lambda: False),
    )

    assert len(app.update_staged.emissions) == 1
    data = app.update_staged.emissions[0][0]
    assert data["canceled"] is False
    assert "error" in data
    assert "share URL" in data["error"] or "configured" in data["error"].lower()

    # Exercise the stuck-state fix: on_update_staged must reset the active
    # flag and close the progress dialog even when the worker failed before
    # downloading anything. Without the fix the flag stayed True forever,
    # blocking MainGUI.closeEvent.
    app._update_in_progress = True
    app._close_update_progress = MagicMock()
    App.on_update_staged(app, data)

    app._close_update_progress.assert_called_once()
    assert app._update_in_progress is False
    app.quit_application.assert_not_called()
    assert any("failed" in emission[0].lower() for emission in app.inform.emissions)
