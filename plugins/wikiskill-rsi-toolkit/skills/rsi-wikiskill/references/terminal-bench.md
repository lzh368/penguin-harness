# Terminal-Bench：外部方案与现有 Harbor 执行器

> 新实验最终测试按 [final-test.md](final-test.md) 执行：冻结 `reuse_unchanged` 时，最终业务技能为空且有效状态/评测条件一致，只做 test-H0 并保存 skip 回执；下文“两套 test”对有技能变化或预先选择 `repeat` 的实验成立。旧冻结实验保持旧协议。

本页供实验准备者、Controller 和 Evaluator 使用。学习角色不读取 benchmark 源码、题库清单或私有评测资料。算法仍是 train → Wiki → 单技能候选 → validation 严格筛选，冻结后才比较 H0/final test。

Penguin 已有 Terminal-Bench 接入。团队 benchmark 仓库的 Sec E 是混合类别 CPU 子集，不能当作 Software 全集。本入口复用该仓库的 `agents/penguin_agent`，另从官方源码取得所选任务；不修改 Penguin core/web，不另造执行器，也不调用工作区中 GDPevo 专用的 `scripts/rsi.py`。

## 1. 来源与实验选择

本页示例是一次自定义探索，不是官方榜单协议或算法默认要求：

| 项 | 示例 |
|---|---|
| 任务来源 | `https://github.com/harbor-framework/terminal-bench`，tag `v4.0.0` |
| 固定提交 | `452bf305c6daa62fc59061d22133a7cbc7c1572e` |
| 候选 | metadata.category 为 `Software` 的 18 题 |
| 自定义划分 | 8 train / 5 validation / 5 test，固定 seed `20261015` |
| 模型 | 六角色 `openrouter / deepseek/deepseek-v4.1-flash / xhigh`，可在新方案中改选 |
| 轮数/重复 | K=2，每题一次，本次仅 r1 |
| 单格预算 | 1500 秒、200 turns；与官方每题声明的 28800 秒不同 |
| 调度 | 同 phase 最多 10 个 job，按真实资源减少并发；阶段之间串行 |
| 费用 | 操作者另设全角色合计 25 USD 软阈值，不能冒充供应商硬限额 |

划分按 subcategory 做确定性的容量平衡，不看成绩或答案；不能保证难度完全一致或小类别都覆盖三个集合。`--split-file` 可提供提前确定的原始 task ID 列表，替代自动划分。语义近重复需额外检查，脚本不会自动证明语义独立。

在实验工作区保留两个不同 checkout：`TB_SOURCE` 为完整官方 task 来源，`ADAPTER` 为团队 benchmark 仓库（已验证接口的提交是 `e5d58d23...`）。前者固定实际完整 SHA，后者的完整 commit、Penguin 宿主/容器版本、安装 bundle hash 和本技能 hash 另记入 `experiment.json`。容器接口当前固定 Penguin 0.2.13，使用与主机架构一致的无凭证 bundle；宿主升级不代表容器也升级。

准备工具要求 Python 3.11+（使用标准库 `tomllib`）。以下变量均为操作者已核实的绝对真实路径，不含密钥：

```bash
python3 -B "$SKILL/scripts/terminal_bench.py" prepare \
  --source-root "$TB_SOURCE" --output "$EXPERIMENT/spec-frozen.json" \
  --split 8,5,5 --seed 20261015 \
  --provider openrouter --model-id deepseek/deepseek-v4.1-flash --thinking xhigh \
  --iterations 2 --replica r1 --concurrency 10 --cell-timeout 1500 --max-turns 200 \
  > "$EXPERIMENT/preparation.json"
python3 -B "$SKILL/scripts/spec_campaign.py" init \
  --spec "$EXPERIMENT/spec-frozen.json" --source-root "$TB_SOURCE" \
  --out "$EXPERIMENT/campaign" --penguin-home "$PENGUIN_HOME" \
  --project-id "$PROJECT_ID" --agent-prefix wiki_tb_001
```

先建立 `$EXPERIMENT`，但不预建 `campaign/`。初始化只冻结身份与目录，不调用模型或创建 Agent。`preparation.json` 保留实际 task 清单、资源及划分方法。元数据可读，其他任务文件只做不透明字节 hash，不把答案文本交给 Controller。清单 `status=planning_only` 保持原义，`source_verified=true` 也不代表容器已运行。

后续变量对应 `CAMPAIGN="$EXPERIMENT/campaign"`、`OUT="$CAMPAIGN/replicas/r1/out"`；重复实验改为其自身 replica 的 OUT。`PRIVATE_JOBS="$OUT/private/jobs"`，每阶段的 `CELL_OUTPUT` 位于该阶段 `plans/<label>/cells/`。

## 2. 真正开跑前的环境检查

