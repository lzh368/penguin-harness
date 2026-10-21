#!/usr/bin/env python3
"""Convert an audited Penguin/Harbor 0.23.0 trial into a Penguin cell JSON.

Reads result.json, a receipt, plan, audits and the private exported Target traces.
No model/container calls, credentials or gold. Schema 2 recomputes injection and
runtime facts from all exported root shards. File deployment, export completeness
and isolation still require independent audits; hashes are not attestations.
Schema 3 binds a single-step local task to an explicit frozen experiment spec.
Without --spec, the existing GDPevo protocol remains unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from uuid import UUID

sys.dont_write_bytecode = True


def trace_tool():
    spec = importlib.util.spec_from_file_location("wikiskill_trace_binding", Path(__file__).with_name("trace_binding.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def spec_tool():
    spec = importlib.util.spec_from_file_location("wikiskill_harbor_spec", Path(__file__).with_name("harbor_spec.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BENCHMARK_REVISION = "e5d58d230bda928d9a98ae775c54c159af8f8363"
PENGUIN_VERSION = "0.2.13"
GROUPS = ("003", "005", "010", "014", "018", "024")
REQUEST_FIELDS = {"protocol_version", "case_id", "run", "expected_version",
                  "test_agent_id", "benchmark_id", "provider", "model_id"}
PLAN_FIELDS = {"phase", "benchmark_id", "case_ids", "runs_per_case", "agent_id",
               "expected_version", "provider", "model_id", "thinking_level"}
RECEIPT_FIELDS = {"schema_version", "benchmark_revision", "request", "thinking_level",
                  "task_name", "trial_dir", "trial_id", "result_sha256",
                  "phase_plan", "state_binding", "audit"}
BINDING_FIELDS = {"deployed_state_sha256", "archive_sha256", "injected_block_sha256",
                  "container_agent_state", "container_workspace", "state_version"}
AUDIT_CHECKS = ("network", "actual_state_bound", "reward_validated", "trace_complete")
HASH = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{1,63}\Z")


class InvalidTrial(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidTrial(message)


def exact(value, fields, label):
    require(isinstance(value, dict) and set(value) == fields,
            f"{label} must have exactly the documented fields")


def integer(value, label, minimum=1):
    require(type(value) is int and value >= minimum, f"invalid {label}")


def digest(value, label):
    require(isinstance(value, str) and HASH.fullmatch(value), f"invalid {label} SHA-256")


def unique_keys(pairs):
    output = {}
    for key, value in pairs:
        require(key not in output, "duplicate JSON key")
        output[key] = value
    return output


def reject_constant(_value):
    raise InvalidTrial("non-finite JSON number")


def file_bytes(path):
    path = Path(os.path.abspath(path))
    require(all(not p.is_symlink() for p in (path, *path.parents)), "symlink input refused")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode), "input must be a regular file")
    # Reports cannot use a hard link to bypass the named, private evidence paths.
    require(info.st_nlink == 1, "hard-linked input refused")
    return path.read_bytes()


def json_bytes(data):
    value = json.loads(data, object_pairs_hook=unique_keys, parse_float=Decimal,
                       parse_constant=reject_constant)
    require(isinstance(value, dict), "JSON input must be an object")
    return value


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def number(value, label):
    require(type(value) in (int, Decimal), f"{label} must be a JSON number")
    value = Decimal(value)
    require(value.is_finite(), f"non-finite {label}")
    return value


def container_path(value, label):
    require(isinstance(value, str) and value.startswith("/") and "\x00" not in value,
            f"{label} must be an absolute container path")
    parts = PurePosixPath(value).parts
    require(".." not in parts and str(PurePosixPath(value)) == value,
            f"{label} must be canonical")


def checked_artifact(directory, entry, filename):
    exact(entry, {"artifact", "sha256"}, "artifact reference")
    require(entry["artifact"] == filename, "unexpected evidence path")
    digest(entry["sha256"], "artifact")
    data = file_bytes(directory / filename)
    require(sha256(data) == entry["sha256"], "audit/plan artifact hash mismatch")
    return data


def receipt_check(receipt, directory, allow_legacy=False, frozen=None):
    version = receipt.get("schema_version")
    require(type(version) is int and version in (1, 2, 3), "unsupported receipt version")
    require(version >= 2 or allow_legacy, "legacy receipt requires explicit --allow-legacy (historical review only)")
    require((version == 3) == (frozen is not None), "schema 3 requires --spec and --source-root; legacy receipts cannot use a spec")
    extra = {"experiment_spec_sha256", "purpose"} if version == 3 else set()
    exact(receipt, RECEIPT_FIELDS | ({"runtime_evidence"} if version >= 2 else set()) | extra, "receipt")
    revision = frozen["spec"]["source"]["revision"] if frozen is not None else BENCHMARK_REVISION
    require(receipt["benchmark_revision"] == revision, "benchmark revision mismatch")
    if frozen is not None:
        require(receipt["experiment_spec_sha256"] == frozen["spec_sha256"], "experiment spec binding mismatch")
    request = receipt["request"]
    exact(request, REQUEST_FIELDS, "request")
    require(type(request["protocol_version"]) is int and request["protocol_version"] == 1,
            "protocol version mismatch")
    integer(request["run"], "run")
    integer(request["expected_version"], "expected_version")
    require(isinstance(request["test_agent_id"], str) and IDENTIFIER.fullmatch(request["test_agent_id"]),
            "invalid test_agent_id")
    for key in ("provider", "model_id"):
        value = request[key]
        require(isinstance(value, str) and value and not value.startswith("-") and
                all(character.isprintable() and not character.isspace() for character in value),
                f"invalid request {key}")
    require("/" not in request["provider"], "provider ID cannot contain a slash")
    require(receipt["thinking_level"] in ("low", "medium", "high", "xhigh", "max"),
            "invalid thinking_level")
    task = receipt["task_name"]
    require(isinstance(task, str), "invalid task_name")
    if frozen is None:
        match = re.fullmatch(r"tg(003|005|010|014|018|024)-(train|test)-00([1-5])", task)
        require(match is not None, "task outside the pinned GDPevo subset")
        group, split, task_index = match.groups()
        case_number = GROUPS.index(group) * 5 + int(task_index)
        require(request["case_id"] == f"CASE-{case_number:03d}-{task}", "case/task mapping mismatch")
        require(request["benchmark_id"] == f"penguinharness-benchmark-gdpevo-{split}",
                "benchmark/task mapping mismatch")
    else:
        catalog_task = spec_tool().case_task(frozen, request["case_id"])
        require(task == PurePosixPath(catalog_task["path"]).name, "case/task mapping mismatch")
    trial = receipt["trial_dir"]
    require(isinstance(trial, str) and re.fullmatch(re.escape(task) + r"__[A-Za-z0-9_-]+", trial),
            "invalid trial basename")
    require(isinstance(receipt["trial_id"], str) and str(UUID(receipt["trial_id"])) == receipt["trial_id"],
            "invalid trial UUID")
    digest(receipt["result_sha256"], "result")

    plan = json_bytes(checked_artifact(directory, receipt["phase_plan"], "plan.json"))
    exact(plan, PLAN_FIELDS, "phase plan")
    require(plan["phase"] in ("train", "validation", "test"), "invalid plan phase")
    # Explicit phase is checked against the approved 24/6/30 mapping, not merely
    # trusted because a self-consistent receipt calls a held-out task "train".
    cases = plan["case_ids"]
    require(isinstance(cases, list) and cases and all(isinstance(x, str) for x in cases)
            and len(cases) == len(set(cases)), "invalid phase case list")
    if frozen is None:
        indices = {"train": range(1, 5), "validation": (5,), "test": range(1, 6)}[plan["phase"]]
        source_split = "test" if plan["phase"] == "test" else "train"
        approved_cases = {
            f"CASE-{group_index * 5 + index:03d}-tg{group_id}-{source_split}-{index:03d}"
            for group_index, group_id in enumerate(GROUPS) for index in indices
        }
        require(set(cases) <= approved_cases, "phase plan violates the approved GDPevo split")
    else:
        spec_tool().validate_plan(frozen, plan, receipt["purpose"])
    require(request["case_id"] in cases, "case absent from the declared phase plan")
    integer(plan["runs_per_case"], "runs_per_case")
    require(request["run"] <= plan["runs_per_case"], "run outside the phase plan")
    integer(plan["expected_version"], "plan expected_version")
    for key in ("benchmark_id", "expected_version", "provider", "model_id"):
        require(plan[key] == request[key], f"phase plan {key} mismatch")
    require(plan["agent_id"] == request["test_agent_id"] and
            plan["thinking_level"] == receipt["thinking_level"], "phase plan runtime mismatch")

    binding = receipt["state_binding"]
    exact(binding, {"expected", "observed"}, "state_binding")
    for kind in ("expected", "observed"):
        record = binding[kind]
        exact(record, BINDING_FIELDS, f"state_binding.{kind}")
        for key in ("deployed_state_sha256", "archive_sha256", "injected_block_sha256"):
            digest(record[key], key)
        for key in ("container_agent_state", "container_workspace"):
            container_path(record[key], key)
        integer(record["state_version"], "bound State version")
        require(record["state_version"] == request["expected_version"], "bound State version mismatch")
        require(PurePosixPath(record["container_agent_state"]).name == "agent_state",
                "container Agent State path must end in agent_state")
        require(PurePosixPath(record["container_agent_state"]).parent.name == request["test_agent_id"],
                "container Agent State does not belong to the requested Target")
    require(binding["expected"] == binding["observed"], "deployed/archive/context binding mismatch")

    audits = receipt["audit"]
    require(isinstance(audits, list) and len(audits) == len(AUDIT_CHECKS), "four independent audit checks required")
    seen = set()
    for audit in audits:
        exact(audit, {"check", "verdict", "artifact", "artifact_sha256"}, "audit fact")
        check = audit["check"]
        require(check in AUDIT_CHECKS and check not in seen, "unknown or duplicate audit check")
        seen.add(check)
        require(audit["verdict"] == "pass", f"unverified audit: {check}")
        report = json_bytes(checked_artifact(
            directory, {"artifact": audit["artifact"], "sha256": audit["artifact_sha256"]},
            f"audit/{check}.json"))
        require(report.get("check") == check and report.get("verdict") == "pass",
                "audit report disagrees with receipt or lacks its check/verdict")
    return request


def duration_ms(execution):
    require(isinstance(execution, dict), "missing agent_execution timing")
    times = []
    for key in ("started_at", "finished_at"):
        value = execution.get(key)
        require(isinstance(value, str), "missing agent execution timestamp")
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(timestamp.utcoffset() is not None, "agent timestamps require a timezone")
        times.append(timestamp)
    delta = times[1] - times[0]
    microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
    require(microseconds >= 0, "agent execution ended before it started")
    return int((Decimal(microseconds) / 1000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def convert(result_path, receipt_path, allow_legacy=False, spec_path=None, source_root=None):
    result_path, receipt_path = Path(result_path), Path(receipt_path)
    require(result_path.name == "result.json", "expected a trial/result.json")
    receipt = json_bytes(file_bytes(receipt_path))
    require((spec_path is None) == (source_root is None), "--spec and --source-root must be supplied together")
    frozen = spec_tool().load(spec_path, source_root) if spec_path is not None else None
    request = receipt_check(receipt, receipt_path.absolute().parent, allow_legacy, frozen)
    raw = file_bytes(result_path)
    require(sha256(raw) == receipt["result_sha256"], "result does not match the audited bytes")
    result = json_bytes(raw)
    require(result_path.parent.name == receipt["trial_dir"], "result directory binding mismatch")
    require(result.get("id") == receipt["trial_id"], "trial UUID mismatch")
    require(result.get("task_name") == receipt["task_name"], "result task mismatch")
    require(result.get("trial_name") == receipt["trial_dir"], "result trial_name mismatch")
    task_id = result.get("task_id")
    exact(task_id, {"path"}, "supported LocalTaskId")
    task_path = task_id["path"]
    if frozen is None:
        require(isinstance(task_path, str) and ".." not in PurePosixPath(task_path).parts and
                PurePosixPath(task_path).parts[-4:] == ("benchmarks", "gdpevo", "tasks", receipt["task_name"]),
                "local task path does not match the pinned task layout")
    else:
        catalog_task = spec_tool().case_task(frozen, request["case_id"])
        expected_path = spec_tool().task_path(source_root, catalog_task["path"])
        require(task_path == str(expected_path), "result task path differs from the actual frozen source")
    require("exception_info" in result and result["exception_info"] is None,
            "trial has an exception or lacks its exception status")
    require(result.get("step_results") in (None, []), "multi-step trials are not supported")
    require(not result.get("error") and not result.get("errors"), "trial has an error")

    agent = result.get("agent_result")
    require(isinstance(agent, dict), "missing agent_result")
    metadata = agent.get("metadata")
    require(isinstance(metadata, dict), "missing adapter metadata")
    require(metadata.get("penguin_version") == PENGUIN_VERSION,
            "container Penguin version differs from the frozen runtime")
    require(metadata.get("agent_state") == "custom", "stock or unbound Agent State refused")
    require(metadata.get("provider") == request["provider"] and metadata.get("model_id") == request["model_id"],
            "adapter model identity mismatch")
    require(metadata.get("thinking") == receipt["thinking_level"], "adapter thinking mismatch")
    integer(metadata.get("requests"), "positive model request count")
    require(metadata.get("server_ready") is True, "adapter server never became ready")
    status = metadata.get("status")
    require(status in ("completed", "timeout"), "adapter did not complete a scorable execution")
    require(metadata.get("timed_out") is (status == "timeout"), "timeout metadata disagrees")
    if "exit_code" in metadata:
        require(type(metadata["exit_code"]) is int, "invalid adapter exit code")
        require(status != "completed" or metadata["exit_code"] == 0,
                "completed adapter has a nonzero exit code")
    if "run_status" in metadata:
        allowed = ("completed",) if status == "completed" else ("running", "aborted", "completed")
        require(metadata["run_status"] in allowed, "inner CLI status disagrees with adapter outcome")
    require(isinstance(metadata.get("session_id"), str) and metadata["session_id"].strip(),
            "missing inner Penguin Session identity")
    usage = agent.get("model_usage")
    model = f"{request['provider']}/{request['model_id']}"
    require(isinstance(usage, dict) and set(usage) == {model} and isinstance(usage[model], dict),
            "model_usage must contain exactly the runtime bound by the phase plan")
    require(not any(record.get(key) for record in (metadata, agent) for key in ("error", "errors")),
            "adapter reports an error")

    if receipt["schema_version"] >= 2:
        observation = json_bytes(checked_artifact(receipt_path.absolute().parent,
                                 receipt["runtime_evidence"], "runtime-observation.json"))
        trace = trace_tool()
        # This is intentionally recomputed, not trusted because a report says pass.
        require(observation == trace.observe(result_path), "runtime observation differs from exported traces")
        trace.check_binding(observation, receipt["state_binding"]["expected"], request)

    verifier = result.get("verifier_result")
    exact(verifier, {"rewards"}, "verifier_result")
    rewards = verifier.get("rewards")
    scoring = frozen["spec"]["scoring"] if frozen is not None else {"metric": "reward", "raw_min": 0, "raw_max": 1}
    require(isinstance(rewards, dict) and set(rewards) == {scoring["metric"]}, "expected the frozen verifier reward metric")
    reward = number(rewards[scoring["metric"]], "reward")
    lower, upper = (Decimal(str(scoring[key])) for key in ("raw_min", "raw_max"))
    require(lower <= reward <= upper, "reward outside the frozen range")
    with localcontext() as context:
        context.prec = max(40, len(reward.as_tuple().digits) + 8)
        scored = ((reward - lower) * 100 / (upper - lower)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    cost = agent.get("cost_usd")
    if cost is not None:
        cost = number(cost, "cost_usd")
        require(cost >= 0, "negative cost")
    # A audited real timeout with reward zero stays a valid zero; no synthetic
    # zero is ever created for missing rewards, setup errors, or no model call.
    return {"protocol_version": 1, "status": "ok", "case_id": request["case_id"],
            "run": request["run"], "agent_id": request["test_agent_id"],
            "expected_version": request["expected_version"], "provider": request["provider"],
            "model_id": request["model_id"], "thinking_level": receipt["thinking_level"],
            "score": scored, "cost": cost, "duration_ms": duration_ms(result.get("agent_execution")),
            "session_id": "harbor:" + receipt["trial_dir"]}


def cell_json(cell):
    # Decimal values remain JSON numbers; no binary-float rounding or quoted score.
    return "{" + ",".join(json.dumps(key) + ":" +
                          (format(value, "f") if isinstance(value, Decimal)
                           else json.dumps(value, ensure_ascii=False, allow_nan=False))
                          for key, value in cell.items()) + "}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--spec", type=Path, help="frozen external experiment specification (schema-3 receipts)")
    parser.add_argument("--source-root", type=Path, help="checkout whose revision and task bytes match the spec")
    parser.add_argument("--allow-legacy", action="store_true",
                        help="review schema-1 historical receipts without claiming runtime verification")
    args = parser.parse_args()
    try:
        output = convert(args.result, args.receipt, args.allow_legacy, args.spec, args.source_root)
        print(cell_json(output))
    except (InvalidTrial, InvalidOperation, OSError, ValueError, TypeError, KeyError) as error:
        # Error messages identify failed checks only; never dump source records.
        print(f"invalid Harbor trial: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
