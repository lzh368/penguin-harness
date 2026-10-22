# Guarded native delegation for the continuous GDPevo profile

The main Penguin decides and invokes every native call. control.py only prepares
a requested prompt/ticket, checks evidence or enforces a native hook boundary.
It never calls a model, Harbor, run_subagent or another controller. This reference
applies to the explicitly frozen continuous GDPevo experiment; ordinary RSI
continues to use the shared evaluation protocol without this group contract.

## Installed paths and control requests

Use the installed Skill's absolute scripts/control.py and scripts/evo.py.
The native user-prompt hook supplies the current original trace_path and
initializes a Session-local EVO_CONTROL.json in its native scratchpad. Use that
actual trace path, not a guessed filename. Every control request includes op
and trace_path. Before configure-controller, read the installed SKILL.md and
this control.md completely with native read_file, with sufficient output for
every line. The guard verifies these real read_file observations; an
acknowledgment, a path reference or read-material alone cannot replace them.
Invoke exactly one standalone command:

~~~text
python3 -B <absolute-installed-control.py> <absolute-request.json>
~~~

No shell wrapper, chaining, substitution, background process or changed helper.
A training coordinator writes its request JSON only inside
<private_learning>/requests/. Its shell can run only the fixed control/evo
helpers. It does not solve a task, call business/judge HTTP, write answers,
edit State or author role output. Native role leaves perform those actions
within their assigned scope. Neither wrapper nor Python helper owns a loop.

Ending a turn while native background work is pending is an await boundary,
not final experiment completion. The native stop hook receives a read-only
snapshot of this Session's running children/commands and queued completion
notices. Evo returns wait only at a normal, non-aborted boundary with real
pending work; core rechecks that state and ends the current run before the Goal
stop hook. It writes neither GOAL.json nor evo-acceptance.json. The existing
native completion notification resumes the coordinator; no polling loop or
external stage dispatcher is introduced. A queued failed/stopped notice waits
only until its delivery so the coordinator can record the actual failure.

After pending work has been delivered, final evidence validation is unchanged:
missing training/export/freeze/test evidence cannot become successful completion.
Already terminal Goals and abort/cutoff events never become waiting. This
experiment uses an unbounded native Goal token budget and a separately supervised
dollar budget. A positive native Goal token budget retains its existing Goal
accounting/continuation behavior instead of using Evo's no-spin wait; this helper
does not change the generic Goal budget protocol. Waiting also grants no tool
permission: all native calls still pass the existing pre-tool checks.

| op | Additional fields | Result and caller action |
|---|---|---|
| configure-controller | config: absolute frozen authorized experiment JSON | Returns mode with fixed config/protocol hashes and freeze/completion request paths. Main Session calls this before registering any worker. |
| worker-request | phase: training/h0/final, call: complete native run_subagent arguments | Validates runtime, Benchmark, Target, case, run and prerequisites; returns exact call and ticket. Main Penguin invokes that call itself. |
| role-request | role: solver/reflector/evolver; task_id for solver/reflector; reflectors: all completed audit-role requests for Evolver, in negative-task order | Writes the complete fixed role prompt once; returns a short native call with prompt_file and prompt_sha256. The root invokes that call unchanged. Native run_subagent reads and hashes the file before creating the child. |
| format-request | subagent_id: the original completed evaluation worker handle | Controller only: returns one fixed formatting-only input_subagent call for the same worker's existing result. No tools, rerun or new scores are permitted. |
| validate-workers | phase: training/h0/final | Controller only, read-only: binds the registered workers to their original native replies and validates plain protocol YAML. Returns canonical cells and any worker needing its single formatting repair; never dispatches or writes a matrix. |
| status | none | Mode, clock/compilation reserve, original native trace paths and call count; no dispatch. Use prepare-capture for Solver capture metadata; inspect actual child metadata using read-material for role audits. |
| prepare-capture | task_id | Training root only, after the original Solver's native completed notification. Derives the exact ordered grant, registered child binding, all trace segments and canonical paths; validates evidence in a disposable private directory, then writes requests/capture-<task>-<attempt>.prepared.json once. Returns prepared=true/captured=false and short path/hash/command. Execute the command unchanged, unless context_exists/context_verified=true and command=null: then reuse the already captured context. |
| prepare-seal-training | role_requests: absolute paths to completed Reflector/Evolver audit-role JSON files inside private/requests/, in negative-task order followed by Evolver | Training root only: writes private/requests/seal-training.json with the complete frozen runtime and canonical paths; returns only request_path, request_sha256 and command. Identical requests are reused read-only; conflicting requests are never overwritten. It does not seal, dispatch or judge. |
| verify-training | none | Revalidates the sealed group against its real private/requests/seal-training.json and training-export.json. |
| verify-completion | none | Host verifies freeze and complete matrices; final host-request cost remains pending. |

