# Ground WikiSkill cross-field checks in supported relations

Date: 2026-10-21
Type: fix
Scope: wikiskill-rsi-toolkit
PR: [#2](https://github.com/lzh368/penguin-harness/pull/2)

[简体中文](2026-10-21-wikiskill-relation-evidence.zh.md)

WikiSkill `2026.10.21.1` distinguishes field definitions from evidence for relations between fields. A proposed mandatory answer change must justify its entity, time and decision prerequisites. Self-consistency under an inferred rule is not independent evidence that the rule is correct; useful observed checks remain eligible for compilation and validation.

`2026.10.21.2` applies this throughout the role prompts. Maintainers preserve source types and gaps in bounded views rather than treating self-check output as independent correctness evidence. Proposers verify Wiki interpretations against full training records and select a supported operation, with stopping and output boundaries. Auditors walk through the actual injected Skill and check the validity of its assertions; a failed self-written assertion does not always justify changing the answer. No extra candidate evaluations are introduced.

Reviewers must resolve missing prerequisites before staging instead of downgrading them to wording warnings. Access reviews separately report denied probes and actual disclosure. Replay documentation distinguishes development diagnostics from independent evaluation.

These are method and audit instructions, not a new enforcement mechanism or a demonstrated score improvement. Scoring, model settings, acceptance thresholds, experiment splits and frozen runs are unchanged.
