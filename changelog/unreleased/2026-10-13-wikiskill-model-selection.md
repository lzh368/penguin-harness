# Choose the model used by a WikiSkill experiment

- **Date:** 2026-10-13
- **Type:** fix
- **Scope:** `skills`, `web`
- **PR:** [lzh368/penguin-harness #1](https://github.com/lzh368/penguin-harness/pull/1)

[中文版](2026-10-13-wikiskill-model-selection.zh.md)

WikiSkill accepts the user's provider, model and thinking level. The GDPevo profile and Harbor result conversion use those selections while checking that the measured runtime matches the experiment. The original DeepSeek profile remains readable; another selection can use a separate campaign and Agent prefix without replacing existing results.

The Evaluation Center offers WikiSkill in the optimization method selector. Its draft uses the actual conversation model when starting a new experiment, resolves training and validation inputs before execution, and does not inherit another experiment's baseline model. Per-case repetitions remain separate from independent experiment repetitions.

The skill and plugin quick start describe the algorithm independently of the optional GDPevo workflow. Subagent calls carry each role's selected runtime explicitly.
