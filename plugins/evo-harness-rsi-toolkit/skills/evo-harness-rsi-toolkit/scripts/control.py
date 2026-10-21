#!/usr/bin/env python3
"""Native Evo hook checks and single requested preparation operations.

This module never calls a model, spawns a role, runs Harbor, chooses a next
phase, retries an attempt, or authors a lesson. Penguin makes those decisions.
The guard is a protocol boundary for cooperative agents, not an OS sandbox.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import time

import evo

SKILL = Path(__file__).resolve().parents[1]
CONTROL = Path(__file__).resolve()
TOOL = CONTROL.with_name("evo.py")
MARKER = "EVO_CONTROL.json"
ROOT_OPS = {"read-material", "file-digest", "capture", "compile", "export", "fingerprint",
            "journal", "audit-role", "verify-package", "seal-training"}


def check(value, message):
    evo.require(value, message)


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
    for line in evo.regular(trace).read_text().splitlines():
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
        "Use role-request to prepare each requested native call; then invoke run_subagent with its exact returned call. "
        "Use status to locate original child traces; capture the Solver's canonical judge request/receipt. "
        "Only helpers run in this root's shell. Root write_file may write private request/evidence JSON, "
        "not submission, State, traces, hooks or role outputs. Empty input_subagent polls are allowed; "
        "new answer attempts and free-form steering are not. On missing evidence stop as method_failed. "
        "Zero candidates still requires one Evolver, compilation, export and seal.\n"
    )
    body = "\n\n".join(f"FULL METHOD MATERIAL {name}\n{text}" for name, text in material.items())
    return header + "\n" + json.dumps(t, ensure_ascii=False) + "\n\n" + body


def runtime_call(call, runtime):
    check(isinstance(call, dict) and isinstance(call.get("prompt"), str) and call["prompt"].strip(), "complete native call required")
    check(not call.get("agent_id"), "role workers inherit this Agent; do not switch Agent")
    for key in ("provider", "model_id", "thinking_level"):
        check(call.get(key) == runtime[key], "native worker runtime mismatch: " + key)


def protocol_module(mode):
    check(sha(mode["config"]) == mode["config_sha256"], "frozen experiment config changed")
    check(sha(mode["protocol"]) == mode["protocol_sha256"], "frozen benchmark protocol changed")
    check(sha(Path(mode["protocol"]).with_name("acceptance.py")) == mode["acceptance_sha256"], "frozen acceptance helper changed")
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


def freeze(mode, phase="final"):
    module = protocol_module(mode)
    config = evo.read(mode["config"])
    request = evo.read(mode["freeze_request"])
    check(request["runtime"] == config["runtime"] and request["test_case_ids"] == config["test_ids"], "freeze differs from authorized runtime/test roster")
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
             'Seal your assigned output using {"op":"file-digest","path":"ABS_OUTPUT"}. '
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
        text += f"\nRead full role file {role_file} and all granted text using read-material."
        text += f"\nFinal answer: {Path(t['answer_dir']) / (task + '.json')}\n"
        text += "Use judge exactly once via one standalone helper command. Never probe judge directly. "
        text += "After feedback, do not revise the answer or call the service again.\n"
        text += "Canonical judge paths: " + json.dumps({k: str(v) for k, v in paths.items()}, ensure_ascii=False)
        template = {"op": "judge", "split": "train", "train_ids": t["train_ids"], "task_id": task,
                    "attempt_id": t["attempt_id"], "answer": paths["answer"], "output": paths["output"],
                    "url": "REPLACE_WITH_LOCAL_TRAINING_API_BASE/api/judge"}
        text += "\nWrite this JSON to judge_request above (replace only URL with the public local training API): " + json.dumps(template)
        text += f"\nExecute exactly: python3 -B {shlex.quote(str(TOOL))} {shlex.quote(paths['judge_request'])}\n"
        text += "Judge stdout is a compact receipt. Afterwards read the canonical output feedback file fully with read-material chunks; never rerun judge to recover truncated output.\n"
        text += "\nBinary inputs (read via appropriate tools, do not fabricate text receipts): " + json.dumps(binary)
        slot = f"solver/{task}/{t['attempt_id']}"
    elif role == "reflector":
        context = completed_context(t, task, meta)
        check(context["negative"], "success produces no Reflector")
        out = private / "roles" / task / "candidates.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        text += f"\nTask/attempt: {task}/{t['attempt_id']}\nRead {role_file} and {private / 'contexts' / (task + '.json')} in full.\nOutput only to {out}; seal with file-digest.\n"
        slot = f"reflector/{task}/{t['attempt_id']}"
    else:
        contexts = [completed_context(t, task_id, meta) for task_id in t["train_ids"]]
        candidates = []
        for context in contexts:
            if context["negative"]:
                candidates.extend(evo.read(private / "roles" / context["task_id"] / "candidates.json"))
        target = private / "candidates.json"
        if target.exists():
            check(evo.read(target) == candidates, "candidate concatenation changed")
        else:
            evo.write_once(target, evo.encoded(candidates))
        materials = [str(role_file), str(target), str(private / "prebatch-library.json")]
        materials += [str(private / "contexts" / (task_id + ".json")) for task_id in t["train_ids"]]
        text += "\nRead all these files in full: " + json.dumps(materials)
        text += f"\nOutput operations to {private / 'decisions.json'} and seal with file-digest. Zero candidates requires [] and a real Evolver return.\n"
        slot = "evolver/batch-1"
    call = {"description": f"Evo-Harness {role}" + (f" {task}" if task else ""), "prompt": text,
            **{k: t["runtime"][k] for k in ("provider", "model_id", "thinking_level")}, "run_in_background": True}
    return write_ticket(scratch, mode, call, slot, role, {"task_id": task})


def worker_request(req, scratch, mode):
    check(mode["mode"] == "controller" and "config" in mode, "configure the controller before dispatch")
    config = evo.read(mode["config"])
    call, phase = req["call"], req["phase"]
    runtime_call(call, config["runtime"])
    check(phase in ("training", "h0", "final"), "unknown worker phase")
    benchmark = config["benchmark_id"] if phase == "training" else config["test_benchmark_id"]
    field = "trainer_agent_id" if phase == "training" else "test_agent_id"
    agent = config[field].removeprefix("create:").strip()
    def line(key):
        found = re.findall(r"^" + key + r":\s*(\S+)\s*$", call["prompt"], re.M)
        check(len(found) == 1, "worker prompt needs one unambiguous " + key)
        return found[0]
    check(line("benchmark_id") == benchmark and line("test_agent_id") == agent, "worker identity differs from frozen protocol")
    for key in ("provider", "model_id", "thinking_level"):
        check(line(key) == config["runtime"][key], "worker prompt runtime differs from frozen protocol")
    case, run = line("case_id"), line("run")
    check(run == "1", "one frozen repetition required")
    if phase == "training":
        check(line("training_protocol_version") == "1", "continuous group worker contract required")
        check(case.endswith("tg" + config["group"] + "-train"), "not the frozen continuous group")
    else:
        acceptance = freeze(mode, phase)
        check(case in acceptance["test_case_ids"], "test case is not frozen")
        check(line("expected_version") == str(acceptance[phase if phase == "h0" else "final"]["version"]), "wrong State version")
        if phase == "final":
            check(not acceptance["equivalent_h0"], "valid H0-equivalent compilation must reuse H0, not rerun")
            h0 = evo.read(Path(mode["completion_request"]).parent / "h0-matrix-request.json")
            check(protocol_module(mode).matrix(h0)["complete"], "finish the complete H0 matrix before final testing")
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
        if ticket["phase"] != "evolver":
            task = ticket["task_id"]
            for prior in t["train_ids"][:t["train_ids"].index(task)]:
                completed_context(t, prior, meta)
        else:
            for task in t["train_ids"]:
                completed_context(t, task, meta)
    claim = ticket_path(scratch, mode, args).parent / (evo.digest(ticket["slot"].encode()) + ".claimed.json")
    if claim.exists():
        check(evo.read(claim)["tool_call_id"] == call_id, "semantic slot already dispatched; reconcile its original child, never retry")
    else:
        evo.write_once(claim, evo.encoded({"slot": ticket["slot"], "tool_call_id": call_id, "request_sha256": evo.digest(canonical(args).encode())}))


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
        if "train_ids" in req:
            check(req["train_ids"] == t["train_ids"], "coordinator batch changed")
        for field in ("limits", "max_entries", "max_bytes"):
            if field in req:
                check(req[field] == t[field], "frozen method budget changed")


def training_final(meta, mode):
    _, t = training_profile(meta["agent_state"])
    private = Path(t["private_learning"])
    request = evo.read(private / "requests/seal-training.json")
    check(Path(request["private_learning"]).resolve() == private.resolve(), "wrong private export")
    check(request["train_ids"] == t["train_ids"] and request["runtime"] == t["runtime"], "frozen training changed")
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
    accepted = freeze(mode, "final")
    h0_req = evo.read(request["h0_matrix_request"])
    config = evo.read(mode["config"])
    h0 = module.matrix(h0_req)
    check(h0["complete"] and h0_req["case_ids"] == accepted["test_case_ids"], "complete frozen H0 matrix required")
    check(h0_req["identity"]["expected_version"] == accepted["h0"]["version"], "H0 version mismatch")
    if accepted["equivalent_h0"]:
        check(request.get("final_policy") == "reused_equivalent_h0" and not request.get("final_matrix_request"), "valid no-op must explicitly reuse H0")
    else:
        final_req = evo.read(request["final_matrix_request"])
        check(module.matrix(final_req)["complete"] and final_req["case_ids"] == accepted["test_case_ids"], "complete frozen final matrix required")
        check(final_req["identity"]["expected_version"] == accepted["final"]["version"], "final version mismatch")
    for identity in [h0_req["identity"]] + ([] if accepted["equivalent_h0"] else [final_req["identity"]]):
        check(all(identity[k] == accepted["runtime"][k] for k in ("provider", "model_id", "thinking_level")), "test runtime mismatch")
        check(identity["agent_id"] == config["test_agent_id"].removeprefix("create:").strip(), "test matrix names another Target")
    for matrix_req in [h0_req] + ([] if accepted["equivalent_h0"] else [final_req]):
        check(matrix_req["runs"] == config["test_repeats"] == 1 and Path(matrix_req["evo_tool"]).resolve() == TOOL,
              "test matrix repetitions/helper differ from frozen protocol")
    check(request.get("host_cost_status") == "pending_final_request", "own final request cost must remain pending, not falsely settled")
    return {"complete": True, "equivalent_h0": accepted["equivalent_h0"], "host_cost_status": "pending_final_request"}


def block_goal(scratch, reason):
    path = scratch / "GOAL.json"
    if path.exists():
        goal = evo.read(path)
        if not goal.get("ended") and goal.get("status") in ("active", "complete", "wrapping_up"):
            goal["status"] = "blocked"
            evo.atomic(path, evo.encoded(goal))
    evo.atomic(scratch / "evo-acceptance.json", evo.encoded({"complete": False, "status": "method_failed", "reason": reason}))


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
                check(not args.get("prompt", "").strip(), "free-form child steering/retries are forbidden; preserve failure and stop")
            elif mode["mode"] == "training":
                private = Path(mode["private_learning"])
                if name == "exec_command":
                    script, request_path, req = safe_helper(args, {CONTROL, TOOL})
                    inside(request_path, private / "requests")
                    if script == TOOL:
                        training_helper(req, meta, scratch, mode)
                    else:
                        check(req.get("op") in ("role-request", "status", "verify-training"), "invalid training coordinator operation")
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
                check(not re.search(r"\bharbor\s+(?:run|jobs?\s+run)|\bpenguin(?:-evo)?\s+(?:run|chat)\b", command), "host must delegate execution through registered native workers")
            return {}
        if event == "stop":
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
