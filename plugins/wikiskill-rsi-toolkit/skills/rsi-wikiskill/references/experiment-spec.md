# 算法、实验方案与执行适配

WikiSkill 接收实验方案，不内置某个数据集的划分比例。Skill 可以附带规划和校验工具；这不意味着每次优化时让学习角色重新选择训练题或测试题。

| 层次 | 职责 | 放在哪里 |
|---|---|---|
| 算法 | 用 train 轨迹更新 Wiki、提出单技能改动；valid 严格筛选；拒绝保留 Wiki；技能冻结后才比较 test | `SKILL.md`、学习/角色/状态说明与通用状态工具 |
| 实验方案 | 任务来源与划分、哪些任务共享 Wiki/Skill、模型、轮数、独立重复数、评分约定及预算 | 工作区内每个实验自己的清单；插件只提供校验工具和明确标注的专用方案 |
| 执行适配 | 将任务、Target State 和模型绑定送入真实环境，执行评分器，验证隔离与证据并转换成绩 | 已验证的 Penguin/benchmark/Harbor 接口及其专用工具 |

算法不强制同领域，也不保证混合领域一定有效。领域组合是要检验的实验选择。一个方案只描述一个 `learning_unit`：其所有 train 任务共同更新一套 Wiki/Skill，validation/test 在该单元的冻结技能条件下评测。独立重复各自从空 Wiki/Skill 开始；若要按领域分别学习，使用多个方案与互不共享的 OUT。

## 先确定名单，再学习

1. 按研究问题、任务元数据和运行资源确定候选范围；划分依据、选择人和去重检查记在实验目录的说明中。原有官方 train/test 边界优先保留；自定义划分须明确标注，不能冒充官方榜单协议。
2. 在首次真实评测前确定 train/validation/test 清单。按原始任务身份、内容和已知近重复关系检查，不通过改 case ID 让同一题跨集合；不能用候选的 validation/test 表现挑选更有利的划分。
3. 只用 train 产生学习轨迹与 Wiki。validation 的汇总分数用于候选接受决策，不把题目、逐题反馈或轨迹交给学习角色。最终技能冻结后才运行 test；test 不参与后续技能选择。
4. 检查所选 runner 的真实环境、评分和隔离，再做独立 smoke。规划通过只表示清单内部一致，不表示任务已能执行。

不规定题数、比例、三次重复、四并发或固定模型。当前实现沿用 Proposer 至少阅读四条训练轨迹的协议，并要求来自四个不同任务；这是本实现的学习输入约束，不是 RSI 的理论下限。必须有独立 validation；test 可以为空，但这时只能报告验证实验。具体运行预算与权限在启动前另行落实，不能用规划文件替代费用授权或停止机制。

## 通用规划工具

`scripts/experiment_spec.py` 只读取操作者提供的 JSON，校验并冻结声明；不会自动划分任务、选择模型、读取 benchmark 文件或答案，也不创建 Agent、启动容器或修改旧 profile。

在工作区的独立实验目录准备 `spec-input.json`。它是精确字段协议，不允许额外字段：

| 字段 | 内容 |
|---|---|
| `schema_version` / `algorithm` | `1` / `"wikiskill"` |
| `source` | `repository`：无凭证、查询参数或 fragment 的 HTTPS 仓库地址；`revision`：完整小写 Git commit SHA |
| `adapter` | `id`、`version`；只记录声明，不查询注册表或证明已支持 |
| `tasks` | 每项有 `case_id`、`original_id`、`path`、`content_sha256`、`category`、`duplicate_family`；path 为规范仓库相对路径，duplicate_family 为已知近重复组 ID 或 `null` |
| `phases` | `train`、`validation`、`test` 三个 case ID 数组；每个 catalog 任务恰好出现一次，保留指定顺序 |
| `learning_unit` | 例如 `{"id":"software-exploration"}`，标识本次共享学习单元 |
| `config` | `train_feedback_mode`、`role_models`、`max_iterations`、`runs_per_case`、`replicas`、`independent_replicas`、`concurrency`、`cell_timeout_seconds`、`max_turns` |
| `scoring` | `metric`、`raw_min`、`raw_max`、`direction`、`normalization`、`aggregation`、`success_rule` |

