"""Frozen test repetitions use distinct slots without repeating training."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_control import control


class RepeatedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = {'provider': 'fixture', 'model_id': 'no-model', 'thinking_level': 'xhigh'}
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'runtime': self.runtime, 'test_repeats': 3,
            'benchmark_id': 'train', 'test_benchmark_id': 'test',
            'test_agent_id': 'target', 'trainer_agent_id': 'trainer'}))
        self.mode = {'mode': 'controller', 'config': str(self.config), 'session_id': 'controller-fixture'}
        self.registration = {'training': {'case_ids': ['train-case']}, 'test': {'case_ids': ['a', 'b']}}

    def call(self, case, run, phase='h0'):
        training = phase == 'training'
        text = '\n'.join([f'case_id: {case}', f'run: {run}',
            f'benchmark_id: {"train" if training else "test"}',
            f'test_agent_id: {"trainer" if training else "target"}',
            'expected_version: 1', 'training_protocol_version: 1',
            *[f'{k}: {v}' for k, v in self.runtime.items()]])
        return dict(self.runtime, prompt=text, run_in_background=True)

    def request(self, case, run, phase='h0'):
        frozen = {'test_case_ids': ['a', 'b'], 'h0': {'version': 1}}
        with patch.object(control, 'registered_benchmarks', return_value=self.registration), \
                patch.object(control, 'freeze', return_value=frozen):
            return control.worker_request({'phase': phase, 'call': self.call(case, run, phase)}, self.root, self.mode)

    def test_three_rounds_have_distinct_immutable_worker_slots(self):
        tickets = [self.request('a', run) for run in (1, 2, 3)]
        self.assertEqual([value['slot'] for value in tickets], ['h0/a/1', 'h0/a/2', 'h0/a/3'])
        self.assertEqual(len({value['operation_id'] for value in tickets}), 3)
        self.assertEqual(self.request('a', 2), tickets[1])
        for run in (0, 4, '01'):
            with self.subTest(run=run), self.assertRaises(ValueError):
                self.request('a', run)

    def test_test_repetitions_do_not_authorize_a_second_training_execution(self):
        self.assertEqual(self.request('train-case', 1, 'training')['slot'], 'training/train-case/1')
        with self.assertRaises(ValueError):
            self.request('train-case', 2, 'training')

    def validation(self, identities):
        tickets = [{'slot': f'h0/{case}/{run}', 'identity': {'case_id': case, 'run': run}} for case, run in identities]
        with patch.object(control, 'controller_trace', return_value=({}, self.root)), \
                patch.object(control, 'registered_benchmarks', return_value=self.registration), \
                patch.object(control, 'phase_tickets', return_value=tickets), \
                patch.object(control, 'worker_identity', side_effect=lambda ticket: ticket['identity']), \
                patch.object(control, 'native_worker', return_value={'handle': 'native', 'session_id': 'native'}), \
                patch.object(control, 'worker_result', side_effect=lambda *args: ({'status': 'ok', **args[-1]['identity']}, {})):
            return control.validate_workers(self.mode, 'h0')

    def test_phase_completeness_requires_each_case_in_all_three_rounds(self):
        full = [(case, run) for case in ('a', 'b') for run in (1, 2, 3)]
        self.assertTrue(self.validation(full)['complete'])
        missing = self.validation(full[:-1])
        self.assertFalse(missing['complete'])
        self.assertEqual(missing['missing'], ['b/run3'])
        self.assertFalse(self.validation(full[:-1] + [full[0]])['complete'])


if __name__ == '__main__':
    unittest.main()
