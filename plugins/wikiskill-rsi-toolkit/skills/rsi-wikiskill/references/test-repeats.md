# 固定最终 Skill：三次测试、平均分与标准差

本协议回答“这一套已学到的 Skill 测试表现是否稳定”。一次学习（例如 K=2）完成后，不再更新 Wiki、提出候选或重跑 validation；各领域复用各自通过 validation 接受的最终 Skill，不能互相借用。三次独立学习是另一个协议，不能混称。重复数与预算是实验配置，不是 WikiSkill 论文的强制要求。

## 计划与启动

1. 在首次 test 前冻结关联学习运行ID、最终 Skill 内容指纹、H0/最终部署、题目名单与内容、模型/provider/thinking、执行/评分环境、时间/步数、并发、评分标度和聚合方式。默认 `repetition_mode=fixed_skill_test_repeats`、`test_repeats=3`、`std_ddof=1`，重复ID建议 `test-r1/test-r2/test-r3`，不要与学习轮次 r0001/r0002 混淆。用户另定重复数时显式记录，不能看到测试分数后延长到满意为止。
2. 每个重复都在相同条件下测完整 test：H0 一次、冻结最终 Skill 一次；每题新 Session/Workspace，重新做题。不复制基线分数/轨迹凑三次，也不复用上次做题上下文。`repeat_id` 配对表示同一次测试对照，不声称模型使用了相同随机数。
3. 第一次可用原学习实验自带的真实 test，但必须属于同一预先声明的测试计划，且源文件、技能注入、运行条件与完整矩阵都经审核。已有测试结果才临时决定追加，须标为事后探索，不冒充预注册独立复现。不要覆盖原学习的冻结文件；测试计划单独关联其指纹。
4. 遵守当前阶段暂停和预算。用户要求先汇报 train/valid 时，先交付最终 accepted、两轮决定和验证分，等待测试授权；写入本规范或准备计划不授权自动开始 r2/r3。预算按用户指定的完整测试重复范围统计，包含全部领域/两条件/失败尝试；15美元不是算法默认值，也不是每个领域单独15美元。
5. 最终技能为空且满足 [final-test.md](final-test.md) 的完整同状态核验时，每次只测 H0。该组有三个 H0 数据点，final 与 delta 均为 null；不重复相同状态、不画虚构 final 柱。不以“必须每组都有 Skill”为由增加 K 或放宽接受门槛。

## 统计单位与图表

先对每次完整 test 中的 N 道题求均分 `b_r`、`s_r`；再对 R=3 次均分计算：

- `mean = sum(x_r) / R`。
- **样本标准差** `sample_std = sqrt(sum((x_r - mean)^2) / (R-1))`，即 ddof=1。
- 同次差值 `d_r = s_r - b_r`；同样报告三次差值的均值和样本标准差。不要用两组标准差相减代替差值标准差。

每领域显示 H0/最终 Skill 两柱或两点，误差棒为 **mean ± 1 sample SD，n=3**，叠加三次原始点；展示逐次成绩表。无最终 Skill 的领域只显示 H0 一组并注明原因。SD 不是 SE 或95%置信区间；误差棒重叠与否都不是显著性检验，三个点不足以证明稳定因果提升。不能用某一轮内部题目之间的标准差冒充重复波动，也不能把3题×3次当成9次独立学习。

多领域先按每个 repeat_id 取领域均分，再在三次之间计算总体 mean/SD，不能直接平均各领域的SD。H0 可汇总全部领域；paired 汇总只含实际测了两条件的领域，必须标注参与领域数及名单，不能把接受组的提升当作全部领域的算法效果。历史 `campaign.py` 的 population_std（ddof=0）与此不同，保留旧数据，不静默改口径。

## 随 Skill 发布的离线统计工具

执行适配先核验真实评分、完整轨迹、实际 Skill 注入和环境一致性，再导出下列 JSON。工具不执行模型，不读题面/答案，也不代替运行审计；它只检查记录完整性、声明的指纹和重复身份并计算统计。`score` 是已按冻结规则转换的0..100分，可保留原始精度。基础设施错误/缺测不能写成0；不足完整矩阵时工具拒绝生成三次汇总。

输入结构：

```json
{
  "schema_version": 1,
  "mode": "fixed_skill_test_repeats",
  "repeat_ids": ["test-r1", "test-r2", "test-r3"],
  "std_ddof": 1,
  "groups": [{
    "id": "domain-id",
    "learning_run_id": "frozen-learning-run",
    "case_ids": ["case-a", "case-b", "case-c"],
    "conditions_sha256": "<64 hex: fixed runtime/task/scoring conditions>",
    "h0_skill_sha256": "<64 hex: initial empty derived skill set>",
    "final_skill_sha256": "<64 hex: frozen accepted skill set, or null>",
    "unchanged_state_receipt_sha256": null,
    "evaluations": [{
      "repeat_id": "test-r1",
      "condition": "h0",
      "evaluation_id": "unique audited evaluation id",
      "conditions_sha256": "<same frozen conditions>",
      "skill_sha256": "<corresponding skill set>",
      "cells": [{"case_id":"case-a", "session_id":"unique real Target session", "status":"ok", "score":"60.123456", "evidence_sha256":"<64 hex: audited cell evidence>"}]
    }]
  }]
}
```

上面仅展示字段，不是完整结果，不能直接当真实数据使用。填齐每个 repeat×condition×case；没有最终 Skill 的组 final_skill_sha256=null，必须引用同状态跳过回执的哈希。evaluation_id、session_id 与逐格 evidence_sha256 在输入全局不得复用；指纹声明仍需原始运行证据支持。

```sh
python3 -B <skill>/scripts/test_repeats.py --input <test-campaign>/audited-repeats.json > <test-campaign>/statistics.json
```

输出每领域的三次均分、H0/final/delta 的 mean/sample_std/error_bar、全部领域 H0 汇总与实际两条件领域汇总。该文件供 HTML 绘图，原始成绩与审计文件另行保存。仅完成学习或测试尚未获准启动时，不生成模拟误差棒。
