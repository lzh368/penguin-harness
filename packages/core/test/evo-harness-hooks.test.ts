/** Native installation and hook boundaries, with local Python fixtures and no model calls. */
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";
import {
  DEFAULT_PROJECT_ID,
  GenerativeModel,
  createAgent,
  hooksDir,
  installPlugin,
  libraryPlugin,
  listInstalledHooks,
  systemConfigPath,
  userText,
} from "../src/index.js";
import type { Session, SystemConfig } from "../src/index.js";
import type { OmniMessage } from "../src/omnimessage/index.js";
import type { StopHook } from "../src/hooks/stop-hook.js";
import type { PreToolUseHook } from "../src/hooks/tool-hook.js";
import type { UserPromptHook } from "../src/hooks/prompt-hook.js";
import { runHookScript } from "../src/hooks/script-hook.js";
import { runStopHooks } from "../src/hooks/stop-hook.js";
import { runPreToolUseHooks } from "../src/hooks/tool-hook.js";
import { stubProviderKeys } from "./provider-keys.js";
import { seedProject } from "./seeded-project.js";

const NAME = "evo-harness-rsi-toolkit";
const ids = [DEFAULT_PROJECT_ID, "evo-native-test"] as const;
let root: string;
let state: string;
let script: string;
let control: string;
let previousHome: string | undefined;
let restoreKeys: () => void;
let agent: Awaited<ReturnType<typeof createAgent>>;
let sessions: Session[];
let network: MockInstance<typeof fetch>;
let model: MockInstance<typeof GenerativeModel.prototype.streamGenerate>;

type NativeHooks = {
  stopHooks: StopHook[];
  preToolUseHooks: PreToolUseHook[];
  userPromptHooks: UserPromptHook[];
  consultUserPromptHooks(input: OmniMessage[]): Promise<{ records: OmniMessage[] } | null>;
};
const nativeHooks = (session: Session) => session as unknown as NativeHooks;
const scratch = (session = "s1") => path.join(path.dirname(state), "scratchpad", session);
const input = (hook = "pre_tool_use", session = "s1") => ({
  hook,
  session_id: session,
  tool_name: "run_subagent",
  tool_call_id: "call-fixture",
  arguments: '{"prompt":"synthetic task"}',
});
const fixture = (source: string) => fs.writeFile(control, source, "utf8");
const nativeCall = (value: unknown) => runHookScript(script, value, { timeoutS: 3 });

beforeEach(async () => {
  previousHome = process.env.PENGUIN_HOME;
  root = await fs.mkdtemp(path.join(os.tmpdir(), "penguin-evo-native-"));
  process.env.PENGUIN_HOME = root;
  restoreKeys = stubProviderKeys();
  network = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline test: no network"));
  model = vi.spyOn(GenerativeModel.prototype, "streamGenerate").mockImplementation(() => {
    throw new Error("offline test: no model request");
  });
  sessions = [];
  await seedProject(root);
  agent = await createAgent({ agentId: ids[1] });
  await installPlugin(root, ...ids, libraryPlugin(NAME)!);
  state = path.dirname(hooksDir(root, ...ids));
  script = path.join(hooksDir(root, ...ids), NAME, "guard.mjs");
  control = path.join(state, "skills", NAME, "scripts", "control.py");
  await fs.mkdir(path.dirname(control), { recursive: true });
});

afterEach(async () => {
  for (const session of sessions) session.dispose();
  expect(model).not.toHaveBeenCalled();
  expect(network).not.toHaveBeenCalled();
  vi.restoreAllMocks();
  restoreKeys();
  if (previousHome === undefined) delete process.env.PENGUIN_HOME;
  else process.env.PENGUIN_HOME = previousHome;
  await fs.rm(root, { recursive: true, force: true });
});

async function session(options: Parameters<typeof agent.createSession>[0] = {}) {
  const result = await agent.createSession(options);
  sessions.push(result);
  return result;
}

async function enableHooks(enabled: boolean) {
  const file = systemConfigPath(root, ...ids);
  const cfg = parseYaml(await fs.readFile(file, "utf8")) as SystemConfig;
  cfg.hooks = { enabled };
  await fs.writeFile(file, stringifyYaml(cfg), "utf8");
}

