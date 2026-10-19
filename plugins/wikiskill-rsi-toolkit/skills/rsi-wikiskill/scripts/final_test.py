#!/usr/bin/env python3
"""Record an unchanged final deployment, without inventing a second test result.

The caller must audit the real H0 test and freeze this policy before learning.
Snapshots/conditions are evidence, not OS isolation or runtime attestations.
"""
import argparse
from contextlib import nullcontext
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("final_test_state", Path(__file__).with_name("state.py"))
states = importlib.util.module_from_spec(spec)
spec.loader.exec_module(states)
require = states.require
REUSE = "reuse_unchanged"
REPEAT = "repeat"
POLICIES = (REUSE, REPEAT)


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path):
    path = Path(path).absolute()
    states.no_symlink_chain(path)
    return json.loads(path.read_bytes(), object_pairs_hook=states.unique_keys)


def snapshot(root):
    root = Path(root).absolute()
    states.no_symlink_chain(root)
    require(root.is_dir(), "deployment snapshot missing")
    files, raw = {}, {}
    for file in sorted(root.rglob("*")):
        states.no_symlink_chain(file)
        if file.is_dir():
            continue
        require(file.is_file(), "snapshot contains a special file")
        name = file.relative_to(root).as_posix()
        data = file.read_bytes()
        raw[name] = states.sha(data)
        # Only this deployment bookkeeping field may differ. Prompts, model,
        # memory, tools, and every other file remain byte-for-byte compared.
        if name == "system_config.yaml":
            data, count = re.subn(rb"(?m)^version: [0-9]+\r?$", b"version: <deployment>", data)
            require(count == 1, "snapshot requires one integer deployment version")
        files[name] = states.sha(data)
    require("system_config.yaml" in files and "AGENTS.md" in files, "incomplete deployment snapshot")
    return {"path": str(root), "raw_files": raw, "effective_files": files}


def conditions(path, binding):
    value = read(path)
    require(isinstance(value, dict) and value.get("binding") == binding, "conditions do not bind this experiment")
    for field in ("runtime", "benchmark", "execution"):
        require(isinstance(value.get(field), dict) and bool(value[field]), "missing conditions: " + field)
    return {"path": str(Path(path).absolute()), "sha256": digest(value), "value": value}


def make_record(out, h0_state, final_state, h0_conditions, final_conditions, h0_summary, binding, iterations, *, already_locked=False):
    require(type(iterations) is int and iterations > 0, "invalid iteration limit")
    store = states.Store(out)
    with nullcontext() if already_locked else store.lock():
        state = states.load_verified(store)
        require(state["pending"] is None, "pending candidate cannot skip final test")
        require(state["round"] <= iterations and
                (state["round"] == iterations or Decimal(state["best_score"]) == Decimal(state["score_max"])),
                "learning has not finished")
        require(not state["accepted"]["manifest"] and not store.tree("accepted/skills"),
                "final skills are nonempty; run final test (including after a later rejection)")
        state_hash = store.digest("state.json")
    initial, final = snapshot(h0_state), snapshot(final_state)
    require(initial["effective_files"] == final["effective_files"], "effective Target state changed")
    empty_block = states.render_text({}, block=True)
    for item in (initial, final):
        text = (Path(item["path"]) / "AGENTS.md").read_text()
        require(text.count(empty_block) == 1, "empty derived Skill block is missing")
    before, after = conditions(h0_conditions, binding), conditions(final_conditions, binding)
    require(before["value"] == after["value"], "evaluation conditions changed")
    require(isinstance(h0_summary, dict) and "score" in h0_summary, "missing audited H0 test")
    states.score(h0_summary["score"], "100")
    body = {"schema_version": 1, "status": "skipped_unchanged_state", "policy": REUSE,
            "binding": binding, "state_sha256": state_hash, "max_iterations": iterations,
            "h0_state": initial, "final_state": final,
            "h0_conditions": before, "final_conditions": after,
            "h0_evaluation_sha256": digest(h0_summary),
            "test_final": None, "delta": None, "additional_target_trials": 0,
            "score_source": "test-H0 (same deployment; not a second measurement)"}
    return {**body, "record_sha256": digest(body)}


def verify_record(record, out, h0_summary, binding, iterations, *, already_locked=False):
    expected = make_record(out, record["h0_state"]["path"], record["final_state"]["path"],
                           record["h0_conditions"]["path"], record["final_conditions"]["path"],
                           h0_summary, binding, iterations, already_locked=already_locked)
    require(record == expected, "final-test skip evidence changed")
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("out", "h0-state", "final-state", "h0-conditions", "final-conditions", "h0-summary", "record"):
        parser.add_argument("--" + flag, type=Path, required=True)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--iterations", type=int, required=True)
    args = parser.parse_args()
    try:
        value = make_record(args.out, args.h0_state, args.final_state, args.h0_conditions,
                            args.final_conditions, read(args.h0_summary), args.binding, args.iterations)
        states.no_symlink_chain(args.record.absolute())
        with args.record.open("xb") as stream:
            stream.write(canonical(value))
        print(json.dumps({"status": value["status"], "record": str(args.record)}))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        print("invalid final-test skip: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
