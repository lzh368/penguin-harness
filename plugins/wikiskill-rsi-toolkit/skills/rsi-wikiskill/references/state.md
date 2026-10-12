# 本地状态工具与恢复

工具只做文件管理和计算，不调用模型、不启动 Penguin，不自动部署 Target。`state.py`、`aggregate.py` 不读取 benchmark；`gdpevo_profile.py` 可核验指定 benchmark checkout；`harbor_result.py` 校验私有回执并转换单次真实 trial 的结果。命令均为 `python3 <skill目录>/scripts/<工具>.py`。需要 Python 3.10+；state.py 使用 Linux/Unix 的文件锁。工具输出 JSON，错误退出码非零；不能把报错输出当作成功。

GDPevo 的计划生成见 [gdpevo.md](gdpevo.md)，Harbor 结果接入见 [harbor.md](harbor.md)。每个 replica 独立一个 OUT，不能共享 state/Wiki、复用 H0 实测结果或沿用上一 replica 的最终技能。

## 实验目录

```text
OUT/
  experiment.json                 Controller：固定配置、授权范围和论文偏离项
  state.json                      state.py：唯一技能接受账本
  accepted/skills/<name>/
    SKILL.md
    PURPOSE.md
  candidates/c0001/
    skills/<name>/...              候选时的完整技能集合
    proposal.json
    meta.json                     父版本/候选/证据 hash
    diff.patch                    完整 unified diff
  raw/r0001/...                    Controller：只追加 Target 的 train 轨迹
  wiki/index.md
  wiki/patterns/*.md               Maintainer 更新
  wiki/logs.md                     Maintainer/Controller 日志，不能删历史
  wiki/skill-impact.md             state.py 追加验证后的改动结果
  proposals/r0001.json             Proposer 输出
  rendered/...                    派生注入文件
  evaluations/<unique-id>/
    plan.json
    cells/<case>-<run>.json        worker 原始协议对象，禁止人工改分
    summary.json                  aggregate.py 输出
  private/...                     无凭证基线存档/部署账本/私有诊断
```

学习角色仅获得 roles.md 指定路径，不因为 OUT 是同级目录就有权读 `evaluations/`。`raw` 只存 train；val/test 轨迹留在 evaluator 的受限范围内。整个 OUT 禁用符号链接，脚本在每次操作时校验；这不是对恶意并发进程的防护。不要同时运行多个 Controller 或让 Maintainer 与 Controller 同时写同一日志。

## 评测矩阵与汇总

在发出本次第一格请求前冻结 plan。`expected_version` 对应本次实际装载的 Target，另在部署账本绑定技能 hash：

```json
{
  "phase": "validation",
  "benchmark_id": "val-benchmark",
  "case_ids": ["case-a", "case-b"],
  "runs_per_case": 1,
  "agent_id": "target-id",
  "expected_version": 2,
  "provider": "provider-name",
  "model_id": "model-name",
  "thinking_level": "medium"
}
```

只把此次 cell 的 JSON 放进 `cells/`；summary、诊断、历史执行放在外面。固定所有实验的 validation case 顺序/集合、repeat 数、任务提示、评分者和评分方法。每格新 Session；同一执行不能冒充两个 run。

```bash
python3 <skill>/scripts/aggregate.py \
  --plan <OUT>/evaluations/<eval-id>/plan.json \
  --results <OUT>/evaluations/<eval-id>/cells \
  > <OUT>/evaluations/<eval-id>/summary.json
```

成功输出包含 `score`（十进制字符串）、`cell_count`、`evaluation_id`（输入内容 hash）、plan 和各 cell 的 SHA-256。汇总使用 40 位十进制精度，不再取两位小数；state 以 Decimal 比较并以字符串保存分数，避免浮点转换吞掉微小差异。缺失、额外、重复 cell、失败 cell、session 复用、模型/thinking/版本漂移、非法分数均拒绝。

工具只验证给定清单与结果的一致性，**不证明** worker 诚实、benchmark 正确或 plan 未被换掉。Controller 必须对照冻结 experiment、部署账本、请求日志及 trace 绑定；返回协议未含 benchmark_id，不能仅凭结果字段鉴别同名 case 所属 benchmark。

## 初始化、提案和门控

1. 首先建立 `experiment.json`、`evaluations/`、`private/`。基线前还没有 helper state，直接使用固定基础 AGENTS 加空派生段；先完成实测空技能 validation。
2. 确认 summary.phase=validation，验证集合与配置正确，再执行：

```bash
python3 <skill>/scripts/state.py init --out <OUT> \
  --baseline-score <实际基线summary.score> --score-max 100
```

