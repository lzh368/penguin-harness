#!/usr/bin/env python3
"""Single-operation Evo-Harness tools. No model calls or experiment scheduler.

Invoke with python3 -B evo.py REQUEST.json. Requests/results are JSON. Private
contexts and journals stay outside the exported, data-only skill directory.
"""
import sys
sys.dont_write_bytecode = True

import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

VERSION = 1
IDENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def ident(value):
    require(isinstance(value, str) and IDENT.fullmatch(value), "invalid identifier")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode()


def read(path):
    return json.loads(Path(path).read_text())


def write_once(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".evo-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def regular(path):
    path = Path(path)
    require(not path.is_symlink() and path.is_file(), f"not a regular file: {path}")
    return path


def filemap(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), "invalid directory")
    result = {}
    for path in sorted(root.rglob("*")):
        require(not path.is_symlink(), "symlinks are forbidden")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
        else:
            require(path.is_dir(), "non-regular entry")
    return result


def finite(value, low=0, high=None):
    require(type(value) in (int, float) and math.isfinite(value) and value >= low
            and (high is None or value <= high), "invalid numeric value")
    return value


def nonempty(value, label):
    require(isinstance(value, str) and value.strip(), f"missing {label}")
    return value


def feedback(value, task, attempt, answer_sha):
    require(value["task_id"] == task and value["attempt_id"] == attempt,
            "feedback task/attempt mismatch")
    require(value["answer_sha256"] == answer_sha, "feedback belongs to another answer")
    require(value["status"] in ("ok", "error"), "invalid feedback status")
    if value["status"] == "error":
        require(value.get("score") is None and value.get("correct") is None,
                "failed judge cannot supply a score")
        return False
    finite(value["score"], high=1)
    require(type(value["correct"]) is bool, "correct must be a boolean")
    raw = json.loads(value["raw_response"])
    if value.get("source") == "penguin-evaluation":
        require(raw["protocol_version"] == 1 and raw["status"] == "ok", "invalid evaluator receipt")
        require(raw["case_id"] == task, "evaluation case mismatch")
        require(raw["score"] / 100 == value["score"] and (raw["score"] == 100) == value["correct"],
                "evaluation normalization mismatch")
        require(set(value["evaluation_identity"]) == {"agent_id", "expected_version", "provider", "model_id", "thinking_level", "run"},
                "incomplete evaluation identity")
        for key, expected in value["evaluation_identity"].items():
            require(raw[key] == expected, "evaluation identity mismatch")
        return not value["correct"]
    require(value["http_status"] == 200, "feedback did not succeed")
    require(raw["score"] == value["score"] and raw["correct"] == value["correct"],
            "normalized feedback differs from raw response")
    return not value["correct"] or value["score"] < 1


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def judge(req):
    """One authorized training attempt. Failure stays an error, never a zero."""
    task, attempt = ident(req["task_id"]), ident(req["attempt_id"])
    require(req["split"] == "train" and task in req["train_ids"], "train-only judge")
    url = urllib.parse.urlparse(req["url"])
    require(url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost", "::1")
            and url.path == "/api/judge" and not url.query and not url.fragment
            and not url.username and not url.password, "judge must be the local training API")
    answer_bytes = regular(req["answer"]).read_bytes()
    answer = json.loads(answer_bytes)
    require(isinstance(answer, dict), "answer must be an object")
    dest = Path(req["output"])
    with locked(str(dest) + ".lock"):
        if dest.exists():
            previous = read(dest)
            require(previous["task_id"] == task and previous["attempt_id"] == attempt
                    and previous["answer_sha256"] == digest(answer_bytes), "attempt identity conflict")
            # Even an interrupted HTTP call is never repeated silently.
            return previous
        record = {"task_id": task, "attempt_id": attempt, "answer_sha256": digest(answer_bytes),
                  "status": "error", "error": "judge call in flight or interrupted", "score": None,
                  "correct": None, "url": req["url"], "request": {"task_id": task, "answer": answer}}
        atomic(dest, encoded(record))
        request = urllib.request.Request(req["url"], data=encoded(record["request"]),
                                         headers={"Content-Type": "application/json"})
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=30) as response:
                raw = response.read().decode()
                record.update(raw_response=raw, http_status=response.status)
            parsed = json.loads(raw)
            finite(parsed["score"], high=1)
            require(type(parsed["correct"]) is bool, "missing boolean correctness")
            require(record["http_status"] == 200, "judge HTTP failure")
            record.update(status="ok", score=parsed["score"], correct=parsed["correct"])
            record.pop("error")
        except (OSError, ValueError, KeyError) as exc:
            record["error"] = str(exc)
        atomic(dest, encoded(record))
        return record


