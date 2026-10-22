# Explicit offline replay of historical training evidence

Use this entry only when the installed `replay.json` binds an authorized frozen
configuration whose protocol is `evo-harness-offline-replay-v1`. This is a new
learning run from historical training observations. The fresh learning children
are **not the original Solver**, do not inherit its conversation, and must not be
reported as original-session reflection or a new complete training experiment.
The normal full-RSI entry remains unchanged when this explicit binding is absent.

The main Penguin organizes all steps using native tools. Helpers prepare exact
requests, validate original evidence, compile child-authored decisions, and
account for cost; they never call a model, dispatch children, choose a next phase,
or author candidate lessons. Read `references/evaluation.md` before evaluators.

The authorized profile is five historical train sources, one fresh learning
child for each source in order, one independent Evolver, one frozen package, and
**one final-only test round of five cases, concurrency at most five**. Do not run
new training or a new baseline. A historical baseline is a labeled reference,
not a new measurement with this configuration. Stop and report after this round.
Do not resume a second round without a separately frozen authorization.

## Exact main-Session interface

The native hook supplies the absolute replay helper, current `trace_path` and
frozen config. Every main request is a JSON file containing `op`, `trace_path`
and the fields below; invoke it with one command:

```text
python3 -B <installed-toolkit>/scripts/replay.py <absolute-request.json>
```

1. `prepare-benchmark` generates only the held-out native Benchmark from the
   frozen config. It does not register or run a training Benchmark.
2. For each frozen train_id in order, request `prepare-role` with
   `role: learner` and that `task_id`. Invoke the returned `call` with native
   `run_subagent` unchanged. It includes a bound `prompt_file`, its SHA, explicit
   provider/model/thinking, and background completion. Wait for the original
   native completion notification, then `collect-role` with the same role/task.
   Do not launch a replacement child or inject further learning instructions.
3. After all five are collected, use `prepare-role` with `role: evolver` and no
   task_id. Invoke its returned native call once, await completion and use
   `collect-role` for the Evolver. `verify-learning` publishes only the immutable
   data package under this Session's replay directory. The compiler uses the
   original-version validated contexts; it does not pretend old source traces
   complied with new execution rules.
4. Use existing Penguin State and snapshot tools to create the separate frozen
   Target. Preserve an initial snapshot, install the package according to
   `references/state.md` (`package_state_files` places methods as native sibling
   Skills), and create the final snapshot. Call `prepare-freeze` with
   `initial_snapshot`, `final_snapshot`, `target_state`. Snapshot arguments are absolute paths to the native snapshot tar archives;
   versions are read from their frozen system_config.yaml. Freeze checks the actual
   Target filemap against the sealed native compilation. It does not update State
   or create snapshots for you.
5. Construct five standard evaluation tasks from `references/evaluation.md`,
   using the registered case IDs, exact frozen Target version, model/thinking
   and `run: 1`. Each task calls Harbor through the native evaluator. Register
   each exact native call using `worker-request` with `round: 1`, `case_id` and
   `call`; invoke the returned call unchanged. There are only five semantic
   slots, each usable once. Do not run Harbor from the main Session.
6. After all five native completion notifications, call `collect-round` with
   `round: 1`. The helper reads the actual worker YAML, binds each to its original
   Harbor trial and verifies the full frozen loading evidence. You do not write
   score cells yourself. A formatting-only correction, when admissible, uses
   `format-request` with the existing `subagent_id` and the returned exact native
   `input_subagent` call; it cannot add a tool call, answer, learning or retest.
7. Call `reconcile-costs`, then `verify-completion`. Write the normal report and
   scoreboard from accepted records, labeling this as fresh-session replay and
   final-only evaluation. The main Session's last request remains pending until
   external accounting after it stops; do not claim it costs zero.