- 按 [penguin.md](penguin.md) 准备本次 Controller/Target。正式身份读 `campaign.json`，smoke 另用独立 Target；由 Controller 自行准备，不要求用户手工建一排 Agent。六角色逐次显式使用 spec 中的模型/档位，不依赖项目默认模型。
- H0 指**没有 WikiSkill 派生技能**、空 Wiki 与空历史记忆。官方 Software 里 `cumulative-layout-shift` 声明任务自带 skills，这是 benchmark 固定资源，保留并在所有版本一致，不称整个环境没有技能。
- 18 题在已核对版本均有 separate verifier，5 题有 Compose 多服务。任务最高声明 16 CPU/16 GiB；10 job 不是固定 20 个容器，也不是固定 10 CPU。统计 agent、服务、verifier 的实际占用，预留共享机余量；不得操作别人的任务。
- 这些官方任务默认 agent 网络均为 `public`。Harbor 0.23.0 的 `--allow-agent-host` 只给限制策略添加例外，**不能把 public 改成 allowlist**。运行前验证实际网络限制、服务间访问、评分器隔离及公开答案不可达；提示词或一个成功的模型 API 请求不能证明隔离。未落实则停止在环境准备并报告具体缺口，不制造 network pass。
- 不直接修改官方 task.toml 后绕过 Git/hash 校验。若环境需要受控任务配置投影，另存可审计的来源/差异并适配验证；当前源码入口不冒充已经支持任意投影。也不能为跑通而开放所有公共网络。预拉镜像只解决构建耗时，不证明隔离。
- 宿主只有 `sudo -n /usr/bin/docker` 权限时，先核对 Harbor 实际调用路径及 Compose 所需环境变量；如使用实验专属 wrapper，只转发已有授权的 Docker CLI。不要把整个 Penguin/Harbor 以 root 启动、修改 sudoers/socket 权限或写全局 wrapper。

## 3. State、计划与单格执行

基础 State 冻结工具、提示词、thinking 与 `max_turns=spec.config.max_turns`，每次派生技能部署递增 version。**不要传 `--ak max_turns`**：现有 adapter 会通过 API 改 State，破坏已冻结版本。用 [harbor.md](harbor.md) 的无凭证打包与独立部署测量步骤，不能打包整个私人数据根。

新的外部 campaign 用 `state.py render --block` 生成完整规范注入段：

```bash
python3 -B "$SKILL/scripts/state.py" render --out "$OUT" \
  --source accepted --block --output rendered/accepted-block.txt
```

候选时 `--source c0001`。H0 尚无 state.json，规范空段恰为 `<!-- WIKISKILL_DERIVED_BEGIN -->\n<!-- WIKISKILL_DERIVED_END -->`（这里 `\n` 表示一个换行），不为了 render 先填虚假基线初始化；可调用 `state.render_text({}, block=True)` 得到同一字节序列。完整段按原样注入，不再包第二层标记；末端标记后的文件换行不属于注入段 hash。

正式阶段用独立 label：`validation-H0`、`train-r0001`、`validation-r0001`、`train-r0002`、`validation-r0002`、`test-H0`、`test-final`。无改动轮次不建候选 validation。`$SKILL_HASH` 是 Store 的整个技能集合 hash（H0 为 `state.tree_info({})['sha256']`），不是 ZIP 或注入段 hash：

```bash
python3 -B "$SKILL/scripts/spec_campaign.py" plan \
  --campaign "$CAMPAIGN" --replica r1 --phase validation --label validation-H0 \
  --version "$STATE_VERSION" --skill-hash "$SKILL_HASH"
```

结果位于 `replicas/r1/plans/<label>/{plan.json,deployment.json,cells/}`；`sources.json` 由 Evaluator 汇集，每条恰为 `case_id/run/result/receipt`，后两项是该 replica `out/private/` 内的真实绝对路径，格式同 [training-views.md](training-views.md)。Harbor jobs、私有计划和审计均放 `out/private/`，不能把 private 资料放入 raw/Wiki。H0 阶段先建 private，不提前制造 accepted/state；取得实测 validation 均分后才 `state.py init`。

仅把实际所需 task 的执行命令交给叶子 Evaluator：

```bash
python3 -B "$SKILL/scripts/terminal_bench.py" command \
  --spec "$CAMPAIGN/spec-frozen.json" --source-root "$TB_SOURCE" --case-id "$CASE_ID" \
  --adapter-root "$ADAPTER" --agent-id "$TARGET_ID" --state-archive "$STATE_ARCHIVE" \
  --bundle "$BUNDLE" --host-home "$PENGUIN_HOME" --project-id "$PROJECT_ID" \
  --api-host "$API_HOST" --job-name "$UNIQUE_JOB" --jobs-dir "$PRIVATE_JOBS"
```

工具只打印 argv/environment/shell，不启动任务。Evaluator 完成环境预检后执行返回的原有 Harbor 命令。每个 job 仅一次 attempt，唯一名称；same phase 同版 State 可有限并发，不跨阶段提前跑 test。运行时实际任务路径必须等于冻结源码路径，不能用同名任务替换。费用计入所有失败尝试；基础设施故障不记成零分。

