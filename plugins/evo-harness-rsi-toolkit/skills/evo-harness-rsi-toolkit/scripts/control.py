#!/usr/bin/env python3
"""Native Evo hook checks and single requested preparation operations.

This module never calls a model, spawns a role, runs Harbor, chooses a next
phase, retries an attempt, or authors a lesson. Penguin makes those decisions.
The guard is a protocol boundary for cooperative agents, not an OS sandbox.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

import evo

SKILL = Path(__file__).resolve().parents[1]
CONTROL = Path(__file__).resolve()
TOOL = CONTROL.with_name("evo.py")
MARKER = "EVO_CONTROL.json"
FORMAT_PROMPT = ("Resend exactly one plain protocol YAML document from your existing result in this same Session. "
                 "Remove the Markdown opening/closing code fences; output no fences, heading or surrounding prose. "
                 "Keep every original protocol field and value unchanged, including null cost and the bound Session id. "
                 "Do not call tools, relaunch, rescore, change values, or add commentary. "
                 "This is a formatting-only repair under the installed shared evaluation.md contract.")
WORKER_YAML_PARSER = r"""
const fs = require('node:fs');
const YAML = require('node:module').createRequire(process.argv[1])('yaml');
try {
  const docs = YAML.parseAllDocuments(fs.readFileSync(0, 'utf8'), { uniqueKeys: true, prettyErrors: false });
  if (docs.length !== 1 || docs[0].errors.length || docs[0].warnings.length) throw Error();
  const value = docs[0].toJS({ maxAliasCount: 0 });
  if (!value || Array.isArray(value) || typeof value !== 'object') throw Error();
  function finite(v) {
    if (typeof v === 'number' && !Number.isFinite(v)) throw Error();
    if (v && typeof v === 'object') Object.values(v).forEach(finite);
  }
  finite(value); process.stdout.write(JSON.stringify(value));
} catch (_) { process.exitCode = 2; }
"""
ROOT_OPS = {"read-material", "file-digest", "capture", "compile", "export", "fingerprint",
            "journal", "audit-role", "verify-package", "seal-training"}


def check(value, message):
    evo.require(value, message)


class WorkerPending(ValueError):
    """A specific native reply boundary has not completed; never a repair grant."""


class WorkerFormatError(ValueError):
    """Only document syntax can qualify for the one existing-value repair."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(path):
    return evo.digest(evo.regular(path).read_bytes())


def inside(path, root):
    p, root = Path(path), Path(root).resolve()
    check(p.is_absolute() and p.resolve().is_relative_to(root), "path outside allowed directory")
    check(not p.is_symlink(), "symlink path forbidden")
    return p


def trace_context(trace, state=None):
    rows = []
    for line in evo.regular(trace).read_bytes().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # a live final line may still be being written
    metas = [r["payload"] for r in rows if r.get("type") == "session_meta" and not r.get("origin")]
    check(len(metas) == 1, "one original native Session metadata record required")
    meta = metas[0]
    check(isinstance(meta["source"], str) and meta["source"], "missing native Session source")
    native_state = Path(meta["agent_state"]).resolve()
    if state is not None:
        check(native_state == Path(state).resolve(), "native State differs from installed hook State")
    scratch = native_state.parent / "scratchpad" / evo.ident(meta["session_id"])
    return meta, scratch, rows


def load_mode(trace, state=None):
    meta, scratch, rows = trace_context(trace, state)
    marker = scratch / MARKER
    mode = evo.read(marker) if marker.exists() else None
    if mode is not None:
        check(mode["session_id"] == meta["session_id"], "guard Session identity changed")
    return meta, scratch, rows, mode


def role_text(role):
    title = {"solver": "Solver (group training leaf)", "reflector": "Reflector", "evolver": "Evolver"}[role]
    text = (SKILL / "references/roles.md").read_text()
    common = text.split("## Solver", 1)[0]
    section = text.split("## " + title + "\n", 1)[1].split("\n## ", 1)[0]
    return common + "## " + title + "\n" + section


def training_profile(state):
    path = Path(state) / "skills/evo-harness-rsi-toolkit/training.json"
    t = evo.read(path)
    check(t.get("guard_version") == 2, "guarded training profile v2 required")
    check(t["attempt_id"] == "attempt-1" and t["train_ids"], "invalid frozen attempt")
    for name in ("private_learning", "answer_dir", "feedback_dir", "workspace"):
        check(Path(t[name]).is_absolute(), "training paths must be absolute")
    inside(t["feedback_dir"], t["private_learning"])
    evo.finite(t["training_budget_usd"], low=0.01)
    return path, t


def training_bootstrap(meta, scratch, state, trace_path=None):
    path, t = training_profile(state)
    private = Path(t["private_learning"])
    private.mkdir(parents=True, exist_ok=True)
    for sub in ("requests", "contexts", "roles", "dispatch", "judge-requests"):
        (private / sub).mkdir(exist_ok=True)
    files = ["references/group-training.md", "references/roles.md", "references/tools.md"]
    material = {name: (SKILL / name).read_text() for name in files}
    mode = {"schema": 2, "mode": "training", "session_id": meta["session_id"],
            "training_path": str(path), "training_sha256": sha(path), "private_learning": str(private)}
    marker = scratch / MARKER
    if marker.exists():
        check(evo.read(marker) == mode, "training guard identity changed on resume")
    else:
        evo.write_once(marker, evo.encoded(mode))
    clock = private / "coordinator-clock.json"
    if not clock.exists():
        evo.write_once(clock, evo.encoded({"started_unix": time.time(), "session_id": meta["session_id"]}))
    for role in ("solver", "reflector", "evolver"):
        dest = private / "roles" / (role + ".md")
        expected = role_text(role).encode()
        if dest.exists():
            check(dest.read_bytes() == expected, "frozen role text changed")
        else:
            evo.write_once(dest, expected)
    library = private / "prebatch-library.json"
    slot = Path(state) / "skills/evo-harness-learned"
    package = evo.validate_package(slot)
    check(not evo.read(slot / "index.json")["entries"], "this continuous v2 profile requires a cold-start library")
    if library.exists():
        check(evo.read(library) == [], "pre-batch library changed")
    else:
        evo.write_once(library, evo.encoded([]))
    header = (
        "EVO-HARNESS CONTINUOUS TRAINING DELEGATION (native harness contract)\n"
        "You are the training coordinator, not a Solver. The benchmark's generic permission to choose "
        "attempts is resolved by this frozen method: one attempt per member, in listed order. "
        "Do not solve tasks yourself, directly call business/judge HTTP, or write submission answers. "
        "Use native role children with the complete fixed prompts. No test inputs are available here.\n"
        f"State: {state}\nFrozen training profile: {path}\nPrivate evidence: {private}\n"
        f"Exact current trace_path: {trace_path}\nRequest directory: {private / 'requests'}\nSession scratchpad: {scratch}\n"
        f"Control helper: python3 -B {CONTROL} REQUEST.json\nMethod helper: python3 -B {TOOL} REQUEST.json\n"
        "Every control request includes trace_path (your current original native trace). "
        "Use role-request to prepare each requested native call; then invoke run_subagent with its exact short returned call, "
        "including prompt_file and prompt_sha256. The native tool reads the complete immutable prompt for the child; "
        "do not copy its text into a prompt argument or reconstruct the role instructions. "
        "After each Solver's native completed notification, use prepare-capture with only task_id and your trace_path; "
        "execute its returned exact command to capture the original evidence. Never hand-copy inputs or bindings. "
        "Only helpers run in this root's shell. Root write_file may write private request/evidence JSON, "
        "not submission, State, traces, hooks or role outputs. Empty input_subagent polls are allowed; "
        "new answer attempts and free-form steering are not. On missing evidence stop as method_failed. "
        "Zero candidates still requires one Evolver, compilation, export and seal.\n"
    )
    body = "\n\n".join(f"FULL METHOD MATERIAL {name}\n{text}" for name, text in material.items())
    return header + "\n" + json.dumps(t, ensure_ascii=False) + "\n\n" + body


def original_session_rows(meta):
    """Read original segments, including pre-compaction native completion notices."""
    root = Path(meta["agent_state"]).parent / "traces"
    collected = []
    for path in sorted(root.rglob("*.jsonl")):
        try:
            identity, _, rows = trace_context(path)
        except (ValueError, KeyError, OSError):
            continue
        if identity["session_id"] == meta["session_id"]:
            check(identity["agent_state"] == meta["agent_state"], "native Session State changed")
            collected.extend(row for row in rows if not row.get("origin"))
    return collected


