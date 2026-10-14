# Penguin 执行接口

供 Controller 和单次 evaluator 使用。基础 State/技能注入规则两种接口共用；本文件的 cell 启动、原生 Session 路径和 rubric 打分只适用于本地 Workspace。GDPevo 改读 [gdpevo.md](gdpevo.md) 和 [harbor.md](harbor.md)，使用 Harbor 容器、trial 与原始 verifier。不要同时加载另一份存在冲突的评测指令。

## Controller：接入和冻结

需要现成可用的 Penguin CLI/Server、顶层 `run_subagent` / `input_subagent`、Linux/Unix 上的 Python 3.10+；本地接口另需 Workspace benchmark，GDPevo 另需其 Harbor 依赖。检查能力即可，不为一次实验自动安装服务或修改 core。本技能由 `wikiskill-rsi-toolkit` plugin 提供。当前普通 Agent 加载本技能后即承担 Controller；不要求特殊 Agent 类型。现有项目可通过 `penguin plugin install <plugin绝对目录>` 导入。技能源码留在 plugin 的 `skills/rsi-wikiskill/`，不要再维护第二份编辑副本。

从 Environment 的 App Data Dir 推导：

```text
PROJECT_DIR = <App Data Dir>
TARGET = PROJECT_DIR/agents/<target_id>
STATE = TARGET/agent_state
BENCHMARK = PROJECT_DIR/benchmarks/<benchmark_id>
```

### 按需准备身份

用户发出准备或执行 WikiSkill 实验的请求后，你负责准备身份，不把创建 Agent 的命令转交给用户逐项操作。只处理当前 smoke/replica；尚未开始的重复实验无需预建 Agent。普通 Web 入口可以让当前 Agent 做 Controller；可选 GDPevo campaign 工具会冻结专用 Controller ID，使用该工具时须从对应 Agent 的新 Session 启动，不能把当前身份冒写成冻结身份。

1. 从 Environment 确认 App Data Dir、当前 Agent ID 和 Session ID；确认这是没有其他实验历史的新会话，且当前加载的用户/Workspace 记忆没有其他 replica 的 Wiki、技能或结果。新 Session 本身不会清除持久记忆。若已注入其他实验经验，不能宣称独立重复；换干净的 Controller/Workspace 后再开始，不删除用户的通用记忆。
2. 解析本次实验清单。已有 `experiment.json` 或 campaign 时沿用其 `target_id` 和 OUT；页面的 `target_setup: skill-managed` 只表示由技能准备身份，不能覆盖清单中的具体 ID。新实验没有 Target ID 时生成 `wiki_target_<UUID的32位小写hex>`，以 `[a-z][a-z0-9_]{1,63}` 限制并检查不等于当前 Agent ID。按用户给定 OUT，或在当前用户工作区的独立实验子目录中分配 OUT，记录创建意图及身份；OUT 不能位于 Target State、任何做题 Workspace 内，也不能复用其他实验目录。
3. 用现有 CLI 查询项目，按结果判断是否需要创建。命令中的 `PROJECT_ID` 从当前 Session 的项目身份核实；不要切换到别的 Penguin 数据根。不要输出完整环境变量。

```bash
PROJECT_ID="<已核实的项目ID>"
TARGET_ID="<本次已记录的Target ID>"
penguin agent ls --project-id "$PROJECT_ID" --json
```

4. 如果 ID 不存在，由你调用原生创建命令，不传 `--plugins`、不导入快照、不复制 Controller 的 State、技能、记忆或对话：

```bash
penguin agent create --project-id "$PROJECT_ID" \
  --agent-id "$TARGET_ID" --name "$TARGET_ID" \
  --description "Dedicated WikiSkill experiment Target" --json
```

非 `default_agent` 的普通新 Agent 在未指定 plugin 时技能为空；仍以返回值和实际 State 检查为准。若返回 ID 已占用，不覆盖、删除或盲目换号；先查询并核对它是否属于本次准备事务。没有归属证据的碰撞保留原 Agent，新实验另分配 ID 并记录；已冻结身份发生冲突则停止，不能改写冻结清单。

5. 创建后或用户明确选了已有 Target 时，再执行：

```bash
penguin agent ls --project-id "$PROJECT_ID" --json
penguin session ls --project-id "$PROJECT_ID" \
  --agent-id "$TARGET_ID" --all --json
```

