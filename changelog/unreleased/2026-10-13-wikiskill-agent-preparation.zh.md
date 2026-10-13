# 在 WikiSkill 实验开始时准备 Target

- **Date:** 2026-10-13
- **Type:** fix
- **Scope:** `skills`, `web`

[English](2026-10-13-wikiskill-agent-preparation.md)

执行 WikiSkill 的现有 Agent 通过 Penguin 原生命令准备专用 Target。新实验使用空 Target；续跑先核对记录中的身份和状态再复用。已有 Agent 和实验记录保留。Wiki 整理、技能提案、评测与审计角色使用临时子会话。

WikiSkill 优化对话框默认由技能创建 Target，也可选择与优化者不同的已有 Target。对话框只准备对话草稿，用户发送后由技能解析实验配置。其他优化方法保留原有 Agent 选择。
