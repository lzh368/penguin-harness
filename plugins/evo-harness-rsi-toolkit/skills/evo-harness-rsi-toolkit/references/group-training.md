# Continuous group delegation (GDPevo adaptation)

Only the host Controller owns baseline → training → publish → final evaluation.
This reference delegates one training batch, not another experiment controller.
Penguin's leaf evaluation worker cannot spawn children. Harbor's adapter starts
a new native root Target Session in the container; that root can invoke the
Solver, Reflector and Evolver leaves while the shared service remains alive.

## Host preparation and worker request

Create a separate Trainer Agent with the Solver's fixed settings and H0 learned
slot, plus this method Skill. Its AGENTS.md must distinguish the root from a
child: only a Session with `run_subagent`, given a continuous group instruction,
reads this reference's “Container training root”; child Sessions follow their
explicit role. Write frozen training inputs in
`skills/evo-harness-rsi-toolkit/training.json`: train_ids, attempt_id, limits,
max_entries, max_bytes, runtime, workspace, output paths, compilation deadline.
Also freeze `training_budget_usd`, `host_operation_id`, and `config_sha256` from
the host's reserved training operation. Pass these explicitly to `prepare-trainer`;
the budget includes the container root and all its child roles. Reserve host
Controller/worker overhead separately. Do not infer an allocation from old runs.
There is no test material or scoreboard in that State. Use the benchmark
protocol helper's `prepare-trainer` operation and verify its State snapshot.

Call one `run_subagent` with explicit runtime and this prompt:

```text
Read <group-training.md> section "Training evaluation worker" and
<evaluation.md>. Execute exactly this one group training delegation.
training_protocol_version: 1
operation_id: <unique id>
config_sha256: <frozen experiment config hash>
training_budget_usd: <host-reserved allocation, including container children>
case_id: <the published continuous group case>
run: 1
expected_version: <Trainer State version>
test_agent_id: <Trainer agent id, NOT the held-out Target>
benchmark_id: <training Benchmark>
provider: <fixed provider>
model_id: <fixed model>
expected_members: [train_001, train_002, train_003, train_004, train_005]
```

The host records its native worker id before waiting. Never stop a completed
Trainer at “answers done”; compilation/export are part of this same task.

## Training evaluation worker

Follow the shared `evaluation.md` worker's validation, fixed-runtime, Harbor
invocation, State immutability and failure semantics. Run exactly one Harbor
group task with the prepared Trainer State. Never spawn another worker, edit
State, or write scoreboard. Verify training.json's host_operation_id,
config_sha256 and training_budget_usd match this request before launching Harbor.
Use the pinned current adapter and install bundle, `run_timeout=task`,
`max_turns=-1`. Respect original task timeout and reserve
time within it for all learning and export. Only you may read final verifier
summary; it is not learning input.

After Harbor fully terminates, validate the five exported final answers and
nonempty Skill, raw training summary, runtime and cleanup. Return the ordinary
cell YAML fields plus `training_protocol_version: 1`, `operation_id`,
`trial_path`, `submission_path`, `private_learning_path`, `member_ids`,
`skill_sha256`, `complete`. This is an explicit training-only extension;
independent test workers return the unchanged ordinary cell protocol. A failed
group has no fabricated score. The host keeps the diagnostic record outside
scoreboard and publishes only after export/injection checks succeed.

## Container training root

You have only one batch delegation. Require all five configured input folders,
the allowed environment access document and native `run_subagent`. Resolve your
own App Data Dir and Agent State from Environment, not assumed host paths.
Read training.json and the complete roles.md/tools.md. Use `/app` as inherited
workspace and `/logs/agent/evo-harness/` for private learning material, never
inside the learned package. Do not read benchmark repository, tests, solutions,
hidden rulebooks, private service files, host scoreboards or held-out materials.

