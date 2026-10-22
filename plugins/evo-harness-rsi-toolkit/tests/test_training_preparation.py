"""R18 preparation scopes: exact own metadata, no business before Skill reads."""
import json
from pathlib import Path
import unittest

import test_solver_skill

evo = test_solver_skill.evo


class TrainingPreparationTests(unittest.TestCase):
    def setUp(self):
        self.f = test_solver_skill.SolverSkillTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.state = self.f.f['binding']['agent_state']
        self.workspace = self.f.f['binding']['workspace']
        self.config = self.state + '/skills/evo-harness-rsi-toolkit/training.json'
        self.agents = self.state + '/AGENTS.md'

    def capture_after(self, before):
        self.f.write(self.f.rows[:2] + before + self.f.rows[2:])
        result = evo.capture(self.f.request)
        context = evo.read(result['path'])
        evo.validate_contexts([context], ['train_001'])
        self.assertEqual(context['feedback'], self.f.feedback)
        self.assertEqual(Path(self.f.f['paths']['output']).read_bytes(), self.f.feedback_bytes)

    def test_own_native_agents_and_training_config_may_precede_full_skill(self):
        before = self.f.pair('read_file', {'file_path': self.agents}, 'SYNTHETIC own native instructions', 'agents')
        before += self.f.pair('read_file', {'file_path': self.config}, 'SYNTHETIC bound training profile', 'config')
        self.capture_after(before)

    def test_same_bound_config_is_allowed_by_literal_shell_not_general_training_access(self):
        command = 'cd ' + self.state + ' && cat AGENTS.md && cat skills/evo-harness-rsi-toolkit/training.json'
        self.capture_after(self.f.pair('exec_command', {'cmd': command}, 'SYNTHETIC metadata', 'metadata'))

    def test_standalone_and_initial_cd_ls_are_harmless_preparation(self):
        before = self.f.pair('exec_command', {'cmd': 'cd ' + self.state}, '', 'standalone-cd')
        before += self.f.pair('exec_command', {'cmd': 'cd /tmp && ls -la'}, 'SYNTHETIC directory names', 'discover')
        before += self.f.pair('exec_command', {'cmd': 'cd ' + self.state + ' && ls skills'}, 'SYNTHETIC Skill names', 'skill-names')
        self.capture_after(before)

    def test_cd_can_precede_exact_full_skill_read_without_changing_material_rights(self):
        command = 'cd ' + self.state + ' && ls skills && cat skills/evo-harness-learned/SKILL.md'
        observed = self.f.pair('exec_command', {'cmd': command}, 'SYNTHETIC Skill names\n' + self.f.raw.decode(), 'cd-full-skill')
        self.f.write(self.f.rows[:2] + observed + self.f.rows[4:])
        result = evo.capture(self.f.request)
        evo.validate_contexts([evo.read(result['path'])], ['train_001'])

    def test_foreign_agents_configs_training_files_and_secrets_remain_forbidden(self):
        paths = ['/other/agent_state/AGENTS.md', '/other/agent_state/skills/evo-harness-rsi-toolkit/training.json',
                 self.state + '/skills/other/training.json', self.state + '/.vault.toml',
                 self.state + '/skills/evo-harness-rsi-toolkit/scripts/evo.py',
                 self.workspace + '/train_tasks/train_002/instruction.md']
        for path in paths:
            for tool, arguments in (('read_file', {'file_path': path}),
                                    ('exec_command', {'cmd': 'cd /tmp && cat ' + path})):
                with self.subTest(path=path, tool=tool):
                    self.f.write(self.f.rows[:2] + self.f.pair(tool, arguments, 'SYNTHETIC forbidden') + self.f.rows[2:])
                    with self.assertRaises(ValueError): evo.capture(self.f.request)
                    self.assertFalse(Path(self.f.request['output']).exists())

    def test_preparation_never_allows_business_writes_or_dynamic_shell(self):
        for command in ('cd /tmp && python3 -c pass', 'cd /tmp && touch /app/answer',
                        'cd /tmp && curl http://service/query', 'pwd && cd /tmp && ls',
                        'cd /tmp || ls', 'cd -', 'cd $HOME && ls'):
            with self.subTest(command=command):
                self.f.write(self.f.rows[:2] + self.f.pair('exec_command', {'cmd': command}) + self.f.rows[2:])
                with self.assertRaises(ValueError): evo.capture(self.f.request)
                self.assertFalse(Path(self.f.request['output']).exists())

    def test_preparation_files_are_explicit_not_implicitly_given_to_test_targets(self):
        slot = self.state + '/skills/evo-harness-learned'
        call = {'name': 'exec_command', 'arguments': json.dumps({'cmd': 'cat ' + self.config})}
        with self.assertRaises(ValueError):
            evo.inspection._readonly_commands(call, self.workspace, slot)
        self.assertEqual(evo.inspection._readonly_commands(call, self.workspace, slot,
                         preparation_files={self.config})[3], [self.config])
        for files in ({'relative/training.json'}, {self.state + '/../training.json'}, 'not-a-set'):
            with self.subTest(files=files), self.assertRaises(ValueError):
                evo.inspection._readonly_commands(call, self.workspace, slot, preparation_files=files)

    def test_fixed_public_json_loop_does_not_open_wildcards_to_training(self):
        command = 'cd /app/input/payloads && for f in *.json; do echo "=== $f ==="; cat "$f"; echo; done'
        call = {'name': 'exec_command', 'arguments': json.dumps({'cmd': command})}
        self.assertEqual(evo.inspection._readonly_commands(call, '/app', '/state/skills/evo-harness-learned')[1],
                         '/app/input/payloads')
        for changed in (command.replace('cat "$f"', 'python3 "$f"'), command.replace('/app/input/payloads', '/private')):
            with self.subTest(command=changed), self.assertRaises(ValueError):
                evo.inspection._readonly_commands({'name': 'exec_command', 'arguments': json.dumps({'cmd': changed})},
                                                   '/app', '/state/skills/evo-harness-learned')
        with self.assertRaises(ValueError):
            evo.inspection._readonly_commands(call, '/app', '/state/skills/evo-harness-learned', public_files={'/app/input/a.json'})


if __name__ == '__main__':
    unittest.main()
