# Target State and snapshots

Use the native `agent_state/skills/` format. H0 is an empty learned package
exported by `evo.py` from `[]` with the frozen limits. Install it at
`STATE/skills/evo-harness-learned/` and map independent method files using the
manifest as described below. For the declared .12 agents-md-v1 transport, invoke
one deterministic entry before each H0/final snapshot:

```json
{"op":"install-context","state":"ABS_NATIVE_TARGET_STATE"}
```

Run this request with the installed evo.py. It copies the complete fixed loading
set into one marked AGENTS.md block without changing surrounding instructions.
The native system template must contain {{AGENTS_MD}}. The original native
session_meta records the actual assembled system_prompt; verify the exact block,
text length and hashes in every context segment. This replaces model-driven
loading for the NEW protocol only. Do not append the legacy mandatory read-tool
contract or change a running/frozen State. Legacy experiments retain their own
Reader/read-tool evidence requirements and original conclusions.

The fixed Reader, max_bytes, models and tools stay identical in
H0 and final. For a new held-out Target, use the native preparation below to
disable memory and hooks; retain those settings throughout. Existing Targets need a snapshot before changes and
authorization for changes beyond the declared learned package. Never reset existing work.

Before freezing a new H0, set the output capacity of both `exec_command` and
`input_command` to at least `6 * max_bytes + 8192` characters: **104192** when the
frozen guidance budget is 16000 UTF-8 bytes. Their native default is 16000
characters, which leave insufficient room for full guidance plus tool framing
or escaped training material receipts. This is a fixed tool-output capacity, not a larger learned
Skill or injection budget. H0, Trainer and final retain the same capacity.

Prepare the fresh Target through the pinned Benchmark's native API helper before
any Session or snapshot exists; do not hand-edit or regex-rewrite native YAML:

```text
node <benchmark>/agents/penguin_agent/helper.mjs prepare-evo-state <PENGUIN_HOME> <projectId> <targetAgentId> target
```

The helper accepts only a fresh version-1 Agent with no native Sessions or
snapshots. It reads the complete native configuration, preserves the entire
built-in tools array and all other fields, sets maxTurns=-1, disables memory and
Target hooks, and raises only the two output capacities to at least 104192. It
uses one native config update when needed and verifies effective values. The
model, thinking settings, time limits, permissions and other tools are unchanged.
A matching configuration is a read-only no-op. Use its trainer mode only for a
separately created fresh native Trainer; the ordinary prepare-trainer operation
copies the already prepared H0 and installs the declared training hooks.

Freeze only after preparation and readback. Harbor startup must find the frozen
max_turns=-1 and make no config update; a mismatch is a setup error, never a
reason to reserialize or repair a frozen State during evaluation.

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
tar --exclude=agent_state/.vault.toml --exclude=agent_state/memory --exclude=agent_state/schedule -czf snapshots/v<N>.tar.gz.tmp agent_state
mv snapshots/v<N>.tar.gz.tmp snapshots/v<N>.tar.gz
```

Record all transported State file hashes, excluding only the top-level
`.vault.toml`, `memory/` and `schedule/`, exactly as the native transport and
container State inventory do. Preserve every Skill, `AGENTS.md` and configuration
byte; similarly named files inside Skills are not excluded. Snapshot archives
must already omit these three roots; the checker does not silently remove them
from an old snapshot. Existing snapshots and their original audits stay intact.
Immediately before and after
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
only the verified `artifacts/submission/skill/` package, preserving every byte.
Use `evo.package_state_files` to map root SKILL.md to
`skills/evo-harness-learned/SKILL.md` and each declared
`methods/<native-name>/SKILL.md` to `skills/<native-name>/SKILL.md`. These sibling
Skill directories are independently discoverable by native Penguin. Never place
methods only below the Reader directory or regenerate/reword content. The exact
manifest is the file allowlist; no selector, index, supporting code or unlisted
files are installed. Reader plus manifest plus all body/frontmatter bytes fit
max_bytes; every physical line fits 2000 UTF-16 code units. Compare exported
file hashes, mapped State hashes, tar members and the actual full context block
in each new-protocol Target trace (legacy runs retain native file observations).
All five final tasks receive the same context bodies and general descriptions.
General body reads remain outside the description-only loading policy. Loading a rule does not
make its applicability conditions true. Never shorten or drop rules at injection.

An explicitly authorized new compilation may export with
`general_storage: description-only`. The deterministic exporter retains each
general description and identity in its native file, marks body_storage in the
manifest, and leaves the authored general body in the private compiled library.
Topic files remain complete. This prevents later file inspection from exposing
additional general rules; it does not turn extra body reads in old runs into
valid loading. Existing frozen exports and legacy storage remain unchanged.

Archive old State, stage the exact new learned file set, increment State version, snapshot and
hash before evaluation. On a failure retain the interrupted version and
evidence; do not silently publish an empty package. Exact H0-equivalent
packages use H0 measurements as reuse, never another score chosen afterward.

For guarded continuous GDPevo, first validate the full verify-export request,
then create the freeze request described in [control.md](control.md). Its native
snapshots contain agent_state/ as their root, unlike the worker's State-only
transport tar. Both omit the three top-level transport exclusions above.
verify-freeze compares every transported State file and the full learned
package. Only the declared Reader/method Skill paths and one native plain top-level
integer version and the exact deterministic AGENTS.md block may differ; all surrounding AGENTS.md bytes remain identical; the combined package byte budget remains fixed. H0 is always the same
valid Reader format with empty rules. Changed guidance requires final_version > h0_version. An equivalent
no-op requires a successfully sealed complete training batch, identical State
file contents and identical versions; missing/failed training is never no-op.

Freeze final before any held-out test. Restore exact H0 for its matrix, then
restore exact final for the final matrix. The same freeze request is rechecked
against the active State before dispatch, and final State must be restored for
completion verification. A verified no-op keeps the same State throughout.
Neither learners nor evaluation children publish State or write scoreboard.
