# Algorithm provenance and adaptations

Paper checked 2026-10-21 against arXiv **2608.15071v2** (§3, Algorithm 1, §4.5,
§4.6, Appendix F) and official release commit
`3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf` in
[A-EVO-Lab/a-evolve](https://github.com/A-EVO-Lab/a-evolve/tree/3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf).
Read implementation paths `evo_harness/cl_bench.py`, `swe_bench.py`,
`terminal_bench.py`, and launchers. The CL loading path below was rechecked
against that same local commit on 2026-10-22.

The paper formalizes frozen solver weights, relevant selection under a budget,
failure/negative-feedback reflection into lesson/trigger/evidence/scope_hint,
then batch compilation with ADD/MERGE/REVISE/SKIP into general and topic skills.
Reflection is not final curation. Do not give the reflecting Solver extra library material to
compare against. The Evolver receives both candidates and existing entries.
The learning product is a coherent method for future tasks: observable need,
required information, operations, result and use. Preserve effective operations
while addressing supported gaps; neither a reminder list nor an invented rule
deduced from a low score serves that purpose.

The release has benchmark-specific vocabulary: CL proposals NEW/ENHANCE/NONE,
curation ACCEPT/MERGE/SKIP, and general NEW/UPDATE/DELETE. The actual CL path is
`solve_raw → _process_one_task → _propose_in_context`: solve_raw retains the
system prompts, user/assistant messages, client and model_id; the proposal
function copies those messages and appends feedback to the same Solver context.
It is not a fresh Reflector Agent. This version preserves that continuity with
one native Solver run through scoring, full feedback observation and reflection,
then captures the terminal trace. No second model or reconstructed conversation
is used. The negative-feedback predicate and exactly-one judge are the frozen
GDPevo protocol; the official launcher's transport retries/rephrased feedback
are not additional permissions here.

CL also supplies existing context-skill descriptions at proposal time. This
adaptation follows the paper's separation from final curation: the Solver keeps
already-seen pre-batch guidance but receives no extra library retrieval during
reflection. The independent Evolver performs batch curation using the paper's
explicit four-operation formulation. These differences are recorded, not a
claim of byte-for-byte reproduction of one benchmark launcher. The CL general curator's repeated-
context threshold is not imposed on localized topic rules. Useful enterprise
rules may be supported by a single authorized training task.

Paper Appendix F uses batch size 16, up to 5 general and 5 skills per topic,
and Sonnet 4.5 selection; release launchers use Bedrock. This does not make
per-task selection mandatory in every official execution path. In the pinned
CL implementation, `_process_one_task` calls `solve_raw`, which calls
`_build_system_prompt` directly. That function loads all matching current-context
Skill bodies and all general Skill descriptions; it does not call the separately
defined `_select_skills`. The general-body claim in its docstring differs from
the executed description-only loop. See `evo_harness/cl_bench.py` lines
1209, 856 and 908–940, especially 917–929.

This version's full-group loading follows that official context-level approach.
Combining the fixed Reader and every retained general/topic rule in one complete
SKILL.md is this GDPevo/Penguin delivery arrangement, not a claim that the release
uses that file layout. The private two-level library and four operations remain.
Every final task reads the same complete bytes; H0 uses the same format with
empty rules. There is no runtime index, selector or selected-entry count limit.
The whole file, including Reader, metadata and framing, fits the fixed 16,000
UTF-8 byte budget; it is never clipped at injection. Penguin uses native configured
models and provider-default sampling. Record every model and this adaptation;
the byte budget is not an exact token budget.

GDPevo adaptation: all five training members in one continuing Harbor task,
one batch compilation before container teardown, frozen transfer to five new
test containers. This experiment profile trains and freezes first, then measures
all five H0 cells concurrently, then all five final cells concurrently; it is
not a new default order for ordinary RSI Benchmarks. The native control hook
checks explicit Penguin role/worker requests and complete evidence, while the
main Penguin still makes and performs every delegation and phase decision.
This is inspired by the paper's train-split transfer setting,
not online adaptation on the test stream or a replication of published scores.
Only normalized score/correct is available; no gold, rubric explanation or
reliable error attribution. This resembles sparse external feedback, not the
paper's richer diagnostic condition. The feedback ablation finds model self-
judgment unreliable; do not replace a failed judge with self-scoring.

The role guidance distinguishes explicit task definitions, observed records and
actions, model interpretations, and scoring feedback. A candidate describes the
original operation, its final gap or useful substep, and the proposed change
with support. Neither low score nor success validates a local explanation;
a proposed repair is not a demonstrated gain. The Evolver reconciles candidates
with all authorized contexts, including counterevidence, without creating
positive-source candidates or retesting training answers.

Compilation preserves certainty and field/entity/time/granularity scope in the
actual trigger, scope and body. Triggers describe applicability, not a retrieval
decision. Reading all rules does not establish their preconditions: check the
relevant task facts and authorized records before applying a procedure.
Methods may use supported logical
inference and local conventions from one training task. Unknown benefit is
allowed, but missing premises require bounded evidence checks, narrower scope
or SKIP, not unsupported thresholds, labels or defaults. Temporary IDs, dates,
amounts and answers remain in private records. Both levels are considered.
Either level may remain empty; limits are ceilings. The existing reason records a
training-record walkthrough, not another role, execution or scoring round.

No Wiki database, one-candidate edit loop, strictly-improving acceptance gate,
TRACE contrastive trajectories, SkillAdam edit budget, or test-driven selection.

Revision 2026.10.22.3 replaces 2026.10.22.2's separate Reflector Session.
Earlier frozen runs and their verdicts remain historical evidence; they are
not relabelled as having used in-session reflection. Post-score tool boundaries
are verified from the original paired trace and bound helper receipts. Native
leaves do not run root hooks, so this is cooperative execution acceptance, not
an arbitrary-code runtime sandbox. Violations block compilation and testing.

Revision 2026.10.22.4 makes the original Solver's execution interface self-contained
before scoring: candidate field types, evidence references and authorized input
hashes are supplied with its native task. This adds no lesson, feedback, memory
retrieval or model call. The phase boundary permits ordinary pre-judge preparation
without treating it as a post-judge operation; actual post-judge access stays
restricted. R13's failed runs retain their original evidence and verdicts.
