# 运行监督、提速与版本交接

这些工具解决执行可靠性，不改变 WikiSkill 的训练→Wiki→单技能候选→独立验证流程，也不保证技能提分。`state.py` 仍以严格超过历史最好 validation 分为接受标准。

## 费用和接力

使用 `scripts/run_guard.py` 作为外部监督器的决策门。它不创建 Agent、不调用模型、不发 steering、不终止容器。已有 relay/watch 不会因安装插件自动接入；操作者必须把其启动、续跑和跨副本分支接到此工具，验证退出码与 `action`，再启用自动接力。不要同时保留一个能绕过它发继续指令的旧 relay。

初始化时保存真实 Controller 的五字段身份 JSON：`sessionId`、`agentId`、`projectId`、`workspace`、`source`。来自原生 Session 查询，`source` 可为 `cli`、`user` 或 `api`；不能使用最新会话推断根 Controller，也不能把 `id` 当 `sessionId`。启动后绑定一次，本次实验不自动改绑另一个 Session。

```bash
python3 -B <skill>/scripts/run_guard.py --guard <replica>/guard.json init \
  --identity <private>/controller-identity.json --cap <本副本USD阈值> --global-cap <整个campaignUSD阈值>
python3 -B <skill>/scripts/run_guard.py --guard <replica>/guard.json check \
  --snapshot <private>/supervisor-snapshot.json
```

每次快照是监督器的现场观测，不能预写一份循环复用：

| 字段 | 来源与要求 |
|---|---|
| `captured_at` | UTC ISO 时间，观测不超过 120 秒；采样失败不能仅更新时间 |
| `sessions` | 原生 Session 列表 JSON 数组，保留身份、status、pendingFollowUpCount、原生 backgroundTasks；根或叶子尚有队列/后台任务，以及叶子 running、compacting 或状态未知时不发 nudge |
| `host_cost` | 当前 Controller 及其叶子的原生 `penguin cost --agent-id ... --json` 输出；`summary.total.hasUncosted=false` 且 cost 已知；原生空账本 requests=0、total=0、cost=null 可识别为零，已产生请求却无费用则不行；确认查询时间范围覆盖整个实验 |
| `inventory_complete` | 与执行前记录的全部启动账本对齐后才为 true，不能仅扫描已有成功 result |
| `attempts` | 所有 Target 尝试，包括失败和重试；每项恰为 `{launch_id,status,result}`。active 时 result=null；finished 时为本机真实 trial/result.json 绝对路径，不是 job 层汇总 |
| `global_cost_complete`、`global_spent_usd` | 同一 campaign 所有副本的宿主＋容器已结算费用；逐项完整才为 true，未知不是零 |

容器调用不在宿主费用里。工具汇总每个已结束 trial 的 `agent_result.cost_usd`，拒绝缺费用、重复 trial、时间过期、费用倒退或不完整快照。调度前先记启动账本，任务不明或结果丢失时不能伪称清单完整。

guard 持久保存已观察到的启动清单：后续快照不得漏掉已见的 launch，不得把 finished 改回 active，也不得替换已绑定的结果路径或内容。其他费用或会话信息缺失时，仍保留有效的启动观测；修复采集器后用完整清单重查，不能通过删条目恢复调度。旧 guard 缺少此清单时从首次有效观测建立，因此不能证明此前没有遗漏。

已知费用的下界达到阈值就持久停止，即使该快照已过期或其他费用/会话观测仍未知；过期快照只能提供停止依据，不能授权继续。根和叶子 Session 的后台任务字段可能在进程重载后消失，字段缺省不能代替操作者核查真实进程和子任务。

- `action=continue`：可在既定授权/并发内调度；只有 `may_nudge=true` 才可向返回的根 `sessionId` 投递续跑消息。它不是启动许可的替代品。
- `action=wait` 或退出码 2：暂停新调度和 nudge，保留在途工作，修复观测后重查。
- `action=stop`（退出码 3）：停止状态已持久化；发送停止新调度的通知，让在途工作收尾。之后任何轮询或监督器重启都不会自动恢复，不得删 guard 绕过停止。

