# WikiSkill 方案默认使用最高思考强度

- **Date:** 2026-10-13
- **Type:** feature
- **Scope:** `skills`

[English](2026-10-13-wikiskill-default-thinking.md)

不显式指定思考强度时，新生成的 GDPevo 方案六个角色改用 `thinking_level: xhigh`。两处默认值同时提高：命令行 `--thinking` 的兜底值，以及 `build_profile` 的兜底运行时。

显式给出的 `--thinking`（或 `--role-models`）仍然优先；已有方案保持它冻结时的档位——默认值只决定新建方案从哪一档开始。运行环境冻结在实验自己的 `profile.json` 里，因此已有实验不受影响；要改某个实验的档位需要重建它的配置。
