import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import appTranslation  # noqa: E402


class TestLanguagesDir(unittest.TestCase):

    def test_source_run_uses_module_folder(self):
        with mock.patch.object(sys, 'frozen', False, create=True):
            expected = os.path.join(os.path.dirname(os.path.abspath(appTranslation.__file__)), 'locale')
            self.assertEqual(appTranslation.languages_dir(), expected)

    def test_frozen_run_uses_locale_next_to_library_zip(self):
        app_root = os.path.join('C:' + os.sep, 'FlatCAM-Evo')
        module_file = os.path.join(app_root, 'lib', 'library.zip', 'appTranslation.py')
        with mock.patch.object(sys, 'frozen', True, create=True), \
                mock.patch.object(appTranslation, '__file__', module_file):
            self.assertEqual(appTranslation.languages_dir(), os.path.join(app_root, 'lib', 'locale'))


if __name__ == '__main__':
    unittest.main()
