# GDPevo：本项目实验配置

> 新实验最终测试按 [final-test.md](final-test.md) 执行：冻结 `reuse_unchanged` 时，最终业务技能为空且有效状态/评测条件一致，只做 test-H0 并保存 skip 回执；下文“两套 test”对有技能变化或预先选择 `repeat` 的实验成立。旧冻结实验保持旧协议。

本文件规定本项目已确认的数据与重复实验方案，不是 WikiSkill 的通用参数。先选择模型并生成清单，再按 [harbor.md](harbor.md) 执行；数据、模型和预算在首个真实 cell 前冻结。

这是 [experiment-spec.md](experiment-spec.md) 所区分的专用实验适配：`gdpevo_profile.py`、`campaign.py`、Harbor 回执/转换与训练视图工具共同校验这套固定协议。它们不接受任意 benchmark 清单；通用规划文件不能替换这里的 profile。既有 profile/campaign 保持原字节与 hash，新规划工具不迁移或重写历史实验。

## 数据与版本

| 固定项 | 值 |
|---|---|
| benchmark | `Prism-Shadow/penguin-harness-benchmark` PR #4，`e5d58d230bda928d9a98ae775c54c159af8f8363` |
| 宿主 Penguin 接口参考 | PR #1034，`4c9c1797156921525b420f7f6d4bfab788059ae7` |
| Harbor | `0.23.0` |
| 容器 Penguin 包参考 | `@prismshadow/penguin-cli@0.2.13`，单独记录实际安装包/离线 bundle hash |
| 模型 | 用户选择 provider/model_id/thinking，写入 profile；可统一配置，也可分别指定六个角色 |
| 初步探索默认值 | 2 轮、每格一次、串行；thinking 使用所选模型支持的设置并记录在 profile |
| 复现方式 | r1、r2、r3 独立初始化、独立进化；固定相同配置，不能共享学习状态 |

最初的工作区 profile 选用了 `deepseek/deepseek-flash`、thinking=`medium`；这是历史配置，不限制新的模型选择。使用 OpenRouter 时填写该分组中实际可用的完整 model ID，并配置对应分组的 Key。模型清单只用于核对绑定与可用性，密钥不进入 profile、技能或报告。

已有 profile/campaign 内容不可覆盖。尚未执行也可保留原目录，按新选择另存 profile 并初始化独立 campaign；已执行的结果继续绑定原配置。换渠道后重新验证凭证可用、实际端点、网络放行和模型参数，完成独立 smoke。不同渠道的结果可分别报告，不能把它们混为同一配置下的三次重复。

宿主源码 commit 不代表容器已经使用相同代码：上游离线 bundle 脚本下载 npm 包，不打包本地 fork。安装时记录宿主 commit、容器 package version 和 bundle hash；需自建容器包时先验证协议，重新冻结全套配置。

每组原 train 的 001–004 是逻辑 train，005 是逻辑 validation；test 的 001–005 不变：

| task-group | 领域 | 原 Penguin 包中的 CASE 序号范围 |
|---|---|---|
| tg003 | CRM | 001–005 |
| tg005 | ERP | 006–010 |
| tg010 | Finance | 011–015 |
| tg014 | Healthcare | 016–020 |
| tg018 | Legal | 021–025 |
| tg024 | Data analysis | 026–030 |

train 包和 test 包的 CASE 序号分别从 001 开始。例如 `CASE-010-tg005-train-005` 是 **validation**，Harbor task 名仍为 `tg005-train-005`。不重命名上游目录，不用文件名中的 train 字样推断权限。原数据包共 30 train、30 test；本实验从前者留出 6 validation，最终 24/6/30。

本方案中六组共享每个 replica 自己的一份 Wiki/Skill；这是实验选择，不是算法要求。正式三次实验保留完整 30 题 test，使用原始 GDPevo verifier 打分，Evaluator 模型不替代评分器。按组独立学习或更改划分属于新方案，需要新的适配与实验记录，不能靠改旧 profile 让当前工具接受。

生成可审核清单（不启动模型）；将所选模型通过工具支持的参数写入 profile，不把下面的占位符作为值：

```bash
python3 <skill>/scripts/gdpevo_profile.py --out <profiles>/selected.json \
  --provider <所选provider> --model-id <所选model-id> \
  --thinking <所选thinking> --iterations 2
```

没有 checkout 时输出 `source_verified=false`，只能规划。正式运行前另存经源码核对的清单：

```bash
python3 <skill>/scripts/gdpevo_profile.py \
  --benchmark-root <固定commit的完整benchmark-checkout> \
  --out <profiles>/selected-verified.json \
  --provider <所选provider> --model-id <所选model-id> \
  --thinking <所选thinking> --iterations 2
```

