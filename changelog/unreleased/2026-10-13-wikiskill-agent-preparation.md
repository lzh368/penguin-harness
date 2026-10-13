# Prepare WikiSkill Targets when an experiment starts

- **Date:** 2026-10-13
- **Type:** fix
- **Scope:** `skills`, `web`
- **PR:** [lzh368/penguin-harness #1](https://github.com/lzh368/penguin-harness/pull/1)

[中文版](2026-10-13-wikiskill-agent-preparation.zh.md)

An existing Agent running WikiSkill prepares a dedicated Target through Penguin's native commands. New experiments start with an empty Target; resuming checks the recorded identity and state before reuse. Existing Agents and experiment records are preserved. Maintainer, proposer, evaluator and auditor roles use temporary subagent sessions.

The WikiSkill optimization dialog defaults to letting the Skill create its Target. Users can still select an existing Target distinct from the optimizer. The dialog only prepares a conversation draft; the Skill resolves the experiment configuration after the user sends it. Other optimization methods keep their existing Agent selections.
