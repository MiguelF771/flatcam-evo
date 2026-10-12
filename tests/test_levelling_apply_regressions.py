"""S5 apply snapshot/freshness regressions; production workers run on demand."""
import unittest
from copy import deepcopy
from unittest.mock import patch

from test_levelling_tool import (
    FakeNewCNCJob, make_apply_target, make_storage_grid, make_tool,
)


class TestApplyInputFreshness(unittest.TestCase):
    def setUp(self):
        self.target = make_apply_target({
            1: 'G21\nG90\nG1 X0 Y0 Z-0.1000\n',
            2: 'X10 Y0\n',
        })
        self.tool = make_tool(storage=make_storage_grid([0, 10], [0, 10]))
        self.tool._al_target_name = 'job1'
        self.tool._al_target_obj = self.target
        self.tool.app.collection.get_by_name.return_value = self.target
        self.tool.ui.al_method_radio.get_value.return_value = 'b'
        self.outputs = []

        def new_object(kind, name, initializer):
            obj = FakeNewCNCJob()
            if initializer(obj, self.tool.app) == 'fail':
                return 'fail'
            self.outputs.append(obj)
            return 'success'

        self.tool.app.app_obj.new_object.side_effect = new_object

    def schedule(self):
        self.tool.apply_autolevel(self.target)
        self.tool.app.worker_task.emit.assert_called_once()
        self.assertTrue(self.tool._al_apply_running)
        return self.tool.app.worker_task.emit.call_args.args[0]['fcn']

    def assert_original_map_or_refusal(self):
        for obj in self.outputs:
            self.assertIn('Z0.0000', obj.tools[1]['gcode'])
            self.assertIn('Z0.1000', obj.tools[2]['gcode'])
        self.assertFalse(self.tool._al_apply_running)

    def test_delayed_worker_never_reads_in_place_mutated_heights(self):
        worker = self.schedule()
        for value in self.tool.al_voronoi_geo_storage.values():
            value['height'] = 5.0
        worker()
        self.assert_original_map_or_refusal()

    def test_delayed_worker_never_reads_replaced_storage(self):
        worker = self.schedule()
        replacement = make_storage_grid([0, 10], [0, 10])
        for value in replacement.values():
            value['height'] = 5.0
        self.tool.al_voronoi_geo_storage = replacement
        worker()
        self.assert_original_map_or_refusal()

    def test_replacement_generation_rejects_delayed_apply(self):
        worker = self.schedule()
        self.tool._al_capture_context(replacement=True)
        self.tool.al_heights_valid = True
        worker()
        self.tool.app.app_obj.new_object.assert_not_called()
        self.assertFalse(self.tool._al_apply_running)

    def test_captured_map_units_ignore_mutable_tool_units(self):
        worker = self.schedule()
        self.tool.units = 'IN'
        worker()
        self.assertEqual(len(self.outputs), 1)
        self.assert_original_map_or_refusal()

    def test_changed_project_units_reject_delayed_apply(self):
        worker = self.schedule()
        self.tool.app.app_units = 'IN'
        worker()
        self.tool.app.app_obj.new_object.assert_not_called()
        self.assertFalse(self.tool._al_apply_running)

    def test_same_name_target_replacement_rejects_delayed_apply(self):
        worker = self.schedule()
        replacement = make_apply_target({1: 'G1 X0 Y0 Z-0.1000\n'})
        self.tool.app.collection.get_by_name.return_value = replacement
        worker()
        self.tool.app.app_obj.new_object.assert_not_called()
        self.assertFalse(self.tool._al_apply_running)

    def test_target_owner_change_rejects_delayed_apply(self):
        worker = self.schedule()
        self.tool._al_target_name = 'other'
        self.tool._al_target_obj = make_apply_target({1: 'G1 Z-0.1000\n'})
        worker()
        self.tool.app.app_obj.new_object.assert_not_called()
        self.assertFalse(self.tool._al_apply_running)

    def test_mutation_during_real_generation_rejected_before_factory(self):
        worker = self.schedule()
        original_line = self.tool.autolevell_gcode_line
        mutated = False

        def level_line(line, state):
            nonlocal mutated
            result = original_line(line, state)
            if not mutated:
                mutated = True
                self.tool._al_capture_context(replacement=True)
                self.tool.al_heights_valid = True
            return result

        with patch.object(self.tool, 'autolevell_gcode_line', side_effect=level_line):
            worker()
        self.assertTrue(mutated)
        self.tool.app.app_obj.new_object.assert_not_called()
        self.assertFalse(self.tool._al_apply_running)

    def test_delayed_initializer_rejects_same_name_target_replacement(self):
        worker = self.schedule()
        initializers = []
        self.tool.app.app_obj.new_object.side_effect = (
            lambda kind, name, initializer: initializers.append(initializer)
        )
        worker()
        self.assertEqual(len(initializers), 1)
        self.tool.app.collection.get_by_name.return_value = make_apply_target({1: ''})
        obj = FakeNewCNCJob()
        result = initializers[0](obj, self.tool.app)
        self.assertEqual(result, 'fail')
        self.assertFalse(obj.geometry_created)