`status` is read-only and returns existing records and reservations. Repeating a
preparation request reuses its exact immutable ticket; it does not authorize a
second dispatch. If a child is still running, wait for its original completion.
Strict profiles retain blocking evidence checks. In an explicitly frozen `audit_policy: record-only` profile, incomplete read/order/seal evidence is recorded as `audit_passed: false` and processing continues with the real child output; it is never certified as valid. Preserve errors and charges;
never turn an unexecuted/missing learner into a successful empty candidate list.

## Source and learning boundary

The frozen manifest names five immutable original contexts and an original-helper
validation receipt. Each child sees only its own prepared bundle: granted public
inputs, original final answer, complete authorized raw feedback, and original
trace prefixes ending at the complete feedback observation. Original terminal
traces, later reflections, candidates, compiled Skills, tests and hidden scoring
materials are not supplied. Original paths quoted in a bundle are provenance;
they are not permission to open those paths. The source bytes and cut positions
remain independently hash-bound. Preparation never changes original evidence.

Every child's complete prompt includes its content-quality guidance, exact
output path, native write_file template and ready-made bounded material
read requests. Each chunk is at most 32,000 source characters and its complete
JSON receipt is at most 60,000 UTF-8 bytes, requiring a deployment-verified `exec_command.maxOutputLength >= 64000` (the native default is 16000); escaped text may require smaller chunks. Execute those foreground read commands individually until all
chunks are actually observed. A path, parent summary, short receipt or truncated
output cannot substitute for the material. Requests are already on disk; do not
recreate them, use shell loops, inspect source code or calculate hashes.

A learner emits candidate memories only from negative feedback. A successful
source emits `[]`; a negative source can explicitly conclude `[]` after reading
and reasoning. Candidate structure and evidence kinds remain the normal Evo
`lesson`, `trigger`, `evidence`, `scope_hint` schema, with task/attempt namespace.
Tool IDs refer only to the visible original source prefix. Label facts versus
inference; aggregate feedback does not establish a particular loss cause.

The independent Evolver reads all five authorized bundles, the fresh candidates
and initially empty library. The same executable-method, counterevidence,
uncertainty and scope requirements from `roles.md` apply. It performs one batch
of ADD/MERGE/REVISE/SKIP, with evidence-based general/topic classification and
legal empty levels. There is no training retest or score-based acceptance loop.
The fixed seal checks the actual complete rendering before accepting decisions.
Only a structured recoverable unsealed draft error permits the child to rewrite
its own complete array using native write_file and retry that same seal. Once
sealed, no further tools or changes are allowed.

The frozen package keeps independent native Skills. Every test receives the same
Reader and current-context bodies in full and all general descriptions; general
bodies remain stored but are not loaded, following the pinned official CL path.
No selector or other group's Skill is used. Reader, manifest, all stored bodies
and metadata still share the 16,000 UTF-8 byte cap; no truncation.

## Accounting and limits of evidence

The frozen per-run $10 supervision includes main, fresh learners, Evolver,
evaluators, container Targets and failed attempts. Before dispatch, native usage
is read from the dedicated Project once per owning Session; container ownership
usage is added once per original trial. Unknown charges retain their reservations;
completed role reservations release only with original native completion, idle
state and fully priced usage. Pauses do not reset this ledger. The server monitor
provides the independently authorized running-call budget stop.

These are audited cooperative native execution constraints, not a new sandbox
for arbitrary hostile code. Complete evidence shows what was loaded, authored,
frozen and evaluated. Neither the new protocol nor offline tests prove a score
improvement. Test answers and detailed feedback are never supplied to learning.

Revision `2026.10.22.10` permits the explicitly authorized diagnostic policy. `complete` means artifacts or measurements were collected; `audit_passed` separately states their evidence audit. Unknown charges remain reserved. Actual child identity, immutable source boundaries, no repeated semantic dispatch/scoring, actual trial scores, and the frozen Skill contents remain required. Audit warnings do not authorize test feedback for learning or permit invented missing outputs. No full-RSI behavior changes.
