# 来源、复现边界与工程约定

> 新实验最终测试按 [final-test.md](final-test.md) 执行：冻结 `reuse_unchanged` 时，最终业务技能为空且有效状态/评测条件一致，只做 test-H0 并保存 skip 回执；下文“两套 test”对有技能变化或预先选择 `repeat` 的实验成立。旧冻结实验保持旧协议。

论文：Liyan Tang 等，**WikiSkill: Compiling Agent Experience into Persistent Knowledge for Skill Evolution**，2026-08-27，Google Research；Tu Vu 同时署名 Virginia Tech。

- 摘要：https://arxiv.org/abs/2608.27454
- 全文：https://arxiv.org/html/2608.27454v1
- PDF：https://arxiv.org/pdf/2608.27454

本文档与角色指令是独立改写，不是作者原始 prompt 或官方实现。

## 原文已有的具体学习指导

论文不只给出三层架构。Appendix E.2 / E.3 公布了 Maintainer、Proposer 的系统提示：从实际操作中分析原因，比较成功与失败记录，将具体操作整理为简短模式页；提案前查看 Wiki 与拒绝历史、阅读至少四条轨迹，输出带适用边界的单技能创建或局部补丁。§3.2.3 的训练摘要还包含预测与训练标准答案。

本包将这些指导改写到 roles.md / learning.md：允许编译已经观察到的做法，区分中间尝试与最终交付，用具体训练场景推演操作依据。2026.10.20.4 进一步要求技能各步骤与适用范围一致，并区分输出结构、业务规则和源数据；缺少字段不能自行变成排除证据的依据。这些是本适配的细化，不声称论文要求相同的审计表，也不把提示词检查当作效果复现。`score_only` 没有论文提供的训练标签，无法仅凭总分确定某个业务字段为何错误；不能靠更强措辞消除这个信息差，不能据此擅自开放 rubric 或切换反馈协议。

2026.10.21.1 进一步区分字段定义与字段间关系的依据、内部一致与独立事实核对，并要求访问审计区分被拒尝试与实际泄漏。它修正本适配的提案和审计指导，不改变接受阈值或评分，也不表示已证明效果改善。旧版本实验保留其原始决策与已发现的限制。

2026.10.21.2 将这些要求贯穿角色委托：Maintainer 保留来源种类和视图缺口，不凭自检排除失分原因；Proposer 复核 Wiki 的推断，选择有依据的操作过程；Auditor 按实际注入正文走查步骤，并核对自写检查本身。该版本补充适用范围、停止和交付边界，不增加候选试分、不公开私有标签，也不要求先证明收益才允许候选。它仍是适配指导优化，不宣称为论文原句或最优解。

## 必须保留的机制

| 机制 | 依据 |
|---|---|
| train/val/test 分开，技能集合从空开始，模型和工具固定 | §2 |
| immutable raw → 持续更新 Wiki → 可执行 skills；每技能有 SKILL 和 PURPOSE | §3.1 |
| Target 获得全部当前 SKILL 正文，不能读 Wiki；采用完整注入 | §3.2.1 |
| 完整 train rollout 后更新 Wiki，随后提出一次单技能改动 | §3.2.2–3.2.3；Algorithm 1 |
| 空技能实测验证基线；只接受严格超过历史最佳；拒绝时仅回滚技能 | §3.2.4；Algorithm 1 |
| 外层程序在验证后记录完整 diff、分数和接受结果，包含失败修改 | §3.1 Wiki Layer；§3.2.4 |
| Maintainer 最多 5 个失败、3 个成功，每轨迹视图 15,000 字符 | Appendix C |
| Proposer 提案前至少读 4 条轨迹；create / 局部 patch / no_action | Appendix E.3 |

## 本适配的明确差异

- **固定 Skill 测试复测**：用户可选择一次学习后，空 Skill 与最终 Skill 各测三次，报告三次测试均分的 mean ± sample SD（ddof=1），见 [test-repeats.md](test-repeats.md)。这是本适配的评测方案，不宣称为论文强制协议，也不等于三次独立学习；历史 campaign 的三次独立学习及 ddof=0 输出保持原义。