def trace_view(paths, expected, path_map=None):
    """Preserve complete action/result pairs, with raw sources and no head/tail cut."""
    messages, sources, metas = [], [], []
    for path in paths:
        raw = regular((path_map or {}).get(str(path), path)).read_bytes()
        sources.append({"path": str(path), "sha256": digest(raw), "bytes": len(raw)})
        for line_no, line in enumerate(raw.splitlines(), 1):
            row = json.loads(line)
            require(not row.get("origin"), "use original child trace, not forwarded duplicates")
            payload = row.get("payload", {})
            if row.get("type") == "session_meta":
                metas.append(payload)
            elif row.get("type") == "model_msg" and payload.get("type") in (
                    "text", "tool_call", "tool_call_output"):
                messages.append({"source": str(path), "line": line_no, "payload": payload})
    require(metas, "trace missing session_meta")
    for meta in metas:
        for key in ("session_id", "provider", "model_id", "workspace", "agent_state", "source"):
            require(meta.get(key) == expected[key], f"trace binding mismatch: {key}")
    require(expected["source"] in ("cli", "benchmark", "subagent"), "unsupported trace source")
    if expected["source"] == "subagent":
        parent_path = (path_map or {}).get(expected["parent_trace"], expected["parent_trace"])
        parent = [json.loads(line) for line in regular(parent_path).read_text().splitlines()]
        require(any(row.get("type") == "event_msg" and row.get("payload", {}).get("type") == "subagent"
                    and row["payload"].get("session_id") == expected["session_id"] for row in parent),
                "child not referenced by parent trace")
    calls, results = {}, {}
    for message in messages:
        p = message["payload"]
        if p["type"] == "tool_call":
            key = p["tool_call_id"]
            require(key not in calls, "duplicate tool call")
            calls[key] = message
        elif p["type"] == "tool_call_output":
            key = p["tool_call_id"]
            require(key not in results, "duplicate tool output")
            results[key] = message
    require(set(calls) == set(results), "incomplete action/observation pairing")
    return {"binding": expected, "sources": sources, "truncated": False,
            "messages": messages, "pairs": [{"call": calls[k], "observation": results[k]} for k in calls]}


def contains_json(text, value):
    """Tool output may wrap stdout with an exit-code header; parse, do not substring-score."""
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char == "{":
            try:
                parsed, _ = decoder.raw_decode(text[i:])
                if parsed == value:
                    return True
            except ValueError:
                pass
    return False


def material(req):
    path = regular(req["path"])
    raw = path.read_bytes()
    content = raw.decode()
    start = req.get("offset", 0)
    require(type(start) is int and 0 <= start <= len(content), "invalid material offset")
    end = min(start + 4000, len(content))
    return {"material_path": str(path), "sha256": digest(raw), "offset": start,
            "end": end, "total": len(content), "text": content[start:end]}


def verify_reads(view, paths, path_map=None):
    receipts = []
    for path in paths:
        actual = regular((path_map or {}).get(str(path), path))
        raw = actual.read_bytes()
        content = raw.decode()
        # Complete fixed-size chunks must be present as actual tool outputs.
        for offset in range(0, max(len(content), 1), 4000):
            end = min(offset + 4000, len(content))
            expected = {"material_path": str(path), "sha256": digest(raw), "offset": offset,
                        "end": end, "total": len(content), "text": content[offset:end]}
            require(any(contains_json(p["observation"]["payload"]["output"], expected)
                        for p in view["pairs"]), f"material not fully read: {path} offset {offset}")
        receipts.append({"path": str(path), "sha256": digest(raw)})
    return receipts


