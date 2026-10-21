# 核实 WikiSkill 跨字段检查的关系依据

Date: 2026-10-21
Type: fix
Scope: wikiskill-rsi-toolkit
PR: [#2](https://github.com/lzh368/penguin-harness/pull/2)

[English](2026-10-21-wikiskill-relation-evidence.md)

WikiSkill `2026.10.21.1` 区分字段定义与字段间关系的依据。强制修改答案的规则须核实实体、时间及决策前提；基于自拟规则的内部自洽不等于规则正确。训练中已有的有效检查仍可编译为候选并接受验证。

审计须在 stage 前处理缺失的关系前提，不能仅降成措辞 warning。访问审计分别记录被拒探测与实际信息泄漏；回放文档明确开发诊断与独立评测的区别。

本次修改是方法与审计指导，不是新增强制隔离机制，也不代表已实测提分。不改变评分、模型、接受阈值、数据划分或冻结实验。
