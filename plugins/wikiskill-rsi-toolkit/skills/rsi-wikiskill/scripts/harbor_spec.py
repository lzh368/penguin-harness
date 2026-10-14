#!/usr/bin/env python3
"""Bind existing Penguin/Harbor evaluations to an external frozen task catalog.

Evaluator-side only: task bytes are hashed, never returned as learning content.
This checks source identity and configuration, not container isolation or scores.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

sys.dont_write_bytecode = True


def sibling(name):
    spec = importlib.util.spec_from_file_location("harbor_spec_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


spec_tool = sibling("experiment_spec")
require = spec_tool.require
PLAN_FIELDS = {"phase", "benchmark_id", "case_ids", "runs_per_case", "agent_id",
               "expected_version", "provider", "model_id", "thinking_level"}


def task_path(root, relative):
    root = spec_tool.safe_path(root)
    spec_tool.relative_path(relative)
    path = spec_tool.safe_path(root / relative)
    require(path.is_dir(), "task directory is missing")
    return path


def task_digest(root, relative):
    """SHA256 of sorted JSON records [kind, relative path, executable, digest].

    Internal symlinks are recorded as links, with their targets constrained to
    this task. Their real target files also occur in the sorted tree. Escape,
    dangling links, device files and directory symlink loops are refused.
    """
    path = task_path(root, relative)
    records = []
    for item in sorted(path.rglob("*")):
        name = item.relative_to(path).as_posix()
        info = item.lstat()
        if item.is_symlink():
            try:
                resolved = item.resolve(strict=True)
                resolved.relative_to(path)
            except (ValueError, OSError, RuntimeError):
                raise ValueError("task symlink escapes, loops or has no target") from None
            require(resolved.is_file(), "only internal file symlinks are supported")
            records.append(["L", name, os.readlink(item)])
        elif stat.S_ISDIR(info.st_mode):
            records.append(["D", name])
        else:
            require(stat.S_ISREG(info.st_mode), "task contains a non-regular file")
            digest = hashlib.sha256()
            with item.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            records.append(["F", name, bool(info.st_mode & 0o111), digest.hexdigest()])
    require(records, "task tree is empty")
    return hashlib.sha256(spec_tool.canonical(records)).hexdigest()


def verify_source(frozen, root):
    spec_tool.validate_frozen(frozen)
    spec = frozen["spec"]
    require(spec["adapter"] == {"id": "penguin-harbor", "version": "0.23.0"},
            "unsupported execution adapter; use the existing Penguin Harbor 0.23.0 adapter")
    require(spec["config"]["train_feedback_mode"] == "score_only",
            "this execution adapter only exports score-only training feedback")
    root = spec_tool.safe_path(root)
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            capture_output=True, text=True, timeout=10)
    require(result.returncode == 0 and result.stdout.strip() == spec["source"]["revision"],
            "source checkout HEAD differs from the frozen revision")
    paths = [task["path"] for task in spec["tasks"]]
    result = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all", "--", *paths],
                            capture_output=True, text=True, timeout=10)
    require(result.returncode == 0 and not result.stdout.strip(), "selected source task trees are not clean")
    for task in spec["tasks"]:
        require(task_digest(root, task["path"]) == task["content_sha256"],
                "source task content differs from the frozen fingerprint")
    return frozen


def load(filename, source_root):
    require(source_root is not None, "an explicit source root is required")
    value = spec_tool.validate_frozen(spec_tool.read_json(filename))
    return verify_source(value, source_root)


def benchmark_id(frozen):
    return "wikiskill-" + frozen["spec_sha256"][:16]


def case_task(frozen, case_id):
    matches = [task for task in frozen["spec"]["tasks"] if task["case_id"] == case_id]
    require(len(matches) == 1, "case is absent from the frozen catalog")
    return matches[0]


def validate_plan(frozen, plan, purpose="evaluation"):
    spec_tool.exact(plan, PLAN_FIELDS, "phase plan")
    spec = frozen["spec"]
    require(purpose in ("evaluation", "smoke"), "unknown evaluation purpose")
    phase = plan["phase"]
    require(phase in spec_tool.PHASES, "unknown phase")
    require(plan["benchmark_id"] == benchmark_id(frozen), "plan belongs to another experiment")
    cases = plan["case_ids"]
    require(isinstance(cases, list) and cases and all(isinstance(x, str) for x in cases),
            "plan must list task identities")
    if purpose == "smoke":
        require(phase == "train" and len(cases) == 1 and cases[0] in spec["phases"]["train"],
                "smoke must use exactly one declared train task")
        require(type(plan["runs_per_case"]) is int and plan["runs_per_case"] == 1,
                "smoke must run once")
    else:
        require(cases == spec["phases"][phase], "formal plan must cover the exact frozen phase in order")
        require(type(plan["runs_per_case"]) is int and plan["runs_per_case"] == spec["config"]["runs_per_case"],
                "runs per case differ from the frozen experiment")
    runtime = spec["config"]["role_models"]["target"]
    for key in ("provider", "model_id", "thinking_level"):
        require(plan[key] == runtime[key], "plan runtime differs from the frozen Target runtime")
    spec_tool.identifier(plan["agent_id"], "Target identity")
    spec_tool.positive(plan["expected_version"], "State version")
    return plan
