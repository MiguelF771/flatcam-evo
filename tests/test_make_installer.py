from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import make_installer


class TestReadAppVersion(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory(prefix="flatcam-installer-test-")
        self.addCleanup(self._tempdir.cleanup)
        self.path = Path(self._tempdir.name) / "appMain.py"

    def test_reads_version_string(self):
        self.path.write_text('class App:\n    version = "1.0.0"\n', encoding="utf-8")
        self.assertEqual("1.0.0", make_installer.read_app_version(self.path))

    def test_ignores_assignment_outside_app_class(self):
        self.path.write_text(
            'version = "decoy"\nclass Other:\n    version = "other"\n'
            'class App:\n    # version = "commented"\n    version = "Unstable"\n',
            encoding="utf-8",
        )
        self.assertEqual("Unstable", make_installer.read_app_version(self.path))

    def test_missing_version_raises(self):
        self.path.write_text("x = 1\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            make_installer.read_app_version(self.path)

    def test_reads_real_app_main(self):
        self.assertTrue(make_installer.read_app_version())

    def test_numeric_version(self):
        self.assertEqual("1.0.0", make_installer.numeric_version("1.0.0"))
        self.assertEqual("1.2.3", make_installer.numeric_version("v1.2.3"))
        self.assertEqual("0.0.0", make_installer.numeric_version("Unstable"))
        self.assertEqual("0.0.0", make_installer.numeric_version("1.0.0-rc1"))


class TestFindIscc(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory(prefix="flatcam-iscc-test-")
        self.addCleanup(self._tempdir.cleanup)
        self.base = Path(self._tempdir.name)

    def _make_iscc(self, *parts):
        path = self.base.joinpath(*parts, "Inno Setup 6", "ISCC.exe")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
        return path

    def test_path_wins(self):
        self._make_iscc("local", "Programs")
        found = make_installer.find_iscc(
            {"LOCALAPPDATA": str(self.base / "local")}, which=lambda name: "C:/path/ISCC.exe"
        )
        self.assertEqual(Path("C:/path/ISCC.exe"), found)

    def test_per_user_before_program_files(self):
        local = self._make_iscc("local", "Programs")
        self._make_iscc("pf86")
        env = {"LOCALAPPDATA": str(self.base / "local"), "ProgramFiles(x86)": str(self.base / "pf86")}
        self.assertEqual(local, make_installer.find_iscc(env, which=lambda name: None))

    def test_program_files_fallback(self):
        pf86 = self._make_iscc("pf86")
        env = {"LOCALAPPDATA": str(self.base / "none"), "ProgramFiles(x86)": str(self.base / "pf86")}
        self.assertEqual(pf86, make_installer.find_iscc(env, which=lambda name: None))

    def test_missing_returns_none(self):
        self.assertIsNone(make_installer.find_iscc({}, which=lambda name: None))


class TestBuildCommand(unittest.TestCase):
    def test_command_contains_defines_and_script(self):
        cmd = make_installer.build_command(
            Path("ISCC.exe"), "Unstable", Path("src"), Path("out"), Path("a.iss")
        )
        self.assertEqual("ISCC.exe", cmd[0])
        self.assertIn("/DAppVersion=Unstable", cmd)
        self.assertIn("/DNumericVersion=0.0.0", cmd)
        self.assertIn(f"/DSourceDir={Path('src')}", cmd)
        self.assertIn(f"/DOutputDir={Path('out')}", cmd)
        self.assertIn("/DOutputBaseFilename=FlatCAM-Evo-Setup-Unstable", cmd)
        self.assertEqual("a.iss", cmd[-1])

    def test_installer_path(self):
        self.assertEqual(
            Path("out") / "FlatCAM-Evo-Setup-1.0.0.exe",
            make_installer.installer_path("1.0.0", Path("out")),
        )


class TestMain(unittest.TestCase):
    def setUp(self):
        self.freeze = mock.Mock()
        self.freeze.default_build_dir.return_value = Path("default-build")
        self.freeze.build.side_effect = lambda d: d
        patches = [
            mock.patch.dict("sys.modules", {"make_freeze": self.freeze}),
            mock.patch.object(make_installer, "find_iscc", return_value=Path("ISCC.exe")),
            mock.patch.object(make_installer, "read_app_version", return_value="1.0.0"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        run = mock.patch.object(
            make_installer.subprocess, "run", return_value=types.SimpleNamespace(returncode=0)
        )
        self.run = run.start()
        self.addCleanup(run.stop)

    def test_missing_iscc_returns_1(self):
        with mock.patch.object(make_installer, "find_iscc", return_value=None):
            self.assertEqual(1, make_installer.main([]))
        self.run.assert_not_called()

    def test_bad_version_fails_before_freeze(self):
        with mock.patch.object(make_installer, "read_app_version", side_effect=RuntimeError("nope")):
            self.assertEqual(1, make_installer.main([]))
        self.freeze.build.assert_not_called()
        self.run.assert_not_called()

    def test_iscc_failure_exit_code_is_propagated(self):
        self.run.return_value = types.SimpleNamespace(returncode=5)
        self.assertEqual(5, make_installer.main(["--skip-freeze"]))

    def test_skip_freeze_does_not_build(self):
        self.assertEqual(0, make_installer.main(["--skip-freeze"]))
        self.freeze.build.assert_not_called()
        self.freeze.validate_main_stage.assert_called_once()

    def test_freeze_runs_by_default(self):
        self.assertEqual(0, make_installer.main([]))
        self.freeze.build.assert_called_once()

    def test_paths_are_absolute_in_command(self):
        argv = ["--skip-freeze", "--build-dir", "rel-build", "--output-dir", "rel-out"]
        self.assertEqual(0, make_installer.main(argv))
        cmd = self.run.call_args[0][0]
        source = next(a for a in cmd if a.startswith("/DSourceDir="))[len("/DSourceDir="):]
        output = next(a for a in cmd if a.startswith("/DOutputDir="))[len("/DOutputDir="):]
        self.assertTrue(Path(source).is_absolute())
        self.assertTrue(Path(output).is_absolute())


if __name__ == "__main__":
    unittest.main()
