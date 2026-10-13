# GDPevo 三次独立实验入口

`scripts/campaign.py` 负责本项目 GDPevo 配置的身份冻结、评测矩阵及复算汇总；真实执行仍按本技能与 Harbor 接口。这是可选的实验管理工具；普通 Web 实验可以由当前 Controller 按本技能直接准备 Target 和清单，不依赖工作区的 `rsi.py`。工具从所选 profile 读取模型，不限定某个服务商，也不创建 Penguin Agent、模型 Session、baseline 分数或审计 pass。

```bash
python3 <skill>/scripts/campaign.py init \
  --profile <source-verified-profile.json> --out <campaign> \
  --penguin-home <独立Penguin数据根> --project-id default_project \
  --agent-prefix <本实验独立的前缀>
```

更换模型或渠道时使用新 profile 和新 campaign 目录，保留旧实验及证据；无论旧实验是否已执行，都不能通过改写冻结文件来复用旧结果。新 campaign 须完成适用于新配置的 smoke，不能沿用另一配置的通过回执。

初始化产生 `campaign.json`、冻结的 `profile.json`，并记录三个 replica 各自的 Controller、Target、OUT 和 plans。这些是预留身份，不代表对应 Agent 已创建。只在即将执行某个 replica 时，由启动入口准备该 Controller；该 Controller 执行技能后再按 [penguin.md](penguin.md) 核验或创建本次 Target。ID 为 `<前缀>_controller_r1` / `<前缀>_target_r1`，r2/r3 同理。前缀使用 2–40 个小写字母、数字或下划线，以字母开头；同一数据根下的新 campaign 使用独立前缀，避免共享已有 Agent。默认 `wiki` 保留给最初的实验。campaign 与 Penguin 数据根必须互不包含；相同初始化可重复，改变配置不能覆盖现有 campaign。`out/` 的 `state.json` 只在实际 validation 基线后由 `state.py init` 创建。

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

本工具约定三个不同的 Controller/Target ID，共享一个隔离的宿主 Penguin 数据根和模型配置，各自 OUT、Wiki、技能、会话、容器和学习历史互不共享。专用 Controller ID 是这个工具的约定，WikiSkill 通用入口只要求干净的 Controller 定义及每次全新的 Session。smoke 另用 `<前缀>_controller_smoke` / `<前缀>_target_smoke` 和独立目录；smoke 不写正式 campaign，不导入它的轨迹或成绩。

工作区的 `rsi.py bootstrap [smoke|r1|r2|r3]` 仅是可选便利命令，按当前选择准备一个 Controller；`rsi.py run` 会准备本次需要的 Controller。Target 始终由执行本技能的 Controller 按需准备。已存在的八个预建 Agent 保留，运行对应实验时核验后使用，不批量删除或重建。