`agent ls` 的 `activeSessionCount`、`sessionCount`、`skillCount`、`memoryCount` 提供初筛；`memoryCount` 不统计各作用域的 `MEMORY.md` 索引，不能仅凭计数为零判定空记忆。核对规范路径、无符号链接越界，以及该 Target 的非敏感 State 和记忆索引内容。**新实验**要求零历史 Session、零活动 Session、空技能与记忆、无 Wiki/旧注入段/学到的业务规则；已有未使用的预建 Target 满足同样条件即可沿用，不必删除重建。不把其他实验曾使用的 Target 清空后充作新身份。**续跑**允许历史 Session 和已接受技能，但要求属于本实验，配置、版本和注入 hash 与冻结清单及部署账本吻合；有运行中的 cell 时先确认其状态，不能重复启动或重置。

6. 在 `experiment.json` 记录实际 Controller ID/Session ID、Target ID、创建/复用依据、OUT 及所选 runtime，再按下一节冻结基础 Target。先完成本次身份准备，再调用评测角色。准备过程中不执行 `penguin run` 给 Target 发送试探消息，也不伪造 baseline。

这些命令使用当前 Session 的原生 CLI 控制环境和项目访问权限。`agent create` 无需开启 Agent 对外 API；CLI 自行处理内部鉴权，模型不读取 `.project_config.toml`、`.vault.toml`、token 文件或打印 Key。若 CLI 不可用、项目访问被拒或命令审批受限，报告具体阻塞，让操作者修复环境；不创建第二个服务或绕过审批。

Controller 的复用只复用定义：每次独立重复从新 Session、新 Workspace 开始，禁止读取其他实验的 scratchpad、trace、Wiki 或结果。不能将一个 replica 的完整父对话传给下一次。Evaluator、Maintainer、Proposer、Auditor 是本次 Controller 的叶子 `run_subagent` 会话，不出现在需手工维护的持久 Agent 清单中。

### 冻结运行配置

模型绑定来自已选实验 profile；新实验按用户的网页选择或启动参数记录完整 `provider/model_id` 和 thinking。没有显式选择时可采用当前 Controller Environment 的完整模型对，并在实验配置中说明来源。Target `system_config.yaml` 的 `model` 块不含 provider/model ID，不能只凭它或卡片显示名确定绑定。可用现有 API/CLI 的脱敏模型元数据核对模型与 Key 配置状态；不直接读取凭证文件或回显 Key。

技能不要求特定服务商。使用官方端点或 OpenRouter 都按所选分组配置凭证，实验记录真实 provider、完整 model_id 及非敏感端点/路由参数。所选模型须支持运行器所需工具和参数；thinking 不受支持时在新实验开始前选择可用设置，不在失败后静默降级。续跑仅接受原配置，改选则新建独立实验。

使用独立 Target。冻结 `system_config.yaml`（除递增的顶层 version）、基础 `AGENTS.md`、工具/hooks、模型参数、固定任务提示、依赖版本及 benchmark。无运行中的 Target Session 才能切换技能。Target 自动记忆、跨 Session 的可变产物和网络持久状态也可能携带经验：禁用此类跨 cell 输入，或提供明确的隔离/恢复策略。无法清除任务环境残留时停止该 benchmark 接入。

Controller 只读取 split 清单和指纹；validation/test 内容指纹由数据管理员或 evaluator 计算后返回，不让 Controller 打开内容。以原始任务标识和去重指纹验证各 split 不重叠。benchmark 标定 `status = "failed"` 时拒绝运行；其他状态允许但必须在实验中冻结。

### 将技能接入 Target

论文把全部活跃技能注入系统上下文。此处采用固定基础 `AGENTS.md` 加一个确定性派生段：

```text
<原始基础 AGENTS.md，逐字保留>

<!-- WIKISKILL_DERIVED_BEGIN -->
<state.py render 的完整输出；空技能时同样有此限定段>
<!-- WIKISKILL_DERIVED_END -->
```

首次基线前保存基础文件至 Controller 的私有实验存档；基础文件已含上述标记时停止并核对，不能叠加多段。每次从冻结基础文件和完整 render 输出重新构造文件，原子替换，只改派生段。不要人工摘要、截断、增加策略或插入 Wiki。检查拼接标记唯一，拒绝技能正文伪造限定标记。

`accepted/skills` 和 `candidates/.../skills` 是 Controller 的真正技能文件；Target 接收它们的 `SKILL.md` 全文。默认不把这些目录复制到 Target，以免把 `PURPOSE.md` 一并暴露，也避免与按需加载重复。Target 不安装本优化 skill，技能目录初始为空。这个接入方式修改的是可重建的加载段；优化器的搜索空间仍只有技能文件。

每次装载（含恢复旧技能）将 Target 顶层 `version` 单调加一，记录 `version → skill_set_hash → evaluation_id`；不要把 helper 的 c0001 当作 State version。留存该版无凭证的 State 快照与全文件 hash 清单，禁止覆盖历史快照或归档 vault。基线形成 H0，后续除派生段和 version 外必须与 H0 相同。

