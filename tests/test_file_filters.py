#!/usr/bin/env python
"""
Tests for the open-file dialog filters. On Linux the Qt file dialog matches the filter patterns case-sensitive,
so each pattern must also be there in upper case (EasyEDA writes e.g. 'board.GTL', 'board.DRL').

Run: python tests/test_file_filters.py
"""

import os
import sys
import unittest
from fnmatch import fnmatchcase
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6 import QtWidgets

from appGUI.GUIElements import add_case_variants


def patterns_of(ext_filter):
    """Return the patterns of each filter group, keyed by the group name."""
    groups = {}
    for group in ext_filter.split(';;'):
        name, _sep, patterns = group.rpartition('(')
        groups[name.strip()] = patterns.rstrip(')').split()
    return groups


def matches(ext_filter, filename):
    """True if a file type group, not the 'All Files (*.*)' group, shows the file."""
    return any(fnmatchcase(filename, p)
               for group in patterns_of(ext_filter).values() if '*.*' not in group
               for p in group)


class AddCaseVariantsTests(unittest.TestCase):

    def test_adds_upper_case_after_each_pattern(self):
        result = add_case_variants("Gerber Files (*.gbr *.gtl);;All Files (*.*)")
        self.assertEqual("Gerber Files (*.gbr *.GBR *.gtl *.GTL);;All Files (*.*)", result)

    def test_adds_lower_case_for_an_upper_case_pattern(self):
        self.assertEqual("DXF (*.DXF *.dxf)", add_case_variants("DXF (*.DXF)"))

    def test_keeps_group_name_and_text_without_patterns(self):
        self.assertEqual("SVG File .svg (*.svg *.SVG)", add_case_variants("SVG File .svg (*.svg)"))
        self.assertEqual("No patterns here", add_case_variants("No patterns here"))

    def test_is_idempotent(self):
        once = add_case_variants("Excellon Files (*.drl *.txt);;All Files (*.*)")
        self.assertEqual(once, add_case_variants(once))


class DialogFilterTests(unittest.TestCase):
    """Run the real dialog callbacks with a patched QFileDialog and check the filter they pass to Qt."""

    @classmethod
    def setUpClass(cls):
        cls.qapp = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def captured_filter(self, call):
        stub = SimpleNamespace(log=MagicMock(), inform=MagicMock(), worker_task=MagicMock(),
                               app=MagicMock(), splash=MagicMock())
        stub.app.get_last_folder.return_value = ''
        with patch.object(QtWidgets.QFileDialog, 'getOpenFileNames', return_value=([], '')) as dialog:
            call(stub)
        return dialog.call_args.kwargs['filter']

    def test_gerber_dialog_shows_upper_and_lower_case_files(self):
        from appHandlers.appIO import appIO
        ext_filter = self.captured_filter(lambda stub: appIO.on_file_open_gerber(stub))
        for filename in ('board.gtl', 'board.GTL', 'board.GBR', 'board.gme', 'board.GME', 'board.GKO'):
            self.assertTrue(matches(ext_filter, filename), filename)
        # every group gets the upper case, not only the first one
        self.assertIn('*.ART', patterns_of(ext_filter)['Allegro Files'])

    def test_excellon_gcode_and_hpgl2_dialogs_show_upper_case_files(self):
        from appHandlers.appIO import appIO
        cases = ((appIO.on_file_open_excellon, 'board.DRL'),
                 (appIO.on_file_open_gcode, 'job.NC'),
                 (appIO.on_file_open_hpgl2, 'plot.PLT'))
        for callback, filename in cases:
            with self.subTest(callback=callback.__name__):
                ext_filter = self.captured_filter(lambda stub: callback(stub))
                self.assertTrue(matches(ext_filter, filename))
                self.assertTrue(matches(ext_filter, filename.lower()))

    def test_svg_and_dxf_import_dialogs_show_both_cases(self):
        from appHandlers.appIO import appIO
        cases = ((appIO.on_file_import_svg, 'art.svg'), (appIO.on_file_import_dxf, 'outline.dxf'))
        for callback, filename in cases:
            with self.subTest(callback=callback.__name__):
                ext_filter = self.captured_filter(lambda stub: callback(stub, 'geometry'))
                self.assertTrue(matches(ext_filter, filename))
                self.assertTrue(matches(ext_filter, filename.upper()))


if __name__ == '__main__':
    unittest.main(verbosity=2)
