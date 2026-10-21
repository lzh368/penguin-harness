# Target State and snapshots

Use the native `agent_state/skills/` format. H0 is an empty learned package
exported by `evo.py` from `[]` with the frozen limits. Install it at
`STATE/skills/evo-harness-learned/`. Append exactly once to `STATE/AGENTS.md`:

```text
Before acting on a task, read skills/evo-harness-learned/SKILL.md in your Agent State and follow its selection procedure. Do not modify your State.
```

The reader, models and tools stay identical in H0 and final. On a new Target
set `memory.enabled: false` and `hooks.enabled: false` in system_config.yaml;
retain these throughout. Existing Targets need a snapshot before changes and
authorization for changes beyond the learned slot. Never reset existing work.

A new Target starts at version 1. Otherwise choose one greater than the highest
current version, snapshot `v<N>.tar.gz`, and scoreboard version. Never reuse
abandoned/measured version numbers. Use the shared snapshot shape, atomically:

```bash
cd "<TARGET>"
mkdir -p snapshots
# If v<N>.tar.gz exists, validate/reuse it; never overwrite it.
tar --exclude=.vault.toml -czf snapshots/v<N>.tar.gz.tmp agent_state
mv snapshots/v<N>.tar.gz.tmp snapshots/v<N>.tar.gz
```

Record all State file hashes except `.vault.toml`. Immediately before and after
every evaluation matrix compare State hashes; a mutation invalidates it. The
Harbor evaluator packs State using the shared evaluation protocol, excluding
vault, memory and schedule, and passes `agent_state_tar`. Do not copy scratch,
training artifacts, traces, candidates or judge records into State.

For GDPevo the Trainer is a separate fresh Agent State derived from the same
Solver settings; it additionally holds this method Skill and a group-training
reader. This temporary delegation State is never the test State. The training
adapter launches it as a native root, permitting one level of role children.
Once the group exports its package, the host Controller verifies and copies
only `artifacts/submission/skill/` into the Target learned slot, preserving every
byte. No host regeneration/rewording of role content. The package allowlist is
SKILL.md, index.json, select.py and indexed general/topic Markdown entries.
Compare the exported hash, installed slot hash, tar members and actual trace
selection receipt. The Selector is fixed tooling, never Evolver-written code.

Archive old State, stage the new slot, increment State version, snapshot and
hash before evaluation. On a failure retain the interrupted version and
evidence; do not silently publish an empty package. Exact H0-equivalent
packages use H0 measurements as reuse, never another score chosen afterward.
