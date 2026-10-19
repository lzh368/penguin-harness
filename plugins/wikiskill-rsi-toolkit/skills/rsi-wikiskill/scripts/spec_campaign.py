#!/usr/bin/env python3
"""Freeze identities and verify WikiSkill runs using an external experiment spec.

No model, Docker or Penguin operations. Every formal cell is re-converted from
its private Harbor receipt; this never promotes a planning file to a smoke pass.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import importlib.util
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True


def sibling(name):
    spec = importlib.util.spec_from_file_location("spec_campaign_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


specs = sibling("experiment_spec")
evaluation = sibling("aggregate")
states = sibling("state")
require = specs.require
encoded = specs.canonical
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{1,63}\Z")
TEST_LABELS = ("test-H0", "test-final")
final_tests = sibling("final_test")


def path(value):
    return specs.safe_path(value).resolve()


def read(filename):
    return specs.read_json(path(filename))


def hashed(value, field):
    return {**value, field: hashlib.sha256(encoded(value)).hexdigest()}


def write_new(filename, value):
    with path(filename).open("xb") as stream:
        stream.write(encoded(value))


def configuration(root, frozen, source_root, home, project_id, prefix, final_test_policy=None):
    require(final_test_policy is None or final_test_policy in final_tests.POLICIES, "unknown final test policy")
    require(IDENTIFIER.fullmatch(project_id or ""), "invalid project ID")
    require(isinstance(prefix, str) and re.fullmatch(r"[a-z][a-z0-9_]{1,39}", prefix), "invalid Agent prefix")
    require(root != home and not root.is_relative_to(home) and not home.is_relative_to(root),
            "campaign and Penguin home must be separate, non-nested directories")
    replicas = []
    for replica in frozen["spec"]["config"]["replicas"]:
        controller, target = f"{prefix}_controller_{replica}", f"{prefix}_target_{replica}"
        require(IDENTIFIER.fullmatch(controller) and IDENTIFIER.fullmatch(target),
                "replica IDs must yield valid lowercase Penguin Agent IDs of at most 64 characters")
        replicas.append({"id": replica, "controller_agent_id": controller, "target_id": target,
                         "out": str(root / "replicas" / replica / "out"),
                         "plans": str(root / "replicas" / replica / "plans")})
    require(frozen["spec"]["phases"]["test"], "this campaign entry requires held-out test tasks")
    return hashed({"schema_version": 1, "kind": "wikiskill-spec-campaign", "campaign_root": str(root),
                   "spec_sha256": frozen["spec_sha256"],
                   "spec_file_sha256": hashlib.sha256(encoded(frozen)).hexdigest(),
                   "source_root": str(source_root), "penguin_home": str(home), "project_id": project_id,
                   **({"final_test_policy": final_test_policy} if final_test_policy else {}),
                   "agent_prefix": prefix, "replicas": replicas}, "campaign_sha256")


def load_campaign(campaign):
    root = path(campaign)
    config = read(root / "campaign.json")
    require(isinstance(config, dict), "campaign must be an object")
    frozen = sibling("harbor_spec").load(root / "spec-frozen.json", path(config["source_root"]))
    require(hashlib.sha256((root / "spec-frozen.json").read_bytes()).hexdigest() == config.get("spec_file_sha256"),
            "frozen spec file changed")
    expected = configuration(root, frozen, path(config["source_root"]), path(config["penguin_home"]),
                             config.get("project_id"), config.get("agent_prefix"), config.get("final_test_policy"))
    require(encoded(config) == encoded(expected), "campaign configuration changed")
    for replica in config["replicas"]:
        require(path(replica["plans"]).is_dir(), "replica plans directory is missing")
        path(replica["out"])
    return root, config, frozen


def initialize(spec_path, source_root, out, penguin_home, project_id, agent_prefix, final_test_policy=None):
    source_root = path(source_root)
    frozen = sibling("harbor_spec").load(spec_path, source_root)
    root = path(out)
    config = configuration(root, frozen, source_root, path(penguin_home), project_id, agent_prefix, final_test_policy)
    if root.exists():
        _, previous, _ = load_campaign(root)
        require(encoded(previous) == encoded(config), "existing campaign has different initialization settings")
        return config
    require(root.parent.is_dir(), "campaign parent directory must exist")
    root.mkdir()
    for replica in config["replicas"]:
        Path(replica["plans"]).mkdir(parents=True)
    write_new(root / "spec-frozen.json", frozen)
    write_new(root / "campaign.json", config)
    return config


def replica_config(config, replica_id):
    matches = [item for item in config["replicas"] if item["id"] == replica_id]
    require(len(matches) == 1, "unknown replica")
    return matches[0]


def timestamp(value):
    require(isinstance(value, str), "missing experiment timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.utcoffset() is not None, "timestamp must include a timezone")
    return result


def terminal_state(state, frozen):
    require(state["pending"] is None, "pending candidate prevents freezing")
    limit = frozen["spec"]["config"]["max_iterations"]
    require(state["round"] <= limit and (state["round"] == limit or Decimal(state["best_score"]) == 100),
            "evolution is incomplete; an operator stop is not a final Skill freeze")


def freeze_record(config, replica, state_hash, accepted_hash, frozen_at):
    return hashed({"schema_version": 1, "campaign_sha256": config["campaign_sha256"],
                   "experiment_spec_sha256": config["spec_sha256"], "replica_id": replica["id"],
                   "state_sha256": state_hash, "accepted_sha256": accepted_hash, "frozen_at": frozen_at},
                  "freeze_sha256")


def load_freeze(config, replica, frozen, store, state):
    terminal_state(state, frozen)
    record = read(Path(replica["out"]).parent / "freeze.json")
    require(isinstance(record, dict), "freeze must be an object")
    expected = freeze_record(config, replica, store.digest("state.json"), state["accepted"]["sha256"], record.get("frozen_at"))
    require(record == expected, "final state or Skill freeze changed")
    boundary = timestamp(record["frozen_at"])
    require(boundary <= datetime.now(timezone.utc), "Skill freeze timestamp is in the future")
    for item in state["decisions"] + state["no_actions"]:
        require(timestamp(item["time"]) <= boundary, "Skill freeze predates the final learning decision")
    return record


def freeze_skills(campaign, replica_id):
    _, config, frozen = load_campaign(campaign)
    replica = replica_config(config, replica_id)
    store = states.Store(replica["out"])
    with store.lock():
        state = states.load_verified(store)
        terminal_state(state, frozen)
        filename = Path(replica["out"]).parent / "freeze.json"
        if filename.exists():
            return load_freeze(config, replica, frozen, store, state)
        frozen_at = datetime.now(timezone.utc).isoformat()
        for item in state["decisions"] + state["no_actions"]:
            require(timestamp(item["time"]) <= timestamp(frozen_at), "learning decision is in the future")
        record = freeze_record(config, replica, store.digest("state.json"), state["accepted"]["sha256"], frozen_at)
        write_new(filename, record)
        return record


def phase_plan(frozen, replica, phase, version):
    specs.positive(version, "Target version")
    require(phase in specs.PHASES, "unknown phase")
    spec = frozen["spec"]
    plan = {"phase": phase, "benchmark_id": "wikiskill-" + frozen["spec_sha256"][:16],
            "case_ids": list(spec["phases"][phase]), "runs_per_case": spec["config"]["runs_per_case"],
            "agent_id": replica["target_id"], "expected_version": version,
            **spec["config"]["role_models"]["target"]}
    sibling("harbor_spec").validate_plan(frozen, plan, purpose="evaluation")
    return plan


def deployment(config, replica, phase, label, version, skill_hash, plan):
    require(isinstance(skill_hash, str) and specs.HASH.fullmatch(skill_hash), "invalid Skill hash")
    return hashed({"schema_version": 1, "binding_status": "declared",
                   "campaign_sha256": config["campaign_sha256"], "experiment_spec_sha256": config["spec_sha256"],
                   "replica_id": replica["id"], "phase": phase, "label": label,
                   "target_id": replica["target_id"], "expected_version": version,
                   "skill_sha256": skill_hash, "plan_sha256": hashlib.sha256(encoded(plan)).hexdigest()},
                  "deployment_sha256")


def create_plan(campaign, replica_id, phase, label, version, skill_hash):
    _, config, frozen = load_campaign(campaign)
    replica = replica_config(config, replica_id)
    require(isinstance(label, str) and LABEL.fullmatch(label), "invalid phase label")
    require((phase == "test" and label in TEST_LABELS) or (phase != "test" and label not in TEST_LABELS),
            "test labels are reserved for test-H0 and test-final")
    if phase == "test":
        store = states.Store(replica["out"])
        with store.lock():
            load_freeze(config, replica, frozen, store, states.load_verified(store))
    if label == "test-final" and config.get("final_test_policy") == final_tests.REUSE:
        check_store = sibling("state").Store(replica["out"])
        with check_store.lock():
            check_state = sibling("state").load_verified(check_store)
            require(bool(check_state["accepted"]["manifest"]),
                    "unchanged empty skills: record skip evidence instead of planning test-final")
    plan = phase_plan(frozen, replica, phase, version)
    binding = deployment(config, replica, phase, label, version, skill_hash, plan)
    folder = path(Path(replica["plans"]) / label)
    require(not folder.exists(), "phase label exists; refusing to overwrite")
    folder.mkdir()
    (folder / "cells").mkdir()
    write_new(folder / "plan.json", plan)
    write_new(folder / "deployment.json", binding)
    return {"plan_dir": str(folder), "plan": str(folder / "plan.json"),
            "cells": str(folder / "cells"), "sources": str(folder / "sources.json"), "deployment": binding}


def verified_sources(root, config, replica, folder, plan, cells, injected_block_hash, frozen_at):
    """Re-convert the complete matrix, without trusting published cell scores."""
    sources = read(folder / "sources.json")
    require(isinstance(sources, list), "sources must be an array")
    expected = {(case, run) for case in plan["case_ids"] for run in range(1, plan["runs_per_case"] + 1)}
    require(len(sources) == len(expected), "missing or extra source receipts")
    private = path(Path(replica["out"]) / "private")
    converter = sibling("harbor_result")
    seen = set()
    for source in sources:
        specs.exact(source, {"case_id", "run", "result", "receipt"}, "source")
        key = (source["case_id"], source["run"])
        require(type(source["run"]) is int and key in expected and key not in seen, "unexpected/repeated source cell")
        seen.add(key)
        for field in ("result", "receipt"):
            require(isinstance(source[field], str) and Path(source[field]).is_absolute(), "source path must be absolute")
            require(path(source[field]).is_relative_to(private), "source must be in this replica OUT/private")
        receipt = read(source["receipt"])
        require(receipt.get("purpose") == "evaluation", "smoke evidence cannot become a formal matrix")
        require(receipt.get("state_binding", {}).get("expected", {}).get("injected_block_sha256") == injected_block_hash,
                "runtime injected Skill block differs from the actual expected Skill files")
        require(read(Path(source["receipt"]).parent / "plan.json") == plan,
                "receipt plan differs from the complete frozen phase plan")
        cell = converter.convert(Path(source["result"]), Path(source["receipt"]),
                                 spec_path=root / "spec-frozen.json", source_root=Path(config["source_root"]))
        if plan["phase"] == "test":
            actual = read(source["result"])
            boundary = timestamp(frozen_at)
            require(timestamp(actual.get("agent_execution", {}).get("started_at")) >= boundary,
                    "test execution began before the final Skill freeze")
            if "started_at" in actual:
                require(timestamp(actual["started_at"]) >= boundary, "test trial began before the final Skill freeze")
        require((cell["case_id"], cell["run"]) == key, "source identity mismatch")
        require(cell == cells[key], "cell differs from its audited result")


def verify_replica(campaign, replica_id, used_sessions=None):
    root, config, frozen = load_campaign(campaign)
    replica = replica_config(config, replica_id)
    store = states.Store(replica["out"])
    with store.lock():
        state = states.load_verified(store)
        freeze = load_freeze(config, replica, frozen, store, state)
        require(state["pending"] is None, "pending candidate prevents wrapup")
        require(Decimal(state["score_max"]) == 100, "unexpected score scale")
        require(state["round"] <= frozen["spec"]["config"]["max_iterations"], "iteration budget exceeded")
        require(state["round"] == frozen["spec"]["config"]["max_iterations"] or Decimal(state["best_score"]) == 100,
                "evolution is incomplete; an operator stop is not completed wrapup")
        empty_hash = states.tree_info({})["sha256"]
        # Bind declarations to immutable candidate content, not merely to other
        # declarations in a receipt. Earlier accepted sets remain in candidates.
        injected_blocks = {empty_hash: states.sha(states.render_text({}, block=True))}
        for candidate in state["candidates"]:
            files = store.tree(f"candidates/{candidate}/skills")
            injected_blocks[states.tree_info(files)["sha256"]] = states.sha(states.render_text(files, block=True))
        used_sessions = set() if used_sessions is None else used_sessions
        previous_session_count = len(used_sessions)
        labels, version_skills = set(), {}

        def phase(label, name, skill_hash, minimum_version=1):
            folder = path(Path(replica["plans"]) / label)
            actual, binding = read(folder / "plan.json"), read(folder / "deployment.json")
            version = binding["expected_version"]
            expected = phase_plan(frozen, replica, name, version)
            require(actual == expected, "phase differs from the complete frozen matrix")
            require(binding == deployment(config, replica, name, label, version, skill_hash, expected),
                    "phase deployment does not bind the expected Skill set")
            require(version >= minimum_version, "Target deployment version went backwards")
            require(version not in version_skills or version_skills[version] == skill_hash,
                    "Target version identifies different Skill sets")
            version_skills[version] = skill_hash
            result = evaluation.aggregate(folder / "plan.json", folder / "cells")
            require(result["plan_sha256"] == binding["plan_sha256"], "phase plan bytes changed")
            rows = {}
            for item in result["cell_sha256"]:
                row, actual_hash = evaluation.read_json(folder / "cells" / item["file"])
                require(actual_hash == item["sha256"], "cell changed during verification")
                require(row["session_id"] not in used_sessions, "Target session reused across phases")
                used_sessions.add(row["session_id"])
                rows[row["case_id"], row["run"]] = row
            require(skill_hash in injected_blocks, "expected Skill content is missing")
            verified_sources(root, config, replica, folder, expected, rows, injected_blocks[skill_hash], freeze["frozen_at"])
            labels.add(label)
            return result, version

        baseline, version = phase("validation-H0", "validation", empty_hash)
        require(Decimal(baseline["score"]) == Decimal(state["baseline_score"]), "baseline differs from cells")
        decisions = {row["candidate_id"]: row for row in state["decisions"]}
        candidates = {store.json(f"candidates/{cid}/meta.json")["round"]: cid for cid in state["candidates"]}
        accepted = empty_hash
        for round_number in range(1, state["round"] + 1):
            _, version = phase(f"train-r{round_number:04d}", "train", accepted, version)
            candidate = candidates.get(round_number)
            if candidate is None:
                continue
            meta = store.json(f"candidates/{candidate}/meta.json")
            require(meta["base"]["sha256"] == accepted, "candidate base is not accepted Skills")
            result, version = phase(f"validation-r{round_number:04d}", "validation", meta["content"]["sha256"], version)
            decision = decisions[candidate]
            require(result["evaluation_id"] == decision["evaluation_id"] and
                    Decimal(result["score"]) == Decimal(decision["score"]), "decision differs from validation evidence")
            if decision["accepted"]:
                accepted = meta["content"]["sha256"]
        require(accepted == state["accepted"]["sha256"], "final accepted Skills differ from round history")
        h0, _ = phase("test-H0", "test", empty_hash, version)
        skipped = config.get("final_test_policy") == final_tests.REUSE and accepted == empty_hash
        if skipped:
            require(not (Path(replica["plans"]) / "test-final").exists(), "duplicate final test exists under skip policy")
            record = final_tests.read(Path(replica["out"]).parent / "final-test-decision.json")
            final_tests.verify_record(record, replica["out"], h0, config["campaign_sha256"], frozen["spec"]["config"]["max_iterations"], already_locked=True)
            final = None
        else:
            final, _ = phase("test-final", "test", accepted, version)
        require({p.name for p in path(replica["plans"]).iterdir()} == labels, "unexpected/unverified phase plan")
        with localcontext() as context:
            context.prec = 40
            delta = None if skipped else str(Decimal(final["score"]) - Decimal(h0["score"]))
        return {"status": "verified", "replica_id": replica_id, "campaign_sha256": config["campaign_sha256"],
                "experiment_spec_sha256": frozen["spec_sha256"], "accepted_sha256": accepted,
                "freeze_sha256": freeze["freeze_sha256"],
                "H0_test": h0["score"], "final_test": None if skipped else final["score"], "delta": delta,
                "final_test_status": "skipped_unchanged_state" if skipped else "measured",
                "phase_count": len(labels), "target_trials": len(used_sessions) - previous_session_count,
                "scope": "state_matrix_and_receipt_consistency; runtime_audit_truth_requires_operator_review"}


def summarize(campaign):
    _, config, frozen = load_campaign(campaign)
    sessions = set()
    reports = [verify_replica(campaign, replica["id"], sessions) for replica in config["replicas"]]
    with localcontext() as context:
        context.prec = 40
        overall = {}
        for field in ("H0_test", "final_test", "delta"):
            values = [Decimal(item[field]) for item in reports if item[field] is not None]
            mean = sum(values) / len(values) if values else None
            overall[field] = {"measured_replicas": len(values), "mean": str(mean) if mean is not None else None,
                              "population_std": str((sum((x - mean) ** 2 for x in values) / len(values)).sqrt()) if values else None}
    return {"schema_version": 1, "campaign_sha256": config["campaign_sha256"],
            "experiment_spec_sha256": frozen["spec_sha256"], "replica_count": len(reports),
            "paired": reports, "overall": overall, "score_max": 100}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    for flag in ("spec", "source-root", "out", "penguin-home"):
        init.add_argument("--" + flag, type=Path, required=True)
    init.add_argument("--project-id", required=True)
    init.add_argument("--agent-prefix", required=True)
    init.add_argument("--final-test-policy", choices=final_tests.POLICIES, default=final_tests.REUSE)
    plan = commands.add_parser("plan")
    plan.add_argument("--phase", choices=specs.PHASES, required=True)
    plan.add_argument("--label", required=True)
    plan.add_argument("--version", type=int, required=True)
    plan.add_argument("--skill-hash", required=True)
    verify = commands.add_parser("verify-replica")
    freeze = commands.add_parser("freeze", help="freeze terminal learning state before either held-out test")
    summary = commands.add_parser("summary")
    for command in (plan, verify, freeze, summary):
        command.add_argument("--campaign", type=Path, required=True)
    for command in (plan, verify, freeze):
        command.add_argument("--replica", required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            result = initialize(args.spec, args.source_root, args.out, args.penguin_home, args.project_id, args.agent_prefix, args.final_test_policy)
        elif args.command == "plan":
            result = create_plan(args.campaign, args.replica, args.phase, args.label, args.version, args.skill_hash)
        elif args.command == "verify-replica":
            result = verify_replica(args.campaign, args.replica)
        elif args.command == "freeze":
            result = freeze_skills(args.campaign, args.replica)
        else:
            result = summarize(args.campaign)
        sys.stdout.buffer.write(encoded(result))
        return 0
    except (ValueError, OSError, TypeError, KeyError, InvalidOperation) as error:
        print(f"invalid spec campaign: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
