# Compile demonstrated behavior into WikiSkill

Date: 2026-10-20
Type: fix
Scope: wikiskill-rsi-toolkit
PR: [#2](https://github.com/lzh368/penguin-harness/pull/2)

[中文](2026-10-20-wikiskill-experience-compilation.zh.md)

WikiSkill can compile useful behavior already observed in training without requiring each proposal to invent a new action.

## Evidence review

Learning roles distinguish intermediate attempts, later corrections, and actual final submissions. Audits still reject false premises, unsupported rules, and access violations; fixed validation determines uncertain benefits.

## Experience replay

A frozen-experience replay protocol records the conditions for reusing training and baseline evidence, regenerates the Wiki and proposal, and distinguishes replay from independent training.
