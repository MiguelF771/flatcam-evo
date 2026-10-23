from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import make_installer


class TestReadAppVersion(unittest.TestCase):
    def setUp(self):
        self._tempdir = tempfile.TemporaryDirectory(prefix="flatcam-installer-test-")
        self.addCleanup(self._tempdir.cleanup)
        self.path = Path(self._tempdir.name) / "appMain.py"

    def test_reads_version_string(self):
        self.path.write_text('class App:\n    version = "1.0.0"\n', encoding="utf-8")
        self.assertEqual("1.0.0", make_installer.read_app_version(self.path))

    def test_ignores_commented_assignment(self):
        self.path.write_text('    version = "Unstable"\n    # version = 1.0\n', encoding="utf-8")
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
        self.assertEqual("a.iss", cmd[-1])

    def test_installer_path(self):
        self.assertEqual(
            Path("out") / "FlatCAM-Evo-Setup-1.0.0.exe",
            make_installer.installer_path("1.0.0", Path("out")),
        )


if __name__ == "__main__":
    unittest.main()
