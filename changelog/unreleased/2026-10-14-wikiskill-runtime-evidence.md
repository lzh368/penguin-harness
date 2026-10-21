# Verify WikiSkill runtime evidence and replica handoffs

- **Date:** 2026-10-14
- **Type:** fix
- **Scope:** `skills`

[中文版](2026-10-14-wikiskill-runtime-evidence.zh.md)

WikiSkill's maintained receipt builder now reads every exported Target root trace shard and binds its actual injection, model and paths into schema-2 evidence. Conversion recomputes those facts instead of trusting matching expected/observed declarations. Historical schema-1 receipts require explicit `--allow-legacy`; container deployment measurements, export completeness and isolation still need independent audits.

The plugin also supplies a persistent supervisor gate and a `campaign.py verify-replica` command. Budget stops survive restarts, unknown costs block new work, Controller routing uses the pinned native `sessionId`, and failed wrapup cannot advance a replica. Operators must connect their relay to the gate; installing the skill does not change an already-running supervisor. Spending thresholds remain soft because in-flight container usage is settled later.

Independent review added checks for stale over-budget observations, queued/background work in idle child sessions, disappearing launch records, and inconsistent State versions across phases. Decision replay also verifies the recorded previous best score. Synthetic acceptance, rejection, no-action and full-score early-stop workflows exercise the complete local protocol.

The algorithm, strict validation acceptance rule, model selection and existing frozen profiles stay unchanged. Upgrade in an independent checkout and smoke experiment; preserve old receipts and results. Validation uses synthetic Python regression tests and Penguin's plugin-loader checks, without paid models or containers.
