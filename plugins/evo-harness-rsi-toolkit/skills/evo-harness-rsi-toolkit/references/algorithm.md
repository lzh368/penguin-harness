# Algorithm provenance and adaptations

Verified 2026-10-21 against arXiv **2608.15071v2** (§3, Algorithm 1, §4.5,
§4.6, Appendix F) and official release commit
`3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf` in
[A-EVO-Lab/a-evolve](https://github.com/A-EVO-Lab/a-evolve/tree/3c7c7b8c62a3f07da1d4407aa03f0ca079954bcf).
Read implementation paths `evo_harness/cl_bench.py` (proposal, curator,
general curator, selector), `swe_bench.py`, `terminal_bench.py`, and launchers.

The paper formalizes frozen solver weights, relevant selection under a budget,
failure/negative-feedback reflection into lesson/trigger/evidence/scope_hint,
then batch compilation with ADD/MERGE/REVISE/SKIP into general and topic skills.
Reflection is not final curation. Do not give the Reflector the library to
compare against. The Evolver receives both candidates and existing entries.

The release has benchmark-specific vocabulary: CL proposals NEW/ENHANCE/NONE,
curation ACCEPT/MERGE/SKIP, and general NEW/UPDATE/DELETE. Its CL reflector can
see existing context skills, unlike the separation stated in paper §3.2.
This implementation follows the paper's explicit four-operation formulation
and isolated reflection, records the discrepancy, and does not claim byte-for-
byte reproduction of one benchmark launcher. The CL general curator's repeated-
context threshold is not imposed on localized topic rules. Useful enterprise
rules may be supported by a single authorized training task.

Paper Appendix F uses batch size 16, up to 5 general and 5 skills per topic,
and Sonnet 4.5 selection; release launchers use Bedrock. Penguin uses native
configured models and a Solver semantic selection step over compact metadata,
with a deterministic count/UTF-8-byte guard. Sampling follows provider defaults.
Record every model and the byte budget; do not call it an exact token budget.

GDPevo adaptation: all five training members in one continuing Harbor task,
one batch compilation before container teardown, frozen transfer to five new
test containers. This is inspired by the paper's train-split transfer setting,
not online adaptation on the test stream or a replication of published scores.
Only normalized score/correct is available; no gold, rubric explanation or
reliable error attribution. This resembles sparse external feedback, not the
paper's richer diagnostic condition. The feedback ablation finds model self-
judgment unreliable; do not replace a failed judge with self-scoring.

Scope-bearing entries must state the trigger, data to inspect, query/join/
calculation/decision procedure, expected result and where to use it. Preserve
stable company conventions while abstracting temporary IDs, amounts and final
answers. Keep observed facts separate from inference. Neither low total score
nor partial success locates a particular mistaken premise. Analyze the final
delivery, not an already repaired intermediate error. Resolve known false
premises before export; uncertain but plausible lessons retain explicit scope.

No Wiki database, one-candidate edit loop, strictly-improving acceptance gate,
TRACE contrastive trajectories, SkillAdam edit budget, or test-driven selection.