Use the returned call object verbatim, including prompt_file, prompt_sha256, provider, model_id,
thinking_level and run_in_background=true for prepared learning roles. Do not
read and retype the prompt file or replace it with an inline prompt: the native
tool supplies its complete, hash-checked text as the child's original task.
Ordinary evaluation worker requests may keep the shared inline prompt.
Guarded evaluation workers must run
in native background mode so their terminal status arrives as a harness
notification, including for formatting-only recovery. Do not supply agent_id: the native worker
inherits this Agent's installed method. Workspace and permission are inherited,
not call arguments. Native leaves have no run_subagent/input_subagent.
For worker-request, the ordinary shared evaluation prompt also carries one
unambiguous thinking_level line matching the call's frozen thinking_level;
keep the shared evaluation.md file and worker result schema unchanged.

The pre-tool hook claims a semantic slot before native dispatch. Repeating a
slot with another tool call is blocked even if a previous launch is ambiguous.
Record the returned native child id immediately. For learning roles, input_subagent may poll with an empty prompt only. Never
change ids to retry, repair a failed role by editing its output, or send new
task-solving instructions to the same child. A host evaluation worker's existing
result may receive the shared protocol's formatting repair: after its original
native completed notification, request format-request and invoke exactly the
returned input_subagent arguments once. The guard binds the original registered
worker across trace compaction and verifies a new completed native reply after
the exact repair prompt, no tools and unchanged result values. Remove Markdown
fences when requested; repeating the same fenced text is not a format repair.
If that repair still lacks valid YAML, stop; it does not authorize rescoring.
Pre-start Harbor engineering repair remains within the original worker and must
prove no Target/model request began. Preserve all failed launch records.

Before dispatch initialize the canonical budget journal: the training root uses
<private_learning>/journal.json with training_budget_usd; the main Controller
uses <scratchpad>/evo-guard/journal.json with the authorized total budget. Reserve
the coordinator's own overhead under slot=controller before delegating. Each
role-request/worker-request returns its slot, request_sha256, derived operation_id
and journal path. Use these exact identities in an evo.py journal reserve
operation with a positive, explicitly chosen reserve_usd, then invoke the native
call and bind its real returned child id. A missing or insufficient reservation
blocks dispatch; the helper never picks a budget or starts a worker for you.

The training root may init/reserve/bind/status its journal but must not settle
itself or its running children. The host settles the whole Harbor trial from
its terminated native Sessions' actual costs; do not double-count those children
on top of the trial total. Unknown cost stays reserved. This is a dispatch
reservation check, not a provider-enforced hard spending stop.

## Main Controller records

After configure-controller, register the two Benchmarks using the pinned protocol's
prepare-benchmarks operation with config, config_sha256, repo and output set to
the native Project benchmarks directory. Use the returned CASE-NNN-... case_ids,
not the underlying GDPevo task ids, in worker prompts and matrices. The helper
writes the established native Benchmark shape, including the bundle's pinned Node
version in each Harbor launch. verify-benchmarks rechecks the complete case files
and source pins before every worker; the host must not hand-author or move cases.
The returned case_to_task mapping preserves the original task and scoring rules.

After training, write the full verify-export request to a separate
JSON file, preserving submission, training_export, private_learning, trace_root,
train_ids, runtime and evo_tool. verify-export must succeed after Harbor ends.

At the returned freeze_request path write:

~~~json
{
  "op": "verify-freeze",
  "evo_tool": "<absolute-installed-evo.py>",
  "export_request": "<absolute-complete-verify-export-request.json>",
  "h0_snapshot": "<native-H0-agent_state-rooted.tar.gz>",
  "final_snapshot": "<native-final-agent_state-rooted.tar.gz>",
  "h0_version": 1,
  "final_version": 2,
  "target_state": "<actual-held-out-Target-agent_state>",
  "test_case_ids": ["<registered-CASE-001>", "<registered-CASE-002>", "<registered-CASE-003>", "<registered-CASE-004>", "<registered-CASE-005>"],
  "runtime": "<copy the complete frozen config.runtime object verbatim here, not this placeholder string>"
}
~~~

