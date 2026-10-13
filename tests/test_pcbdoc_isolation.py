import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from shapely.geometry import Point, box
from shapely.ops import unary_union

from appPlugins.ToolIsolation import ToolIsolation


class TestPcbDocIsolation(unittest.TestCase):
    def isolate(self, entries):
        solid = entries[0]['solid']
        envelope = solid.buffer(0.1).exterior
        app = SimpleNamespace(options={'gerber_circle_steps': 64}, inform=Mock(), app_obj=Mock())
        tool = SimpleNamespace(app=app, decimals=4, validation_status=True,
                               generate_envelope=Mock(return_value=[envelope]))
        source = SimpleNamespace(obj_options={'name': 'native'}, solid_geometry=[solid],
                                 tools={0: {'type': 'REG', 'size': 0, 'geometry': entries}})
        storage = {1: {'tooldia': 0.2, 'data': {
            'tools_iso_isotype': 'full', 'tools_iso_passes': 1,
            'tools_iso_overlap': 0, 'tools_iso_milling_type': 'cl'}}}
        created = []

        def create(kind, name, initialize, **kwargs):
            obj = SimpleNamespace(obj_options={'name': name})
            self.assertNotEqual(initialize(obj, app), 'fail')
            created.append(obj)
            return obj

        app.app_obj.new_object.side_effect = create
        ToolIsolation.isolate(
            tool, source, [1], storage, geometry=source.solid_geometry, plot=False,
            combine=False, iso_except=False, e_passes=1, area_except=False,
            area_shape='square', rest=False, sel_type=0, tip_shape='C1',
            iso_simplification=False, simplification_tol=0.001)
        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].multigeo)
        self.assertEqual(created[0].tools[1]['tooldia'], 0.2)
        return unary_union(created[0].solid_geometry), envelope, source

    def test_solid_only_region_keeps_isolation_without_fake_follow(self):
        entry = {'solid': box(0, 0, 2, 2)}
        actual, envelope, source = self.isolate([entry])
        self.assertTrue(actual.equals(envelope))
        self.assertEqual(set(source.tools[0]['geometry'][0]), {'solid'})

    def test_point_flash_still_receives_extra_pad_geometry(self):
        pad = Point(0, 0).buffer(1)
        actual, envelope, source = self.isolate([{'solid': pad, 'follow': Point(0, 0)}])
        extra = pad.buffer(0.2 / 2.0000001, 64)
        self.assertLess(actual.symmetric_difference(unary_union([envelope, extra])).area, 1e-8)
        self.assertGreater(actual.area, 0)
        self.assertIsInstance(source.tools[0]['geometry'][0]['follow'], Point)


if __name__ == '__main__':
    unittest.main()
