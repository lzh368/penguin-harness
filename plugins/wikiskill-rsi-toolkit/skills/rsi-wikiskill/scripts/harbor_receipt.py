#!/usr/bin/env python3
"""Build a schema-2 receipt after independent deployment and isolation audits.

Evaluator only. Expected binding is the prelaunch ledger; observed-state is a
separate container measurement (archive/state hashes and version). The remaining
observed fields come from actual root traces. Never creates audit verdicts.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True


def sibling(name):
    spec = importlib.util.spec_from_file_location("receipt_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


converter = sibling("harbor_result")
trace = sibling("trace_binding")


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def build(result_path, private, request_path, expected_path, observed_state_path):
    private = trace.safe_path(private)
    converter.require(expected_path.resolve() != observed_state_path.resolve(),
                      "expected ledger and container measurement must be different inputs")
    request = trace.parse(trace.read(request_path))
    expected = trace.parse(trace.read(expected_path))
    observed = trace.parse(trace.read(observed_state_path))
    converter.exact(observed, {"deployed_state_sha256", "archive_sha256", "state_version"},
                    "container measurement")
    observation = trace.observe(result_path)
    trace.check_binding(observation, expected, request)
    first = observation["root_shards"][0]
    observed = {**observed, **{key: first[key] for key in
                ("container_agent_state", "container_workspace", "injected_block_sha256")}}
    result_raw = trace.read(result_path)
    result = trace.parse(result_raw)
    plan_raw = trace.read(private / "plan.json")
    plan = trace.parse(plan_raw)
    audits = []
    for check in converter.AUDIT_CHECKS:
        raw = trace.read(private / "audit" / (check + ".json"))
        audits.append({"check": check, "verdict": "pass", "artifact": f"audit/{check}.json",
                       "artifact_sha256": trace.sha(raw)})
    # convert() below validates each existing audit's actual verdict. This list
    # cannot turn a fail, unknown or absent audit into an accepted cell.
    receipt = {"schema_version": 2, "benchmark_revision": converter.BENCHMARK_REVISION,
               "request": request, "thinking_level": plan["thinking_level"],
               "task_name": result["task_name"], "trial_dir": result_path.parent.name,
               "trial_id": result["id"], "result_sha256": trace.sha(result_raw),
               "phase_plan": {"artifact": "plan.json", "sha256": trace.sha(plan_raw)},
               "state_binding": {"expected": expected, "observed": observed}, "audit": audits,
               "runtime_evidence": {"artifact": "runtime-observation.json", "sha256": trace.sha(encoded(observation))}}
    outputs = {"runtime-observation.json": encoded(observation), "receipt.json": encoded(receipt)}
    converter.require(all(not (private / name).exists() and not (private / name).is_symlink()
                          for name in outputs), "receipt/evidence already exists; preserve it")
    # Validate in a private temporary directory before publishing any receipt.
    with tempfile.TemporaryDirectory(prefix=".receipt-", dir=private) as folder:
        staging = Path(folder)
        (staging / "plan.json").write_bytes(plan_raw)
        (staging / "audit").mkdir()
        for check in converter.AUDIT_CHECKS:
            relative = Path("audit") / (check + ".json")
            (staging / relative).write_bytes(trace.read(private / relative))
        for name, raw in outputs.items():
            (staging / name).write_bytes(raw)
        converter.convert(result_path, staging / "receipt.json")
    for name, raw in outputs.items():
        with (private / name).open("xb") as handle:
            handle.write(raw)
    return {"receipt": str(private / "receipt.json"), "schema_version": 2,
            "root_shards": len(observation["root_shards"]),
            "scope": "trace binding recomputed; container measurements and isolation independently audited"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("result", "private", "request", "expected", "observed-state"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.result, args.private, args.request, args.expected, args.observed_state)))
    except (OSError, ValueError, TypeError, KeyError):
        print("receipt refused; review inputs and private evidence, no successful receipt generated", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
