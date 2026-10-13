# GDPevo 的 Harbor cell 接口

仅用于 [gdpevo.md](gdpevo.md) 指定的数据和接口版本。Controller 编排 WikiSkill；每个叶子 Evaluator 执行一个 Harbor trial，使用原始 verifier，并输出与 `aggregate.py` 兼容的 cell。当前提供的是执行约定和离线校验工具，真实容器预检仍需完成。

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

以上超时/turns 是初步配置；宿主并发默认 1。任何变更在开始前冻结，不能失败后改网络或扩大预算直到过题。超时发生后确认适配器 abort/清理完成，不在容器仍运行时重启同一格。真实已执行任务的失败可以由 verifier 给 0；server/config/setup/评分器失败、没有模型请求、缺 reward 都不是合法 0 分。

## 结果、回执与分数

Evaluator 私有保存 trial 的 `result.json`、原始 verifier 结果、adapter 日志和完整 Target trace；先审核，再写回执。回执恰含以下 11 个字段，未知字段拒绝；可运行的合成样例见 `tests/test_harbor_result.py` 的 `HarborResultTest.setUp`，不可将其虚构 hash/审计结论用于正式实验。

| 字段 | 内容 |
|---|---|
| schema_version | 整数 1 |
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

每个 State binding 对象恰含 `deployed_state_sha256`、`archive_sha256`、`injected_block_sha256`、`container_agent_state`、`container_workspace`、`state_version`。前三项是实际 hash；路径为容器规范绝对路径（Workspace 为 `/app`），version 等于请求。Agent State 路径形如 `/opt/penguin-bench/home/default_project/agents/wiki-target/agent_state`，其中 Target 必须与请求一致。expected 来自启动前冻结账本，observed 来自实际部署/trace 检查，不能直接复制 expected 伪造观测。

`plan.json` 是 [state.md](state.md) 的九字段完整 phase plan；GDPevo validation 的 benchmark ID 仍为 `penguinharness-benchmark-gdpevo-train`。audit 的路径固定为 `audit/network.json`、`audit/actual_state_bound.json`、`audit/reward_validated.json`、`audit/trace_complete.json`。receipt、plan 和 audit 留在私有目录，不能放进聚合的 `cells/`。

```bash
python3 <skill>/scripts/harbor_result.py \
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

audit artifact 只放脱敏后的私有审计 JSON 对象，至少有 `check`、`verdict`，并用其他字段保存证据引用与核查结论；两字段必须与回执一致且 verdict 为 pass。使用回执目录内的固定相对路径及 SHA-256，不把答案内容粘进报告/回执。转换器验证身份、字段一致性和文件 hash，不输出报告内容，也不自动判断证据是否足够，更不能凭空生成 pass。Evaluator 提交的 hash 绑定不是数字签名或安全沙箱，仍需实际权限和独立审计。

转换器输出的 cell 可交 Controller 聚合；receipt、validation/test trace 与报告留在私有评测区。只有逻辑 train 的 Target trace 可导出至 `raw/`，并保留 trial/root/subsession 的映射及所有文件 hash。`train-005` 的任何内容仍是 validation 私有资料。四条完整训练轨迹要求不因 Harbor 而降低。

所有 cell 转换成功后按 [state.md](state.md) 聚合。Controller 对照 experiment/profile/部署账本再次核对 plan；单个转换器不证明跨 cell 的全矩阵完整性或计划没有被替换。