describe("Evo native hook installation and execution", () => {
  it("installs all three points, the guard bytes, and the method with the real installer", async () => {
    const [hook] = await listInstalledHooks(root, ...ids);
    expect(hook).toMatchObject({
      name: NAME,
      version: "2026.10.21.3",
      user_prompt: [{ command: "guard.mjs", timeout: 30, trigger: "prompt" }],
      pre_tool_use: [{ command: "guard.mjs", timeout: 30 }],
      stop: [{ command: "guard.mjs", timeout: 30 }],
    });
    expect(await fs.readFile(script, "utf8")).toBe(
      await fs.readFile(
        path.resolve(import.meta.dirname, `../../../plugins/${NAME}/hooks/guard.mjs`),
        "utf8",
      ),
    );
    expect(await fs.readFile(path.join(state, "skills", NAME, "SKILL.md"), "utf8")).toContain(
      "Evo-Harness",
    );
  });

  it("preserves native stdin and runs only fixed installed control.py with the derived State", async () => {
    await fixture(`import json, pathlib, sys
raw = sys.stdin.read()
assert sys.argv[1:] == ['--hook', '--state', str(pathlib.Path(__file__).resolve().parents[3])]
print(json.dumps({'context': raw}))
`);
    const raw =
      ' { "hook": "user_prompt", "session_id": "s1", "prompt": "synthetic", "state": "/outside", "script": "/outside/arbitrary.py", "unknown": 12 }\n';
    const run = spawnSync(process.execPath, [script], {
      input: raw,
      encoding: "utf8",
      timeout: 3_000,
    });
    expect(run.error).toBeUndefined();
    expect(run.status).toBe(0);
    expect(JSON.parse(run.stdout)).toEqual({ context: raw });
  });

  it("keeps successful no-op responses in the native approval flow", async () => {
    await fixture("print('{}')\n");
    const current = await session();
    const outcome = await runPreToolUseHooks(nativeHooks(current).preToolUseHooks, {
      sessionId: current.sessionId,
      toolName: "run_subagent",
      toolCallId: "fixture",
      argumentsJson: "{}",
    });
    expect(outcome.decision).toBeNull();
    expect(await nativeCall(input("stop"))).toEqual({});
  });

  it("injects the root contract on the initial goal prompt through the normal native prompt hook", async () => {
    await installPlugin(root, ...ids, libraryPlugin("goal")!);
    await fixture(`import json, sys
request = json.load(sys.stdin)
print(json.dumps({'context': 'SYNTHETIC full Evo root contract: ' + request['prompt']}))
`);
    const current = await session();
    const goal = await current.runUserPromptHook("goal", "perform synthetic experiment", {
      budget: 500,
    });
    expect(goal?.context).toContain("goal mode");
    const outcome = await nativeHooks(current).consultUserPromptHooks([
      userText("perform synthetic experiment"),
      userText(goal!.context!, "harness"),
    ]);
    expect(outcome?.records).toContainEqual(
      userText("SYNTHETIC full Evo root contract: perform synthetic experiment", "harness"),
    );
  });

  it("requires the Trainer hook exception and keeps native Solver leaves free of root hooks", async () => {
    await enableHooks(false); // Frozen H0 config is intentionally hook-free.
    expect(nativeHooks(await session()).preToolUseHooks).toEqual([]);
    await enableHooks(true); // prepare_trainer must make this explicit exception.
    const trainer = nativeHooks(await session({ source: "cli", subagentDepth: 0 }));
    expect(trainer.userPromptHooks.map((hook) => hook.name)).toEqual([NAME]);
    expect(trainer.preToolUseHooks.map((hook) => hook.name)).toEqual([NAME]);
    expect(trainer.stopHooks.map((hook) => hook.name)).toEqual([NAME]);
    for (const opts of [{ source: "subagent" as const }, { subagentDepth: 1 }]) {
      const leaf = nativeHooks(await session(opts));
      expect(leaf.userPromptHooks).toEqual([]);
      expect(leaf.preToolUseHooks).toEqual([]);
      expect(leaf.stopHooks).toEqual([]);
    }
  });
});

