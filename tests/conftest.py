"""pytest configuration for the FlatCAM tests.

appMain.App parses sys.argv with getopt when the class is defined, and it calls sys.exit(2) on an unknown
option. pytest options such as -q or -x would stop every test that imports appMain (for example Gerber() imports
it). Import appMain 1 time here with a clean sys.argv; the later imports get the module from sys.modules.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_saved_argv = sys.argv
sys.argv = sys.argv[:1]
try:
    import appMain  # noqa: F401
except ImportError:
    # without the GUI packages the tests that need appMain fail on their own
    pass
finally:
    sys.argv = _saved_argv
