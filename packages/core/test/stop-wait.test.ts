import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  Environment,
  Session,
  assistantText,
  parseStopHookResult,
  runStopHooks,
  scriptStopHook,
  toolCall,
  userText,
} from "../src/index.js";
import type {
  HookPayload,
  LLMInterface,
  LLMOutcome,
  OmniMessage,
  StopHook,
  StopHookLifecycle,
  SubagentRunner,
} from "../src/index.js";

const empty = (): StopHookLifecycle => ({
  schema: 1,
  subagents: [],
  commands: [],
  notices: [],
  cutoff: false,
  aborted: false,
});
const child = (): StopHookLifecycle => ({
  ...empty(),
  subagents: [{ session_id: "child-12345678", subagent_id: "subagent-12345678", running: true }],
});
const wait: StopHook = { name: "method", run: async () => ({ decision: "wait" }) };
const goal: StopHook = { name: "goal", run: async () => ({ decision: "continue", input: "next" }) };

describe("explicit native stop wait", () => {
  it("defers the unchanged Goal hook and counts the wait plus notice tasks once at completion", async () => {
    const dir = await mkdtemp(path.join(tmpdir(), "penguin-wait-goal-"));
    const stateDir = path.join(dir, "agent", "agent_state");
    const goalPath = path.join(dir, "agent", "scratchpad", "root", "GOAL.json");
    const tracePath = path.join(dir, "trace.jsonl");
    const hooksDir = path.resolve(import.meta.dirname, "../../../plugins/goal/hooks");
    const initial = {
      objective: "offline lifecycle proof",
      status: "active",
      budget: -1,
      round: 1,
      tokens_used: 0,
    };
    const record = (payload: Record<string, unknown>) => ({ type: "event_msg", payload });
    const text = (role: string, value: string, sender?: string) => ({
      type: "model_msg",
      payload: { type: "text", role, text: value, ...(sender ? { sender } : {}) },
    });
    const usage = (total: number) =>
      record({ type: "token_usage", request: { total, cache_read: 0 } });
    const rows: unknown[] = [
      { type: "session_meta", payload: { session_id: "root", agent_state: stateDir } },
      text("user", "original goal round", "harness"),
      text("assistant", "waiting"),
      usage(100),
      record({ type: "request_end", status: "completed" }),
    ];
    const saveTrace = () =>
      writeFile(tracePath, rows.map((r) => JSON.stringify(r)).join("\n") + "\n");
    try {
      await mkdir(path.dirname(goalPath), { recursive: true });
      await writeFile(goalPath, JSON.stringify(initial));
      await saveTrace();
      const installedGoal = scriptStopHook("goal", hooksDir, "stop.mjs", { timeoutS: 5 });
      const before = await readFile(goalPath, "utf8");
      const waiting = await runStopHooks([wait, installedGoal], {
        sessionId: "root",
        tracePath,
        lifecycle: child(),
      });
      expect(waiting.events).toHaveLength(1);
      expect(await readFile(goalPath, "utf8")).toBe(before);
      rows.push(
        text(
          "user",
          "[background_task_done]\nkind: subagent\nid: subagent-12345678\nstatus: completed\n[/background_task_done]",
          "harness",
        ),
        text("assistant", "complete"),
        usage(50),
        record({ type: "request_end", status: "completed" }),
      );
      await saveTrace();
      await writeFile(goalPath, JSON.stringify({ ...initial, status: "complete" }));
      const complete = await runStopHooks(
        [{ name: "method", run: async () => ({ decision: "stop" }) }, installedGoal],
        { sessionId: "root", tracePath, lifecycle: empty() },
      );
      expect(complete.next).toBeNull();
      expect(JSON.parse(await readFile(goalPath, "utf8"))).toMatchObject({
        status: "complete",
        ended: true,
        round: 1,
        tokens_used: 150,
      });
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it.each(["child", "command", "completed", "failed", "stopped"])(
    "waits for owned %s work without invoking a later continuation hook",
    async (kind) => {
      const state = kind === "child" ? child() : empty();
      if (kind === "command") state.commands.push({ process_id: "proc-1", running: true });
      if (["completed", "failed", "stopped"].includes(kind)) {
        state.notices.push({ kind: "subagent", id: "subagent-1", status: kind as "completed" });
      }
      let later = 0;
      const out = await runStopHooks(
        [
          wait,
          {
            name: "goal",
            run: async () => {
              later++;
              return { decision: "continue", input: "spin" };
            },
          },
        ],
        { sessionId: "root", lifecycle: state },
      );
      expect(out.next).toBeNull();
      expect(later).toBe(0);
      expect(out.events.map((e) => (e.payload as HookPayload).decision)).toEqual(["wait"]);
    },
  );

  it.each(["absent", "idle", "foreground", "cutoff", "aborted", "signal"])(
    "does not accept wait for %s state",
    async (kind) => {
      const state = kind === "idle" ? empty() : child();
      if (kind === "foreground") state.subagents[0]!.subagent_id = null;
      if (kind === "cutoff") state.cutoff = true;
      if (kind === "aborted") state.aborted = true;
      const out = await runStopHooks([wait, goal], {
        sessionId: "root",
        ...(kind !== "absent" ? { lifecycle: state } : {}),
        ...(kind === "signal" ? { signal: AbortSignal.abort() } : {}),
      });
      expect(out.next).toBe("next");
      expect(out.events[0]!.payload).toMatchObject({ output: { wait_rejected: true } });
    },
  );

  it("rechecks native state after an asynchronous hook; a disappeared child cannot strand the parent", async () => {
    let state = child();
    const out = await runStopHooks(
      [
        {
          name: "method",
          run: async (input) => {
            expect(input.lifecycle?.subagents[0]?.running).toBe(true);
            state = empty();
            return { decision: "wait" };
          },
        },
        goal,
      ],
      { sessionId: "root" },
      undefined,
      () => state,
    );
    expect(out.next).toBe("next");
    expect(out.events[0]!.payload).toMatchObject({ output: { wait_rejected: true } });
  });

  it("accepts a child settling during the hook only through its actual queued notice", async () => {
    let state = child();
    const out = await runStopHooks(
      [
        {
          name: "method",
          run: async () => {
            state = {
              ...empty(),
              notices: [{ kind: "subagent", id: "subagent-12345678", status: "failed" }],
            };
            return { decision: "wait" };
          },
        },
        goal,
      ],
      { sessionId: "root" },
      undefined,
      () => state,
    );
    expect(out.next).toBeNull();
    expect(out.events).toHaveLength(1);
  });

  it("does not override an earlier stop, or accept a wait that injects input/spawns work", async () => {
    for (const hooks of [
      [{ name: "budget", run: async () => ({ decision: "stop" as const }) }, wait, goal],
      [{ name: "method", run: async () => ({ decision: "wait" as const, input: "spin" }) }, goal],
      [
        {
          name: "method",
          run: async () => ({ decision: "wait" as const, subagent: { prompt: "new work" } }),
        },
        goal,
      ],
    ]) {
      const out = await runStopHooks(hooks, { sessionId: "root", lifecycle: child() });
      expect(out.next).toBe("next");
      expect(out.events.some((e) => (e.payload as HookPayload).output?.wait_rejected)).toBe(true);
    }
  });

  it("keeps existing stop/continue behavior even when native background work is live", async () => {
    const out = await runStopHooks(
      [
        { name: "old-stop", run: async () => ({ decision: "stop" }) },
        goal,
        { name: "later", run: async () => ({ decision: "continue", input: "later" }) },
      ],
      { sessionId: "root", lifecycle: child() },
    );
    expect(out.next).toBe("next");
    expect(out.events).toHaveLength(3);
  });

  it("passes the minimal lifecycle through a real installed script and parses wait", async () => {
    const dir = await mkdtemp(path.join(tmpdir(), "penguin-wait-script-"));
    try {
      await writeFile(
        path.join(dir, "hook.mjs"),
        `let raw=''; for await (const c of process.stdin) raw+=c;
const i=JSON.parse(raw); process.stdout.write(JSON.stringify({decision:'wait',output:{schema:i.lifecycle.schema,children:i.lifecycle.subagents.length,private_payload:JSON.stringify(i).includes('SECRET')}}));`,
      );
      const result = await scriptStopHook("method", dir, "hook.mjs", { timeoutS: 5 }).run({
        sessionId: "root",
        lifecycle: child(),
      });
      expect(result).toEqual({
        decision: "wait",
        output: { schema: 1, children: 1, private_payload: false },
      });
      expect(parseStopHookResult({ decision: "wait" })).toEqual({ decision: "wait" });
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});

const until = async (condition: () => boolean) => {
  const end = Date.now() + 5000;
  while (!condition()) {
    if (Date.now() >= end) throw new Error("native background event did not arrive");
    await new Promise((r) => setTimeout(r, 5));
  }
};

describe("Session native background wait", () => {
  it.each(["abort", "fatal"])(
    "passes an actual %s cutoff to hooks and never waits on it",
    async (kind) => {
      const dir = await mkdtemp(path.join(tmpdir(), "penguin-wait-cutoff-"));
      const abort = new AbortController();
      let observed: StopHookLifecycle | undefined;
      let later = 0;
      const session = new Session({
        meta: {
          session_id: "root",
          provider: "offline",
          model_id: "scripted",
          model_context_window: 10000,
          system_prompt: "test",
          agent_state: dir,
          workspace: dir,
          source: "user",
        },
        bootstrap: async () => ({
          llm: {
            async *streamGenerate() {
              yield assistantText("offline cutoff");
              if (kind === "abort") abort.abort();
              return kind === "fatal"
                ? { status: "fatal", errorMessage: "offline synthetic failure" }
                : { status: "completed" };
            },
          },
        }),
        environment: {
          listTools: async () => [],
          toolPermission: () => undefined,
          executeTool: async function* () {
            throw new Error("no tool expected");
          },
          listBackgroundSubagents: () => [
            { sessionId: "child", subagentId: "subagent-1", running: true },
          ],
        },
        imagesDir: path.join(dir, "images"),
        modelHasVision: true,
        hooks: {
          stop: [
            {
              name: "method",
              run: async ({ lifecycle }) => {
                observed = lifecycle;
                return { decision: "wait" };
              },
            },
            {
              name: "later",
              run: async () => {
                later++;
                return { decision: "stop" };
              },
            },
          ],
        },
      });
      try {
        const events: OmniMessage[] = [];
        for await (const event of session.run([userText("offline")], { signal: abort.signal }))
          events.push(event);
        expect(observed?.cutoff || observed?.aborted).toBe(true);
        if (kind === "fatal") expect(observed?.cutoff).toBe(true);
        if (kind === "abort") expect(observed?.aborted).toBe(true);
        expect(later).toBe(1);
        expect(events.some((event) => (event.payload as HookPayload).output?.wait_rejected)).toBe(
          true,
        );
      } finally {
        session.dispose();
        await rm(dir, { recursive: true, force: true });
      }
    },
  );

  it.each(["subagent", "command"])(
    "ends without spinning and wakes once for a real %s",
    async (kind) => {
      const dir = await mkdtemp(path.join(tmpdir(), "penguin-native-wait-"));
      let releaseChild = () => {};
      let childCalls = 0;
      let childSession: Session | undefined;
      const runner: SubagentRunner = {
        async spawn() {
          childSession = new Session({
            meta: {
              session_id: "child-12345678",
              provider: "offline",
              model_id: "scripted",
              model_context_window: 10000,
              system_prompt: "test",
              agent_state: dir,
              workspace: dir,
              source: "subagent",
            },
            bootstrap: async () => ({
              llm: {
                async *streamGenerate() {
                  childCalls++;
                  await new Promise<void>((r) => {
                    releaseChild = r;
                  });
                  yield assistantText("SECRET child output");
                  return { status: "completed" } satisfies LLMOutcome;
                },
              },
            }),
            environment: new Environment({
              workspaceDir: dir,
              toolConfig: { customTools: [], mcpServers: [] },
            }),
            imagesDir: path.join(dir, "child-images"),
            modelHasVision: true,
          });
          return {
            sessionId: childSession.sessionId,
            takeMeta: () => null,
            run: ({ messages, signal }) => childSession!.run(messages, { signal }),
            dispose: () => childSession!.dispose(),
          };
        },
      };
      const name = kind === "subagent" ? "run_subagent" : "exec_command";
      const env = new Environment({
        workspaceDir: dir,
        toolConfig: {
          customTools: [{ name, description: "test", permission: "rw", maxOutputLength: 16000 }],
          mcpServers: [],
        },
        services: { subagentRunner: runner },
      });
      let calls = 0;
      const inputs: OmniMessage[][] = [];
      const llm: LLMInterface = {
        async *streamGenerate({ newMessages }) {
          calls++;
          inputs.push(newMessages);
          if (calls === 1)
            yield toolCall({
              name,
              toolCallId: "launch",
              stopReason: "completed",
              arguments: JSON.stringify(
                kind === "subagent"
                  ? { prompt: "SECRET child prompt", run_in_background: true }
                  : {
                      cmd: "printf 'SECRET command output'; while [ ! -f release ]; do sleep 0.02; done",
                      run_in_background: true,
                    },
              ),
            });
          else yield assistantText("task boundary");
          return { status: "completed" };
        },
      };
      let finalHooks = 0;
      const snapshots: StopHookLifecycle[] = [];
      const parent = new Session({
        meta: {
          session_id: "root",
          provider: "offline",
          model_id: "scripted",
          model_context_window: 10000,
          system_prompt: "test",
          agent_state: dir,
          workspace: dir,
          source: "user",
        },
        bootstrap: async () => ({ llm }),
        environment: env,
        imagesDir: path.join(dir, "images"),
        modelHasVision: true,
        hooks: {
          stop: [
            {
              name: "method",
              run: async ({ lifecycle }) => {
                snapshots.push(lifecycle!);
                const pending =
                  lifecycle!.subagents.some((s) => s.running) ||
                  lifecycle!.commands.some((p) => p.running) ||
                  lifecycle!.notices.length > 0;
                return pending ? { decision: "wait" } : { decision: "stop" };
              },
            },
            {
              name: "final-goal",
              run: async () => {
                finalHooks++;
                return { decision: "stop" };
              },
            },
          ],
        },
      });
      let signaled = 0;
      let followup: Promise<void> | undefined;
      parent.onBackgroundNotice(() => {
        signaled++;
        const notices = parent.takeBackgroundNotices();
        followup = (async () => {
          for await (const _ of parent.run(notices)) {
            /* actual host delivery */
          }
        })();
      });
      try {
        const events: OmniMessage[] = [];
        for await (const event of parent.run([userText("start")], { approve: async () => "allow" }))
          events.push(event);
        expect(calls).toBe(2);
        expect(finalHooks).toBe(0);
        expect(
          events.some(
            (e) =>
              e.type === "event_msg" &&
              (e.payload as HookPayload).type === "hook" &&
              (e.payload as HookPayload).decision === "wait",
          ),
        ).toBe(true);
        expect(JSON.stringify(snapshots)).not.toContain("SECRET");
        await new Promise((r) => setTimeout(r, 30));
        expect(calls).toBe(2);
        if (kind === "subagent") {
          expect(childCalls).toBe(1);
          releaseChild();
        } else await writeFile(path.join(dir, "release"), "");
        await until(() => followup !== undefined);
        await followup;
        expect(signaled).toBe(1);
        expect(calls).toBe(3);
        expect(finalHooks).toBe(1);
        expect(JSON.stringify(inputs[2])).toContain("background_task_done");
        expect(parent.hasPendingBackgroundNotices()).toBe(false);
      } finally {
        releaseChild();
        parent.dispose();
        childSession?.dispose();
        await rm(dir, { recursive: true, force: true });
      }
    },
  );
});