0. On first entry create a batch-local journal under the private output directory;
   require positive training_budget_usd plus host_operation_id/config_sha256 in
   training.json, matching this delegation. Missing allocation blocks dispatch.
   reserve only the training allocation already reserved by the host (do not
   count these costs twice). On resume read it first. Stable semantic slots are
   `batch-1/<task_id>/attempt-1/solver`, `.../reflector`, and `batch-1/evolver`.
   Before each native call reserve its slot/request hash, then bind its native
   Session id. A reserved/bound/completed slot is never dispatched again:
   inspect that Session and its real trace/output, await a running child, or
   reconcile completed work. Ambiguous dispatch is blocked, never retried by
   changing ids. Formatting fixes reuse the same child. No automatic repeat
   after an answer was attempted. Record actual terminated child and cost or
   pending cost. This journal is method state, not an external scheduler.
1. Inspect real time remaining; reserve the frozen compilation/export window
   within the task's 6-hour solving limit. A missing/unreadable input blocks
   the group; do not invent missing examples. Record pre-batch library and hash.
2. For each configured member in order, invoke a fresh native Solver leaf
   with its complete role text, exact task/attempt, input directory, answer path
   `/app/submission/answers/<task_id>.json`, local judge URL from
   environment_access.md, and helper path. Give the frozen H0 library only.
   Include a literal JSON task grant in the native Solver prompt:
   `{"evo_task":{"split":"train","task_id":"<id>","attempt_id":"attempt-1","inputs":["<exact allowed input paths>"]}}`.
   Derive these files only from that member's public input directory plus
   environment_access.md; no test/other-member paths. Solver must use
   `read-material` for each granted text file before action; preserve binary
   payload facts through paired tools and identify their hashes separately.
   One leaf at a time; five tasks share the same business service. Wait for
   completion. Record native Session id, model, thinking and inherited cwd.
3. Use `capture` on every segment of that Solver's original trace (including
   current completed segment), the actual answer, exact judge receipt and
   input files. Bind the parent trace's subagent pointer. Check the actual
   judge output appears in the Solver's trace. Infrastructure/HTTP/parse error
   stops the batch; no self-scoring or extra attempt. No candidates from full
   success. For negative feedback invoke one Reflector leaf with the complete
   captured context and full role text; it returns a candidate JSON array.
   Do not pass its candidates to later Solvers. Empty proposals are valid.
4. After all five have captured successful judge calls, concatenate candidate
   arrays without rewriting them. Invoke one Evolver leaf with the complete
   batch candidates, their captured evidence and pre-batch library. Verify
   actual reads in its trace, then `compile` the operations. If zero candidates,
   the Evolver may return `[]`; preserve the library as an explicit no-op.
5. `export` the resulting library to `/app/submission/skill/`. Keep private
   contexts, candidates, operations, role traces/bindings and audit receipts
   under `/logs/agent/evo-harness/` (Harbor agent logs are exported). Use `seal-training` to produce `training-export.json` from the five contexts,
   candidates, decisions, pre-batch library and role audit requests. Every role
   record binds original traces, model/Session, material file paths, full role
   text and its sealed output. Store copies of role text/library in the private
   directory before delegation; all audited materials must be in that directory.
   The sealer records hashes and role provenance; do not hand-author a complete
   flag in place of its checks.
   Check all answers still match the judge-bound bytes. They are frozen once
   feedback was obtained. Do not overwrite failed learning with an empty Skill.
6. Wait for all native children, verify no active business writes remain and
   return. The adapter then collects cost/trace and shuts down; the host worker
   waits for Harbor verification/artifact export. Only the host Controller
   installs the package into test Target State and starts evaluation.

The whole batch shares one training root context/workspace/service; each leaf
has a scoped task conversation. This permitted delegation does not turn the
group into five independent Harbor trials. Test receives only the frozen
learned package through Agent State. The test image disables training judge;
verify that deployment with a model-free probe, not a parameter name alone.