def audit_role(req):
    view = trace_view(req["traces"], req["binding"], req.get("path_map"))
    reads = verify_reads(view, req["materials"], req.get("path_map"))
    outputs = []
    for path in req.get("outputs", []):
        receipt = {"file_path": path, "sha256": digest(regular(req.get("path_map", {}).get(path, path)).read_bytes())}
        require(any(contains_json(p["observation"]["payload"]["output"], receipt) for p in view["pairs"]),
                "role output not sealed in native trace")
        outputs.append(receipt)
    return {"binding": req["binding"], "sources": view["sources"], "materials": reads,
            "outputs": outputs, "complete": True}


def verify_workspace_inputs(view, paths, workspace, records=None):
    root = Path(workspace).resolve()
    for name in paths:
        path = Path(name) if records is not None else regular(name)
        require(path.resolve().is_relative_to(root), "workspace input escapes public statement workspace")
        lines = (records[name] if records is not None else path.read_text()).splitlines()
        observed = {}
        for pair in view["pairs"]:
            call = pair["call"]["payload"]
            if call["name"] != "read_file":
                continue
            args = json.loads(call["arguments"])
            target = Path(args.get("file_path", ""))
            if not target.is_absolute():
                target = root / target
            if target.resolve() != path.resolve():
                continue
            for line in pair["observation"]["payload"]["output"].splitlines():
                match = re.match(r"^\s*(\d+)\t(.*)$", line)
                if match:
                    observed[int(match[1])] = match[2]
        require(all(observed.get(i) == line for i, line in enumerate(lines, 1)),
                "workspace input not fully read through native read_file")


def seal_training(req):
    root = Path(req["private_learning"]).resolve()
    def rel(path):
        p = regular(path).resolve()
        require(p.is_relative_to(root), "private evidence must stay under private_learning")
        return p.relative_to(root).as_posix()
    contexts = [read(p) for p in req["contexts"]]
    validated = validate_contexts(contexts, req["train_ids"])
    validate_candidates(read(req["candidates"]), validated)
    roles, trace_paths, reflected = [], set(), []
    sessions = {c["trace"]["binding"]["session_id"] for c in contexts}
    for role in req["roles"]:
        audit_role(role)
        require(role["role"] in ("reflector", "evolver"), "invalid learning role")
        require(role["role_text_path"] in role["materials"], "role text missing")
        require(len(role["outputs"]) == 1, "one role output required")
        require(role["binding"]["session_id"] not in sessions, "learning roles require fresh sessions")
        sessions.add(role["binding"]["session_id"])
        if role["role"] == "reflector":
            context_path = next(p for p, c in zip(req["contexts"], contexts) if c["task_id"] == role["task_id"])
            require(context_path in role["materials"], "Reflector did not read assigned context")
            proposals = read(role["outputs"][0])
            require(all(c["task_id"] == role["task_id"] for c in proposals), "Reflector output crosses task boundary")
            reflected.extend(proposals)
        else:
            require(role["outputs"][0] == req["decisions"], "Evolver output mismatch")
            require(set(req["contexts"] + [req["candidates"], req["library"]]) <= set(role["materials"]),
                    "Evolver did not read complete batch evidence and library")
        for path in role["traces"]:
            trace_paths.add(path)
        if role["binding"].get("parent_trace"):
            trace_paths.add(role["binding"]["parent_trace"])
        roles.append({**role, "material_files": {p: rel(p) for p in role["materials"]},
                      "output_files": {p: rel(p) for p in role["outputs"]}})
    for binding in [c["trace"]["binding"] for c in contexts] + [r["binding"] for r in roles]:
        require(all(binding[k] == req["runtime"][k] for k in ("provider", "model_id")), "frozen runtime mismatch")
    require(reflected == read(req["candidates"]), "candidate concatenation differs from Reflector outputs")
    require(sum(r["role"] == "evolver" for r in roles) == 1, "one Evolver required")
    require([r["task_id"] for r in roles if r["role"] == "reflector"] ==
            [c["task_id"] for c in contexts if c["negative"]], "Reflector coverage mismatch")
    members = {}
    for path, c in zip(req["contexts"], contexts):
        trace_paths.update(s["path"] for s in c["trace"]["sources"])
        if c["trace"]["binding"].get("parent_trace"):
            trace_paths.add(c["trace"]["binding"]["parent_trace"])
        members[c["task_id"]] = {"answer_sha256": c["answer_sha256"], "attempt_id": c["attempt_id"],
            "context_file": rel(path), "container_context_path": path, "context_sha256": digest(regular(path).read_bytes()),
            "feedback_sha256": digest(encoded(c["feedback"])), "solver_session_id": c["trace"]["binding"]["session_id"]}
    verify_compilation({**req, "path_map": {}})
    record = {"complete": True, "runtime": req["runtime"], "members": members, "roles": roles, "trace_paths": sorted(trace_paths),
              "candidates_file": rel(req["candidates"]), "candidates_sha256": digest(regular(req["candidates"]).read_bytes()),
              "container_candidates_path": req["candidates"], "container_library_path": req["library"],
              "library_file": rel(req["library"]), "library_sha256": digest(regular(req["library"]).read_bytes()),
              "limits": req["limits"], "max_entries": req["max_entries"], "max_bytes": req["max_bytes"],
              "decisions_file": rel(req["decisions"]), "decisions_sha256": digest(regular(req["decisions"]).read_bytes()),
              "skill_sha256": validate_package(req["skill"])["sha256"]}
    write_once(req["output"], encoded(record))
    return record


