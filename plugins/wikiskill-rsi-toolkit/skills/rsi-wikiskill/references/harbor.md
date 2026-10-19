# GDPevo 的 Harbor cell 接口

> 新实验最终测试按 [final-test.md](final-test.md) 执行：冻结 `reuse_unchanged` 时，最终业务技能为空且有效状态/评测条件一致，只做 test-H0 并保存 skip 回执；下文“两套 test”对有技能变化或预先选择 `repeat` 的实验成立。旧冻结实验保持旧协议。

仅用于 [gdpevo.md](gdpevo.md) 指定的数据和接口版本。Controller 编排 WikiSkill；每个叶子 Evaluator 执行一个 Harbor trial，使用原始 verifier，并输出与 `aggregate.py` 兼容的 cell。当前提供的是执行约定和离线校验工具，真实容器预检仍需完成。

本页命令和任务约定属于固定 GDPevo 适配。Terminal-Bench 入口见 [terminal-bench.md](terminal-bench.md)，不要套用本页的题目路径、24/6/30、60 turns 或业务服务约定。共用的 `harbor_receipt.py` / `harbor_result.py` 通过 `--spec` 与 `--source-root` 切换到 schema-3 外部清单绑定；注入、四项审计和完整 trace 观测规则仍需遵守。

## 首次启动前

确认 Docker、Harbor 0.23.0、完整 benchmark checkout、可用的 Penguin 安装 bundle 和模型配置。目录结构必须完整，Docker build 依赖共享环境 build context，不能只复制某个 task 子目录。模型密钥经现有 Penguin 配置交给适配器，不写入命令参数、skill、job 配置或审计报告；只允许适配器读取所需模型配置并在容器内按受限权限存放，不回显凭证。

GDPevo Target 为容器内非特权 agent，在 `/app` 做任务，通过 loopback 访问业务服务，按模板写 `/app/answer.json`。环境用户持有 `/opt/gdpevo`，Target 不得读取私有数据。确认 `TASK_ENV_ENABLE_JUDGE=0`、无 gold/judge 文件残留或镜像层泄漏、无宿主 repo/OUT 挂载。Evaluator/管理员保管 benchmark 中的答案与评分器。

必须验证 agent/verifier 的网络隔离，仅放行冻结模型端点的确切 host 和环境内部 loopback。不得套用 shared-network overlay；GDPevo 公开 pilot 曾发生下载 GitHub gold 的泄漏。禁止靠“不要联网”的 prompt 代替网络策略。实际出现成功访问非允许来源、读私有答案、跨 cell 状态残留等，相关结果无效；被阻断的尝试记录并分析，不能把未知审计结果标为 pass。

## 每版 Target 的打包与绑定

先按 [penguin.md](penguin.md) 冻结基础 State，完整注入当前所有 `SKILL.md`，仅更改派生段和单调递增的 State version。先在基础 State 配好固定 60-turn 上限；模板不传 `--ak max_turns`，因为该选项会在容器启动后通过 API 修改配置，可能改变冻结状态/版本。Target 不含本优化 skill、Wiki、PURPOSE 或其他学到的业务技能。

从允许的 State 文件建立无凭证 staging：保存基础配置、工具及必要资源，检查不存在符号链接/硬链接、路径逃逸、vault、API token、外部 State 引用或额外学习状态。再归档 staging 的**内容**：tar 根为 `AGENTS.md`、`system_config.yaml` 等，不要多包一层 `agent_state/`。适配器把它直接解到容器 Agent State；不要直接将含未知文件的整个宿主目录打包。

保存 staging 文件 hash 清单、规范化 State hash、归档字节 SHA-256、完整派生段 SHA-256、version。宿主 State 路径与容器路径不同，回执预先记录实际目标容器路径。Evaluator 在容器内核对已部署文件、version 与派生段，并从 Target 完整 root trace 的每个 `session_meta.payload.system_prompt` 验证完整注入段逐字一致。容器版本不提供这些证据时停止接入，不按文件存在推断已加载。

所有同版 cell 结束前，不能更改 State/archive。拒绝候选后生成 accepted 的新部署版，递增 version 并重新打包。H0 和最终 test 也分别有明确版本。trial 是容器执行单位；转换后的 `session_id` 为 `harbor:<trial目录名>`，不能用它去查宿主原生 Session 目录。实际 root Session ID 与完整 trace 清单保存在私有回执/审计证据中。

## 单格命令模板

变量来自冻结配置；`MODEL_REF` 为所选 `provider/model_id`，model_id 本身可能含斜杠，必须完整保留。`THINKING_LEVEL` 取 profile。`TASK_NAME` 必须由当前 phase 的 case 映射取得。`EXPERIMENT_HOME` 是独立 Penguin 数据根，不能复用个人生产 Agent。

