# Add the WikiSkill RSI toolkit

- **Date:** 2026-10-12
- **Type:** feature
- **Scope:** `skills`, `core`, `cli`, `desktop`, `docs`
- **PR:** [lzh368/penguin-harness #1](https://github.com/lzh368/penguin-harness/pull/1)

[中文版](2026-10-12-wikiskill-rsi-toolkit.zh.md)

Added `wikiskill-rsi-toolkit` to the plugin library, with the `rsi-wikiskill` Skill for compiling training trajectories into a persistent Wiki and reusable skills.

## Experiment workflow

The Controller proposes one skill change per round. The evaluator scores each candidate on the complete validation set; only a strict improvement replaces the accepted skill set. Rejected candidates leave their Wiki findings available for later rounds. Final testing uses a frozen skill set and an isolated evaluator.

The toolkit includes local candidate management, score aggregation, a GDPevo Harbor result converter, and a campaign tool that freezes three independent replicas and recomputes paired test scores. Its experiment protocol separates the one-case preflight from those replicas and pins the train, validation and test partitions.

## Installation

Registered the package with the core, CLI and desktop plugin libraries and their documentation. The toolkit is installed on the Controller and is excluded from default Agent preinstallation. The Target receives the deployed skills without the Controller's Wiki or evaluator artifacts.
