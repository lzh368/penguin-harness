# WikiSkill integration with the plugin-driven method picker

- **Date:** 2026-10-13
- **Type:** fix
- **Scope:** `web`, `core`, `skills`

[中文版](2026-10-13-wikiskill-main-integration.zh.md)

Integrated WikiSkill with the method picker that discovers toolkits from plugin manifests. Its quick start selects `rsi-wikiskill`, and its localized plugin name appears in the method list without a static registry.

## Experiment handoff

WikiSkill keeps its own optimization prompt, the current Session's runtime and an optional Target prepared by the Skill. Selecting the Controller as the Target blocks the draft. Other toolkits retain their existing prompts and baseline runtime selection.
