/**
 * Stop hooks — functions the Session runs after every Task of one `run` call, at the
 * **stop** hook point: the moment a Task ends (the model's final reply with no tool call,
 * or a cutoff — user abort, LLM failure, the max_turns cap).
 *
 * A hook gets the Session id, Trace location and a minimal native lifecycle snapshot;
 * it derives method state and token usage from its files and the Trace. It answers with
 * a decision: `continue` keeps the run going — the
 * hook's `input` becomes the next Task's user message, inside the same `run` call — while
 * `stop` (or no answer at all) lets the run end. It may also ask for a background subagent
 * (`subagent`), which the Session spawns detached and records by session id. Every non-void
 * answer is recorded as one `hook` event message, streamed and written to the Trace; the
 * injected input is not in the event, it is the user message that follows it. The first
 * `continue` wins unless a hook explicitly requests a valid background `wait`: that ends
 * this pass before later hooks and lets native background notices wake the Session. A
 * previous hook's explicit stop prevents waiting. The
 * Session refuses to continue after a cutoff or once its signal is
 * aborted (the answer is still recorded). A hook that throws is recorded with the error as
 * its reason and treated as having no opinion, so a broken hook never takes the run down.
 *
 * Hooks installed into an Agent's `agent_state/hooks/` are scripts run through
 * script-hook.ts; the in-process interface here is what SDK embedders and tests register
 * directly. The loop that consults them is `Session.run`.
 * Docs: /docs/agent-loop § "Stop hooks".
 */
import { hookEvent } from "../omnimessage/index.js";
import type { OmniMessage } from "../omnimessage/index.js";
import type { ApproveFn } from "../interfaces/index.js";
import { errorMessage, failedAnswer } from "./answer.js";
import type { PreToolUseHook } from "./tool-hook.js";
import type { UserPromptHook } from "./prompt-hook.js";

/** Read-only ownership snapshot; deliberately excludes commands, prompts and outputs. */
export interface StopHookLifecycle {
  schema: 1;
  subagents: { session_id: string; subagent_id: string | null; running: boolean }[];
  commands: { process_id: string; running: boolean }[];
  notices: {
    kind: "command" | "subagent";
    id: string;
    status: "completed" | "failed" | "stopped";
  }[];
  cutoff: boolean;
  aborted: boolean;
}

/** What a stop hook is told: the Session's record and its native background state. */
export interface StopHookInput {
  sessionId: string;
  /**
   * Absolute path of the Trace file the Session is writing right now — the current context
   * segment (compaction rotates to a new file). Absent for a Session without a Trace.
   */
  tracePath?: string;
  /** The run's abort signal. */
  signal?: AbortSignal;
  /** Optional for SDK compatibility. Only a native live/pending snapshot permits `wait`. */
  lifecycle?: StopHookLifecycle;
}

/** A background subagent a hook asks for: spawned detached, its first user message being `prompt`. */
export interface HookSubagentRequest {
  prompt: string;
  /** The child Agent's id; omitted = the Session's own Agent. */
  agentId?: string;
}

/** A stop hook's answer; `undefined` (no answer) means "no opinion, nothing to record". */
export interface StopHookResult {
  /** `wait` defers remaining hooks until native background work wakes the Session; it requires authoritative pending work and cannot carry input or spawn work. */
  decision?: "continue" | "stop" | "wait";
  /** With `continue`: the next Task's user input text (a `continue` without one cannot continue and is recorded as-is). */
  input?: string;
  /** One line for people — the `hook` event carries it (hosts render it). */
  reason?: string;
  /** The hook's own structured record, scalars only — the `hook` event carries it for hosts and the Trace. */
  output?: Record<string, string | number | boolean>;
  /** Work to hand off: a detached background child Session; its session id joins `output` as `session_id`. */
  subagent?: HookSubagentRequest;
}

/** A named stop hook. The name identifies its `hook` events (e.g. `goal`, `continual-learning`). */
export interface StopHook {
  name: string;
  run(input: StopHookInput): Promise<StopHookResult | void>;
}

/**
 * What honoring a subagent request yields: the child's session id, and its origin-stamped
 * `session_meta` for the parent stream — the record a host registers a child session from
 * (the server gives it a session row and lists it with the other subagent sessions), so the
 * id on the event leads somewhere. Null when the runner's handle predates the upfront-meta
 * seam.
 */
export interface HookSubagentSpawned {
  sessionId: string;
  meta: OmniMessage | null;
}

/** Spawns the background child Session a hook asked for; `approve` is the run's approval callback, which the child inherits. */
export type HookSubagentSpawner = (
  request: HookSubagentRequest,
  approve?: ApproveFn,
) => Promise<HookSubagentSpawned>;

