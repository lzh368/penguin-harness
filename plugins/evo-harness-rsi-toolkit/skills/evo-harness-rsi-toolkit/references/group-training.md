# Continuous group delegation (GDPevo adaptation)

The main Penguin owns training → compile/freeze → H0 tests → final tests.
One native evaluation child handles the single continuous Harbor training case.
Harbor starts a fresh native training root with one level of Solver and Evolver leaves. The five members share its workspace and business service;
they are not five independent Harbor trials. See [control.md](control.md).

## Host preparation and worker

Keep the test Target at a sanitized frozen H0. For .12, first run install-context
on its mapped empty package before snapshotting or preparing the Trainer.
 Create a separate fresh Trainer
from those Solver settings through the benchmark prepare-trainer operation.
Pass config, config_sha256, repo, evo_tool, base_state, output, method_skill,
method_hooks, training_budget_usd and host_operation_id. method_hooks is the
native installed hooks/evo-harness-rsi-toolkit directory, including hooks.json,
guard.mjs and icon.svg, not the source plugin's uninstalled hooks directory.
Preparation installs the exact method Skill and native hook package, enables
Trainer hooks and writes guard_version=2 and reflection_mode=solver-session in training.json; the test Target's hooks
stay disabled. Never copy host credentials or test materials into Trainer State.

training.json freezes train_ids, attempt_id=attempt-1, limits,
max_bytes, runtime, workspace=/app, private_learning=/logs/agent/evo-harness,
answer_dir=/app/submission/answers, feedback_dir under private_learning,
training_budget_usd, host_operation_id, config_sha256, compilation_reserve_sec
and solving_timeout_sec. The .12 profile also binds agents-md-v1 context transport,
observed-v1 local reflection and native-trial-v1 receipt handling. The host reserves this training allocation once for
the container root and all its children; host/worker overhead is separate.

After configure-controller, call the pinned protocol's prepare-benchmarks with
config, config_sha256, repo and the native Project benchmarks directory as output.
Use its training/test case_ids and case_to_task mapping; do not hand-build Case
folders. This preserves the native CASE-NNN-... registration and pins the bundle's
actual Node version in every Harbor statement. Before worker authorization the
guard revalidates these files through verify-benchmarks.

Then prepare a worker-request with phase=training and
a call containing the explicit frozen runtime, run_in_background=true and:

~~~text
Read <absolute group-training.md> section "Training evaluation worker" and
<absolute evaluation.md>. Execute exactly this one group training delegation.
training_protocol_version: 1
operation_id: <host_operation_id>
config_sha256: <frozen hash>
training_budget_usd: <reserved allocation including container roles>
case_id: <registered CASE-NNN-... continuous group case>
run: 1
expected_version: <Trainer version>
test_agent_id: <Trainer id>
benchmark_id: <training Benchmark id>
provider: <fixed provider>
model_id: <fixed model>
thinking_level: <fixed thinking level>
expected_members: [train_001, train_002, train_003, train_004, train_005]
~~~

Invoke the complete returned call unchanged using native run_subagent, including
its description. Do not rewrite a registered prompt or its native arguments. Record the child id; await
that child without new steering. Do not start any held-out worker yet.

## Training evaluation worker

Follow the unchanged evaluation.md worker contract for validation, fixed
runtime, Harbor invocation, State immutability, cleanup and failure semantics.
Launch exactly one group task with the prepared Trainer State and pinned current
adapter/install bundle, run_timeout=task and max_turns=-1. Check the operation,
config hash and training allocation against training.json. Never spawn another
worker, edit State, solve training tasks or write scoreboard.

Wait for Harbor termination, verifier/artifact export and cleanup. Preserve all
five final answers, full private evidence and the learned package. Empty-rule
guidance can be valid; a missing package or failed/incomplete seal cannot.
The host independently reruns verify-export before publication. Final verifier
details are host diagnostics, never learning input.

Return the ordinary cell YAML plus training_protocol_version: 1, operation_id,
trial_path, submission_path, private_learning_path, member_ids, skill_sha256 and
complete. This extension applies only to training. No fabricated score or
complete flag can replace original evidence.

## Container training root

You coordinate this one batch; you do not solve tasks yourself. The native
user-prompt hook injects the complete contract, original trace_path, frozen
profile and role files. Require native run_subagent and the five public member
folders. Use only the fixed helpers for shell operations. Write request JSON
inside private_learning/requests; do not write submission answers, State,
native traces or role-authored candidates/decisions.

1. Read the frozen profile and injected method material. Initialize the private
   budget journal using the host-reserved training allocation and operation/hash.
   Use the exact canonical journal path from control.md; reserve the root's own
   overhead at slot=controller. Reconcile tickets, claims and native children
   before any new call. For each returned role call, reserve its exact derived
   operation_id/slot/request_sha256 with your chosen reserve_usd before dispatch,
   and bind the actual returned child id afterward. The root does not settle its
   own or child expenses while running; the host accounts for the complete trial.
   Respect the original solving timeout and reserve the frozen compilation
   window; never extend the task or spend a historical allocation.
2. In listed member order request role-request with role=solver and task_id.
   The helper constructs the full role prompt, literal evo_task grant, public
   text inputs, binary hashes, exact answer/judge paths and frozen runtime. Its
   initial prompt also supplies the exact candidate schema and authorized input
   hashes, so reflection requires no later source/trace inspection or hash tool.
   Invoke its returned run_subagent call unchanged. This one Solver keeps its
   native conversation through the single judge, complete raw feedback and,
   for negative feedback, candidate generation and sealing. Its allocation
   covers solving and reflection together. Await this leaf before the next Solver; the service is shared. Do not send candidates to later Solvers.
