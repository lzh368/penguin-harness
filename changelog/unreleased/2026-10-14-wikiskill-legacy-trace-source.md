# Accept the pinned Penguin runtime's benchmark session source

- **Date:** 2026-10-14
- **Type:** fix
- **Scope:** `skills`

[中文版](2026-10-14-wikiskill-legacy-trace-source.zh.md)

Schema-2 trace verification rejected the `benchmark` root-session source emitted by the pinned Penguin 0.2.13 Harbor runtime, although newer Penguin records these CLI sessions as `cli`. Accept the legacy marker only when adapter metadata names 0.2.13, retaining the existing CLI path and all model, State, trace-hash and audit checks. Raw traces are never rewritten; unsupported or missing sources remain errors. Synthetic tests cover receipt construction and conversion for the legacy source, rejection boundaries and unchanged evidence bytes. This does not upgrade the container bundle or certify a real deployment. Skill version: 2026.10.14.5.
