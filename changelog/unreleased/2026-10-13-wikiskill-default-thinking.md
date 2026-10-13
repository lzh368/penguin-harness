# WikiSkill profiles default to the highest thinking level

- **Date:** 2026-10-13
- **Type:** feature
- **Scope:** `skills`

[中文版](2026-10-13-wikiskill-default-thinking.zh.md)

A GDPevo profile built without an explicit thinking level now runs all six roles at `thinking_level: xhigh`. The default is raised in both places that carry one: the command line's `--thinking` fallback and `build_profile`'s fallback runtime.

An explicit `--thinking` (or `--role-models`) still wins, and a profile that already exists keeps the level it was frozen with — the default only decides what a newly created profile starts from. Because an experiment's runtime is frozen inside its `profile.json`, existing experiments are unaffected; changing one means rebuilding its configuration.
