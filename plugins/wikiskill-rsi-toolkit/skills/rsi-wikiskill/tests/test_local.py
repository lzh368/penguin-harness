"""Offline regression checks; no Penguin, credentials, network, or model calls."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("aggregate", ROOT / "scripts/aggregate.py")
agg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agg)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wikiskill-test-")
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.call("init", "--baseline-score", "60")
        self.evidence = []
        for i in range(4):
            path = f"raw/train-{i}.md"
            (self.out / path).write_text(f"Synthetic distinct task {i}; inspect then verify.\n")
            self.evidence.append(path)
        self.proposal = {"action": "create", "skill": "verify-edits", "rationale": "Reusable verification",
                         "evidence": self.evidence,
                         "skill_md": "---\nname: verify-edits\ndescription: Verify edited artifacts.\n---\nReopen and check the result.\n",
                         "purpose_md": "PRIVATE_RATIONALE: derived from training patterns.\n"}

    def call(self, command, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/state.py"), command,
                                 "--out", str(self.out), *args], text=True, capture_output=True)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result

    def stage(self, proposal=None, ok=True):
        (self.out / "proposals/current.json").write_text(json.dumps(proposal or self.proposal))
        return self.call("stage", "--proposal", "proposals/current.json", ok=ok)

    def test_reject_retains_wiki_and_proposal_then_accept(self):
        (self.out / "wiki/patterns/lesson.md").write_text("Keep this lesson even after rejection.\n")
        self.stage()
        self.call("decide", "--candidate", "c0001", "--score", "60", "--evaluation-id", "val-1")
        self.assertFalse(list((self.out / "accepted/skills").iterdir()))
        self.assertIn("Keep", (self.out / "wiki/patterns/lesson.md").read_text())
        impact = (self.out / "wiki/skill-impact.md").read_text()
        self.assertIn("REJECT", impact)
        self.assertIn("Reopen", impact)
        revised = copy.deepcopy(self.proposal)
        revised["skill_md"] += "Check read-only tasks without writing a new file.\n"
        self.stage(revised)
        self.call("decide", "--candidate", "c0002", "--score", "70", "--evaluation-id", "val-2")
        self.assertEqual((self.out / "accepted/skills/verify-edits/SKILL.md").read_text(), revised["skill_md"])
        self.call("verify")

    def test_decide_is_idempotent_and_conflicting_replay_fails(self):
        self.stage()
        args = ("--candidate", "c0001", "--score", "65", "--evaluation-id", "val-1")
        self.call("decide", *args)
        before = (self.out / "wiki/skill-impact.md").read_bytes()
        result = self.call("decide", *args)
        self.assertEqual(json.loads(result.stdout)["status"], "already_decided")
        self.assertEqual(before, (self.out / "wiki/skill-impact.md").read_bytes())
        self.call("decide", "--candidate", "c0001", "--score", "66", "--evaluation-id", "val-1", ok=False)

    def test_render_excludes_private_purpose(self):
        self.stage()
        rendered = self.call("render", "--source", "c0001").stdout
        self.assertIn("Reopen", rendered)
        self.assertNotIn("PRIVATE_RATIONALE", rendered)

    def test_pending_prevents_second_stage_and_no_action(self):
        self.stage()
        self.stage(ok=False)
        self.call("no-action", "--reason", "nothing", ok=False)

    def test_evidence_tampering_is_detected(self):
        self.stage()
        (self.out / self.evidence[0]).write_text("changed")
        self.call("verify", ok=False)

    def test_purpose_only_and_ambiguous_patch_are_rejected(self):
        self.proposal["skill_md"] += "Reopen and check the result.\n"
        self.stage()
        self.call("decide", "--candidate", "c0001", "--score", "70", "--evaluation-id", "val-1")
        proposal = {"action": "patch", "skill": "verify-edits", "rationale": "more evidence",
                    "evidence": self.evidence,
                    "edits": [{"file": "PURPOSE.md", "op": "append", "text": "more rationale"}]}
        self.stage(proposal, ok=False)
        proposal["edits"] = [{"file": "SKILL.md", "op": "replace", "old": "Reopen", "text": "Read"}]
        self.stage(proposal, ok=False)

    def test_invalid_score_is_not_a_rejection(self):
        self.stage()
        for value in ("NaN", "inf", "-1", "101"):
            self.call("decide", "--candidate", "c0001", "--score", value, "--evaluation-id", "bad", ok=False)
        state = json.loads(self.call("verify").stdout)
        self.assertEqual(state["pending"], "c0001")
        self.assertEqual(state["best_score"], "60")

    def test_no_action_preserves_threshold(self):
        self.call("no-action", "--reason", "insufficient evidence")
        state = json.loads(self.call("verify").stdout)
        self.assertEqual((state["round"], state["best_score"], state["pending"]), (1, "60", None))

    def test_recover_after_process_dies_during_commit(self):
        self.stage()
        code = '''
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("state", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original = module.Store.atomic
def crash_before_state(self, value, text):
    if value == "state.json":
        os._exit(23)
    return original(self, value, text)
module.Store.atomic = crash_before_state
sys.argv = [sys.argv[1], "decide", "--out", sys.argv[2],
            "--candidate", "c0001", "--score", "70", "--evaluation-id", "val-crash"]
module.main()
'''
        result = subprocess.run([sys.executable, "-B", "-c", code,
                                 str(ROOT / "scripts/state.py"), str(self.out)], capture_output=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.call("verify", ok=False)
        self.call("recover")
        status = json.loads(self.call("verify").stdout)
        self.assertEqual((status["best_score"], status["pending"]), ("70", None))
        impact = (self.out / "wiki/skill-impact.md").read_bytes()
        self.call("recover")
        self.assertEqual(impact, (self.out / "wiki/skill-impact.md").read_bytes())

    def test_path_escape_and_symlink_are_rejected(self):
        proposal = copy.deepcopy(self.proposal)
        proposal["evidence"][0] = "../outside.md"
        self.stage(proposal, ok=False)
        (self.out / self.evidence[0]).unlink()
        (self.out / self.evidence[0]).symlink_to(self.out / self.evidence[1])
        self.stage(ok=False)

    def test_duplicate_proposal_key_is_rejected(self):
        text = json.dumps(self.proposal).replace('"action": "create"', '"action": "no_action", "action": "create"')
        (self.out / "proposals/current.json").write_text(text)
        self.call("stage", "--proposal", "proposals/current.json", ok=False)

    def test_decimal_precision_is_preserved_in_strict_gate(self):
        self.stage()
        value = "60.0000000000000001"
        result = self.call("decide", "--candidate", "c0001", "--score", value, "--evaluation-id", "precise")
        self.assertTrue(json.loads(result.stdout)["accepted"])
        self.assertEqual(json.loads(self.call("verify").stdout)["best_score"], value)

    def test_old_evaluation_cannot_decide_new_pending_candidate(self):
        self.stage()
        self.call("decide", "--candidate", "c0001", "--score", "60", "--evaluation-id", "val-1")
        revised = copy.deepcopy(self.proposal)
        revised["skill_md"] += "Verify before and after.\n"
        self.stage(revised)
        self.call("decide", "--candidate", "c0002", "--score", "60", "--evaluation-id", "val-1", ok=False)
        self.call("decide", "--score", "60", "--evaluation-id", "val-1", ok=False)
        self.assertEqual(json.loads(self.call("verify").stdout)["pending"], "c0002")


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wikiskill-matrix-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cells = self.root / "cells"
        self.cells.mkdir()
        self.plan = {"phase": "validation", "benchmark_id": "val", "case_ids": ["a", "b"],
                     "runs_per_case": 1, "agent_id": "target", "expected_version": 2,
                     "provider": "fixture", "model_id": "fixture", "thinking_level": "medium"}
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_text(json.dumps(self.plan))
        self.rows = []
        for case, score in (("a", 80), ("b", 60)):
            self.rows.append({"protocol_version": 1, "status": "ok", "case_id": case,
                              "run": 1, "agent_id": "target", "expected_version": 2,
                              "provider": "fixture", "model_id": "fixture", "thinking_level": "medium",
                              "score": score, "cost": None, "duration_ms": 12, "session_id": f"session-{case}"})
        self.write()

    def write(self):
        for i, row in enumerate(self.rows):
            (self.cells / f"{i}.json").write_text(json.dumps(row))

    def run_aggregate(self):
        return agg.aggregate(self.plan_path, self.cells)

    def test_complete_matrix_has_mean_and_reproducible_binding(self):
        summary = self.run_aggregate()
        self.assertEqual(summary["score"], "70")
        self.assertEqual(summary, self.run_aggregate())
        self.rows[0]["score"] = 82
        self.write()
        self.assertNotEqual(summary["evaluation_id"], self.run_aggregate()["evaluation_id"])

    def test_missing_or_failed_cells_block_aggregation(self):
        (self.cells / "1.json").unlink()
        with self.assertRaises(agg.InvalidEvaluation):
            self.run_aggregate()
        self.rows[1]["status"] = "failed"
        self.write()
        with self.assertRaises(agg.InvalidEvaluation):
            self.run_aggregate()

    def test_duplicate_cell_or_reused_session_blocks_aggregation(self):
        for key, value in (("case_id", "a"), ("session_id", "session-a")):
            original = self.rows[1][key]
            self.rows[1][key] = value
            self.write()
            with self.assertRaises(agg.InvalidEvaluation):
                self.run_aggregate()
            self.rows[1][key] = original

    def test_model_drift_and_invalid_scores_block_aggregation(self):
        original = copy.deepcopy(self.rows[1])
        for key, value in (("provider", "other"), ("thinking_level", "low"), ("expected_version", 3),
                           ("score", -1), ("score", 101), ("score", True), ("score", 0.001), ("score", "80")):
            self.rows[1] = {**original, key: value}
            self.write()
            with self.assertRaises(agg.InvalidEvaluation):
                self.run_aggregate()

    def test_duplicate_json_keys_are_rejected(self):
        path = self.cells / "0.json"
        path.write_text(path.read_text().replace('"score": 80', '"score": 80, "score": 90'))
        with self.assertRaises(agg.InvalidEvaluation):
            self.run_aggregate()


if __name__ == "__main__":
    unittest.main()
