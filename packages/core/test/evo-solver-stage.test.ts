/** Real native parent/child/tool execution, scripted model emissions and a loopback canary. */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import http from "node:http";
import { spawnSync } from "node:child_process";
import { afterEach, expect, it, vi } from "vitest";
import {
  createAgent,
  installPlugin,
  readLibraryPackage,
  GenerativeModel,
  userText,
  toolCall,
  assistantText,
} from "../src/index.js";
import type { Session } from "../src/index.js";
import { seedProject } from "./seeded-project.js";
import { stubProviderKeys } from "./provider-keys.js";

let root: string, server: http.Server;
let parent: Session | undefined;
let restoreKeys: (() => void) | undefined;
afterEach(async () => {
  parent?.dispose();
  restoreKeys?.();
  vi.restoreAllMocks();
  if (server) await new Promise<void>((resolve) => server.close(() => resolve()));
  if (root) await fs.rm(root, { recursive: true, force: true });
});
const quote = (s: string) => "'" + s.replaceAll("'", "'\\''") + "'";
type Step = { name: string; args: Record<string, unknown>; id?: string };

it("refuses the real 024 query before HTTP; permits same-Session draft edits and sealing after native denials", async () => {
  root = await fs.mkdtemp(path.join(os.tmpdir(), "evo-native-stage-"));
  restoreKeys = stubProviderKeys();
  await seedProject(root);
  let judges = 0,
    queries = 0;
  server = http.createServer((req, res) => {
    req.resume();
    req.on("end", () => {
      res.setHeader("Content-Type", "application/json");
      if (req.url === "/api/judge") {
        judges++;
        res.end(
          JSON.stringify({ score: 0.5, correct: false, feedback: "SYNTHETIC aggregate feedback" }),
        );
      } else {
        queries++;
        res.end('{"rows":[{"unexpected":true}]}');
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as { port: number }).port;
  vi.spyOn(globalThis, "fetch").mockRejectedValue(
    new Error("No external/model network in this test"),
  );
  const agent = await createAgent({
    root,
    agentId: "trainer",
    controlEnv: (ctx) => ({ PENGUIN_SESSION_ID: ctx.sessionId }),
  });
  const workspace = path.join(root, "workspace");
  await fs.mkdir(workspace);
  // Assemble the test parent before installing the method. Its child reads the real newly installed hooks.
  parent = await agent.createSession({
    workspaceDir: workspace,
    source: "cli",
    provider: "deepseek",
    modelId: "deepseek-flash",
    thinkingLevel: "xhigh",
  });
  const boot = (parent as unknown as { ensureReady(): AsyncGenerator<unknown> }).ensureReady();
  for await (const _ of boot) {
  }
  await installPlugin(
    root,
    "default_project",
    "trainer",
    readLibraryPackage(
      path.resolve(import.meta.dirname, "../../../plugins/evo-harness-rsi-toolkit"),
    ),
  );
  const state = path.join(root, "default_project/agents/trainer/agent_state");
  const fixture = spawnSync(
    "python3",
    [
      "-B",
      path.resolve(
        import.meta.dirname,
        "../../../plugins/evo-harness-rsi-toolkit/tests/native_stage_fixture.py",
      ),
    ],
    {
      input: JSON.stringify({
        state,
        workspace,
        parent_session: parent.sessionId,
        judge_url: `http://127.0.0.1:${port}/api/judge`,
      }),
      encoding: "utf8",
    },
  );
  expect(fixture.status, fixture.stderr).toBe(0);
  const f = JSON.parse(fixture.stdout);
  const cmd = (request: string): Step => ({
    name: "exec_command",
    args: { cmd: `python3 -B ${quote(f.helper)} ${quote(request)}` },
  });
  const steps: Step[] = f.reads.map(cmd);
  steps.push({
    name: "write_file",
    args: { file_path: f.paths.answer, content: '{"synthetic":true}' },
  });
  // Exact 005 failure shape: helper launched with a cd prefix used to abort
  // readiness, then poison later evidence as a second executed judge.
  steps.push({ name: "exec_command", id: "blocked-005-wrapped-judge",
    args: { cmd: `cd ${quote(workspace)} && python3 -B ${quote(f.helper)} ${quote(f.paths.judge_request)}` } });
  steps.push(cmd(f.paths.judge_request));
  let parentCalls = 0,
    childCalls = 0,
    expanded = false,
    childDone = false;
  let childHandle = "";
  const models = new WeakMap<object, "parent" | "child">();
  vi.spyOn(GenerativeModel.prototype, "streamGenerate").mockImplementation(async function* (
    this: GenerativeModel,
    params,
  ) {
    let role = models.get(this);
    if (!role) {
      role = params.newMessages.some(
        (m) =>
          "type" in m.payload && m.payload.type === "text" && m.payload.text.includes('"evo_task"'),
      )
        ? "child"
        : "parent";
      models.set(this, role);
    }
    if (role === "parent") {
      if (parentCalls++ === 0)
        yield toolCall({
          name: "run_subagent",
          arguments: JSON.stringify(f.call),
          toolCallId: "native-parent-dispatch",
        });
      else if (childDone) yield assistantText("Native child finished.");
      else {
        const out = params.newMessages
          .filter((m) => "type" in m.payload && m.payload.type === "tool_call_output")
          .map((m) => (m.payload as { output: string }).output)
          .join("\n");
        childHandle = out.match(/subagent-[a-f0-9]+/)?.[0] ?? childHandle;
        if (out.includes("[subagent error:") || parentCalls > 80) throw new Error(out.slice(-3000));
        if (!childHandle) throw new Error("Missing native child handle: " + out.slice(-2000));
        yield toolCall({
          name: "input_subagent",
          arguments: JSON.stringify({ subagent_id: childHandle, yield_time_ms: 1000 }),
          toolCallId: `parent-poll-${parentCalls}`,
        });
      }
    } else {
      if (childCalls === steps.length && !expanded) {
        expanded = true;
        steps.push({
          name: "write_file",
          id: "blocked-before-full-feedback",
          args: { file_path: f.contract.output, content: JSON.stringify([f.candidate]) },
        });
        const feedback = await fs.readFile(f.paths.output, "utf8");
        for (let offset = 0; offset < Array.from(feedback).length; offset += 4000) {
          const p = path.join(f.contract.request_dir, `feedback-${offset}.json`);
          steps.push(
            {
              name: "write_file",
              args: {
                file_path: p,
                content: JSON.stringify({ op: "read-material", path: f.paths.output, offset }),
              },
            },
            cmd(p),
          );
        }
        // Actual native calls, not a forged denial receipt. The local server must observe zero queries.
        steps.push({
          name: "exec_command",
          id: "blocked-024-query",
          args: {
            cmd: `curl -s -X POST http://127.0.0.1:${port}/api/query -H 'Content-Type: application/json' -d '{"sql":"SELECT * FROM work_items WHERE id IN (?,?,?)","params":["A","B","C"]}'`,
          },
        });
        steps.push(
          {
            name: "edit_file",
            id: "blocked-answer-edit",
            args: { file_path: f.paths.answer, old_string: "true", new_string: "false" },
          },
          { ...cmd(f.paths.judge_request), id: "blocked-second-judge" },
          {
            name: "edit_file",
            id: "blocked-cross-task-edit",
            args: { file_path: f.other, old_string: "OTHER", new_string: "CHANGED" },
          },
        );
        const local = path.join(f.contract.request_dir, "local.json");
        steps.push(
          {
            name: "write_file",
            args: {
              file_path: local,
              content: JSON.stringify({
                op: "reflection-evidence",
                task_id: "train_001",
                source: { input: f.input },
                metrics: { total: { op: "sum", field: ["value"] } },
              }),
            },
          },
          cmd(local),
        );
        const text = JSON.stringify([{ ...f.candidate, lesson: "", task_id_note: null }], null, 2);
        const old = '"lesson": ""';
        const seal = path.join(f.contract.request_dir, "seal.json");
        steps.push(
          { name: "write_file", args: { file_path: f.contract.output, content: text } },
          {
            name: "write_file",
            args: {
              file_path: seal,
              content: JSON.stringify({
                op: "seal-role-output",
                role: "reflector",
                task_id: "train_001",
              }),
            },
          },
          { ...cmd(seal), id: "recoverable-draft-seal" },
          {
            name: "edit_file",
            id: "allowed-format-edit",
            args: {
              file_path: f.contract.output,
              old_string: ',\n    "task_id_note": null',
              new_string: "",
            },
          },
          {
            name: "edit_file",
            id: "allowed-own-edit",
            args: {
              file_path: f.contract.output,
              old_string: old,
              new_string:
                '"lesson": ' +
                JSON.stringify(f.candidate.lesson.replace("sum authorized", "sum the authorized")),
            },
          },
        );
        steps.push(
          {
            name: "write_file",
            args: {
              file_path: seal,
              content: JSON.stringify({
                op: "seal-role-output",
                role: "reflector",
                task_id: "train_001",
              }),
            },
          },
          { ...cmd(seal), id: "allowed-seal" },
        );
        steps.push({
          name: "edit_file",
          id: "blocked-sealed-edit",
          args: { file_path: f.contract.output, old_string: "SYNTHETIC", new_string: "ALTERED" },
        });
      }
      const next = steps[childCalls++];
      if (next)
        yield toolCall({
          name: next.name,
          arguments: JSON.stringify(next.args),
          toolCallId: next.id ?? `native-step-${childCalls}`,
        });
      else {
        childDone = true;
        yield assistantText("Reflection sealed in this original Session.");
      }
    }
    return { status: "completed" };
  });
  const messages = [];
  for await (const m of parent.run([userText("Run the native original Solver fixture.")], {
    approve: async () => "allow",
  }))
    messages.push(m);
  expect(
    judges,
    JSON.stringify(
      messages
        .filter((m) => "type" in m.payload && m.payload.type === "tool_call_output")
        .slice(-5),
    ),
  ).toBe(1);
  expect(queries).toBe(0);
  expect(await fs.readFile(f.paths.answer, "utf8")).toBe('{"synthetic":true}');
  expect(await fs.readFile(f.other, "utf8")).toBe("OTHER_UNCHANGED");
  const traceDir = path.join(root, "default_project/agents/trainer/traces");
  const nativeRows = [];
  for (const day of await fs.readdir(traceDir)) {
    for (const file of await fs.readdir(path.join(traceDir, day))) {
      if (!file.endsWith(".jsonl")) continue;
      const rows = (await fs.readFile(path.join(traceDir, day, file), "utf8"))
        .trim()
        .split("\n")
        .map((line) => JSON.parse(line));
      if (rows.some((r) => r.type === "session_meta" && r.payload.source === "subagent"))
        nativeRows.push(...rows);
    }
  }
  const outputs = nativeRows
    .filter((r) => r.type === "model_msg" && r.payload.type === "tool_call_output")
    .map((r) => r.payload as { tool_call_id: string; stop_reason: string; output: string });
  for (const id of [
    "blocked-005-wrapped-judge",
    "blocked-before-full-feedback",
    "blocked-024-query",
    "blocked-answer-edit",
    "blocked-second-judge",
    "blocked-cross-task-edit",
    "blocked-sealed-edit",
  ]) {
    expect(
      outputs.find((o) => o.tool_call_id === id),
      id,
    ).toMatchObject({ stop_reason: "aborted" });
  }
  expect(
    outputs.find((o) => o.tool_call_id === "allowed-own-edit"),
    JSON.stringify(outputs.slice(-10)),
  ).toMatchObject({ stop_reason: "completed" });
  const sealed = outputs.find((o) => o.tool_call_id === "allowed-seal");
  expect(sealed?.output, JSON.stringify(outputs.slice(-8))).toContain('"complete": true');
  expect(await fs.readFile(f.contract.output, "utf8")).toContain("sum the authorized rows");
  expect(outputs.find((o) => o.tool_call_id === "recoverable-draft-seal")?.output).toContain(
    '"recoverable": true',
  );
  const local = outputs.find((o) => o.output.startsWith('{"op": "reflection-evidence"'));
  expect(JSON.parse(JSON.parse(local!.output).text)).toEqual([{ group: [], total: 5 }]);
}, 45000);
