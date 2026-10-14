"""TclCommandJoinGerber, driven headless: check_args + execute with a mock app whose new_object calls the
initialize callback with a real, empty GerberObject (the sources are real GerberObjects parsed from a fixture)."""

from unittest.mock import Mock

import pytest

from appObjects.GerberObject import GerberObject
from tclCommands import TclCommandJoinGerber
from tclCommands.TclCommand import TclCommand
from tclCommands.TclCommandJoinGerber import TclCommandJoinGerber as JoinGerber

from test_unify_characterization import _headless_qt, app, make_gerber  # noqa: F401  (fixtures)


def run_join(app, objs_by_name, *argv):
    """Runs the command. Returns (result, created) where created = {name: GerberObject built by initialize}."""
    created = {}

    def new_object(kind, name, initialize, plot=True, **kwargs):
        assert kind == 'gerber'
        obj = GerberObject(name, app)
        initialize(obj, app)
        created[name] = obj
        return 'ok'

    app.collection.get_by_name = Mock(side_effect=lambda n: objs_by_name.get(n))
    app.app_obj.new_object = Mock(side_effect=new_object)
    cmd = JoinGerber(app)
    args, unnamed = cmd.check_args(tuple(argv))
    return cmd.execute(args, unnamed), created


def test_command_is_registered():
    assert issubclass(JoinGerber, TclCommand)
    assert 'join_gerber' in JoinGerber.aliases and 'join_gerbers' in JoinGerber.aliases
    assert TclCommandJoinGerber is not None


def test_join_two_gerbers_with_outname(app):
    a = make_gerber(app, name='a')
    b = make_gerber(app, name='b')
    b.offset((100.0, 0.0))
    bounds_a, bounds_b = a.bounds(), b.bounds()
    res, created = run_join(app, {'a': a, 'b': b}, 'a', 'b', '-outname', 'both')
    assert res is None
    assert list(created) == ['both']
    final = created['both']
    bounds_f = final.bounds()
    assert bounds_f[0] == pytest.approx(bounds_a[0]) and bounds_f[2] == pytest.approx(bounds_b[2])
    assert bounds_f[2] - bounds_f[0] > bounds_b[2] - bounds_b[0] + 99.0
    app.app_obj.new_object.assert_called_once()
    # the sources are untouched
    assert a.bounds() == bounds_a and b.bounds() == bounds_b


def test_default_outname(app):
    a, b = make_gerber(app, name='a'), make_gerber(app, name='b')
    res, created = run_join(app, {'a': a, 'b': b}, 'a', 'b')
    assert res is None and list(created) == ['joined_gerber']


def test_result_is_independent_of_the_sources(app):
    a, b = make_gerber(app, name='a'), make_gerber(app, name='b')
    res, created = run_join(app, {'a': a, 'b': b}, 'a', 'b', '-outname', 'both')
    final = created['both']
    bounds_a = a.bounds()
    final.offset((50.0, 0.0))
    assert a.bounds() == bounds_a and b.bounds() == bounds_a
    assert final.bounds()[0] == pytest.approx(bounds_a[0] + 50.0)


@pytest.mark.parametrize('argv', [(), ('a',)])
def test_fails_with_fewer_than_two_objects(app, argv):
    a = make_gerber(app, name='a')
    res, created = run_join(app, {'a': a}, *argv)
    assert res == 'fail' and not created
    app.app_obj.new_object.assert_not_called()


def test_fails_with_an_unknown_object(app):
    a = make_gerber(app, name='a')
    res, created = run_join(app, {'a': a}, 'a', 'nope')
    assert res == 'fail' and not created


def test_fails_with_a_non_gerber_object(app):
    a = make_gerber(app, name='a')
    res, created = run_join(app, {'a': a, 'x': Mock()}, 'a', 'x')
    assert res == 'fail' and not created