每个 cell 创建全新 Session，以加载本轮 State；上下文压缩/恢复也可能重载，因此禁止在任何 Target Session 存活时切换技能。冻结前检查 `system_config.system_prompt` 含 `{{AGENTS_MD}}`，部署时保存完整派生段及其 hash。Controller 在调度前将实际部署段与候选 render 比对；Evaluator 从启动前的 `STATE/AGENTS.md` 快照提取该段，无需读取 Controller 私有目录。在 root 的**每个 trace shard**中读取 `type: session_meta` 记录的 `payload.system_prompt`，检查 BEGIN/END 各一次，截取完整派生段与启动前快照逐字比较，同时核对身份字段。缺少元数据、system 内容或有差异，均返回 evaluation_failed；val/test 的 system/trace 不回传 Controller。不能仅看文件存在就当作加载成功。技能集合超过上下文预算时停止/缩减候选，不能静默丢掉某个 skill。

比较包含首尾标记、不额外包含标记外的换行。注意 Penguin 模板后续替换可能处理 SKILL 中的 `{{...}}`；提案审核应检查与该版本模板变量的冲突，实际注入差异仍按失败处理。

### 调度 cell

以下原生 worker 请求、CLI 和轨迹路径仅用于 Workspace。GDPevo 复用八字段请求和 aggregate 结果格式，按 Harbor 文档派发 worker；逻辑 phase 额外通过冻结的 plan 绑定。

顶层 Controller 为每个 phase 生成完整 plan，结构见 [state.md](state.md)。Controller Session 自身使用 `role_models.controller`；启动 Evaluator、Wiki Maintainer、Skill Proposer、Companion Auditor 时，按相应 `role_models` 条目向 `run_subagent` 显式传入 `provider`、`model_id`、`thinking_level`。不能只在提示词中声明模型或依赖默认继承。每个 cell 调一次叶子 evaluator；其工具参数绑定 Evaluator，而提示词内八字段请求绑定 Target：

```text
run_subagent(
  provider="<role_models.evaluator.provider>",
  model_id="<role_models.evaluator.model_id>",
  thinking_level="<role_models.evaluator.thinking_level>",
  prompt="读取 <本文件绝对路径> 的‘Evaluator’段并严格执行。
只评测以下一个 cell，最后仅返回协议 JSON：
protocol_version: 1
case_id: <id>
run: <正整数标签>
expected_version: <冻结的State版本>
test_agent_id: <target>
benchmark_id: <本phase的benchmark>
provider: <role_models.target.provider>
model_id: <role_models.target.model_id>")
```

不把整段父对话传给 worker。先按串行运行；提高并发时仍需独立 Workspace 和不变 State。所有 worker 真正结束后才可部署/回滚技能。叶子 evaluator 通过 CLI 启动 Target，不能再调用 `run_subagent`。

保存每份 worker 原始结果为独立 JSON 文件；使用 `aggregate.py` 校验完整矩阵。若 worker 发了非协议文字，只让同一会话重发已有结果，不重启 Target、不重评分。基础设施失败时保留原结果与诊断，进入恢复流程。

### Train 轨迹与访问审计

根据成功协议中的 `session_id` 取训练 root Session，归档 `TARGET/traces/<date>/<session_id>_<index>.jsonl` 的全部分片（包含压缩前后）。验证 session_meta 的 canonical workspace、agent_state、provider、model。沿 subagent 事件引用补充 Target 的直接关联子 Session 及其分片；如有更深实际链路，完整追踪，不能混入 evaluator Session。

通用 Workspace 在 `raw/rXXXX/<case>-<run>.md` 建不可变入口，列出 case 身份、原始文件 hash、全部分片及子轨迹路径、预测与反馈。GDPevo/Harbor 使用 [training-views.md](training-views.md) 导出器生成 `raw/rXXXX/items/<编号>/index.md` 及完整原始分片，不另外手工导出或截断 JSON。Proposer 必须读取入口所指完整轨迹，入口本身的几行摘要不等于完成阅读。原始文件置于同一轮 raw 子目录，写入后只读并登记 hash。后续轮次加新目录，不覆盖旧文件。

伴生审计接收经过筛选的工具访问路径/调用元数据；validation/test 输出与评分内容仍不共享。Controller 不为了审计而打开 validation/test trace：由对应 evaluator 就地审计访问范围，异常按 evaluation_failed 返回并在私有审计档案留证。只有 train 的完整 trace 可供优化器读取。

## Evaluator：执行且评分一个 cell

