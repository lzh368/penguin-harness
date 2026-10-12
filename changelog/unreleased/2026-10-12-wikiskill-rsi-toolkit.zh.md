# 新增 WikiSkill RSI 工具包

- **Date:** 2026-10-12
- **Type:** feature
- **Scope:** `skills`, `core`, `cli`, `desktop`, `docs`

[English](2026-10-12-wikiskill-rsi-toolkit.md)

在插件库中新增了 `wikiskill-rsi-toolkit`，提供 `rsi-wikiskill` 技能，将训练轨迹整理为持久 Wiki 与可复用技能。

## 实验流程

Controller 每轮提出一个技能修改。评测角色在完整验证集上评估候选；只有分数严格提升，才替换已接受的技能包。候选被拒绝后，其 Wiki 经验仍可供后续轮次使用。最终测试使用冻结的技能包与隔离的评测角色。

工具包附带本地候选管理、分数聚合、GDPevo Harbor 结果转换，以及冻结三次独立实验并复算配对测试成绩的 campaign 脚本。实验协议区分单题预检与正式重复实验，并固定训练、验证和测试分区。

## 安装

将插件登记到 core、CLI 和 desktop 的插件库及对应文档。工具包安装到 Controller，不随默认 Agent 预装。Target 接收部署的技能，不接收 Controller 的 Wiki 或评测产物。
