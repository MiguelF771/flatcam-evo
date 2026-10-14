# ###########################################################
# FlatCAM EVO: dispatch metadata lookup for objects          #
# ###########################################################

"""
Reads the per-kind dispatch metadata (class attributes of the object classes, for example ``status_color``) from an
object. Stand-ins that are not FlatCAMObj instances (test doubles, SimpleNamespace) only carry a ``kind`` string;
for them the value comes from the class registered for that kind, and unknown kinds get the FlatCAMObj default.
"""

_MISSING = object()


def _kind_classes():
    from appObjects.AppObjectTemplate import FlatCAMObj
    from appObjects.CNCJobObject import CNCJobObject
    from appObjects.DocumentObject import DocumentObject
    from appObjects.ExcellonObject import ExcellonObject
    from appObjects.GeometryObject import GeometryObject
    from appObjects.GerberObject import GerberObject
    from appObjects.ScriptObject import ScriptObject

    return FlatCAMObj, {
        "gerber": GerberObject,
        "excellon": ExcellonObject,
        "cncjob": CNCJobObject,
        "geometry": GeometryObject,
        "script": ScriptObject,
        "document": DocumentObject,
    }


def dispatch_attr(obj, name):
    """
    :param obj:     an App object or a stand-in with a ``kind`` attribute
    :param name:    the name of the dispatch attribute (a FlatCAMObj class attribute)
    :return:        the value of the attribute for the kind of the object
    """
    value = getattr(obj, name, _MISSING)
    if value is not _MISSING:
        return value
    base, classes = _kind_classes()
    return getattr(classes.get(getattr(obj, "kind", None), base), name)