/** The hooks a Session is built with (`SessionConfig.hooks`): one list per hook point, plus the spawner hook answers may need. */
export interface SessionHooks {
  stop?: StopHook[];
  /** Consulted by the engine before each tool call's approval (see tool-hook.ts). */
  preToolUse?: PreToolUseHook[];
  /** Consulted every time the user submits a Prompt; the ones marked `trigger: "host"` run only through `Session.runUserPromptHook` (see prompt-hook.ts). */
  userPrompt?: UserPromptHook[];
  /** How a `subagent` answer is honored; without it the request is recorded as unhonored. */
  spawnSubagent?: HookSubagentSpawner;
}

/**
 * What one pass over the stop hooks produced: the messages to record and stream, in order,
 * and the first `continue`'s input (null = the run ends). Behind a hook's `hook` event comes
 * the origin-stamped session_meta of a child it spawned — stream material only (the Trace
 * writer drops origin-stamped messages, as it does for a tool-spawned child's). The parent
 * Trace keeps no pointer to the child: a hook's child is detached by design, out of the
 * parent's nested subagent view; the event's `output.session_id` is its record there.
 */
export interface StopHooksOutcome {
  events: OmniMessage[];
  next: string | null;
}

/** A wait is a lifecycle operation, never an inference from a model's final text. */
function canWait(input: StopHookInput): boolean {
  const state = input.lifecycle;
  return (
    state?.schema === 1 &&
    state.cutoff === false &&
    state.aborted === false &&
    Array.isArray(state.subagents) &&
    Array.isArray(state.commands) &&
    Array.isArray(state.notices) &&
    !input.signal?.aborted &&
    (state.subagents.some(
      (child) =>
        child.running === true &&
        typeof child.subagent_id === "string" &&
        child.subagent_id.length > 0,
    ) ||
      state.commands.some((command) => command.running === true) ||
      state.notices.length > 0)
  );
}

/**
 * Runs the stop hooks in registration order and turns every non-void answer into a `hook`
 * event. The first `continue` that carries an input decides the continuation; later ones are
 * recorded but not honored. A `subagent` request is handed to `spawn` and the child's session
 * id recorded on the event (`output.session_id`), with the child's session_meta following the
 * event; a failed spawn, or no spawner, is recorded in the reason. A throwing hook is
 * recorded with the error as its reason. Only an explicit eligible `wait` ends the hook
 * pass early; without it all prior stop/continue behavior is unchanged.
 */
export async function runStopHooks(
  hooks: readonly StopHook[],
  input: StopHookInput,
  spawn?: (request: HookSubagentRequest) => Promise<HookSubagentSpawned>,
  /** Session supplies a fresh snapshot around asynchronous hooks (a child may settle meanwhile). */
  lifecycle?: () => StopHookLifecycle,
): Promise<StopHooksOutcome> {
  const events: OmniMessage[] = [];
  let next: string | null = null;
  let priorStop = false;
  for (const hook of hooks) {
    let result: StopHookResult | void;
    try {
      result = await hook.run(lifecycle ? { ...input, lifecycle: lifecycle() } : input);
    } catch (err) {
      result = failedAnswer(err);
    }
    if (!result) continue;
    if (result.decision === "wait") {
      const current = lifecycle ? { ...input, lifecycle: lifecycle() } : input;
      if (
        !priorStop &&
        canWait(current) &&
        result.input === undefined &&
        result.subagent === undefined
      ) {
        events.push(
          hookEvent({
            hook: "stop",
            name: hook.name,
            decision: "wait",
            ...(result.reason !== undefined ? { reason: result.reason } : {}),
            ...(result.output !== undefined ? { output: result.output } : {}),
          }),
        );
        return { events, next: null };
      }
      // Unsupported/stale snapshots and cutoffs cannot strand a Session. Remaining
      // hooks retain their existing behavior; no requested input or spawn is honored.
      events.push(
        hookEvent({
          hook: "stop",
          name: hook.name,
          decision: "stop",
          reason:
            "background wait rejected: no eligible native pending work or invalid wait answer",
          output: { wait_rejected: true },
        }),
      );
      continue;
    }
    if (result.decision === "stop") priorStop = true;
    if (result.decision === "continue" && next === null && result.input) next = result.input;
    let output = result.output;
    let reason = result.reason;
    const childRecords: OmniMessage[] = [];
    if (result.subagent) {
      if (!spawn) {
        reason = `${reason ? `${reason} · ` : ""}subagent not spawned: no spawner`;
      } else {
        try {
          const child = await spawn(result.subagent);
          output = { ...output, session_id: child.sessionId };
          if (child.meta) childRecords.push(child.meta);
        } catch (err) {
          reason = `${reason ? `${reason} · ` : ""}subagent not spawned: ${errorMessage(err)}`;
        }
      }
    }
    events.push(
      hookEvent({
        hook: "stop",
        name: hook.name,
        ...(result.decision !== undefined ? { decision: result.decision } : {}),
        ...(reason !== undefined ? { reason } : {}),
        ...(output !== undefined ? { output } : {}),
      }),
      ...childRecords,
    );
  }
  return { events, next };
}