这是**软费用阈值**：Target 在途费用未结算，异步通知也有延迟，可能超过阈值。不能标成严格 USD 熔断；需要绝不超额时必须使用供应商侧限额或在启动前可靠预留每次最坏费用，做不到就说明限制。达到阈值后仍可本地整理已有证据，额外模型调用也计费。

完成一个副本后，先确认所有角色会话/队列、Harbor 进程、容器退出，再复算：

```bash
python3 -B <skill>/scripts/campaign.py verify-replica --campaign <campaign> --replica r1
python3 -B <skill>/scripts/run_guard.py --guard <replica>/guard.json finish \
  --campaign <campaign> --replica r1 --snapshot <private>/supervisor-snapshot.json
```

`verify-replica` 检查 state、无 pending、训练与验证矩阵、决策绑定、最终技能及两套 test。`finish` 重新调用它，只有 `action=complete` 可作为接力条件，不能忽略返回的 False、异常或非零退出码。提前预算停止保留部分结果，不冒充正常完成。下一副本仍须已有用户授权，并在其自身 guard 中核对最新总费用。

上述两个 `--campaign` 命令是 GDPevo 入口。外部清单（如 Terminal-Bench）改用 `spec_campaign.py verify-replica --campaign <campaign> --replica r1`，guard 收尾改为 `finish --spec-campaign <campaign> --replica r1 --snapshot ...`；不可同时传两种 campaign 参数。外部 campaign 还复核实际技能注入段及 test 晚于最终冻结。没有 `campaign.py guard` 子命令，费用入口始终是 `run_guard.py`。

## 有限并发与重试

按授权的并发数在**同一 phase、同一 State/archive**内滚动补位；所有 cell 收尾并通过审计后才能进入 Wiki、提案、换 State 或下一 phase。使用相互独立的 Workspace、容器、job 名、私有证据目录；同一 logical cell 未确认结束前不重投。不要因某题慢而跳过、复制结果或扩大模型/turn/时间预算。并发和变更时间进入运行记录。

通过原始 Dockerfile 预热实际 Harbor 会复用的镜像/构建缓存，并验证实际命中；另起名字的镜像不保证被 Harbor 使用。基础镜像 metadata、网络等待、评分、模型执行分别计时，先找瓶颈，不承诺预热能消除所有等待。稳定供电与防睡眠属于宿主环境准备。

重试按预先记录的故障规则，不看成绩高低：区分 setup/网络故障与真实已执行任务的超时。保留原 trial、请求/工具时间线、成本、排除原因和替代关系；出现输出 token 或成功工具调用时，不能写“从未实际开始”。证据不够标 unverified，不能自行择优留分。

## 审计边界与升级

隔离探针必须匹配真实 Harbor 版本、adapter、挂载、UID、网络和 verifier 生命周期。单独启动裸预热镜像不能证明实际 trial 的 `/logs/verifier` 权限；清理已有 reward 文件也不能证明后台进程无法重写它。shared verifier 的进程清理和写权限需要单独取证。脚本检查、LLM 审计、OS 隔离是不同证据。

访问审计按真实 parent/sessionId 归类并覆盖全部分片，保留完整命令和工具返回状态供本机私有复核；公开报告只导出脱敏清单及 hash。截断命令、未知来源或缺失分片不能宣称无泄漏。

GDPevo 新实验使用 schema-2，外部清单使用绑定 spec 的 schema-3；历史 schema-1 可加 `--allow-legacy` 复核旧分数，结果仍只有原来的审计强度。不得覆盖旧 receipt 或借新探针为历史运行补造 pass。新版本单独检出、离线检查、独立 smoke 后启用，不在运行中拉代码、重建 bundle、重装技能或改 profile。

不向学习角色提供其他实验的 test 分析，不修改既有实验的 K、划分、模型或 thinking。新实验固定 `experiment.json`（含并发与预算）的文件 hash，与 profile/spec hash 一起记录；hash 本身不覆盖清单未包含的字段。交接时同时提供 profile/spec、campaign、preflight 原件及脱敏说明，原始与脱敏 hash 分开。外部 spec 已绑定 train_feedback_mode 和并发，GDPevo 的历史 profile 则未绑定这些字段。
