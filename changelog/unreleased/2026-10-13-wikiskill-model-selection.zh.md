# 选择 WikiSkill 实验使用的模型

- **Date:** 2026-10-13
- **Type:** fix
- **Scope:** `skills`, `web`
- **PR:** [lzh368/penguin-harness #1](https://github.com/lzh368/penguin-harness/pull/1)

[English](2026-10-13-wikiskill-model-selection.md)

WikiSkill 接受用户选择的服务商、模型与思考等级。GDPevo profile 和 Harbor 结果转换使用这些配置，同时检查实测运行配置与实验一致。原 DeepSeek profile 仍可读取；其他选择可以使用独立 campaign 和 Agent 前缀，不替换已有结果。

评估中心的优化方法列表新增 WikiSkill。新实验草稿采用实际会话模型，执行前确定训练与验证输入，不继承其他实验的基线模型。每题重复次数与独立实验次数保持区分。

技能与插件快速开始将算法说明和可选的 GDPevo 工作流分开。子 Agent 调用显式传入各角色选择的模型配置。
