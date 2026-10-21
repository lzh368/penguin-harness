# 分离 WikiSkill 算法与具体 benchmark 实验方案

- **Date:** 2026-10-14
- **Type:** feat
- **Scope:** `skills`

[English](2026-10-14-wikiskill-experiment-boundary.md)

WikiSkill 现明确区分学习算法、外部实验方案与执行适配。新增离线工具，校验并保存显式的任务划分、共享 Wiki/Skill 的学习单元、模型、评分与预算。输出仅为实验规划记录，不证明数据来源真实或运行环境已就绪。既有 GDPevo profile、campaign 工具及结果协议保持原有行为。Terminal-Bench 的题目选择与运行适配仍须独立完成，不把具体题量、划分比例或重复次数当成算法要求。技能版本：2026.10.14.4。
