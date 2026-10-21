# Continuous group delegation (GDPevo adaptation)

The main Penguin owns training → compile/freeze → H0 tests → final tests.
One native evaluation child handles the single continuous Harbor training case.
Harbor starts a fresh native training root with one level of Solver, Reflector
and Evolver leaves. The five members share its workspace and business service;
they are not five independent Harbor trials. See [control.md](control.md).

## Host preparation and worker

Keep the test Target at a sanitized frozen H0. Create a separate fresh Trainer
from those Solver settings through the benchmark prepare-trainer operation.
Pass config, config_sha256, repo, evo_tool, base_state, output, method_skill,
method_hooks, training_budget_usd and host_operation_id. method_hooks is the
native installed hooks/evo-harness-rsi-toolkit directory, including hooks.json,
guard.mjs and icon.svg, not the source plugin's uninstalled hooks directory.
Preparation installs the exact method Skill and native hook package, enables
Trainer hooks and writes guard_version=2 training.json; the test Target's hooks
stay disabled. Never copy host credentials or test materials into Trainer State.

training.json freezes train_ids, attempt_id=attempt-1, limits, max_entries,
max_bytes, runtime, workspace=/app, private_learning=/logs/agent/evo-harness,
answer_dir=/app/submission/answers, feedback_dir under private_learning,
training_budget_usd, host_operation_id, config_sha256, compilation_reserve_sec
and solving_timeout_sec. The host reserves this training allocation once for
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

Invoke the returned call using native run_subagent. Record the child id; await
that child without new steering. Do not start any held-out worker yet.

## Training evaluation worker

Follow the unchanged evaluation.md worker contract for validation, fixed
runtime, Harbor invocation, State immutability, cleanup and failure semantics.
Launch exactly one group task with the prepared Trainer State and pinned current
adapter/install bundle, run_timeout=task and max_turns=-1. Check the operation,
config hash and training allocation against training.json. Never spawn another
worker, edit State, solve training tasks or write scoreboard.

Wait for Harbor termination, verifier/artifact export and cleanup. Preserve all
five final answers, full private evidence and the learned package. Empty indexed
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
   text inputs, binary hashes, exact answer/judge paths and frozen runtime.
   Invoke its returned run_subagent call unchanged. Await this leaf before the
   next Solver; the service is shared. Do not send candidates to later Solvers.
3. After the leaf returns, use status to locate original trace segments. Read
   actual session metadata with read-material and bind session_id, source,
   provider, model_id, workspace, agent_state and the parent trace. Use capture
   with all trace segments, granted text inputs, exact answer, canonical feedback
   path and judge_request path. The Solver's short judge receipt must bind the
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
4. For each negative captured execution, request one role=reflector for that
   task and invoke the exact returned call. It reads its full role/context and
   writes private_learning/roles/<task_id>/candidates.json. Candidate ids use
   the fixed task/attempt prefix; the role validates its own draft before
   seal-role-output. The same child resolves any missing-read/structure error
   before a successful seal; root never sends another instruction or rewrites it.
   Audit its original trace/material/output bindings. Successful
   executions have no Reflector or candidate. Do not rewrite candidates.
5. Once all five contexts and eligible Reflectors are complete, request
   role=evolver with reflectors containing every negative task's original
   audit-role request, in frozen task order (each includes role, task_id,
   role_text_path, traces, binding, materials and outputs). Use [] when all
   five executions succeeded. The helper re-audits these roles and their
   task/attempt candidate namespaces before concatenating arrays without edits;
   a failed Reflector blocks Evolver dispatch. The Evolver receives the actual
   frozen entry/UTF-8 budgets and checks its own draft with validate-decisions
   before sealing. Invoke the returned call; the Evolver reads all
   contexts, candidates and prebatch-library.json and writes decisions.json.
   Zero candidates still requires a real Evolver call and explicit [] output.
   It uses seal-role-output to verify complete actual reads and structure; any
   missing-read repair occurs in that same child before success. Audit its seal.
6. Write a compile request using the five contexts, candidates.json,
   decisions.json, prebatch-library.json and frozen limits; execute evo.py.
   Export the resulting library with unchanged max_entries/max_bytes to the
   /app/submission/skill directory. GDPevo precreates this directory with only
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
   Even a valid no-op exports the fixed reader, selector and empty index.
   Private artifacts never enter the skill slot.
7. Write private_learning/requests/seal-training.json with train_ids, contexts,
   candidates, decisions, pre-batch library, skill, all Reflector/Evolver audit
   requests, runtime and frozen limits/budgets; output must be
   private_learning/training-export.json. Execute seal-training, then control's
   verify-training. The stop hook rechecks this request and complete export.
   Missing reflection/compilation/trace/seal evidence is failure, not no-op.
8. Wait for all native children to finish and return their evidence paths and
   costs/unknown liabilities. Harbor then exports artifacts and terminates.
   The host, not this training root, installs the exact package and runs tests.

The host restores H0 for five concurrent tests only after verify-freeze succeeds,
then restores final for five concurrent tests after the complete H0 matrix.
Each test is fresh and cannot access training judge. A valid no-op explicitly
reuses the H0 matrix; failed learning produces neither final publication nor
held-out completion. Ordinary RSI Benchmarks do not inherit this 5/5 contract.
