# Deterministic operations

Run `python3 -B <Skill>/scripts/evo.py <request.json>`. It performs one requested
operation, prints JSON, and exits 2 on invalid input. Never import it with
bytecode enabled in a plugin tree. Paths below are explicitly resolved by the
caller. Captured evidence and sealed role outputs are immutable; do not fix a role's JSON
by editing its output. Before its own output seal, a role may validate and
correct its draft with validate-candidates or validate-decisions, using only its
already assigned training evidence. Ordinary RSI may use only the shared protocol's existing-result
formatting repair. The guarded continuous profile forbids free-form child
steering and replacement dispatch; preserve invalid output as method_failed.
Read [control.md](control.md) before group operations. The training root writes
request JSON only under private_learning/requests and executes only the two
fixed helpers; native Solver leaves alone may execute judge.

Common limits: `{"general":5,"topic":5}`. Internal `library.json` is an array
of entry objects (`id,level,topic?,trigger,scope,body`), initially `[]`.

| op | Required request fields (in addition to op) | Output |
|---|---|---|
| prepare-judge-request | task_id, url | Data-only canonical request writer; derives all paths/attempt/roster from installed training.json, returns exact judge command and makes no HTTP call |
| validate-candidates | contexts, train_ids, candidates, candidate_prefix | Read-only draft check for the Reflector before output sealing; no rewritten content |
| validate-decisions | compile fields except output, plus max_bytes | Read-only compile/export validation of the complete rendered SKILL.md, including the whole-file UTF-8 budget and native 2000-UTF-16-unit physical-line capacity, in a temporary directory before Evolver sealing; no published State |
| judge | split=train, task_id, attempt_id, train_ids, url, answer (JSON file), output; request file itself must have the canonical judge_request path | Writes full canonical ledger with raw feedback and hashes; CLI returns a short ledger path/hash/byte-count receipt |
| capture | split=train, task_id, attempt_id, train_ids, answer, feedback (canonical receipt path), judge_request (canonical JSON path), inputs (text file paths), traces (all Solver JSONL segment paths), binding, output | Full context; requires exactly one canonical native judge exec and rejects changed or extra feedback. Guarded group roots obtain this request through control.py prepare-capture, never by copying grant paths. |
| capture-evaluation | capture fields except feedback and judge_request, plus evaluation and evaluation_identity as described below | Bound native workspace feedback and captured context; no HTTP receipt |
| compile | train_ids, contexts (captured files), candidates (array file), decisions (Evolver array file), library, limits, output | Validated new internal library; no learned prose is invented |
| export | library, limits, max_bytes, output (new directory) | Exactly one SKILL.md containing the fixed Reader and every retained rule, within the full-file budget; no truncation or per-task selection |
| fingerprint | path (directory) | File map and canonical SHA-256 |
| journal | action and fields below | Durable single-operation accounting; no dispatch |

`binding` contains exact session_id, provider, model_id, workspace, agent_state,
source; source is cli for current roots, benchmark only for explicitly pinned
legacy roots, or subagent with parent_trace whose native event names the child.
Current tests use 0.2.16 cli/subagent, not guessed legacy trace semantics.
For workspace evaluations use `capture-evaluation`: the capture fields except
feedback and judge_request, plus evaluation (the original native worker protocol converted to
JSON without changing fields) and evaluation_identity containing agent_id,
expected_version, provider, model_id, thinking_level and run; all six fields are
required. The answer is the final UTF-8 text artifact, which need not be JSON.
Bind its Session
id to the Solver trace. It preserves original 0..100 feedback and normalizes
score/100 explicitly, without an invented HTTP status. Keep the shared evaluation
request and Target prompt unchanged. Inputs must be public statement files inside
the bound Target workspace, with complete native `read_file` output in its trace.
This path requires neither an `evo_task` grant nor GDPevo material-read receipts.

## Canonical continuous-training judge

The installed training.json determines all paths, keyed by config_sha256,
task_id and attempt_id. role-request returns these paths to the Solver:

```text
answer = <answer_dir>/<task_id>.json
judge_request = <private_learning>/judge-requests/<config_sha256>/<task_id>.<attempt_id>.json
output = <feedback_dir>/<config_sha256>/<task_id>.<attempt_id>.json
training_snapshot = <private_learning>/frozen-training.json
```

Prefer prepare-judge-request to generate this data from the installed profile:
write a separate request containing op=prepare-judge-request, task_id and the
public local judge URL, then run the fixed helper on that file. This preparation
makes zero HTTP/model calls. Execute its returned canonical command exactly once.
The complete generated request file contains the required identity and operation:

```json
{"op":"judge","split":"train","task_id":"<frozen task>","attempt_id":"attempt-1",
 "train_ids":["train_001","train_002","train_003","train_004","train_005"],
 "url":"http://127.0.0.1:<documented-port>/api/judge",
 "answer":"<canonical answer>","output":"<canonical output>"}
```

Use the real roster and URL from the frozen profile/public access document.
Execute only `python3 -B <absolute-installed-evo.py> <absolute-judge_request>`.
The helper binds its actual request file and installed profile, reserves the
canonical durable ledger before HTTP, and never silently repeats an interrupted
call. Changing output, answer, attempt or roster cannot create another attempt.
Do not call twice even when the helper would return its existing receipt:
trace acceptance requires exactly one original canonical native exec. No direct
HTTP probe or echoed/hand-authored receipt qualifies. A failed HTTP or parse
result remains an error with unknown score, not zero, and blocks compilation.

