# WikiSkill integration with the plugin-driven method picker

- **Date:** 2026-10-13
- **Type:** fix
- **Scope:** `web`, `core`, `skills`, `landing`
- **PR:** [lzh368/penguin-harness #1](https://github.com/lzh368/penguin-harness/pull/1)

[中文版](2026-10-13-wikiskill-main-integration.zh.md)

Integrated WikiSkill with the method picker that discovers toolkits from plugin manifests. Its quick start selects `rsi-wikiskill`, and its localized plugin name appears in the method list without a static registry.

Added WikiSkill and its paper link to the landing page's algorithm catalogue.

## Experiment handoff

WikiSkill keeps its own optimization prompt, the current Session's runtime and an optional Target prepared by the Skill. Selecting the Controller as the Target blocks the draft. Other toolkits retain their existing prompts and baseline runtime selection.
