"""Every project object kind can be constructed.

FlatCAMObj.__init__ forwarded the 'app' keyword to the next class in the MRO. For ScriptObject and DocumentObject
no camlib base follows QObject, so PyQt6 raised "TypeError: 'app' is an unknown keyword argument" and the app could
not create a Script or a Document object.
"""

import pytest

from appObjects.CNCJobObject import CNCJobObject
from appObjects.DocumentObject import DocumentObject
from appObjects.ExcellonObject import ExcellonObject
from appObjects.GeometryObject import GeometryObject
from appObjects.GerberObject import GerberObject
from appObjects.ScriptObject import ScriptObject
from test_unify_regressions_0b import make_app, no_shape_collection  # noqa: F401  (fixture)

KINDS = {
    GerberObject: 'gerber', ExcellonObject: 'excellon', GeometryObject: 'geometry', CNCJobObject: 'cncjob',
    ScriptObject: 'script', DocumentObject: 'document',
}


@pytest.mark.parametrize('cls', list(KINDS), ids=lambda c: c.__name__)
def test_object_kind_can_be_constructed(cls, no_shape_collection):  # noqa: F811
    app = make_app()
    obj = cls('obj', app)
    assert obj.kind == KINDS[cls]
    assert obj.obj_options['name'] == 'obj'
    assert obj.app is app
