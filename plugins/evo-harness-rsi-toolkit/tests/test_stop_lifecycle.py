"""Consume authoritative native stop snapshots, without model/API or dispatcher calls."""
import json
import unittest
import test_control as fixtures

control = fixtures.control


class StopLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ControlTests('test_first_solver_has_complete_role_and_canonical_request_but_does_not_dispatch')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.lifecycle = {'schema': 1, 'subagents': [{'session_id': 'native-child-session',
            'subagent_id': 'subagent-opaque', 'running': True}], 'commands': [], 'notices': [], 'cutoff': False, 'aborted': False}
        self.goal = self.f.scratch / 'GOAL.json'
        self.f.write(self.goal, {'objective': 'synthetic native background work', 'status': 'active',
                                'ended': False, 'budget': -1, 'round': 1, 'tokens_used': 0})
        self.acceptance = self.f.scratch / 'evo-acceptance.json'
        self.f.write(self.acceptance, {'complete': False, 'status': 'synthetic_existing_record'})

    def stop(self, lifecycle=None):
        return self.f.invoke('stop', lifecycle=self.lifecycle if lifecycle is None else lifecycle)

    def preserved(self):
        paths = [self.goal, self.acceptance, self.f.trace]
        return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}

    def test_running_native_child_waits_without_writing_goal_acceptance_or_trace(self):
        before = self.preserved()
        result = self.stop()
        self.assertEqual(result['decision'], 'wait')
        self.assertEqual(result['output'], {'awaiting_background': True, 'running_workers': 1,
                                          'running_commands': 0, 'queued_notices': 0})
        self.assertEqual(self.preserved(), before)
        self.assertNotIn('complete', result['output'])
        # Waiting never replaces the existing dispatch guard.
        self.assertEqual(self.f.tool('run_subagent', {'prompt': 'unregistered'})['decision'], 'deny')

    def test_background_preparation_command_also_waits(self):
        self.lifecycle['subagents'] = []
        self.lifecycle['commands'] = [{'process_id': 'proc-native-preparation', 'running': True}]
        before = self.preserved()
        result = self.stop()
        self.assertEqual(result['decision'], 'wait')
        self.assertEqual(result['output']['running_commands'], 1)
        self.assertEqual(self.preserved(), before)
        self.assertEqual(self.f.tool('exec_command', {'cmd': 'harbor run forbidden'})['decision'], 'deny')

    def test_queued_notices_wait_without_live_registry_then_strictly_stop_after_delivery(self):
        self.lifecycle['subagents'] = []
        for kind in ('subagent', 'command'):
            for status in ('completed', 'failed', 'stopped'):
                with self.subTest(kind=kind, status=status):
                    self.lifecycle['notices'] = [{'kind': kind, 'id': 'native-pending-notice', 'status': status}]
                    before = self.preserved()
                    self.assertEqual(self.stop()['decision'], 'wait')
                    self.assertEqual(self.preserved(), before)
        self.lifecycle['notices'] = []
        result = self.stop()
        self.assertEqual(result['output']['status'], 'method_failed')
        self.assertNotEqual(result['decision'], 'wait')

    def test_cutoff_abort_missing_snapshot_and_foreground_child_never_wait(self):
        variants = [{**self.lifecycle, 'cutoff': True}, {**self.lifecycle, 'aborted': True},
                    {**self.lifecycle, 'subagents': [{'session_id': 'native-foreground', 'subagent_id': None, 'running': True}]},
                    {**self.lifecycle, 'subagents': [], 'commands': [{'process_id': 'proc-ended', 'running': False}]},
                    {**self.lifecycle, 'subagents': [{'session_id': 'native-idle', 'subagent_id': 'subagent-idle', 'running': False}]}]
        for lifecycle in variants:
            with self.subTest(lifecycle=lifecycle):
                self.assertEqual(self.stop(lifecycle)['output']['status'], 'method_failed')
        self.assertEqual(self.f.invoke('stop')['output']['status'], 'method_failed')

    def test_terminal_goal_is_never_reopened_or_treated_as_waiting(self):
        for status, ended in (('blocked', True), ('complete', True), ('complete', False), ('wrapping_up', False)):
            self.f.write(self.goal, {'status': status, 'ended': ended, 'budget': -1})
            original = self.goal.read_bytes()
            with self.subTest(status=status, ended=ended):
                self.assertEqual(self.stop()['output']['status'], 'method_failed')
                if ended:
                    self.assertEqual(self.goal.read_bytes(), original)

    def test_positive_goal_token_budget_retains_native_goal_flow_not_method_failure(self):
        for status in ('active', 'wrapping_up'):
            self.f.write(self.goal, {'status': status, 'ended': False, 'budget': 10, 'tokens_used': 9, 'round': 1})
            before = self.preserved()
            result = self.stop()
            self.assertNotIn('decision', result)
            self.assertTrue(result['output']['native_goal_budget_flow'])
            self.assertEqual(self.preserved(), before)

    def test_frozen_profile_change_cannot_be_hidden_by_pending_work(self):
        profile = self.f.skill / 'training.json'
        value = control.evo.read(profile)
        value['runtime']['model_id'] = 'changed'
        self.f.write(profile, value)
        self.assertEqual(self.stop()['output']['status'], 'method_failed')

    def test_malformed_native_snapshot_cannot_claim_wait(self):
        for changed in ({**self.lifecycle, 'schema': True}, {**self.lifecycle, 'cutoff': 'false'},
                        {**self.lifecycle, 'subagents': [{'session_id': 'x', 'subagent_id': 'x', 'running': 'true'}]},
                        {**self.lifecycle, 'notices': [{'kind': 'subagent', 'id': 'x', 'status': 'invented'}]}):
            with self.subTest(changed=changed):
                self.assertEqual(self.stop(changed)['output']['status'], 'method_failed')

    def test_training_root_without_goal_uses_same_native_wait(self):
        self.goal.unlink()
        self.assertEqual(self.stop()['decision'], 'wait')
        self.assertFalse(self.goal.exists())


if __name__ == '__main__':
    unittest.main()
