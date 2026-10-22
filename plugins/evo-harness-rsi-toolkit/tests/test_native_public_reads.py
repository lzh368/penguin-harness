"""Solver public-input evidence only. Synthetic data; no model or benchmark calls."""
import copy
import json
from pathlib import Path
import unittest
import test_evo
evo = test_evo.evo


def view_for(content, windows=None, path='/public/instruction.md'):
    lines=content.split('\n')
    if not content or content.endswith('\n'):lines.pop()
    lines=[line[:-1] if line.endswith('\r') else line for line in lines]
    view={'binding':{'workspace':'/public'},'pairs':[],'messages':[]}
    for i,args in enumerate(windows or [{}]):
        args={'file_path':'instruction.md',**args}
        offset=max(1,evo.native_read_count(args.get('offset'),1))
        limit=evo.native_read_count(args.get('limit'),2000)
        end=min(offset+limit-1,len(lines))
        output=[f'{n:6}\t{lines[n-1]}' for n in range(offset,end+1)]
        if '\r\n' in content:output.append('(file uses CRLF line endings)')
        if end<len(lines):output.append(f'[file has {len(lines)} lines total; showing {offset}-{end} — call again with offset to continue]')
        text='\n'.join(output) if content else f'"{args["file_path"]}" is an empty file (0 lines).'
        call={'source':'trace','line':i*2+1,'payload':{'type':'tool_call','name':'read_file','arguments':json.dumps(args),'tool_call_id':str(i)}}
        result={'source':'trace','line':i*2+2,'payload':{'type':'tool_call_output','stop_reason':'completed','output':text,'tool_call_id':str(i)}}
        view['pairs'].append({'call':call,'observation':result})
        view['messages'].extend([call,result])
    return view,[(path,content,evo.digest(content.encode()))]


class NativePublicReadTests(unittest.TestCase):
    def accepted(self,content,windows=None):
        view,records=view_for(content,windows)
        self.assertEqual(evo.verify_solver_inputs(view,records)[0],[{'path':records[0][0],'sha256':records[0][2]}])
        return view,records

    def test_full_exact_native_unicode_crlf_and_empty_reads(self):
        for content in ('alpha\n\n','a\u2028b\u2029c\x85d\n','alpha\r\nbeta\r\n',
                        'alpha\rbeta\r','','\n','… [line truncated]\n[output truncated: literal]\n','😀'*1000):
            with self.subTest(content=content[:25]):self.accepted(content)

    def test_paging_uses_native_line_counts_and_handles_duplicate_pages(self):
        self.accepted('a\nb\nc\n',[{'offset':1,'limit':1},{'offset':2,'limit':2},{'offset':1,'limit':1}])
        self.accepted('a\nb\nc\n',[{'offset':'0x1','limit':'1.9'},{'offset':'2.9','limit':'2'}])
        self.accepted('a\nb\n',[{'offset':None,'limit':None}])
        self.accepted('a\nb\n',[{'offset':-2.5,'limit':2.8}])

    def test_missing_page_does_not_count_as_complete(self):
        view,records=view_for('a\nb\nc\n',[{'offset':1,'limit':1},{'offset':3,'limit':1}])
        with self.assertRaisesRegex(ValueError,'not fully read'):evo.verify_solver_inputs(view,records)

    def test_wrong_tool_path_failure_images_arguments_or_extra_output_do_not_count(self):
        base,records=view_for('required facts\n')
        variants=[]
        for status in ('fatal','aborted',None):
            view=copy.deepcopy(base);view['pairs'][0]['observation']['payload']['stop_reason']=status;variants.append(view)
        for path in ('other.md','https://example.com/instruction.md','/foreign/instruction.md'):
            view=copy.deepcopy(base);view['pairs'][0]['call']['payload']['arguments']=json.dumps({'file_path':path});variants.append(view)
        view=copy.deepcopy(base);view['pairs'][0]['call']['payload']['name']='exec_command';variants.append(view)
        view=copy.deepcopy(base);view['pairs'][0]['call']['payload']['arguments']='[]';variants.append(view)
        view=copy.deepcopy(base);view['pairs'][0]['observation']['payload']['images']=['data:image/png;base64,AA=='];variants.append(view)
        for suffix in ('\n[tool timeout: exceeded 100ms]','\n[output archived: /scratch/file]','\nextra'):
            view=copy.deepcopy(base);view['pairs'][0]['observation']['payload']['output']+=suffix;variants.append(view)
        for view in variants:
            with self.subTest(view=view['pairs'][0]),self.assertRaises(ValueError):evo.verify_solver_inputs(view,records)

    def test_lexical_workspace_binding_works_without_host_source_tree(self):
        view,records=self.accepted('public')
        for target in ('/public/other/../instruction.md','//public/instruction.md'):
            view['pairs'][0]['call']['payload']['arguments']=json.dumps({'file_path':target})
            self.assertIsNotNone(evo.native_file_read(view,*records[0][:2]))

    def test_truncation_budget_head_tail_and_utf16_long_line_are_rejected(self):
        view,records=view_for('a\nb\nc\n')
        for output in ('     1\ta\n[file has 3 lines total; showing 1-1 (output limit reached) — call again with offset to continue]',
                       '     1\ta\n[output truncated: kept first 8 and last 8 of 99 chars]\n     3\tc',
                       '     1\ta\n     2\tb',
                       'prefix\n     1\ta\n     2\tb\n     3\tc'):
            view['pairs'][0]['observation']['payload']['output']=output
            with self.assertRaises(ValueError):evo.verify_solver_inputs(view,records)
        for content in ('😀'*1001,'x'*2001,'a\0b'):
            view,records=view_for(content)
            with self.assertRaises(ValueError):evo.verify_solver_inputs(view,records)
        self.assertIsNone(evo.native_file_read({'binding':{'workspace':'/public'},'pairs':[]},
                          '/public/instruction.md','x\n'*(4*1024*1024)))

    def test_empty_input_needs_actual_completed_empty_file_observation(self):
        view,records=view_for('')
        view['pairs']=[];view['messages']=[]
        with self.assertRaises(ValueError):evo.verify_solver_inputs(view,records)
        view,records=view_for('')
        view['pairs'][0]['observation']['payload']['output']=''
        with self.assertRaises(ValueError):evo.verify_solver_inputs(view,records)

    def test_native_reads_do_not_replace_feedback_or_role_material_receipts(self):
        view,records=self.accepted('required')
        with self.assertRaises(ValueError):evo.verify_material_records(view,records)
        with self.assertRaises(ValueError):evo.verify_material_records(view,records,standalone=True)

    def test_fingerprint_and_invalid_counts_fail(self):
        view,records=view_for('required')
        with self.assertRaisesRegex(ValueError,'fingerprint'):evo.verify_solver_inputs(view,[(records[0][0],records[0][1],'0'*64)])
        for value in (True,[],{},'','bad','Infinity',float('inf'),'\x851'):
            self.assertIsNone(evo.native_read_count(value,1))
        self.assertEqual(evo.native_read_count('0b10',1),2)
        self.assertEqual(evo.native_read_count('0o10',1),8)

    def test_observation_must_finish_before_judge_even_when_call_started_earlier(self):
        view,records=view_for('a\nb\n',[{'offset':1,'limit':1},{'offset':2,'limit':1}])
        judge={'source':'trace','line':10,'payload':{'type':'tool_call','name':'exec_command','tool_call_id':'judge','arguments':'{}'}}
        judged={'source':'trace','line':11,'payload':{'type':'tool_call_output','tool_call_id':'judge','output':'{}'}}
        view['pairs'].append({'call':judge,'observation':judged})
        view['messages'].extend([judge,judged])
        self.assertEqual(evo.verify_solver_inputs(view,records,'judge')[1],3)
        last=view['pairs'][1]['observation']
        view['messages'].remove(last);view['messages'].append(last)
        with self.assertRaises(ValueError):evo.verify_solver_inputs(view,records,'judge')
        with self.assertRaises(ValueError):evo.verify_solver_inputs(view,records,'nonexistent')

    def test_observation_before_its_call_is_not_evidence(self):
        view,records=view_for('required')
        view['messages'].reverse()
        with self.assertRaises(ValueError):evo.verify_solver_inputs(view,records)


class NativeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_evo.BatchTests('test_complete_batch_compiles_and_exports_one_complete_skill')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        c=evo.read(f.contexts[0]);self.original=c
        trace=Path(c['trace']['sources'][0]['path'])
        rows=[json.loads(line) for line in trace.read_bytes().splitlines()]
        inp=c['inputs'][0]
        rows[2]['payload']={'type':'tool_call','name':'read_file','arguments':json.dumps({'file_path':inp['source']}),'tool_call_id':'read-1'}
        rows[3]['payload']={'type':'tool_call_output','tool_call_id':'read-1','stop_reason':'completed','output':f"     1\t{inp['text']}"}
        trace.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
        self.request={'op':'capture','split':'train','task_id':c['task_id'],'attempt_id':c['attempt_id'],
                      'train_ids':f.train,'answer':str(f.root/'answers/train_001.json'),
                      'feedback':c['feedback']['feedback_path'],'judge_request':c['judge_request']['path'],
                      'inputs':[inp['source']],'traces':[str(trace)],'binding':c['trace']['binding'],
                      'output':str(f.root/'native-context.json')}

    def test_real_capture_and_five_context_revalidation_use_identical_native_rule(self):
        result=evo.capture(self.request)
        self.fixture.contexts[0]=result['path']
        self.assertEqual(evo.compile_batch(self.fixture.req)['entries'],1)
        context=evo.read(result['path'])
        self.assertEqual(context['inputs'],self.original['inputs'])
        self.assertEqual(context['feedback'],self.original['feedback'])
        trace=Path(self.request['traces'][0]);trace.write_bytes(trace.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'raw solver trace changed'):evo.compile_batch(self.fixture.req)

    def test_post_judge_native_read_is_rejected_without_changing_score(self):
        trace=Path(self.request['traces'][0])
        rows=[json.loads(line) for line in trace.read_bytes().splitlines()]
        rows=rows[:2]+rows[4:]+rows[2:4]
        trace.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
        with self.assertRaisesRegex(ValueError,'not fully read'):evo.capture(self.request)
        self.assertEqual(evo.read(self.request['feedback']),self.original['feedback'])


if __name__=='__main__':unittest.main()
