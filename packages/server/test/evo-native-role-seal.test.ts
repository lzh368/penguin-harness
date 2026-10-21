/** Real production parent/leaf/exec/trace with scripted model messages only. */
import fs from "node:fs/promises";
import path from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { expect, it, vi } from "vitest";
import {
  GenerativeModel,
  saveProjectConfig,
  toolCall,
  assistantText,
  userText,
} from "@prismshadow/penguin-core";
import type { GenerativeModelParameters, OmniMessage } from "@prismshadow/penguin-core";
import { createTestApp, waitFor } from "./helpers.js";
import type { ChannelEvent } from "../src/runtime/channel.js";

const run = promisify(execFile);
const shellQuote = (s: string) => "'" + s.replaceAll("'", "'\\''") + "'";

it("a native leaf seals against its in-flight original exec and parent dispatch, then audits after relocation", async () => {
  const network = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network forbidden"));
  const calls = new WeakMap<object, { leaf: boolean; turns: number }>();
  let leafCommands: string[] = [];
  let dispatch: Record<string, unknown> = {};
  const model = vi
    .spyOn(GenerativeModel.prototype, "streamGenerate")
    .mockImplementation(async function* (this: GenerativeModel, params: GenerativeModelParameters) {
      let state = calls.get(this);
      if (!state) {
        state = {
          leaf: JSON.stringify(params.newMessages).includes("native-seal-leaf-marker"),
          turns: 0,
        };
        calls.set(this, state);
      }
      const turn = state.turns++;
      if (!state.leaf && turn === 0) {
        yield toolCall({
          name: "run_subagent",
          arguments: JSON.stringify(dispatch),
          toolCallId: "native-dispatch",
          stopReason: "completed",
        });
      } else if (state.leaf && turn < leafCommands.length) {
        yield toolCall({
          name: "exec_command",
          arguments: JSON.stringify({ cmd: leafCommands[turn] }),
          toolCallId: "native-leaf-" + turn,
          stopReason: "completed",
        });
      } else {
        yield assistantText("synthetic complete");
      }
      return { status: "completed" } as const;
    });
  const app = await createTestApp({
    titles: { maybeGenerate() {} },
    beforeSeed: async (root) =>
      saveProjectConfig(root, "default_project", {
        providers: {},
        models: [
          {
            provider: "custom",
            model_id: "synthetic-role-seal",
            context_window: 100000,
            api_key: "SYNTHETIC_UNUSED_KEY",
            base_url: "http://127.0.0.1:1",
            client_type: "openai-chat",
          },
        ],
        default_model: { provider: "custom", model_id: "synthetic-role-seal" },
      }),
  });
  try {
    const workspace = path.join(app.root, "workspace");
    await fs.mkdir(workspace);
    const root = await app.deps.sessionService.createSession({
      projectId: "default_project",
      agentId: "default_agent",
      provider: "custom",
      modelId: "synthetic-role-seal",
      workspace,
      approvalMode: "allow-all",
      isAdmin: true,
    });
    const statePath = path.join(app.root, "default_project/agents/default_agent/agent_state");
    const fixtures = path.resolve("../../plugins/evo-harness-rsi-toolkit/tests");
    // Captured negative training material is wholly synthetic. Its HTTP boundary is
    // mocked by the existing fixture; the role's exec, trace and seal remain real.
    const setup = await run(
      "python3",
      [
        "-B",
        "-c",
        `
import sys,json,types,shutil
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import test_evo as f
base=Path(sys.argv[2]); fixture=base/'synthetic-capture'; fixture.mkdir()
f.tempfile.TemporaryDirectory=lambda **kw: types.SimpleNamespace(name=str(fixture),cleanup=lambda:None)
state=Path(sys.argv[3]); helper=state/'skills/evo-harness-rsi-toolkit/scripts/evo.py'
helper.parent.mkdir(parents=True,exist_ok=True); helper.write_bytes(f.TOOL.read_bytes())
private=base/'private'; (private/'roles/train_001').mkdir(parents=True); (private/'contexts').mkdir()
old_install=f.install_training
def native_install(root,train):
 _,original=old_install(root,train)
 t={**original,'guard_version':2,'private_learning':str(private),'feedback_dir':str(private/'feedback'),'runtime':{'provider':'custom','model_id':'synthetic-role-seal'},'limits':{'general':5,'topic':5},'max_entries':5,'max_bytes':16000}
 (helper.parent.parent/'training.json').write_bytes(f.evo.encoded(t))
 return helper,t
f.install_training=native_install
b=f.BatchTests(methodName='runTest'); b.setUp(); t=b.training
c=f.evo.role_contract(t,'reflector','train_001')
Path(c['materials'][0]).write_text('SYNTHETIC role instructions.\\n'*200)
Path(c['materials'][1]).write_bytes(Path(b.contexts[0]).read_bytes())
candidate=b.candidates[0]; candidate['id']='train_001-attempt-1-c01'; Path(c['output']).write_bytes(f.evo.encoded([candidate]))
prompt='native-seal-leaf-marker\\n'+json.dumps({'evo_role_seal':{**c,'parent_session_id':sys.argv[4]}},ensure_ascii=False)
call={'prompt':prompt,'description':'SYNTHETIC native Reflector','provider':'custom','model_id':'synthetic-role-seal'}
slot='reflector/train_001/attempt-1'; ticket={'session_id':sys.argv[4],'phase':'reflector','task_id':'train_001','slot':slot,'call':call}
d=private/'dispatch'; d.mkdir(); (d/'ticket.json').write_bytes(f.evo.encoded(ticket))
canonical=json.dumps(call,ensure_ascii=False,sort_keys=True,separators=(',',':'))
(d/(f.evo.digest(slot.encode())+'.claimed.json')).write_bytes(f.evo.encoded({'tool_call_id':'native-dispatch','request_sha256':f.evo.digest(canonical.encode())}))
requests=[]
seal=private/'seal.json'; seal.write_bytes(f.evo.encoded({'op':'seal-role-output','role':'reflector','task_id':'train_001'})); requests.append(str(seal))
for p in c['materials']:
 for offset in range(0,len(Path(p).read_text()),4000):
  q=private/('read-'+str(len(requests))+'.json'); q.write_bytes(f.evo.encoded({'op':'read-material','path':p,'offset':offset})); requests.append(str(q))
requests.append(str(seal))
print(json.dumps({'helper':str(helper),'contract':c,'requests':requests,'call':call}))
`,
        fixtures,
        app.root,
        statePath,
        root.sessionId,
      ],
      { maxBuffer: 1024 * 1024 },
    );
    const prepared = JSON.parse(setup.stdout) as {
      helper: string;
      requests: string[];
      call: Record<string, unknown>;
      contract: Record<string, unknown>;
    };
    dispatch = prepared.call;
    leafCommands = prepared.requests.map(
      (req) => "python3 -B " + shellQuote(prepared.helper) + " " + shellQuote(req),
    );
    const events: ChannelEvent[] = [];
    app.deps.channels.get(root.sessionId).subscribe((e) => events.push(e));
    await app.deps.manager.startTask(root.sessionId, [userText("native-seal-root-marker")]);
    await waitFor(() => app.deps.manager.statusOf(root.sessionId) === "idle", 20000);
    const messages = events
      .filter((e) => e.event === undefined)
      .map((e) => JSON.parse(e.data) as OmniMessage);
    const payload = (m: OmniMessage) => m.payload as unknown as Record<string, unknown>;
    const outputFor = (id: string) => {
      const found = messages.find(
        (m) => payload(m).type === "tool_call_output" && payload(m).tool_call_id === id,
      );
      expect(found, JSON.stringify(messages)).toBeDefined();
      const output = String(payload(found!).output);
      const json = output.split("\n").find((line) => line.startsWith("{"));
      expect(json, output).toBeDefined();
      return JSON.parse(json!) as Record<string, unknown>;
    };
    expect(outputFor("native-leaf-0").complete).toBe(false);
    const sealed = outputFor("native-leaf-" + (leafCommands.length - 1));
    expect(sealed.complete, JSON.stringify(sealed)).toBe(true);
    const leafMeta = messages.find((m) => m.type === "session_meta" && m.origin?.length);
    expect(sealed.session_id).toBe(payload(leafMeta!).session_id);
    expect(sealed.session_id).not.toBe(root.sessionId);
    // The same original receipts must survive host export relocation, including the
    // frozen training profile. Do not fabricate, rewrite or flatten native traces.
    const audited = await run("python3", [
      "-B",
      "-c",
      `
import json,sys,importlib.util,shutil
from pathlib import Path
helper=Path(sys.argv[1]); spec=importlib.util.spec_from_file_location('evo',helper); e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
state=helper.parents[3]; traces=list((state.parent/'traces').rglob(sys.argv[2]+'_*.jsonl')); parent=list((state.parent/'traces').rglob(sys.argv[3]+'_*.jsonl'))
rows=[json.loads(line) for p in traces for line in p.read_bytes().splitlines()]
binding=next(r['payload'] for r in rows if r['type']=='session_meta'); binding={k:binding[k] for k in ('session_id','source','provider','model_id','workspace','agent_state')}; binding['parent_trace']=str(parent[0])
t=e.read(helper.parent.parent/'training.json'); c=e.role_contract(t,'reflector','train_001')
req={'role':'reflector','task_id':'train_001','role_text_path':c['materials'][0],'materials':c['materials'],'outputs':[c['output']],'binding':binding,'traces':[str(p) for p in traces],'require_role_seal':True}
assert e.audit_role(req)['complete']
dest=Path(sys.argv[4])/'relocated'; dest.mkdir(); mapping={}
paths=[*req['traces'],str(parent[0]),*c['materials'],c['output'],str(Path(t['private_learning'])/'frozen-training.json')]
for n,p in enumerate(paths):
 q=dest/str(n);shutil.copyfile(p,q);mapping[p]=str(q)
assert e.audit_role({**req,'path_map':mapping})['complete']
print(json.dumps({'complete':True,'relocated':True,'native_trace_parts':len(traces)}))
`,
      prepared.helper,
      String(sealed.session_id),
      root.sessionId,
      app.root,
    ]);
    expect(JSON.parse(audited.stdout)).toMatchObject({ complete: true, relocated: true });
    expect(network).not.toHaveBeenCalled();
  } finally {
    await app.deps.manager.shutdown();
    await app.cleanup();
    model.mockRestore();
    network.mockRestore();
  }
}, 30000);