3. After the Solver's native completed notification, write one control request:
   `{"op":"prepare-capture","trace_path":"<your current original root trace>","task_id":"<member>"}`.
   Execute it with the installed control.py, then execute its returned exact
   command with no edits. If recovery returns context_exists=true and
   context_verified=true with command=null, use that already captured context;
   do not run capture or judge again. The helper derives the unique registered Solver,
   original trace segments, exact ordered input grant and canonical paths.
   Do not reconstruct, reorder or copy inputs, session bindings or judge paths
   into a hand-written capture request. In particular instruction.md is a
   member-root file when the original grant says so; do not infer a path under
   input/. Preparation checks the original evidence in a disposable private
   directory but does not publish a captured context. Only the returned
   evo.py capture command writes that context. The Solver's short judge receipt must bind the
   full ledger, and all ledger chunks must have been read after that one judge
   command. Its captured context output is
   private_learning/contexts/<task_id>.json. The canonical judge command and
   all feedback read receipts must be present in the native trace. Each granted
   public input must have complete helper receipts or exact successful native
   read_file windows covering the entire file before the judge. Partial,
   truncated, failed or foreign-path reads do not qualify; use read-material
   for truncated native output, lines over 2000 UTF-16 units or files at least
   8 MiB. Learning-role materials and feedback still require helper receipts. Transport, parse,
   identity or evidence failure ends this batch as method_failed.
4. Check the captured context's reflection binding. For negative feedback it
   must contain the original Solver's complete sealed candidate audit, even
   when its output array is empty. Successful tasks have no reflection output.
   The internal role label `reflector` names a phase of that Solver Session;
   never request role=reflector or create another Agent. Do not edit candidates,
   replay a finished reflection, or send a nonempty continuation to the Solver.
5. Once all five terminal contexts are complete, request role=evolver. The
   helper derives each required reflection audit from its captured context,
   verifies the original Solver identity, feedback/answer/order and output,
   and concatenates candidates without edits. Do not hand-supply a replacement
   learner identity. The Evolver receives all contexts, candidates and
   prebatch-library.json plus the frozen entry ceilings and package budget.
   It validates its own decisions, then seals decisions.json. Zero candidates
   still requires this real Evolver call and explicit [] output. Missing-read
   or structural repair occurs in that same child before sealing. Audit its
   original completed trace and successful seal.
6. Write a compile request using the five contexts, candidates.json,
   decisions.json, prebatch-library.json and frozen limits; execute evo.py.
   Export the resulting library with unchanged limits/max_bytes into
   /app/submission/skill: a fixed Reader SKILL.md with the complete manifest,
   plus methods/<native-name>/SKILL.md for every retained entry. Their total
   bytes include all metadata and rules; no selected subset is exported.
   GDPevo precreates this directory with only
   an empty `.keep`; do not delete it or run a shell cleanup. The fixed export
   helper accepts exactly this placeholder at the installed training.json's
   bound destination. It builds and validates the complete package first,
   atomically moves only the original `.keep` file into a unique sibling backup,
   records and rechecks its contents, mode and identity, then removes only the
   empty original directory and publishes atomically without replacing an
   existing skill. It never renames the original lower-layer directory. It records source/profile
   hashes, the backup location, package hash and publication status in
   private_learning/skill-export-publication.json. The backup stays beside the
   destination because /app and /logs/agent may use different filesystems.
   Any other content, nonempty marker, symlink, prior export receipt or already
   exported skill is an error; preserve it and report rather than clearing it.
   If publication fails, the helper attempts a no-overwrite rollback and keeps
   the receipt/backup on conflict. Do not automatically retry that export.
   Even a valid no-op exports the same Reader format with an empty manifest and no methods.
   Private artifacts never enter the skill slot.
7. Write a control request inside private_learning/requests/ with
   op="prepare-seal-training", your trace_path and role_requests (absolute
   path to the completed independent Evolver audit-role request inside
   private_learning/requests/). Solver reflection audits are derived from the
   five captured contexts, never copied into a new Session identity.
   Execute it with the installed control
   helper. It copies the complete frozen runtime and limits, derives the
   canonical evidence paths, and writes requests/seal-training.json without
   calling a model or judge. Its short result gives request_path, request_sha256
   and command; execute that exact command, then control's verify-training.
   Do not transcribe a subset of runtime fields or copy the prepared JSON from
   tool output. A conflicting existing request is preserved; only an explicit
   metadata correction before any training-export.json exists may be retried,
   without new solving/scoring. An existing export is never overwritten.
   The stop hook rechecks this request and complete export.
   Missing reflection/compilation/trace/seal evidence is failure, not no-op.
8. Account for each Solver Session once, including its reflection. Reflection
   audit records do not create another expense; unknown final usage remains
   reserved until the native total settles. Wait for all native children to finish and return their evidence paths and
   costs/unknown liabilities. Harbor then exports artifacts and terminates.
   The host, not this training root, installs the exact package and runs tests.

The host restores H0 for five concurrent tests only after verify-freeze succeeds,
then restores final for five concurrent tests after the complete H0 matrix.
Each test is fresh and cannot access training judge. All five final tests receive
the same complete Reader with all general descriptions and every current-context
Skill body before business actions; general bodies are not loaded. Apply rules
only when their conditions hold. A valid no-op explicitly
reuses the H0 matrix; failed learning produces neither final publication nor
held-out completion. Ordinary RSI Benchmarks do not inherit this 5/5 contract.
