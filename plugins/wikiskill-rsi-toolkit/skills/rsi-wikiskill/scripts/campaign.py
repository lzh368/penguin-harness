#!/usr/bin/env python3
"""Prepare and summarize the fixed WikiSkill/GDPevo campaign locally.

No model calls, containers, Penguin creation, credentials, or fabricated scores.
Deployment bindings record declarations; actual loading still requires audit.
"""
import argparse
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

# Direct CLI use must not leave binary resources in the text-only plugin tree.
# The workspace launcher also suppresses bytecode before importing this module.
sys.dont_write_bytecode = True


def sibling(name):
    spec = importlib.util.spec_from_file_location("campaign_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profile_tool = sibling("gdpevo_profile")
evaluation = sibling("aggregate")
REPLICAS = ("r1", "r2", "r3")
TEST_LABELS = ("test-H0", "test-final")
final_tests = sibling("final_test")
HASH = re.compile(r"[0-9a-f]{64}\Z")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{1,63}\Z")


class InvalidCampaign(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidCampaign(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return profile_tool.canonical_bytes(value)


def path(value):
    return profile_tool.no_symlink_components(value).resolve()


def read_json(filename):
    return evaluation.read_json(path(filename))


def digest(value, label):
    require(isinstance(value, str) and HASH.fullmatch(value), f"invalid {label} SHA-256")


def positive(value, label):
    require(type(value) is int and value > 0, f"{label} must be a positive integer")


def with_hash(value, field):
    return {**value, field: sha(encoded(value))}


def validate_profile(value):
    require(isinstance(value, dict), "profile must be an object")
    digest(value.get("profile_sha256"), "profile")
    body = {key: item for key, item in value.items() if key != "profile_sha256"}
    require(sha(encoded(body)) == value["profile_sha256"], "profile self-hash mismatch")
    require(value.get("source_verified") is True, "profile source must be verified")
    verification = value.get("source_verification")
    require(isinstance(verification, dict) and
            set(verification) == {"method", "file_count", "source_tree_sha256"},
            "profile source verification is incomplete")
    require(verification["method"] == "pinned_git_and_opaque_file_hashes", "unknown source verification method")
    positive(verification["file_count"], "verified file count")
    digest(verification["source_tree_sha256"], "source tree")
    config = value.get("config")
    require(isinstance(config, dict), "profile configuration is missing")
    positive(config.get("max_iterations"), "iteration budget")
    role_models = profile_tool.validate_role_models(config.get("role_models"))
    expected = profile_tool.build_profile(config["max_iterations"], role_models=role_models)
    expected["source_verified"] = True
    expected["source_verification"] = verification
    expected = with_hash({key: item for key, item in expected.items() if key != "profile_sha256"}, "profile_sha256")
    require(encoded(value) == encoded(expected),
            "profile differs from the fixed splits, selected runtimes, replicas, runs, or source refs")
    return value


def campaign_config(root, profile, penguin_home, project_id, agent_prefix="wiki", final_test_policy=None):
    require(final_test_policy is None or final_test_policy in final_tests.POLICIES, "unknown final test policy")
    require(isinstance(project_id, str) and IDENTIFIER.fullmatch(project_id),
            "project ID must contain 2..64 lowercase ASCII letters, digits or underscores and start with a letter")
    require(root != penguin_home and not root.is_relative_to(penguin_home) and
            not penguin_home.is_relative_to(root),
            "campaign and Penguin home must be separate, non-nested directories")
    require(isinstance(agent_prefix, str) and re.fullmatch(r"[a-z][a-z0-9_]{1,39}", agent_prefix),
            "Agent prefix must contain 2..40 lowercase letters, digits or underscores and start with a letter")
    return with_hash({
        "schema_version": 1, "campaign_root": str(root),
        "profile_sha256": profile["profile_sha256"], "profile_file_sha256": sha(encoded(profile)),
        "penguin_home": str(penguin_home), "project_id": project_id,
        **({"final_test_policy": final_test_policy} if final_test_policy else {}),
        **({"agent_prefix": agent_prefix} if agent_prefix != "wiki" else {}),
        "replicas": [{"id": replica, "controller_agent_id": f"{agent_prefix}_controller_{replica}",
                      "target_id": f"{agent_prefix}_target_{replica}",
                      "out": str(root / "replicas" / replica / "out"),
                      "plans": str(root / "replicas" / replica / "plans")}
                     for replica in REPLICAS],
    }, "campaign_sha256")


def load_campaign(campaign):
    root = path(campaign)
    config, _ = read_json(root / "campaign.json")
    profile, actual_hash = read_json(root / "profile.json")
    validate_profile(profile)
    require(isinstance(config, dict), "campaign configuration must be an object")
    require(actual_hash == config.get("profile_file_sha256"), "frozen profile file hash mismatch")
    require(isinstance(config.get("penguin_home"), str), "campaign Penguin home is missing")
    expected = campaign_config(root, profile, path(config["penguin_home"]), config.get("project_id"),
                               config.get("agent_prefix", "wiki"), config.get("final_test_policy"))
    require(encoded(config) == encoded(expected), "frozen campaign configuration mismatch")
    for replica in config["replicas"]:
        require(path(replica["plans"]).is_dir(), f"missing plan directory for {replica['id']}")
        path(replica["out"])
    return root, config, profile


def write_new(filename, value):
    with filename.open("xb") as stream:
        stream.write(encoded(value))


def initialize(profile_path, out, penguin_home, project_id, agent_prefix="wiki", final_test_policy=None):
    profile, _ = read_json(profile_path)
    validate_profile(profile)
    root, home = path(out), path(penguin_home)
    config = campaign_config(root, profile, home, project_id, agent_prefix, final_test_policy)
    if root.exists():
        _, previous, _ = load_campaign(root)
        require(encoded(previous) == encoded(config), "existing campaign has different initialization settings")
        return config
    require(root.parent.is_dir(), "campaign parent directory does not exist")
    root.mkdir()
    for replica in config["replicas"]:
        Path(replica["plans"]).mkdir(parents=True)
    write_new(root / "profile.json", profile)
    # Written last: an interrupted initialization cannot look complete.
    write_new(root / "campaign.json", config)
    return config


def replica_config(config, replica):
    require(replica in REPLICAS, "unknown replica")
    return next(item for item in config["replicas"] if item["id"] == replica)


def phase_plan(profile, replica, phase, version):
    require(phase in profile["phases"], "unknown phase")
    positive(version, "Target version")
    phase_config = profile["phases"][phase]
    return {"phase": phase, "benchmark_id": phase_config["benchmark_id"],
            "case_ids": [task["case_id"] for task in phase_config["tasks"]],
            "runs_per_case": profile["config"]["runs_per_case"],
            "agent_id": replica["target_id"], "expected_version": version,
            **profile["config"]["role_models"]["target"]}


def deployment(config, replica, phase, label, version, skill_hash, plan):
    digest(skill_hash, "skill")
    return with_hash({
        "schema_version": 1, "binding_status": "declared",
        "campaign_sha256": config["campaign_sha256"], "profile_sha256": config["profile_sha256"],
        "replica_id": replica["id"], "phase": phase, "label": label,
        "target_id": replica["target_id"], "expected_version": version,
        "skill_sha256": skill_hash, "plan_sha256": sha(encoded(plan)),
    }, "deployment_sha256")


def create_plan(campaign, replica_id, phase, label, version, skill_hash):
    _, config, profile = load_campaign(campaign)
    replica = replica_config(config, replica_id)
    require(isinstance(label, str) and LABEL.fullmatch(label), "invalid plan label")
    require((phase == "test" and label in TEST_LABELS) or
            (phase != "test" and label not in TEST_LABELS),
            "test labels must be test-H0 or test-final and are reserved for test")
    if label == "test-final" and config.get("final_test_policy") == final_tests.REUSE:
        check_store = sibling("state").Store(replica["out"])
        with check_store.lock():
            check_state = sibling("state").load_verified(check_store)
            require(bool(check_state["accepted"]["manifest"]),
                    "unchanged empty skills: record skip evidence instead of planning test-final")
    plan = phase_plan(profile, replica, phase, version)
    binding = deployment(config, replica, phase, label, version, skill_hash, plan)
    folder = path(Path(replica["plans"]) / label)
    require(not folder.exists(), "plan label already exists; refusing to overwrite")
    folder.mkdir()
    (folder / "cells").mkdir()
    write_new(folder / "plan.json", plan)
    write_new(folder / "deployment.json", binding)
    return {"plan_dir": str(folder), "plan": str(folder / "plan.json"),
            "cells": str(folder / "cells"), "deployment": binding}


def checked_test(config, profile, replica, label, used_sessions):
    folder = path(Path(replica["plans"]) / label)
    actual_plan, plan_hash = read_json(folder / "plan.json")
    binding, _ = read_json(folder / "deployment.json")
    require(isinstance(binding, dict), "deployment must be an object")
    version, skill_hash = binding.get("expected_version"), binding.get("skill_sha256")
    expected_plan = phase_plan(profile, replica, "test", version)
    expected_binding = deployment(config, replica, "test", label, version, skill_hash, expected_plan)
    require(encoded(actual_plan) == encoded(expected_plan), "test plan differs from the frozen complete matrix")
    require(encoded(binding) == encoded(expected_binding) and plan_hash == binding["plan_sha256"],
            "test deployment/plan binding mismatch")
    cells = path(folder / "cells")
    result = evaluation.aggregate(path(folder / "plan.json"), cells)
    require(result["plan_sha256"] == plan_hash, "test plan changed during aggregation")
    task_groups = {task["case_id"]: task["group"] for task in profile["phases"]["test"]["tasks"]}
    grouped = {group["id"]: [] for group in profile["groups"]}
    for entry in result["cell_sha256"]:
        row, actual_hash = read_json(cells / entry["file"])
        require(actual_hash == entry["sha256"], "cell changed during aggregation")
        require(row["session_id"] not in used_sessions, "test matrices reuse a Target session")
        used_sessions.add(row["session_id"])
        grouped[task_groups[row["case_id"]]].append(Decimal(row["score"]))
    domain_scores = {group["domain"]: decimal_mean(grouped[group["id"]]) for group in profile["groups"]}
    return {"score": result["score"], "domain_scores": domain_scores,
            "evaluation_id": result["evaluation_id"], "cell_count": result["cell_count"],
            "plan_sha256": plan_hash, "deployment_sha256": binding["deployment_sha256"],
            "skill_sha256": skill_hash, "expected_version": version}


def decimal_mean(values):
    return str(sum(values, Decimal(0)) / Decimal(len(values)))


def statistics(values):
    values = [Decimal(value) for value in values]
    mean = sum(values) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values))
    return {"mean": str(mean), "population_std": str(variance.sqrt())}


def verify_replica(campaign, replica_id):
    """Recompute phase matrices and decisions before automatic replica handoff.

    Does not certify receipts, OS isolation, or absence of active processes.
    The supervisor must separately establish quiescence and settled costs.
    """
    _, config, profile = load_campaign(campaign)
    replica = replica_config(config, replica_id)
    states = sibling("state")
    store = states.Store(replica["out"])
    with store.lock():
        state = states.load_verified(store)
        require(state["pending"] is None, "pending candidate prevents wrapup")
        require(Decimal(state["score_max"]) == 100, "unexpected score scale")
        require(state["round"] <= profile["config"]["max_iterations"], "iteration budget exceeded")
        require(state["round"] == profile["config"]["max_iterations"] or Decimal(state["best_score"]) == 100,
                "evolution is incomplete; an early operator stop is not completed wrapup")
        empty_hash = states.tree_info({})["sha256"]
        used_sessions, verified_labels, version_skills = set(), set(), {}

        def phase(label, name, skill_hash, minimum_version=1):
            folder = path(Path(replica["plans"]) / label)
            actual, plan_hash = read_json(folder / "plan.json")
            binding, _ = read_json(folder / "deployment.json")
            version = binding["expected_version"]
            expected = phase_plan(profile, replica, name, version)
            require(encoded(actual) == encoded(expected), "phase differs from complete frozen matrix")
            require(encoded(binding) == encoded(deployment(config, replica, name, label, version, skill_hash, expected)),
                    "phase deployment is not the expected skill set")
            require(version >= minimum_version, "Target deployment version went backwards")
            require(version not in version_skills or version_skills[version] == skill_hash,
                    "Target deployment version identifies different skill sets")
            version_skills[version] = skill_hash
            result = evaluation.aggregate(folder / "plan.json", folder / "cells")
            require(result["plan_sha256"] == plan_hash, "phase plan changed")
            for entry in result["cell_sha256"]:
                cell, digest = read_json(folder / "cells" / entry["file"])
                require(digest == entry["sha256"], "cell changed during verification")
                require(cell["session_id"] not in used_sessions, "Target session reused across phases")
                used_sessions.add(cell["session_id"])
            verified_labels.add(label)
            return result, version

        baseline, version = phase("validation-H0", "validation", empty_hash)
        require(Decimal(baseline["score"]) == Decimal(state["baseline_score"]), "baseline score differs from cells")
        decisions = {row["candidate_id"]: row for row in state["decisions"]}
        candidates = {store.json(f"candidates/{cid}/meta.json")["round"]: cid for cid in state["candidates"]}
        accepted = empty_hash
        for round_number in range(1, state["round"] + 1):
            _, version = phase(f"train-r{round_number:04d}", "train", accepted, version)
            candidate = candidates.get(round_number)
            if candidate is None:  # load_verified has already checked no_action rounds.
                continue
            meta = store.json(f"candidates/{candidate}/meta.json")
            require(meta["base"]["sha256"] == accepted, "candidate was not based on accepted skills")
            result, version = phase(f"validation-r{round_number:04d}", "validation", meta["content"]["sha256"], version)
            decision = decisions[candidate]
            require(result["evaluation_id"] == decision["evaluation_id"] and
                    Decimal(result["score"]) == Decimal(decision["score"]), "decision differs from validation evidence")
            if decision["accepted"]:
                accepted = meta["content"]["sha256"]
        require(accepted == state["accepted"]["sha256"], "final accepted skill differs from round history")
        # Both tests follow evolution, but the protocol does not order them
        # relative to each other. An unchanged deployment may be reused.
        h0, _ = phase("test-H0", "test", empty_hash, version)
        skipped = config.get("final_test_policy") == final_tests.REUSE and accepted == empty_hash
        if skipped:
            require(not (Path(replica["plans"]) / "test-final").exists(), "duplicate final test exists under skip policy")
            record = final_tests.read(Path(replica["out"]).parent / "final-test-decision.json")
            final_tests.verify_record(record, replica["out"], h0, config["campaign_sha256"], profile["config"]["max_iterations"], already_locked=True)
            final = None
        else:
            final, _ = phase("test-final", "test", accepted, version)
        require({p.name for p in path(replica["plans"]).iterdir()} == verified_labels,
                "unexpected/unverified phase plan remains")
        with localcontext() as context:
            context.prec = 40
            delta = None if skipped else str(Decimal(final["score"]) - Decimal(h0["score"]))
        return {"status": "verified", "replica_id": replica_id,
                "campaign_sha256": config["campaign_sha256"], "accepted_sha256": accepted,
                "H0_test": h0["score"], "final_test": None if skipped else final["score"], "delta": delta,
                "final_test_status": "skipped_unchanged_state" if skipped else "measured",
                "phase_count": len(verified_labels), "target_trials": len(used_sessions),
                "scope": "state_and_matrix_consistency; runtime_audits_required_separately"}


def summarize(campaign):
    _, config, profile = load_campaign(campaign)
    if config.get("final_test_policy") == final_tests.REUSE:
        reports = [verify_replica(campaign, r["id"]) for r in config["replicas"]]
        sessions = set()
        for replica, result in zip(config["replicas"], reports):
            checked_test(config, profile, replica, "test-H0", sessions)
            if result["final_test_status"] == "measured":
                checked_test(config, profile, replica, "test-final", sessions)
        overall = {}
        for field in ("H0_test", "final_test", "delta"):
            values = [Decimal(r[field]) for r in reports if r[field] is not None]
            overall[field] = {**(statistics(values) if values else {"mean": None, "population_std": None}),
                              "measured_replicas": len(values)}
        return {"schema_version": 2, "campaign_sha256": config["campaign_sha256"],
                "replica_count": len(reports), "paired": reports, "overall": overall,
                "note": "skipped final tests are null; measured-only means are not whole-campaign RSI gain"}
    paired, sessions = [], set()
    with localcontext() as context:
        context.prec = 40
        for replica in config["replicas"]:
            h0 = checked_test(config, profile, replica, "test-H0", sessions)
            final = checked_test(config, profile, replica, "test-final", sessions)
            paired.append({"replica_id": replica["id"], "H0": h0, "final": final,
                           "delta": str(Decimal(final["score"]) - Decimal(h0["score"])),
                           "domain_deltas": {name: str(Decimal(final["domain_scores"][name]) - Decimal(score))
                                             for name, score in h0["domain_scores"].items()}})
        overall = {name: statistics([item[name]["score"] for item in paired]) for name in ("H0", "final")}
        overall["delta"] = statistics([item["delta"] for item in paired])
        domains = {}
        for group in profile["groups"]:
            name = group["domain"]
            domains[name] = {stage: statistics([item[stage]["domain_scores"][name] for item in paired])
                             for stage in ("H0", "final")}
            domains[name]["delta"] = statistics([item["domain_deltas"][name] for item in paired])
    return {"schema_version": 1, "campaign_sha256": config["campaign_sha256"],
            "profile_sha256": config["profile_sha256"], "score_max": 100,
            "replica_count": len(paired), "paired": paired, "overall": overall, "domains": domains,
            "deployment_evidence": "declared_bindings; actual deployment and isolation require separate audit"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--profile", required=True, type=Path)
    init.add_argument("--out", required=True, type=Path)
    init.add_argument("--penguin-home", required=True, type=Path)
    init.add_argument("--project-id", required=True)
    init.add_argument("--agent-prefix", default="wiki")
    init.add_argument("--final-test-policy", choices=final_tests.POLICIES, default=final_tests.REUSE)
    plan = commands.add_parser("plan")
    plan.add_argument("--campaign", required=True, type=Path)
    plan.add_argument("--replica", required=True, choices=REPLICAS)
    plan.add_argument("--phase", required=True, choices=("train", "validation", "test"))
    plan.add_argument("--label", required=True)
    plan.add_argument("--version", required=True, type=int)
    plan.add_argument("--skill-hash", required=True)
    summary = commands.add_parser("summary")
    summary.add_argument("--campaign", required=True, type=Path)
    verify = commands.add_parser("verify-replica")
    verify.add_argument("--campaign", required=True, type=Path)
    verify.add_argument("--replica", required=True, choices=REPLICAS)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = initialize(args.profile, args.out, args.penguin_home, args.project_id, args.agent_prefix, args.final_test_policy)
        elif args.command == "plan":
            result = create_plan(args.campaign, args.replica, args.phase, args.label, args.version, args.skill_hash)
        elif args.command == "verify-replica":
            result = verify_replica(args.campaign, args.replica)
        else:
            result = summarize(args.campaign)
        sys.stdout.buffer.write(encoded(result))
    except (InvalidCampaign, evaluation.InvalidEvaluation, profile_tool.InvalidProfile,
            InvalidOperation, OSError, ValueError, TypeError, KeyError) as error:
        print(f"invalid campaign: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
