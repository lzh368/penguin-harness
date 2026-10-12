# GDPevo 三次独立实验入口

`scripts/campaign.py` 负责冻结身份、生成评测矩阵及复算汇总；真实执行仍按本技能与 Harbor 接口。工具不创建模型 Session、baseline 分数或审计 pass。

```bash
python3 <skill>/scripts/campaign.py init \
  --profile <source-verified-profile.json> --out <campaign> \
  --penguin-home <独立Penguin数据根> --project-id default_project
```

初始化产生 `campaign.json`、冻结的 `profile.json`，并记录三个 replica 各自的 Controller、Target、OUT 和 plans。ID 为 `wiki_controller_r1` / `wiki_target_r1`，r2/r3 同理。campaign 与 Penguin 数据根必须互不包含；相同初始化可重复，改变配置不能覆盖现有 campaign。`out/` 的 `state.json` 只在实际 validation 基线后由 `state.py init` 创建。

每次评测用实际已部署的正整数版本和技能集合 SHA-256 建立完整 phase 计划：

```bash
python3 <skill>/scripts/campaign.py plan \
  --campaign <campaign> --replica r1 \
  --phase validation --label validation-H0 \
  --version 1 --skill-hash <实际技能集合hash>
```

产物在 `replicas/r1/plans/validation-H0/`：`plan.json` 是 aggregate 的完整九字段协议，`deployment.json` 记录声明的版本/hash，`cells/` 仅容纳成功转换的 cell JSON。声明不是实际部署证明；Harbor receipt 和 trace 审计仍必需。每个 label 唯一，禁止覆盖。train、validation、test 分别严格为 24、6、30 题；test 只接受 `test-H0`、`test-final` 两个标签，其他 phase 不能冒用。

测试前先冻结最终技能。三份 replica 完成 H0 与 final 的完整 test 矩阵之后：

```bash
python3 <skill>/scripts/campaign.py summary --campaign <campaign>
```

汇总重新验证六份原始矩阵，不信任预写 summary；缺题、额外题、失败、重复 Session、模型或部署声明漂移均拒绝。输出每次 H0/final/差值、三次算术均值、总体标准差（ddof=0）、六领域均值及可追溯 hash。它证明矩阵内部一致，不代替原始审计真实性和算法流程审查。

三个独立 Controller 与 Target 共享一个隔离的宿主 Penguin 数据根和模型配置，各自 OUT、Wiki、技能、会话、容器和学习历史互不共享。smoke 另用 `wiki_controller_smoke` / `wiki_target_smoke` 和独立目录；smoke 不写正式 campaign，不导入它的轨迹或成绩。
