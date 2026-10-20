# Diagnose training behavior before proposing WikiSkill changes

Date: 2026-10-20
Type: fix
Scope: wikiskill-rsi-toolkit
PR: [#2](https://github.com/lzh368/penguin-harness/pull/2)

[中文](2026-10-20-wikiskill-learning-diagnosis.zh.md)

WikiSkill 2026.10.20.1 connects task requirements, observed inputs, actions and deliverables before selecting a skill change. Proposers walk through the proposed behavior using existing training evidence; later rounds distinguish rules that did not trigger, were not followed, or left an observed problem unresolved.

## Training inputs

Behavior views sample matching tool calls and results together within each session and reserve space for the task and final response. Missing or ambiguous counterparts are labelled. Raw traces, the 15,000-character view limit, sampling counts and independent validation acceptance remain unchanged.

## Learning guidance

Learning roles retain supported correct substeps in partially successful tasks, distinguish retrieval problems from reasoning or execution problems, and check whether a proposed rule changes the relevant action. Walkthroughs do not call the grader or establish performance. Existing experiments retain their frozen inputs; new learning runs use the new guidance and view renderer.
