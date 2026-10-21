/** Exact, bounded file transport for a model's native subagent delegation. */
import { createHash } from "node:crypto";
import path from "node:path";
import { localFsPort, secretStoreName } from "./fs-port.js";
import type { ToolExecutionContext } from "./types.js";

export const MAX_SUBAGENT_PROMPT_BYTES = 1024 * 1024;

/** Read through the Session's sandbox port; no shell, URL, or content echo. */
export async function readSubagentPromptFile(
  file: string,
  expectedSha256: string,
  ctx: ToolExecutionContext,
): Promise<string> {
  if (!file || file.includes("\0") || /^[a-z][a-z0-9+.-]*:\/\//i.test(file)) {
    throw new Error("prompt_file must be a local file path");
  }
  const resolved = path.resolve(ctx.workspaceDir, file);
  if (secretStoreName(resolved)) throw new Error("prompt_file cannot read a secret store");
  const fs = ctx.fs ?? localFsPort;
  const target = await fs.realpath(resolved);
  if (secretStoreName(target)) throw new Error("prompt_file cannot read a secret store");
  if (target !== resolved)
    throw new Error("prompt_file must use a canonical path without symlinks");
  const stat = await fs.stat(resolved);
  if (
    !stat.isFile ||
    !Number.isSafeInteger(stat.size) ||
    stat.size <= 0 ||
    stat.size > MAX_SUBAGENT_PROMPT_BYTES
  ) {
    throw new Error("prompt_file must be a nonempty regular file of at most 1 MiB");
  }
  if (ctx.signal?.aborted) throw new Error("prompt_file read aborted");
  // A bounded read also detects growth after stat, without an unbounded readFile.
  // Only these exact bytes are hashed, decoded and passed to the child.
  const bytes = await fs.readRange(resolved, 0, MAX_SUBAGENT_PROMPT_BYTES + 1);
  if (
    bytes.length !== stat.size ||
    bytes.length > MAX_SUBAGENT_PROMPT_BYTES ||
    (await fs.realpath(resolved)) !== resolved
  ) {
    throw new Error("prompt_file changed during reading");
  }
  if (createHash("sha256").update(bytes).digest("hex") !== expectedSha256) {
    throw new Error("prompt_file SHA256 does not match prompt_sha256");
  }
  if (bytes.includes(0)) throw new Error("prompt_file must be UTF-8 text without NUL bytes");
  // ignoreBOM=true preserves a UTF-8 BOM in the delivered string. No trimming,
  // CRLF conversion or Unicode normalization may change the authorized text.
  const prompt = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  if (!prompt.trim()) throw new Error("prompt_file must contain non-whitespace text");
  return prompt;
}
