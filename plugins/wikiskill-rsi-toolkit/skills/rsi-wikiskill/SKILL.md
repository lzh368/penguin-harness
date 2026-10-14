---
name: rsi-wikiskill
description: Run or resume WikiSkill experiments in Penguin. Compile training traces into a persistent Wiki, propose one skill change per batch, and accept changes only when independent validation improves. Use for skill-based RSI with isolated evaluation, including GDPevo through Harbor.
version: 2026.10.14.4
---

# WikiSkill 自进化

你是外层 Controller。把训练经验编译成可复用的技能；优化产物是 `SKILL.md`，不更新模型权重。Wiki 是优化过程的持久知识库，执行任务的 Target 不读取它。

依据：[WikiSkill: Compiling Agent Experience into Persistent Knowledge for Skill Evolution](https://arxiv.org/abs/2608.27454)。本包是面向 Penguin 的独立适配，不是作者发布的代码。论文机制与适配差异见 [paper.md](references/paper.md)。

## Before you start

- 用户只要求设计、编写或审核 skill：交付文件和检查结果，不启动模型实验。
- 用户明确要求离线演练：只在独立临时 OUT 使用标明合成的轨迹与评分，执行本地工具链；不要求虚构 App Data Dir/真实 Session。报告为 fixture 检查，实际加载、角色隔离和模型效果均标未验证，模拟记录不能混入正式实验。
- 用户要求运行：先按 [experiment-spec.md](references/experiment-spec.md) 区分实验方案与执行适配，再读取 [penguin.md](references/penguin.md)，核对参数、实验权限和运行条件。使用本项目 GDPevo 配置时还须先读 [gdpevo.md](references/gdpevo.md) 的实验方案，并通过 [harbor.md](references/harbor.md) 执行 cell。
- 用户要求续跑：已有 `state.json` 时先执行 `state.py verify`；尚处准备、smoke 或基线评测阶段时，核对创建记录、`experiment.json`、H0 部署和既有 Session/trial，恢复原事务。缺少 `state.json` 不代表应该重建 Target 或重跑已启动的题。恢复事务时读 [state.md](references/state.md)，不要重新初始化、补造分数或默认从下一轮开始。
- 用户要求无人值守接力、费用监控或提速：读取 [operations.md](references/operations.md)，使用本包的运行检查工具；不得在临时脚本里重新实现预算停止、收尾通过和回执校验。正在运行的实验继续使用其冻结版本，新协议另开实验，不替换现有证据。

加载本技能的当前普通 Agent 就是 **Controller**，无需用户另建专门的 Controller。Controller 按 [penguin.md](references/penguin.md) 在准备阶段自行创建或核验本次实验的专用 **Target**；用户无需逐个创建 Agent，也无需预先创建全部重复实验的身份。Target 不能加载本技能、评测 skill 或 Wiki。模型、服务商和 thinking 由用户选择，技能不限定 DeepSeek 官方、OpenRouter 或其他渠道。已提供本地 Workspace 和 GDPevo/Harbor 两种执行接口；新环境须先适配执行、评分与隔离接口。

模型选择与算法分开：优先使用用户指定的实验 profile；创建新实验时采用用户在网页或启动参数中选择的模型，记录各角色实际配置。显示名相同不证明版本、端点或服务行为相同，绑定与核查使用 `provider`、`model_id` 和 thinking。续跑保持已冻结配置；用户改选时新建 profile/campaign，保留原实验及结果，不静默覆盖或混算。

算法消费预先确定的 train/validation/test 清单，不规定某个数据集、划分比例、领域组合、重复次数或并发数。具体任务名单与运行参数放在工作区的独立实验目录；哪些任务共享 Wiki/Skill 也由实验方案明确。插件可以附带方案校验和数据集适配工具，不因此把某个实验的选择变成算法要求。规划与执行的边界见 [experiment-spec.md](references/experiment-spec.md)。

本项目 GDPevo 的固定方案与专用工具见 [gdpevo.md](references/gdpevo.md) 和 [campaign.md](references/campaign.md)。工作区的 `scripts/rsi.py` 是该方案的可选检查与启动入口，不是其他 WikiSkill 实验的依赖。首次接入执行环境时先用独立 smoke Target 跑一个 train cell，确认注入、评分及隔离；其记录不进入正式训练或 baseline。只有规划清单、尚未验证执行适配时不能开始正式实验。

## 准备实验

在当前新 Session 中读取本次的 benchmark、模型和预算选择。网页传入 `target_setup: skill-managed` 或未给 Target ID 时，由你按 Penguin 接入步骤创建独立 Target；若 profile/campaign 已冻结 Target ID，按该 ID 核验或创建，不能另造身份替代。已有 Target 不等于可直接复用：新实验检查空技能、空记忆、无历史实验和无活动 Session；续跑核对已有清单与部署记录，不能清空状态“重新开始”。

只准备当前要执行的 smoke 或 replica。创建身份、记录配置不执行做题模型；用户仅要求准备时，完成检查并报告下一步。用户已要求开始且预算、权限齐全时继续执行，不为每个 Agent 重复索要许可。遇到权限不足，说明具体失败命令和缺少的能力，不读取凭证或扩大权限。

Wiki Maintainer、Skill Proposer、Evaluator、Companion Auditor 都由顶层 Controller 通过 `run_subagent` 开启临时叶子会话，不创建同名持久 Agent。按实验计划指定的重复次数，每次独立重复各用新的 Controller Session、独立 Target 和 OUT，从空 Wiki/技能起步；可以复用干净的 Controller 定义，但不能复用上一次重复实验的对话或注入其记忆。使用已冻结 campaign 时保留其中的 Controller/Target ID 约定。

## 实验配置

在首次评测前写入 `experiment.json`，至少包含：

- App Data Dir、Controller ID 与本次 Session ID、专用 Target ID、创建或核验记录、独立 OUT 绝对路径；OUT 在 Target State 和所有做题 Workspace 之外。
- train / validation / test 的 benchmark ID、case ID 列表和内容指纹；划分按原始题目身份去重，不能仅靠换 ID。通用探索可暂不指定 test，此时只称验证实验；具体 benchmark 方案可以提出更严格的要求。记录本实验共享 Wiki/Skill 的学习单元；若按领域分别学习，每个单元另用独立方案和 OUT，不暗中共享记忆。
- 用户选择的 Target `provider`、`model_id`、thinking level，以及优化/评测角色的实际模型配置；角色可以按实验设计分别配置，调度时显式绑定，不能只在提示词里声明。记录非敏感端点或路由设置以便复现，不保存 API Key。一次实验中的 H0、候选和最终比较保持同一配置；切换渠道建立新实验，并在报告中区分条件。使用实验预先声明的评分器与聚合规则，不能为提高分数更换评分方式。
- `max_iterations`、每题 `runs_per_case`、并发数和停止预算。用户未指定时，初步探索可采用 **2 轮、每题 1 次、串行**，注明这是初步探索配置。预估 Target 次数为 `Nval*R + K*(Ntrain+Nval)*R`，最终测试另计；预算不允许完整一轮时不启动它。
- `train_feedback_mode: score_only`（默认）或 `with_labels`；后者只用用户明确提供的训练标签出口，不读取私有 rubric 来造标签。
- `skill_provisioning: full_injection`、`isolation_mode`、冻结的基础 Agent State 指纹及工具/提示词/版本。默认从空技能集合、空 Wiki 开始，专用 Target 不继承其他业务技能。

App Data Dir、数据划分、可用模型对或预算授权无法确定时，只问缺失项；已有 profile、网页选择或安全配置查询能够确定的内容无需重复询问。缺少 Target ID 按上述流程自行准备，不要求用户手建。可以先完成静态规划，不能从凭证文件猜测配置。最少需要四个不同训练任务；更小样例可检查脚本，但不宣称运行了论文流程。

## 不变量

1. 每轮完整训练集使用同一个已接受技能集合，所有任务结束后才更新 Wiki 和提出改动。
2. 每次候选只创建或修补一个技能；Proposer 提改动前实际阅读至少四条来自不同训练任务的完整轨迹，不能用四个分片或四份摘要凑数。
3. 先测空技能 validation 基线。只接受 `candidate_score > historical_best_score`，平分也拒绝；达到评分上限即可停止。
4. 拒绝候选只恢复技能内容及其派生注入段，保留 Wiki、本轮 train 原始轨迹和完整的被拒修改记录。
5. 学习角色只接收 train 内容、Wiki、技能、历次 validation **汇总**分数和接受记录。validation/test 的题目、输出、逐题评分、轨迹与评分细则不进入 Wiki 或 proposal。
6. 基础设施失败、缺题、重复结果、模型漂移或评分异常不能当成 0 分，也不能在删去失败样本后取平均。
7. 预算停止后不再发继续指令或启动下一副本。费用未知时暂停新调度，保留在途任务；收尾检查失败不能标成 complete。会话 idle 和容器消失均不等于实验成功。

## 执行一轮

先读 [roles.md](references/roles.md) 分发角色，再读 [state.md](references/state.md) 使用本地状态工具。学习和提案审计角色须读 [learning.md](references/learning.md)，按训练证据写出有适用边界的规则。Penguin 中只有顶层 Controller 编排 `run_subagent`；各子角色是独立叶子会话，不再启动子 Agent。

### 0. 空技能基线

按 Penguin 适配文档冻结基础 Target，注入空技能段，评测完整 validation 矩阵。Controller 用 `scripts/aggregate.py` 校验矩阵后得到算术均值，再以该实测值初始化 `state.py`。保存 H0 指纹、基线评测 ID 和原始协议结果。初始化工具本身不做评测，不证明分数真实。

### 1. 训练与原始层

把当前 accepted 集合装载到 Target，新 Session、新 Workspace 跑每个训练 cell；GDPevo 每格使用全新 Harbor 容器。独立 evaluator 返回评分，Controller 仅归档 **Target 做训练题的 Session 及其 Target 子会话**至 `raw/`，形成不可变的训练结果清单；Evaluator 会话、rubric 和私有评分推理不进入 raw。按预先指定的任务成功判定分组；若任务只有连续评分，默认满分成功，其余失败并记录该约定。

检查 State/benchmark 指纹和实际模型绑定；伴生审计角色核查数据访问边界及加载内容。如果发现泄漏、运行中状态变化或注入失败，本轮无效，停止排查，不生成“改进”。

GDPevo/Harbor 用 [training-views.md](references/training-views.md) 的 `train_views.py` 将整轮已审计结果导出到新的 `raw/rNNNN/`，同时保存完整原始分片、轨迹入口与采样 manifest；不要先用原始 JSON 前缀手工造摘要。其他执行接口须保留同等来源、完整轨迹与视图限制记录。

### 2. Wiki Maintainer

从当轮训练记录分层抽样，最多 5 条失败、3 条成功；每条给 Maintainer 的轨迹视图最多 15,000 字符，记录采样种子/ID及截断方式，保留原文件。Harbor 使用导出 manifest 的 selected 视图，不另行挑选。让 Maintainer 区分观察和解释，记录条件、动作、结果、反例与未知之处，更新 `wiki/patterns/`、完整 `index.md` 并追加 `logs.md`。新证据削弱旧模式时同时修正索引中的结论，不把互相冲突的建议一直累加。

### 3. Skill Proposer

开启新会话，只给角色指令、允许的路径和训练清单。它从 Wiki 索引、历史 `skill-impact.md`、训练结果摘要和当前技能开始，至少读四个不同训练任务的完整轨迹，围绕一个可检验的行为变化输出一个结构化 proposal。每条新规则在 PURPOSE 留下证据和反例/未知范围，Target 所见 SKILL 正文写明启用及跳过条件；不能把局部经验升级为无条件的业务规定。具体准则与格式见学习指南和角色文档。

`no_action`：记录理由，保留 Wiki 和技能，消耗一轮，跳过重复 validation。本规则是工程约定。格式错误可在同一角色会话修正；不要向它透露 validation 私有细节。修正仍不合法时停止本轮，不把错误伪装成算法拒绝。

### 4. 构造并审计候选

先让伴生审计检查提案证据、单技能范围、通用性与数据访问；问题由 Proposer 根据训练证据修正。再用 `state.py stage` 在候选副本上应用 proposal，核对真实 diff；精确补丁锚点不存在或重复、实际 SKILL 内容未变均失败。审计不创造新技能、不读评测答案；stage 后发现问题时保留 pending 并按状态文档停止处理，不手工覆盖候选。

保持其他配置固定，完整注入所有候选 `SKILL.md`。`PURPOSE.md` 只供优化角色追溯来源，不提供给 Target。

### 5. 验证与提交

跑完整、固定的 validation Case × Run 矩阵。Controller 可以处理逐 cell 协议元数据和分数以校验完整性，但不读取 validation 题面、答案和轨迹，也不亲自据逐题结果写技能。用 `aggregate.py` 验证、聚合；将输出和输入 hash 绑定为唯一 evaluation ID。

调用 `state.py decide`：严格改善则更新 accepted；否则保持 accepted。工具将 proposal、完整 diff、分数及决策追加到 Wiki 的 `skill-impact.md`。等所有任务真正结束后，把实际 Target 与 accepted 重新对齐。先确认日志与目标状态一致，再进入下一轮。

如果 evaluation 失败，保留 pending 和 Wiki，先恢复实际 Target 为 accepted 并停止排查；失败没有合法的 accept/reject 结果。不得把候选当作最终版本。恢复策略见状态文档。

## 停止与交付

达到轮次/预算或 validation 满分后，冻结 accepted 的内容 hash。只有冻结后才能评测 test；若要报告提升，在同一固定条件下测 H0 与最终技能。看到 test 结果之后不得再据此选版本、修改技能并重用该 test 宣称提升。

返回：最终技能目录与 hash、基线/最佳 validation 分、每轮 accept/reject/no_action、预算与实际执行量、Wiki/证据路径、论文偏离项。只有运行了独立 test 才报告 test 指标；没有真实运行则写“静态/本地样例验证”，不能把模拟分数说成性能提升。

GDPevo 自动接力前运行 `campaign.py verify-replica`，并独立确认所有角色、Harbor 进程和任务容器均已结束。复算只证明状态和矩阵一致，不证明容器隔离或原始审计真实。分数下降照实报告，不据 test 改写本实验的 Wiki/技能。
