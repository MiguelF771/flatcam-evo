from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import make_freeze
from services.updater.release import build_deterministic_zip


class FakeExecutable:
    def __init__(self, script, **kwargs):
        self.script = script
        self.kwargs = kwargs


def _write_recovery_runtime(target: Path) -> None:
    library = target / "lib" / "library.zip"
    library.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(library, "w") as archive:
        archive.writestr("services/updater/recovery.pyc", b"recovery")


def _fake_freezer_class(calls, *, include_recovery=True, include_executable=True):
    class FakeFreezer:
        def __init__(self, *, executables, target_dir, **kwargs):
            self.executables = executables
            self.target_dir = Path(target_dir)
            self.kwargs = kwargs
            calls.append(self)

        def freeze(self):
            self.target_dir.mkdir(parents=True, exist_ok=True)
            executable = self.executables[0]
            if include_executable:
                (self.target_dir / executable.kwargs["target_name"]).write_bytes(b"executable")
            if self.target_dir.name == "updater" and include_recovery:
                _write_recovery_runtime(self.target_dir)

    return FakeFreezer


class TestMakeFreeze(unittest.TestCase):
    def test_import_does_not_require_cx_freeze(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.modules['cx_Freeze'] = None; import make_freeze",
            ],
            cwd=make_freeze.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stderr)

    def test_executable_definitions_use_windows_names_and_gui_base(self):
        with patch.object(make_freeze, "Executable", FakeExecutable):
            main = make_freeze.make_main_executable()
            updater = make_freeze.make_updater_executable()

        self.assertEqual("flatcam.py", Path(main.script).name)
        self.assertEqual("FlatCAM.exe", main.kwargs["target_name"])
        self.assertEqual("gui", main.kwargs["base"])
        self.assertEqual("updater_app.py", Path(updater.script).name)
        self.assertEqual("FlatCAMUpdater.exe", updater.kwargs["target_name"])
        self.assertEqual("gui", updater.kwargs["base"])

    def test_executables_use_application_icon_when_present(self):
        root = Path(self._tempdir.name) / "source"
        icon = root / "assets" / "resources" / "flatcam_icon256.ico"
        icon.parent.mkdir(parents=True)
        icon.write_bytes(b"ico")

        with patch.object(make_freeze, "Executable", FakeExecutable):
            main = make_freeze.make_main_executable(root)
            updater = make_freeze.make_updater_executable(root)
            bare = make_freeze.make_main_executable(Path(self._tempdir.name) / "empty")

        self.assertEqual(str(icon), main.kwargs["icon"])
        self.assertEqual(str(icon), updater.kwargs["icon"])
        self.assertNotIn("icon", bare.kwargs)

    def test_main_include_files_only_contains_existing_payload_directories(self):
        root = Path(self._tempdir.name) / "source"
        for relative in ("assets", "locale", "preprocessors"):
            (root / relative).mkdir(parents=True)
        (root / "docs").mkdir()

        include_files = make_freeze.get_main_include_files(root)

        destinations = {Path(destination).as_posix() for _, destination in include_files}
        self.assertEqual({"lib/assets", "lib/locale", "lib/preprocessors"}, destinations)
        self.assertNotIn("lib/docs", destinations)

    def test_freezers_keep_main_and_updater_stages_independent(self):
        calls = []
        fake_freezer = _fake_freezer_class(calls)
        with patch.object(make_freeze, "Executable", FakeExecutable), patch.object(
            make_freeze, "Freezer", fake_freezer
        ):
            with self.subTest(stage="main"):
                main_stage = Path(self._tempdir.name) / "main"
                make_freeze.build_main_stage(main_stage)
            with self.subTest(stage="updater"):
                updater_stage = Path(self._tempdir.name) / "updater"
                make_freeze.build_updater_stage(updater_stage)

        self.assertEqual(2, len(calls))
        self.assertEqual(main_stage, calls[0].target_dir)
        self.assertEqual(updater_stage, calls[1].target_dir)
        self.assertEqual(["services.updater.recovery"], calls[1].kwargs["includes"])
        self.assertIn("OpenGL", calls[0].kwargs["packages"])
        self.assertTrue((main_stage / "FlatCAM.exe").is_file())
        self.assertTrue((updater_stage / "FlatCAMUpdater.exe").is_file())

    def test_incomplete_updater_is_rejected_without_touching_existing_final_runtime(self):
        calls = []
        fake_freezer = _fake_freezer_class(calls, include_recovery=False)
        build_dir = Path(self._tempdir.name) / "build"
        stable_updater = build_dir / "updater"
        stable_updater.mkdir(parents=True)
        (build_dir / "FlatCAM.exe").write_bytes(b"old-main")
        (stable_updater / "FlatCAMUpdater.exe").write_bytes(b"old-updater")

        with patch.object(make_freeze, "Executable", FakeExecutable), patch.object(
            make_freeze, "Freezer", fake_freezer
        ):
            with self.assertRaisesRegex(RuntimeError, "services.updater.recovery"):
                make_freeze.build(build_dir)

        self.assertEqual(b"old-main", (build_dir / "FlatCAM.exe").read_bytes())
        self.assertEqual(
            b"old-updater", (build_dir / "updater" / "FlatCAMUpdater.exe").read_bytes()
        )

    def test_final_assembly_keeps_complete_updater_runtime_in_fixed_subfolder(self):
        main_stage = Path(self._tempdir.name) / "main-stage"
        updater_stage = Path(self._tempdir.name) / "updater-stage"
        build_dir = Path(self._tempdir.name) / "build"
        (main_stage / "lib").mkdir(parents=True)
        (main_stage / "FlatCAM.exe").write_bytes(b"main")
        (main_stage / "lib" / "library.zip").write_bytes(b"main-runtime")
        updater_stage.mkdir()
        (updater_stage / "FlatCAMUpdater.exe").write_bytes(b"updater")
        _write_recovery_runtime(updater_stage)
        (updater_stage / "runtime" / "support.dat").parent.mkdir(parents=True)
        (updater_stage / "runtime" / "support.dat").write_bytes(b"support")

        make_freeze.assemble_final_build(main_stage, updater_stage, build_dir)

        self.assertEqual(b"main", (build_dir / "FlatCAM.exe").read_bytes())
        self.assertEqual(
            b"updater", (build_dir / "updater" / "FlatCAMUpdater.exe").read_bytes()
        )
        self.assertEqual(
            b"support", (build_dir / "updater" / "runtime" / "support.dat").read_bytes()
        )
        self.assertTrue((build_dir / "updater" / "lib" / "library.zip").is_file())
        self.assertFalse((build_dir / "FlatCAMUpdater.exe").exists())

    def test_release_zip_excludes_build_script_and_stable_updater_runtime(self):
        root = Path(self._tempdir.name) / "release-root"
        root.mkdir()
        (root / "flatcam.py").write_text("payload", encoding="utf-8")
        (root / "make_freeze.py").write_text("build", encoding="utf-8")
        (root / "make_freezed.py").write_text("build", encoding="utf-8")
        (root / "build.py").write_text("build", encoding="utf-8")
        (root / "updater" / "FlatCAMUpdater.exe").parent.mkdir()
        (root / "updater" / "FlatCAMUpdater.exe").write_bytes(b"stable")

        archive_path = Path(self._tempdir.name) / "release.zip"
        build_deterministic_zip(root, archive_path)

        with ZipFile(archive_path) as archive:
            names = set(archive.namelist())
        self.assertIn("flatcam.py", names)
        self.assertNotIn("make_freeze.py", names)
        self.assertNotIn("make_freezed.py", names)
        self.assertNotIn("build.py", names)
        self.assertNotIn("updater/FlatCAMUpdater.exe", names)

    def setUp(self):
        import tempfile

        self._tempdir = tempfile.TemporaryDirectory(prefix="flatcam-freeze-test-")

    def tearDown(self):
        self._tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
