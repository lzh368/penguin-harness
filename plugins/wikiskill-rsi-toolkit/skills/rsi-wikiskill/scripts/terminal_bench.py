#!/usr/bin/env python3
"""Prepare an external Terminal-Bench experiment using Penguin's Harbor adapter.

Offline only: parse task metadata, hash opaque source files, freeze a reproducible
split, and print phase plans or one-cell commands. Never reads answer contents
as text, starts a model/container, imports benchmark code, or certifies isolation.
Run this in the evaluator/experiment preparation context, not a learning role.
"""
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tomllib

sys.dont_write_bytecode = True


def sibling(name):
    spec = importlib.util.spec_from_file_location("terminal_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


schema = sibling("experiment_spec")


def binding():
    return sibling("harbor_spec")


DEFAULT_REPOSITORY = "https://github.com/harbor-framework/terminal-bench"
DEFAULT_REVISION = "452bf305c6daa62fc59061d22133a7cbc7c1572e"
RESOURCE_FIELDS = ("cpus", "memory_mb", "storage_mb", "gpus", "build_timeout_sec")
PHASES = ("train", "validation", "test")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git(root, *args):
    process = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    require(process.returncode == 0, "Git source verification failed")
    return process.stdout.decode("utf-8").strip()


def verify_checkout(root, revision):
    root = schema.safe_path(root).resolve()
    require(root.is_dir(), "source root must be a directory")
    require(Path(git(root, "rev-parse", "--show-toplevel")).resolve() == root,
            "source root must be the Git repository root")
    require(git(root, "rev-parse", "HEAD") == revision, "source HEAD differs from the declared revision")
    require(not git(root, "status", "--porcelain", "--untracked-files=no"),
            "source has tracked changes; restore the pinned source before preparation")
    return root


def resources(section):
    """Never expose environment variables, verifier commands, or private tokens."""
    return {key: section[key] for key in RESOURCE_FIELDS
            if type(section.get(key)) in (int, float)}


def inventory(source_root, revision=DEFAULT_REVISION, category="Software"):
    root = verify_checkout(source_root, revision)
    folder = root / "tasks"
    require(folder.is_dir() and not folder.is_symlink(), "expected a tasks/ directory in the source")
    items = []
    for directory in sorted(folder.iterdir()):
        require(not directory.is_symlink(), "task directory cannot be a symlink")
        if not directory.is_dir():
            continue
        metadata_path = directory / "task.toml"
        if not metadata_path.is_file():
            continue
        require(not metadata_path.is_symlink(), "task metadata cannot be a symlink")
        metadata = tomllib.loads(metadata_path.read_text(encoding="utf-8"))
        annotation = metadata.get("metadata", {})
        if annotation.get("category") != category:
            continue
        schema.identifier(directory.name, "original task ID")
        environment = metadata.get("environment", {})
        verifier = metadata.get("verifier", {})
        subcategory = annotation.get("subcategory", "Unspecified")
        require(isinstance(subcategory, str) and subcategory.strip(), "invalid subcategory metadata")
        item = {
            "original_id": directory.name,
            "path": directory.relative_to(root).as_posix(),
            "category": category,
            "subcategory": subcategory,
            "environment": resources(environment),
            "verifier_environment": resources(verifier.get("environment", {})),
            "verifier_environment_mode": verifier.get("environment_mode", "shared"),
            "has_compose": any((directory / "environment" / name).is_file()
                               for name in ("docker-compose.yaml", "docker-compose.yml", "compose.yaml", "compose.yml")),
            "declares_builtin_skills": bool(environment.get("skills_dir")),
            "environment_network_mode": environment.get("network_mode", "public"),
            "agent_network_mode": metadata.get("agent", {}).get("network_mode"),
            "verifier_network_mode": verifier.get("network_mode"),
        }
        for key, section in (("agent_timeout_seconds", metadata.get("agent", {})),
                             ("verifier_timeout_seconds", verifier)):
            if type(section.get("timeout_sec")) in (int, float):
                item[key] = section["timeout_sec"]
        items.append(item)
    require(items, "no tasks match the requested metadata category")
    verify_checkout(root, revision)
    return items


def rank(seed, *parts):
    return hashlib.sha256(json.dumps([str(seed), *parts], ensure_ascii=False).encode("utf-8")).hexdigest()


def stratify(items, counts=(8, 5, 5), seed="20261015"):
    """Deterministic capacity-constrained category balancing, never score based.

    Exact counts take precedence over proportional subcategory representation.
    Small strata cannot be represented in every phase. This is a draft split,
    not a claim of equal difficulty, semantic deduplication, or an official split.
    """
    require(len(counts) == 3 and all(type(count) is int and count > 0 for count in counts),
            "split must have three positive counts")
    require(sum(counts) == len(items), "split counts must sum to the selected task count")
    require(counts[0] >= 4, "the Proposer protocol needs at least four train tasks")
    quotas = dict(zip(PHASES, counts))
    left = dict(quotas)
    groups = defaultdict(list)
    for item in items:
        groups[item["subcategory"]].append(item["original_id"])
    phases = {phase: [] for phase in PHASES}
    for name in sorted(groups, key=lambda value: (-len(groups[value]), rank(seed, value))):
        group = sorted(groups[name], key=lambda task: rank(seed, name, task))
        assigned = Counter()
        for task in group:
            candidates = [phase for phase in PHASES if left[phase]]
            phase = max(candidates, key=lambda candidate: (
                Fraction(len(group) * quotas[candidate], len(items)) - assigned[candidate],
                Fraction(left[candidate], quotas[candidate]), rank(seed, name, task, candidate)))
            phases[phase].append(task)
            assigned[phase] += 1
            left[phase] -= 1
    return {phase: sorted(tasks) for phase, tasks in phases.items()}


def build_spec(source_root, *, repository=DEFAULT_REPOSITORY, revision=DEFAULT_REVISION,
               category="Software", counts=(8, 5, 5), seed="20261015", split=None,
               provider, model_id, thinking, iterations=1, concurrency=10,
               cell_timeout=1500, max_turns=200, replicas=("r1",)):
    root = verify_checkout(source_root, revision)
    items = inventory(root, revision, category)
    require(thinking in ("low", "medium", "high", "xhigh", "max"),
            "thinking must be supported by the existing PenguinAgent adapter")
    require(type(cell_timeout) is int and cell_timeout >= 60,
            "PenguinAgent run timeout must be at least 60 seconds")
    require(all(type(item.get("agent_timeout_seconds")) in (int, float)
                and item["agent_timeout_seconds"] >= cell_timeout + 210 for item in items),
            "every task must declare an agent timeout with room for adapter abort/cleanup")
    if split is None:
        split = stratify(items, counts, seed)
    schema.exact(split, set(PHASES), "explicit original-ID split")
    originals = {item["original_id"] for item in items}
    flat = [task for phase in PHASES for task in split[phase]]
    require(len(flat) == len(set(flat)) and set(flat) == originals,
            "explicit split must assign each selected original task exactly once")
    model = {"provider": provider, "model_id": model_id, "thinking_level": thinking}
    tasks = [{"case_id": item["original_id"], "original_id": item["original_id"], "path": item["path"],
              "content_sha256": binding().task_digest(root, item["path"]),
              "category": category, "duplicate_family": None} for item in items]
    value = {
        "schema_version": 1, "algorithm": "wikiskill",
        "source": {"repository": repository, "revision": revision},
        "adapter": {"id": "penguin-harbor", "version": "0.23.0"},
        "tasks": tasks, "phases": split, "learning_unit": {"id": "terminal-bench-software"},
        "config": {"train_feedback_mode": "score_only", "role_models": {role: dict(model) for role in schema.ROLES},
                   "max_iterations": iterations, "runs_per_case": 1, "replicas": list(replicas),
                   "independent_replicas": True, "concurrency": concurrency,
                   "cell_timeout_seconds": cell_timeout, "max_turns": max_turns},
        "scoring": {"metric": "reward", "raw_min": 0, "raw_max": 1,
                    "direction": "higher_is_better", "normalization": "linear_to_0_100",
                    "aggregation": "mean", "success_rule": "normalized_score_equals_100"}}
    frozen = schema.freeze(value)
    verify_checkout(root, revision)
    binding().verify_source(frozen, root)
    return frozen, items


def phase_plan(frozen, phase, agent_id, expected_version, smoke_case=None):
    schema.validate_frozen(frozen)
    require(phase in PHASES, "invalid phase")
    require(re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", agent_id), "invalid Target agent ID")
    require(type(expected_version) is int and expected_version > 0, "invalid State version")
    cases = list(frozen["spec"]["phases"][phase])
    if smoke_case is not None:
        require(phase == "train" and smoke_case in cases, "smoke must use one declared train task")
        cases = [smoke_case]
    runtime = frozen["spec"]["config"]["role_models"]["target"]
    return {"phase": phase, "benchmark_id": binding().benchmark_id(frozen), "case_ids": cases,
            "runs_per_case": frozen["spec"]["config"]["runs_per_case"], "agent_id": agent_id,
            "expected_version": expected_version, **runtime}


def command(frozen, source_root, case_id, *, adapter_root, agent_id, state_archive,
            bundle, host_home, project_id, api_host, job_name, jobs_dir):
    """A one-cell argv for the existing runner; callers orchestrate phase limits."""
    binding().verify_source(frozen, source_root)
    spec = frozen["spec"]
    tasks = {item["case_id"]: item for item in spec["tasks"]}
    require(case_id in tasks, "case is absent from the frozen task catalog")
    require(re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", agent_id), "invalid Target agent ID")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", job_name), "job name must be a safe unique identifier")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", api_host) and ".." not in api_host,
            "allow-agent-host must name one concrete API host, without a URL, port or wildcard")
    adapter_root = schema.safe_path(adapter_root).resolve()
    require((adapter_root / "agents/penguin_agent/agent.py").is_file(), "existing PenguinAgent adapter not found")
    state_archive, bundle, host_home, jobs_dir = [schema.safe_path(path).resolve()
                                               for path in (state_archive, bundle, host_home, jobs_dir)]
    require(state_archive.is_file() and bundle.is_file() and host_home.is_dir(),
            "State archive, install bundle and host configuration directory must already exist")
    runtime = spec["config"]["role_models"]["target"]
    cfg = spec["config"]
    require(cfg["cell_timeout_seconds"] >= 60, "PenguinAgent run timeout must be at least 60 seconds")
    task = Path(source_root).resolve() / tasks[case_id]["path"]
    task_metadata = tomllib.loads((task / "task.toml").read_text(encoding="utf-8"))
    outer_timeout = task_metadata.get("agent", {}).get("timeout_sec")
    require(type(outer_timeout) in (int, float) and outer_timeout >= cfg["cell_timeout_seconds"] + 210,
            "task agent timeout must leave run_timeout plus adapter abort/cleanup margin")
    environment_mode = task_metadata.get("environment", {}).get("network_mode", "public")
    agent_mode = task_metadata.get("agent", {}).get("network_mode", environment_mode)
    argv = ["uvx", "--from", "harbor==0.23.0", "harbor", "run", "-p", str(task.parent),
            "-i", task.name, "-a", "penguin_agent:PenguinAgent", "-m", runtime["provider"] + "/" + runtime["model_id"]]
    options = {"thinking": runtime["thinking_level"], "penguin_version": "0.2.13",
               "run_timeout": str(cfg["cell_timeout_seconds"]) + "s",
               "host_penguin_home": str(host_home), "host_project_id": project_id,
               "agent_id": agent_id, "agent_state_tar": str(state_archive), "install_bundle": str(bundle)}
    for key, value in options.items():
        argv.extend(["--ak", f"{key}={value}"])
    argv.extend(["--allow-agent-host", api_host, "--agent-setup-timeout-multiplier", "2.5",
                 "-k", "1", "-n", "1", "--job-name", job_name, "-o", str(jobs_dir), "-y"])
    return {"argv": argv, "environment": {"PYTHONPATH": str(adapter_root / "agents")},
            "shell": "PYTHONPATH=" + shlex.quote(str(adapter_root / "agents")) + " " + shlex.join(argv),
            "executed": False, "spec_sha256": frozen["spec_sha256"],
            "source_verified": True, "runtime_verified": False,
            "network_preflight_required": True, "source_agent_network_mode": agent_mode,
            "required_state_max_turns": cfg["max_turns"],
            "notes": ["Run as evaluator only. Keep benchmark source and verifier assets out of learning-role workspaces.",
                      "Do not launch until network isolation is independently verified. --allow-agent-host does not restrict public network mode.",
                      "Freeze max_turns in the supplied State archive; do not pass --ak max_turns, which mutates the deployed State version.",
                      "One job may have many services and a separate verifier. Schedule by declared resources, not container count.",
                      "Verify source/runtime/network/State bindings and score through the common receipt converter.",
                      "No dollar cap is enforced by this command; use the campaign cost guard and provider limit."]}


