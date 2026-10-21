/**
 * Native Penguin hook adapter, not an experiment runner. Main Penguin owns experiment
 * decisions; installed control.py only validates its requested actions and evidence.
 * Native hook exceptions otherwise mean "no opinion", so this
 * adapter converts transport failures into explicit denial and a Session-local latch.
 */
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const STATE = path.dirname(path.dirname(import.meta.dirname));
const CONTROL = path.join(STATE, "skills", "evo-harness-rsi-toolkit", "scripts", "control.py");
const TIMEOUT_MS = 20_000; // Leave time to persist failure before the native 30-second timeout.
const MAX_OUTPUT_BYTES = 4 * 1024 * 1024;
const POINTS = new Set(["user_prompt", "pre_tool_use", "stop"]);
const BLOCKED =
  "Evo-Harness is blocked: its native control hook could not be verified. " +
  "Do not continue this experiment, launch workers, or claim completion. " +
  "Inspect this Session's evo-hook-blocked.json diagnostic and repair the installation.";

function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function scratchpad(input) {
  if (
    path.basename(STATE) !== "agent_state" ||
    !object(input) ||
    typeof input.session_id !== "string" ||
    !/^[A-Za-z0-9_-]+$/.test(input.session_id)
  ) {
    return null;
  }
  // Never use caller-supplied scratchpad_dir, trace_path, state or executable fields.
  return path.join(path.dirname(STATE), "scratchpad", input.session_id);
}

async function exists(file) {
  try {
    await fs.access(file);
    return true;
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

async function atomicJson(file, value) {
  const temporary = `${file}.${process.pid}.tmp`;
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  await fs.rename(temporary, file);
}

async function block(input, code) {
  const dir = scratchpad(input);
  let persisted = false;
  let goalBlocked = false;
  if (dir !== null) {
    try {
      await fs.mkdir(dir, { recursive: true });
      const diagnostic = {
        schema: "evo-native-hook-failure/v1",
        hook: POINTS.has(input.hook) ? input.hook : "invalid",
        session_id: input.session_id,
        code,
        time: new Date().toISOString(),
      };
      // Do not put prompts, arguments, Python stderr, environment variables or keys in logs.
      await fs.appendFile(
        path.join(dir, "evo-hook-failures.jsonl"),
        `${JSON.stringify(diagnostic)}\n`,
        { mode: 0o600 },
      );
      const marker = path.join(dir, "evo-hook-blocked.json");
      if (!(await exists(marker))) await atomicJson(marker, diagnostic);
      persisted = true;
    } catch {
      /* The response must still deny when diagnostic storage is unavailable. */
    }
    if (input.hook === "stop") {
      try {
        const file = path.join(dir, "GOAL.json");
        const goal = JSON.parse(await fs.readFile(file, "utf8"));
        if (
          object(goal) &&
          goal.ended !== true &&
          ["active", "complete", "wrapping_up"].includes(goal.status)
        ) {
          await atomicJson(file, { ...goal, status: "blocked" });
          goalBlocked = true;
        }
      } catch {
        /* Record failure explicitly; never spawn a replacement or another run. */
      }
    }
  }
  const output = { evo_blocked: true, failure_code: code, diagnostic_persisted: persisted };
  if (input?.hook === "user_prompt") return { context: BLOCKED };
  if (input?.hook === "stop") {
    return { decision: "stop", reason: BLOCKED, output: { ...output, goal_blocked: goalBlocked } };
  }
  return { decision: "deny", reason: BLOCKED, output };
}

function validateResult(result, hook) {
  if (!object(result)) throw new Error("invalid_control_result");
  // Hooks may inspect and gate; they may never dispatch detached subagents or a new task.
  if ("subagent" in result || "input" in result) throw new Error("control_dispatch_forbidden");
  if (hook === "user_prompt") {
    if (typeof result.context !== "string" || Object.keys(result).some((k) => k !== "context")) {
      throw new Error("invalid_control_result");
    }
  } else {
    const allowed = new Set(["decision", "reason", "output"]);
    if (
      Object.keys(result).some((k) => !allowed.has(k)) ||
      (result.reason !== undefined && typeof result.reason !== "string") ||
      (result.decision !== undefined &&
        !(hook === "stop" ? ["stop"] : ["allow", "deny"]).includes(result.decision)) ||
      (result.output !== undefined &&
        (!object(result.output) ||
          Object.values(result.output).some(
            (v) => !["string", "number", "boolean"].includes(typeof v),
          )))
    ) {
      throw new Error("invalid_control_result");
    }
  }
  return result;
}

function runControl(raw, timeoutMs) {
  return new Promise((resolve, reject) => {
    const child = spawn("python3", ["-B", CONTROL, "--hook", "--state", STATE], {
      cwd: STATE,
      stdio: ["pipe", "pipe", "ignore"],
      shell: false,
    });
    let stdout = "";
    let bytes = 0;
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) {
        child.kill("SIGKILL");
        reject(new Error(error));
      } else resolve(value);
    };
    const timer = setTimeout(() => finish("control_timeout"), timeoutMs);
    child.on("error", () => finish("control_spawn_failed"));
    child.stdin.on("error", () => {
      /* Exit status is authoritative, including early exits. */
    });
    child.stdout.setEncoding("utf8").on("data", (chunk) => {
      bytes += Buffer.byteLength(chunk);
      if (bytes > MAX_OUTPUT_BYTES) finish("control_output_limit");
      else stdout += chunk;
    });
    child.on("close", (code) => {
      if (settled) return;
      if (code !== 0) return finish("control_exit_failed");
      try {
        finish(null, JSON.parse(stdout));
      } catch {
        finish("invalid_control_json");
      }
    });
    child.stdin.end(raw); // Preserve native stdin bytes/fields; no prompt reconstruction.
  });
}

/** Exported only for deterministic timeout tests; the CLI accepts no options or path override. */
export async function guard(raw, timeoutMs = TIMEOUT_MS) {
  let input;
  try {
    input = JSON.parse(raw);
    if (!object(input) || !POINTS.has(input.hook) || scratchpad(input) === null) {
      return await block(input, "invalid_hook_input");
    }
    if (await exists(path.join(scratchpad(input), "evo-hook-blocked.json"))) {
      return await block(input, "session_previously_blocked");
    }
    return validateResult(await runControl(raw, timeoutMs), input.hook);
  } catch (error) {
    const codes = new Set([
      "control_timeout",
      "control_spawn_failed",
      "control_exit_failed",
      "invalid_control_json",
      "control_output_limit",
      "invalid_control_result",
      "control_dispatch_forbidden",
    ]);
    return await block(input, codes.has(error.message) ? error.message : "control_adapter_failed");
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let raw = "";
  for await (const chunk of process.stdin) raw += chunk;
  process.stdout.write(`${JSON.stringify(await guard(raw))}\n`);
}