def capture(req):
    require(req["split"] == "train" and req["task_id"] in req["train_ids"], "learning is train only")
    task, attempt = ident(req["task_id"]), ident(req["attempt_id"])
    answer_bytes = regular(req["answer"]).read_bytes()
    if req.get("op") == "capture-evaluation":
        raw = regular(req["evaluation"]).read_text()
        result = json.loads(raw)
        require(result["status"] == "ok", "failed evaluation has no learning feedback")
        finite(result["score"], high=100)
        require(result["session_id"] == req["binding"]["session_id"], "evaluator/solver Session mismatch")
        fb = {"source": "penguin-evaluation", "task_id": task, "attempt_id": attempt,
              "answer_sha256": digest(answer_bytes), "status": "ok", "raw_response": raw,
              "score": result["score"] / 100, "correct": result["score"] == 100,
              "evaluation_identity": req["evaluation_identity"]}
    else:
        fb = read(req["feedback"])
    negative = feedback(fb, task, attempt, digest(answer_bytes))
    require(fb["status"] == "ok", "judge error blocks reflection and compilation")
    view = trace_view(req["traces"], req["binding"])
    if fb.get("source") == "penguin-evaluation":
        verify_workspace_inputs(view, req["inputs"], req["binding"]["workspace"])
    else:
        grant = {"evo_task": {"split": "train", "task_id": task, "attempt_id": attempt, "inputs": req["inputs"]}}
        require(any(m["payload"].get("role") == "user" and m["payload"].get("type") == "text"
                    and contains_json(m["payload"]["text"], grant) for m in view["messages"]),
                "input files not bound to the native task delegation")
        verify_reads(view, req["inputs"])
    # The real judge result must occur in the solver's tool observations.
    if fb.get("source") != "penguin-evaluation":
        require(any(contains_json(p["observation"]["payload"]["output"], fb) for p in view["pairs"]),
                "solver trace has no receipt of this attempt's judge feedback")
    inputs = []
    for source in req["inputs"]:
        raw = regular(source).read_bytes()
        inputs.append({"source": source, "sha256": digest(raw), "text": raw.decode()})
    record = {"schema": VERSION, "split": "train", "task_id": task, "attempt_id": attempt,
              "inputs": inputs, "answer": answer_bytes.decode() if fb.get("source") == "penguin-evaluation" else json.loads(answer_bytes),
              "answer_text": answer_bytes.decode(),
              "answer_sha256": digest(answer_bytes),
              "feedback": fb, "negative": negative, "trace": view}
    write_once(req["output"], encoded(record))
    return {"path": req["output"], "sha256": digest(encoded(record)), "negative": negative}


