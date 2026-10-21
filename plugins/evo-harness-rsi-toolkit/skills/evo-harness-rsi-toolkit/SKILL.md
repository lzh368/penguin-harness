---
name: evo-harness-rsi-toolkit
description: Run Evo-Harness with frozen Solver settings, negative-feedback reflection and batch compilation of general and task-type skills. Use native Penguin workers, State, snapshots and evaluation; the continuous GDPevo profile adds checked native delegation and export acceptance.
version: 2026.10.21.7
---

# Evo-Harness

Implement [Evo-Harness v2](https://arxiv.org/html/2608.15071v2), §3,
§4.5–4.6 and Appendix F, with the declared adaptations in
[algorithm.md](references/algorithm.md). The
[official release](https://github.com/A-EVO-Lab/a-evolve/tree/3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf)
is pinned for provenance. A frozen Solver selects relevant skills under a fixed
budget; negative executions produce candidates; one Evolver compiles each
complete batch with ADD / MERGE / REVISE / SKIP at general and topic levels.
Keep the final compilation. There is no score-based candidate search or
strictly-improving acceptance gate. Never learn from held-out tests.

## Before you start

You are the main Penguin Session and must have run_subagent. Read
[evaluation.md](references/evaluation.md), the unchanged shared RSI single-cell
protocol. You organize the entire experiment: native evaluation children run
Targets and call Harbor for Harbor cases, then you collect results, publish
State and write the shared scoreboard. No external script or Codex stage
instruction is needed. Deterministic method helpers validate the operation
you requested; they never dispatch a role, choose a phase or author a lesson.

Resolve together, from the request or frozen configuration:

- A separate test_agent_id (or create: <id>), training benchmark_id, held-out
  test_benchmark_id, complete case rosters and training batch boundaries.
- One explicit provider/model_id/thinking_level for all roles unless the user
  froze a different protocol. Never fall back to a project default.
- One pass, one answer/feedback attempt per training task and one compilation
  per batch; runs=1 per test State by default. Freeze any changes before work.
- Initially empty library; at most 5 general and 5 entries per topic, at most
  5 selected entries and 16,000 UTF-8 bytes per task. Keep selection budgets
  identical in H0 and final. Record byte budgeting as an adaptation.
- Current experiment USD authorization covering the main Session, every
  worker/role/Target and failed attempt. Historical approval is insufficient.

Preparation and offline checks may proceed before authorization; paid calls
must wait. A UI runs count controls test repeats, not training attempts.

## Initialize the experiment

Resolve PROJECT_DIR from Environment App Data Dir; Target State is
PROJECT_DIR/agents/<test_agent_id>/agent_state. Keep all method evidence in
your Session scratchpad, outside Target State. Do not recursively search
projects or read credentials. Read [state.md](references/state.md) and
[tools.md](references/tools.md). A requested create: id must be fresh; do not
clear another experiment's State.

Freeze versions and source hashes, runtime, training/test rosters, attempt and
repeat counts, selection limits, feedback predicate, phase order, concurrency,
task timeout and compilation reserve, deployment pins, H0 State/package hashes,
budget reserves and all adaptations. Use the native State and snapshot format.
The Solver's models, instructions and tools stay fixed; only the learned slot
and a final State version may change.

For the continuous GDPevo profile, also read
[group-training.md](references/group-training.md) and
[control.md](references/control.md), plus the pinned benchmark protocol.
This profile is one whole group: five sequential training members in one Harbor
task, one batch compilation, then freezing, then all five H0 tests concurrently,
then all five final tests concurrently. Do not run H0 tests before training
under this profile. A valid equivalent H0 reuses its one complete matrix.
The main Penguin configures the native guard and registers each exact worker
call before invoking run_subagent itself. The guard does not run the experiment.
The profile does not impose GDPevo's task count, phase order or training guard
on ordinary RSI Benchmarks.

## Execute the method

For ordinary RSI, use the declared Benchmark protocol and the shared native
evaluation workers; the default is H0 measurement before training. For GDPevo,
use the training-first sequence above and the exact delegation in
group-training.md. In both settings:

1. Preserve H0 and its fixed empty reader. Keep test tasks, outputs, traces and
   scores outside every learning role's materials. Native workers evaluate
   one case × run, with explicit runtime and the correct State version.
2. Execute training tasks in the frozen order with the pre-batch library fixed.
   For ordinary workspace cases use capture-evaluation on the original worker
   result, public inputs and actual Target trace. A score of 100 means complete
   success unless another predicate was frozen. The continuous GDPevo profile
   uses canonical judge receipts and capture inside the training container.
3. For each completed negative execution call a fresh native Reflector with
   the full role instructions and task-bound context. Successful executions
   remain evidence and produce no candidate. Infrastructure, missing evidence
   or judge failure stops learning; it never becomes a zero or an empty update.
4. After every task in the batch is complete, call one native Evolver with all
   candidates, contexts and the pre-batch library. Even zero candidates requires
   its real return and explicit empty decisions. Use compile and export to
   validate its operations and produce the complete learned package. Do not
   rewrite a role's content or generate executable support files.
5. Verify the exported package and publish its exact bytes following state.md.
   Freeze it before final evaluation. For GDPevo, every test authorization
   reruns verify-export and verify-freeze; missing seal, partial learning or
   altered evidence blocks tests. Only a fully verified no-op can reuse H0.
6. Evaluate the complete frozen final roster × runs through the same native
   protocol, or explicitly record reused_equivalent_h0. A partial matrix has
   no mean or scoreboard entry. Preserve valid verifier-defined zero scores;
   infrastructure failures have no score. Write the existing scoreboard schema
   with actual versions and runtime. Training scores remain diagnostics.

## Recovery, stopping and reporting

Before dispatch reserve its semantic operation id, request hash and cost; bind
the returned native Session/job. Reconcile an existing operation instead of
launching another. Unknown costs remain reserved. Trial totals already include
their container roles: do not count those children twice. Host and worker
overhead are separate. This accounting is not a provider-enforced spending cap.

In ordinary RSI follow the shared protocol's narrowly evidenced formatting or
pre-start recovery rules. The guarded continuous profile permits empty child polls and a registered,
fixed evaluator formatting repair that resends only its existing result. It
forbids a second semantic dispatch or free-form learning-role steering; retain
an invalid/ambiguous attempt as method_failed. Evaluator pre-start recovery
still requires the unchanged shared protocol’s evidence that no Target ran. Never turn failed learning
into reused H0, invent missing cells, or silently replace an interrupted package.

You decide the next authorized stage and stop after the frozen batches and
complete matrices, or on a concrete integrity/infrastructure/budget blocker.
Wait for all native children and Harbor jobs to terminate. Report coverage,
actual per-task scores, complete-matrix delta, learned procedures and scope,
State/source/package hashes, known and pending costs, and failures. The main
Session's own final request cost remains pending until native accounting settles
after it stops. Do not claim improvement, full cost settlement or a paid result
without evidence.