def write_json(filename, value):
    path = schema.safe_path(filename)
    data = schema.canonical(value)
    if path.exists():
        require(path.is_file() and path.read_bytes() == data, "refusing to overwrite a different output")
        return
    with path.open("xb") as stream:
        stream.write(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    inspect = commands.add_parser("inventory", help="show safe metadata only from a pinned checkout")
    prepare = commands.add_parser("prepare", help="freeze an external experiment; never launches a runner")
    for item in (inspect, prepare):
        item.add_argument("--source-root", type=Path, required=True)
        item.add_argument("--revision", default=DEFAULT_REVISION)
        item.add_argument("--category", default="Software")
    prepare.add_argument("--repository", default=DEFAULT_REPOSITORY)
    prepare.add_argument("--split", default="8,5,5", help="train,validation,test counts for metadata stratification")
    prepare.add_argument("--split-file", type=Path, help="explicit phase lists of original task IDs, instead of automatic stratification")
    prepare.add_argument("--seed", default="20261015")
    prepare.add_argument("--provider", required=True)
    prepare.add_argument("--model-id", required=True)
    prepare.add_argument("--thinking", required=True)
    prepare.add_argument("--iterations", type=int, default=1)
    prepare.add_argument("--concurrency", type=int, default=10)
    prepare.add_argument("--cell-timeout", type=int, default=1500)
    prepare.add_argument("--max-turns", type=int, default=200)
    prepare.add_argument("--replica", action="append", default=None)
    prepare.add_argument("--output", type=Path, required=True)
    plan = commands.add_parser("plan", help="emit an exact nine-field phase plan for the common converter")
    run = commands.add_parser("command", help="print a single-cell Harbor/PenguinAgent argv; does not run it")
    for item in (plan, run):
        item.add_argument("--spec", type=Path, required=True)
        item.add_argument("--source-root", type=Path, required=True)
        item.add_argument("--agent-id", required=True)
    plan.add_argument("--phase", choices=PHASES, required=True)
    plan.add_argument("--expected-version", type=int, required=True)
    plan.add_argument("--smoke-case")
    plan.add_argument("--output", type=Path, required=True)
    run.add_argument("--case-id", required=True)
    for name in ("adapter-root", "state-archive", "bundle", "host-home", "jobs-dir"):
        run.add_argument("--" + name, type=Path, required=True)
    run.add_argument("--project-id", default="default_project")
    run.add_argument("--api-host", required=True)
    run.add_argument("--job-name", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "inventory":
            output = {"tasks": inventory(args.source_root, args.revision, args.category),
                      "revision": args.revision, "runtime_verified": False}
        elif args.action == "prepare":
            counts = tuple(int(part) for part in args.split.split(","))
            frozen, items = build_spec(args.source_root, repository=args.repository, revision=args.revision,
                category=args.category, counts=counts, seed=args.seed,
                split=schema.read_json(args.split_file) if args.split_file else None,
                provider=args.provider, model_id=args.model_id, thinking=args.thinking,
                iterations=args.iterations, concurrency=args.concurrency, cell_timeout=args.cell_timeout,
                max_turns=args.max_turns, replicas=args.replica or ("r1",))
            schema.write_frozen(args.output, frozen)
            output = {"spec_sha256": frozen["spec_sha256"], "source_verified": True, "runtime_verified": False,
                      "phases": frozen["spec"]["phases"], "tasks": items,
                      "split_method": "explicit_original_ids" if args.split_file else "seeded_subcategory_capacity_balance",
                      "seed": None if args.split_file else args.seed,
                      "scope": "custom software subset experiment; not an official Terminal-Bench leaderboard score"}
        else:
            frozen = binding().load(args.spec, args.source_root)
            if args.action == "plan":
                output = phase_plan(frozen, args.phase, args.agent_id, args.expected_version, args.smoke_case)
                write_json(args.output, output)
            else:
                output = command(frozen, args.source_root, args.case_id, adapter_root=args.adapter_root,
                    agent_id=args.agent_id, state_archive=args.state_archive, bundle=args.bundle,
                    host_home=args.host_home, project_id=args.project_id, api_host=args.api_host,
                    job_name=args.job_name, jobs_dir=args.jobs_dir)
        print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        print("Terminal-Bench preparation refused; check pinned source, split, explicit runtime and output paths", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
