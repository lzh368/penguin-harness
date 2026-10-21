import { readFileSync } from "node:fs";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { loadLibraryPlugins } from "../src/plugins/index.js";
import { Environment } from "../src/environment/index.js";
import { toolCall } from "../src/omnimessage/index.js";

describe("Evo-Harness native plugin", () => {
  it("loads the method and all support files through the real plugin library", () => {
    const plugin = loadLibraryPlugins().find((p) => p.name === "evo-harness-rsi-toolkit");
    expect(plugin?.category).toBe("rsi");
    expect(plugin?.skills.map((s) => s.name)).toEqual(["evo-harness-rsi-toolkit"]);
    expect(plugin?.version).toBe("0.2.16");
  });

  it("reuses the complete shared evaluation protocol without changing other methods", () => {
    const root = resolve(import.meta.dirname, "../../../plugins");
    const content = readFileSync(
      `${root}/evo-harness-rsi-toolkit/skills/evo-harness-rsi-toolkit/references/evaluation.md`,
      "utf8",
    );
    for (const method of ["opro", "ape", "ace", "awm"]) {
      expect(content).toBe(
        readFileSync(
          `${root}/${method}-rsi-toolkit/skills/${method}-rsi-toolkit/references/evaluation.md`,
          "utf8",
        ),
      );
    }
  });

  it("preserves a legal maximum-size selector receipt through the actual native tool output boundary", async () => {
    const root = await mkdtemp(resolve(tmpdir(), "synthetic-evo-selector-"));
    try {
      const library = [
        {
          id: "escaped-entry",
          level: "general",
          trigger: "synthetic",
          scope: "synthetic",
          body: '"\\\n'.repeat(5300),
        },
      ];
      await writeFile(resolve(root, "library.json"), JSON.stringify(library));
      await writeFile(
        resolve(root, "export.json"),
        JSON.stringify({
          op: "export",
          library: resolve(root, "library.json"),
          limits: { general: 5, topic: 5 },
          max_entries: 5,
          max_bytes: 16000,
          output: resolve(root, "skill"),
        }),
      );
      const helper = resolve(
        import.meta.dirname,
        "../../../plugins/evo-harness-rsi-toolkit/skills/evo-harness-rsi-toolkit/scripts/evo.py",
      );
      const exported = spawnSync("python3", ["-B", helper, resolve(root, "export.json")], {
        encoding: "utf8",
      });
      expect(exported.error).toBeUndefined();
      expect(exported.status, exported.stderr).toBe(0);
      const run = async (maxOutputLength: number) => {
        const env = new Environment({
          workspaceDir: root,
          toolConfig: {
            customTools: [
              {
                name: "exec_command",
                description: "Synthetic selector boundary check",
                permission: "rw",
                maxOutputLength,
                parameters: {
                  type: "object",
                  properties: { cmd: { type: "string" } },
                  required: ["cmd"],
                },
              },
            ],
            mcpServers: [],
          },
        });
        try {
          let output = "";
          for await (const row of env.executeTool({
            toolCall: toolCall({
              name: "exec_command",
              arguments: JSON.stringify({ cmd: "python3 -B skill/select.py --ids escaped-entry" }),
              toolCallId: "selection",
            }),
          })) {
            if ("type" in row.payload && row.payload.type === "tool_call_output") {
              output = row.payload.output;
            }
          }
          return output;
        } finally {
          env.dispose();
        }
      };
      expect(await run(16000)).toContain("output truncated");
      const complete = await run(6 * 16000 + 8192);
      expect(complete).not.toContain("output truncated");
      const receipt = JSON.parse(complete.split("\n")[0]!);
      expect(receipt.selected).toEqual(["escaped-entry"]);
      expect(receipt.bytes).toBeLessThanOrEqual(16000);
      expect(receipt.text).toContain(library[0]!.body);
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
});
