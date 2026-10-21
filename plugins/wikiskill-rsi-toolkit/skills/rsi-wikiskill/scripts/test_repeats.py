#!/usr/bin/env python3
"""Summarize audited repeated tests of one frozen Skill; never launch tasks.

Checks records, not the truth of runtime audits. Input scores use the frozen
0..100 normalization. SD is across complete test means, not across tasks.
"""
import argparse
from decimal import Decimal, InvalidOperation, localcontext
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
HASH = re.compile(r"[0-9a-f]{64}\Z")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def identifier(value):
    require(isinstance(value, str) and bool(value.strip()), "missing identifier")
    return value


def digest(value):
    require(isinstance(value, str) and HASH.fullmatch(value), "invalid evidence/conditions/Skill hash")
    return value


def unique_ids(values):
    require(isinstance(values, list) and values, "expected nonempty identifier list")
    for value in values:
        identifier(value)
    require(len(set(values)) == len(values), "duplicate identifier")
    return values


def score(value):
    require(type(value) in (str, int, float, Decimal), "invalid score type")
    value = Decimal(str(value))
    require(value.is_finite() and 0 <= value <= 100, "score outside finite 0..100")
    return value


def statistics(values):
    require(len(values) >= 2, "at least two measurements needed for sample SD")
    with localcontext() as context:
        context.prec = 40
        mean = sum(values) / len(values)
        sd = (sum((value - mean) ** 2 for value in values) / (len(values) - 1)).sqrt()
        return {"n": len(values), "values": list(map(str, values)), "mean": str(mean),
                "sample_std": str(sd), "ddof": 1,
                "error_bar": {"kind": "mean_plus_minus_one_sample_sd", "lower": str(mean - sd),
                              "upper": str(mean + sd)}}


def _summarize(data):
    require(type(data.get("schema_version")) is int and data["schema_version"] == 1, "unsupported schema")
    require(data.get("mode") == "fixed_skill_test_repeats", "expected fixed-Skill test repeats, not learning replicas")
    require(type(data.get("std_ddof")) is int and data["std_ddof"] == 1, "sample SD requires ddof=1")
    repeats = unique_ids(data["repeat_ids"])
    require(len(repeats) >= 2, "at least two planned repeats required")
    groups = data["groups"]
    require(isinstance(groups, list) and groups, "missing learning groups")
    group_ids, evaluation_ids, sessions, evidence = set(), set(), set(), set()
    reports = []
    for group in groups:
        gid = identifier(group["id"])
        require(gid not in group_ids, "duplicate group")
        group_ids.add(gid)
        identifier(group["learning_run_id"])
        cases = unique_ids(group["case_ids"])
        condition_hash = digest(group["conditions_sha256"])
        h0_hash = digest(group["h0_skill_sha256"])
        final_hash = group["final_skill_sha256"]
        if final_hash is None:
            digest(group["unchanged_state_receipt_sha256"])
            conditions = {"h0": h0_hash}
        else:
            digest(final_hash)
            require(final_hash != h0_hash, "unchanged Skill must use baseline-only reporting")
            require(group.get("unchanged_state_receipt_sha256") is None, "changed Skill cannot claim unchanged receipt")
            conditions = {"h0": h0_hash, "final": final_hash}
        expected = {(repeat, condition) for repeat in repeats for condition in conditions}
        rows = group["evaluations"]
        require(isinstance(rows, list) and len(rows) == len(expected), "missing or extra evaluations")
        means = {}
        for row in rows:
            key = (row["repeat_id"], row["condition"])
            require(key in expected and key not in means, "duplicate or unexpected repeat/condition")
            require(row["conditions_sha256"] == condition_hash, "runtime/task/scoring conditions drift")
            require(row["skill_sha256"] == conditions[key[1]], "frozen Skill drift")
            eid = identifier(row["evaluation_id"])
            require(eid not in evaluation_ids, "reused evaluation")
            evaluation_ids.add(eid)
            cells = row["cells"]
            require(isinstance(cells, list) and len(cells) == len(cases), "missing or extra task cells")
            seen, values = set(), []
            for cell in cells:
                case = cell["case_id"]
                require(case in cases and case not in seen, "duplicate or unexpected task")
                seen.add(case)
                require(cell["status"] == "ok", "failed/unscored cell cannot enter the mean")
                session = identifier(cell["session_id"])
                require(session not in sessions, "reused Target session")
                sessions.add(session)
                cell_hash = digest(cell["evidence_sha256"])
                require(cell_hash not in evidence, "reused cell evidence")
                evidence.add(cell_hash)
                values.append(score(cell["score"]))
            means[key] = sum(values) / len(values)
        baseline = [means[r, "h0"] for r in repeats]
        final = [means[r, "final"] for r in repeats] if final_hash else None
        reports.append({"id": gid, "learning_run_id": group["learning_run_id"], "case_count": len(cases),
                        "conditions_sha256": condition_hash, "h0_skill_sha256": h0_hash,
                        "final_skill_sha256": final_hash,
                        "status": "measured_pair" if final else "baseline_only_unchanged_state",
                        "baseline": statistics(baseline), "final": statistics(final) if final else None,
                        "paired_delta": statistics([b - a for a, b in zip(baseline, final)]) if final else None})

    def across(selected, field):
        values = [sum(Decimal(g[field]["values"][i]) for g in selected) / len(selected)
                  for i in range(len(repeats))]
        return statistics(values)

    paired = [g for g in reports if g["final"] is not None]
    return {"schema_version": 1, "mode": data["mode"], "repeat_ids": repeats, "std_ddof": 1,
            "scope": "one frozen learning outcome per group; repeated test execution, not repeated learning",
            "audit_scope": "record consistency and statistics only; original scoring/injection audits required",
            "groups": reports,
            "all_groups_baseline": {"group_ids": [g["id"] for g in reports],
                                    "aggregation": "equal group means within each repeat, then mean/sample SD",
                                    **across(reports, "baseline")},
            "paired_groups": {"group_ids": [g["id"] for g in paired], "group_count": len(paired),
                              "total_group_count": len(reports),
                              "scope": "measured pairs only; not the whole-campaign algorithm gain",
                              **{key: across(paired, key) if paired else None
                                 for key in ("baseline", "final", "paired_delta")}},
            "evidence_count": len(evidence)}


def summarize(data):
    with localcontext() as context:
        context.prec = 40
        return _summarize(data)


def unique_keys(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "duplicate JSON key")
        value[key] = item
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()
    try:
        require(args.input.is_file() and not args.input.is_symlink(), "input must be a regular file")
        data = json.loads(args.input.read_text(), object_pairs_hook=unique_keys, parse_float=Decimal)
        print(json.dumps(summarize(data), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, AttributeError, InvalidOperation, OSError) as error:
        print("invalid repeated tests: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