describe("Evo hook failures are explicit and do not schedule recovery", () => {
  it.each([
    ["crash", "raise RuntimeError('SECRET-fixture-never-log')\n"],
    ["missing output", "pass\n"],
    ["invalid JSON", "print('SECRET-fixture-never-log')\n"],
    ["wrong result type", "print('null')\n"],
    ["continuation", 'print(\'{"decision":"continue"}\')\n'],
    ["detached subagent", 'print(\'{"subagent":{"prompt":"SECRET-fixture-never-log"}}\')\n'],
  ])("denies %s without leaking child output or silently falling open", async (_, source) => {
    await fixture(source);
    expect(await nativeCall(input())).toMatchObject({
      decision: "deny",
      output: { evo_blocked: true },
    });
    const diagnostic = await fs.readFile(path.join(scratch(), "evo-hook-blocked.json"), "utf8");
    const log = await fs.readFile(path.join(scratch(), "evo-hook-failures.jsonl"), "utf8");
    expect(diagnostic + log).not.toContain("SECRET-fixture-never-log");
    // Repairing a child process must not silently resume a previously failed Session.
    await fixture("print('{}')\n");
    expect(await nativeCall(input())).toMatchObject({
      decision: "deny",
      output: { failure_code: "session_previously_blocked" },
    });
  });

  it("blocks future tools when the user-prompt hook fails", async () => {
    await fixture("raise RuntimeError('secret prompt fixture')\n");
    expect(
      await nativeCall({ ...input("user_prompt"), prompt: "secret prompt fixture" }),
    ).toMatchObject({ context: expect.stringContaining("Evo-Harness is blocked") });
    await fixture("print('{}')\n");
    expect(await nativeCall(input())).toMatchObject({ decision: "deny" });
  });

  it("times out inside the native deadline and returns a deny instead of throwing", async () => {
    await fixture("import time\ntime.sleep(60)\n");
    const evaluation =
      `const { guard } = await import(${JSON.stringify(pathToFileURL(script).href)});\n` +
      `process.stdout.write(JSON.stringify(await guard(${JSON.stringify(JSON.stringify(input()))}, 25)));`;
    const run = spawnSync(process.execPath, ["--input-type=module", "-e", evaluation], {
      encoding: "utf8",
      timeout: 3_000,
    });
    expect(run.error).toBeUndefined();
    expect(run.status).toBe(0);
    expect(JSON.parse(run.stdout)).toMatchObject({
      decision: "deny",
      output: { failure_code: "control_timeout", diagnostic_persisted: true },
    });
  });

  it.each(["active", "complete", "wrapping_up"])(
    "corrects an invalid %s before the native Goal stop hook can continue or accept it",
    async (status) => {
      await installPlugin(root, ...ids, libraryPlugin("goal")!);
      await fixture("raise RuntimeError('synthetic control failure')\n");
      const current = await session();
      const hookSet = nativeHooks(current);
      expect(hookSet.stopHooks.map((hook) => hook.name)).toEqual([NAME, "goal"]);
      await current.runUserPromptHook("goal", "synthetic experiment", { budget: 500 });
      const file = path.join(scratch(current.sessionId), "GOAL.json");
      const goal = JSON.parse(await fs.readFile(file, "utf8"));
      await fs.writeFile(file, JSON.stringify({ ...goal, status }));
      const trace = path.join(root, "synthetic-trace.jsonl");
      await fs.writeFile(
        trace,
        `${JSON.stringify({
          type: "session_meta",
          payload: { session_id: current.sessionId, agent_state: state },
        })}\n`,
      );
      const spawn = vi.fn();
      const outcome = await runStopHooks(
        hookSet.stopHooks,
        { sessionId: current.sessionId, tracePath: trace },
        spawn,
      );
      expect(outcome.next).toBeNull();
      expect(spawn).not.toHaveBeenCalled();
      expect(JSON.parse(await fs.readFile(file, "utf8"))).toMatchObject({
        status: "blocked",
        ended: true,
      });
      expect(outcome.events).toContainEqual(
        expect.objectContaining({
          payload: expect.objectContaining({
            name: "goal",
            decision: "stop",
            output: expect.objectContaining({ status: "blocked" }),
          }),
        }),
      );
    },
  );

  it("does not follow caller-provided scratchpad coordinates or rewrite an ended Goal", async () => {
    await fixture("raise RuntimeError('synthetic')\n");
    await fs.mkdir(scratch(), { recursive: true });
    const file = path.join(scratch(), "GOAL.json");
    const ended = { status: "complete", ended: true, objective: "previous result" };
    await fs.writeFile(file, JSON.stringify(ended));
    const outside = path.join(root, "unrelated-scratchpad");
    expect(await nativeCall({ ...input("stop"), scratchpad_dir: outside })).toMatchObject({
      decision: "stop",
      output: { goal_blocked: false },
    });
    expect(JSON.parse(await fs.readFile(file, "utf8"))).toEqual(ended);
    await expect(fs.stat(outside)).rejects.toMatchObject({ code: "ENOENT" });
  });
});