def validate_contexts(contexts, train_ids, path_map=None):
    require(len(train_ids) == len(set(train_ids)) and train_ids, "invalid training roster")
    require(len(contexts) == len(train_ids), "batch incomplete")
    seen, sessions = set(), set()
    for c in contexts:
        require(c["split"] == "train" and c["task_id"] in train_ids and c["task_id"] not in seen,
                "foreign, test or duplicate context")
        seen.add(c["task_id"])
        require(c["trace"]["binding"]["session_id"] not in sessions, "Solver tasks require fresh sessions")
        sessions.add(c["trace"]["binding"]["session_id"])
        require(c["feedback"]["status"] == "ok", "judge error blocks the batch")
        negative = feedback(c["feedback"], c["task_id"], c["attempt_id"], c["answer_sha256"])
        require(c["negative"] == negative, "negative feedback flag mismatch")
        require(c["trace"]["truncated"] is False, "incomplete context")
        expected_answer = c["answer_text"] if c["feedback"].get("source") == "penguin-evaluation" else json.loads(c["answer_text"])
        require(digest(c["answer_text"].encode()) == c["answer_sha256"]
                and expected_answer == c["answer"], "captured answer changed")
        for source in c["inputs"]:
            require(digest(source["text"].encode()) == source["sha256"], "captured input changed")
        for source in c["trace"]["sources"]:
            require(digest(regular((path_map or {}).get(source["path"], source["path"])).read_bytes()) == source["sha256"],
                    "raw solver trace changed after capture")
        actual = trace_view([s["path"] for s in c["trace"]["sources"]], c["trace"]["binding"], path_map)
        require(actual == c["trace"], "captured trace differs from raw evidence")
        if c["feedback"].get("source") == "penguin-evaluation":
            require(json.loads(c["feedback"]["raw_response"])["session_id"] == c["trace"]["binding"]["session_id"],
                    "evaluation session mismatch")
            verify_workspace_inputs(actual, [s["source"] for s in c["inputs"]], c["trace"]["binding"]["workspace"],
                                    {s["source"]: s["text"] for s in c["inputs"]})
        else:
            grant = {"evo_task": {"split": "train", "task_id": c["task_id"], "attempt_id": c["attempt_id"],
                                  "inputs": [s["source"] for s in c["inputs"]]}}
            require(any(m["payload"].get("role") == "user" and m["payload"].get("type") == "text"
                        and contains_json(m["payload"]["text"], grant) for m in actual["messages"]), "input grant changed")
            for source in c["inputs"]:
                content = source["text"]
                for offset in range(0, max(len(content), 1), 4000):
                    expected = {"material_path": source["source"], "sha256": source["sha256"], "offset": offset,
                                "end": min(offset + 4000, len(content)), "total": len(content), "text": content[offset:offset + 4000]}
                    require(any(contains_json(p["observation"]["payload"]["output"], expected) for p in actual["pairs"]),
                            "captured input absent from Solver observations")
            require(any(contains_json(p["observation"]["payload"]["output"], c["feedback"])
                        for p in actual["pairs"]), "feedback absent from trace")
    return {(c["task_id"], c["attempt_id"]): c for c in contexts}


