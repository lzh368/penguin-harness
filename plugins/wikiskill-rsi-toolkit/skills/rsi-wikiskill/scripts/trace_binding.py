#!/usr/bin/env python3
"""Recompute redacted runtime facts from a trial's complete exported traces.

Evaluator-only: reads private traces, prints hashes/identities, never prompts or
answers. This proves facts about supplied bytes, not OS isolation or export
completeness. Container file/archive/version checks remain separate audits.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
BEGIN = "<!-- WIKISKILL_DERIVED_BEGIN -->"
END = "<!-- WIKISKILL_DERIVED_END -->"
# The pinned Harbor runtime predates Penguin's benchmark -> cli source rename.
# This is read-time compatibility, never a rewrite of the original trace bytes.
LEGACY_BENCHMARK_VERSION = "0.2.13"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def reject_constant(_):
    raise ValueError("non-finite JSON value")


def parse(data):
    return json.loads(data, object_pairs_hook=unique, parse_constant=reject_constant)


def safe_path(path):
    path = Path(os.path.abspath(path))
    require(all(not p.is_symlink() for p in (path, *path.parents)), "symlink evidence refused")
    return path


def read(path):
    path = safe_path(path)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "evidence must be an unlinked regular file")
    return path.read_bytes()


def block(prompt):
    require(isinstance(prompt, str), "missing assembled system prompt")
    require(prompt.count(BEGIN) == prompt.count(END) == 1, "expected exactly one complete injection block")
    start, end = prompt.index(BEGIN), prompt.index(END)
    require(start < end, "reversed injection markers")
    return prompt[start:end + len(END)].encode("utf-8")


def observe(result_path):
    result_path = safe_path(result_path)
    result_raw = read(result_path)
    result = parse(result_raw)
    meta = result["agent_result"]["metadata"]
    root_id = meta["session_id"]
    require(isinstance(root_id, str) and root_id, "missing adapter root session_id")
    trace_root = safe_path(result_path.parent / "agent/penguin/traces")
    require(trace_root.is_dir(), "missing complete trace export")
    inventory, roots, sessions, edges = [], [], set(), {}
    root_sources = set()
    def walk_error(error):
        raise error

    for directory, dirs, files in os.walk(trace_root, followlinks=False, onerror=walk_error):
        for name in dirs + files:
            safe_path(Path(directory) / name)
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix != ".jsonl":
                continue
            raw = read(path)
            lines = raw.splitlines()
            require(bool(lines), "empty trace shard")
            own, children = [], set()
            for index, line in enumerate(lines):
                require(bool(line.strip()), "empty record in trace shard")
                message = parse(line)
                require(isinstance(message, dict), "invalid trace record")
                if message.get("type") == "session_meta":
                    payload = message.get("payload")
                    require(isinstance(payload, dict), "invalid session metadata")
                    sid = payload.get("session_id")
                    require(isinstance(sid, str) and sid, "missing trace session identity")
                    if message.get("origin"):
                        children.add(sid)
                    else:
                        own.append((index, payload))
                elif message.get("type") == "event_msg" and not message.get("origin"):
                    payload = message.get("payload")
                    if isinstance(payload, dict) and payload.get("type") == "subagent":
                        child = payload.get("session_id")
                        require(isinstance(child, str) and child, "invalid subagent pointer")
                        children.add(child)
                    elif isinstance(payload, dict) and payload.get("type") == "hook" and payload.get("hook") == "stop":
                        output = payload.get("output")
                        if isinstance(output, dict) and "session_id" in output:
                            child = output["session_id"]
                            require(isinstance(child, str) and child, "invalid hook child pointer")
                            children.add(child)
            require(len(own) == 1 and own[0][0] == 0,
                    "each shard must begin with exactly one own session_meta")
            payload = own[0][1]
            sid = payload["session_id"]
            sessions.add(sid)
            edges.setdefault(sid, set()).update(children)
            relative = path.relative_to(trace_root).as_posix()
            inventory.append({"path": relative, "sha256": sha(raw), "bytes": len(raw), "session_id": sid})
            if sid == root_id:
                source = payload.get("source")
                require(source == "cli" or
                        (source == "benchmark" and meta.get("penguin_version") == LEGACY_BENCHMARK_VERSION),
                        "Target root is not a CLI session or a supported legacy benchmark session")
                root_sources.add(source)
                require(len(root_sources) == 1, "Target root source changes across trace shards")
                require(payload.get("provider") == meta.get("provider") and
                        payload.get("model_id") == meta.get("model_id"), "trace/adapter model drift")
                roots.append({"path": relative, "session_id": sid,
                              "provider": payload["provider"], "model_id": payload["model_id"],
                              "container_agent_state": payload.get("agent_state"),
                              "container_workspace": payload.get("workspace"),
                              "injected_block_sha256": sha(block(payload.get("system_prompt")))})
    require(bool(roots), "adapter root session has no trace shards")
    visited, visiting = set(), set()

    def visit(sid):
        require(sid in sessions, "referenced child has no exported own trace")
        require(sid not in visiting, "cyclic child session pointers")
        if sid in visited:
            return
        visiting.add(sid)
        for child in edges[sid]:
            visit(child)
        visiting.remove(sid)
        visited.add(sid)

    visit(root_id)
    require(visited == sessions, "unrelated session in Target trace export")
    # Completeness against the live source inventory remains a separate audit;
    # absence from this directory alone cannot prove absence before export.
    return {"schema_version": 1, "trial_id": result["id"], "trial_dir": result_path.parent.name,
            "result_sha256": sha(result_raw), "root_session_id": root_id,
            "trace_files": sorted(inventory, key=lambda row: row["path"]),
            "root_shards": sorted(roots, key=lambda row: row["path"]),
            "child_sessions": {sid: sorted(children) for sid, children in sorted(edges.items())},
            "scope": "exported_trace_bytes_only"}


def check_binding(observation, binding, request):
    for shard in observation["root_shards"]:
        for key in ("container_agent_state", "container_workspace", "injected_block_sha256"):
            require(shard[key] == binding[key], "root trace binding mismatch: " + key)
        for key in ("provider", "model_id"):
            require(shard[key] == request[key], "root trace runtime mismatch: " + key)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(observe(args.result), ensure_ascii=False, sort_keys=True, indent=2))
    except (OSError, ValueError, TypeError, KeyError):
        # Never include raw records, prompts, paths from tools or secrets in errors.
        print("invalid runtime evidence; check trace inventory and metadata privately", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