describe("the actual installed Evo control.py behind native hooks", () => {
  async function trace(source: string, sessionId = "s1") {
    const file = path.join(root, `${sessionId}-original.jsonl`);
    await fs.writeFile(
      file,
      `${JSON.stringify({
        type: "session_meta",
        payload: {
          session_id: sessionId,
          agent_state: state,
          source,
          workspace: root,
          provider: "deepseek",
          model_id: "deepseek-flash",
        },
      })}\n`,
    );
    await fs.mkdir(scratch(sessionId), { recursive: true });
    return file;
  }

  it("is explicitly installed instead of enabling hooks on every default Agent", () => {
    expect(libraryPlugin(NAME)?.preinstall).toBe(false);
  });

  it.each(["user", "api"])("leaves unrelated %s Sessions in their native flow", async (source) => {
    const tracePath = await trace(source);
    const request = {
      ...input("user_prompt"),
      trace_path: tracePath,
      prompt: "An unrelated request",
    };
    expect(await nativeCall(request)).toEqual({ context: "" });
    expect(await nativeCall({ ...request, ...input() })).toEqual({});
    expect(await nativeCall({ ...request, ...input("stop") })).toEqual({});
    await expect(fs.stat(path.join(scratch(), "evo-hook-blocked.json"))).rejects.toMatchObject({
      code: "ENOENT",
    });
  });

  it("denies tools after an invalid initialization instead of falling through to the host policy", async () => {
    const tracePath = await trace("cli");
    await fs.writeFile(
      path.join(scratch(), "EVO_CONTROL.json"),
      JSON.stringify({
        schema: 2,
        mode: "invalid",
        session_id: "s1",
      }),
    );
    for (const tool of ["exec_command", "write_file", "run_subagent"]) {
      expect(
        await nativeCall({
          ...input(),
          trace_path: tracePath,
          tool_name: tool,
          arguments: JSON.stringify({
            cmd: "curl http://business.invalid",
            file_path: "/submission/answer.json",
          }),
        }),
      ).toMatchObject({ decision: "deny" });
    }
  });

  it("injects complete training contracts even when the benchmark prompt does not mention the Skill", async () => {
    const tracePath = await trace("cli");
    const privateLearning = path.join(root, "private-learning");
    const emptyLibrary = path.join(root, "empty-library.json");
    await fs.writeFile(emptyLibrary, "[]");
    const exportRequest = path.join(root, "export-request.json");
    await fs.writeFile(
      exportRequest,
      JSON.stringify({
        op: "export",
        library: emptyLibrary,
        limits: { general: 5, topic: 5 },
        max_entries: 5,
        max_bytes: 16000,
        output: path.join(state, "skills/evo-harness-learned"),
      }),
    );
    const exported = spawnSync(
      "python3",
      ["-B", path.join(path.dirname(control), "evo.py"), exportRequest],
      {
        encoding: "utf8",
        timeout: 3_000,
      },
    );
    expect(exported.error).toBeUndefined();
    expect(exported.status, exported.stderr).toBe(0);
    const profile = {
      guard_version: 2,
      train_ids: ["synthetic-member"],
      attempt_id: "attempt-1",
      private_learning: privateLearning,
      answer_dir: path.join(root, "submission"),
      feedback_dir: path.join(privateLearning, "feedback"),
      workspace: root,
      training_budget_usd: 1,
      runtime: { provider: "deepseek", model_id: "deepseek-flash", thinking_level: "high" },
    };
    await fs.writeFile(path.join(state, "skills", NAME, "training.json"), JSON.stringify(profile));
    const result = await nativeCall({
      ...input("user_prompt"),
      trace_path: tracePath,
      prompt: "Complete the assigned public task.",
    });
    expect(result).toMatchObject({
      context: expect.stringContaining("FULL METHOD MATERIAL references/group-training.md"),
    });
    expect(result).toMatchObject({
      context: expect.stringContaining("FULL METHOD MATERIAL references/roles.md"),
    });
    expect(result).toMatchObject({
      context: expect.stringContaining("FULL METHOD MATERIAL references/tools.md"),
    });
    expect(
      JSON.parse(await fs.readFile(path.join(scratch(), "EVO_CONTROL.json"), "utf8")),
    ).toMatchObject({ mode: "training", session_id: "s1" });
    expect(await fs.readFile(path.join(privateLearning, "roles/solver.md"), "utf8")).toContain(
      "Solver (group training leaf)",
    );
  });

  it("rejects invalid completion through actual control.py before the real Goal stop hook", async () => {
    await installPlugin(root, ...ids, libraryPlugin("goal")!);
    const current = await session({ source: "cli" });
    const tracePath = await trace("cli", current.sessionId);
    await current.runUserPromptHook("goal", "synthetic failed experiment", { budget: 500 });
    const file = path.join(scratch(current.sessionId), "GOAL.json");
    const goal = JSON.parse(await fs.readFile(file, "utf8"));
    await fs.writeFile(file, JSON.stringify({ ...goal, status: "complete" }));
    await fs.writeFile(
      path.join(scratch(current.sessionId), "EVO_CONTROL.json"),
      JSON.stringify({
        schema: 2,
        mode: "controller",
        configured: false,
        session_id: current.sessionId,
      }),
    );
    const spawn = vi.fn();
    const outcome = await runStopHooks(
      nativeHooks(current).stopHooks,
      { sessionId: current.sessionId, tracePath },
      spawn,
    );
    expect(outcome.next).toBeNull();
    expect(spawn).not.toHaveBeenCalled();
    expect(JSON.parse(await fs.readFile(file, "utf8"))).toMatchObject({
      status: "blocked",
      ended: true,
    });
    expect(
      JSON.parse(
        await fs.readFile(path.join(scratch(current.sessionId), "evo-acceptance.json"), "utf8"),
      ),
    ).toMatchObject({ complete: false, status: "method_failed" });
  });
});