## 4. 回执、smoke 与完整学习

四项真实审计、八字段 request、六字段 expected binding、三字段 observed-state 和完整 trace 的要求与 [harbor.md](harbor.md) 相同。schema-3 增加 `experiment_spec_sha256` 与 `purpose`；benchmark_revision 改为本 spec 的来源 SHA，case/task 直接使用原始身份，reward 按冻结 scoring 规范转换。每格执行：

```bash
python3 -B "$SKILL/scripts/harbor_receipt.py" \
  --spec "$CAMPAIGN/spec-frozen.json" --source-root "$TB_SOURCE" --purpose evaluation \
  --result "$TRIAL/result.json" --private "$PRIVATE_CELL" \
  --request "$PRIVATE_CELL/request.json" --expected "$PRELAUNCH/expected-binding.json" \
  --observed-state "$PRIVATE_CELL/observed-state.json"
python3 -B "$SKILL/scripts/harbor_result.py" \
  --spec "$CAMPAIGN/spec-frozen.json" --source-root "$TB_SOURCE" \
  --result "$TRIAL/result.json" --receipt "$PRIVATE_CELL/receipt.json" > "$CELL_OUTPUT"
```

只有退出码为 0 才发布 cell，转换失败的空输出不是成绩。receipt/plan/audit 都保留原件；转换器重新读取实际 traces，不接受只写相等的 expected/observed 声明。

首次用 `terminal_bench.py plan --spec ... --source-root ... --phase train --smoke-case <train-ID> --agent-id <独立smoke-Target> --expected-version ... --output ...` 建单题计划，回执参数改为 `--purpose smoke`。它验证真实执行与证据链，不执行 Wiki/Proposer，也不能作为正式 train 或 H0。保存真实 smoke 回执、源码/spec/技能/bundle/宿主绑定与隔离结论；**GDPevo smoke 不能代替本预检**。只有此次真实预检通过且预算已落实，才开始 r1。

正式顺序：完整 5 题 validation-H0 → 实测均分初始化 Store → 8 题 train → 导出 train views → Maintainer 更新 Wiki → Proposer 阅读至少四题完整轨迹并改一个技能 → 审计 → 完整 5 题 validation → `state.py decide`。严格超过历史最佳才接受，否则技能保持原版、Wiki 保留。再执行预定下一轮；具体状态命令与聚合接口沿用 [state.md](state.md)。

每轮 `train_views.py` 使用本阶段 plan/cells/sources，并增加 `--spec "$CAMPAIGN/spec-frozen.json" --source-root "$TB_SOURCE"`。仅把导出的 train 输入交给学习角色；不把验证/测试 trace 或逐题分数送进 Wiki。固定 spec 的 `train_feedback_mode=score_only` 进入 hash。

## 5. 最终冻结、比较与收尾

达到 K 或 validation 满分、无 pending 后，在任何 test 计划或执行之前：

```bash
python3 -B "$SKILL/scripts/spec_campaign.py" freeze --campaign "$CAMPAIGN" --replica r1
```

工具写不可覆盖的 `freeze.json`，绑定最终 state 字节、accepted、spec/campaign 与冻结时间。此后不能再改算法状态；分别部署 H0 和最终技能，生成 test-H0/test-final 的完整 5 题计划、执行和聚合。真实 test 开始时间必须晚于冻结及最后决策，不能事后冻一份给提前偷跑的 test 补票。

```bash
python3 -B "$SKILL/scripts/spec_campaign.py" verify-replica --campaign "$CAMPAIGN" --replica r1
python3 -B "$SKILL/scripts/spec_campaign.py" summary --campaign "$CAMPAIGN"
```

收尾重算每格真实回执，核对完整矩阵、技能内容→规范注入→实际 trace 的链、决策/版本及 test 时序。它只证明证据一致性，不是签名、OS 隔离证明或模型效果保证。最终报告 H0 test、final test 与差值；仅一个副本不能声称已完成三次重复。

费用监督读 [operations.md](operations.md)：使用 `run_guard.py`，本次 cap/global-cap 均为 25 时，宿主角色、容器、失败尝试和 smoke 都必须计入已声明的总预算；不要在新 r1 账户里漏掉此前付费部分。先保存独立 smoke 成本并计入 global_spent_usd。收尾用 `finish --spec-campaign "$CAMPAIGN"`；没有 `campaign.py guard`。在途成本未结算时软阈值可能超额；供应商限额才提供另一层硬约束。并发 10 是上限，不承诺整晚必定完成。

本示例最多 `5 + 2×(8+5) + 2×5 = 41` 个正式 Target cell，另加一个独立 smoke；优化/审计角色调用和基础设施重试另计。工具提供准备、转换和验收，不包含另一套自动付费启动器；Controller 用 Penguin 现有角色调用执行以上步骤，外部监督器服从持久 guard。