- **训练反馈**：Penguin 私有评测契约不向优化器公开 rubric。默认 `score_only` 用训练轨迹、预测和分数；论文 Proposer 同时有训练 ground truth。只有指定训练标签出口时才能启用 `with_labels`；两种模式必须分别标注。
- **完整注入**：从技能文件确定性生成 Target `AGENTS.md` 的限定段，作为当前 Penguin 系统上下文接入方式。固定段外文字不变，不把生成的文本视为额外可优化 system prompt。未验证注入时不能声称忠实复现。不能静默改为按需技能检索。
- **评测标度**：本地 Penguin rubric 使用 0–100，按固定 cell 集合取算术均值；满分为 100。算法的严格比较不变。连续评分、评测模型与原论文 benchmark reward 不同，结果不直接横比。
- **初步探索**：2 轮、每题 1 次、串行仅是可配置的 smoke 建议。论文表中迭代 0–7 不能作为通用“默认八轮”的依据；Proposer 的 10–20 ReAct 步是观察值，不是硬预算。
- **no_action**：消耗一轮、记录原因、跳过无改动的重复验证；原文未规定具体调度语义。
- **更严格的提案协议**：禁止同名 create 覆盖、路径越界、符号链接和歧义锚点；至少四个不同训练任务，不用同一轨迹的分片凑数。初版只进化两个 Markdown 文件，不生成可执行脚本。
- **学习提示与视图选择**：本包的学习指南要求区分观察与解释、引用支持/冲突证据，在技能正文写明启用/跳过条件；一次提案围绕一个可检验的行为变化。2026.10.20.1 增加训练内的错误定位、部分正确步骤保留、原动作与新动作的证据推演，以及后续轮次的规则遵循检查；不增加训练试分、候选数量或私有反馈。Harbor 视图先提取任务、动作与结果，保留 root 任务/最终回复的文字预算并成对抽取工具调用/结果，不直接截取 trace JSON 前缀。仍保持最多 5 失败/3 成功及每视图 15,000 字符，并要求 Proposer 阅读完整轨迹。这些具体提示与截断策略是本适配的实现选择，不冒充论文原始 prompt，也不保证提分。
- **固定经验回放**：可按 [experience-replay.md](experience-replay.md) 复用已核验训练经验诊断学习提示。它不是新的独立学习重复，也不能替代下一轮在当前 accepted Skill 下的训练。
- **提案语义审计**：2026.10.20.2 区分“缺少可执行的操作依据”和“操作合理但收益未知”。前者须补正，不能仅记 warning；后者仍允许进入固定 validation。禁止把同名字段、模板优先或最简单映射当作无需证据的业务规则；不禁止有依据的领域推理。训练内推演只核查已有材料，不增加模型做题或评分器调用。旧实验及其已通过审计不据新版规则追溯改分。
- **Wiki 文件名**：统一 `logs.md`。原文正文和附录分别出现 `logs.md` / `log.md`。附录关于 inference agents 阅读模式索引的文字与主文不一致，遵循主文 Target 不读 Wiki。
- **伴生审计**：检查读取路径、状态漂移、证据和候选范围，是 Penguin 适配增加的可审计措施；不等同于论文新增优化器，也不等同于安全 sandbox。
- **运行环境**：提供本地 Workspace 接口与固定 GDPevo/Harbor 接口、计划生成和结果转换工具。没有复现论文五种环境；本次改动仅做本地合成检查，真实 Harbor 加载、隔离和效果尚未验证。
- **GDPevo 划分**：经用户确认，每组原 train 的 001–004 用于训练、005 用于 validation，原 test 全留：24/6/30。它是本项目的适配方案，不是 WikiSkill 原论文的数据划分，也不同于 GDPevo 原始 30-train 协议。
- **旧固定 GDPevo 方案的重复和模型**：该方案做三次独立初始化、独立进化，最终 test 分数取均值；模型与服务商由用户选择并在 profile 中冻结，最初的 DeepSeek 官方配置不是算法要求。Wiki/候选不能跨三次学习实验继承。该适配沿用原始 verifier 的 0..1 reward，转换为 0..100、半向上保留两位小数后聚合，不由 LLM rubric 重评分。固定 Skill 的三次测试按上面的独立评测协议处理，不伪装成三次学习，也不改写旧结果。

## Penguin 参考版本

按 `Prism-Shadow/penguin-harness` main 的 `a54bee279` 核对：

- [既有单次评测 skill](https://github.com/Prism-Shadow/penguin-harness/blob/a54bee279/plugins/agent-tuning/skills/agent-evaluation/SKILL.md)：请求、评分与协议返回结构。
- [CLI run 实现](https://github.com/Prism-Shadow/penguin-harness/blob/a54bee279/packages/cli/src/commands/run.ts)：显式 thinking、JSON sessionId、超时后仍继续运行。
- [Agent 上下文](https://github.com/Prism-Shadow/penguin-harness/blob/a54bee279/packages/core/src/agent.ts)：新上下文加载 State，sub-agent 深度限制。
- [Trace 类型](https://github.com/Prism-Shadow/penguin-harness/blob/a54bee279/packages/core/src/omnimessage/types.ts)与[写入规则](https://github.com/Prism-Shadow/penguin-harness/blob/a54bee279/packages/core/src/trace/writer.ts)：会话绑定和分片。

以上是原生 Workspace 接口的核对基线。GDPevo 接入参考 Penguin PR #1034 的 `4c9c1797156921525b420f7f6d4bfab788059ae7` 和 benchmark PR #4 的 `e5d58d230bda928d9a98ae775c54c159af8f8363`；2026-10-12 检查时均未合并。具体固定项和公开源链接见 [gdpevo.md](gdpevo.md)。本技能自行编排 WikiSkill，不能直接调用上游 DefaultRSI 代替算法；也不假设安装了 `agent-supervision`。实际接入重新核对版本，不能在一次实验中途升级接口。
