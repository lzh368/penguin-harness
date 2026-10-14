"""Regressions for spending-stop reversal, wrong Session keys and failed wrapup."""
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_harbor_result import module, ROOT

guard = module("run_guard")


class RunGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = {"sessionId": "root-session", "agentId": "controller",
                         "projectId": "project", "workspace": "/synthetic/run", "source": "cli"}
        self.state = guard.initial(self.identity, "25", "75")
        self.now = datetime.now(timezone.utc)
        self.snapshot = {"captured_at": self.now.isoformat(),
                         "sessions": [{**self.identity, "status": "idle", "pendingFollowUpCount": 0}],
                         "host_cost": {"summary": {"total": {"hasUncosted": False, "cost": 1}}},
                         "inventory_complete": True, "attempts": [],
                         "global_cost_complete": True, "global_spent_usd": 1}

    def test_sessionId_and_pinned_identity_win_over_newer_leaf(self):
        self.snapshot["sessions"].insert(0, {**self.identity, "sessionId": "leaf", "source": "subagent",
                                             "status": "idle", "pendingFollowUpCount": 0})
        result = guard.check(self.state, self.snapshot, self.now)
        self.assertEqual(result["sessionId"], "root-session")
        self.assertTrue(result["may_nudge"])
        self.snapshot["sessions"].pop()
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "wait")

    def test_per_replica_stop_persists_across_restart_and_lower_readings(self):
        self.snapshot["host_cost"]["summary"]["total"]["cost"] = 26
        self.snapshot["global_spent_usd"] = 26
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "stop")
        disk = self.root / "guard.json"
        guard.save(disk, self.state)
        reloaded = json.loads(disk.read_text())
        self.snapshot["host_cost"]["summary"]["total"]["cost"] = 0
        self.assertEqual(guard.check(reloaded, self.snapshot, self.now)["action"], "stop")

    def test_global_threshold_stops_before_replica_threshold(self):
        self.snapshot["global_spent_usd"] = 75
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "stop")

    def test_known_threshold_is_not_masked_by_missing_other_observations(self):
        self.snapshot["host_cost"]["summary"]["total"]["cost"] = 26
        self.snapshot["global_cost_complete"] = False
        self.snapshot["sessions"] = []
        self.snapshot["attempts"] = [{"launch_id": "bad", "status": "finished", "result": "/missing"}]
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "stop")
        self.snapshot["host_cost"]["summary"]["total"]["cost"] = 1
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "stop")

    def test_native_empty_usage_bucket_can_start_but_unpriced_usage_cannot(self):
        bucket = self.snapshot["host_cost"]["summary"]["total"]
        bucket.update(total=0, requests=0, cost=None, hasUncosted=False)
        self.snapshot["global_spent_usd"] = 0
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "continue")
        bucket["requests"] = 1
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "wait")

    def test_unknown_stale_or_regressing_cost_never_becomes_zero(self):
        for change in (
            lambda s: s["host_cost"]["summary"]["total"].update(hasUncosted=True),
            lambda s: s["host_cost"]["summary"]["total"].update(cost=None),
            lambda s: s.update(global_cost_complete=False),
            lambda s: s.update(inventory_complete=False),
            lambda s: s.update(captured_at=(self.now - timedelta(minutes=3)).isoformat()),
            lambda s: s.update(global_spent_usd="NaN"),
        ):
            snapshot = copy.deepcopy(self.snapshot)
            change(snapshot)
            self.assertEqual(guard.check(self.state, snapshot, self.now)["action"], "wait")
        guard.check(self.state, self.snapshot, self.now)
        self.snapshot["host_cost"]["summary"]["total"]["cost"] = 0
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "wait")

    def test_no_nudge_during_active_trial_or_queued_steering(self):
        self.snapshot["attempts"] = [{"launch_id": "attempt", "status": "active", "result": None}]
        result = guard.check(self.state, self.snapshot, self.now)
        self.assertEqual(result["action"], "continue")
        self.assertFalse(result["may_nudge"])
        self.state = guard.initial(self.identity, "25", "75")
        self.snapshot["attempts"] = []
        self.snapshot["sessions"][0]["pendingFollowUpCount"] = 1
        self.assertFalse(guard.check(self.state, self.snapshot, self.now)["may_nudge"])

    def test_no_nudge_during_native_background_work_or_visible_running_leaf(self):
        self.snapshot["sessions"][0]["backgroundTasks"] = {"processes": 0, "subagents": 1}
        self.assertFalse(guard.check(self.state, self.snapshot, self.now)["may_nudge"])
        del self.snapshot["sessions"][0]["backgroundTasks"]
        self.snapshot["sessions"].append({**self.identity, "sessionId": "leaf", "source": "subagent",
                                         "status": "running", "pendingFollowUpCount": 0})
        self.assertFalse(guard.check(self.state, self.snapshot, self.now)["may_nudge"])
        for status in ("compacting", None):
            self.snapshot["sessions"][-1]["status"] = status
            self.assertFalse(guard.check(self.state, self.snapshot, self.now)["may_nudge"])

    def test_failed_trials_count_and_job_summary_is_not_double_counted(self):
        result = self.root / "result.json"
        result.write_text(json.dumps({"id": "trial-uuid", "trial_name": "trial",
                                      "exception_info": {"type": "error"}, "agent_result": {"cost_usd": 2}}))
        self.snapshot["attempts"] = [{"launch_id": "attempt", "status": "finished", "result": str(result)}]
        self.snapshot["global_spent_usd"] = 3
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["spent_usd"], "3")
        self.snapshot["attempts"].append({"launch_id": "retry", "status": "finished", "result": str(result)})
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "wait")
        result.write_text('{"stats":{"cost_usd":2}}')
        self.snapshot["attempts"].pop()
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "wait")

    def test_missing_finished_trial_cost_blocks_scheduling(self):
        self.snapshot["attempts"] = [{"launch_id": "attempt", "status": "finished", "result": "/missing/result.json"}]
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "wait")

    def test_false_or_exception_from_wrapup_is_terminal(self):
        result = guard.finish(self.state, lambda: False)
        self.assertEqual(result["action"], "stop")
        self.assertEqual(self.state["reason"], "wrapup_failed")
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "stop")
        with self.assertRaises(ValueError):
            guard.finish(self.state, lambda: {"status": "verified"})

    def test_unexpected_wrapup_exception_is_persistable_stop(self):
        def broken_verifier():
            raise RuntimeError("synthetic broken verifier")
        self.assertEqual(guard.finish(self.state, broken_verifier)["action"], "stop")
        state_path = self.root / "guard.json"
        guard.save(state_path, self.state)
        reloaded = json.loads(state_path.read_text())
        self.assertEqual(guard.check(reloaded, self.snapshot, self.now)["action"], "stop")

    def test_successful_wrapup_is_the_only_complete_transition(self):
        result = guard.finish(self.state, lambda: {"status": "verified", "synthetic": True})
        self.assertEqual(result["action"], "complete")
        self.assertEqual(guard.check(self.state, self.snapshot, self.now)["action"], "stop")

    def test_cli_preserves_stop_and_refuses_reinitialization(self):
        identity, snapshot, state = (self.root / name for name in ("identity.json", "snapshot.json", "guard.json"))
        identity.write_text(json.dumps(self.identity))
        snapshot.write_text(json.dumps(self.snapshot))
        command = [sys.executable, "-B", str(ROOT / "scripts/run_guard.py"), "--guard", str(state)]
        def call(*args):
            return subprocess.run([*command, *map(str, args)], capture_output=True, text=True)
        self.assertEqual(call("init", "--identity", identity, "--cap", "25", "--global-cap", "75").returncode, 0)
        self.assertEqual(call("stop", "--reason", "operator_stop").returncode, 3)
        self.assertEqual(call("init", "--identity", identity, "--cap", "100", "--global-cap", "100").returncode, 2)
        self.assertEqual(call("check", "--snapshot", snapshot).returncode, 3)
        self.assertEqual(json.loads(state.read_text())["reason"], "operator_stop")

    def test_cli_failed_campaign_loading_is_persistent_wrapup_stop(self):
        snapshot, state = self.root / "snapshot.json", self.root / "guard.json"
        snapshot.write_text(json.dumps(self.snapshot))
        guard.save(state, self.state)
        (self.root / "campaign.json").write_text("{}")
        command = [sys.executable, "-B", str(ROOT / "scripts/run_guard.py"), "--guard", str(state)]
        finish = subprocess.run([*command, "finish", "--campaign", str(self.root), "--replica", "r1",
                                 "--snapshot", str(snapshot)], capture_output=True, text=True)
        self.assertEqual(finish.returncode, 3, finish.stderr)
        self.assertEqual(json.loads(state.read_text())["reason"], "wrapup_failed")
        check = subprocess.run([*command, "check", "--snapshot", str(snapshot)], capture_output=True, text=True)
        self.assertEqual(json.loads(check.stdout)["action"], "stop")

    def cli_guard(self, name):
        folder = self.root / name
        folder.mkdir()
        identity, snapshot, state = (folder / name for name in ("identity.json", "snapshot.json", "guard.json"))
        identity.write_text(json.dumps(self.identity))
        command = [sys.executable, "-B", str(ROOT / "scripts/run_guard.py"), "--guard", str(state)]

        def call(*args):
            return subprocess.run([*command, *map(str, args)], capture_output=True, text=True)

        self.assertEqual(call("init", "--identity", identity, "--cap", "25", "--global-cap", "75").returncode, 0)

        def observe(value, finish=False):
            snapshot.write_text(json.dumps(value))
            args = ["finish", "--campaign", folder / "missing-campaign", "--replica", "r1"] if finish else ["check"]
            result = call(*args, "--snapshot", snapshot)
            self.assertIn(result.returncode, (0, 3), result.stderr)
            return json.loads(result.stdout)

        return state, observe

    def test_cli_old_known_excess_is_terminal_but_old_under_budget_cannot_continue(self):
        for label, field, value in (("local", "local", 26), ("global", "global", 75)):
            with self.subTest(label=label):
                state, observe = self.cli_guard(label)
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["captured_at"] = (self.now - timedelta(minutes=3)).isoformat()
                if field == "local":
                    snapshot["host_cost"]["summary"]["total"]["cost"] = value
                else:
                    snapshot["global_spent_usd"] = value
                self.assertEqual(observe(snapshot)["action"], "stop")
                self.assertEqual(json.loads(state.read_text())["reason"], "spending_threshold")
                self.assertEqual(observe(self.snapshot)["action"], "stop")
        _, observe = self.cli_guard("old-under-budget")
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["captured_at"] = (self.now - timedelta(minutes=3)).isoformat()
        self.assertEqual(observe(snapshot)["action"], "wait")

    def test_idle_leaf_queue_and_background_work_prevent_nudge_and_finish(self):
        for label, activity in (
            ("queue", {"pendingFollowUpCount": 1}),
            ("process", {"backgroundTasks": {"processes": 1, "subagents": 0}}),
            ("subagent", {"backgroundTasks": {"processes": 0, "subagents": 1}}),
        ):
            with self.subTest(label=label):
                state, observe = self.cli_guard("leaf-" + label)
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["sessions"].append({**self.identity, "sessionId": "leaf", "source": "subagent",
                                             "status": "idle", "pendingFollowUpCount": 0, **activity})
                decision = observe(snapshot)
                self.assertEqual(decision["action"], "continue")
                self.assertFalse(decision["may_nudge"])
                self.assertEqual(observe(snapshot, finish=True)["action"], "wait")
                self.assertEqual(json.loads(state.read_text())["status"], "active")

    def test_unknown_idle_leaf_queue_or_background_activity_never_authorizes_nudge(self):
        for activity in ({"pendingFollowUpCount": None}, {"backgroundTasks": {"processes": None, "subagents": 0}}):
            snapshot = copy.deepcopy(self.snapshot)
            snapshot["sessions"].append({**self.identity, "sessionId": "leaf", "source": "subagent",
                                         "status": "idle", "pendingFollowUpCount": 0, **activity})
            self.assertFalse(guard.check(self.state, snapshot, self.now)["may_nudge"])

    def test_cli_seen_active_launch_survives_incomplete_observation_and_busy_finish(self):
        for label in ("complete", "missing-sessions", "unknown-cost", "finish"):
            with self.subTest(label=label):
                state, observe = self.cli_guard("launch-" + label)
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["attempts"] = [{"launch_id": "seen", "status": "active", "result": None}]
                if label == "missing-sessions":
                    snapshot["sessions"] = []
                elif label == "unknown-cost":
                    snapshot["host_cost"]["summary"]["total"]["cost"] = None
                decision = observe(snapshot, finish=label == "finish")
                self.assertFalse(decision["may_nudge"])
                self.assertIn("seen", json.loads(state.read_text())["observed_attempts"])
                self.assertEqual(observe(self.snapshot)["action"], "wait")

    def test_cli_finished_launch_cannot_disappear_regress_or_change_result(self):
        for label in ("disappear", "active", "path", "bytes"):
            with self.subTest(label=label):
                _, observe = self.cli_guard("finished-" + label)
                result = self.root / (label + "-result.json")
                result.write_text(json.dumps({"id": "paid-trial", "trial_name": "trial", "agent_result": {"cost_usd": 2}}))
                snapshot = copy.deepcopy(self.snapshot)
                snapshot["attempts"] = [{"launch_id": "paid", "status": "finished", "result": str(result)}]
                snapshot["global_spent_usd"] = 3
                self.assertEqual(observe(snapshot)["action"], "continue")
                if label == "disappear":
                    snapshot["attempts"] = []
                    snapshot["host_cost"]["summary"]["total"]["cost"] = 3
                elif label == "active":
                    snapshot["attempts"][0].update(status="active", result=None)
                    snapshot["host_cost"]["summary"]["total"]["cost"] = 3
                elif label == "path":
                    replacement = self.root / "replacement.json"
                    replacement.write_bytes(result.read_bytes())
                    snapshot["attempts"][0]["result"] = str(replacement)
                else:
                    changed = json.loads(result.read_text())
                    changed["id"] = "replacement-trial"
                    result.write_text(json.dumps(changed))
                self.assertEqual(observe(snapshot)["action"], "wait")

    def test_cli_legacy_guard_remembers_launch_and_accepts_real_completion(self):
        state, observe = self.cli_guard("legacy")
        legacy = json.loads(state.read_text())
        del legacy["observed_attempts"]
        state.write_text(json.dumps(legacy))
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["attempts"] = [{"launch_id": "seen", "status": "active", "result": None}]
        self.assertFalse(observe(snapshot)["may_nudge"])
        result = self.root / "completed.json"
        snapshot["attempts"][0].update(status="finished", result=str(result))
        snapshot["global_spent_usd"] = 3
        self.assertEqual(observe(snapshot)["action"], "wait")
        result.write_text(json.dumps({"id": "completed-trial", "trial_name": "trial", "agent_result": {"cost_usd": 2}}))
        self.assertTrue(observe(snapshot)["may_nudge"])
        self.assertTrue(observe(snapshot)["may_nudge"])


if __name__ == "__main__":
    unittest.main()