```bash
API_HOST="$(python3 "$BENCH/tools/agent_host.py" -m "$MODEL_REF" \
  --host-penguin-home "$EXPERIMENT_HOME" --host-project-id "$PROJECT_ID")"
PYTHONPATH="$BENCH/agents" uvx --from harbor==0.23.0 harbor run \
  -p "$BENCH/benchmarks/gdpevo/tasks" -i "$TASK_NAME" \
  -a penguin_agent:PenguinAgent -m "$MODEL_REF" \
  --ak thinking="$THINKING_LEVEL" --ak penguin_version=0.2.13 \
  --ak run_timeout=15m \
  --ak host_penguin_home="$EXPERIMENT_HOME" --ak host_project_id="$PROJECT_ID" \
  --ak agent_id="$TARGET_ID" --ak agent_state_tar="$STATE_ARCHIVE" \
  --ak install_bundle="$BUNDLE" --allow-agent-host "$API_HOST" \
  --agent-setup-timeout-multiplier 2.5 -k 1 -n 1 \
  --job-name "$UNIQUE_JOB" -o "$JOBS_DIR" -y
```

`API_HOST` 必须非空且符合冻结端点；失败即停止，不用默认全网。模板使用适配器默认的容器 project；若显式设置 `--ak project_id`，同步修改预期容器路径。宿主 `host_project_id` 不等于容器 project。实际 thinking 必须与 plan/State 相同。`-k 1 -n 1` 对应一个 task、一次 attempt，三次 replica 由 Controller 外层调度。

以上超时/turns 是初步配置；宿主并发默认 1。需要提速时按 [operations.md](operations.md) 在同一 phase 内有限并发并记录授权和配置。不能失败后改网络或扩大预算直到过题。超时发生后确认适配器 abort/清理完成，不在容器仍运行时重启同一格。真实已执行任务的失败可以由 verifier 给 0；server/config/setup/评分器失败、没有模型请求、缺 reward 都不是合法 0 分。

## 结果、回执与分数

Evaluator 私有保存 trial 的 `result.json`、原始 verifier 结果、adapter 日志和完整 Target trace；先审核，再写回执。新实验回执使用 schema 2，恰含以下 12 个字段，未知字段拒绝。使用 `scripts/harbor_receipt.py` 生成，不让 Controller 临时重写校验器。合成样例见 `tests/test_runtime_evidence.py`，不可将其虚构 hash/审计结论用于正式实验。

| 字段 | 内容 |
|---|---|
| schema_version | 整数 2 |
| benchmark_revision | 固定 benchmark 完整 commit |
| request | penguin.md 的八字段请求；使用真实 source package benchmark ID |
| thinking_level | 与 State、plan、实际 adapter 相同 |
| task_name | 如 `tg003-train-005` |
| trial_dir | 实际 trial 目录 basename，如 `tg003-train-005__abc123` |
| trial_id | `result.json.id` 的 UUID |
| result_sha256 | `result.json` 原始字节的 SHA-256 |
| phase_plan | `{"artifact":"plan.json","sha256":"<真实hash>"}`；计划在 receipt 同目录 |
| state_binding | expected、observed 两个对象，结构相同且实际值必须一致，见下文 |
| audit | 四个对象，分别含 check、verdict=`pass`、artifact、artifact_sha256 |
| runtime_evidence | `{"artifact":"runtime-observation.json","sha256":"<真实hash>"}`，由原始 trace 计算 |

每个 State binding 对象恰含 `deployed_state_sha256`、`archive_sha256`、`injected_block_sha256`、`container_agent_state`、`container_workspace`、`state_version`。前三项是实际 hash；路径为容器规范绝对路径（Workspace 为 `/app`），version 等于请求。Agent State 路径形如 `/opt/penguin-bench/home/default_project/agents/wiki-target/agent_state`，其中 Target 必须与请求一致。expected 来自启动前冻结账本。observed 的路径与注入 hash 从本次 root trace 自动提取；`deployed_state_sha256`、`archive_sha256`、`state_version` 由 Evaluator 的独立容器核查输入，不得复制 expected。后面三项仍是外部审计，不是 trace 工具自动证明的事实。特别是该 adapter 会删除上传到容器的归档，须在传输/部署阶段取证；缺历史证据时不能事后拿宿主归档 hash 冒充容器观测。

`plan.json` 是 [state.md](state.md) 的九字段完整 phase plan；GDPevo validation 的 benchmark ID 仍为 `penguinharness-benchmark-gdpevo-train`。audit 的路径固定为 `audit/network.json`、`audit/actual_state_bound.json`、`audit/reward_validated.json`、`audit/trace_complete.json`。receipt、plan 和 audit 留在私有目录，不能放进聚合的 `cells/`。

