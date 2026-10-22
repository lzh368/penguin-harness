"""018 preparation replay and native read plans; synthetic content, zero models."""
import copy,json,unittest
import test_evo
import test_full_skill
evo=test_evo.evo
class ReadPlanTests(unittest.TestCase):
 def setUp(self):
  self.slot='/state/skills/evo-harness-learned';self.workspace='/app';self.ins=evo.inspection
 def call(self,cmd):return {'name':'exec_command','arguments':json.dumps({'cmd':cmd})}
 def inspect(self,cmd,**kw):return self.ins._readonly_commands(self.call(cmd),self.workspace,self.slot,**kw)
 def test_original018_preparation_commands_new_protocol_only(self):
  self.inspect('cd /app && cat input/prompt.txt && echo "=== FILES ===" && find input/payloads -type f | sort && echo "=== ENV ===" && cat environment_access.md')
  self.inspect('ls -R /state/skills | head -60; echo ---; cat /state/AGENTS.md')
  self.inspect('cd input/payloads && for f in *.json; do echo "=== $f ==="; cat "$f"; echo; done')
 def test_business_loop_and_edited_public_loop_still_rejected(self):
  bad=['cd /app && for e in jurisdictions cases; do curl -s "http://127.0.0.1:9018/api/$e"; done',
       'cd input/payloads && for f in *.json; do echo "=== $f ==="; cat "$f"; echo; curl http://service; done',
       'cd /state && cat private','cd /app; cat input/prompt.txt','cd /app && cd input && cat prompt.txt',
       'cat /state/training.json','cat /state/skills/other/SKILL.md',
       'cd input/* && for f in *.json; do echo "=== $f ==="; cat "$f"; echo; done']
  for cmd in bad:
   with self.subTest(cmd=cmd),self.assertRaises(ValueError):self.inspect(cmd)
  with self.assertRaises(ValueError):self.inspect('cd input/payloads && for f in *.json; do echo "=== $f ==="; cat "$f"; echo; done',public_files={'/app/input/payloads/one.json'})
 def test_cd_resolves_relative_skill_without_any_business(self):
  raw=evo.render_skill([],16000);cmd='cd /app && cat '+self.slot+'/SKILL.md'
  self.assertTrue(self.ins.complete_file_stdout(self.call(cmd),raw.decode(),raw,self.slot+'/SKILL.md','/app',self.slot))
  self.assertFalse(self.ins.complete_file_stdout(self.call('cat /state/AGENTS.md'),'native instructions',raw,self.slot+'/SKILL.md','/app',self.slot))
 def test_actual_reader_prefers_native_plan_and_separates_business_from_observation(self):
  raw=evo.LEGACY_READER
  self.assertLess(raw.index('Prefer this native read plan'),raw.index('cat of one exact required file'))
  self.assertIn('business actions require a later tool call\nafter the complete output is observed',raw)
  self.assertIn('deterministic install-context',evo.READER)
  self.assertIn('No selector, extra file read',evo.READER)
  self.assertEqual(evo.parse_reader(evo.render_skill([],16000))['read_calls'],[])
 def test_complete_native_topic_page_plan_excludes_general_bodies(self):
  f=test_full_skill.FullSkillTests('runTest');f.setUp();self.addCleanup(f.doCleanups);entries=copy.deepcopy(f.entries[:1]+f.entries[-2:]);entries[-1]['body']='row\n'*1100
  files=evo.render_package(entries,16000);reader=evo.parse_reader(files['SKILL.md']);calls=reader['read_calls'];self.assertTrue(calls)
  expected=evo.context_read_calls(reader['entries'],files);self.assertEqual(calls,expected)
  body_paths={evo.GDPEVO_STATE+'/'+e['state_path']for e in reader['entries']if e['load_mode']=='body'}
  self.assertEqual({c['file_path']for c in calls},body_paths);self.assertTrue(any(c['offset']==1001 for c in calls));self.assertTrue(all(c['limit']<=1000 for c in calls))
  for e in reader['entries']:
   if e['level']=='general':self.assertNotIn(evo.GDPEVO_STATE+'/'+e['state_path'],body_paths)
  manifest={**reader,'read_calls':calls[:-1]};broken=dict(files);broken['SKILL.md']=evo.READER.encode()+evo.FULL_SKILL_OPEN.encode()+evo.encoded(manifest)+evo.FULL_SKILL_CLOSE.encode()
  with self.assertRaisesRegex(ValueError,'read plan'):evo.validate_package_files(broken)
  self.assertEqual(evo.parse_reader(evo.render_skill([],16000))['read_calls'],[])
if __name__=='__main__':unittest.main()
