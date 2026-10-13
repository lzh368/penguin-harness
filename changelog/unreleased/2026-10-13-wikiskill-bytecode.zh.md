# 防止 WikiSkill 实验脚本生成二进制插件文件

- **Date:** 2026-10-13
- **Type:** fix
- **Scope:** `skills`
- **PR:** [lzh368/penguin-harness #1](https://github.com/lzh368/penguin-harness/pull/1)

[English](2026-10-13-wikiskill-bytecode.md)

直接用 Python 执行 `campaign.py` 时，导入辅助模块可能在技能目录生成 `.pyc` 文件。内置插件加载器拒绝二进制技能资源，导致后续 Agent 操作加载插件库时失败。现在脚本在导入辅助模块之前禁止写入字节码。

已经生成的 `.pyc` 文件仍需从对应技能的 `__pycache__` 目录中清理，保留 Python 源码和实验数据。工作区启动器也为进程内导入、辅助脚本命令和服务子进程禁止写入字节码。
