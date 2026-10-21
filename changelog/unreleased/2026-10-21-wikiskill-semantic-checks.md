# Scope WikiSkill checks to the decisions they support

Date: 2026-10-21
Type: fix
Scope: wikiskill-rsi-toolkit
PR: [#2](https://github.com/lzh368/penguin-harness/pull/2)

[简体中文](2026-10-21-wikiskill-semantic-checks.zh.md)

WikiSkill `2026.10.21.4` consolidates the learning guide and role prompts around compiling executable problem-solving methods: conditions, required information, operations, intermediate results and their use in the task. One Skill may contain a complete sequence of related steps for one objective; it need not be a small check. The guide retains full evidence and boundary walkthroughs without requiring proof of improvement before validation.

It distinguishes retrieving records, interpreting business rules, selecting the population and computing aggregates. Query counts alone cannot establish completeness or effective filtering; schema conformance and internal arithmetic cannot establish semantic correctness. A check's stopping condition applies to that check, not automatically to the whole task.

Learning roles may cache supported definitions but must allow existing contradictions to reopen provisional assumptions. Unknown inputs do not become false by default. Experiment preparation records whether referenced examples and rules are actually supplied under the selected feedback protocol. No scoring, acceptance, model, split or frozen run is changed; these instructions do not establish a performance improvement.
