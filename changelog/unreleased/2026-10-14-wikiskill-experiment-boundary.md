# Separate WikiSkill from benchmark-specific experiment plans

- **Date:** 2026-10-14
- **Type:** feat
- **Scope:** `skills`

[中文版](2026-10-14-wikiskill-experiment-boundary.zh.md)

WikiSkill now distinguishes its learning algorithm, an external experiment plan, and the execution adapter. A new offline helper checks and freezes explicit task splits, the shared Wiki/Skill learning unit, models, scoring and budgets. Its output is a planning record, not proof of source integrity or runtime readiness. Existing GDPevo profiles, campaign tools and result contracts retain their behavior. Terminal-Bench task selection and execution adaptation remain separate work; no dataset size, split ratio or repeat count becomes an algorithm requirement. Skill version: 2026.10.14.4.
