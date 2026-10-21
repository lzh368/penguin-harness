/** Production server -> core subagent -> OS exec. Only LLM output is scripted;
 * no controlEnv override, injected Session id, fake Session/runner or network.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { expect, it, vi } from "vitest";
import {
  createAgent,
  GenerativeModel,
  saveProjectConfig,
  toolCall,
  assistantText,
  userText,
} from "@prismshadow/penguin-core";
import type { GenerativeModelParameters, OmniMessage } from "@prismshadow/penguin-core";
import { createTestApp, waitFor } from "./helpers.js";
import type { ChannelEvent } from "../src/runtime/channel.js";

it("server injects each native leaf own Session id into real exec commands", async () => {
  const calls = new WeakMap<object, { role: string; turns: number }>();
  let syntheticTurns = 0;
  const network = vi
    .spyOn(globalThis, "fetch")
    .mockRejectedValue(new Error("synthetic env probe forbids network"));
  const model = vi
    .spyOn(GenerativeModel.prototype, "streamGenerate")
    .mockImplementation(async function* (this: GenerativeModel, params: GenerativeModelParameters) {
      let state = calls.get(this);
      if (!state) {
        state = {
          role: JSON.stringify(params.newMessages).includes("synthetic-leaf-marker")
            ? "leaf"
            : "root",
          turns: 0,
        };
        calls.set(this, state);
      }
      state.turns++;
      syntheticTurns++;
      if (state.turns === 1) {
        const code =
          'import json,os; print(json.dumps({k:os.environ.get(k) for k in ["PENGUIN_SESSION_ID","PENGUIN_AGENT_ID","PENGUIN_PROJECT_ID"]}))';
        yield toolCall({
          name: "exec_command",
          arguments: JSON.stringify({ cmd: "python3 -c '" + code + "'" }),
          toolCallId: "probe_" + state.role,
          stopReason: "completed",
        });
      } else if (state.role === "root" && state.turns === 2) {
        yield toolCall({
          name: "run_subagent",
          arguments: JSON.stringify({ prompt: "synthetic-leaf-marker", agent_id: "leaf" }),
          toolCallId: "delegate_leaf",
          stopReason: "completed",
        });
      } else {
        yield assistantText("synthetic complete");
      }
      return { status: "completed" } as const;
    });
  const app = await createTestApp({
    titles: { maybeGenerate() {} },
    beforeSeed: async (root) => {
      await saveProjectConfig(root, "default_project", {
        providers: {},
        models: [
          {
            provider: "custom",
            model_id: "synthetic-control-env",
            context_window: 100000,
            api_key: "SYNTHETIC_UNUSED_KEY",
            base_url: "http://127.0.0.1:1",
            client_type: "openai-chat",
          },
        ],
        default_model: { provider: "custom", model_id: "synthetic-control-env" },
      });
      await createAgent({ root, projectId: "default_project", agentId: "leaf" });
    },
  });
  try {
    const workspace = path.join(app.root, "synthetic-workspace");
    await fs.mkdir(workspace);
    const root = await app.deps.sessionService.createSession({
      projectId: "default_project",
      agentId: "default_agent",
      provider: "custom",
      modelId: "synthetic-control-env",
      workspace,
      approvalMode: "allow-all",
      isAdmin: true,
    });
    const events: ChannelEvent[] = [];
    app.deps.channels.get(root.sessionId).subscribe((event) => events.push(event));
    await app.deps.manager.startTask(root.sessionId, [userText("synthetic-root-marker")]);
    await waitFor(() => app.deps.manager.statusOf(root.sessionId) === "idle", 15000);
    const messages = events
      .filter((e) => e.event === undefined)
      .map((e) => JSON.parse(e.data) as OmniMessage);
    const payload = (m: OmniMessage) => m.payload as unknown as Record<string, unknown>;
    const leafMeta = messages.find((m) => m.type === "session_meta" && m.origin?.length);
    expect(
      leafMeta,
      JSON.stringify(
        messages.map((m) => ({ type: m.type, origin: m.origin, payload: payload(m) })),
      ),
    ).toBeDefined();
    const leafId = payload(leafMeta!).session_id;
    expect(leafId).not.toBe(root.sessionId);
    expect(leafMeta!.origin).toContain(leafId);
    for (const [role, sessionId, agentId] of [
      ["root", root.sessionId, "default_agent"],
      ["leaf", leafId, "leaf"],
    ]) {
      const output = messages.find(
        (m) =>
          payload(m).type === "tool_call_output" && payload(m).tool_call_id === "probe_" + role,
      );
      expect(output, "real " + role + " exec output").toBeDefined();
      const line = String(payload(output!).output)
        .split("\n")
        .find((line) => line.startsWith("{"));
      expect(JSON.parse(line!)).toEqual({
        PENGUIN_SESSION_ID: sessionId,
        PENGUIN_AGENT_ID: agentId,
        PENGUIN_PROJECT_ID: "default_project",
      });
    }
    expect(syntheticTurns).toBe(5);
    expect(network).not.toHaveBeenCalled();
  } finally {
    await app.deps.manager.shutdown();
    await app.cleanup();
    model.mockRestore();
    network.mockRestore();
  }
}, 20000);