The CLI's small stdout has source=evo-judge-receipt, feedback_path, sha256,
bytes, task_id, attempt_id, config_sha256, status, score and correct. The full
canonical ledger remains on disk and is never truncated to fit tool output.
Immediately after the one judge command, the Solver must read the entire ledger
using read-material chunks until end=total. capture validates the short receipt,
its exact ledger hash and all complete native read chunks occurring after judge;
neither a compact receipt alone nor a parent-written feedback copy is sufficient.

Additional operations:
- Guarded group `control.py prepare-capture`: only op, trace_path (the current
  original root trace) and task_id. After the Solver's native completed notice,
  derive exact metadata from its registered dispatch, original parent/child
  trace and literal USER grant. The helper validates through existing capture
  in a disposable private directory, writes only a deterministic request and
  returns a short path/hash/command with prepared=true/captured=false. Run that
  command unchanged to publish the actual context; preparation is not capture
  completion. Wrong old request files remain unchanged, conflicting canonical
  requests are refused, identical requests still revalidate the real evidence.
  If the canonical context already exists and matches that revalidation, the
  receipt gives context_exists/context_verified=true, its path/hash and
  command=null. Reuse that context; do not publish or score it again.
- `read-material`: path, offset (default 0); returns a 4000-character chunk with
  exact bytes hash, text, offset/end/total. Roles repeat until end=total. Require
  untruncated foreground exec_command output. Use one literal absolute helper
  and request path per exec; no variables or batch loops. Do not pipe, redirect
  or background receipts; their canonical output must remain in the
  native tool observation. `capture` accepts only inputs named in the native
  user/parent task grant and actually read through these receipts, or through
  exact successful native read_file output for Solver public inputs only.
  Native windows must cover every line before the single judge, match the frozen
  bytes as rendered by Penguin, and contain no truncation or error wrapper.
  Lines over 2000 UTF-16 units or files at least 8 MiB need read-material.
  Empty files require a successful native empty-file observation. This option
  does not apply to feedback ledgers or Reflector/Evolver materials.
- `seal-role-output`: role=reflector/evolver, task_id for Reflector only; guarded
  training leaves call this from their own native Session. It derives fixed
  materials/output/validation from training.json and the registered parent task,
  checks every actual read and validates the unsealed draft. Missing offsets
  return complete=false/sealed=false; the same leaf reads those chunks and
  retries, without any new solving or scoring. Success includes file_path/sha256,
  role/Session/config identity and material hashes. Output is then immutable.
  The only permitted unpaired trace action is this currently running seal call.
- `file-digest`: path; prints file_path/sha256 for ordinary unguarded RSI or
  inspection. It cannot replace guarded Reflector/Evolver sealing.
- `audit-role`: traces, binding, materials (paths), outputs (paths); verifies
  full material receipts and output seals in actual paired tool results.
- `verify-package`: path; validates the fixed Reader, complete rule rendering,
  byte/line limits and the sole-file SKILL.md allowlist, rejecting
  traces/answers/extra support.
- `seal-training`: private_learning, train_ids, contexts, candidates, decisions,
  library (pre-batch), skill, roles, runtime, limits, max_bytes, output.
  Each roles item is an audit-role
  request plus role=reflector/evolver, task_id for reflector, role_text_path.
  All materials/outputs/contexts must be within private_learning. It verifies
  candidate concatenation and writes portable hashes and trace bindings for
  the host's verify-export. No role dispatch or stage decision occurs here.
  In guarded group training the request is exactly
  private_learning/requests/seal-training.json and its output is exactly
  private_learning/training-export.json. Include the frozen-training snapshot
  and canonical judge request/feedback ledger evidence; the sealer records portable mappings
  so host verification works after Harbor exports the original artifacts.
  Before any write, all guarded context snapshots must match one hashed frozen
  profile, and runtime must equal that profile's complete object: missing,
  extra or changed fields are rejected. Use control's prepare-seal-training
  (trace_path, role_requests: absolute paths to the completed audit-role JSON
  files inside private_learning/requests/) to write the canonical request; it
  returns only the path, SHA and exact helper command, not the large request.
  Preparation preserves identical existing requests and refuses conflicts.
  Correcting rejected request metadata before publication needs no new role
  or judge call; it never permits replacing an already sealed export.

Every helper invocation and its output belongs in the native role trace.
The caller must also verify role materials were actually read completely and
the requested thinking level appears on model request events: session_meta
alone does not record thinking in 0.2.16. None of these files is a security
sandbox against a malicious model; containers, user permissions and restricted
network carry that boundary. Do not give learners the host benchmark checkout.

Journal operations, under a filesystem lock:

- init: journal, config_sha256, controller_session_id, budget_usd.
- reserve: journal, config_sha256, operation_id, reserve_usd, request_sha256,
  controller_trace, role, slot (stable batch/task/attempt/role or State/case/run). Refuses an existing operation id or exhausted budget.
- bind: journal, config_sha256, operation_id, external_id (native Session/job).
- settle: journal, config_sha256, operation_id, terminated=true, cost_usd
  (number or null), evidence (paths). Null retains the in-flight reserve.
- reconcile_cost: same identity, cost_usd and evidence after delayed accounting.
- status: journal, config_sha256; read current records before any recovery.

Reserve includes every in-flight container and pending role, plus host overhead.
If an operation exceeds its reserve, record actual cost without rounding down;
stop further launches and report overspend. Unpriced requests and interrupted
provider calls remain unknown liabilities. No helper makes an absolute billing
guarantee or treats Controller completion as child/job termination.