def validate_candidates(candidates, contexts):
    result = {}
    for c in candidates:
        key = ident(c["id"])
        require(key not in result, "duplicate candidate")
        ctx = contexts[(c["task_id"], c["attempt_id"])]
        require(ctx["negative"], "successful execution is not a reflection source")
        for field in ("lesson", "trigger", "scope_hint"):
            nonempty(c[field], field)
        require(c["evidence"], "candidate needs evidence")
        for ref in c["evidence"]:
            require(ref["kind"] in ("input", "tool", "answer", "feedback"), "unknown evidence type")
            nonempty(ref["claim"], "evidence claim")
            require(ref["epistemic"] in ("observed", "inferred"), "label fact versus inference")
            if ref["kind"] == "tool":
                require(ref["tool_call_id"] in {p["call"]["payload"]["tool_call_id"]
                        for p in ctx["trace"]["pairs"]}, "evidence tool not in attempt")
            elif ref["kind"] == "input":
                require(ref["sha256"] in {i["sha256"] for i in ctx["inputs"]}, "input evidence mismatch")
        result[key] = c
    return result


def validate_entries(entries, limits):
    counts, seen = {}, set()
    for entry in entries:
        key = ident(entry["id"])
        require(key not in seen, "duplicate entry")
        seen.add(key)
        require(entry["level"] in ("general", "topic"), "invalid harness level")
        for field in ("trigger", "scope", "body"):
            nonempty(entry[field], field)
        topic = ident(entry["topic"]) if entry["level"] == "topic" else "general"
        bucket = (entry["level"], topic)
        counts[bucket] = counts.get(bucket, 0) + 1
        require(counts[bucket] <= limits[entry["level"]], "skill slot budget exceeded; merge or skip")


def compile_batch(req):
    contexts = validate_contexts([read(p) for p in req["contexts"]], req["train_ids"], req.get("path_map"))
    candidates = validate_candidates(read(req["candidates"]), contexts)
    current = read(req["library"])
    validate_entries(current, req["limits"])
    entries = {e["id"]: e for e in current}
    decisions = read(req["decisions"])
    covered, targets, layer_coverage = set(), set(), {"general": set(), "topic": set()}
    for edit in decisions:
        op = edit["op"]
        require(op in ("ADD", "MERGE", "REVISE", "SKIP"), "unknown evolver operation")
        nonempty(edit["reason"], "decision reason")
        ids = edit["candidate_ids"]
        require(ids and len(ids) == len(set(ids)) and set(ids) <= candidates.keys(), "unknown candidate")
        layer = edit.get("level", "all") if op == "SKIP" else edit["entry"]["level"]
        require(layer in ("general", "topic", "all"), "invalid compilation layer")
        for level in (("general", "topic") if layer == "all" else (layer,)):
            require(not layer_coverage[level].intersection(ids), "candidate decided twice in one layer")
            layer_coverage[level].update(ids)
        covered.update(ids)
        if op == "SKIP":
            continue
        entry = edit["entry"]
        target = ident(entry["id"])
        require(target not in targets, "combine edits to the same entry")
        targets.add(target)
        if op == "ADD":
            require(target not in entries, "ADD target already exists")
        else:
            require(target in entries, "MERGE/REVISE target does not exist")
        # The Evolver supplies the complete replacement; scripts do not invent lessons.
        entries[target] = {k: entry[k] for k in ("id", "level", "trigger", "scope", "body")}
        if entry["level"] == "topic":
            entries[target]["topic"] = entry["topic"]
    require(covered == candidates.keys(), "every candidate needs an explicit decision")
    result = sorted(entries.values(), key=lambda e: e["id"])
    validate_entries(result, req["limits"])
    write_once(req["output"], encoded(result))
    return {"entries": len(result), "candidates": len(candidates), "sha256": digest(encoded(result))}


def verify_compilation(req):
    """Rebuild the data-only package from audited role outputs, without any model call."""
    with tempfile.TemporaryDirectory(prefix="evo-rebuild-") as tmp:
        library = str(Path(tmp) / "compiled.json")
        compile_batch({**req, "output": library})
        expected = export_skill({**req, "library": library, "output": str(Path(tmp) / "skill")})
        require(expected == validate_package(req["skill"]), "exported Skill differs from audited compilation")
    return expected


