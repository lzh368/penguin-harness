# 校验 WikiSkill 运行证据与副本接力

- **日期：** 2026-10-14
- **类型：** 修复
- **范围：** `skills`

[English](2026-10-14-wikiskill-runtime-evidence.md)

WikiSkill 新增维护在插件内的回执生成器，从所有已导出的 Target root trace 分片提取实际注入、模型和路径，生成 schema-2 证据；转换时重新读取原始 trace，避免仅靠预期值与观测声明相等判断成功。旧 schema-1 回执仅可显式加 `--allow-legacy` 做历史复核。容器文件部署、导出完整性与隔离仍须独立审计。

新增持久化监督门和 `campaign.py verify-replica`。预算停止跨进程重启保持，费用未知暂停新调度，使用原生 `sessionId` 绑定根 Controller，收尾失败阻止下一副本。操作者须将现有 relay 接入监督门；安装技能不会改变正在运行的监督器。在途容器费用尚未结算，费用阈值仍是软阈值。

不改变算法、严格验证接受规则、用户模型选择或已有冻结 profile。新版在独立检出和 smoke 实验中接入，保留旧回执与结果。验证使用合成 Python 回归测试和 Penguin 插件加载检查，不调用付费模型或容器。