Evaluator 先保存 `request.json`、预先冻结的六字段 `expected-binding.json`、独立测得的三字段 `observed-state.json`，以及计划和四份真实审计。保留完整的 `<trial>/agent/penguin/traces/**/*.jsonl`，不可只选第一片或删去不匹配片。执行：

```bash
python3 -B <skill>/scripts/harbor_receipt.py \
  --result <trial>/result.json --private <private-cell> \
  --request <private-cell>/request.json \
  --expected <prelaunch>/expected-binding.json \
  --observed-state <private-cell>/observed-state.json
```

生成器不创建审计 pass；任何现有审计非 pass 或数据不一致即失败，不覆盖旧 receipt。`runtime-observation.json` 只保存分片 hash、身份、路径和注入 hash，不包含题面、答案、prompt。转换器每次重新读取原始 trace，核对全部 root shards 的注入、模型、路径及已记录子会话是否有导出文件，不能只比较两份输入声明。实际版本和 thinking 继续按部署/adapter 核查，不能说 trace 提供了它没有记录的字段。

固定的容器 Penguin `0.2.13` 使用 `--source benchmark` 时，原始 trace 的 `session_meta.source` 为 `benchmark`；较新 CLI 改记为 `cli`。观察器接受 `cli`，并只为 metadata 中版本为 `0.2.13` 的旧运行接受 `benchmark`；同一根会话各分片的来源必须一致。其他或缺失的根来源仍拒绝；实际版本、模型、State、完整 trace 与隔离审计照常核验。不要把旧 trace 的 source 改成 cli，也不要为绕过校验升级 bundle 或使用 schema-1 历史模式；会话来源兼容与回执 schema 版本是两件事。宿主升级不自动更新容器 bundle，新实验应使用本技能完整版本；已有实验保留原始证据和配置。

```bash
python3 -B <skill>/scripts/harbor_result.py \
  --result <trial>/result.json \
  --receipt <private-cell>/receipt.json \
  > <eval>/cells/<case>-1.json
```

只有退出码 0 才能作为成功 cell。发生错误保留私有原始证据，停止当前评测；不要向空输出补分数。转换器核对固定 benchmark 版本、容器 metadata 的 `penguin_version=0.2.13`、case/task 映射、plan 中所有 case 所属的已确认逻辑 phase、Harbor 身份、实际模型使用、成功启动/请求记录及 reward，按 Decimal `reward×100` 半向上保留两位。结果沿用原生 13 字段协议；duration 由真实执行时间计算，cost 可为 null。转换器可处理同 phase 的子集，正式实验仍需 Controller 对照 profile 确认完整 24/6/30 清单。容器升级必须在新实验开始前更新并重验适配，不允许将其版本冒写为旧版。

审计证据至少包含：

| check | 必须完成的私有检查 |
|---|---|
| network | 实际网络策略、允许 host、日志/trace 中的外联与响应；所有允许之外成功读取均失败 |
| actual_state_bound | 归档/部署 hash、version、容器路径和模型一致；每个 root shard 的完整技能注入段一致 |
| reward_validated | 原始 verifier 正常执行、reward 有限且 0..1；区别任务错误与基础设施错误 |
| trace_complete | trial 对应的所有 root shards、关联 Target 子会话可追溯；不混入 evaluator 会话 |

audit artifact 只放脱敏后的私有审计 JSON 对象，至少有 `check`、`verdict`，并用其他字段保存证据引用与核查结论；两字段必须与回执一致且 verdict 为 pass。使用回执目录内的固定相对路径及 SHA-256，不把答案内容粘进报告/回执。转换器验证身份、字段一致性、文件 hash 和已导出 trace 的注入/运行时事实。它不能证明导出前没有丢分片，不能证明容器文件没有被改，也不自动判断网络和隔离证据是否足够；这些仍须独立审计。Evaluator 提交的 hash 绑定不是数字签名或安全沙箱，仍需实际权限和独立审计。

转换器输出的 cell 可交 Controller 聚合；receipt、validation/test trace 与报告留在私有评测区。只有逻辑 train 的 Target trace 可导出至 `raw/`，并保留 trial/root/subsession 的映射及所有文件 hash。`train-005` 的任何内容仍是 validation 私有资料。四条完整训练轨迹要求不因 Harbor 而降低。

所有 cell 转换成功后按 [state.md](state.md) 聚合。Controller 对照 experiment/profile/部署账本再次核对 plan；单个转换器不证明跨 cell 的全矩阵完整性或计划没有被替换。

历史 schema-1 回执只能通过显式 `--allow-legacy` 复核，报告注明未经过 schema-2 trace 重算，不用于新实验。保留旧原件，补证单独追加。
