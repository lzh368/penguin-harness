"""Synthetic statistics only; no experiment results or model calls."""
import copy
from decimal import Decimal
import hashlib
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("repeated_tests", Path(__file__).parents[1] / "scripts/test_repeats.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def hashed(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def group(gid="a", has_skill=True):
    result = {"id": gid, "learning_run_id": "learn-" + gid, "case_ids": ["one", "two"],
              "conditions_sha256": hashed("conditions"), "h0_skill_sha256": hashed("empty"),
              "final_skill_sha256": hashed(gid) if has_skill else None,
              "unchanged_state_receipt_sha256": None if has_skill else hashed("skip-" + gid), "evaluations": []}
    # Per-repeat means are 20,40,60 and 30,50,70. Within-repeat task SD is
    # different: a deliberate guard against reporting task spread as repeat SD.
    for repeat, values in enumerate(((0, 40), (20, 60), (40, 80)), 1):
        for condition in ("h0", "final") if has_skill else ("h0",):
            eid = f"{gid}-{repeat}-{condition}"
            result["evaluations"].append({"repeat_id": f"test-r{repeat}", "condition": condition,
                "evaluation_id": eid, "conditions_sha256": result["conditions_sha256"],
                "skill_sha256": result[condition + "_skill_sha256"],
                "cells": [{"case_id": case, "session_id": f"session-{eid}-{case}", "status": "ok",
                           "score": str(value + (10 if condition == "final" else 0)),
                           "evidence_sha256": hashed(f"{eid}-{case}")}
                          for case, value in zip(result["case_ids"], values)]})
    return result


def fixture():
    return {"schema_version": 1, "mode": "fixed_skill_test_repeats", "std_ddof": 1,
            "repeat_ids": ["test-r1", "test-r2", "test-r3"], "groups": [group()]}


class RepeatedTestStatistics(unittest.TestCase):
    def test_means_and_sample_sd_across_three_whole_test_runs(self):
        result = tool.summarize(fixture())["groups"][0]
        self.assertEqual(result["baseline"]["values"], ["20", "40", "60"])
        self.assertEqual(Decimal(result["baseline"]["mean"]), 40)
        self.assertEqual(Decimal(result["baseline"]["sample_std"]), 20)
        self.assertEqual(Decimal(result["final"]["mean"]), 50)
        self.assertEqual(Decimal(result["paired_delta"]["mean"]), 10)
        self.assertEqual(Decimal(result["paired_delta"]["sample_std"]), 0)
        self.assertEqual(result["baseline"]["error_bar"]["lower"], "20")
        self.assertEqual(result["baseline"]["n"], 3)

    def test_unchanged_group_has_no_synthetic_final_or_gain(self):
        data = fixture(); data["groups"].append(group("b", False))
        result = tool.summarize(data)
        self.assertIsNone(result["groups"][1]["final"])
        self.assertIsNone(result["groups"][1]["paired_delta"])
        self.assertEqual(result["paired_groups"]["group_ids"], ["a"])
        self.assertEqual(result["paired_groups"]["total_group_count"], 2)
        data["groups"] = [group("b", False)]
        self.assertIsNone(tool.summarize(data)["paired_groups"]["final"])

    def test_group_sd_not_averaged_for_overall_sd(self):
        data = fixture(); second = group("b")
        # Reverse scores across repeats while retaining fresh session/evidence IDs.
        for row, other in zip(second["evaluations"], reversed(data["groups"][0]["evaluations"])):
            values = [Decimal(c["score"]) for c in other["cells"]]
            correction = -10 if row["condition"] == "h0" else 10
            for cell, value in zip(row["cells"], values):cell["score"] = str(value + correction)
        data["groups"].append(second)
        result = tool.summarize(data)
        self.assertEqual(Decimal(result["all_groups_baseline"]["sample_std"]), 0)
        self.assertEqual(Decimal(result["groups"][1]["baseline"]["sample_std"]), 20)

    def test_missing_failed_or_duplicate_cells_rejected(self):
        for mutation in (lambda d:d["groups"][0]["evaluations"].pop(),
                         lambda d:d["groups"][0]["evaluations"][0]["cells"].pop(),
                         lambda d:d["groups"][0]["evaluations"][0]["cells"][0].update(status="error"),
                         lambda d:d["groups"][0]["evaluations"].__setitem__(1, copy.deepcopy(d["groups"][0]["evaluations"][0]))):
            data = fixture(); mutation(data)
            with self.assertRaises(ValueError):tool.summarize(data)

    def test_reused_baselines_or_sessions_or_evidence_rejected(self):
        for key in ("session_id", "evidence_sha256"):
            data = fixture(); rows = data["groups"][0]["evaluations"]
            rows[2]["cells"][0][key] = rows[0]["cells"][0][key]
            with self.assertRaisesRegex(ValueError, "reused"):tool.summarize(data)
        data = fixture(); rows = data["groups"][0]["evaluations"]
        rows[2]["evaluation_id"] = rows[0]["evaluation_id"]
        with self.assertRaisesRegex(ValueError, "reused"):tool.summarize(data)

    def test_skill_and_conditions_drift_rejected(self):
        for key in ("skill_sha256", "conditions_sha256"):
            data = fixture(); data["groups"][0]["evaluations"][1][key] = hashed("drift")
            with self.assertRaisesRegex(ValueError, "drift"):tool.summarize(data)

    def test_invalid_scores_and_wrong_sd_protocol_rejected(self):
        for value in ("NaN", "Infinity", "-1", "101", True):
            data = fixture(); data["groups"][0]["evaluations"][0]["cells"][0]["score"] = value
            with self.assertRaises(ValueError):tool.summarize(data)
        for ddof in (0, True):
            data = fixture(); data["std_ddof"] = ddof
            with self.assertRaises(ValueError):tool.summarize(data)

    def test_no_skill_requires_skip_evidence_and_only_h0(self):
        data = fixture(); data["groups"] = [group("b", False)]
        data["groups"][0]["unchanged_state_receipt_sha256"] = None
        with self.assertRaises(ValueError):tool.summarize(data)
        data = fixture(); data["groups"][0]["final_skill_sha256"] = data["groups"][0]["h0_skill_sha256"]
        with self.assertRaisesRegex(ValueError, "unchanged Skill"):tool.summarize(data)


if __name__ == "__main__":
    unittest.main()