不先填 0 占位。init 允许已有实验清单/评测存档，拒绝已有 state 或受管目录，防止覆盖；它创建空 accepted/raw/wiki/proposals。基线 evaluation_id 与 state 的绑定由 Controller 写入 experiment/部署账本。

3. 完成全 train、Wiki 维护和提案审核后构造候选：

```bash
python3 <skill>/scripts/state.py stage --out <OUT> \
  --proposal proposals/r0001.json
python3 <skill>/scripts/state.py render --out <OUT> \
  --source c0001 --output rendered/c0001.md
```

proposal 必须在 OUT/proposals 内。stage 在副本上改动，仅支持 roles.md 的 create/patch；只有 PURPOSE 改变、无效替换、仅首尾空白变化均拒绝。检查返回的真实 diff，再部署 render 的完整内容。`--output` 只能写 OUT/rendered 下，省略时输出正文到 stdout。render 不包含 PURPOSE/Wiki。

stage 开始一轮，使 round 加一并设置 pending；decide 完成同一轮不再加。候选 ID 根据 round 生成，no_action 后可能跳号；它不是 Penguin version。pending 未结束时禁止另 stage。候选/meta/diff/proposal/引用的 raw 证据由 hash 固定，不手工编辑。候选集合 hash 取 stage 返回的 `sha256` 或 `candidates/<id>/meta.json` 的 `content.sha256`；`state.candidates.<id>` 只存状态与 metadata hash。

4. candidate validation 完整成功，核实 summary 属于该候选、该次部署与冻结 validation，再决定：

```bash
python3 <skill>/scripts/state.py decide --out <OUT> \
  --candidate c0001 \
  --score <候选summary.score> \
  --evaluation-id <候选summary.evaluation_id>
python3 <skill>/scripts/state.py render --out <OUT> \
  --source accepted --output rendered/accepted.md
python3 <skill>/scripts/state.py verify --out <OUT>
```

decide 不自动读取评测文件：传入值必须来自已成功校验的 summary，不能来自预测、挑选的最佳单格或 train/test 分数。接受时更新 accepted，拒绝时 accepted 字节保持不变，两者都将完整差异和结果记入 Wiki。它不部署真实 Target；Controller 等运行全部结束后用 accepted 渲染结果对齐 Target 并记录新的 State version。

5. 无可靠提案时：

```bash
python3 <skill>/scripts/state.py no-action --out <OUT> --reason "本轮原因"
```

该命令消耗一轮但不产生候选/评分。不能将格式错误、评测故障或泄漏写成 no_action。它没有请求 ID，响应丢失时先检查 state 的 no_actions 和 logs，不可盲目重放。

## 中断与异常

| 现场 | 行动 |
|---|---|
| `.transaction.json` 存在 | 先确认没有活跃 writer，再 `state.py recover --out OUT`，随后 verify。事务检查外部冲突，按原意完成写入，不猜测新结果。冲突时停止，不强行覆盖。 |
| stage 响应丢失 | verify 并检查 pending/meta，复用已建立候选，不重复 stage。 |
| decide 响应丢失 | 用完全相同 candidate、score、evaluation_id 重放；工具识别同一决策，不重复追加 impact。 |
| accepted 已提交，但实际 Target 仍是候选/旧版 | 确认全部 Target Session 已结束，再从 accepted 重新装载、递增版本、更新部署账本。 |
| 某 cell 已启动但状态不明 | 保留 Workspace、trace、pending；核实完成/中止前不重跑或切换技能。 |
| 评测不完整或基础设施失败 | 不调用 decide。暂停实验、保留 pending/Wiki/原结果。任务停止后可恢复 Target 到 accepted。经诊断只补确定未启动的 cell；已执行但无有效分数的 cell 不静默重采样。若需要新的评测策略/整轮重试，先明确变更并记录为新评测，不能覆盖旧记录。 |
| stage 后审计不通过/候选文件被改/证据 hash 不符 | 停止该实验，保留记录。工具刻意不提供无痕撤销 pending；修正方案应在新实验中验证，或显式扩展带原因的中止状态后重新审查。不要伪造一个低分来结束 pending。 |

verify 校验受管文件、候选完整性、接受历史和引用证据，不能证明 Wiki 语义正确、所有未引用 raw 未变、运行权限或真实模型效果；这些由 Controller 的原始 hash 清单及审核补充。

## 修改工具后的本地检查

在 skill 目录运行 `python3 -B -m unittest discover -s tests -v`。这些合成数据测试覆盖门控、Wiki 留存、重复提交、进程中断恢复、路径/证据校验和完整评测矩阵；不使用模型，不代表真实 benchmark 提升。
