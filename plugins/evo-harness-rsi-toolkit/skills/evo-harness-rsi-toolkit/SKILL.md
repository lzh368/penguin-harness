---
name: evo-harness-rsi-toolkit
description: Run Evo-Harness on a frozen training batch and held-out Benchmark. Reflect on negative execution feedback, compile general and task-type skills once per batch, then freeze and compare through Penguin's native evaluation workers. Supports continuous GDPevo group training.
version: 2026.10.21.1
---

# Evo-Harness

Implement [Evo-Harness v2](https://arxiv.org/html/2608.15071v2), §3, §4.5–4.6,
Appendix F, and the [official release](https://github.com/A-EVO-Lab/a-evolve/tree/3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf).
Read [algorithm.md](references/algorithm.md) before learning. A frozen Solver
selects relevant external skills, executes tasks, reflects on negative feedback,
and an Evolver compiles the complete batch into ADD / MERGE / REVISE / SKIP
operations. Keep the final compilation; there is no validation score gate or
search for a winning candidate. Never learn from held-out tests.

## Before you start

You are the top-level Penguin Session. Require `run_subagent`. Use Penguin's
native workers, Target Agent State, snapshots, and Evaluation Center scoreboard.
Read [evaluation.md](references/evaluation.md), the byte-identical shared
single-cell protocol used by APE, OPRO, ACE and AWM. No extra `agent-evaluation`
installation is needed. Workers run Targets, call Harbor for Harbor cases, and
return the existing protocol YAML. Only you publish State and write scoreboard.
No script owns the experiment loop, chooses the next stage, or authors lessons.

Resolve from the request or an explicitly provided frozen configuration:

- `test_agent_id`: a separate Target, or `create: <id>`; never yourself.
- `benchmark_id`: frozen training Benchmark; `test_benchmark_id`: separate
  held-out Benchmark; complete case rosters and training batch boundaries.
- Fixed provider/model_id pair and thinking level for Solver and learning roles.
  Use the request's pair or your own Environment, never an unkeyed project default.
- One pass, one execution/feedback attempt per training task, one compilation per
  batch, `runs=1` per test State by default. Changes require predeclared protocol.
- Empty initial library by default; at most 5 general skills and 5 per topic,
  select at most 5 entries and 16,000 UTF-8 bytes per task. Byte budgeting and
  Solver-led semantic selection are declared adaptations, not paper token counts.
- Explicit current experiment spending authorization and total USD budget,
  including this Controller, workers, all learning roles, Targets and failures.
  A historical budget or a plugin quick start does not authorize new charges.

For GDPevo, resolve one whole group, its one continuous training case and all
five independent test cases. Read [group-training.md](references/group-training.md)
and the benchmark's `experiments/gdpevo/evo-harness/v1/README.md`. Do not treat
the five training members as five ordinary Harbor cells. Missing experimental
inputs are collected together before paid work. Preparation and model-free
checks may proceed. A UI `runs` value means frozen test repeats, not training
passes, proposals, or attempts.

## Initialize and freeze protocol

Use the Environment's App Data Dir, not a recursive project search:

```text
PROJECT_DIR = <app_data_dir>
TARGET = PROJECT_DIR/agents/<test_agent_id>
STATE = TARGET/agent_state
SNAPSHOTS = TARGET/snapshots
SLOT = STATE/skills/evo-harness-learned
OUT = <your session scratchpad>/evo-harness-rsi-toolkit/<experiment_id>
```

Create a requested fresh Target with `penguin agent create --agent-id <id>
--project-id <project_id> --json`, no unrelated plugins. Refuse an existing id
when `create:` was requested. Never clear another experiment's State. Follow
[state.md](references/state.md) for H0, the fixed reader, snapshots and publication.
Read [tools.md](references/tools.md) before calling the deterministic helper.

Record in `OUT/experiment.json`: method/Skill/source versions and hashes;
training and test rosters; batch order; all runtimes; attempt and repeat counts;
feedback source; budgets and in-flight reserves; concurrency; task-derived
timeouts and cleanup; immutable task, adapter, host and container bundle pins;
H0 State and learned-package hashes; source isolation probes; all adaptations.
For GDPevo require the published 5/5 split, one batch, one learning repeat, one
test repeat, training concurrency 1, and test concurrency 4 (maximum 8 if set).
The suggested first group is ERP 005, a development choice, not unbiased coverage.

Before dispatch write the operation id, request hash and your Session id to an
append-only method record; bind its native worker Session and Harbor job as soon
as available. Use `journal` to reserve/settle expenses; it never dispatches.
An existing reserved/running id means reconcile the actual Session/job/process,
not launch again. A formatting repair reuses the same worker's result. Apply
the shared protocol's retry rules only when evidence proves Target never ran.
Unknown cost remains reserved and reported as unknown. Reserve Controller and
worker overhead separately from trial costs; trial totals already include their
container children. This supervision is not a provider-enforced spending cap.

## Execute, learn and measure

1. Snapshot initialized H0. Run the complete held-out H0 matrix through native
   `run_subagent` evaluation workers using `evaluation.md`. Do not forward its
   tasks, answers, traces or scores to any learning role. Lock these artifacts
   outside training inputs. Wait for every worker and Harbor job to terminate.
2. Train only on declared training batches. For ordinary workspace cases,
   dispatch one evaluation worker per task serially, retain the public task,
   Solver trace and final artifact with its external score, then a native
   Reflector on negative executions. Supply the full role text from
   [roles.md](references/roles.md), task/attempt-bound materials and explicit
   model pair/thinking level in each `run_subagent` request. No private rubric
   reaches Reflector or Evolver. Workspace score=100 is complete success unless
   a different predicate was frozen before execution. For GDPevo use the
   controlled one-group delegation in `group-training.md` instead.
3. After the whole batch is complete, dispatch one native Evolver with all
   candidates and the pre-batch library. It authors the operations. Run
   `compile` to validate/apply them and `export` to produce one installable
   package. A successful record remains evidence but produces no candidate.
   Judge infrastructure failure blocks learning; it never becomes score zero.
   The library stays fixed throughout the batch. A later declared batch uses
   the updated library. Do not add passes, retry answers, or reflect-3.
4. Publish the exact exported package as a new Target State using `state.md`.
   Freeze all support files too. If its behavior and bytes equal H0, reuse the
   H0 matrix and label it `reused_equivalent_h0`; do not fabricate a final
   measurement or attribute retest variation to learning.
5. Otherwise measure the final State on exactly the H0 test roster × runs,
   through the unchanged single-cell protocol. Test containers/sessions are
   fresh, train judge disabled, memory and hooks off. Verify actual selection
   tool output, State/Skill hashes and role-read evidence from traces. Mere
   paths or self-reported reading are insufficient. No final test result
   affects compilation, retained version, or stopping.
6. Append each complete test matrix to that Benchmark's `scoreboard.yaml` in
   the shared schema, with the actual State version and runtime. Partial
   matrices have no mean/scoreboard entry; retain missing cells as diagnostics.
   Original verifier-defined missing/malformed-answer zeroes remain valid
   scores. Infrastructure failure has no score. Training group score is a
   training diagnostic, not a held-out evaluation.

Stop after the frozen batches and two test matrices (or H0 reuse), or on a
concrete integrity/infrastructure/budget blocker. Do not leave live children
behind. Report actual termination, per-task scores and coverage, delta only
when complete, learned procedures with scope and uncertainties, source and
injection hashes, State versions, all known/pending costs and failed attempts.
Separate synthetic offline verification from real model measurements. Say
plainly when there is no improvement or no paid experiment has run.
