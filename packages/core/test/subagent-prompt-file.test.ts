import { createHash } from "node:crypto";
import { mkdir, mkdtemp, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Environment, Session, assistantText } from "../src/index.js";
import type { OmniMessage, SubagentRunner } from "../src/index.js";
import { defaultSystemConfig } from "../src/state/default-config.js";
import { createSubagentTool } from "../src/environment/tools/run-subagent.js";
import { MAX_SUBAGENT_PROMPT_BYTES } from "../src/environment/tools/subagent-prompt.js";
import { localFsPort } from "../src/environment/tools/fs-port.js";
import type { ToolFs } from "../src/environment/tools/fs-port.js";
import { SubagentSessionManager } from "../src/environment/tools/subagent/index.js";
import type { ToolResult } from "../src/environment/tools/types.js";

const sha = (bytes: string | Buffer) => createHash("sha256").update(bytes).digest("hex");
const definition = defaultSystemConfig().tools!.builtin!.find(
  (tool) => tool.name === "run_subagent",
)!;
let dir: string;
let manager: SubagentSessionManager;
beforeEach(async () => {
  dir = await realpath(await mkdtemp(path.join(tmpdir(), "penguin-file-prompt-")));
  manager = new SubagentSessionManager();
});
afterEach(async () => {
  manager.dispose();
  await rm(dir, { recursive: true, force: true });
});

async function invoke(args: Record<string, unknown>, fs?: ToolFs, signal?: AbortSignal) {
  const received: OmniMessage[][] = [];
  const spawn = vi.fn(async () => ({
    sessionId: "child-12345678",
    async *run({ messages }: { messages: OmniMessage[] }) {
      received.push(messages);
      yield assistantText("offline child finished");
      return null;
    },
    dispose() {},
  }));
  const tool = createSubagentTool(definition, {
    subagentRunner: { spawn },
    subagentSessions: manager,
  });
  const gen = tool.execute(args, { workspaceDir: dir, toolCallId: "call-file", fs, signal });
  const events: OmniMessage[] = [];
  let result: ToolResult | void;
  for (;;) {
    const next = await gen.next();
    if (next.done) {
      result = next.value;
      break;
    }
    events.push(next.value);
  }
  return { spawn, received, events, result };
}

