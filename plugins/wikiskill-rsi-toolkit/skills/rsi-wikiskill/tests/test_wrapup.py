"""Real local state transitions plus synthetic matrices, with no paid execution."""
from decimal import Decimal
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

    def start(self, versions=None, baseline_score=None):
        self.initialize()
        self.versions = {"validation-H0": 1, "train-r0001": 1,
                         "validation-r0001": 2, "train-r0002": 2,
                         "validation-r0002": 3, "test-H0": 4, "test-final": 5}
        self.versions.update(versions or {})
        self.empty = states.tree_info({})["sha256"]
        self.accepted = self.empty
        self.out = self.campaign / "replicas/r1/out"
        self.store = states.Store(self.out, create=True)
        baseline = self.matrix("validation-H0", "validation", self.empty, score=baseline_score)
        states.init(self.store, SimpleNamespace(baseline_score=baseline["score"], score_max=100))

    def proposal(self, round_number):
        evidence = []
        for i in range(4):
            relative = f"raw/round-{round_number}-task-{i}.txt"
            (self.out / relative).write_text(f"Synthetic training trace {i}, round {round_number}")
            evidence.append(relative)
        proposal = {"skill": "synthetic-check", "rationale": "synthetic evidence", "evidence": evidence}
        if round_number == 1:
            proposal.update(action="create",
                            skill_md="---\nname: synthetic-check\ndescription: Synthetic checks.\n---\n\nCheck before writing.\n",
                            purpose_md="# Synthetic fixture\nNot experimental evidence.\n")
        else:
            proposal.update(action="patch", edits=[
                {"file": "SKILL.md", "op": "append", "text": "\nReopen the edited artifact.\n"},
                {"file": "PURPOSE.md", "op": "append", "text": "\nSecond round synthetic traces.\n"}])
        prop_path = self.out / f"proposals/round-{round_number}.json"
        prop_path.write_text(json.dumps(proposal))
        return states.stage(self.store, states.load_verified(self.store), SimpleNamespace(proposal=str(prop_path)))

    def decide(self, round_number, pending, final=True, score=None):
        validation = self.matrix(f"validation-r{round_number:04d}", "validation", pending["sha256"],
                                 final=final, score=score)
        decision = states.decide(self.store, states.load_verified(self.store),
                                SimpleNamespace(candidate=pending["candidate_id"], score=validation["score"],
                                                evaluation_id=validation["evaluation_id"]))
        self.accepted = states.load_verified(self.store)["accepted"]["sha256"]
        return decision

    def final_tests(self, order=("test-H0", "test-final")):
        for label in order:
            self.matrix(label, "test", self.empty if label == "test-H0" else self.accepted,
                        final=label == "test-final")

    def evolution(self, second_round="no_action", versions=None, test_order=("test-H0", "test-final")):
        self.start(versions)
        self.matrix("train-r0001", "train", self.empty)
        self.first = self.decide(1, self.proposal(1))
        self.accepted_before_second = self.store.tree("accepted/skills")
        self.matrix("train-r0002", "train", self.accepted)
        self.pattern = self.out / "wiki/patterns/round-2.md"
        self.pattern.write_text("Synthetic second round training insight; retained after rejection.\n")
        if second_round == "no_action":
            states.no_action(self.store, states.load_verified(self.store), SimpleNamespace(reason="synthetic no action"))
        else:
            self.second = self.decide(2, self.proposal(2), final=second_round == "tie")
        self.final_tests(test_order)

    def matrix(self, label, phase, skill, final=False, score=None):
        planned = self.plan(label=label, phase=phase, skill_hash=skill, version=self.versions[label])
        self.cells(planned, final=final)
        if score is not None:
            for path in Path(planned["cells"]).iterdir():
                value = json.loads(path.read_text())
                value["score"] = score
                path.write_text(json.dumps(value))
        return campaign.evaluation.aggregate(Path(planned["plan"]), Path(planned["cells"]))

    def test_complete_round_history_recomputes_before_handoff(self):
        self.evolution()
        result = campaign.verify_replica(self.campaign, "r1")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["phase_count"], 6)
        self.assertEqual(result["target_trials"], 120)
        self.assertEqual(result["delta"], "3")
        self.assertEqual(self.versions["validation-H0"], self.versions["train-r0001"])
        self.assertEqual(self.versions["validation-r0001"], self.versions["train-r0002"])

    def assert_rejected_second_round(self, kind):
        self.evolution(second_round=kind)
        self.assertTrue(self.first["accepted"])
        self.assertFalse(self.second["accepted"])
        self.assertEqual(self.store.tree("accepted/skills"), self.accepted_before_second)
        self.assertNotEqual(self.store.tree("candidates/c0002/skills"), self.accepted_before_second)
        self.assertEqual(self.pattern.read_text(), "Synthetic second round training insight; retained after rejection.\n")
        self.assertIn("c0002 — REJECT", (self.out / "wiki/skill-impact.md").read_text())
        result = campaign.verify_replica(self.campaign, "r1")
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["phase_count"], 7)
        self.assertEqual(result["target_trials"], 126)

    def test_equal_validation_rejects_candidate_and_retains_accepted_and_wiki(self):
        self.assert_rejected_second_round("tie")
        self.assertEqual(self.second["score"], Decimal(self.second["previous_best"]))

    def test_lower_validation_rejects_candidate_and_retains_accepted_and_wiki(self):
        self.assert_rejected_second_round("decrease")
        self.assertLess(self.second["score"], Decimal(self.second["previous_best"]))

    def test_same_version_cannot_identify_different_skill_sets(self):
        self.evolution(versions={label: 1 for label in (
            "validation-H0", "train-r0001", "validation-r0001", "train-r0002", "test-H0", "test-final")})
        with self.assertRaisesRegex(ValueError, "version identifies different skill sets"):
            campaign.verify_replica(self.campaign, "r1")

    def test_evolution_versions_cannot_go_backwards_for_unchanged_skills(self):
        self.evolution(versions={"validation-r0001": 3, "train-r0002": 2})
        with self.assertRaisesRegex(ValueError, "version went backwards"):
            campaign.verify_replica(self.campaign, "r1")

    def test_rejected_candidate_requires_new_version_to_restore_accepted(self):
        self.evolution(second_round="tie", versions={"test-final": 2})
        with self.assertRaisesRegex(ValueError, "version went backwards"):
            campaign.verify_replica(self.campaign, "r1")

    def test_test_deployments_cannot_reuse_one_version_for_different_skills(self):
        self.evolution(versions={"test-H0": 4, "test-final": 4})
        with self.assertRaisesRegex(ValueError, "version identifies different skill sets"):
            campaign.verify_replica(self.campaign, "r1")

    def test_final_test_can_precede_h0_and_reuse_unchanged_final_deployment(self):
        self.evolution(versions={"test-final": 2, "test-H0": 3}, test_order=("test-final", "test-H0"))
        self.assertEqual(campaign.verify_replica(self.campaign, "r1")["status"], "verified")

    def test_h0_test_cannot_reuse_a_version_from_before_evolution(self):
        self.evolution(versions={"test-H0": 1})
        with self.assertRaisesRegex(ValueError, "version went backwards"):
            campaign.verify_replica(self.campaign, "r1")

    def test_perfect_baseline_stops_without_training_rounds(self):
        self.start(versions={"test-H0": 1, "test-final": 1}, baseline_score=100)
        self.final_tests()
        result = campaign.verify_replica(self.campaign, "r1")
        self.assertEqual(result["phase_count"], 3)
        self.assertEqual(result["target_trials"], 66)

    def test_perfect_candidate_stops_before_iteration_budget(self):
        self.start(versions={"test-final": 2, "test-H0": 3})
        self.matrix("train-r0001", "train", self.empty)
        self.decide(1, self.proposal(1), score=100)
        self.final_tests(order=("test-final", "test-H0"))
        result = campaign.verify_replica(self.campaign, "r1")
        self.assertEqual(result["phase_count"], 5)
        self.assertEqual(result["target_trials"], 96)

    def test_pending_candidate_blocks_wrapup(self):
        self.start()
        self.matrix("train-r0001", "train", self.empty)
        self.proposal(1)
        with self.assertRaisesRegex(ValueError, "pending candidate"):
            campaign.verify_replica(self.campaign, "r1")

    def test_previous_best_must_match_recomputed_history(self):
        self.evolution(second_round="tie")
        path = self.out / "state.json"
        value = json.loads(path.read_text())
        value["decisions"][1]["previous_best"] = "99"
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "previous_best disagrees with history"):
            states.load_verified(self.store)
        with self.assertRaisesRegex(ValueError, "previous_best disagrees with history"):
            campaign.verify_replica(self.campaign, "r1")

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