def worker_yaml(text, mode, allow_fence=False):
    """Parse one worker document using the frozen native server YAML package."""
    check(isinstance(text, str) and text.strip() and len(text.encode()) <= 1024 * 1024,
          "worker reply must be one bounded plain YAML document")
    if allow_fence:
        # This is only the immutable pre-repair value source. Never strip the
        # repaired/final reply, choose among multiple documents, or alter values.
        fenced = re.fullmatch(r"\s*```(?:yaml|yml|text|plaintext)?[ \t]*\r?\n(.*?)\r?\n```\s*", text, re.S | re.I)
        if fenced:
            text = fenced[1]
    if re.search(r"(?m)^[ \t]*(?:```|~~~)", text):
        raise WorkerFormatError("worker reply must be plain YAML without Markdown fences")
    check(sha(mode["config"]) == mode["config_sha256"], "frozen experiment config changed")
    deployment = evo.read(mode["config"])["deployment"]
    package = Path(deployment["penguin_checkout"]) / "packages/server/package.json"
    try:
        parsed = subprocess.run([deployment["node"], "--input-type=commonjs", "-e", WORKER_YAML_PARSER,
                                 str(package)], input=text.encode(), capture_output=True, timeout=10,
                                cwd=deployment["penguin_checkout"], env={"PATH": os.defpath}, check=False)
        if parsed.returncode == 2:
            raise WorkerFormatError("worker reply must be exactly one valid plain YAML mapping")
        check(parsed.returncode == 0, "native worker YAML parser failed")
        return json.loads(parsed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise ValueError("native worker YAML parser failed") from None


def worker_identity(ticket):
    text = evo.native_prompt(ticket["call"])
    def field(name):
        found = re.findall(r"^" + name + r":\s*(\S+)\s*$", text, re.M)
        check(len(found) == 1, "registered worker task needs one " + name)
        return found[0]
    expected = {key: field(key) for key in ("case_id", "provider", "model_id", "thinking_level")}
    expected["agent_id"] = field("test_agent_id")
    for key in ("run", "expected_version"):
        value = field(key)
        check(re.fullmatch(r"[1-9][0-9]*", value), "invalid registered worker " + key)
        expected[key] = int(value)
    return expected


def validate_worker_record(value, ticket):
    check(isinstance(value, dict), "worker protocol must be a YAML mapping")
    identity = worker_identity(ticket)
    base = {"protocol_version", "status", *identity}
    keys = base | ({"score", "cost", "duration_ms", "session_id"} if value.get("status") == "ok" else {"failure_code"})
    extension = {"training_protocol_version", "operation_id", "trial_path", "submission_path",
                 "private_learning_path", "member_ids", "skill_sha256", "complete"}
    check(set(value) == keys or (ticket["phase"] == "training" and set(value) == keys | extension),
          "worker protocol fields differ from shared evaluation contract")
    check(type(value["protocol_version"]) is int and value["protocol_version"] == 1,
          "unknown worker protocol")
    for key, expected in identity.items():
        check(type(value[key]) is type(expected) and value[key] == expected,
              "worker runtime/State/task mismatch: " + key)
    if value["status"] == "ok":
        evo.finite(value["score"], high=100)
        if value["cost"] is not None:
            evo.finite(value["cost"])
        check(type(value["duration_ms"]) is int and value["duration_ms"] >= 0, "invalid worker duration")
        check(isinstance(value["session_id"], str) and value["session_id"], "missing worker trial binding")
    else:
        check(value["status"] == "failed" and value["failure_code"] in
              ("invalid_request", "benchmark_invalid", "version_changed", "evaluation_failed"),
              "invalid worker failure result")
    return value


def same_record(left, right):
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_record(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same_record(a, b) for a, b in zip(left, right))
    return left == right


def worker_text(rows, repaired=False, last_request=False):
    """Require a fresh completed native request, never reuse an earlier reply."""
    texts, active, finished = [], False, False
    for row in rows:
        p = row.get("payload", {})
        if p.get("type") == "request_begin":
            active, finished = True, False
            if last_request:
                texts = []
        elif p.get("type") == "request_end":
            check(active, "worker request end without its original begin")
            active, finished = False, p.get("status") == "completed"
        elif repaired and p.get("type") == "tool_call":
            check(False, "formatting repair issued tools")
        elif p.get("type") == "text" and p.get("role") == "assistant" and p.get("text", "").strip():
            check(active, "worker reply has no fresh native request")
            texts.append(p["text"])
    if active:
        raise WorkerPending("worker reply awaits its completed native request")
    check(texts and finished, "worker reply lacks a completed native request")
    return "\n".join(texts)


def native_worker(meta, scratch, mode, ticket):
    """Bind one claimed original worker call, parent pointer, notice and child."""
    rows = original_session_rows(meta)
    claim_path = ticket_path(scratch, mode, ticket["call"]).parent / (evo.digest(ticket["slot"].encode()) + ".claimed.json")
    claim = evo.read(evo.regular(claim_path))
    check(claim.get("request_sha256") == evo.digest(canonical(ticket["call"]).encode()), "worker claim changed")
    calls = [r["payload"] for r in rows if r.get("type") == "model_msg"
             and r.get("payload", {}).get("type") == "tool_call"
             and r["payload"].get("tool_call_id") == claim["tool_call_id"]]
    check(len(calls) == 1 and calls[0].get("name") == "run_subagent"
          and json.loads(calls[0]["arguments"]) == ticket["call"], "original worker call differs from registered task")
    outputs = [r["payload"]["output"] for r in rows if r.get("type") == "model_msg"
               and r.get("payload", {}).get("type") == "tool_call_output"
               and r["payload"].get("tool_call_id") == claim["tool_call_id"]]
    check(len(outputs) == 1, "original worker launch observation missing")
    handles = re.findall(r"\bsubagent_id ([A-Za-z_0-9-]+)(?:[;\]\s])", outputs[0])
    check(len(handles) == 1, "original native worker handle missing")
    handle = handles[0]
    status = None
    for row in rows:
        p = row.get("payload", {})
        if p.get("type") == "text" and p.get("sender") == "harness":
            block = re.match(r"(?:\[background_task_done\]\n(.*?)\n\[/background_task_done\]|<background_task_done>\n(.*?)\n</background_task_done>)", p.get("text", ""), re.S)
            if block:
                values = dict(re.findall(r"^(kind|id|status):\s*(\S+)\s*$", block[1] or block[2], re.M))
                if values.get("kind") == "subagent" and values.get("id") == handle:
                    status = values.get("status")
    check(status not in ("failed", "stopped"), "original native evaluator terminated: " + str(status))
    if status is None:
        raise WorkerPending("await the original evaluator's native completed notification")
    check(status == "completed", "invalid original evaluator completion status")
    referenced = {r["payload"]["session_id"] for r in rows if r.get("type") == "event_msg"
                  and r.get("payload", {}).get("type") == "subagent"}
    children = {}
    for trace in sorted((Path(meta["agent_state"]).parent / "traces").rglob("*.jsonl")):
        identity, _, child_rows = trace_context(trace)
        if identity["session_id"] in referenced and identity.get("source") == "subagent":
            children.setdefault(identity["session_id"], []).extend(r for r in child_rows if not r.get("origin"))
    prompt = evo.native_prompt(ticket["call"])
    found = []
    for session_id, child in children.items():
        users = [r["payload"] for r in child if r.get("type") == "model_msg" and r.get("payload", {}).get("type") == "text"
                 and r["payload"].get("role") == "user" and r["payload"].get("sender") == "parent_agent"]
        if users and users[0]["text"] == prompt:
            metas = [r["payload"] for r in child if r.get("type") == "session_meta"]
            check(metas and all(m["agent_state"] == meta["agent_state"] and
                  all(m[k] == ticket["call"][k] for k in ("provider", "model_id")) for m in metas),
                  "worker native runtime/State changed")
            found.append((session_id, child))
    check(len(found) == 1, "worker needs exactly one original native child Session")
    return {"session_id": found[0][0], "rows": found[0][1], "handle": handle, "parent_rows": rows}


def format_request(req, meta, scratch, mode):
    check(mode["mode"] == "controller" and "config" in mode, "format repair is controller-only")
    child_id = evo.ident(req["subagent_id"])
    rows = original_session_rows(meta)
    calls = {r["payload"]["tool_call_id"]: r["payload"] for r in rows
             if r.get("type") == "model_msg" and r.get("payload", {}).get("type") == "tool_call"
             and r["payload"].get("name") == "run_subagent"}
    launches = []
    for row in rows:
        payload = row.get("payload", {})
        if row.get("type") != "model_msg" or payload.get("type") != "tool_call_output":
            continue
        call_id = payload.get("tool_call_id")
        if call_id not in calls or not re.search(r"subagent_id " + re.escape(child_id) + r"(?:[;\]\s])", payload.get("output", "")):
            continue
        args = json.loads(calls[call_id]["arguments"])
        ticket = evo.read(ticket_path(scratch, mode, args))
        claim = ticket_path(scratch, mode, args).parent / (evo.digest(ticket["slot"].encode()) + ".claimed.json")
        check(evo.read(claim)["tool_call_id"] == call_id, "format repair requires the registered native evaluator")
        launches.append(ticket)
    check(len(launches) == 1, "format repair needs exactly one registered evaluator launch")
    completed = False
    for row in rows:
        p = row.get("payload", {})
        if p.get("type") == "text" and p.get("sender") == "harness":
            text = p.get("text", "")
            block = re.match(r"(?:\[background_task_done\]\n(.*?)\n\[/background_task_done\]|<background_task_done>\n(.*?)\n</background_task_done>)", text, re.S)
            if block:
                fields = dict(re.findall(r"^(kind|id|status):\s*(\S+)\s*$", block[1] or block[2], re.M))
                if fields.get("kind") == "subagent" and fields.get("id") == child_id:
                    completed = fields.get("status") == "completed"
    check(completed, "await the original evaluator's native completed notification before formatting repair")
    referenced = {r["payload"]["session_id"] for r in rows if r.get("type") == "event_msg"
                  and r.get("payload", {}).get("type") == "subagent"}
    sessions = set()
    # Registry handles are subagent-<tail>, with a random fallback on collision.
    # Bind the original child by its native parent pointer and exact initial task,
    # never by guessing that its Session id ends with the tool handle.
    for trace in (Path(meta["agent_state"]).parent / "traces").rglob("*.jsonl"):
        try:
            identity, _, child_rows = trace_context(trace)
        except (ValueError, KeyError, OSError):
            continue
        if identity["session_id"] not in referenced or identity.get("source") != "subagent":
            continue
        if any(not r.get("origin") and r.get("payload", {}).get("type") == "text"
               and r["payload"].get("role") == "user" and r["payload"].get("sender") == "parent_agent"
               and r["payload"].get("text") == evo.native_prompt(launches[0]["call"]) for r in child_rows):
            sessions.add(identity["session_id"])
    check(len(sessions) == 1, "format repair must bind the original native child Session")
    source = native_worker(meta, scratch, mode, launches[0])
    check(source["handle"] == child_id and source["session_id"] in sessions, "format repair worker changed")
    check(sum(row.get("payload", {}).get("type") == "text" and row["payload"].get("role") == "user"
              and row["payload"].get("sender") == "parent_agent" for row in source["rows"]) == 1,
          "worker has unregistered follow-up input")
    original_text = worker_text(source["rows"])
    original_result = validate_worker_record(worker_yaml(worker_text(source["rows"], last_request=True), mode, allow_fence=True), launches[0])
    call = {"description": "Resend the existing evaluation YAML", "subagent_id": child_id, "prompt": FORMAT_PROMPT}
    repair = {"schema": 2, "subagent_id": child_id, "session_id": next(iter(sessions)), "slot": launches[0]["slot"], "call": call,
              "worker_ticket": str(ticket_path(scratch, mode, launches[0]["call"])),
              "original_text_sha256": evo.digest(original_text.encode()), "original_result": original_result}
    path = scratch / "evo-guard/format" / (child_id + ".json")
    check(not path.with_suffix(".claimed.json").exists(), "only one formatting repair per evaluator; preserve an unresolved protocol failure")
    if path.exists():
        check(evo.read(path) == repair, "format repair identity changed")
    else:
        evo.write_once(path, evo.encoded(repair))
    return {"call": call, "ticket": str(path), "automatic_dispatch": False}


def authorize_input(args, meta, scratch, mode, call_id):
    if not args.get("prompt", "").strip():
        return
    check(mode["mode"] == "controller", "training role steering/retries are forbidden; preserve failure and stop")
    child_id = evo.ident(args["subagent_id"])
    path = scratch / "evo-guard/format" / (child_id + ".json")
    repair = evo.read(path)
    check(args == repair["call"] and args["prompt"] == FORMAT_PROMPT, "only the registered formatting-only evaluation repair is allowed")
    claim = path.with_suffix(".claimed.json")
    if claim.exists():
        check(evo.read(claim)["tool_call_id"] == call_id, "formatting repair already dispatched")
    else:
        evo.write_once(claim, evo.encoded({"tool_call_id": call_id}))


def controller_trace(mode):
    scratch = Path(mode["completion_request"]).parent.parent
    root = scratch.parent.parent / "traces"
    metas = []
    for path in sorted(root.rglob("*.jsonl")):
        meta, _, _ = trace_context(path)
        if meta["session_id"] == mode["session_id"]:
            metas.append(meta)
    fields = ("session_id", "source", "agent_state", "workspace", "provider", "model_id")
    check(metas and all(all(meta[k] == metas[0][k] for k in fields) for meta in metas), "controller original trace missing/changed")
    return metas[0], scratch


def worker_result(meta, scratch, mode, ticket):
    source = native_worker(meta, scratch, mode, ticket)
    path = scratch / "evo-guard/format" / (source["handle"] + ".json")
    child = source["rows"]
    users = [(i, r["payload"]["text"]) for i, r in enumerate(child) if r.get("type") == "model_msg"
             and r.get("payload", {}).get("type") == "text" and r["payload"].get("role") == "user"
             and r["payload"].get("sender") == "parent_agent"]
    if not path.with_suffix(".claimed.json").exists():
        check(len(users) == 1, "worker has unregistered follow-up input")
        return validate_worker_record(worker_yaml(worker_text(child), mode), ticket), source
    repair, claim = evo.read(evo.regular(path)), evo.read(evo.regular(path.with_suffix(".claimed.json")))
    check(repair.get("schema") == 2 and repair["session_id"] == source["session_id"]
          and repair["slot"] == ticket["slot"] and repair["worker_ticket"] == str(ticket_path(scratch, mode, ticket["call"])),
          "formatting repair binding changed")
    calls = [r["payload"] for r in source["parent_rows"] if r.get("type") == "model_msg"
             and r.get("payload", {}).get("type") == "tool_call"
             and r["payload"].get("tool_call_id") == claim["tool_call_id"]]
    check(len(calls) == 1 and calls[0].get("name") == "input_subagent"
          and json.loads(calls[0]["arguments"]) == repair["call"], "original formatting repair call missing/changed")
    if len(users) == 1:
        raise WorkerPending("await the formatting prompt and fresh request in the original child")
    check(len(users) == 2 and users[1][1] == FORMAT_PROMPT, "formatting repair lacks exact original child input")
    original = worker_text(child[:users[1][0]])
    check(evo.digest(original.encode()) == repair["original_text_sha256"], "pre-repair worker result changed")
    before = validate_worker_record(worker_yaml(worker_text(child[:users[1][0]], last_request=True), mode, allow_fence=True), ticket)
    after = validate_worker_record(worker_yaml(worker_text(child[users[1][0] + 1:], repaired=True), mode), ticket)
    check(same_record(before, repair["original_result"]) and same_record(after, before),
          "formatting repair changed original result values")
    return after, source


def verify_format_repairs(mode):
    """A repair must be a new, completed, plain reply with unchanged values."""
    scratch = Path(mode["completion_request"]).parent.parent
    meta = None
    for path in (scratch / "evo-guard/format").glob("*.json"):
        if path.name.endswith(".claimed.json"):
            continue
        if not path.with_suffix(".claimed.json").exists():
            continue
        if meta is None:
            meta, _ = controller_trace(mode)
        repair = evo.read(evo.regular(path))
        check(repair.get("schema") == 2, "format repair lacks original result binding")
        ticket = evo.read(evo.regular(repair["worker_ticket"]))
        worker_result(meta, scratch, mode, ticket)


def phase_tickets(scratch, mode, phase):
    tickets = []
    for path in sorted((scratch / "evo-guard/dispatch").glob("*.json")):
        if path.name.endswith(".claimed.json"):
            continue
        ticket = evo.read(evo.regular(path))
        if ticket.get("phase") == phase:
            check(path == ticket_path(scratch, mode, ticket["call"]) and ticket["session_id"] == mode["session_id"],
                  "registered evaluator ticket identity changed")
            if path.with_name(evo.digest(ticket["slot"].encode()) + ".claimed.json").exists():
                tickets.append(ticket)
    return tickets


def verify_worker_matrix(mode, phase, matrix):
    """Bind matrix cells to strict original worker records; scoring stays in matrix()."""
    meta, scratch = controller_trace(mode)
    tickets = phase_tickets(scratch, mode, phase)
    cells = evo.read(matrix["cells"])
    check(len(tickets) == len(cells), "matrix needs exactly one claimed native worker per cell")
    seen = set()
    for ticket in tickets:
        value, source = worker_result(meta, scratch, mode, ticket)
        matches = [cell for cell in cells if cell["case_id"] == value["case_id"] and cell["run"] == value["run"]]
        check(len(matches) == 1 and same_record(value, matches[0]), "native worker result differs from matrix cell")
        check(source["session_id"] not in seen, "one worker cannot certify several matrix cells")
        seen.add(source["session_id"])
    return {"complete": True, "workers": len(seen)}


def validate_workers(mode, phase):
    """Read-only receipt check before matrix construction; never repair or dispatch."""
    check(mode["mode"] == "controller" and phase in ("training", "h0", "final"), "invalid worker validation phase")
    meta, scratch = controller_trace(mode)
    registration = registered_benchmarks(mode)
    expected = registration["training" if phase == "training" else "test"]["case_ids"]
    tickets = phase_tickets(scratch, mode, phase)
    results, cases = [], []
    for ticket in tickets:
        identity = worker_identity(ticket)
        cases.append(identity["case_id"])
        item = {"slot": ticket["slot"], "valid": False, "pending": False, "format_required": False, "repair_available": False}
        try:
            source = native_worker(meta, scratch, mode, ticket)
            item.update(subagent_id=source["handle"], session_id=source["session_id"])
            value, _ = worker_result(meta, scratch, mode, ticket)
            item.update(valid=True, cell=value)
        except WorkerPending as exc:
            item.update(pending=True, reason=str(exc))
        except WorkerFormatError as exc:
            item["reason"] = str(exc)
            if "subagent_id" in item:
                repair = scratch / "evo-guard/format" / (item["subagent_id"] + ".claimed.json")
                if not repair.exists():
                    try:
                        validate_worker_record(worker_yaml(worker_text(source["rows"], last_request=True), mode, allow_fence=True), ticket)
                        item.update(format_required=True, repair_available=True)
                    except (ValueError, KeyError, OSError):
                        pass
        except (ValueError, KeyError, OSError) as exc:
            item["reason"] = str(exc)
        results.append(item)
    return {"complete": sorted(cases) == sorted(expected) and all(item["valid"] and item["cell"]["status"] == "ok" for item in results),
            "phase": phase, "workers": results, "missing": sorted(set(expected) - set(cases)),
            "cells": [item["cell"] for item in results if item["valid"]],
            "read_only": True, "automatic_dispatch": False}


def runtime_call(call, runtime):
    evo.native_prompt(call)
    check(not call.get("agent_id"), "role workers inherit this Agent; do not switch Agent")
    for key in ("provider", "model_id", "thinking_level"):
        check(call.get(key) == runtime[key], "native worker runtime mismatch: " + key)


def protocol_module(mode):
    check(sha(mode["config"]) == mode["config_sha256"], "frozen experiment config changed")
    check(sha(mode["protocol"]) == mode["protocol_sha256"], "frozen benchmark protocol changed")
    check(sha(Path(mode["protocol"]).with_name("acceptance.py")) == mode["acceptance_sha256"], "frozen acceptance helper changed")
    for name, expected in mode.get("support_sha256", {}).items():
        check(name in ("registration.py", "injection.py") and sha(Path(mode["protocol"]).with_name(name)) == expected,
              "frozen benchmark support helper changed")
    spec = importlib.util.spec_from_file_location("evo_benchmark_protocol", mode["protocol"])
    module = importlib.util.module_from_spec(spec)
    # Protocol-local acceptance module is loaded by the protocol itself.
    spec.loader.exec_module(module)
    return module


def configure_controller(req, meta, scratch, rows):
    calls = {r["payload"]["tool_call_id"]: r for r in rows if not r.get("origin") and r.get("type") == "model_msg"
             and r.get("payload", {}).get("type") == "tool_call" and r["payload"].get("name") == "read_file"}
    outputs = {r["payload"]["tool_call_id"]: r for r in rows if not r.get("origin") and r.get("type") == "model_msg"
               and r.get("payload", {}).get("type") == "tool_call_output"}
    pairs = [{"call": calls[k], "observation": outputs[k]} for k in calls if k in outputs]
    materials = [str(SKILL / p) for p in ("SKILL.md", "references/control.md")]
    evo.verify_workspace_inputs({"pairs": pairs}, materials, meta["agent_state"])
    config = evo.read(req["config"])
    check(config["protocol"] == "evo-harness-gdpevo-v1", "this guard requires the continuous GDPevo profile")
    check(config.get("paid_authorized") is True, "current experiment must already be authorized")
    evo.finite(config["budget_usd"], low=0.01)
    protocol = Path(config["deployment"]["benchmark_checkout"]) / "experiments/gdpevo/evo-harness/v1/protocol.py"
    mode = {"schema": 2, "mode": "controller", "session_id": meta["session_id"],
            "config": str(Path(req["config"]).resolve()), "config_sha256": sha(req["config"]),
            "protocol": str(protocol), "protocol_sha256": sha(protocol),
            "acceptance_sha256": sha(protocol.with_name("acceptance.py")),
            "support_sha256": {name: sha(protocol.with_name(name)) for name in ("registration.py", "injection.py")},
            "freeze_request": str(scratch / "evo-guard/freeze-request.json"),
            "completion_request": str(scratch / "evo-guard/completion-request.json")}
    module = protocol_module(mode)
    module.validate_config(config, config["deployment"]["benchmark_checkout"])
    check(meta["provider"] == config["runtime"]["provider"] and meta["model_id"] == config["runtime"]["model_id"], "controller model mismatch")
    previous = evo.read(scratch / MARKER)
    check(previous.get("configured") is False or previous == mode, "controller contract cannot be replaced")
    evo.atomic(scratch / MARKER, evo.encoded(mode))
    (scratch / "evo-guard/dispatch").mkdir(parents=True, exist_ok=True)
    return mode


def registered_benchmarks(mode):
    module = protocol_module(mode)
    config = evo.read(mode["config"])
    deployment = config["deployment"]
    return module.execute({"op": "verify-benchmarks", "config": mode["config"],
        "config_sha256": mode["config_sha256"], "repo": deployment["benchmark_checkout"],
        "output": str(Path(deployment["penguin_home"]) / deployment["project_id"] / "benchmarks")})


def freeze(mode, phase="final"):
    module = protocol_module(mode)
    config = evo.read(mode["config"])
    request = evo.read(mode["freeze_request"])
    registration = registered_benchmarks(mode)
    check(request["runtime"] == config["runtime"] and request["test_case_ids"] == registration["test"]["case_ids"], "freeze differs from authorized runtime/test roster")
    check(Path(request["evo_tool"]).resolve() == TOOL, "use the installed trusted method helper")
    target = config["test_agent_id"].removeprefix("create:").strip()
    deployment = config["deployment"]
    target_state = Path(deployment["penguin_home"]) / deployment["project_id"] / "agents" / target / "agent_state"
    check(Path(request["target_state"]).resolve() == target_state.resolve(), "freeze names another Target")
    exported = evo.read(request["export_request"])
    check(Path(exported["evo_tool"]).resolve() == TOOL and exported["train_ids"] == config["train_ids"] and exported["runtime"] == config["runtime"],
          "export differs from frozen method batch")
    frozen_profile = evo.read(Path(exported["private_learning"]) / "frozen-training.json")
    check(frozen_profile["config_sha256"] == mode["config_sha256"] and frozen_profile["guard_version"] == 2,
          "export belongs to another experiment/profile")
    request["active_state"] = phase
    return module.execute({**request, "op": "verify-freeze"})


def ticket_path(scratch, mode, call):
    base = Path(mode["private_learning"]) / "dispatch" if mode["mode"] == "training" else scratch / "evo-guard/dispatch"
    return base / (evo.digest(canonical(call).encode()) + ".json")


def journal_path(scratch, mode):
    return (Path(mode["private_learning"]) if mode["mode"] == "training" else scratch / "evo-guard") / "journal.json"


def write_ticket(scratch, mode, call, slot, phase, extra=None):
    path = ticket_path(scratch, mode, call)
    ticket = {"schema": 2, "session_id": mode["session_id"], "call": call, "slot": slot, "phase": phase, **(extra or {})}
    if path.exists():
        check(evo.read(path) == ticket, "dispatch request changed; reconcile the original operation")
    else:
        evo.write_once(path, evo.encoded(ticket))
    return {"call": call, "ticket": str(path), "slot": slot, "dispatched": False,
            "request_sha256": evo.digest(canonical(call).encode()), "operation_id": "op-" + evo.digest(slot.encode())[:24],
            "journal": str(journal_path(scratch, mode))}


def dispatch_budget(args, ticket, meta, scratch, mode):
    ledger = evo.read(journal_path(scratch, mode))
    if mode["mode"] == "training":
        _, profile = training_profile(meta["agent_state"])
        budget, config_hash = profile["training_budget_usd"], profile["config_sha256"]
    else:
        budget, config_hash = evo.read(mode["config"])["budget_usd"], mode["config_sha256"]
    check(ledger["controller_session_id"] == meta["session_id"] and ledger["config_sha256"] == config_hash,
          "dispatch ledger belongs to another coordinator/config")
    check(ledger["budget_usd"] == budget, "dispatch budget differs from authorized allocation")
    operations = list(ledger["operations"].values())
    check(any(op["slot"] == "controller" and op["status"] in ("reserved", "running", "cost_pending") for op in operations),
          "reserve the coordinator's remaining cost before dispatch")
    matching = [op for op in operations if op["slot"] == ticket["slot"]]
    check(len(matching) == 1 and matching[0]["status"] == "reserved"
          and matching[0]["request_sha256"] == evo.digest(canonical(args).encode()), "native call needs its exact reserved budget operation")
    committed = ledger["known_cost_usd"] + sum(op["reserve_usd"] for op in operations if op["status"] != "complete")
    check(committed <= budget, "dispatch budget exhausted")


def completed_context(t, task, parent=None):
    context = evo.read(Path(t["private_learning"]) / "contexts" / (task + ".json"))
    evo.validate_contexts([context], [task])
    check(context["trace"]["binding"]["source"] == "subagent", "a native Solver child is required")
    if parent is not None:
        root_meta, _, _ = trace_context(context["trace"]["binding"]["parent_trace"])
        check(root_meta["session_id"] == parent["session_id"] and root_meta["agent_state"] == parent["agent_state"],
              "Solver belongs to another training coordinator")
    for key in ("provider", "model_id"):
        check(context["trace"]["binding"][key] == t["runtime"][key], "Solver runtime changed")
    return context


def role_request(req, meta, scratch, mode):
    state = Path(meta["agent_state"])
    _, t = training_profile(state)
    check(sha(mode["training_path"]) == mode["training_sha256"], "training profile changed")
    private = Path(t["private_learning"])
    role = req["role"]
    check(role in ("solver", "reflector", "evolver"), "unknown method role")
    task = req.get("task_id")
    if role != "evolver":
        check(task in t["train_ids"], "task not in frozen batch")
        prior = t["train_ids"][:t["train_ids"].index(task)]
        for previous in prior:
            completed_context(t, previous, meta)
    text = f"Caller agent: {meta.get('agent_id', state.parent.name)}\n" + role_text(role)
    text += f"\nExact helper: {TOOL}\nPrivate evidence: {private}\n"
    text += ("\nHelper requests are JSON files you write with write_file. Execute python3 -B EXACT_HELPER ABS_REQUEST.json. "
             'Full reads use {"op":"read-material","path":"ABS_MATERIAL","offset":0}; repeat offsets 4000,8000,... until end=total. '
             'Each read-material exec must contain exactly one helper invocation with a literal absolute helper and request path; no variables, shell loops, batching, pipes or redirection. Independent reads may be parallel native tool calls with distinct request files; await every full receipt before sealing. '
             "Do not edit helpers, State, role text or evidence. Only the Solver uses its exact judge template below.\n")
    role_file = private / "roles" / (role + ".md")
    if role == "solver":
        directory = Path(t["workspace"]) / "train_tasks" / task
        check(directory.is_dir(), "missing public member input directory")
        inputs, binary = [], []
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            inside(path, directory)
            try:
                path.read_text()
                inputs.append(str(path))
            except UnicodeDecodeError:
                binary.append({"path": str(path), "sha256": sha(path)})
        access = Path(t["workspace"]) / "environment_access.md"
        check(access.is_file() and inputs, "public input/environment access missing")
        inputs.append(str(access))
        grant = {"evo_task": {"split": "train", "task_id": task, "attempt_id": t["attempt_id"], "inputs": inputs}}
        paths = evo.judge_paths(t, task, t["attempt_id"])
        # The Solver fills the existing API URL from its public access document;
        # the helper checks the canonical identity and never permits another attempt.
        text += "\n" + json.dumps(grant, ensure_ascii=False)
        text += "\nFirst satisfy the learned reader inherited in your Agent State: use native read_file for the public task and full learned SKILL.md/index.json, then run its foreground selector and observe its complete output. After selection, complete all public inputs before solving. A complete native read_file of a granted public input also satisfies its read audit; do not duplicate that read through the helper. Use read-material for any incomplete or truncated native input; this does not replace the learned reader's own native read_file requirement.\n"
        text += f"\nRead full role file {role_file} using read-material. Read every granted public input completely, either with exact successful native read_file windows (all lines, before judge) or read-material chunks. Native output with any truncation, a line over 2000 UTF-16 units, or a file at least 8 MiB needs read-material instead. This public-input option never applies to the feedback ledger or learning-role materials."
        text += f"\nFinal answer: {Path(t['answer_dir']) / (task + '.json')}\n"
        text += "Use judge exactly once via one standalone helper command. Never probe judge directly. "
        text += "After feedback, do not revise the answer or call the service again.\n"
        text += "Canonical judge paths: " + json.dumps({k: str(v) for k, v in paths.items()}, ensure_ascii=False)
        template = {"op": "prepare-judge-request", "task_id": task,
                    "url": "REPLACE_WITH_LOCAL_TRAINING_API_BASE/api/judge"}
        text += "\nWrite this helper request to a separate private JSON file and execute it (it writes canonical request data and makes zero judge calls): " + json.dumps(template)
        text += "\nAfter preparation, execute the returned canonical command exactly once. "
        text += f"It must be: python3 -B {shlex.quote(str(TOOL))} {shlex.quote(paths['judge_request'])}\n"
        text += "Judge stdout is a compact receipt. Afterwards read the canonical output feedback file fully with read-material chunks; never rerun judge to recover truncated output.\n"
        text += "\nBinary inputs (read via appropriate tools, do not fabricate text receipts): " + json.dumps(binary)
        slot = f"solver/{task}/{t['attempt_id']}"
    elif role == "reflector":
        context = completed_context(t, task, meta)
        check(context["negative"], "success produces no Reflector")
        out = private / "roles" / task / "candidates.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        text += f"\nEvery candidate id must start with {task}-{t['attempt_id']}- (for example {task}-{t['attempt_id']}-c01); this prevents collisions between independent Reflectors.\n"
        text += f"\nTask/attempt: {task}/{t['attempt_id']}\nRead {role_file} and {private / 'contexts' / (task + '.json')} in full.\nOutput only to {out}.\n"
        text += "Before sealing, validate your own output and correct any structural error using: " + json.dumps({
            "op": "validate-candidates", "contexts": [str(private / "contexts" / (task + ".json"))],
            "train_ids": [task], "candidates": str(out), "candidate_prefix": task + "-" + t["attempt_id"] + "-"})
        text += "\nAfter validation succeeds, use seal-role-output below. Do not change output after a successful seal.\n"
        slot = f"reflector/{task}/{t['attempt_id']}"
    else:
        contexts = [completed_context(t, task_id, meta) for task_id in t["train_ids"]]
        candidates = []
        audits = req.get("reflectors", [])
        negative = [context["task_id"] for context in contexts if context["negative"]]
        check([audit.get("task_id") for audit in audits] == negative,
              "Evolver requires each negative task's completed Reflector audit in frozen order")
        evo.verify_training_root(contexts, audits)
        for audit, task_id in zip(audits, negative):
            output = str(private / "roles" / task_id / "candidates.json")
            context_path = str(private / "contexts" / (task_id + ".json"))
            check(audit.get("role") == "reflector" and audit.get("outputs") == [output]
                  and context_path in audit.get("materials", [])
                  and str(private / "roles/reflector.md") in audit["materials"], "wrong Reflector material/output audit")
            evo.audit_role({**audit, "require_role_seal": True})
            values = evo.read(output)
            check(all(candidate["id"].startswith(task_id + "-" + t["attempt_id"] + "-") for candidate in values),
                  "Reflector candidate ids need their frozen task/attempt namespace")
            candidates.extend(values)
        evo.validate_candidates(candidates, {(context["task_id"], context["attempt_id"]): context for context in contexts})
        target = private / "candidates.json"
        if target.exists():
            check(evo.read(target) == candidates, "candidate concatenation changed")
        else:
            evo.write_once(target, evo.encoded(candidates))
        materials = [str(role_file), str(target), str(private / "prebatch-library.json")]
        materials += [str(private / "contexts" / (task_id + ".json")) for task_id in t["train_ids"]]
        text += "\nRead all these files in full: " + json.dumps(materials)
        text += "\nExact frozen compilation/injection limits: " + json.dumps({key: t[key] for key in ("limits", "max_entries", "max_bytes")})
        text += ("\nlimits.general is the total number of general entries; limits.topic is the maximum per topic. "
                 "Every exported entry (including '# id', Trigger, Scope and newline framing) must fit max_bytes UTF-8 bytes. "
                 "Keep trigger/scope concise: each exported index.json physical line must fit 2000 UTF-16 code units, including JSON escaping and indentation, so native read_file can observe it completely. "
                 "The reader selects at most max_entries entries and max_bytes combined bytes. "
                 "Before sealing, validate your own decisions with validate-decisions using the paths and frozen budgets below; "
                 "correct your own invalid JSON before seal-role-output, never ask the coordinator to rewrite it.\n")
        text += "Validation request: " + json.dumps({"op": "validate-decisions", "train_ids": t["train_ids"],
            "contexts": [str(private / "contexts" / (task_id + ".json")) for task_id in t["train_ids"]],
            "candidates": str(target), "decisions": str(private / "decisions.json"), "library": str(private / "prebatch-library.json"),
            **{key: t[key] for key in ("limits", "max_entries", "max_bytes")}})
        text += f"\nOutput operations to {private / 'decisions.json'} and seal with seal-role-output. Zero candidates requires [] and a real Evolver return.\n"
        slot = "evolver/batch-1"
    if role in ("reflector", "evolver"):
        contract = evo.role_contract(t, role, task if role == "reflector" else None)
        text += "\n" + json.dumps({"evo_role_seal": {**contract, "parent_session_id": meta["session_id"]}}, ensure_ascii=False)
        text += "\nSeal request: " + json.dumps({"op": "seal-role-output", "role": role,
                    **({"task_id": task} if role == "reflector" else {})})
        text += ("\nWrite the seal request in your scratchpad, then execute one foreground python3 -B command using the literal absolute helper and request paths. "
                 "The helper discovers your current native Session from its server-injected environment and checks all your original trace segments, fixed materials and draft structure. "
                 "If complete=false, read each missing offset in this same leaf with a separate read-material exec and retry seal; "
                 "if draft validation fails, correct only your own unsealed draft and retry. This is receipt/format repair, never another Solver attempt, service call, or judge call. "
                 "Do not claim completion until complete=true. Generic file-digest is not a role seal. "
                 "Do not request parent steering. After successful sealing, do not change your output or retry learning.\n")
    # The coordinator copies only a short native call. Preserve the complete
    # original role/grants in an immutable file; do not ask the model to retype
    # thousands of exact characters through a truncated tool-result window.
    prompt_path = private / "dispatch" / (evo.digest(slot.encode()) + ".prompt.txt")
    raw_prompt = text.encode("utf-8")
    if prompt_path.exists():
        check(evo.regular(prompt_path).read_bytes() == raw_prompt, "registered role prompt changed")
    else:
        evo.write_once(prompt_path, raw_prompt)
    call = {"description": f"Evo-Harness {role}" + (f" {task}" if task else ""),
            "prompt_file": str(prompt_path), "prompt_sha256": evo.digest(raw_prompt),
            **{k: t["runtime"][k] for k in ("provider", "model_id", "thinking_level")}, "run_in_background": True}
    return write_ticket(scratch, mode, call, slot, role, {"task_id": task,
        **({"reflectors": req.get("reflectors", [])} if role == "evolver" else {})})


def worker_request(req, scratch, mode):
    check(mode["mode"] == "controller" and "config" in mode, "configure the controller before dispatch")
    config = evo.read(mode["config"])
    call, phase = req["call"], req["phase"]
    registration = registered_benchmarks(mode)
    runtime_call(call, config["runtime"])
    check(call.get("run_in_background") is True, "guarded evaluators must use native background completion notifications")
    check(phase in ("training", "h0", "final"), "unknown worker phase")
    benchmark = config["benchmark_id"] if phase == "training" else config["test_benchmark_id"]
    field = "trainer_agent_id" if phase == "training" else "test_agent_id"
    agent = config[field].removeprefix("create:").strip()
    def line(key):
        found = re.findall(r"^" + key + r":\s*(\S+)\s*$", evo.native_prompt(call), re.M)
        check(len(found) == 1, "worker prompt needs one unambiguous " + key)
        return found[0]
    check(line("benchmark_id") == benchmark and line("test_agent_id") == agent, "worker identity differs from frozen protocol")
    for key in ("provider", "model_id", "thinking_level"):
        check(line(key) == config["runtime"][key], "worker prompt runtime differs from frozen protocol")
    case, run = line("case_id"), line("run")
    check(run == "1", "one frozen repetition required")
    if phase == "training":
        check(line("training_protocol_version") == "1", "continuous group worker contract required")
        check(case in registration["training"]["case_ids"], "not the frozen continuous group Case")
    else:
        acceptance = freeze(mode, phase)
        check(case in acceptance["test_case_ids"], "test case is not frozen")
        check(line("expected_version") == str(acceptance[phase if phase == "h0" else "final"]["version"]), "wrong State version")
        if phase == "final":
            check(not acceptance["equivalent_h0"], "valid H0-equivalent compilation must reuse H0, not rerun")
            h0 = evo.read(Path(mode["completion_request"]).parent / "h0-matrix-request.json")
            check(protocol_module(mode).matrix(h0)["complete"], "finish the complete H0 matrix before final testing")
            verify_worker_matrix(mode, "h0", h0)
    return write_ticket(scratch, mode, call, f"{phase}/{case}/{run}", phase)


def authorize_call(args, meta, scratch, mode, call_id):
    ticket = evo.read(ticket_path(scratch, mode, args))
    check(ticket["call"] == args and ticket["session_id"] == meta["session_id"], "native call differs from registered request")
    dispatch_budget(args, ticket, meta, scratch, mode)
    if mode["mode"] == "controller":
        worker_request({"call": args, "phase": ticket["phase"]}, scratch, mode)
    else:
        _, t = training_profile(meta["agent_state"])
        runtime_call(args, t["runtime"])
        prepared = role_request({"role": ticket["phase"], "task_id": ticket["task_id"],
                                 "reflectors": ticket.get("reflectors", [])}, meta, scratch, mode)
        check(prepared["call"] == args, "role evidence changed after its registered request")
    claim = ticket_path(scratch, mode, args).parent / (evo.digest(ticket["slot"].encode()) + ".claimed.json")
    if claim.exists():
        check(evo.read(claim)["tool_call_id"] == call_id, "semantic slot already dispatched; reconcile its original child, never retry")
    else:
        evo.write_once(claim, evo.encoded({"slot": ticket["slot"], "tool_call_id": call_id, "request_sha256": evo.digest(canonical(args).encode())}))


def host_executes_experiment(command):
    """Recognize actual cooperative CLI launches, not CLI words in written data.

    This is not an arbitrary-program security boundary. Native worker tickets,
    immutable artifacts and the isolated runtime remain the experiment contract.
    """
    def python_launch(code):
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
        imports = {}
        assignments = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    imports[item.asname or item.name] = item.name
            elif isinstance(node, ast.ImportFrom) and node.module in ("subprocess", "os"):
                for item in node.names:
                    imports[item.asname or item.name] = node.module + "." + item.name
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assignments[target.id] = node.value
        def literal(node, seen=frozenset()):
            if isinstance(node, ast.Name) and node.id not in seen and node.id in assignments:
                return literal(assignments[node.id], seen | {node.id})
            try:
                return ast.literal_eval(node)
            except (ValueError, TypeError, SyntaxError):
                return None
        def function(node):
            if isinstance(node, ast.Name):
                return imports.get(node.id, node.id)
            if isinstance(node, ast.Attribute):
                return function(node.value) + "." + node.attr
            return ""
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or function(node.func) not in (
                    "os.system", "os.popen", "subprocess.run", "subprocess.call", "subprocess.Popen",
                    "subprocess.check_call", "subprocess.check_output"):
                continue
            value = literal(node.args[0]) if node.args else next((literal(k.value) for k in node.keywords if k.arg == "args"), None)
            if isinstance(value, str) and host_executes_experiment(value):
                return True
            if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value) and cli(list(value)):
                return True
        return False
    def cli(argv):
        while argv and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0], re.S):
            argv = argv[1:]
        if not argv:
            return False
        binary = Path(argv[0]).name
        if binary in ("env", "sudo", "nohup", "exec", "command"):
            rest = argv[1:]
            valued = ({"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C", "--close-from", "-T", "--command-timeout"}
                      if binary == "sudo" else {"-u", "--unset", "-C", "--chdir"} if binary == "env" else set())
            while rest and (rest[0].startswith("-") or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", rest[0], re.S)):
                if binary == "env" and rest[0] in ("-S", "--split-string"):
                    return len(rest) > 1 and host_executes_experiment(rest[1])
                rest = rest[2:] if rest[0] in valued else rest[1:]
            return cli(rest)
        if binary in ("harbor", "penguin", "penguin-evo"):
            if any(arg in ("-h", "--help", "--version") for arg in argv[1:]):
                return False
            rest = argv[1:]
            valued = {"--server", "--home", "--project-id", "--agent-id", "--config", "--log-level"}
            while rest and rest[0].startswith("-"):
                rest = rest[2:] if rest[0] in valued else rest[1:]
            if binary == "harbor":
                return bool(rest) and (rest[0] == "run" or (len(rest) > 1 and rest[0] in ("job", "jobs") and rest[1] == "run"))
            return bool(rest) and rest[0] in ("run", "chat")
        if binary in ("uv", "uvx", "pnpm", "npx"):
            # These wrappers leave the explicit target binary in argv; options
            # before it belong to the wrapper, not to written documentation.
            return any(Path(arg).name in ("harbor", "penguin", "penguin-evo") and cli(argv[i:]) for i, arg in enumerate(argv[1:], 1))
        if re.fullmatch(r"python[0-9.]*", binary):
            if "-c" in argv and argv.index("-c") + 1 < len(argv):
                return python_launch(argv[argv.index("-c") + 1])
            if "-m" in argv and argv.index("-m") + 1 < len(argv):
                return cli(argv[argv.index("-m") + 1:])
        if binary in ("sh", "bash", "dash", "zsh") and len(argv) > 2 and argv[1] in ("-c", "-lc"):
            return host_executes_experiment(argv[2])
        return False
    lines, shell, i = command.splitlines(), [], 0
    while i < len(lines):
        match = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", lines[i])
        if not match:
            shell.append(lines[i])
            i += 1
            continue
        end = i + 1
        while end < len(lines) and lines[end].strip() != match[2]:
            end += 1
        header, body = lines[i][:match.start()], "\n".join(lines[i + 1:end])
        if re.search(r"(?:^|[\s;&|])(?:\S*/)?python[0-9.]*\s", header) and python_launch(body):
            return True
        if re.search(r"(?:^|[\s;&|])(?:\S*/)?(?:ba|z|da)?sh\s", header) and host_executes_experiment(body):
            return True
        shell.append(header)
        i = min(end + 1, len(lines))
    try:
        lexer = shlex.shlex("\n".join(shell), posix=True, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        part = []
        for token in lexer:
            if token in (";", "&&", "||", "|", "&", "\n"):
                if cli(part):
                    return True
                part = []
            else:
                part.append(token)
        return cli(part)
    except ValueError:
        return False


def safe_helper(args, allowed_paths):
    raw = args.get("cmd", args.get("command"))
    check(isinstance(raw, str), "one standalone helper command required")
    argv = shlex.split(raw)
    check(len(argv) == 4 and argv[:2] == ["python3", "-B"], "root may execute only python3 -B FIXED_HELPER REQUEST.json")
    check(Path(argv[2]).resolve() in allowed_paths and Path(argv[2]).is_absolute(), "not an installed method helper")
    check(Path(argv[3]).is_absolute(), "request path must be absolute")
    # shlex alone does not detect shell substitutions inside quoted strings.
    check(not any(x in raw for x in ("$", "`", "\n", ";", "|", "&", ">", "<")), "shell composition forbidden for coordinator helpers")
    return Path(argv[2]).resolve(), Path(argv[3]), evo.read(argv[3])


def training_helper(req, meta, scratch, mode):
    """Check file scopes for coordinator primitives; no business or judge tools."""
    _, t = training_profile(meta["agent_state"])
    private, state = Path(t["private_learning"]), Path(meta["agent_state"])
    traces, skill = state.parent / "traces", Path(t["answer_dir"]).parent / "skill"
    check(not req.get("path_map"), "coordinator cannot remap raw evidence")
    op = req.get("op")
    check(op in ROOT_OPS, "training root cannot solve or call judge")
    if op in ("read-material", "file-digest", "fingerprint", "verify-package"):
        path = Path(req["path"])
        check(path.is_absolute() and any(path.resolve().is_relative_to(root.resolve()) for root in (private, state, traces, skill)),
              "coordinator helper reads only method, native traces and captured evidence")
    elif op == "journal":
        check(Path(req["journal"]).resolve() == journal_path(scratch, mode), "wrong coordinator journal")
        check(req["config_sha256"] == t["config_sha256"], "wrong journal config")
        check(req["action"] in ("init", "reserve", "bind", "status"),
              "training cost settles on host only after all Sessions stop")
        if req["action"] == "init":
            check(req["controller_session_id"] == meta["session_id"] and req["budget_usd"] == t["training_budget_usd"], "wrong journal allocation/Session")
    else:
        for field in ("contexts", "materials", "outputs"):
            for path in req.get(field, []):
                inside(path, private)
        for field in ("candidates", "decisions", "library"):
            if field in req:
                inside(req[field], private)
        if "output" in req:
            if op == "export":
                check(Path(req["output"]).resolve() == skill, "only the final submission skill may be exported")
            else:
                inside(req["output"], private)
        if op == "capture":
            task = req["task_id"]
            check(task in t["train_ids"] and req["train_ids"] == t["train_ids"] and req["attempt_id"] == t["attempt_id"], "capture batch changed")
            paths = evo.judge_paths(t, task, t["attempt_id"])
            check(all(req[k] == paths[v] for k, v in (("answer", "answer"), ("feedback", "output"), ("judge_request", "judge_request"))), "capture must use the one canonical attempt")
            check(Path(req["output"]).resolve() == private / "contexts" / (task + ".json"), "wrong context destination")
        if op == "seal-training":
            check(Path(req["private_learning"]).resolve() == private and Path(req["skill"]).resolve() == skill,
                  "wrong training export scope")
            check(canonical(req.get("runtime")) == canonical(t["runtime"]),
                  "seal runtime must exactly match the complete frozen runtime; use prepare-seal-training")
            check(Path(req["output"]).resolve() == private / "training-export.json", "wrong training export destination")
        if "train_ids" in req:
            check(req["train_ids"] == t["train_ids"], "coordinator batch changed")
        for field in ("limits", "max_entries", "max_bytes"):
            if field in req:
                check(req[field] == t[field], "frozen method budget changed")


def prepare_capture(req, meta, scratch, mode):
    """Prepare canonical metadata from one completed registered native Solver.

    The existing capture validator runs into a disposable private directory
    before request publication. Only the caller's later exact capture command
    can publish the canonical context; this helper never judges or dispatches.
    """
    import tempfile
    check(mode["mode"] == "training", "prepare-capture is training-only")
    check(set(req) == {"op", "trace_path", "task_id"}, "prepare-capture takes only trace_path and task_id")
    path, t = training_profile(meta["agent_state"])
    check(str(path) == mode["training_path"] and sha(path) == mode["training_sha256"], "training profile changed")
    private = Path(t["private_learning"])
    check(str(private) == mode["private_learning"], "private evidence directory changed")
    check(evo.regular(private / "frozen-training.json").read_bytes() == evo.regular(path).read_bytes(),
          "portable frozen training snapshot changed")
    task = req["task_id"]
    check(task in t["train_ids"], "task not in frozen batch")
    for previous in t["train_ids"][:t["train_ids"].index(task)]:
        completed_context(t, previous, meta)
    slot = f"solver/{task}/{t['attempt_id']}"
    tickets = []
    for candidate in sorted((private / "dispatch").glob("*.json")):
        if candidate.name.endswith(".claimed.json"):
            continue
        ticket = evo.read(evo.regular(candidate))
        if ticket.get("phase") == "solver" and ticket.get("task_id") == task:
            check(ticket["session_id"] == meta["session_id"] and ticket["slot"] == slot,
                  "Solver ticket belongs to another coordinator/attempt")
            check(candidate == ticket_path(scratch, mode, ticket["call"]), "Solver ticket hash/path changed")
            runtime_call(ticket["call"], t["runtime"])
            check(ticket["call"].get("run_in_background") is True, "Solver must use native background completion")
            claim = evo.read(evo.regular(candidate.parent / (evo.digest(slot.encode()) + ".claimed.json")))
            check(claim.get("slot") == slot and claim.get("request_sha256") == evo.digest(canonical(ticket["call"]).encode()),
                  "Solver claim differs from registered native call")
            tickets.append((ticket, claim))
    check(len(tickets) == 1, "prepare-capture needs exactly one registered Solver dispatch")
    ticket, claim = tickets[0]
    trace_root = Path(meta["agent_state"]).parent / "traces"
    trace_files = sorted(trace_root.rglob("*.jsonl"))
    sessions = {}
    for trace in trace_files:
        inside(trace, trace_root)
        try:
            identity, _, rows = trace_context(trace)
        except (ValueError, KeyError, OSError):
            continue
        sessions.setdefault(identity["session_id"], []).append((trace, identity, rows))
    fields = ("session_id", "source", "provider", "model_id", "workspace", "agent_state")
    def require_all_segments(session_id, segments):
        known = {trace for trace in trace_files if trace.name.startswith(session_id + "_")
                 or trace.name == session_id + ".jsonl"}
        check(known <= {trace for trace, _, _ in segments}, "original Session segment missing/invalid metadata")
    root_segments = sessions.get(meta["session_id"], [])
    require_all_segments(meta["session_id"], root_segments)
    check(root_segments and all(all(identity[k] == meta[k] for k in fields) for _, identity, _ in root_segments),
          "original coordinator trace identity changed")
    # Unlike a status hook, preparation certifies recorded evidence. Never skip
    # a damaged complete parent segment while retaining a convenient call/notice.
    for trace, _, _ in root_segments:
        for line in evo.regular(trace).read_bytes().splitlines():
            json.loads(line)
    root_rows = [(trace, row) for trace, _, rows in root_segments for row in rows if not row.get("origin")]
    calls = [row["payload"] for _, row in root_rows if row.get("type") == "model_msg"
             and row.get("payload", {}).get("type") == "tool_call"
             and row["payload"].get("tool_call_id") == claim["tool_call_id"]]
    check(len(calls) == 1 and calls[0].get("name") == "run_subagent"
          and json.loads(calls[0]["arguments"]) == ticket["call"], "registered Solver call absent from original parent trace")
    outputs = [row["payload"] for _, row in root_rows if row.get("type") == "model_msg"
               and row.get("payload", {}).get("type") == "tool_call_output"
               and row["payload"].get("tool_call_id") == claim["tool_call_id"]]
    check(len(outputs) == 1, "registered Solver launch observation missing/ambiguous")
    handles = re.findall(r"\bsubagent_id ([A-Za-z0-9_-]+)(?:[;\]\s])", outputs[0]["output"])
    check(len(handles) == 1, "native Solver handle missing/ambiguous")
    completed = False
    for _, row in root_rows:
        payload = row.get("payload", {})
        if payload.get("type") != "text" or payload.get("sender") != "harness":
            continue
        block = re.match(r"(?:\[background_task_done\]\n(.*?)\n\[/background_task_done\]|<background_task_done>\n(.*?)\n</background_task_done>)",
                         payload.get("text", ""), re.S)
        if block:
            values = dict(re.findall(r"^(kind|id|status):\s*(\S+)\s*$", block[1] or block[2], re.M))
            if values.get("kind") == "subagent" and values.get("id") == handles[0]:
                completed = values.get("status") == "completed"
    check(completed, "await the original Solver's native completed notification before prepare-capture")
    matching = []
    for session_id, segments in sessions.items():
        prompts = [row["payload"]["text"] for _, _, rows in segments for row in rows
                   if not row.get("origin") and row.get("type") == "model_msg"
                   and row.get("payload", {}).get("type") == "text"
                   and row["payload"].get("role") == "user" and row["payload"].get("sender") == "parent_agent"]
        if not prompts or prompts[0] != evo.native_prompt(ticket["call"]):
            continue
        require_all_segments(session_id, segments)
        identity = segments[0][1]
        check(identity["source"] == "subagent" and identity["agent_state"] == meta["agent_state"]
              and identity["workspace"] == t["workspace"]
              and all(identity[k] == t["runtime"][k] for k in ("provider", "model_id"))
              and all(all(other[k] == identity[k] for k in fields) for _, other, _ in segments),
              "original Solver trace runtime/State/workspace identity changed")
        pointers = [str(trace) for trace, row in root_rows if row.get("type") == "event_msg"
                    and row.get("payload", {}).get("type") == "subagent"
                    and row["payload"].get("session_id") == session_id]
        check(pointers, "Solver has no original parent-child event")
        matching.append((segments, {**{k: identity[k] for k in fields}, "parent_trace": pointers[0]}, prompts[0]))
    check(len(matching) == 1, "prepare-capture requires one unambiguous original Solver Session")
    segments, binding, prompt = matching[0]
    grants = [value["evo_task"] for value in evo.json_values(prompt)
              if isinstance(value, dict) and set(value) == {"evo_task"}]
    check(len(grants) == 1 and isinstance(grants[0], dict), "unique original atomic task grant required")
    grant = grants[0]
    check(set(grant) == {"split", "task_id", "attempt_id", "inputs"}
          and grant["split"] == "train" and grant["task_id"] == task and grant["attempt_id"] == t["attempt_id"],
          "original Solver task grant differs from frozen attempt")
    inputs = grant["inputs"]
    check(isinstance(inputs, list) and inputs and all(isinstance(name, str) for name in inputs)
          and len(inputs) == len(set(inputs)), "invalid granted input list")
    access = str(Path(t["workspace"]) / "environment_access.md")
    check(inputs[-1] == access, "original public access grant missing")
    for name in inputs[:-1]:
        inside(name, Path(t["workspace"]) / "train_tasks" / task)
    paths = evo.judge_paths(t, task, t["attempt_id"])
    prepared = {"op": "capture", "split": "train", "task_id": task, "attempt_id": t["attempt_id"],
        "train_ids": t["train_ids"], "answer": paths["answer"], "feedback": paths["output"],
        "judge_request": paths["judge_request"], "inputs": inputs, "traces": [str(trace) for trace, _, _ in segments],
        "binding": binding, "output": str(private / "contexts" / (task + ".json"))}
    training_helper(prepared, meta, scratch, mode)
    destination = inside(private / "requests" / f"capture-{task}-{t['attempt_id']}.prepared.json", private / "requests")
    with evo.locked(str(destination) + ".lock"):
        if destination.exists():
            check(canonical(evo.read(evo.regular(destination))) == canonical(prepared),
                  "existing capture request differs; preserve the original request")
        else:
            check(not Path(prepared["output"]).exists(), "context already exists; do not prepare a new capture")
        with tempfile.TemporaryDirectory(prefix="capture-check-", dir=private) as directory:
            checked = Path(directory) / "context.json"
            evo.capture({**prepared, "output": str(checked)})
            context_exists = Path(prepared["output"]).exists()
            if context_exists:
                check(evo.regular(prepared["output"]).read_bytes() == checked.read_bytes(), "existing context differs from original evidence")
            context_sha256 = sha(checked) if context_exists else None
        if not destination.exists():
            evo.write_once(destination, evo.encoded(prepared))
    return {"op": "prepare-capture", "prepared": True, "captured": False,
            "request_path": str(destination), "request_sha256": sha(destination),
            "context_exists": context_exists, "context_verified": context_exists,
            "context_path": prepared["output"], "context_sha256": context_sha256,
            "command": None if context_exists else "python3 -B " + shlex.quote(str(TOOL)) + " " + shlex.quote(str(destination)),
            "next_action": "Use the verified existing context; do not capture or judge again." if context_exists
                           else "Execute command unchanged to publish the canonical context.",
            "model_calls": 0, "judge_calls": 0, "automatic_dispatch": False}


def prepare_seal_training(req, meta, mode):
    """Prepare metadata only; never publish an export or dispatch a learning role."""
    check(mode["mode"] == "training", "prepare-seal-training is training-only")
    check(set(req) == {"op", "trace_path", "role_requests"}, "prepare-seal-training takes only trace_path and role_requests")
    path, t = training_profile(meta["agent_state"])
    check(str(path) == mode["training_path"] and sha(path) == mode["training_sha256"], "training profile changed")
    private = Path(t["private_learning"])
    check(evo.regular(private / "frozen-training.json").read_bytes() == evo.regular(path).read_bytes(),
          "portable frozen training snapshot changed")
    check(isinstance(req["role_requests"], list) and req["role_requests"]
          and all(isinstance(path, str) for path in req["role_requests"]),
          "completed audit-role request paths required")
    roles = [evo.read(evo.regular(inside(path, private / "requests"))) for path in req["role_requests"]]
    check(all(isinstance(role, dict) and role.get("op") == "audit-role"
              and role.get("role") in ("reflector", "evolver") for role in roles),
          "completed Reflector/Evolver audit requests required")
    prepared = {"op": "seal-training", "private_learning": str(private), "train_ids": t["train_ids"],
        "contexts": [str(private / "contexts" / (task + ".json")) for task in t["train_ids"]],
        "candidates": str(private / "candidates.json"), "decisions": str(private / "decisions.json"),
        "library": str(private / "prebatch-library.json"), "skill": str(Path(t["answer_dir"]).parent / "skill"),
        "roles": roles, "runtime": t["runtime"], "limits": t["limits"],
        "max_entries": t["max_entries"], "max_bytes": t["max_bytes"], "output": str(private / "training-export.json")}
    destination = inside(private / "requests/seal-training.json", private / "requests")
    with evo.locked(private / "requests/.prepare-seal-training.lock"):
        if destination.exists():
            check(canonical(evo.read(evo.regular(destination))) == canonical(prepared),
                  "existing seal request differs; preserve it and explicitly correct metadata before sealing")
        else:
            check(not Path(prepared["output"]).exists(), "training export already exists; do not prepare a new seal request")
            evo.write_once(destination, evo.encoded(prepared))
    return {"op": "prepare-seal-training", "request_path": str(destination), "request_sha256": sha(destination),
            "command": "python3 -B " + shlex.quote(str(TOOL)) + " " + shlex.quote(str(destination)),
            "model_calls": 0, "judge_calls": 0, "automatic_dispatch": False}


def training_final(meta, mode):
    _, t = training_profile(meta["agent_state"])
    private = Path(t["private_learning"])
    request = evo.read(private / "requests/seal-training.json")
    check(Path(request["private_learning"]).resolve() == private.resolve(), "wrong private export")
    check(request["train_ids"] == t["train_ids"] and canonical(request["runtime"]) == canonical(t["runtime"]), "frozen training changed")
    check(sha(mode["training_path"]) == mode["training_sha256"], "training profile changed")
    check(Path(request["skill"]).resolve() == Path(t["answer_dir"]).parent / "skill", "wrong submission skill")
    check(Path(request["output"]).resolve() == private / "training-export.json", "wrong training export destination")
    for task in t["train_ids"]:
        completed_context(t, task, meta)
    # Sealing is write-once; independently re-run all validation into an ephemeral
    # destination and compare its deterministic record with the real export.
    import tempfile
    with tempfile.TemporaryDirectory(dir=private) as directory:
        verified = evo.seal_training({**request, "output": str(Path(directory) / "verified.json")})
    check(verified["training_root"]["session_id"] == meta["session_id"], "export belongs to another training root")
    check(evo.read(private / "training-export.json") == verified, "missing/invalid sealed training export")
    return {"complete": True, "skill_sha256": verified["skill_sha256"]}


def controller_final(mode):
    module = protocol_module(mode)
    request = evo.read(mode["completion_request"])
    verify_format_repairs(mode)
    accepted = freeze(mode, "final")
    h0_req = evo.read(request["h0_matrix_request"])
    config = evo.read(mode["config"])
    h0 = module.matrix(h0_req)
    check(h0["complete"] and h0_req["case_ids"] == accepted["test_case_ids"], "complete frozen H0 matrix required")
    check(h0_req["identity"]["expected_version"] == accepted["h0"]["version"], "H0 version mismatch")
    verify_worker_matrix(mode, "h0", h0_req)
    if accepted["equivalent_h0"]:
        check(request.get("final_policy") == "reused_equivalent_h0" and not request.get("final_matrix_request"), "valid no-op must explicitly reuse H0")
    else:
        final_req = evo.read(request["final_matrix_request"])
        check(module.matrix(final_req)["complete"] and final_req["case_ids"] == accepted["test_case_ids"], "complete frozen final matrix required")
        check(final_req["identity"]["expected_version"] == accepted["final"]["version"], "final version mismatch")
        verify_worker_matrix(mode, "final", final_req)
    for identity in [h0_req["identity"]] + ([] if accepted["equivalent_h0"] else [final_req["identity"]]):
        check(all(identity[k] == accepted["runtime"][k] for k in ("provider", "model_id", "thinking_level")), "test runtime mismatch")
        check(identity["agent_id"] == config["test_agent_id"].removeprefix("create:").strip(), "test matrix names another Target")
    for matrix_req in [h0_req] + ([] if accepted["equivalent_h0"] else [final_req]):
        check(matrix_req["runs"] == config["test_repeats"] == 1 and Path(matrix_req["evo_tool"]).resolve() == TOOL,
              "test matrix repetitions/helper differ from frozen protocol")
    frozen_request = evo.read(mode["freeze_request"])
    benchmark = Path(config["deployment"]["penguin_home"]) / config["deployment"]["project_id"] / "benchmarks" / config["test_benchmark_id"]
    injection = []
    for phase, matrix_path in [("h0", request["h0_matrix_request"])] + ([] if accepted["equivalent_h0"] else [("final", request["final_matrix_request"])]):
        receipt = module.execute({"op": "verify-injection-matrix", "evo_tool": str(TOOL),
            "config": mode["config"], "config_sha256": mode["config_sha256"],
            "matrix_request": matrix_path, "snapshot": frozen_request[phase + "_snapshot"],
            "runtime": config["runtime"], "expected_version": accepted[phase]["version"], "benchmark": str(benchmark)})
        check(receipt["complete"] is True, "actual frozen Skill injection is not verified")
        injection.append(receipt)
    check(request.get("host_cost_status") == "pending_final_request", "own final request cost must remain pending, not falsely settled")
    return {"complete": True, "equivalent_h0": accepted["equivalent_h0"], "host_cost_status": "pending_final_request", "injection": injection}


def block_goal(scratch, reason):
    path = scratch / "GOAL.json"
    if path.exists():
        goal = evo.read(path)
        if not goal.get("ended") and goal.get("status") in ("active", "complete", "wrapping_up"):
            goal["status"] = "blocked"
            evo.atomic(path, evo.encoded(goal))
    evo.atomic(scratch / "evo-acceptance.json", evo.encoded({"complete": False, "status": "method_failed", "reason": reason}))


def pending_stop(payload, meta, scratch, mode):
    """Defer a normal Task boundary using only the native Session's live state.

    Core owns and rechecks this snapshot. Waiting authorizes no new tool call and
    proves no completion; existing pre-tool and final evidence checks stay intact.
    Do not reconstruct liveness from traces, worker words or missing artifacts.
    """
    lifecycle = payload.get("lifecycle")
    if lifecycle is None:
        return None  # older native callers retain the terminal contract
    check(isinstance(lifecycle, dict) and set(lifecycle) == {
        "schema", "subagents", "commands", "notices", "cutoff", "aborted"}
        and type(lifecycle["schema"]) is int and lifecycle["schema"] == 1,
        "invalid native stop lifecycle")
    check(all(type(lifecycle[key]) is bool for key in ("cutoff", "aborted")), "invalid native stop cutoff")
    if lifecycle["cutoff"] or lifecycle["aborted"]:
        return None
    check(all(isinstance(lifecycle[key], list) for key in ("subagents", "commands", "notices")),
          "invalid native pending work lists")
    for child in lifecycle["subagents"]:
        check(isinstance(child, dict) and set(child) == {"session_id", "subagent_id", "running"}
              and isinstance(child["session_id"], str) and child["session_id"]
              and (child["subagent_id"] is None or isinstance(child["subagent_id"], str) and child["subagent_id"])
              and type(child["running"]) is bool, "invalid native child lifecycle")
    for command in lifecycle["commands"]:
        check(isinstance(command, dict) and set(command) == {"process_id", "running"}
              and isinstance(command["process_id"], str) and command["process_id"]
              and type(command["running"]) is bool, "invalid native command lifecycle")
    for notice in lifecycle["notices"]:
        check(isinstance(notice, dict) and set(notice) == {"kind", "id", "status"}
              and notice["kind"] in ("command", "subagent")
              and isinstance(notice["id"], str) and notice["id"]
              and notice["status"] in ("completed", "failed", "stopped"), "invalid native completion notice")
    running_children = sum(child["running"] and child["subagent_id"] is not None for child in lifecycle["subagents"])
    running_commands = sum(command["running"] for command in lifecycle["commands"])
    if not (running_children or running_commands or lifecycle["notices"]):
        return None
    if mode["mode"] == "training":
        path, profile = training_profile(meta["agent_state"])
        check(str(path) == mode["training_path"] and sha(path) == mode["training_sha256"]
              and profile["private_learning"] == mode["private_learning"], "frozen training changed while awaiting work")
    elif "config" in mode:
        check(sha(mode["config"]) == mode["config_sha256"], "frozen experiment config changed while awaiting work")
    goal_path = scratch / "GOAL.json"
    if goal_path.exists():
        goal = evo.read(evo.regular(goal_path))
        if goal.get("ended") or goal.get("status") not in ("active", "wrapping_up"):
            return None
        budget = goal.get("budget", -1)
        check(type(budget) in (int, float), "invalid native Goal budget")
        if budget > 0:
            # Let the existing Goal hook account/limit its token budget. This
            # revision's no-spin waiting scope is the independently supervised
            # dollar-budget experiment; no generic Goal accounting is changed.
            return {"reason": "Native Goal token-budget flow retained; Evo background waiting is not supported for a positive Goal token budget",
                    "output": {"awaiting_background": True, "native_goal_budget_flow": True}}
        if goal.get("status") != "active":
            return None
    return {"decision": "wait", "reason": "Awaiting this Session's native background work; its completion notices resume the coordinator",
            "output": {"awaiting_background": True, "running_workers": running_children,
                       "running_commands": running_commands, "queued_notices": len(lifecycle["notices"])}}


def hook(payload, state):
    event = payload["hook"]
    if not payload.get("trace_path"):
        return {"context": ""} if event == "user_prompt" else ({"decision": "deny", "reason": "Evo guard requires a native trace"} if event == "pre_tool_use" else {})
    meta, scratch, rows, mode = load_mode(payload["trace_path"], state)
    try:
        # Penguin currently skips hooks for native leaves. Preserve that behavior
        # if a future caller passes a child event; the parent validates its trace.
        if meta["source"] == "subagent":
            return {"context": ""} if event == "user_prompt" else {}
        if event == "user_prompt":
            if (Path(state) / "skills/evo-harness-rsi-toolkit/training.json").exists():
                return {"context": training_bootstrap(meta, scratch, state, payload["trace_path"])}
            prompt = payload.get("prompt", "")
            if mode is None and re.search(r"evo[-_ ]?harness", prompt, re.I) and re.search(r"GDPevo|task_group|continuous group", prompt, re.I):
                evo.atomic(scratch / MARKER, evo.encoded({"schema": 2, "mode": "controller", "configured": False, "session_id": meta["session_id"]}))
                mode = evo.read(scratch / MARKER)
            if mode is None:
                return {"context": ""}
            return {"context": f"Evo-Harness native guard is active. Exact trace_path: {payload['trace_path']}. Session scratchpad: {scratch}. Read {SKILL / 'SKILL.md'} and references/control.md in full. Before any native worker, run configure-controller via python3 -B {CONTROL} REQUEST.json with your current trace_path and frozen config. Register each exact native worker call with worker-request; no wrapper dispatches it. Missing/failed training evidence blocks every test and Goal completion. Never equate a failed batch with a valid empty compilation. Final host-request cost remains pending until after this Session stops."}
        if mode is None:
            return {}
        check(mode.get("mode") in ("controller", "training"), "Evo initialization is blocked")
        if event == "pre_tool_use":
            args = json.loads(payload["arguments"])
            name = payload["tool_name"]
            if name == "run_subagent":
                authorize_call(args, meta, scratch, mode, payload["tool_call_id"])
            elif name == "input_subagent":
                authorize_input(args, meta, scratch, mode, payload["tool_call_id"])
            elif mode["mode"] == "training":
                private = Path(mode["private_learning"])
                if name == "exec_command":
                    script, request_path, req = safe_helper(args, {CONTROL, TOOL})
                    inside(request_path, private / "requests")
                    if script == TOOL:
                        training_helper(req, meta, scratch, mode)
                    else:
                        check(req.get("op") in ("role-request", "status", "prepare-capture", "prepare-seal-training", "verify-training"), "invalid training coordinator operation")
                        check(Path(req["trace_path"]).resolve() == Path(payload["trace_path"]).resolve(), "request belongs to another native trace")
                elif name == "write_file":
                    inside(args["file_path"], private / "requests")
                elif name == "read_file":
                    path = Path(args["file_path"]).resolve()
                    check(path.is_relative_to(private) or path.is_relative_to(Path(state).resolve()), "coordinator reads only method/evidence; Solver reads business inputs")
                elif name == "input_command":
                    check(not any(args.get(key) for key in ("chars", "input", "text")), "coordinator cannot inject shell input")
                else:
                    check(False, "tool is not part of the training coordinator contract")
            elif name == "exec_command":
                # Host prepares native State/snapshots using existing tools. Harbor
                # and alternate model Sessions belong exclusively to native workers.
                command = args.get("cmd", args.get("command", ""))
                check(not host_executes_experiment(command), "host must delegate execution through registered native workers")
            return {}
        if event == "stop":
            waiting = pending_stop(payload, meta, scratch, mode)
            if waiting is not None:
                return waiting
            accepted = training_final(meta, mode) if mode["mode"] == "training" else controller_final(mode)
            evo.atomic(scratch / "evo-acceptance.json", evo.encoded(accepted))
            return {"decision": "stop", "reason": "Evo method evidence verified", "output": {"complete": True}}
        raise ValueError("unknown native hook")
    except (ValueError, KeyError, OSError, TypeError, IndexError) as error:
        reason = "Evo protocol check failed: " + str(error)
        if event == "pre_tool_use":
            return {"decision": "deny", "reason": reason}
        if event == "stop":
            block_goal(scratch, reason)
            return {"decision": "stop", "reason": reason, "output": {"complete": False, "status": "method_failed"}}
        evo.atomic(scratch / MARKER, evo.encoded({"schema": 2, "mode": "invalid", "session_id": meta["session_id"]}))
        return {"context": reason + ". Do not execute tasks; report the blocked initialization."}


def execute(req):
    meta, scratch, rows, mode = load_mode(req["trace_path"])
    check(mode is not None, "native Evo user-prompt hook must initialize this Session first")
    op = req["op"]
    if op == "configure-controller":
        return configure_controller(req, meta, scratch, rows)
    if op == "role-request":
        check(mode["mode"] == "training", "role-request is training-only")
        return role_request(req, meta, scratch, mode)
    if op == "worker-request":
        return worker_request(req, scratch, mode)
    if op == "validate-workers":
        check(set(req) == {"op", "trace_path", "phase"}, "validate-workers takes only trace_path and phase")
        return validate_workers(mode, req["phase"])
    if op == "format-request":
        return format_request(req, meta, scratch, mode)
    if op == "prepare-seal-training":
        return prepare_seal_training(req, meta, mode)
    if op == "prepare-capture":
        return prepare_capture(req, meta, scratch, mode)
    if op == "verify-training":
        return training_final(meta, mode)
    if op == "verify-completion":
        return controller_final(mode)
    if op == "status":
        trace_root = Path(meta["agent_state"]).parent / "traces"
        clock = {}
        if mode["mode"] == "training":
            _, t = training_profile(meta["agent_state"])
            started = evo.read(Path(t["private_learning"]) / "coordinator-clock.json")["started_unix"]
            elapsed = time.time() - started
            clock = {"elapsed_sec": elapsed, "solving_time_remaining_sec": max(0, t["solving_timeout_sec"] - elapsed),
                     "compilation_reserve_sec": t["compilation_reserve_sec"]}
        return {"mode": mode, "clock": clock, "native_trace_paths": [str(p) for p in sorted(trace_root.rglob("*.jsonl"))],
                "role_calls": sum(r.get("payload", {}).get("name") == "run_subagent" for r in rows),
                "automatic_dispatch": False}
    raise ValueError("unknown control operation")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("request", nargs="?")
    parser.add_argument("--hook", action="store_true")
    parser.add_argument("--state")
    args = parser.parse_args()
    try:
        result = hook(json.load(sys.stdin), args.state) if args.hook else execute(evo.read(args.request))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except Exception:
        # Wrapper converts this non-zero result to an explicit native denial.
        # Do not leak raw requests, credentials or provider/tool output in errors.
        print(json.dumps({"error": "Evo guard input or runtime failure"}), file=sys.stderr)
        sys.exit(2)
