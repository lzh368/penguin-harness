# Keep WikiSkill rules within their supported scope

Date: 2026-10-20
Type: fix
Scope: wikiskill-rsi-toolkit
PR: [#2](https://github.com/lzh368/penguin-harness/pull/2)

[简体中文](2026-10-20-wikiskill-rule-scope.zh.md)

WikiSkill `2026.10.20.4` checks that a candidate's required steps apply to the tasks admitted by its trigger. Known scope conflicts must be corrected before validation; uncertain benefits can still be evaluated.

## Learning guidance

- Keep the same conditions and uncertainty in Wiki titles, index summaries and pattern pages. Distinguish task requirements, observed data and the Target's interpretation.
- Propose one coherent operation, with explicit guards for steps that depend on particular inputs. An output schema does not define every business rule or justify discarding unlisted source fields.
- Align the main skill with experience compilation: useful behaviour already present in a training trace can become a candidate. It need not be a new action.

## Proposal review

Reviewers check concrete step prerequisites rather than relying on a general instruction to follow the current task. Cosmetic issues may remain warnings. Acceptance still requires strict improvement on validation; the feedback protocol, scoring, runtime and existing experiment records are unchanged.
