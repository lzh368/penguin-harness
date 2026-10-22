"""Deterministic native-tool test setup; no model, business answers or fake receipts."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

v = json.load(sys.stdin)
state, workspace = Path(v['state']), Path(v['workspace'])
scripts = state / 'skills/evo-harness-rsi-toolkit/scripts'
sys.path.insert(0, str(scripts))
import evo
import control
private = workspace / 'private'
for p in [private/'dispatch', private/'requests', private/'roles', workspace/'answers']:
    p.mkdir(parents=True, exist_ok=True)
t = {'guard_version':2, 'train_ids':['train_001'], 'attempt_id':'attempt-1',
     'config_sha256':'a'*64, 'private_learning':str(private), 'answer_dir':str(workspace/'answers'),
     'feedback_dir':str(private/'feedback'), 'workspace':str(workspace),
     'reflection_mode':'solver-session', 'context_transport':'agents-md-v1',
     'reflection_evidence':'observed-v1','candidate_edit':'native-draft-v1','solver_stage':'bound-post-judge-v1',
     'max_bytes':16000,'limits':{'general':5,'topic':5},
     'runtime':{'provider':'deepseek','model_id':'deepseek-flash','thinking_level':'xhigh'}}
(scripts.parent/'training.json').write_bytes(evo.encoded(t))
(private/'frozen-training.json').write_bytes(evo.encoded(t))
(private/'roles/solver.md').write_text(control.role_text('solver'))
contract=evo.role_contract(t,'reflector','train_001')
Path(contract['request_dir']).mkdir(parents=True,exist_ok=True)
Path(contract['output']).parent.mkdir(parents=True,exist_ok=True)
input_path=workspace/'input.json';input_path.write_text('[{"value":2},{"value":3}]')
other=private/'roles/train_002/candidates.json';other.parent.mkdir();other.write_text('OTHER_UNCHANGED')
paths=evo.judge_paths(t,'train_001','attempt-1')
Path(paths['judge_request']).parent.mkdir(parents=True,exist_ok=True)
judge={'op':'judge','split':'train','task_id':'train_001','attempt_id':'attempt-1','train_ids':['train_001'],
       'url':v['judge_url'],'answer':paths['answer'],'output':paths['output']}
Path(paths['judge_request']).write_bytes(evo.encoded(judge))
learned=state/'skills/evo-harness-learned';learned.mkdir()
(learned/'SKILL.md').write_bytes(evo.render_package([],16000)['SKILL.md']);evo.install_context({'state':str(state)})
prompt=json.dumps({'evo_task':{'split':'train','task_id':'train_001','attempt_id':'attempt-1','inputs':[str(input_path)]}})+'\n'+json.dumps({'evo_role_seal':{**contract,'parent_session_id':v['parent_session']}})
call={'prompt':prompt,'description':'Synthetic original Solver native-tool test','provider':'deepseek','model_id':'deepseek-flash','thinking_level':'xhigh','run_in_background':True,'yield_time_ms':1000}
ticket={'session_id':v['parent_session'],'phase':'solver','task_id':'train_001','slot':'solver/train_001/attempt-1','call':call}
(private/'dispatch/ticket.json').write_bytes(evo.encoded(ticket))
(private/'dispatch'/(evo.digest(ticket['slot'].encode())+'.claimed.json')).write_bytes(evo.encoded({'tool_call_id':'native-parent-dispatch','request_sha256':evo.digest(json.dumps(call,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())}))
candidate={'id':'train_001-attempt-1-c01','task_id':'train_001','attempt_id':'attempt-1',
           'lesson':'SYNTHETIC sum authorized rows at the stated grain.','trigger':'synthetic row total',
           'scope_hint':'synthetic input only','evidence':[{'kind':'input','sha256':evo.digest(input_path.read_bytes()),'claim':'Two authorized input rows','epistemic':'observed'}]}
reads=[]
for tag,path in [('role',Path(contract['materials'][0])),('input',input_path)]:
    for offset in range(0,len(path.read_text()),4000):
        req=Path(contract['request_dir'])/(tag+'-'+str(offset)+'.json');req.write_bytes(evo.encoded({'op':'read-material','path':str(path),'offset':offset}));reads.append(str(req))
print(json.dumps({'call':call,'contract':contract,'helper':str(scripts/'evo.py'),'reads':reads,'input':str(input_path),'other':str(other),'paths':paths,'candidate':candidate}))
