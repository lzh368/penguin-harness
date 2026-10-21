# Bind WikiSkill experiments to Terminal-Bench task manifests

- **Date:** 2026-10-15
- **Type:** feat
- **Scope:** `skills`

[中文版](2026-10-15-wikiskill-terminal-bench.zh.md)

WikiSkill can now prepare an external Terminal-Bench experiment and verify its results through Penguin's existing Harbor adapter. The preparation tool reads safe task metadata, hashes the actual pinned source, and freezes an explicit split and runtime. The documented Software exploration uses 18 tasks with an 8/5/5 split; these are experiment choices, not algorithm requirements or the official leaderboard protocol.

Schema-3 receipts bind the source revision, task path, complete phase plan and experiment spec to the actual runtime traces. Training views reject smoke and held-out data. A separate spec campaign verifies every formal cell, derives injection hashes from the actual Skill files, and requires a final freeze before either held-out test. Budget supervision accepts this campaign type while preserving persistent soft stops. Existing GDPevo profiles and receipt behavior remain unchanged.

This adds plugin tooling and documentation, not a new runner or a Web/core change. Validation covers synthetic execution/receipt/state workflows and the real official metadata; no paid model or container trial was performed. Official task networking is public by default: operators must verify real isolation before running, since `--allow-agent-host` alone does not restrict public networking. Resource scheduling must also account for multi-service tasks and fixed benchmark-native skills.
