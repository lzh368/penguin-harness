"""Real local state transitions plus synthetic matrices, with no paid execution."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import test_campaign as campaign_tests
from test_harbor_result import module

campaign = module("campaign")
states = module("state")


class WrapupTest(unittest.TestCase):
    setUpClass = classmethod(campaign_tests.CampaignCLITest.setUpClass.__func__)
    setUp = campaign_tests.CampaignCLITest.setUp
    cli = campaign_tests.CampaignCLITest.cli
    initialize = campaign_tests.CampaignCLITest.initialize
    plan = campaign_tests.CampaignCLITest.plan
    cells = campaign_tests.CampaignCLITest.cells

    def evolution(self):
        self.initialize()
        self.empty = states.tree_info({})["sha256"]
        self.out = self.campaign / "replicas/r1/out"
        self.store = states.Store(self.out, create=True)
        baseline = self.matrix("validation-H0", "validation", self.empty)
        states.init(self.store, SimpleNamespace(baseline_score=baseline["score"], score_max=100))
        self.matrix("train-r0001", "train", self.empty)
        evidence = []
        for i in range(4):
            relative = f"raw/task-{i}.txt"
            (self.out / relative).write_text(f"Synthetic training trace {i}")
            evidence.append(relative)
        proposal = {"action": "create", "skill": "synthetic-check", "rationale": "synthetic evidence",
                    "evidence": evidence, "skill_md": "---\nname: synthetic-check\ndescription: Synthetic checks.\n---\n\nCheck before writing.\n",
                    "purpose_md": "# Synthetic fixture\nNot experimental evidence.\n"}
        prop_path = self.out / "proposals/one.json"
        prop_path.write_text(json.dumps(proposal))
        pending = states.stage(self.store, states.load_verified(self.store), SimpleNamespace(proposal=str(prop_path)))
        self.accepted = pending["sha256"]
        validation = self.matrix("validation-r0001", "validation", self.accepted, final=True)
        states.decide(self.store, states.load_verified(self.store),
                      SimpleNamespace(candidate="c0001", score=validation["score"], evaluation_id=validation["evaluation_id"]))
        self.matrix("train-r0002", "train", self.accepted)
        states.no_action(self.store, states.load_verified(self.store), SimpleNamespace(reason="synthetic no action"))
        self.matrix("test-H0", "test", self.empty)
        self.matrix("test-final", "test", self.accepted, final=True)

    def matrix(self, label, phase, skill, final=False):
        planned = self.plan(label=label, phase=phase, skill_hash=skill, version=2)
        self.cells(planned, final=final)
        return campaign.evaluation.aggregate(Path(planned["plan"]), Path(planned["cells"]))

    def test_complete_round_history_recomputes_before_handoff(self):
        self.evolution()
        result = campaign.verify_replica(self.campaign, "r1")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["phase_count"], 6)
        self.assertEqual(result["target_trials"], 120)
        self.assertEqual(result["delta"], "3")

    def test_changed_validation_cannot_hide_behind_valid_final_tests(self):
        self.evolution()
        path = self.campaign / "replicas/r1/plans/validation-r0001/cells/000.json"
        value = json.loads(path.read_text())
        value["score"] += 1
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "decision differs"):
            campaign.verify_replica(self.campaign, "r1")

    def test_missing_training_cell_blocks_wrapup(self):
        self.evolution()
        (self.campaign / "replicas/r1/plans/train-r0002/cells/000.json").unlink()
        with self.assertRaises(ValueError):
            campaign.verify_replica(self.campaign, "r1")

    def test_wrong_final_deployment_blocks_wrapup(self):
        self.evolution()
        path = self.campaign / "replicas/r1/plans/test-final/deployment.json"
        value = json.loads(path.read_text())
        value["skill_sha256"] = self.empty
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            campaign.verify_replica(self.campaign, "r1")


if __name__ == "__main__":
    unittest.main()