本节是叶子 worker 的完整任务。全程不发进度、评分解释、gold 或优化建议；最终只返回下述协议 JSON。JSON 同时是 YAML 1.2 子集，字段与主线单次评测契约一致，便于用标准库严格解析。

1. 请求必须恰含八个字段且无冲突/重复；protocol_version=1，run 为正整数。case、agent、benchmark ID 不得越界。缺项返回 invalid_request，不启动执行。模型对必须完整。
2. 仅定位请求的 Target/Case。拒绝路径穿越、符号链接逃逸。要求 `agent_state/system_config.yaml`、`benchmark_config.toml`、`<case>/statement/README.md`、`<case>/rubric/README.md`。rubric 总分恰为 100；标定 failed 或材料不全返回 benchmark_invalid。若任务是 GDPevo，启动前改走 Harbor 接口；其他未适配的外部 runner 返回 benchmark_invalid，不能复制题面后假装完成环境接入。
3. 顶层 version（缺省 1）必须等于 expected_version；记录 Target 配置的 `model.thinking_level`（缺省 medium）。快照所有 State 文件内容 hash（排除 vault，禁止读凭证）、statement/rubric 内容 hash。之后不能改 Agent、benchmark 或 scoreboard。
4. 在 `TARGET/workspaces/` 下创建唯一绝对 Workspace，仅复制 statement 的**内容**，让 README.md 位于 Workspace 根。Target 不得见 rubric/gold/evaluator 资料；同时核对已配置的隔离策略。快照执行前 Workspace，记录启动尝试。
5. 用现成已验证的 CLI，逐行设变量并启动一次前台执行：

```bash
PROJECT_DIR="<App Data Dir>"
PROJECT_ID="$(basename "$PROJECT_DIR")"
PENGUIN_HOME="$(dirname "$PROJECT_DIR")"
export PENGUIN_HOME
penguin run \
  --message "Read README.md in the current Workspace and complete the task exactly as specified there." \
  --provider "<provider>" --model-id "<model_id>" \
  --thinking "<固定的Target thinking level>" \
  --project-id "$PROJECT_ID" --agent-id "<target_id>" \
  --workspace "<canonical unique Workspace>" \
  --approve allow-all --json
```

模板参数由已验证配置填入并正确 shell 引用，不把题面拼进命令。`--approve allow-all` 沿用本地评测方式，只有已授权且隔离的实验环境才可用；它本身不提供隔离。若既有实验要求更严格的审批模式，以该模式为准并记录。

不传 `--session`、`--background` 或软等待 `--timeout`。CLI 的超时可能返回 running 且服务端仍继续；执行工具 yield 时继续等待同一进程。得到 running/连接断开等不确定状态时先核实该 Session 是否终止，不能立刻重跑、改版本或回滚。只有证据证明 Target 从未启动才可诊断后重试；无法确认时 evaluation_failed 并暂停父级实验。

6. 从 CLI JSON 获取确切 sessionId，`text` 只是答案而非得分。检查完整 root trace 及其上下文轮转的 session_meta，与请求的 Workspace/State/provider/model 一致；不得从“最新 trace”猜测。子会话不算额外 root。检查实际读取边界、State 与数据快照未变；非 version 的 State 变化同样是 version_changed。thinking 在 Trace 中不记录，靠显式 CLI 参数及运行前后配置一致性验证，不伪造 Trace 字段。
7. 私有地根据 rubric 给本次输出评分。Target 答错/漏输出是合法低分；启动/评分/绑定失败不能给 0。成绩须是 0..100 有限数，四舍五入到两位小数。使用该 Session 的实际 duration；cost 仅取可靠 trace usage 记录，否则 null，不联网查价。不得修改参考答案来让候选通过。

成功结果，替换示例值，仅发 JSON：

```json
{"protocol_version":1,"status":"ok","case_id":"case-a","run":1,"agent_id":"target-id","expected_version":2,"provider":"provider-name","model_id":"model-name","thinking_level":"medium","score":75.0,"cost":null,"duration_ms":1000,"session_id":"actual-session-id"}
```

失败结果：

```json
{"protocol_version":1,"status":"failed","case_id":"case-a","run":1,"agent_id":"target-id","expected_version":2,"provider":"provider-name","model_id":"model-name","thinking_level":"medium","failure_code":"evaluation_failed"}
```

身份字段缺失或冲突时对应值为 null。失败码仅 invalid_request / benchmark_invalid / version_changed / evaluation_failed；不得包含 score、cost、duration、session_id 或私有数据。父级看到失败后不作接受判断；必要的诊断由评测上下文保留，不能把答案带回优化上下文。
