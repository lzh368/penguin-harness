#!/usr/bin/env python3
"""Validate a frozen Penguin evaluation matrix before computing its mean.

Inputs are a JSON plan and a directory containing one JSON object per cell.
No model calls, rubric reads, dropped cells, or automatic retries.
"""
import argparse
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
from pathlib import Path
import sys


class InvalidEvaluation(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidEvaluation(message)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    require(not path.is_symlink() and path.is_file(), f"not a regular input: {path}")
    data = path.read_bytes()
    parsed = json.loads(data, object_pairs_hook=unique_keys,
                        parse_float=Decimal,
                        parse_constant=lambda value: (_ for _ in ()).throw(
                            InvalidEvaluation(f"non-finite JSON value: {value}")))
    return parsed, hashlib.sha256(data).hexdigest()


def integer(value, name, minimum=1):
    require(type(value) is int and value >= minimum, f"invalid {name}")


def number(value, name):
    require(type(value) in (int, Decimal), f"{name} must be a JSON number")
    result = Decimal(value)
    require(result.is_finite(), f"non-finite {name}")
    return result


def aggregate(plan_path, results_dir):
    plan, plan_hash = read_json(plan_path)
    keys = {"phase", "benchmark_id", "case_ids", "runs_per_case", "agent_id",
            "expected_version", "provider", "model_id", "thinking_level"}
    require(isinstance(plan, dict) and set(plan) == keys, "plan must have exactly the documented fields")
    require(plan["phase"] in ("train", "validation", "test"), "invalid phase")
    for key in ("benchmark_id", "agent_id", "provider", "model_id", "thinking_level"):
        require(isinstance(plan[key], str) and plan[key].strip(), f"empty {key}")
    integer(plan["runs_per_case"], "runs_per_case")
    integer(plan["expected_version"], "expected_version")
    cases = plan["case_ids"]
    require(isinstance(cases, list) and cases and
            all(isinstance(c, str) and c.strip() for c in cases), "case_ids must be a nonempty string array")
    require(len(cases) == len(set(cases)), "duplicate case ID in plan")
    require(not results_dir.is_symlink() and results_dir.is_dir(), "results must be a real directory")
    paths = sorted(results_dir.iterdir())
    require(paths and all(p.is_file() and not p.is_symlink() and p.suffix == ".json" for p in paths),
            "results directory must contain only regular cell .json files")
    expected = {(case, run) for case in cases for run in range(1, plan["runs_per_case"] + 1)}
    require(len(paths) == len(expected), "missing or extra cell files")
    fields = {"protocol_version", "status", "case_id", "run", "agent_id", "expected_version",
              "provider", "model_id", "thinking_level", "score", "cost", "duration_ms", "session_id"}
    seen, sessions, hashes, scores = set(), set(), [], []
    for path in paths:
        row, digest = read_json(path)
        require(isinstance(row, dict), f"cell is not an object: {path.name}")
        require(row.get("status") == "ok", f"unscored/failed cell: {path.name}")
        require(set(row) == fields, f"wrong fields in {path.name}")
        require(type(row["protocol_version"]) is int and row["protocol_version"] == 1, "protocol version mismatch")
        integer(row["run"], "run")
        integer(row["expected_version"], "expected_version")
        require(isinstance(row["case_id"], str), "case ID must be a string")
        cell = (row["case_id"], row["run"])
        require(cell in expected and cell not in seen, f"unexpected or duplicate cell: {cell}")
        seen.add(cell)
        for key in ("agent_id", "expected_version", "provider", "model_id", "thinking_level"):
            require(row[key] == plan[key], f"{key} mismatch in {path.name}")
        session = row["session_id"]
        require(isinstance(session, str) and session.strip() and session not in sessions,
                f"missing or reused Target session in {path.name}")
        sessions.add(session)
        score = number(row["score"], "score")
        require(0 <= score <= 100, f"score outside 0..100 in {path.name}")
        require(score == score.quantize(Decimal("0.01")), f"score has more than two decimals in {path.name}")
        integer(row["duration_ms"], "duration_ms", 0)
        if row["cost"] is not None:
            require(number(row["cost"], "cost") >= 0, f"negative cost in {path.name}")
        scores.append(score)
        hashes.append({"file": path.name, "sha256": digest})
    require(seen == expected, "incomplete matrix")
    with localcontext() as context:
        context.prec = 40
        score = sum(scores) / Decimal(len(scores))
    binding = json.dumps({"plan_sha256": plan_hash, "cells": hashes}, sort_keys=True,
                         separators=(",", ":")).encode()
    return {"phase": plan["phase"], "benchmark_id": plan["benchmark_id"],
            "score": str(score), "score_max": 100, "cell_count": len(scores),
            "evaluation_id": "sha256:" + hashlib.sha256(binding).hexdigest(),
            "plan_sha256": plan_hash, "cell_sha256": hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(aggregate(args.plan, args.results), ensure_ascii=False, indent=2))
    except (InvalidEvaluation, InvalidOperation, OSError, ValueError, TypeError) as error:
        print(f"invalid evaluation: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
