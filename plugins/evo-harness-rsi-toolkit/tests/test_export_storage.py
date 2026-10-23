"""Frozen storage mode across preview/export/seal, not a test of score gain."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import test_control

control = test_control.control
evo = control.evo


class ExportStorageTests(unittest.TestCase):
    def setUp(self):
        self.f = test_control.ControlTests('test_full_sequential_batch_same_solver_reflections_compiles_exports_and_seals')
        self.f.protocol_options = {'general_storage': 'description-only'}
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.patch = patch.object(evo, '__file__', str(self.f.skill/'scripts/evo.py'))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_full_batch_seal_inherits_storage_and_keeps_general_body_private(self):
        self.f.test_full_sequential_batch_same_solver_reflections_compiles_exports_and_seals()
        export = evo.read(self.f.private/'training-export.json')
        self.assertEqual(export['general_storage'], 'description-only')
        request = evo.read(self.f.private/'requests/export.json')
        self.assertEqual(request['general_storage'], 'description-only')
        package = self.f.workspace/'submission/skill'
        manifest = evo.parse_reader((package/'SKILL.md').read_bytes())
        general = [entry for entry in manifest['entries'] if entry['level'] == 'general']
        self.assertTrue(general)
        for entry in general:
            self.assertEqual(entry['body_storage'], 'description-only')
            self.assertNotIn(b'Before submission, compare', (package/entry['path']).read_bytes())
        contract = evo.role_contract(self.f.profile, 'evolver')
        self.assertEqual(contract['validation']['general_storage'], 'description-only')

    def test_conflicting_mode_publishes_nothing_and_can_be_corrected(self):
        entry = {'id':'general','level':'general','description':'Read the supplied field definition before mapping.',
                 'trigger':'A field is requested.','scope':'Synthetic only.','body':'PRIVATE BODY'}
        library = self.f.root/'compiled.json';library.write_text(json.dumps([entry]))
        out = self.f.root/'fixed-export'
        req = {'library':str(library),'limits':{'general':5,'topic':5},'max_bytes':16000,'output':str(out)}
        with self.assertRaises(evo.ExportPolicyError):
            evo.export_skill({**req,'general_storage':'full'})
        self.assertFalse(out.exists())
        evo.export_skill(req)
        self.assertNotIn(b'PRIVATE BODY', b''.join(p.read_bytes()for p in out.rglob('*')if p.is_file()))
        self.assertIn('PRIVATE BODY', library.read_text())

    def test_empty_package_and_reader_identical_in_both_storage_modes(self):
        self.assertEqual(evo.render_package([],16000,general_storage='full'),
                         evo.render_package([],16000,general_storage='description-only'))
        self.assertEqual(evo.READER, test_control.control.evo.READER)


if __name__ == '__main__':
    unittest.main()
