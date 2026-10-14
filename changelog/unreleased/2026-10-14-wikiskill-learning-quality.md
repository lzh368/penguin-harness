# Improve WikiSkill learning inputs and scoped proposals

- **Date:** 2026-10-14
- **Type:** fix
- **Scope:** `skills`

[中文版](2026-10-14-wikiskill-learning-quality.zh.md)

WikiSkill now prepares bounded task/action/result views from complete audited training batches, preserving original traces and source bindings instead of truncating system metadata. Learning prompts require traceable observations, applicable conditions and counterevidence for proposed rules, with semantic review before validation. The batch update and strict historical-best acceptance rule are unchanged; these are adaptation choices, not a measured performance gain. Skill version: 2026.10.14.3.
