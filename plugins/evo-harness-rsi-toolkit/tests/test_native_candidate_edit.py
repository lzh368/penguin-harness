"""Replay original training-only draft edits; never reclassify old runs."""
import json
from pathlib import Path
import unittest
import test_solver_reflection as fixtures

evo = fixtures.evo

class NativeCandidateEdits(unittest.TestCase):
    def test_exact_real_010_and_018_edits_and_legacy_denial(self):
        cases=json.loads((Path(__file__).parent/'fixtures/native-candidate-edits.json').read_text())
        for case in cases:
            args=case['arguments']; path=args['file_path']; task=path.split('/')[-2]
            training={'private_learning':'/logs/agent/evo-harness','attempt_id':'attempt-1',
                      'answer_dir':'/app/answers','feedback_dir':'/logs/agent/evo-harness/feedback','guard_version':2,'train_ids':[task],'config_sha256':'a'*64,'reflection_mode':'solver-session'}
            call={'name':'edit_file','arguments':json.dumps(args)}
            original=case['draft_excerpt']
            with self.subTest(group=case['group']):
                with self.assertRaisesRegex(ValueError,'declared new protocol'):
                    evo.reflection_action(call,training,task,drafts={path:original})
                result=evo.reflection_action(call,{**training,'candidate_edit':'native-draft-v1'},task,drafts={path:original})
                self.assertEqual(result['content'],original.replace(args['old_string'],args['new_string'],1))
                self.assertEqual(json.loads(result['content'])[0]['task_id'],task)
                self.assertEqual(case['original_verdict'],'reflection_boundary_violation; recoverable=false; retained')

    def fixture(self):
        f=fixtures.SolverReflectionTests('test_success_observes_feedback_without_forced_reflection')
        f.protocol_options={'candidate_edit':'native-draft-v1'};f.setUp();self.addCleanup(f.doCleanups);return f

    def test_native_edit_seals_and_captures_original_history(self):
        f=self.fixture();f.read_feedback();f.candidate_write()
        old=f.candidate['lesson'];new=old+' Keep the original authorized scope.'
        text=f.output.read_text().replace(old,new,1);f.output.write_text(text)
        f.tool('edit_file',{'file_path':str(f.output),'old_string':old,'new_string':new},'Successfully edited')
        self.assertTrue(f.seal()['complete']);f.finish()
        context=evo.read(f.capture()['path']);evo.validate_contexts([context],[f.task])

    def test_candidate_structure_still_validated_after_edit(self):
        f=self.fixture();f.read_feedback();f.candidate_write()
        old='"lesson"';new='"unexpected"';f.output.write_text(f.output.read_text().replace(old,new))
        f.tool('edit_file',{'file_path':str(f.output),'old_string':old,'new_string':new},'Successfully edited')
        result=f.seal();self.assertFalse(result['complete']);self.assertTrue(result['recoverable'])

    def test_file_ownership_prior_write_and_unique_match(self):
        f=self.fixture();training={**f.training,'candidate_edit':'native-draft-v1'}
        args={'file_path':str(f.output),'old_string':'same','new_string':'new'}
        for changes,drafts in [({'file_path':str(f.answer)},{}),({},{}),({}, {str(f.output):'same same'})]:
            with self.subTest(changes=changes,drafts=drafts),self.assertRaises(ValueError):
                evo.reflection_action({'name':'edit_file','arguments':json.dumps({**args,**changes})},training,f.task,drafts=drafts)

if __name__=='__main__':unittest.main()