READER = '''---
name: evo-harness-learned
description: Select relevant frozen Evo-Harness procedures for the current task.
version: 2026.10.21.1
---

Before acting, read index.json beside this file. Choose only entries whose trigger
and scope match the current task (zero is valid), in order of relevance. Execute
`python3 -B select.py --ids <comma-separated IDs>` from this Skill directory.
The selector enforces the frozen entry and UTF-8 byte budgets, returns the complete
selected guidance and its hashes. Read that output before solving. Never read other
entry bodies to bypass the budget. Keep the returned receipt in the task workspace.
The library is frozen: do not edit it, learn from test feedback, or fetch training
answers. Current task instructions and observed facts take precedence over a
procedure whose preconditions are not met. Empty guidance is normal cold start.
'''

SELECTOR = '''#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path

def select(root, ids):
    index = json.loads((root / "index.json").read_text())
    assert len(ids) == len(set(ids)), "duplicate selection"
    assert len(ids) <= index["max_entries"], "entry budget exceeded"
    available = {e["id"]: e for e in index["entries"]}
    parts, hashes = [], {}
    for key in ids:
        e = available[key]
        path = root / e["file"]
        assert not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == e["sha256"], "frozen entry changed"
        parts.append(raw.decode())
        hashes[key] = e["sha256"]
    text = "\\n\\n".join(parts)
    assert len(text.encode()) <= index["max_bytes"], "context budget exceeded; select fewer entries"
    return {"selected": ids, "entry_hashes": hashes, "text": text,
            "bytes": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest()}

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ids", required=True)
    args = p.parse_args()
    print(json.dumps(select(Path(__file__).resolve().parent,
                           args.ids.split(",") if args.ids else []), ensure_ascii=False))
'''


