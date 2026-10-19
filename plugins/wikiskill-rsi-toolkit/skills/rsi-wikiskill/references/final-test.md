# 最终状态未变化时，只做一次测试

这是一项可复现的评测资源约定，不修改 WikiSkill 的 train → Wiki → proposal → validation 接受规则，也不宣称是论文的强制要求。新实验开始前将 `final_test_policy: reuse_unchanged` 纳入冻结配置；需要测同状态运行波动时预先选 `repeat`。旧实验缺少该字段时继续执行原来的两次测试，不中途更改。

## 什么可以跳过

完成 K 轮合法决策（或验证满分提前停止）、没有 pending、accepted 业务技能为空；初始与最终的完整 Target State 文件一致（仅忽略 system_config.yaml 顶层整数 version），包括系统提示词、AGENTS、工具、hooks、记忆；provider/model/thinking、时间和步数限制、benchmark/verifier/容器镜像、适配器与任务清单等评测条件也一致。题目自带资源仍属于固定环境。缺证据、条件漂移、训练越界或审计失败均不能以“没有最终技能”为由跳过后宣告完成。

K=2 第一轮接受、第二轮拒绝，accepted 仍有第一轮技能，必须测试。无改动不意味着停止后续进化轮次。跳过只发生在整个学习过程合法结束之后。该协议目前从空派生技能起步，不把已有非空技能的相等性当作可省略理由。

## 谁执行和核验

规则和判断工具随本 Skill 完整发布。benchmark 仓库的实验脚本负责调度 Harbor，并调用此工具；不能自己复制一个宽松的“为空就通过”。

`campaign.py init` 与 `spec_campaign.py init` 新建时默认 `--final-test-policy reuse_unchanged`（可显式传 repeat）。该字段进入 campaign 哈希。使用 Python API 时显式传 `final_test_policy="reuse_unchanged"`；省略参数保留历史兼容行为。旧 campaign 不重新初始化。

1. 先冻结最终技能并验证学习状态。留存初始与最终**实际打包的 State staging 目录**，不可临时编造相同快照。验证原部署/注入记录仍必需。
2. 在学习开始前冻结初始评测条件，部署最终状态时从实际配置核对并记录最终条件。两份 JSON 至少有 `binding`（campaign_sha256 或已冻结运行源指纹）、`runtime`、`benchmark`、`execution` 三个非空对象；后者应记录 bundle/镜像、adapter、工具、超时、步数、隔离策略和所有可影响执行的参数。禁止写密钥。路径/哈希不是自动证实运行时条件真实的签名。
3. 执行完整 test-H0，按原协议审计、聚合。调用下面工具，失败就排查，不能补 pass：

```sh
python3 -B "$SKILL/scripts/final_test.py"   --out "$OUT" --iterations "$K" --binding "$CAMPAIGN_SHA"   --h0-state "$H0_STAGING" --final-state "$FINAL_STAGING"   --h0-conditions "$H0_CONDITIONS" --final-conditions "$FINAL_CONDITIONS"   --h0-summary "$AUDITED_H0_SUMMARY"   --record "$REPLICA/final-test-decision.json"
```

4. 若本实验启用 reuse_unchanged 且 accepted 为空，不创建 test-final 计划、job、cell。`verify-replica` 必须重新验证实际 H0、完整学习状态和 decision 引用的文件、条件与哈希；缺少 skip 回执不能收尾。内置 campaign 的 H0 summary 必须是 `aggregate.py` 对 test-H0 的原样输出，不能用简写或手填均分。独立执行适配器同样调用 `make_record` / `verify_record`，并检查没有额外 test-final 启动。

## 报告

- test-H0：一次真实测得的分数及证据。
- test-final：`null`，状态 `skipped_unchanged_state`；差值 `null`，新增 Target 次数 0。可以说“最终仍是这个已测初始状态”，不能说“再次测得相同分数”。
- 未重复的组仍是完整执行了该新协议，但不能把它当作一对独立实测结果。汇总须显示实际对照数量；只含接受技能组的均值不得冒充全部组的算法效果。
- `repeat` 的两次真实结果及波动如实保留。新旧协议分开标记，不回改历史结果。

## 与三次测试复测的关系

见 [test-repeats.md](test-repeats.md)。`reuse_unchanged` 控制的是每次测试中是否再测一份完全相同的最终状态，不禁止预先计划三次 H0 测量。无最终 Skill 的组只有 H0 的三个真实分数和标准差，final/delta 仍为 null；有最终 Skill 的组各测三次 H0 与 final。这里的 `test_repeats=3` 与本页用于同状态双测的 `final_test_policy=repeat` 是不同参数。
