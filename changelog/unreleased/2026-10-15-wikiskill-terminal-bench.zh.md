# 将 WikiSkill 实验绑定到 Terminal-Bench 任务清单

- **日期：** 2026-10-15
- **类型：** feat
- **范围：** `skills`

[English](2026-10-15-wikiskill-terminal-bench.md)

WikiSkill 新增外部 Terminal-Bench 实验准备和结果验收，执行仍复用 Penguin 现有 Harbor 适配器。准备工具读取安全任务元数据、核对固定源码字节，冻结明确的划分和运行参数。文档的 Software 探索方案选择 18 题、8/5/5 划分；这些是实验选择，不是算法要求或官方榜单协议。

schema-3 回执把源码版本、任务路径、完整阶段计划和实验清单绑定到实际运行轨迹。训练视图拒绝 smoke 和留出数据。独立的 spec campaign 逐格重算正式结果，从真实技能文件推导注入 hash，并要求两套 test 都在最终冻结之后运行。费用监督支持这种 campaign，保留持久软停止。已有 GDPevo profile 与回执行为不变。

改动仅在插件工具与文档，不新建 runner，不修改 Web/core。验证包括合成的执行证据/状态流程和真实官方元数据，未运行付费模型或真实容器。官方任务网络默认 public，`--allow-agent-host` 本身不能限制它，真实开跑前仍须验证隔离；调度还需考虑多服务任务资源及固定的 benchmark 原生 skills。