class TestApplyCompatibility(unittest.TestCase):
    # Keep compatibility checks separate from the expected RED regressions.
    setUp = TestApplyInputFreshness.setUp
    schedule = TestApplyInputFreshness.schedule
    assert_original_map_or_refusal = TestApplyInputFreshness.assert_original_map_or_refusal

    def test_unchanged_apply_preserves_source_and_per_tool_modal_state(self):
        source = deepcopy(self.target.tools)
        worker = self.schedule()
        worker()
        self.assertEqual(len(self.outputs), 1)
        self.assert_original_map_or_refusal()
        self.assertEqual(self.target.tools, source)
        obj = self.outputs[0]
        self.assertEqual(obj.units, self.target.units)
        self.assertEqual(obj.used_tools, self.target.used_tools)
        self.assertTrue(obj.geometry_created)
        self.assertEqual(obj.gcode, ''.join(t['gcode'] for t in obj.tools.values()))

    def test_public_direct_two_argument_call_uses_real_interpolation(self):
        source = deepcopy(self.target.tools)
        result = self.tool.autolevell_gcode(self.target, 'b')
        self.assertIn('Z0.0000', result[1])
        self.assertIn('Z0.1000', result[2])
        self.assertEqual(self.target.tools, source)


class TestDirectMapUnits(unittest.TestCase):
    setUp = TestApplyInputFreshness.setUp

    def test_direct_call_rejects_mm_map_for_in_target_despite_tool_units(self):
        self.assertEqual(self.tool._al_data_units, 'MM')
        self.tool.units = 'IN'
        self.target.units = 'IN'
        self.target.tools[1]['gcode'] = self.target.tools[1]['gcode'].replace('G21', 'G20')
        source = deepcopy(self.target.tools)

        result = self.tool.autolevell_gcode(self.target, 'b')

        self.assertIsNone(result)
        self.assertEqual(self.target.tools, source)
        self.assertTrue(any('[ERROR_NOTCL]' in call.args[0]
                            for call in self.tool.app.inform.emit.call_args_list))

    def test_direct_call_accepts_mm_map_for_mm_target_despite_tool_units(self):
        self.assertEqual(self.tool._al_data_units, 'MM')
        self.tool.units = 'IN'
        source = deepcopy(self.target.tools)

        result = self.tool.autolevell_gcode(self.target, 'b')

        self.assertIsNotNone(result)
        self.assertIn('Z0.0000', result[1])
        self.assertIn('Z0.1000', result[2])
        self.assertEqual(self.target.tools, source)
        self.assertEqual(self.tool.units, 'IN')
        self.assertEqual(self.tool._al_data_units, 'MM')

    def test_direct_call_rejects_unknown_map_units(self):
        self.tool._al_data_units = None
        source = deepcopy(self.target.tools)

        result = self.tool.autolevell_gcode(self.target, 'b')

        self.assertIsNone(result)
        self.assertEqual(self.target.tools, source)
        self.assertIsNone(self.tool._al_data_units)
        self.assertTrue(any('[ERROR_NOTCL]' in call.args[0]
                            for call in self.tool.app.inform.emit.call_args_list))


if __name__ == '__main__':
    unittest.main()
