# WikiSkill 支持如实编译已有做法

Date: 2026-10-20
Type: fix
Scope: wikiskill-rsi-toolkit
PR: [#2](https://github.com/lzh368/penguin-harness/pull/2)

[English](2026-10-20-wikiskill-experience-compilation.md)

WikiSkill 允许把训练中已经执行的有用步骤编译成技能，不再要求每个候选都发明新动作。

## 证据核对

学习角色需区分中间尝试、后续修正和实际最终交付。审计继续拒绝虚假事实前提、无依据的规则及访问越界；收益未知交给固定验证集判断。

## 经验回放

新增固定经验回放协议：记录训练和基线的复用条件，重新生成 Wiki 与候选，明确与独立训练的区别。
