import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { loadLibraryPlugins } from "../src/plugins/index.js";

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
});
