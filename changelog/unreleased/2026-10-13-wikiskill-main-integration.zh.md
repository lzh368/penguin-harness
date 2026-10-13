# WikiSkill 接入由插件驱动的算法选择器

- **Date:** 2026-10-13
- **Type:** fix
- **Scope:** `web`, `core`, `skills`

[English](2026-10-13-wikiskill-main-integration.md)

将 WikiSkill 接入从插件清单发现工具包的算法选择器。快捷入口选择 `rsi-wikiskill`，算法列表显示对应语言的插件名称，无需静态注册表。

## 实验交接

WikiSkill 沿用独立优化提示词和当前 Session 的运行配置，支持由技能准备 Target。将 Controller 选作 Target 时会阻止生成草稿。其他工具包保留原有提示词和基线模型选择。
