#!/usr/bin/env python3
"""Persistent scheduling gate for an operator's WikiSkill supervisor.

No model calls, steering, subprocess termination or container operations. The
caller must obey every decision before launch/nudge/next replica. Observations
come from native session/cost JSON and an exhaustive launch inventory; this is
a soft spending threshold, not a provider-side spending limit.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

sys.dont_write_bytecode = True


def sibling(name):
    spec = importlib.util.spec_from_file_location("guard_" + name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evidence = sibling("trace_binding")
require = evidence.require


def money(value):
    require(isinstance(value, (str, int, float, Decimal)) and not isinstance(value, bool), "unknown cost")
    value = Decimal(str(value))
    require(value.is_finite() and value >= 0, "invalid cost")
    return value


def host_amount(bucket):
    require(isinstance(bucket, dict) and bucket.get("hasUncosted") is False, "host cost incomplete")
    # This is the native empty usage bucket, not an unpriced paid request.
    if bucket.get("cost") is None and type(bucket.get("requests")) is int and bucket["requests"] == 0:
        require(type(bucket.get("total")) is int and bucket["total"] == 0, "not an empty usage bucket")
        return Decimal(0)
    return money(bucket["cost"])


def known_spending(snapshot):
    """A partial sample may stop work but must never authorize continuing it."""
    known, trial_ids = Decimal(0), set()
    try:
        known += money(snapshot["host_cost"]["summary"]["total"]["cost"])
    except (ValueError, TypeError, KeyError, InvalidOperation):
        pass
    attempts = snapshot.get("attempts", [])
    for attempt in attempts if isinstance(attempts, list) else []:
        try:
            require(attempt["status"] == "finished", "not settled")
            result = evidence.parse(evidence.read(attempt["result"]))
            require("trial_name" in result and isinstance(result["id"], str), "not a trial")
            amount = money((result.get("agent_result") or {}).get("cost_usd"))
            if result["id"] not in trial_ids:
                known += amount
                trial_ids.add(result["id"])
        except (OSError, ValueError, TypeError, KeyError, InvalidOperation, AttributeError):
            pass
    try:
        global_known = money(snapshot["global_spent_usd"])
    except (ValueError, TypeError, KeyError, InvalidOperation):
        global_known = Decimal(0)
    return known, global_known


def controller_session(sessions, identity):
    require(isinstance(sessions, list), "session list unavailable")
    rows = [row for row in sessions if isinstance(row, dict) and
            all(row.get(key) == identity[key] for key in
                ("sessionId", "agentId", "projectId", "workspace", "source"))]
    require(len(rows) == 1, "pinned root Controller missing or ambiguous")
    row = rows[0]
    require(row["source"] in ("cli", "user", "api"), "Controller cannot be a leaf session")
    return row


def remember_attempts(state, attempts):
    """Remember a fresh, structurally complete inventory before other checks.

    Missing session/cost data must not erase a launch we have already observed.
    Old guards start with an empty inventory; only the operator can account for
    launches that predate the first recorded observation.
    """
    require(isinstance(attempts, list), "launch inventory unavailable")
    observed = {}
    for attempt in attempts:
        require(isinstance(attempt, dict) and set(attempt) == {"launch_id", "status", "result"}, "invalid attempt")
        launch = attempt["launch_id"]
        require(isinstance(launch, str) and launch and launch not in observed, "duplicate launch")
        if attempt["status"] == "active":
            require(attempt["result"] is None, "active attempt must not masquerade as settled")
        else:
            require(attempt["status"] == "finished" and isinstance(attempt["result"], str) and
                    Path(attempt["result"]).is_absolute(), "unsettled attempt or non-absolute result")
        observed[launch] = {"status": attempt["status"], "result": attempt["result"]}
    previous = state.get("observed_attempts", {})
    require(isinstance(previous, dict) and previous.keys() <= observed.keys(), "observed launch disappeared")
    for launch, old in previous.items():
        require(isinstance(old, dict) and old.get("status") in ("active", "finished"), "invalid saved launch")
        current = observed[launch]
        if old["status"] == "finished":
            require(current["status"] == "finished" and current["result"] == old["result"],
                    "finished launch changed")
            if "result_sha256" in old:
                current["result_sha256"] = old["result_sha256"]
    state["observed_attempts"] = observed
    return observed


def target_cost(attempts, observed):
    total, trial_ids, active = Decimal(0), set(), 0
    for attempt in attempts:
        if attempt["status"] == "active":
            active += 1
            continue
        raw = evidence.read(attempt["result"])
        result = evidence.parse(raw)
        require("trial_name" in result and isinstance(result.get("id"), str), "expected trial, not job-level cost")
        require(result["id"] not in trial_ids, "duplicate trial cost")
        trial_ids.add(result["id"])
        # Include failed/retried trials too. Missing/null cost is never zero.
        total += money((result.get("agent_result") or {}).get("cost_usd"))
        saved = observed[attempt["launch_id"]]
        digest = evidence.sha(raw)
        require(saved.get("result_sha256", digest) == digest, "finished result bytes changed")
        saved["result_sha256"] = digest
    return total, active


def session_quiet(row):
    queued = row.get("pendingFollowUpCount")
    require(type(queued) is int and queued >= 0, "unknown pending follow-ups")
    background = row.get("backgroundTasks", {"processes": 0, "subagents": 0})
    require(isinstance(background, dict) and all(type(background.get(key)) is int and background[key] >= 0
            for key in ("processes", "subagents")), "invalid background activity")
    return row.get("status") == "idle" and queued == 0 and not any(background.values())


def initial(identity, cap, global_cap):
    require(isinstance(identity, dict) and set(identity) ==
            {"sessionId", "agentId", "projectId", "workspace", "source"}, "invalid Controller identity")
    require(all(isinstance(v, str) and v for v in identity.values()), "empty Controller identity")
    require(identity["source"] in ("cli", "user", "api"), "not a root session")
    require(money(cap) > 0 and money(global_cap) >= money(cap), "invalid spending thresholds")
    return {"schema_version": 1, "status": "active", "identity": identity,
            "cap_usd": str(money(cap)), "global_cap_usd": str(money(global_cap)),
            "last_spent_usd": "0", "last_global_usd": "0", "observed_attempts": {}, "reason": None}


def check(state, snapshot, now=None):
    require(state.get("schema_version") == 1 and state.get("status") in
            ("active", "stopped", "complete"), "invalid guard state")
    if state["status"] != "active":
        return {"action": "stop", "reason": state["reason"] or state["status"],
                "sessionId": state["identity"]["sessionId"]}
    try:
        known, global_known = known_spending(snapshot)
        if known >= money(state["cap_usd"]) or global_known >= money(state["global_cap_usd"]):
            state.update(status="stopped", reason="spending_threshold")
            return {"action": "stop", "reason": state["reason"], "sessionId": state["identity"]["sessionId"]}
        stamp = datetime.fromisoformat(snapshot["captured_at"].replace("Z", "+00:00"))
        now = now or datetime.now(timezone.utc)
        require(stamp.utcoffset() is not None and 0 <= (now - stamp).total_seconds() <= 120, "stale observation")
        require(snapshot["inventory_complete"] is True, "launch inventory incomplete")
        observed = remember_attempts(state, snapshot["attempts"])
        target, active = target_cost(snapshot["attempts"], observed)
        root = controller_session(snapshot["sessions"], state["identity"])
        host = snapshot["host_cost"]["summary"]["total"]
        spent = host_amount(host) + target
        require(snapshot["global_cost_complete"] is True, "global cost incomplete")
        global_spent = money(snapshot["global_spent_usd"])
        require(global_spent >= spent, "global cost excludes this replica")
        require(spent >= money(state["last_spent_usd"]) and
                global_spent >= money(state["last_global_usd"]), "cost observation regressed")
        state["last_spent_usd"], state["last_global_usd"] = str(spent), str(global_spent)
        if spent >= money(state["cap_usd"]) or global_spent >= money(state["global_cap_usd"]):
            state.update(status="stopped", reason="spending_threshold")
            return {"action": "stop", "reason": state["reason"], "sessionId": root["sessionId"]}
        # Nudge only this pinned root and only when no work/steering is outstanding.
        quiet = session_quiet(root)
        leaves = [row for row in snapshot["sessions"] if isinstance(row, dict) and
                  row.get("projectId") == root["projectId"] and row.get("agentId") == root["agentId"] and
                  row.get("source") == "subagent"]
        leaves_quiet = all(session_quiet(row) for row in leaves)
        return {"action": "continue", "sessionId": root["sessionId"],
                "may_nudge": quiet and active == 0 and leaves_quiet,
                "active_trials": active, "spent_usd": str(spent),
                "limit_kind": "soft_threshold; in_flight_cost_not_yet_settled"}
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation, AttributeError):
        return {"action": "wait", "reason": "incomplete_or_inconsistent_observation", "may_nudge": False}


def finish(state, verify):
    require(state["status"] == "active", "stopped/complete guard cannot auto-resume")
    try:
        report = verify()
        require(isinstance(report, dict) and report.get("status") == "verified", "wrapup did not verify")
    except Exception:
        # A broken verifier is also a failed wrapup. Persist a stop before the
        # operator diagnoses it; never let a restarted relay interpret it as idle.
        state.update(status="stopped", reason="wrapup_failed")
        return {"action": "stop", "reason": "wrapup_failed"}
    state.update(status="complete", reason="verified_wrapup")
    return {"action": "complete", "report": report}


@contextmanager
def locked(path):
    path = evidence.safe_path(path)
    require(path.parent.is_dir(), "guard parent must exist")
    lock = evidence.safe_path(path.with_suffix(path.suffix + ".lock"))
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield path


def save(path, state):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".guard-", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(state, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guard", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--identity", required=True, type=Path)
    init.add_argument("--cap", required=True)
    init.add_argument("--global-cap", required=True)
    observe = commands.add_parser("check")
    observe.add_argument("--snapshot", required=True, type=Path)
    stop = commands.add_parser("stop")
    stop.add_argument("--reason", required=True)
    done = commands.add_parser("finish")
    done.add_argument("--campaign", type=Path, required=True)
    done.add_argument("--replica", choices=("r1", "r2", "r3"), required=True)
    done.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    try:
        with locked(args.guard) as path:
            if args.command == "init":
                require(not path.exists(), "guard exists; cannot reset stop history")
                state = initial(evidence.parse(evidence.read(args.identity)), args.cap, args.global_cap)
                result = {"action": "initialized"}
            else:
                state = evidence.parse(evidence.read(path))
                if args.command in ("check", "finish"):
                    result = check(state, evidence.parse(evidence.read(args.snapshot)))
                    if args.command == "finish" and result["action"] == "continue":
                        if not result["may_nudge"]:
                            result = {"action": "wait", "reason": "work_still_outstanding", "may_nudge": False}
                        else:
                            def verify():
                                campaign = sibling("campaign")
                                _, config, _ = campaign.load_campaign(args.campaign)
                                replica = campaign.replica_config(config, args.replica)
                                require(replica["controller_agent_id"] == state["identity"]["agentId"] and
                                        config["project_id"] == state["identity"]["projectId"], "guard/campaign identity mismatch")
                                return campaign.verify_replica(args.campaign, args.replica)
                            result = finish(state, verify)
                else:
                    state.update(status="stopped", reason=args.reason)
                    result = {"action": "stop", "reason": args.reason}
            save(path, state)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["action"] in ("initialized", "continue", "complete") else 3
    except (OSError, ValueError, TypeError, KeyError, InvalidOperation):
        print("guard refused; do not launch, nudge or advance replica", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