describe("native subagent prompt-file transport", () => {
  it("advertises both forms without requiring an inline prompt for file calls", () => {
    expect(definition.parameters?.required).toEqual(["description"]);
    expect(definition.parameters?.properties).toHaveProperty("prompt_file");
    expect(definition.parameters?.properties).toHaveProperty("prompt_sha256");
  });

  it.each([false, true])(
    "delivers exact full text in foreground/background (%s)",
    async (background) => {
      const text =
        "\uFEFFCaller agent: root\r\n" + "中文🙂\u2028e\u0301".repeat(2200) + "\n  final  \r\n";
      const file = path.join(dir, "prompt.txt");
      await writeFile(file, text);
      const out = await invoke({
        prompt_file: background ? file : "prompt.txt",
        prompt_sha256: sha(text),
        run_in_background: background,
      });
      await new Promise((r) => setTimeout(r, 0));
      expect(out.result?.stopReason).toBe("completed");
      expect(out.spawn).toHaveBeenCalledTimes(1);
      expect(out.received).toHaveLength(1);
      expect(out.received[0]![0]!.payload).toMatchObject({
        type: "text",
        role: "user",
        sender: "parent_agent",
        text,
      });
      expect(JSON.stringify(out.events)).not.toContain(text);
    },
  );

  it("keeps inline messages unchanged without touching the filesystem", async () => {
    const fs = new Proxy({} as ToolFs, {
      get() {
        throw new Error("unexpected filesystem access");
      },
    });
    const text = " leading \r\nUnicode🙂 trailing  ";
    const out = await invoke({ prompt: text }, fs);
    expect(out.result?.stopReason).toBe("completed");
    expect(out.received[0]![0]!.payload).toMatchObject({ text });
  });

  it.each([
    { prompt: "inline", prompt_file: "x", prompt_sha256: "a".repeat(64) },
    { prompt: null, prompt_file: "x", prompt_sha256: "a".repeat(64) },
    { prompt: "inline", prompt_sha256: "a".repeat(64) },
    { prompt_file: "x" },
    { prompt_file: "x", prompt_sha256: "A".repeat(64) },
    { prompt_file: "x", prompt_sha256: "a".repeat(63) },
    { prompt_file: null, prompt_sha256: "a".repeat(64) },
  ])("rejects ambiguous or incomplete file arguments before spawning: %j", async (args) => {
    const out = await invoke(args);
    expect(out.result?.stopReason).toBe("fatal");
    expect(out.spawn).not.toHaveBeenCalled();
  });

  it.each([
    Buffer.alloc(0),
    Buffer.from(" \t\r\n"),
    Buffer.from([0xff, 0xfe]),
    Buffer.from("x\0y"),
    Buffer.alloc(MAX_SUBAGENT_PROMPT_BYTES + 1, 97),
  ])(
    "rejects empty, invalid, binary or oversized content without a child (case %#)",
    async (bytes) => {
      const file = path.join(dir, "invalid.txt");
      await writeFile(file, bytes);
      const out = await invoke({ prompt_file: file, prompt_sha256: sha(bytes) });
      expect(out.result?.stopReason).toBe("fatal");
      expect(out.spawn).not.toHaveBeenCalled();
    },
  );

  it("accepts exactly the byte limit and rejects a changed file against its original digest", async () => {
    const file = path.join(dir, "limit.txt"),
      bytes = Buffer.alloc(MAX_SUBAGENT_PROMPT_BYTES, 97);
    await writeFile(file, bytes);
    expect(
      (await invoke({ prompt_file: file, prompt_sha256: sha(bytes) })).spawn,
    ).toHaveBeenCalledTimes(1);
    await writeFile(file, "replacement");
    const out = await invoke({ prompt_file: file, prompt_sha256: sha(bytes) });
    expect(out.result?.stopReason).toBe("fatal");
    expect(out.spawn).not.toHaveBeenCalled();
  });

  it("refuses secret lexical names and symlink aliases before reading any content", async () => {
    const secret = path.join(dir, ".project_config.toml");
    await writeFile(secret, "PRIVATE_VALUE");
    const alias = path.join(dir, "alias.txt");
    await symlink(secret, alias);
    const regular = path.join(dir, "regular.txt");
    await writeFile(regular, "ordinary");
    const link = path.join(dir, "link.txt");
    await symlink(regular, link);
    const directory = path.join(dir, "directory");
    await mkdir(directory);
    await symlink(directory, path.join(dir, "linked-dir"));
    await writeFile(path.join(directory, "prompt.txt"), "ordinary");
    for (const file of [
      secret,
      alias,
      link,
      path.join(dir, ".VAULT.TOML"),
      path.join(dir, "linked-dir/prompt.txt"),
    ]) {
      const readRange = vi.fn(localFsPort.readRange);
      const out = await invoke(
        { prompt_file: file, prompt_sha256: sha("PRIVATE_VALUE") },
        { ...localFsPort, readRange },
      );
      expect(out.result?.stopReason).toBe("fatal");
      expect(out.spawn).not.toHaveBeenCalled();
      expect(readRange).not.toHaveBeenCalled();
      expect(JSON.stringify(out.events)).not.toContain("PRIVATE_VALUE");
    }
  });

  it("uses the sandbox port and cannot bypass denied access or call an unbounded read", async () => {
    const file = path.join(dir, "sandbox.txt");
    await writeFile(file, "payload");
    const denied: ToolFs = {
      ...localFsPort,
      sandboxed: true,
      readRange: vi.fn(async () => {
        throw Object.assign(new Error("private sandbox diagnostics"), { code: "EACCES" });
      }),
      readFile: vi.fn(async () => {
        throw new Error("unbounded read forbidden");
      }),
    };
    const failed = await invoke({ prompt_file: file, prompt_sha256: sha("payload") }, denied);
    expect(failed.result?.stopReason).toBe("fatal");
    expect(failed.spawn).not.toHaveBeenCalled();
    expect(denied.readRange).toHaveBeenCalledWith(file, 0, MAX_SUBAGENT_PROMPT_BYTES + 1);
    expect(denied.readFile).not.toHaveBeenCalled();
    expect(JSON.stringify(failed.events)).not.toContain("private sandbox diagnostics");
  });

  it("rejects directories, missing files, URLs and growth between stat and bounded read", async () => {
    for (const file of [dir, path.join(dir, "missing"), "https://example.invalid/prompt"])
      expect(
        (await invoke({ prompt_file: file, prompt_sha256: sha("x") })).spawn,
      ).not.toHaveBeenCalled();
    const file = path.join(dir, "growing");
    await writeFile(file, "x");
    const fs = {
      ...localFsPort,
      readRange: async () => Buffer.alloc(MAX_SUBAGENT_PROMPT_BYTES + 1, 120),
    };
    expect(
      (await invoke({ prompt_file: file, prompt_sha256: sha("x") }, fs)).spawn,
    ).not.toHaveBeenCalled();
  });

  it("does not create a child if interrupted during file transport", async () => {
    const file = path.join(dir, "prompt.txt");
    await writeFile(file, "valid");
    const abort = new AbortController();
    const fs = {
      ...localFsPort,
      readRange: async () => {
        abort.abort();
        return Buffer.from("valid");
      },
    };
    const out = await invoke({ prompt_file: file, prompt_sha256: sha("valid") }, fs, abort.signal);
    expect(out.result?.stopReason).toBe("aborted");
    expect(out.spawn).not.toHaveBeenCalled();
  });

  it("passes the complete file to an actual native child Session's original USER message", async () => {
    const text = "Caller agent: fixture\n" + "long immutable delegation\n".repeat(500);
    const file = path.join(dir, "native.txt");
    await writeFile(file, text);
    const childEvents: OmniMessage[] = [];
    let child: Session | undefined;
    const runner: SubagentRunner = {
      async spawn() {
        child = new Session({
          meta: {
            session_id: "child-12345678",
            provider: "offline",
            model_id: "scripted",
            model_context_window: 100000,
            system_prompt: "offline fixture",
            agent_state: dir,
            workspace: dir,
            source: "subagent",
          },
          bootstrap: async () => ({
            llm: {
              async *streamGenerate() {
                yield assistantText("done");
                return { status: "completed" as const };
              },
            },
          }),
          environment: new Environment({
            workspaceDir: dir,
            toolConfig: { customTools: [], mcpServers: [] },
          }),
          trace: {
            async write(event) {
              childEvents.push(event);
            },
          },
          imagesDir: path.join(dir, "images"),
          modelHasVision: true,
        });
        return {
          sessionId: child.sessionId,
          dispose: () => child!.dispose(),
          run: ({ messages, signal }) => child!.run(messages, { signal }),
        };
      },
    };
    const tool = createSubagentTool(definition, {
      subagentRunner: runner,
      subagentSessions: manager,
    });
    for await (const _ of tool.execute(
      { prompt_file: file, prompt_sha256: sha(text) },
      { workspaceDir: dir, toolCallId: "file-native" },
    )) {
      /* consume native tool */
    }
    const messages = childEvents.filter(
      (event) =>
        event.type === "model_msg" &&
        "type" in event.payload &&
        event.payload.type === "text" &&
        event.payload.role === "user",
    );
    expect(messages).toHaveLength(1);
    expect(messages[0]!.payload).toMatchObject({ text, sender: "parent_agent" });
  });
});
