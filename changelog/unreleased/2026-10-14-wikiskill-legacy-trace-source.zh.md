# 兼容固定 Penguin 运行时的 benchmark 会话来源

- **Date:** 2026-10-14
- **Type:** fix
- **Scope:** `skills`

[English](2026-10-14-wikiskill-legacy-trace-source.md)

固定的 Penguin 0.2.13 Harbor 运行时会输出 `benchmark` 根会话来源，新版 Penguin 则将这类 CLI 会话记为 `cli`；schema-2 轨迹校验此前错误拒绝了旧来源。现在仅在 adapter metadata 标明 0.2.13 时接受旧标记，保留 CLI 路径及原有模型、State、轨迹 hash 与审计检查。不重写原始轨迹，仍拒绝未知或缺失的来源。合成测试覆盖旧来源的回执生成、成绩转换、拒绝边界和证据字节保留；没有升级容器 bundle，也不代表完成真实部署验证。技能版本：2026.10.14.5。
