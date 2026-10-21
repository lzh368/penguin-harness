# Require concrete operations in WikiSkill proposals

- **Date:** 2026-10-20
- **Type:** fix
- **Scope:** `wikiskill-rsi-toolkit`
- **PR:** [#2](https://github.com/lzh368/penguin-harness/pull/2)

[中文版](2026-10-20-wikiskill-action-review.zh.md)

WikiSkill 2026.10.20.2 required a concrete training example connecting the original operation, proposed operation and a checkable intermediate result. It added synthetic examples for calculation and artifact checks.

## Proposal review

The learning guidance distinguished missing operational evidence from uncertain performance. Reviews must request corrections for unsupported field mappings, invented source precedence or misleading evidence; an actionable, supported hypothesis can still proceed to validation without a prior successful score.

## Experiment boundaries

The change preserved the proposal JSON schema, acceptance rule and scoring. Existing experiments retain their frozen method version. The paper reference identified the published agent prompts and the training-label difference in the score-only adaptation.