需要不同角色使用不同模型时，将六个角色的 `provider`、`model_id`、`thinking_level` 写成 JSON 对象，以 `--role-models <roles.json>` 替代三个统一模型参数；角色名为 `controller`、`target`、`evaluator`、`wiki_maintainer`、`skill_proposer`、`companion_auditor`。调度时按 [penguin.md](penguin.md) 显式绑定各角色，不能只把配置写入文件后仍让子会话默认继承。

profile 文件放在 campaign 目录之外；`campaign.py init` 会创建新的 campaign 目录并保存冻结副本，不要先为它创建目录。

工具检查 commit、受版本控制的文件及 task/case 完整性；只用文件 hash，不输出题面/答案。相同输出可幂等运行，内容不同拒绝覆盖。三次实验绑定同一个经验证清单的 `profile_sha256`。工具不替代容器网络、技能注入与权限预检。

每次部署生成 aggregate 的 phase plan，见 [state.md](state.md)。从清单取完整 phase case 集合，并填入该次真实 Target ID、State version、benchmark ID；不能直接把整个 profile 当作 aggregate plan。validation 可以沿用原 train 包 ID，权限由 `phase=validation` 与明确 case 列表共同限定。冻结 plan 后才发请求，所有回执绑定 plan hash。

## 三次独立执行

每个 replica 使用独立 OUT、专用 Target、新的 Controller Session、空技能和空 Wiki；三者共享隔离的宿主 Penguin 数据根与模型配置，以及只读 benchmark 与安装 bundle。Controller 定义可以复用，但不能携带其他重复实验的记忆或对话；可选 campaign 工具另约定三个专用 Controller ID。只在执行当前 replica 时准备它的身份，由 Controller 通过本技能创建或核验 Target。学习状态和会话互不共享，详见 [campaign.md](campaign.md)。每格全新容器与 Session。固定配置相同，真实执行、轨迹、Wiki、候选、验证与最终结果各自独立。

1. 冻结基础 Target 为 H0，运行 6 validation，严格聚合后以实测基线初始化 state。
2. 用当前 accepted 跑全部 24 train；审计通过后更新 Wiki、提出单技能改动。
3. 有合法候选则跑 6 validation；严格超过历史最佳才接受，平分/下降拒绝，Wiki 保留。`no_action` 消耗一轮但跳过该次 validation。
4. 按固定轮数、预算或 validation 满分停止，冻结该 replica 的最终技能 hash。
5. 冻结后分别用 H0、最终技能跑完整 30 test。test 结果不得用于继续优化、选择另一个候选或决定追加轮数。
6. 完成 r1/r2/r3 后聚合；不能把一次进化后的技能拿来连续测试三遍，称为三次独立实验。

若 K=2 且每轮都有候选，每个 replica 最多需 `6 + 2×(24+6) + 30 + 30 = 126` 个 Target cell，三次共 378；另有 Maintainer/Proposer/审计的模型调用。这是次数估计，不是费用授权。正式启动前固定预算/超时；当前默认 2 轮属于初步探索配置，不代表论文完整实验预算。

原仓库 `tools/gdpevo/protocol/replica.sh` 编排 30 train、DefaultRSI 和 test，不能原样代替本流程；`benchmarks/gdpevo/job.yaml` 也包含全部 train/test 且模型参数不同，不能整份提交跑进化。只执行冻结 phase allowlist 中的单格任务。

## 汇总与报告

每格原始 `reward∈[0,1]` 经 Harbor 转换器变为 0..100 分；各阶段先过 `aggregate.py` 的完整矩阵检查。不用上游容忍缺值的汇总绕过检查，不删去失败 cell 后平均。

每个 replica 的 test 分为 30 格算术平均；每领域为该组 5 道 test 平均。报告三份最终 test 分及其均值、总体标准差（ddof=0）；H0 同样报告三份分和均值，提升按三个配对差值求均值。每个数附对应 summary/evaluation ID、部署 hash 和 profile hash。缺任何 replica 或 cell 时报告未完成，不称“三次平均”。零提升、负提升均照实报告。

正式成绩还需保留：逐轮 validation 与接受记录、各模型实际标识、版本、完整性/泄漏审计、预算和停止原因。不得把离线合成样例写成真实测试。

## 公开依据

- 数据、领域及环境：[固定版本 GDPevo README](https://github.com/Prism-Shadow/penguin-harness-benchmark/blob/e5d58d230bda928d9a98ae775c54c159af8f8363/benchmarks/gdpevo/README.md)
- 容器适配：[agents README](https://github.com/Prism-Shadow/penguin-harness-benchmark/blob/e5d58d230bda928d9a98ae775c54c159af8f8363/agents/README.md)
- 原始三次协议：[protocol README](https://github.com/Prism-Shadow/penguin-harness-benchmark/blob/e5d58d230bda928d9a98ae775c54c159af8f8363/tools/gdpevo/protocol/README.md)
- 源码变更：[Penguin PR #1034](https://github.com/Prism-Shadow/penguin-harness/pull/1034)、[benchmark PR #4](https://github.com/Prism-Shadow/penguin-harness-benchmark/pull/4)
