# Target State and snapshots

Use the native `agent_state/skills/` format. H0 is an empty learned package
exported by `evo.py` from `[]` with the frozen limits. Install it at
`STATE/skills/evo-harness-learned/`. Append exactly once to `STATE/AGENTS.md`:

```text
Before acting on a task, read the complete skills/evo-harness-learned/SKILL.md and index.json in your Agent State using native read_file or a separate foreground cat of each exact file, then follow the reader's selection procedure. Only the reader's supported read-only inspection of public task files/directories may precede selection. Do not run arbitrary code, invoke APIs, write files or read entry bodies before selection. Run the selector as a foreground native exec_command without redirects, pipes or wrappers, and observe its complete output before business API calls or writing the task answer. Do not modify your State.
```

The reader, selector, max_entries/max_bytes, models and tools stay identical in
H0 and final. On a new held-out Target
set `memory.enabled: false` and `hooks.enabled: false` in system_config.yaml;
retain these throughout. Existing Targets need a snapshot before changes and
authorization for changes beyond the learned slot. Never reset existing work.

Before freezing a new H0, set the output capacity of both `exec_command` and
`input_command` to at least `6 * max_bytes + 8192` characters: **104192** when the
frozen guidance budget is 16000 UTF-8 bytes. Their native default is 16000
characters, which can truncate a valid selector JSON receipt after adding JSON
escaping and hashes. This is a fixed tool-output capacity, not a larger learned
Skill or injection budget. H0, Trainer and final retain the same capacity.

The exact native YAML field is `tools.builtin[].maxOutputLength`, selected by
`name`. Edit those two entries in the complete tools.builtin array created by
native `penguin agent create`; preserve every other entry and every tool schema,
description, permission and timeout. **Do not replace the array with two partial
tool records**: native Penguin does not merge partial records with defaults.
A model-free preparation command may update the fresh Target as follows (replace
the literal State path and frozen byte limit). Preserve native YAML formatting:
generic YAML reserialization can change its indentation and multiline scalar
style, so use this exact field-only update on the native-created configuration.
Also freeze the native adapter's `max_turns: -1` before taking the snapshot; the
time limit still applies, and startup must not rewrite the frozen State.

```python
from pathlib import Path
import re
state_config = Path("<STATE>/system_config.yaml")
text = state_config.read_text()
required_output_chars = 6 * 16000 + 8192
assert len(re.findall(r"(?m)^max_turns: -?[0-9]+$", text)) == 1
text = re.sub(r"(?m)^max_turns: -?[0-9]+$", "max_turns: -1", text)
for name in ("exec_command", "input_command"):
    pattern = r"(?m)^    - name: " + name + r"\n(?:(?!    - name:|  [A-Za-z_]|\S).*(?:\n|$))*"
    blocks = list(re.finditer(pattern, text))
    assert len(blocks) == 1, "Require the complete native tool configuration"
    block = blocks[0]
    limits = re.findall(r"(?m)^      maxOutputLength: ([0-9]+)$", block[0])
    assert len(limits) == 1, "Require the native output-limit field"
    updated = re.sub(r"(?m)^      maxOutputLength: [0-9]+$",
                     "      maxOutputLength: " + str(max(int(limits[0]), required_output_chars)), block[0])
    text = text[:block.start()] + updated + text[block.end():]
state_config.write_text(text)
```

Read the file back through native State loading and verify both effective tool
limits before recording the H0 snapshot. prepare-trainer and verify-freeze reject
an omitted/insufficient limit; do not bypass that check or raise one side during
the experiment. If a tool later reports truncated output despite this capacity,
retain that failure and inspect the frozen setup before any further experiment.

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
Solver settings; prepare-trainer additionally installs this method Skill, its
native guard hooks and a group-training reader, and enables hooks for this
training root. The method user-prompt hook injects the full coordinator contract;
do not disable Trainer hooks to bypass a rejected operation. This temporary
delegation State is never the test State. The training
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

For guarded continuous GDPevo, first validate the full verify-export request,
then create the freeze request described in [control.md](control.md). Its native
snapshots contain agent_state/ as their root, unlike the worker's State-only
transport tar. verify-freeze compares every State file and the full learned
package. Only the learned slot and one native plain top-level integer version
may differ; both selection budgets remain fixed. H0 is always the valid empty
reader. Changed guidance requires final_version > h0_version. An equivalent
no-op requires a successfully sealed complete training batch, identical State
file contents and identical versions; missing/failed training is never no-op.

Freeze final before any held-out test. Restore exact H0 for its matrix, then
restore exact final for the final matrix. The same freeze request is rechecked
against the active State before dispatch, and final State must be restored for
completion verification. A verified no-op keeps the same State throughout.
Neither learners nor evaluation children publish State or write scoreboard.