`config.role_models` 必须显式提供 `controller`、`target`、`evaluator`、`wiki_maintainer`、`skill_proposer`、`companion_auditor` 六项，每项恰为 `provider`、`model_id`、`thinking_level`。不填 Key；模型/档位是否可用由执行适配核对。`train_feedback_mode` 为 `score_only` 或 `with_labels`；后者仍需合法训练标签出口，规划声明本身不授予答案访问权限。`replicas` 为非空且不重复的 ID 数组，`independent_replicas` 必须为 `true`；其余次数、并发、超时和 turn 上限为正整数。

当前规划协议与通用状态工具约定一致：`scoring.direction="higher_is_better"`、`normalization="linear_to_0_100"`、`aggregation="mean"`、`success_rule="normalized_score_equals_100"`，raw 上下限是有限且严格递增的数字。执行适配必须落实并记录转换；规划工具不转换实际 reward。使用不同评分语义时先适配，不能只给它换一个名称。

```bash
python3 -B <skill>/scripts/experiment_spec.py freeze \
  --input <experiment>/spec-input.json --out <experiment>/spec-frozen.json
python3 -B <skill>/scripts/experiment_spec.py check \
  --spec <experiment>/spec-frozen.json
```

输入、输出须使用真实目录路径；工具拒绝路径自身及任一祖先目录为符号链接。macOS 的 `/tmp` 可能指向 `/private/tmp`，需要临时目录时先解析真实路径，再传入命令。

冻结文件只有 `schema_version`、`status`、`spec`、`spec_sha256`；`status` 始终为 `planning_only`。同路径已有不同内容时拒绝覆盖。工具检查 case、原始 ID、路径及跨集合的相同内容指纹/已声明近重复组，不自动发现未声明的语义重复，也不验证提供的内容 hash 真实。hash 只绑定方案声明。命令输出的摘要中，`source_verified` 和 `runtime_verified` 均为 `false`；`evaluation_scope` 在 test 为空时为 `validation_only`，否则为 `held_out_test_planned`，两者都不是完成的评测。

## 已有 GDPevo 与新的 benchmark

[GDPevo](gdpevo.md) 保留专用 `gdpevo_profile.py` → `campaign.py` 链路。通用 `spec-frozen.json` 不能替代旧 profile 或传给这两个专用工具；已有实验继续绑定原 profile/campaign，不在续跑时重写旧 hash。

[Terminal-Bench](terminal-bench.md) 另有 `terminal_bench.py`：只读取任务元数据并对不透明文件做 hash，可按外部选择生成固定划分；18 道 Software、8/5/5 是附带的探索方案，不是 WikiSkill 算法要求。`harbor_spec.py` 核对实际 Git 提交和任务字节，`spec_campaign.py` 保存身份、阶段计划和最终冻结记录。正式执行继续使用 Penguin 现有的 `penguin_agent:PenguinAgent` 与 Harbor 0.23.0，没有另造 runner。

外部实验给 `harbor_receipt.py`、`harbor_result.py`、`train_views.py` 显式传入 `--spec` 和 `--source-root`，使用 schema-3 回执绑定 `experiment_spec_sha256`、原始 task、实际路径与完整 phase。独立 smoke 用 `purpose=smoke`，不能导出为训练或混入正式矩阵。没有这两个参数时旧 GDPevo 行为不变。

`experiment_spec.py` 本身仍只规划，冻结文件的 `planning_only` 不改成伪造的运行状态。实际来源验证、真实 smoke、隔离证据、技能注入与最终结果分别由执行工具和审计提供；静态清单及单元测试不能当作真实容器预检。更改任务名单、来源或 runtime 时创建新 spec，保留旧方案及结果。
