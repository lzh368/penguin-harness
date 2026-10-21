# Deterministic operations

Run `python3 -B <Skill>/scripts/evo.py <request.json>`. It performs one requested
operation, prints JSON, and exits 2 on invalid input. Never import it with
bytecode enabled in a plugin tree. Paths below are explicitly resolved by the
caller. Private output files are write-once; do not fix a role's JSON by editing
its output. Ask the same role to correct formatting with no new learning input.

Common limits: `{"general":5,"topic":5}`. Internal `library.json` is an array
of entry objects (`id,level,topic?,trigger,scope,body`), initially `[]`.

| op | Required request fields (in addition to op) | Output |
|---|---|---|
| judge | split=train, task_id, attempt_id, train_ids, url, answer (JSON file), output | Raw external feedback, answer SHA, exact request, score/correct or explicit error |
| capture | split=train, task_id, attempt_id, train_ids, answer, feedback (receipt path), inputs (text file paths), traces (all Solver JSONL segment paths), binding, output | Full context file and hash; rejects misbound or missing judge receipt |
| capture-evaluation | capture fields except feedback, plus evaluation and evaluation_identity as described below | Bound native workspace feedback and captured context; no HTTP receipt |
| compile | train_ids, contexts (captured files), candidates (array file), decisions (Evolver array file), library, limits, output | Validated new internal library; no learned prose is invented |
| export | library, limits, max_entries, max_bytes, output (new directory) | Data-only learned package with fixed reader/selector and full file hashes |
| fingerprint | path (directory) | File map and canonical SHA-256 |
| journal | action and fields below | Durable single-operation accounting; no dispatch |

`binding` contains exact session_id, provider, model_id, workspace, agent_state,
source; source is cli for current roots, benchmark only for explicitly pinned
legacy roots, or subagent with parent_trace whose native event names the child.
Current tests use 0.2.16 cli/subagent, not guessed legacy trace semantics.
For workspace evaluations use `capture-evaluation`: the capture fields except
feedback, plus evaluation (the original native worker protocol converted to
JSON without changing fields) and evaluation_identity containing agent_id,
expected_version, provider, model_id, thinking_level and run; all six fields are
required. The answer is the final UTF-8 text artifact, which need not be JSON.
Bind its Session
id to the Solver trace. It preserves original 0..100 feedback and normalizes
score/100 explicitly, without an invented HTTP status. Keep the shared evaluation
request and Target prompt unchanged. Inputs must be public statement files inside
the bound Target workspace, with complete native `read_file` output in its trace.
This path requires neither an `evo_task` grant nor GDPevo material-read receipts.

Additional operations:
- `read-material`: path, offset (default 0); returns a 4000-character chunk with
  exact bytes hash, text, offset/end/total. Roles repeat until end=total. Require
  untruncated tool output. `capture` accepts only inputs named in the native
  user/parent task grant and actually read through these receipts.
- `file-digest`: path; prints file_path/sha256, sealing a role-authored output in
  its native trace. A later parent rewrite invalidates the role audit.
- `audit-role`: traces, binding, materials (paths), outputs (paths); verifies
  full material receipts and output seals in actual paired tool results.
- `verify-package`: path; validates the fixed reader/selector, index, every
  entry hash and exact file allowlist, rejecting traces/answers/extra support.
- `seal-training`: private_learning, train_ids, contexts, candidates, decisions,
  library (pre-batch), skill, roles, runtime, limits, max_entries, max_bytes, output.
  Each roles item is an audit-role
  request plus role=reflector/evolver, task_id for reflector, role_text_path.
  All materials/outputs/contexts must be within private_learning. It verifies
  candidate concatenation and writes portable hashes and trace bindings for
  the host's verify-export. No role dispatch or stage decision occurs here.

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
