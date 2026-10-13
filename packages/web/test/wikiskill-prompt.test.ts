/**
 * WikiSkill's Web handoff freezes a run's selected runtime rather than a vendor baked into a
 * Skill or a different experiment's baseline.
 *
 * - A WikiSkill request with a historical baseline still resolves the actual Session runtime,
 *   without pinning the old provider or model in its prompt.
 * - Both languages name the runtime fields the Session exposes, distinguish per-case runs from
 *   independent repeats, and request a training entry without treating it as validation/test.
 * - A supplied workspace draft stays before the WikiSkill instructions with its paths and
 *   preparation-only scope intact; blank Focus contributes nothing.
 * - Other RSI methods continue receiving their baseline runtime and per-case run count.
 * - A WikiSkill draft needs a Controller; the Skill may prepare a Target, while an explicit
 *   Target must differ from the Controller.
 * - Both languages request preparation without inventing a Target identity, and preserve
 *   explicit existing Targets for the Skill to verify before reuse.
 */
import { describe, expect, it } from "vitest";
import { defaultSystemConfig } from "@prismshadow/penguin-core";
import {
  buildRsiOptimizePrompt,
  wikiSkillAgentPairValid,
  wikiskillOptimizeTail,
} from "../src/features/benchmark/benchmark-prompts";
import { zh } from "../src/lib/strings";
import { en } from "../src/lib/strings-en";

const params = {
  skill: "rsi-wikiskill",
  targetAgentId: "target-reports",
  benchmarkId: "reports-train",
  runs: 3,
};

describe("WikiSkill's experiment handoff", () => {
  it("allows the Skill to prepare a Target but rejects an explicit Controller as Target", () => {
    expect(wikiSkillAgentPairValid("controller", "target")).toBe(true);
    expect(wikiSkillAgentPairValid("default_agent", "default_agent")).toBe(false);
    expect(wikiSkillAgentPairValid(null, "target")).toBe(false);
    expect(wikiSkillAgentPairValid("", "target")).toBe(false);
    expect(wikiSkillAgentPairValid("controller", "")).toBe(true);
    expect(wikiSkillAgentPairValid(null, "")).toBe(false);
    expect(wikiSkillAgentPairValid("", "")).toBe(false);
  });

  it("does not pin a historical baseline's runtime into a new experiment's prompt", () => {
    const text = buildRsiOptimizePrompt("", {
      ...params,
      provider: "deepseek",
      modelId: "old-baseline-model",
    });
    expect(text).toContain("`target-reports`");
    expect(text).not.toContain("old-baseline-model");
    expect(text).not.toContain("deepseek");
    // Only the actual Session establishes runtime, even if an old OpenRouter baseline exists.
    expect(
      buildRsiOptimizePrompt("", {
        ...params,
        provider: "openrouter",
        modelId: "deepseek/deepseek-v4.1-flash",
      }),
    ).toBe(text);
  });

  const environmentLabel = (placeholder: string): string => {
    const line = defaultSystemConfig()
      .system_prompt.split("\n")
      .find((l) => l.endsWith(`: ${placeholder}`));
    const label = /^- (.+): \{\{[A-Z_]+\}\}$/.exec(line ?? "")?.[1];
    if (label === undefined) throw new Error(`no Environment line renders ${placeholder}`);
    return label;
  };

  for (const [locale, dict] of Object.entries({ zh, en })) {
    it(`${locale}: delegates Target preparation without claiming an Agent already exists`, () => {
      const prompt = dict.benchmark.wikiskillOptimizeTail({ ...params, targetAgentId: "" });
      expect(prompt).toMatch(/target_setup[：:] ?`skill-managed`/);
      expect(prompt).not.toContain("test_agent_id");
      expect(prompt).not.toContain("`target-reports`");
      expect(prompt).toMatch(/已有实验记录|existing experiment records/);
      expect(prompt).toMatch(/不提前创建|do not pre-create/);
    });

    it(`${locale}: resolves the Session runtime and separates the training entry from held-out evaluation`, () => {
      const prompt = dict.benchmark.wikiskillOptimizeTail(params);
      expect(prompt).toMatch(/test_agent_id[：:] ?`target-reports`/);
      expect(prompt).not.toContain("target_setup");
      expect(prompt).toMatch(/先核验能否用于此实验|verify that it can be used for this experiment/);
      for (const placeholder of ["{{PROVIDER}}", "{{MODEL_ID}}"])
        expect(prompt).toContain(`\`${environmentLabel(placeholder)}\``);
      expect(prompt).toContain("`rsi-wikiskill`");
      expect(prompt).toMatch(/train_benchmark_id[：:] ?`reports-train`/);
      expect(prompt).toMatch(/runs_per_case[：:] ?`3`/);
      expect(prompt).toContain("validation");
      expect(prompt).toContain("test");
      expect(prompt).toContain("OUT");
      expect(prompt).not.toContain("deepseek");
      expect(prompt).not.toContain("gdpevo-001");
      expect(prompt).not.toContain("agent-evaluation");
    });
  }

  it("preserves a workspace draft's paths and preparation-only scope before the algorithm instructions", () => {
    const focus =
      "Preparation only. Use /experiments/reports-v2/profile.json; OUT=/runs/reports-r1.\n" +
      "Read the validated draft at /drafts/reports-smoke.txt. Do not start evaluation.";
    const prompt = buildRsiOptimizePrompt(focus, params);
    expect(prompt.startsWith(`${focus}\n\n`)).toBe(true);
    expect(prompt).toContain(wikiskillOptimizeTail(params));
    expect(buildRsiOptimizePrompt("   ", params)).toBe(wikiskillOptimizeTail(params));
  });

  it.each(["opro", "ape", "ace", "awm"])(
    "%s keeps its own Skill and historical baseline runtime",
    (method) => {
      const prompt = buildRsiOptimizePrompt("Keep the original budget.", {
        ...params,
        skill: `${method}-rsi-toolkit`,
        provider: "baseline-provider",
        modelId: "baseline-model",
      });
      expect(prompt).toContain(`\`${method}-rsi-toolkit\``);
      expect(prompt).toMatch(/provider[：:] ?`baseline-provider`/);
      expect(prompt).toMatch(/model_id[：:] ?`baseline-model`/);
      expect(prompt).toMatch(/runs[：:] ?`3`/);
      expect(prompt).not.toContain("train_benchmark_id");
      expect(prompt).not.toContain("rsi-wikiskill");
    },
  );
});