def export_skill(req):
    entries = read(req["library"])
    validate_entries(entries, req["limits"])
    require(type(req["max_entries"]) is int and 0 < req["max_entries"] <= 5, "invalid injection count")
    require(type(req["max_bytes"]) is int and req["max_bytes"] > 0, "invalid byte budget")
    dest = Path(req["output"])
    require(not dest.exists(), "export destination already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent, prefix=".skill-") as tmp:
        stage = Path(tmp) / "skill"
        stage.mkdir()
        index = {"schema": VERSION, "max_entries": req["max_entries"], "max_bytes": req["max_bytes"], "entries": []}
        for e in sorted(entries, key=lambda item: item["id"]):
            path = f"{e['level']}/{e['id']}.md"
            raw = (f"# {e['id']}\n\nTrigger: {e['trigger']}\nScope: {e['scope']}\n\n{e['body']}\n").encode()
            require(len(raw) <= req["max_bytes"], "entry cannot fit injection budget")
            write_once(stage / path, raw)
            index["entries"].append({"id": e["id"], "level": e["level"], "trigger": e["trigger"],
                                     "scope": e["scope"], "file": path, "sha256": digest(raw)})
        write_once(stage / "SKILL.md", READER.encode())
        write_once(stage / "select.py", SELECTOR.encode())
        write_once(stage / "index.json", encoded(index))
        os.rename(stage, dest)
    return {"files": filemap(dest), "sha256": digest(encoded(filemap(dest))), "empty": not entries}


def validate_package(root):
    root = Path(root)
    files = filemap(root)
    require((root / "SKILL.md").read_text() == READER, "learned reader changed")
    require((root / "select.py").read_text() == SELECTOR, "learned executable changed")
    index = read(root / "index.json")
    require(index["schema"] == VERSION and type(index["max_entries"]) is int
            and 0 < index["max_entries"] <= 5 and type(index["max_bytes"]) is int
            and index["max_bytes"] > 0, "invalid selection budgets")
    allowed, ids = {"SKILL.md", "select.py", "index.json"}, set()
    for e in index["entries"]:
        ident(e["id"])
        require(e["id"] not in ids, "duplicate package entry")
        ids.add(e["id"])
        require(e["level"] in ("general", "topic"), "invalid level")
        require(e["file"] == f"{e['level']}/{e['id']}.md", "invalid entry path")
        require(files.get(e["file"]) == e["sha256"], "entry fingerprint mismatch")
        require((root / e["file"]).stat().st_size <= index["max_bytes"], "entry exceeds byte budget")
        allowed.add(e["file"])
    require(set(files) == allowed, "unlisted support file or raw evidence in learned package")
    return {"files": files, "sha256": digest(encoded(files)), "empty": not ids}


def journal(req):
    """Controller-authored dispatch receipts. Never chooses or runs another stage."""
    path = Path(req["journal"])
    with locked(str(path) + ".lock"):
        if req["action"] == "init":
            require(not path.exists(), "journal already exists")
            finite(req["budget_usd"], low=0.01)
            state = {"schema": VERSION, "config_sha256": req["config_sha256"],
                     "controller_session_id": ident(req["controller_session_id"]),
                     "budget_usd": req["budget_usd"], "known_cost_usd": 0, "operations": {}}
        else:
            state = read(path)
            require(req["config_sha256"] == state["config_sha256"], "frozen configuration changed")
            if req["action"] == "reserve":
                key = ident(req["operation_id"])
                require(key not in state["operations"], "operation already reserved; reconcile, never redispatch")
                slot = nonempty(req.get("slot", key), "semantic operation slot")
                require(not any(o["slot"] == slot or o["request_sha256"] == req["request_sha256"]
                                for o in state["operations"].values()), "semantic operation already reserved")
                reserve = finite(req["reserve_usd"], low=0.01)
                committed = sum(o["reserve_usd"] for o in state["operations"].values() if o["status"] != "complete")
                require(state["known_cost_usd"] + committed + reserve <= state["budget_usd"], "budget exhausted")
                state["operations"][key] = {"status": "reserved", "reserve_usd": reserve,
                    "request_sha256": req["request_sha256"], "controller_trace": req["controller_trace"],
                    "role": req["role"], "slot": slot, "external_id": None}
            elif req["action"] == "bind":
                op = state["operations"][req["operation_id"]]
                require(op["status"] == "reserved", "operation already bound")
                op.update(status="running", external_id=nonempty(req["external_id"], "external id"))
            elif req["action"] == "settle":
                op = state["operations"][req["operation_id"]]
                require(op["status"] in ("reserved", "running"), "operation already settled")
                require(req["terminated"] is True, "must confirm real process/session termination")
                cost = req.get("cost_usd")
                if cost is None:
                    op.update(status="cost_pending", evidence=req["evidence"])
                else:
                    finite(cost)
                    op.update(status="complete", cost_usd=cost, evidence=req["evidence"])
                    state["known_cost_usd"] += cost
            elif req["action"] == "reconcile_cost":
                op = state["operations"][req["operation_id"]]
                require(op["status"] == "cost_pending", "no pending cost")
                cost = finite(req["cost_usd"])
                op.update(status="complete", cost_usd=cost, evidence=req["evidence"])
                state["known_cost_usd"] += cost
            elif req["action"] != "status":
                raise ValueError("unknown journal action")
        atomic(path, encoded(state))
        return state


def execute(req):
    actions = {"judge": judge, "capture": capture, "capture-evaluation": capture, "compile": compile_batch,
               "read-material": material, "audit-role": audit_role,
               "seal-training": seal_training,
               "file-digest": lambda r: {"file_path": r["path"], "sha256": digest(regular(r["path"]).read_bytes())},
               "export": export_skill, "journal": journal,
               "verify-package": lambda r: validate_package(r["path"]),
               "fingerprint": lambda r: {"files": filemap(r["path"]),
                    "sha256": digest(encoded(filemap(r["path"])))} }
    return actions[req["op"]](req)


if __name__ == "__main__":
    try:
        require(len(sys.argv) == 2, "usage: python3 -B evo.py REQUEST.json")
        print(json.dumps(execute(read(sys.argv[1])), ensure_ascii=False, allow_nan=False))
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        sys.exit(2)
