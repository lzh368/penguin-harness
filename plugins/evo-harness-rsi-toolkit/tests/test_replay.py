"""Zero-model native-shaped explicit replay evidence; no HTTP/model/Harbor calls."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/evo-harness-rsi-toolkit/scripts'
sys.path.insert(0, str(SCRIPTS))
import replay
import evo
import control


def row(kind, **payload):
    return {'type': kind, 'payload': payload}


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(evo.encoded(value))
    return str(path)


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='evo-replay-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'home/project/agents/controller/agent_state'
        self.state.mkdir(parents=True)
        self.skill = self.state / 'skills/evo-harness-rsi-toolkit'
        shutil.copytree(SCRIPTS.parent, self.skill)
        for module, key, value in [(control, 'SKILL', self.skill), (control, 'TOOL', self.skill/'scripts/evo.py'),
                                   (control, 'CONTROL', self.skill/'scripts/control.py'), (replay, 'HERE', self.skill/'scripts/replay.py')]:
            p = patch.object(module, key, value); p.start(); self.addCleanup(p.stop)
        self.runtime = {'provider': 'fixture', 'model_id': 'synthetic', 'thinking_level': 'xhigh'}
        self.meta = {'session_id': 'session-parent', 'source': 'cli', 'provider': 'fixture', 'model_id': 'synthetic',
                     'agent_state': str(self.state), 'workspace': str(self.root/'workspace')}
        self.traces = self.state.parent/'traces'
        self.trace = self.traces/'session-parent_001.jsonl'
        self.write_trace(self.trace, [row('session_meta', **self.meta)])
        self.ids = [f'train_{n:03}' for n in range(1,6)]
        items=[]
        for index, task in enumerate(self.ids):
            negative = index != 1
            text='Original instruction for '+task+'\n'
            inp={'path':'/original/'+task+'/instruction.md', 'sha256':evo.digest(text.encode()), 'text':text}
            prefix=(json.dumps(row('model_msg', type='tool_call',name='business_read',arguments='{}',tool_call_id='original-'+task))+'\n')
            item={'task_id':task,'attempt_id':'attempt-1','negative':negative}
            bundle={'schema':replay.BUNDLE,**item,'inputs':[inp], 'answer':{'text':'original answer','sha256':evo.digest(b'original answer')},
                    'feedback':{'text':('negative raw feedback\n'+'x'*4500) if negative else 'success raw feedback','sha256':''},
                    'trace_prefixes':[{'source_path':'/original/'+task+'.jsonl','through_line':1,'text':prefix,'sha256':evo.digest(prefix.encode())}],
                    'feedback_boundary':{'message_index_zero_based':0}}
            bundle['feedback']['sha256']=evo.digest(bundle['feedback']['text'].encode())
            ctx={**item,'inputs':[{'source':inp['path'],'sha256':inp['sha256'],'text':text}], 'trace':{'pairs':[{'call':{'payload':{'tool_call_id':'original-'+task}}}]}}
            cp=write(self.root/'sources'/task/'context.json',ctx)
            bp=write(self.root/'sources'/task/'bundle.json',bundle)
            items.append({**item,'context_path':cp,'context_sha256':control.sha(cp),'bundle_path':bp,'bundle_sha256':control.sha(bp)})
        proof=write(self.root/'sources/proof.json',{'complete':True,'original_helper':'frozen'})
        source={'schema':replay.SOURCE,'group_id':'003','source_run_id':'original-run','source_method_version':'old',
                'source_helper_sha256':'a'*64,'validated':True,'train_ids':self.ids,'contexts':items,
                'source_validation':{'path':proof,'sha256':control.sha(proof)}}
        self.manifest=write(self.root/'sources/manifest.json',source)
        bdir=self.root/'benchmark/experiments/gdpevo/evo-harness/v1'
        bdir.mkdir(parents=True)
        for name in ['protocol.py','acceptance.py','injection.py','registration.py','offline_replay.py']:
            (bdir/name).write_text('# frozen synthetic dependency\n')
        self.config={'protocol':replay.PROTOCOL,'group':'003','paid_authorized':True,'budget_usd':10,
            'train_ids':self.ids,'test_repeats':1,'test_phase_order':['final_test'],'test_concurrency':5,
            'source_manifest':self.manifest,'source_manifest_sha256':control.sha(self.manifest),
            'runtime':self.runtime,'replay_reserves':{'controller':1,'learner':.3,'evolver':.8,'test':.4},
            'test_agent_id':'create: target','test_benchmark_id':'replay-test','limits':{'general':5,'topic':5},
            'deployment':{'benchmark_checkout':str(self.root/'benchmark'),'penguin_home':str(self.root/'home'),
                          'project_id':'project','controller_id':'controller'}}
        self.configpath=write(self.root/'config.json',self.config)
        write(self.skill/'replay.json',{'config':self.configpath,'config_sha256':control.sha(self.configpath)})
        self.scratch=self.state.parent/'scratchpad/session-parent'
        self.boot=replay.bootstrap({'trace_path':str(self.trace)},str(self.state),self.meta,self.scratch,None)
        self.mode=evo.read(self.scratch/control.MARKER)
        self.n=0

    def write_trace(self,path,rows):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))

    def append(self,path,rows):
        with path.open('a') as f:
            for r in rows:f.write(json.dumps(r)+'\n')

    def request(self, role='learner',task=None):
        return replay.prepare_role({'role':role,'task_id':task},self.meta,self.scratch,self.mode,self.config)

    def launch(self, prepared):
        self.n+=1
        cid='launch-'+str(self.n); sid='session-child-'+str(self.n); handle='subagent-child-'+str(self.n)
        with patch.object(replay,'budget_gate',return_value={}):
            replay.authorize(prepared['call'],self.meta,self.scratch,self.mode,cid)
        self.append(self.trace,[row('model_msg',type='tool_call',name='run_subagent',arguments=json.dumps(prepared['call']),tool_call_id=cid),
            row('model_msg',type='tool_call_output',tool_call_id=cid,stop_reason='completed',output='Started; subagent_id '+handle+';'),
            row('event_msg',type='subagent',session_id=sid)])
        child=self.traces/(sid+'_001.jsonl')
        self.write_trace(child,[row('session_meta',**{**self.meta,'session_id':sid,'source':'subagent'}),
            row('event_msg',type='request_begin'),row('model_msg',type='text',role='user',sender='parent_agent',text=evo.native_prompt(prepared['call']))])
        ticket=evo.read(prepared['ticket']); contract=evo.read(ticket['contract'])
        return child,sid,handle,contract,ticket

    def operation(self,child,name,args,output='ok',stop='completed',callid=None):
        callid=callid or 'call-'+str(len(child.read_text().splitlines()))
        self.append(child,[row('model_msg',type='tool_call',name=name,arguments=json.dumps(args),tool_call_id=callid),
            row('model_msg',type='tool_call_output',tool_call_id=callid,stop_reason=stop,output=output)])
        return callid

    def reads(self,child,contract,omit=None):
        for p in contract['read_plan']:
            if omit and (p['path'],p['offset'])==omit:continue
            self.operation(child,'exec_command',{'cmd':p['command']},json.dumps(replay.replay_material(evo.read(p['request_path']),Path(p['request_path']))))

    def candidate(self,task):
        return [{'id':task+'-attempt-1-c1','task_id':task,'attempt_id':'attempt-1',
                 'lesson':'Query authoritative task data, join by the stated entity key and preserve the requested granularity.',
                 'trigger':'A task requires linking records for an entity','scope_hint':'Use the task-defined key and time basis',
                 'evidence':[{'kind':'feedback','claim':'The aggregate score is partial; it does not identify a particular lost field.','epistemic':'observed'}]}]

    def write_output(self,child,contract,values):
        text=json.dumps(values,ensure_ascii=False)
        Path(contract['output']).write_text(text)
        self.operation(child,'write_file',{'file_path':contract['output'],'content':text})

    def seal(self,child,sid,contract,ticket):
        request=contract['seal_request']; callid='seal-'+str(len(child.read_text().splitlines()))
        self.append(child,[row('model_msg',type='tool_call',name='exec_command',arguments=json.dumps({'cmd':shlex.join(['python3','-B',str(replay.HERE),request])}),tool_call_id=callid)])
        with patch.dict(os.environ,{'PENGUIN_SESSION_ID':sid}):
            result=replay.seal_role(evo.read(request),Path(request))
        self.append(child,[row('model_msg',type='tool_call_output',tool_call_id=callid,stop_reason='completed',output=json.dumps(result))])
        return result

    def finish(self,child,handle):
        self.append(child,[row('model_msg',type='text',role='assistant',text='Completed replay seal.'),row('event_msg',type='request_end',status='completed')])
        self.append(self.trace,[row('model_msg',type='text',role='user',sender='harness',text='[background_task_done]\nkind: subagent\nid: '+handle+'\nstatus: completed\n[/background_task_done]')])

    def run_role(self,role,task,output):
        prepared=self.request(role,task); child,sid,handle,contract,ticket=self.launch(prepared)
        self.reads(child,contract);self.write_output(child,contract,output)
        self.assertTrue(self.seal(child,sid,contract,ticket)['complete'])
        self.finish(child,handle)
        return replay.collect_role({'role':role,'task_id':task},self.meta,self.scratch,self.mode,self.config)

    def all_learners(self):
        for task in self.ids:
            self.run_role('learner',task,[] if task==self.ids[1] else self.candidate(task))

    def test_five_fresh_learners_one_evolver_compile_and_native_provenance(self):
        self.all_learners()
        ids=[self.candidate(t)[0]['id'] for t in self.ids if t!=self.ids[1]]
        decisions=[{'op':'ADD','candidate_ids':ids,'reason':'Retain a supported linking procedure without assuming a cause from partial score.',
            'entry':{'id':'link-records','level':'topic','topic':'crm','trigger':'Task requires linking entity records','scope':'Use the supplied key, granularity and time basis',
                     'body':'Read the task-defined key. Query authorized records. Join on that key, restrict the required time window, and aggregate only at the requested record grain. Use the resulting records to answer the task; if the key is not defined, verify it before joining.'}}]
        self.run_role('evolver',None,decisions)
        learned=replay.learning(self.mode,self.config)
        self.assertEqual(learned['package']['entries'],1)
        self.assertFalse(learned['original_solver_reflection'])
        seal=write(replay.private(self.mode)/'learning.json',learned)
        result=replay.verify_learning_seal(self.config,seal)
        self.assertTrue(result['complete'])
        self.assertEqual(len({r['session_id'] for r in learned['learners']}|{learned['evolver']['session_id']}),6)

    def test_candidate_before_full_feedback_is_terminal(self):
        child,sid,_,contract,ticket=self.launch(self.request(task=self.ids[0]))
        last=contract['read_plan'][-1]
        self.reads(child,contract,omit=(last['path'],last['offset']))
        self.write_output(child,contract,self.candidate(self.ids[0]))
        with self.assertRaisesRegex(ValueError,'observed before output'):
            self.seal(child,sid,contract,ticket)

    def test_structural_draft_repair_native_write_then_seal_same_session(self):
        child,sid,handle,contract,ticket=self.launch(self.request(task=self.ids[0]))
        self.reads(child,contract)
        bad=self.candidate(self.ids[0]);del bad[0]['lesson']
        self.write_output(child,contract,bad)
        rejected=self.seal(child,sid,contract,ticket)
        self.assertEqual(rejected['code'],'replay_draft_invalid')
        self.assertTrue(rejected['recoverable'])
        self.write_output(child,contract,self.candidate(self.ids[0]))
        self.assertTrue(self.seal(child,sid,contract,ticket)['complete'])
        self.finish(child,handle)
        self.assertTrue(replay.collect_role({'role':'learner','task_id':self.ids[0]},self.meta,self.scratch,self.mode,self.config)['complete'])

    def test_foreign_output_or_business_or_judge_rejected(self):
        for name,args in [('write_file',{'file_path':'/other/task/candidates.json','content':'[]'}),
                          ('exec_command',{'cmd':'curl http://business/api/judge'}),
                          ('read_file',{'file_path':'/original/terminal-trace.jsonl'})]:
            with self.subTest(name=name):
                contract={'materials':[],'read_plan':[],'output':'/own/candidates.json','seal_request':'/own/seal.json'}
                view={'pairs':[{'call':{'payload':{'name':name,'arguments':json.dumps(args)}},
                               'observation':{'payload':{'stop_reason':'completed','output':'ok'}}}]}
                with self.assertRaises(ValueError):replay.audit_operations(view,contract)

    def test_cross_task_candidate_and_legacy_full_trace_reference_rejected(self):
        child,sid,_,contract,ticket=self.launch(self.request(task=self.ids[0]))
        self.reads(child,contract)
        value=self.candidate(self.ids[1]);self.write_output(child,contract,value)
        self.assertEqual(self.seal(child,sid,contract,ticket)['code'],'replay_draft_invalid')
        value=self.candidate(self.ids[0]);value[0]['evidence']=[{'kind':'tool','tool_call_id':'old-reflection-write','claim':'unsupported','epistemic':'observed'}]
        self.write_output(child,contract,value)
        self.assertEqual(self.seal(child,sid,contract,ticket)['code'],'replay_draft_invalid')

    def test_no_replay_or_evolver_redispatch_and_no_skipped_negative(self):
        with self.assertRaises((ValueError,OSError)):self.request('evolver')
        with self.assertRaises((ValueError,OSError)):self.request(task=self.ids[1])
        prepared=self.request(task=self.ids[0]);self.launch(prepared)
        with patch.object(replay,'budget_gate',return_value={}),self.assertRaisesRegex(ValueError,'immutable'):
            replay.authorize(prepared['call'],self.meta,self.scratch,self.mode,'different-launch')

    def test_mutated_material_and_after_seal_write_rejected(self):
        child,sid,handle,contract,ticket=self.launch(self.request(task=self.ids[0]))
        self.reads(child,contract);self.write_output(child,contract,[])
        self.assertTrue(self.seal(child,sid,contract,ticket)['complete'])
        self.write_output(child,contract,[])
        self.finish(child,handle)
        with self.assertRaisesRegex(ValueError,'after successful sealing'):
            replay.collect_role({'role':'learner','task_id':self.ids[0]},self.meta,self.scratch,self.mode,self.config)

    def test_source_mutation_rejected_and_no_test_round_two(self):
        item=evo.read(self.manifest)['contexts'][0]
        Path(item['bundle_path']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'material changed'):replay.verify_source(self.config)
        with self.assertRaisesRegex(ValueError,'one final-only'):
            replay.worker_request({'round':2,'case_id':'irrelevant'},self.meta,self.scratch,self.mode,self.config)

    def test_full_rsi_hook_is_not_selected_by_replay_without_binding(self):
        self.assertIn('NEW learning',self.boot['context'])
        self.assertIn('one final-only round',self.boot['context'])
        self.assertNotIn('three final',self.boot['context'])
        self.assertIn('not the original Solver',replay.role_guidance('learner'))
        self.assertNotIn('same Solver conversation',replay.role_guidance('learner'))

    def test_large_escaped_unicode_material_chunks_fit_native_output_and_cover_all_bytes(self):
        path=self.root/'long-line.json'
        content=json.dumps({'raw':'中文😀\\"\t'*20000,'feedback':'END_OF_FULL_FEEDBACK'},ensure_ascii=False)
        path.write_text(content)
        contract_path=self.root/'large-contract.json'
        plan=replay.read_plan(self.root/'large-requests',[str(path)],contract_path)
        write(contract_path,{'materials':[{'path':str(path),'sha256':control.sha(path)}],'read_plan':plan})
        previous=0;parts=[]
        for p in plan:
            self.assertEqual(p['offset'],previous)
            result=replay.replay_material(evo.read(p['request_path']),Path(p['request_path']))
            self.assertLessEqual(len(json.dumps(result,ensure_ascii=False).encode()),60000)
            self.assertLessEqual(p['end']-p['offset'],32000)
            previous=p['end'];parts.append(result['text'])
        self.assertEqual(''.join(parts),content)
        self.assertLess(len(plan),len(content)//4000)

    def test_repeated_bound_read_does_not_erase_prior_complete_observation(self):
        child,sid,handle,contract,ticket=self.launch(self.request(task=self.ids[0]))
        self.reads(child,contract);self.write_output(child,contract,[])
        self.reads(child,contract)
        self.assertTrue(self.seal(child,sid,contract,ticket)['complete'])

    def test_native_budget_includes_main_and_container_pending_without_reset(self):
        controller={'key':'session-parent','requests':2,'cost':.2,'hasUncosted':False}
        def api(home,path,stream=False):
            if path.endswith('usage?groupBy=session'):return {'groupBy':'session','groups':[controller]}
            if path.endswith('/agents'):return {'agents':[{'agentId':'controller'}]}
            if path.endswith('/sessions'):return {'sessions':[{'sessionId':'session-parent','projectId':'project','status':'running'}]}
            raise AssertionError(path)
        job=self.root/'home/project/benchmarks/replay-test/.jobs/one/trial/agent/penguin-cost-by-session.json'
        write(job,{'groupBy':'session','groups':[{'key':'container-child','requests':1,'cost':.3,'hasUncosted':True}]})
        with patch.object(replay,'native_api',side_effect=api):
            first=replay.budget_snapshot(self.meta,self.scratch,self.mode,self.config)
            self.assertAlmostEqual(first['known_usd'],.5)
            self.assertAlmostEqual(first['unsettled_reserve_usd'],1)
            self.assertAlmostEqual(first['supervised_usd'],1.5)
            controller['cost']=10
            with self.assertRaisesRegex(ValueError,'budget reached'):
                replay.budget_gate(self.meta,self.scratch,self.mode,self.config)
            controller['cost']=.1
            again=replay.budget_snapshot(self.meta,self.scratch,self.mode,self.config)
            self.assertGreaterEqual(again['known_usd'],10.3)

    def test_completed_role_releases_only_once_when_idle_and_fully_priced(self):
        receipt=self.run_role('learner',self.ids[0],[])
        sid=receipt['session_id']; pending=True
        def api(home,path,stream=False):
            if path.endswith('usage?groupBy=session'):return {'groupBy':'session','groups':[
                {'key':'session-parent','requests':2,'cost':.2,'hasUncosted':False},
                {'key':sid,'requests':1,'cost':.1,'hasUncosted':pending}]}
            if path.endswith('/agents'):return {'agents':[{'agentId':'controller'}]}
            if path.endswith('/sessions'):return {'sessions':[
                {'sessionId':'session-parent','projectId':'project','status':'running'},
                {'sessionId':sid,'projectId':'project','status':'idle'}]}
            if stream:return {'type':'task_state','state':'idle','queued':0}
            raise AssertionError(path)
        with patch.object(replay,'native_api',side_effect=api):
            first=replay.budget_snapshot(self.meta,self.scratch,self.mode,self.config)
            self.assertAlmostEqual(first['unsettled_reserve_usd'],1.3)
            pending=False
            second=replay.budget_snapshot(self.meta,self.scratch,self.mode,self.config)
            self.assertAlmostEqual(second['unsettled_reserve_usd'],1)
            third=replay.budget_snapshot(self.meta,self.scratch,self.mode,self.config)
            self.assertEqual(second,third)
            ledger=evo.read(control.journal_path(self.scratch,self.mode))
            self.assertAlmostEqual(ledger['known_cost_usd'],.1)

    def test_same_session_cannot_claim_another_contract(self):
        child,sid,_,contract,ticket=self.launch(self.request(task=self.ids[0]))
        self.reads(child,contract);self.write_output(child,contract,[])
        altered={**contract,'output':str(self.root/'other-output.json')}
        write(ticket['contract'],altered)
        with self.assertRaisesRegex(ValueError,'original replay role contract changed'):
            replay.original_view(altered,expected_session=sid)

    def test_usage_groups_do_not_double_count_and_unknown_is_pending(self):
        usage={'groupBy':'session','groups':[{'key':'session-A','requests':2,'cost':.21,'hasUncosted':False},
                         {'key':'session-B','requests':1,'cost':None,'hasUncosted':True}]}
        cost,complete,_=replay.price_groups(usage)
        self.assertAlmostEqual(cost,.21);self.assertFalse(complete)
        with self.assertRaisesRegex(ValueError,'duplicate'):
            replay.price_groups({'groupBy':'session','groups':usage['groups']+[usage['groups'][0]]})

    def test_session_owner_is_counted_once_even_if_optional_provider_differs(self):
        first={'key':'session-A','provider':'first','requests':2,'cost':.21,'hasUncosted':False}
        second={**first,'provider':'second'}
        total,complete,owners=replay.price_groups({'groupBy':'session','groups':[first]})
        self.assertEqual(list(owners),['session-A'])
        self.assertAlmostEqual(total,.21);self.assertTrue(complete)
        with self.assertRaisesRegex(ValueError,'duplicate owner'):
            replay.price_groups({'groupBy':'session','groups':[first,second]})

    def test_model_or_ungrouped_usage_cannot_substitute_for_session_cost(self):
        first={'key':'deepseek-flash','provider':'deepseek','requests':2,'cost':.21,'hasUncosted':False}
        for document in ({'groupBy':'model','groups':[first]}, {'groups':[first]}):
            with self.subTest(document=document),self.assertRaisesRegex(ValueError,'groupBy=session'):
                replay.price_groups(document)

    def record_only(self):
        self.config['audit_policy']='record-only'
        write(self.configpath,self.config)
        self.mode['config_sha256']=control.sha(self.configpath)
        write(self.scratch/control.MARKER,self.mode)
        ledger=evo.read(control.journal_path(self.scratch,self.mode))
        ledger['config_sha256']=self.mode['config_sha256']
        write(control.journal_path(self.scratch,self.mode),ledger)

    def test_real_api_capture_budget_gate_preserves_cost_and_pending_without_provider(self):
        fixture=Path(__file__).parent/'fixtures/replay-native-budget-api-r3.json'
        before=control.sha(fixture); captured=evo.read(fixture)
        self.assertFalse(captured['contains_credentials'])
        self.assertEqual(captured['model_calls'],0)
        config=captured['config'];config['deployment']['penguin_home']=str(self.root/'isolated-real-home')
        mode={**captured['mode'],'private':str(self.root/'isolated-real-private')}
        scratch=self.root/'isolated-real-scratch'
        write(control.journal_path(scratch,mode),captured['journal'])
        for op in captured['journal']['operations'].values():
            if op['slot']!='controller':write(scratch/'evo-guard/dispatch/actual-unclaimed.json',{'slot':op['slot'],'call':{}})
        responses=captured['responses']
        usage=next(v for k,v in responses.items() if k.endswith('/usage?groupBy=session'))
        self.assertTrue(usage['groups']);self.assertTrue(all('provider' not in g for g in usage['groups']))
        expected=sum(g.get('cost') or 0 for g in usage['groups'])
        self.assertGreater(expected,0)
        target_fixture=Path(__file__).parent/'fixtures/replay-native-target-cost.json'
        target_before=control.sha(target_fixture);target=evo.read(target_fixture)
        self.assertEqual(target['document']['groupBy'],'session')
        self.assertTrue(all('provider' not in g for g in target['document']['groups']))
        target_cost=sum(g.get('cost') or 0 for g in target['document']['groups'])
        target_path=Path(config['deployment']['penguin_home'])/config['deployment']['project_id']/'benchmarks'/config['test_benchmark_id']/'.jobs'/'real-trial'/'agent/penguin-cost-by-session.json'
        write(target_path,target['document'])
        def api(home,path,stream=False):return responses[path]
        with patch.object(replay,'native_api',side_effect=api):
            first=replay.budget_gate(captured['meta'],scratch,mode,config)
            second=replay.budget_gate(captured['meta'],scratch,mode,config)
            self.assertEqual(first,second)
            self.assertAlmostEqual(first['known_host_usd'],expected)
            self.assertAlmostEqual(first['known_target_usd'],target_cost)
            self.assertAlmostEqual(first['unsettled_reserve_usd'],1.3)
            self.assertAlmostEqual(first['supervised_usd'],expected+target_cost+1.3)
            with self.assertRaisesRegex(ValueError,'budget reached'):
                replay.budget_gate(captured['meta'],scratch,mode,{**config,'budget_usd':expected+target_cost+1.29})
        live=next(v for k,v in responses.items() if k.endswith('/stream'))
        self.assertEqual(live['type'],'task_state');self.assertIn('state',live);self.assertIn('queued',live)
        self.assertEqual(control.sha(fixture),before)
        self.assertEqual(control.sha(target_fixture),target_before)

    def test_record_only_missing_read_still_collects_actual_output_and_compiles(self):
        self.record_only()
        child,sid,handle,contract,ticket=self.launch(self.request(task=self.ids[0]))
        last=contract['read_plan'][-1]
        self.reads(child,contract,omit=(last['path'],last['offset']))
        self.write_output(child,contract,self.candidate(self.ids[0]))
        result=self.seal(child,sid,contract,ticket)
        self.assertTrue(result['complete']);self.assertFalse(result['audit_passed'])
        self.finish(child,handle)
        collected=replay.collect_role({'role':'learner','task_id':self.ids[0]},self.meta,self.scratch,self.mode,self.config)
        self.assertTrue(collected['complete']);self.assertFalse(collected['audit_passed'])
        for task in self.ids[1:]:self.run_role('learner',task,[])
        self.run_role('evolver',None,[{'op':'SKIP','candidate_ids':[self.candidate(self.ids[0])[0]['id']],'reason':'Evidence is insufficient to retain an executable rule.'}])
        learned=replay.learning(self.mode,self.config)
        self.assertTrue(learned['complete']);self.assertFalse(learned['audit_passed'])
        self.assertEqual(learned['package']['entries'],0)
        seal=write(replay.private(self.mode)/'learning.json',learned)
        verified=replay.verify_learning_seal(self.config,seal)
        self.assertFalse(verified['audit_passed']);self.assertTrue(verified['complete'])

    def test_record_only_missing_seal_is_diagnostic_not_empty_success(self):
        self.record_only()
        child,sid,handle,contract,ticket=self.launch(self.request(task=self.ids[0]))
        self.reads(child,contract);self.write_output(child,contract,self.candidate(self.ids[0]));self.finish(child,handle)
        collected=replay.collect_role({'role':'learner','task_id':self.ids[0]},self.meta,self.scratch,self.mode,self.config)
        self.assertFalse(collected['audit_passed']);self.assertEqual(len(evo.read(collected['output'])),1)

    def test_record_only_source_isolation_and_missing_output_remain_hard(self):
        self.record_only()
        child,sid,handle,contract,ticket=self.launch(self.request(task=self.ids[0]))
        self.reads(child,contract);self.finish(child,handle)
        with self.assertRaisesRegex(ValueError,'missing actual child-authored output'):
            replay.collect_role({'role':'learner','task_id':self.ids[0]},self.meta,self.scratch,self.mode,self.config)
        self.operation(child,'read_file',{'file_path':'/test/answer.json'})
        with self.assertRaisesRegex(ValueError,'other sources, tests'):
            replay.collect_role({'role':'learner','task_id':self.ids[0]},self.meta,self.scratch,self.mode,self.config)

    def test_record_only_does_not_bypass_budget(self):
        self.record_only();prepared=self.request(task=self.ids[0])
        with patch.object(replay,'budget_gate',side_effect=ValueError('budget reached')):
            with self.assertRaisesRegex(ValueError,'budget reached'):
                replay.authorize(prepared['call'],self.meta,self.scratch,self.mode,'blocked')

    def test_record_only_confirmed_trial_boundary_failure_preserves_diagnostics(self):
        self.record_only()
        result={'complete':False,'hard_failure':True,'audit_status':'failed','measurement_record':'/preserved/raw-measurements.json'}
        module=type('Benchmark',(),{'execute':lambda _,req:result})()
        with patch.object(control,'phase_tickets',return_value=list(range(5))), \
             patch.object(control,'worker_result',side_effect=lambda *a:({'case_id':str(a[-1]),'run':1},{'session_id':str(a[-1])})), \
             patch.object(replay,'benchmark',return_value=module):
            with self.assertRaisesRegex(ValueError,'actual measurements preserved'):
                replay.collect_round({'round':1},self.meta,self.scratch,self.mode,self.config)
        self.assertEqual(evo.read(replay.private(self.mode)/'round-1-diagnostic.json'),result)
        self.assertFalse((replay.private(self.mode)/'round-1.json').exists())

    def test_record_only_stop_keeps_benchmark_failed_audit_status(self):
        self.record_only()
        result={'complete':True,'protocol':replay.PROTOCOL,**replay.audit_summary(self.mode,self.config),
                'audit_passed':False,'audit_status':'failed'}
        with patch.object(control,'pending_stop',return_value=None),patch.object(replay,'completion',return_value=result):
            stopped=replay.hook({'hook':'stop'},str(self.state),self.meta,self.scratch,[],self.mode)
        self.assertEqual(stopped['decision'],'stop')
        self.assertFalse(stopped['output']['audit_passed'])
        self.assertEqual(stopped['output']['audit_status'],'failed')

if __name__=='__main__':unittest.main()
