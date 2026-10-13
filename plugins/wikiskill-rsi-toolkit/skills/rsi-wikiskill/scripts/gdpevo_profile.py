#!/usr/bin/env python3
"""Freeze the agreed GDPevo split; never run models or change benchmark files.

Verification inspects paths and hashes opaque file bytes, including private judge
assets. It never parses or emits task, answer, rubric, or verifier contents.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile


BENCHMARK_COMMIT = "e5d58d230bda928d9a98ae775c54c159af8f8363"
HOST_RSI_COMMIT = "4c9c1797156921525b420f7f6d4bfab788059ae7"
GROUPS = (("003", "CRM"), ("005", "ERP"), ("010", "Finance"),
          ("014", "Healthcare"), ("018", "Legal"), ("024", "Dataanalysis"))
ROLES = ("controller", "target", "evaluator", "wiki_maintainer",
         "skill_proposer", "companion_auditor")
THINKING_LEVELS = ("low", "medium", "high", "xhigh", "max")
TASK_FILES = ("task.toml", "instruction.md", "environment/Dockerfile",
              "environment/docker-compose.yaml", "environment/start.sh",
              "environment/workspace/environment_access.md",
              "environment/workspace/input/prompt.txt",
              "environment/workspace/input/payloads/answer_template.json",
              "solution/answer.json", "solution/solve.sh", "tests/score.json",
              "tests/score.py", "tests/test.sh")


class InvalidProfile(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidProfile(message)


def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def split_tasks():
    phases = {}
    for phase, source_split, indices in (("train", "train", range(1, 5)),
                                         ("validation", "train", (5,)),
                                         ("test", "test", range(1, 6))):
        benchmark_id = f"penguinharness-benchmark-gdpevo-{source_split}"
        tasks = []
        for group_index, (group, _) in enumerate(GROUPS):
            for index in indices:
                task_name = f"tg{group}-{source_split}-{index:03d}"
                case_id = f"CASE-{group_index * 5 + index:03d}-{task_name}"
                tasks.append({"group": group, "task_name": task_name,
                              "case_id": case_id,
                              "task_path": f"benchmarks/gdpevo/tasks/{task_name}",
                              "case_path": f"packages/{benchmark_id}/{case_id}"})
        phases[phase] = {"phase": phase, "source_split": source_split,
                         "benchmark_id": benchmark_id, "tasks": tasks}
    return phases


def no_symlink_components(path):
    path = Path(path).absolute()
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        require(not current.is_symlink(), f"symlink is forbidden: {current}")
    return path


def git(root, *args):
    result = subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            check=False)
    require(result.returncode == 0, f"git {' '.join(args[:1])} failed")
    return result.stdout


def raise_walk_error(error):
    raise error


def verify_source(benchmark_root, phases):
    root = no_symlink_components(benchmark_root).resolve()
    require(root.is_dir(), "benchmark root is not a directory")
    require(Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve() == root,
            "benchmark root must be the Git repository root")
    require(git(root, "rev-parse", "HEAD").decode().strip() == BENCHMARK_COMMIT,
            "benchmark HEAD does not match the frozen commit")
    require(not git(root, "status", "--porcelain", "--untracked-files=no"),
            "benchmark has tracked working-tree or index changes")
    tracked = {}
    for entry in git(root, "ls-tree", "-rz", "--full-tree", "HEAD").split(b"\0"):
        if not entry:
            continue
        metadata, path_bytes = entry.split(b"\t", 1)
        mode, kind, digest = metadata.decode("ascii").split()
        path = os.fsdecode(path_bytes)
        require(kind == "blob" and mode in ("100644", "100755"),
                f"unsupported Git object or symlink: {path}")
        tracked[path] = (mode, digest)

    # Reject symlinks anywhere in the checkout, including untracked paths.
    for directory, subdirs, files in os.walk(root, followlinks=False, onerror=raise_walk_error):
        for name in subdirs + files:
            path = Path(directory) / name
            require(not path.is_symlink(), f"symlink is forbidden: {path.relative_to(root)}")
        if Path(directory) == root:
            subdirs[:] = [name for name in subdirs if name != ".git"]

    tasks = [task for phase in phases.values() for task in phase["tasks"]]
    expected_names = {task["task_name"] for task in tasks}
    task_root = root / "benchmarks/gdpevo/tasks"
    require(task_root.is_dir() and {p.name for p in task_root.iterdir()} == expected_names,
            "Harbor task directory must contain exactly the 60 canonical tasks")
    for source_split in ("train", "test"):
        package = root / f"packages/penguinharness-benchmark-gdpevo-{source_split}"
        expected = {task["case_id"] for task in tasks
                    if f"-{source_split}-" in task["task_name"]}
        require(package.is_dir() and
                {p.name for p in package.iterdir() if p.name.startswith("CASE-")} == expected,
                f"Penguin {source_split} package does not match the 30 canonical cases")

    required, prefixes = set(), []
    for phase in phases.values():
        required.add(f"packages/{phase['benchmark_id']}/benchmark_config.toml")
        for task in phase["tasks"]:
            task_path, case_path = task["task_path"], task["case_path"]
            prefixes.extend((task_path + "/", case_path + "/"))
            required.update(f"{task_path}/{name}" for name in TASK_FILES)
            index = task["task_name"].rsplit("-", 1)[1]
            grader_path = f"{task_path}/tests/task_group/{phase['source_split']}_tasks/{index}"
            required.update(f"{grader_path}/{name}" for name in
                            ("eval/eval.sh", "output/answer.json"))
            required.update(f"{case_path}/{name}/README.md" for name in ("statement", "rubric"))
    require(required <= tracked.keys(), "required task/case structure is absent from the pinned tree")
    selected = {path for path in tracked if path in required or path.startswith(tuple(prefixes))}
    actual = set()
    for prefix in prefixes:
        folder = root / prefix
        require(folder.is_dir(), f"missing directory: {prefix}")
        for directory, _, files in os.walk(folder, onerror=raise_walk_error):
            actual.update((Path(directory) / name).relative_to(root).as_posix() for name in files)
    actual.update(path for path in required if path.endswith("/benchmark_config.toml"))
    require(actual == selected, "selected task/case trees contain missing or untracked files")

    records = []
    for relative in sorted(selected):
        path = root / relative
        info = path.stat()
        require(stat.S_ISREG(info.st_mode), f"not a regular file: {relative}")
        blob = hashlib.sha1(f"blob {info.st_size}\0".encode("ascii"))
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                blob.update(block)
                digest.update(block)
        mode, expected_blob = tracked[relative]
        require(blob.hexdigest() == expected_blob, f"file bytes differ from the pinned tree: {relative}")
        require(bool(info.st_mode & 0o111) == (mode == "100755"),
                f"file executable mode differs from the pinned tree: {relative}")
        records.append([relative, mode, expected_blob, digest.hexdigest()])
    require(not git(root, "status", "--porcelain", "--untracked-files=no"),
            "benchmark changed during verification")
    require(git(root, "rev-parse", "HEAD").decode().strip() == BENCHMARK_COMMIT,
            "benchmark HEAD changed during verification")
    return {"method": "pinned_git_and_opaque_file_hashes", "file_count": len(records),
            "source_tree_sha256": hashlib.sha256(canonical_bytes(records)).hexdigest()}


def validate_role_models(value):
    """Validate user-selected runtimes without imposing a vendor or model."""
    require(isinstance(value, dict) and set(value) == set(ROLES),
            "role_models must specify exactly the six experiment roles")
    result = {}
    for role, runtime in value.items():
        require(isinstance(runtime, dict) and
                set(runtime) == {"provider", "model_id", "thinking_level"},
                f"{role} runtime must contain provider, model_id and thinking_level only")
        for key in ("provider", "model_id"):
            item = runtime[key]
            require(isinstance(item, str) and item and not item.startswith("-") and
                    all(character.isprintable() and not character.isspace() for character in item),
                    f"{role} {key} must be a nonempty ID without whitespace or control characters")
        require("/" not in runtime["provider"], "provider ID cannot contain a slash")
        require(runtime["thinking_level"] in THINKING_LEVELS,
                f"{role} thinking_level must be supported by the Harbor adapter")
        result[role] = dict(runtime)
    return result


def build_profile(iterations=2, benchmark_root=None, role_models=None):
    require(type(iterations) is int and iterations >= 1, "iterations must be a positive integer")
    # The fallback runtime of a profile built without one. This is a default, not a
    # constraint: explicit runtimes are retained in the experiment's own hash.
    if role_models is None:
        model = {"provider": "deepseek", "model_id": "deepseek-flash", "thinking_level": "xhigh"}
        role_models = {role: dict(model) for role in ROLES}
    role_models = validate_role_models(role_models)
    phases = split_tasks()
    verification = verify_source(benchmark_root, phases) if benchmark_root is not None else None
    profile = {
        "schema_version": 1, "algorithm": "wikiskill", "benchmark": "GDPevo",
        "source_refs": {"benchmark_commit": BENCHMARK_COMMIT, "host_rsi_commit": HOST_RSI_COMMIT},
        "source_verified": verification is not None, "source_verification": verification,
        "groups": [{"id": group, "domain": domain} for group, domain in GROUPS],
        "config": {"max_iterations": iterations, "iteration_budget_basis": "initial_exploration_not_paper_default",
                   "runs_per_case": 1, "replicas": ["r1", "r2", "r3"],
                   "independent_replicas": True, "initial_skills": "empty", "initial_wiki": "empty",
                   "share_learned_state": False,
                   "role_models": role_models},
        "phases": phases,
    }
    profile["profile_sha256"] = hashlib.sha256(canonical_bytes(profile)).hexdigest()
    return profile


def write_profile(out, profile):
    out = no_symlink_components(out)
    data = canonical_bytes(profile)
    require(out.parent.is_dir(), "output parent directory does not exist")
    if out.exists():
        require(out.is_file() and out.read_bytes() == data,
                "refusing to overwrite an existing different output")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=out.parent, prefix=".gdpevo-profile-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, out)
        except FileExistsError:
            require(not out.is_symlink() and out.is_file() and out.read_bytes() == data,
                    "refusing to overwrite an existing different output")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--benchmark-root", type=Path)
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--role-models", type=Path, help="JSON object with all six role runtimes")
    parser.add_argument("--provider", help="Penguin provider group for all roles")
    parser.add_argument("--model-id", help="Exact model ID in that provider group")
    parser.add_argument("--thinking", choices=THINKING_LEVELS)
    args = parser.parse_args()
    explicit = any(item is not None for item in (args.provider, args.model_id, args.thinking))
    if args.role_models is not None and explicit:
        parser.error("--role-models cannot be combined with --provider, --model-id or --thinking")
    if explicit and (args.provider is None or args.model_id is None):
        parser.error("--provider and --model-id must be supplied together")
    try:
        role_models = None
        if args.role_models is not None:
            def unique_keys(pairs):
                value = {}
                for key, item in pairs:
                    require(key not in value, "duplicate JSON key in role_models")
                    value[key] = item
                return value
            role_models = json.loads(no_symlink_components(args.role_models).read_text(encoding="utf-8"),
                                     object_pairs_hook=unique_keys)
            validate_role_models(role_models)
        elif explicit:
            runtime = {"provider": args.provider, "model_id": args.model_id,
                       "thinking_level": args.thinking or "xhigh"}
            role_models = {role: dict(runtime) for role in ROLES}
        profile = build_profile(args.iterations, args.benchmark_root, role_models)
        if args.benchmark_root is not None:
            root = Path(args.benchmark_root).resolve()
            require(not no_symlink_components(args.out).resolve().is_relative_to(root),
                    "output must be outside the benchmark checkout")
        write_profile(args.out, profile)
    except (InvalidProfile, OSError, UnicodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"out": str(args.out), "profile_sha256": profile["profile_sha256"],
                      "source_verified": profile["source_verified"],
                      "counts": {name: len(phase["tasks"]) for name, phase in profile["phases"].items()}},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
