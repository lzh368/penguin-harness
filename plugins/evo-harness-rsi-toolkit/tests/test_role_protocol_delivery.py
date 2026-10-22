"""Real role preparation and immutable native calls; synthetic public facts only."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_control

control = test_control.control


class RoleProtocolDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_control.ControlTests('test_first_solver_has_complete_role_and_canonical_request_but_does_not_dispatch')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def solver(self):
        prepared = self.fixture.request(op='role-request', role='solver', task_id='train_001')
        return prepared, control.evo.native_prompt(prepared['call'])

    def test_original_prompt_supplies_complete_candidate_contract_before_role_body(self):
        prepared, prompt = self.solver()
        contracts = [v for v in control.evo.json_values(prompt)
                     if isinstance(v, dict) and v.get('schema') == 'evo-candidate-contract-v1']
        self.assertEqual(len(contracts), 1)
        contract = contracts[0]
        grant = next(v['evo_task'] for v in control.evo.json_values(prompt)
                     if isinstance(v, dict) and 'evo_task' in v)
        self.assertEqual(contract, control.evo.candidate_contract('train_001', 'attempt-1', grant['inputs']))
        self.assertEqual([ref['path'] for ref in contract['input_refs']], grant['inputs'])
        for ref in contract['input_refs']:
            self.assertEqual(ref['sha256'], hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest())
        self.assertLess(prompt.index('evo-candidate-contract-v1'), prompt.index('# Native learning roles'))
        self.assertEqual(prompt.count(control.role_text('solver')), 1)
        self.assertEqual(contract['task_id'], 'train_001')
        self.assertFalse(prepared['dispatched'])
        self.assertEqual(prepared['call']['thinking_level'], self.fixture.runtime['thinking_level'])

    def test_initial_prompt_has_fixed_seal_and_no_need_to_inspect_implementation(self):
        _, prompt = self.solver()
        seals = [v for v in control.evo.json_values(prompt) if isinstance(v, dict)
                 and v == {'op': 'seal-role-output', 'role': 'reflector', 'task_id': 'train_001'}]
        self.assertTrue(seals)
        self.assertIn('Post-judge tools are only bound feedback read-material', prompt)
        self.assertIn('write_file', prompt)
        self.assertIn('answer/feedback evidence without extra identifiers', prompt)
        self.assertIn('Before judge, ordinary preparation JSON may use', prompt)
        self.assertIn('use fresh files in that directory only for bound feedback reads', prompt)
        self.assertIn('candidate input_refs identify those inputs; they are not observations', prompt)
        self.assertIn('A draft/receipt repair changes no answer', prompt)
        self.assertIn('In-session reflection', prompt)
        self.assertNotIn('## Evolver', prompt)

    def test_preparation_returns_one_immutable_native_call_including_description(self):
        prepared, prompt = self.solver()
        ticket = control.evo.read(prepared['ticket'])
        self.assertEqual(ticket['call'], prepared['call'])
        self.assertEqual(control.evo.native_prompt(ticket['call']), prompt)
        self.assertEqual(prepared['call']['description'], 'Evo-Harness solver train_001')
        repeated, repeated_prompt = self.solver()
        self.assertEqual(repeated['call'], prepared['call'])
        self.assertEqual(repeated_prompt, prompt)
        self.assertEqual(self.fixture.tool('run_subagent', {**prepared['call'], 'description': 'rewritten'})['decision'], 'deny')
        self.assertFalse(list((self.fixture.private / 'dispatch').glob('*.claimed.json')))

    def test_failed_training_result_format_repair_preserves_failure_without_tools(self):
        f = self.fixture
        mode, child = f.format_fixture()
        rows = [json.loads(line) for line in child.read_text().splitlines()]
        value = {k: v for k, v in f.worker_cell.items() if k not in ('score', 'cost', 'duration_ms', 'session_id')}
        value.update(status='failed', failure_code='evaluation_failed')
        rows[-2]['payload']['text'] = '```yaml\n' + json.dumps(value) + '\n```'
        child.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        repair = f.request(op='format-request', subagent_id='subagent-12345678')
        self.assertFalse(repair['automatic_dispatch'])
        self.assertEqual(control.evo.read(repair['ticket'])['original_result'], value)
        self.assertEqual(f.tool('input_subagent', repair['call']), {})
        f.finish_format(child, repair, json.dumps(value))
        control.verify_format_repairs(mode)
        f.append_trace(child, [{'type': 'model_msg', 'payload': {'type': 'tool_call', 'name': 'exec_command',
            'arguments': '{}', 'tool_call_id': 'new-target'}}])
        with self.assertRaisesRegex(ValueError, 'issued tools'):
            control.verify_format_repairs(mode)


    def test_one_prose_wrapped_failure_record_is_a_source_not_a_final_reply(self):
        f = self.fixture
        mode, child = f.format_fixture()
        rows = [json.loads(line) for line in child.read_text().splitlines()]
        value = {k: v for k, v in f.worker_cell.items() if k not in ('score', 'cost', 'duration_ms', 'session_id')}
        value.update(status='failed', failure_code='evaluation_failed')
        prose = ('Harbor terminated (exit 5, `method_failed`), cleanup completed. The cell has no score.\n\n'
                 + '```text\n' + json.dumps(value) + '\n```\n\n'
                 + '(The block above is the protocol document itself; it carries no fences in the original reply.)')
        rows[-2]['payload']['text'] = prose
        child.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        with self.assertRaises(control.WorkerFormatError):
            control.worker_yaml(prose, mode)
        repair = f.request(op='format-request', subagent_id='subagent-12345678')
        self.assertEqual(control.evo.read(repair['ticket'])['original_result'], value)
        self.assertEqual(f.tool('input_subagent', repair['call']), {})
        f.finish_format(child, repair, json.dumps(value))
        control.verify_format_repairs(mode)
        self.assertEqual(value['status'], 'failed')
        self.assertNotIn('score', value)

    def test_format_source_cannot_select_one_of_multiple_structured_records(self):
        f = self.fixture
        mode, _ = f.format_fixture()
        document = '```yaml\n' + json.dumps(f.worker_cell) + '\n```'
        for extra in ('\nscore: 99', '\n{"score": 99}', '\n---\nscore: 99',
                      '\n' + document, '\n[99]', '\n```text\nsecond\n```'):
            with self.subTest(extra=extra), self.assertRaises(control.WorkerFormatError):
                control.worker_yaml(document + extra, mode, allow_fence=True)

    def test_control_errors_distinguish_format_protocol_and_runtime_without_raw_data(self):
        self.assertEqual(control.public_error(control.WorkerFormatError('fixed format diagnostic'))['error_code'], 'worker_format_invalid')
        self.assertEqual(control.public_error(control.ControlProtocolError('fixed protocol diagnostic'))['error_code'], 'protocol_rejected')
        self.assertEqual(control.public_error(control.ControlRuntimeError('native worker YAML parser failed'))['error_code'], 'control_runtime_failure')
        for error in (OSError('SYNTHETIC_SECRET_PATH'), KeyError('SYNTHETIC_SECRET_KEY'), RuntimeError('SYNTHETIC_RAW_TOOL_OUTPUT')):
            self.assertNotIn('SYNTHETIC_', json.dumps(control.public_error(error)))


if __name__ == '__main__':
    unittest.main()
