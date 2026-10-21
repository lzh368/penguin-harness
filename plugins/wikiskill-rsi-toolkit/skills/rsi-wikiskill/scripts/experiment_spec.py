#!/usr/bin/env python3
"""Validate and freeze a benchmark-independent WikiSkill experiment plan.

This is a planning tool, not a runner or an adapter registry. Source hashes,
runtime choices and adapter capabilities are declarations, not verified facts.
It reads only the supplied JSON; it never reads tasks, answers or credentials,
contacts a service, divides data automatically, or changes a legacy profile.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

ROLES = {"controller", "target", "evaluator", "wiki_maintainer",
         "skill_proposer", "companion_auditor"}
PHASES = ("train", "validation", "test")
HASH = re.compile(r"[0-9a-f]{64}\Z")
REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
SPEC_FIELDS = {"schema_version", "algorithm", "source", "adapter", "tasks",
               "phases", "learning_unit", "config", "scoring"}
CONFIG_FIELDS = {"train_feedback_mode", "role_models", "max_iterations",
                 "runs_per_case", "replicas", "independent_replicas",
                 "concurrency", "cell_timeout_seconds", "max_turns"}
TASK_FIELDS = {"case_id", "original_id", "path", "content_sha256", "category",
               "duplicate_family"}
SCORING_FIELDS = {"metric", "raw_min", "raw_max", "direction", "normalization",
                  "aggregation", "success_rule"}
FROZEN_FIELDS = {"schema_version", "status", "spec", "spec_sha256"}


class InvalidSpec(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidSpec(message)


def exact(value, fields, label):
    require(isinstance(value, dict) and set(value) == fields,
            f"{label} must contain exactly its documented fields")


def identifier(value, label):
    require(isinstance(value, str) and IDENTIFIER.fullmatch(value),
            f"{label} must be a 1..128 character ASCII identifier")


def text(value, label, spaces=False):
    require(isinstance(value, str) and 0 < len(value) <= 512 and value == value.strip()
            and all(c.isprintable() and (spaces or not c.isspace()) for c in value),
            f"{label} must be nonempty text without control characters")


def positive(value, label):
    require(type(value) is int and value > 0, f"{label} must be a positive integer")


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def relative_path(value):
    text(value, "task path")
    require("\\" not in value and ":" not in value, "task path must use repository-relative POSIX syntax")
    path = PurePosixPath(value)
    require(not path.is_absolute() and all(part not in ("", ".", "..") for part in value.split("/"))
            and str(path) == value, "task path must be canonical and cannot escape the repository")


def validate_spec(value):
    exact(value, SPEC_FIELDS, "experiment spec")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1,
            "unsupported experiment spec version")
    require(value["algorithm"] == "wikiskill", "algorithm must be wikiskill")
    source = value["source"]
    exact(source, {"repository", "revision"}, "source")
    text(source["repository"], "repository URL")
    try:
        url = urlsplit(source["repository"])
        hostname, _port = url.hostname, url.port
    except ValueError:
        raise InvalidSpec("invalid repository URL") from None
    require(url.scheme == "https" and hostname and url.path not in ("", "/")
            and not url.username and not url.password and not url.query and not url.fragment,
            "repository must be an HTTPS URL without credentials, query or fragment")
    require(isinstance(source["revision"], str) and REVISION.fullmatch(source["revision"]),
            "source revision must be a complete lowercase Git commit SHA")
    exact(value["adapter"], {"id", "version"}, "adapter declaration")
    identifier(value["adapter"]["id"], "adapter ID")
    identifier(value["adapter"]["version"], "adapter version")
    exact(value["learning_unit"], {"id"}, "shared learning unit")
    identifier(value["learning_unit"]["id"], "learning unit ID")

    config = value["config"]
    exact(config, CONFIG_FIELDS, "configuration")
    require(config["train_feedback_mode"] in ("score_only", "with_labels"), "unsupported train feedback mode")
    exact(config["role_models"], ROLES, "role_models")
    for runtime in config["role_models"].values():
        exact(runtime, {"provider", "model_id", "thinking_level"}, "role runtime")
        for key, item in runtime.items():
            text(item, "runtime " + key)
            require(not item.startswith("-"), "runtime IDs cannot start with an option prefix")
        require("/" not in runtime["provider"], "provider ID cannot contain a slash")
    for key in ("max_iterations", "runs_per_case", "concurrency", "cell_timeout_seconds", "max_turns"):
        positive(config[key], key)
    require(config["independent_replicas"] is True, "replicas must start independently")
    replicas = config["replicas"]
    require(isinstance(replicas, list) and replicas, "declare at least one replica")
    for replica in replicas:
        identifier(replica, "replica ID")
    require(len(set(replicas)) == len(replicas), "duplicate replica ID")

    scoring = value["scoring"]
    exact(scoring, SCORING_FIELDS, "scoring")
    identifier(scoring["metric"], "reward metric")
    for key in ("raw_min", "raw_max"):
        try:
            finite = type(scoring[key]) in (int, float) and math.isfinite(scoring[key])
        except OverflowError:
            finite = False
        require(finite, "score bounds must be finite representable numbers")
    require(scoring["raw_min"] < scoring["raw_max"], "score bounds must be increasing")
    require(scoring["direction"] == "higher_is_better" and scoring["normalization"] == "linear_to_0_100"
            and scoring["aggregation"] == "mean" and scoring["success_rule"] == "normalized_score_equals_100",
            "declare higher-is-better scores, linear 0..100 normalization, arithmetic mean and full-score success")

    tasks = value["tasks"]
    require(isinstance(tasks, list) and tasks, "task catalog must be nonempty")
    by_case, originals, paths = {}, set(), set()
    for task in tasks:
        exact(task, TASK_FIELDS, "task")
        identifier(task["case_id"], "case ID")
        text(task["original_id"], "original task ID")
        relative_path(task["path"])
        text(task["category"], "task category", spaces=True)
        require(isinstance(task["content_sha256"], str) and HASH.fullmatch(task["content_sha256"]),
                "task content fingerprint must be a lowercase SHA-256")
        if task["duplicate_family"] is not None:
            text(task["duplicate_family"], "duplicate family ID")
        require(task["case_id"] not in by_case, "duplicate case ID")
        require(task["original_id"] not in originals, "original task identity reused under multiple cases")
        require(task["path"] not in paths, "task path reused under multiple cases")
        by_case[task["case_id"]] = task
        originals.add(task["original_id"])
        paths.add(task["path"])

    exact(value["phases"], set(PHASES), "phases")
    membership, fingerprints, families = {}, {}, {}
    for phase in PHASES:
        cases = value["phases"][phase]
        require(isinstance(cases, list), "phase cases must be a list")
        for case in cases:
            identifier(case, "phase case ID")
            require(case in by_case, "phase names a case absent from the catalog")
            require(case not in membership, "a case occurs more than once in the phase lists")
            membership[case] = phase
            task = by_case[case]
            for index, key in ((fingerprints, "content_sha256"), (families, "duplicate_family")):
                item = task[key]
                if item is not None:
                    require(item not in index or index[item] == phase,
                            "exact content or declared duplicate family crosses phase boundaries")
                    index[item] = phase
    require(set(membership) == set(by_case), "every catalog task must belong to exactly one phase")
    require(len(value["phases"]["train"]) >= 4,
            "the current Penguin Proposer reading protocol requires at least four different train tasks")
    require(value["phases"]["validation"], "an independent validation set is required")
    # Categories may legitimately recur across phases; category identity is not
    # task identity. An empty test list is explicitly validation-only exploration.
    return value


def freeze(value):
    validate_spec(value)
    # Retain the caller's explicit task order and choices; never reshuffle them.
    spec = json.loads(canonical(value))
    return {"schema_version": 1, "status": "planning_only", "spec": spec, "spec_sha256": sha(spec)}


def validate_frozen(value):
    exact(value, FROZEN_FIELDS, "frozen experiment plan")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1,
            "unsupported frozen plan version")
    require(value["status"] == "planning_only", "this tool cannot certify a runnable experiment")
    validate_spec(value["spec"])
    require(isinstance(value["spec_sha256"], str) and HASH.fullmatch(value["spec_sha256"])
            and value["spec_sha256"] == sha(value["spec"]), "frozen spec hash mismatch")
    return value


def safe_path(filename):
    path = Path(os.path.abspath(filename))
    require(all(not item.is_symlink() for item in (path, *path.parents)), "symlink paths are refused")
    return path


def unique_keys(pairs):
    result = {}
    for key, item in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = item
    return result


def reject_constant(_value):
    raise InvalidSpec("non-finite JSON number")


def read_json(filename):
    path = safe_path(filename)
    require(stat.S_ISREG(path.stat().st_mode), "input must be a regular file")
    return json.loads(path.read_bytes(), object_pairs_hook=unique_keys, parse_constant=reject_constant)


def write_frozen(filename, value):
    validate_frozen(value)
    path = safe_path(filename)
    require(path.parent.is_dir(), "output parent directory must exist")
    data = canonical(value)
    if path.exists():
        require(path.is_file() and path.read_bytes() == data, "refusing to overwrite a different existing file")
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".wikiskill-spec-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            safe_path(path)
            require(path.is_file() and path.read_bytes() == data, "output appeared with different contents")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def summary(value):
    phases = value["spec"]["phases"]
    return {"status": "planning_only", "spec_sha256": value["spec_sha256"],
            "counts": {phase: len(phases[phase]) for phase in PHASES},
            "evaluation_scope": "held_out_test_planned" if phases["test"] else "validation_only",
            "source_verified": False, "runtime_verified": False,
            "scope": "structure_and_declared_identity_only; no adapter support, source bytes, isolation or performance verified"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("freeze", help="validate explicit choices and freeze a planning-only manifest")
    create.add_argument("--input", required=True, type=Path)
    create.add_argument("--out", required=True, type=Path)
    check = commands.add_parser("check", help="check a frozen manifest without launching or modifying anything")
    check.add_argument("--spec", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "freeze":
            value = freeze(read_json(args.input))
            write_frozen(args.out, value)
        else:
            value = validate_frozen(read_json(args.spec))
        print(json.dumps(summary(value), ensure_ascii=False, allow_nan=False))
    except InvalidSpec as error:
        # These messages contain only internal schema labels and fixed reasons;
        # none interpolate supplied task IDs, paths, URLs or runtime values.
        print(f"experiment plan refused: {error}", file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        # Do not echo malformed input, task contents, credential-like strings or
        # private filesystem locations. Inputs contain metadata only by contract.
        print("experiment plan refused: check the documented schema, identities, split, paths and frozen hash", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