Substitute the complete frozen runtime object. This official profile includes
provider, model_id, thinking_level, api_host, effective_reasoning_effort and
client_type. Preserve all frozen fields and values; never shorten or extend it.
The default active_state is final. For an H0 matrix restore the exact H0 State;
the guard rechecks this same request with active_state=h0. Before final testing
restore the frozen final State. Never test different States of one Target
simultaneously. Final version exceeds H0 only for changed learned contents.
A verified identical no-op uses the same version and identical State file
contents, even if tar metadata differs.

verify-freeze reruns all original export/trace evidence every time. It checks
the exact final slot, valid H0 Reader with empty rules, unchanged full-file byte budget,
unchanged State outside the slot/top-level integer version, native snapshots
without links/escape/duplicates, and actual Target hashes. An earlier successful
receipt never substitutes for this recheck.

After each phase's native workers finish, call validate-workers for that phase
before constructing a matrix or advancing to the next State. If a completed
worker needs formatting repair, use its returned handle with format-request,
invoke the exact returned call once, await its completed reply and validate
again. Pending workers must finish; identity, changed-value or execution
violations are not formatting problems. Do not strip fences yourself or copy
values out of a rejected reply into a successful matrix.

When validation is complete, use its canonical cells to store the ordinary
matrix requests with cells, identity, case_ids, runs, time, summary_title,
summary and evo_tool. Preserve original worker values. Final acceptance binds
every matrix cell back to the original plain YAML and original Harbor trial.
The matrix identity is the shared evaluation protocol's five-field object:

~~~json
{"agent_id":"<test_agent_id>","expected_version":1,"provider":"<fixed>","model_id":"<fixed>","thinking_level":"<fixed>"}
~~~

Use the measured version. This identity is not the complete runtime object:
do not add api_host, effective_reasoning_effort or client_type to matrix identity
or worker cells. The complete frozen runtime remains required in the export,
freeze and injection requests.
H0's request belongs at <scratchpad>/evo-guard/h0-matrix-request.json so final
worker authorization can verify that H0 has finished.

At the returned completion_request path write h0_matrix_request, and either
final_matrix_request for changed State or final_policy=reused_equivalent_h0
with no final_matrix_request for a verified no-op. Set
host_cost_status=pending_final_request: this Session cannot settle its own last
model request while still running. Keep after-matrix State hashes, termination
and cost evidence in the experiment record. Call verify-completion before
returning; the native stop hook checks again and records evo-acceptance.json.
Completion also verifies actual Skill use from each original Harbor trial:
unchanged frozen State and complete observations of the same hash-bound
SKILL.md from every Target. This audit does not alter test scores or expose test answers
to learners. Missing injection evidence leaves the raw measurement intact but
cannot establish a complete method run. Missing, incomplete or changed evidence
yields method_failed and blocks Goal completion; it does not select H0 as a substitute.

## Training root records

The user-prompt hook reads the installed guard_version=2 training.json,
freezes its hash, writes full role material under private_learning/roles/ and
the empty prebatch-library.json, and injects the complete training contract.
Do not reconstruct these files. Read group-training.md for the single-batch
sequence and tools.md for exact capture/compile/export/seal schemas.

After each completed Solver call prepare-capture with only op, trace_path and
task_id. It binds the saved ticket/claim to the exact original parent call,
native child pointer, first parent_agent USER grant and completed notification;
it never guesses a Session from a tool handle. It preserves the literal input
paths and order, including instruction.md at its original granted location.
An identical prepared request is reused read-only after evidence revalidation;
a conflicting request or ambiguous/incomplete child is rejected. Old hand-written
request files are left intact. Never edit answers, feedback, traces or a failed
capture request to make it pass; preparation is metadata plumbing, not another
Solver attempt. It makes no service/model calls and does not publish a context.
Run the returned command unchanged; the original capture validator checks all
evidence again and alone writes contexts/<task_id>.json.
If the canonical context already exists, preparation revalidates all original
evidence and requires byte-identical context content. It returns context_exists
and context_verified true with context_path/hash and command=null. Use that
verified existing context without another capture or scoring call. captured
remains false because preparation itself did not publish the context. A changed
existing context is preserved and refused.

Helpers validate cooperative execution; they are not a sandbox against
arbitrary malicious code. The isolated container, restricted account/network
and held-out judge disablement remain required.
