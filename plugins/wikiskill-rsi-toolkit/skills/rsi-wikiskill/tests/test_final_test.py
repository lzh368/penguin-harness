"""Offline protocol tests. Synthetic evidence must never become experiment data."""
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace as Args
import unittest

from test_harbor_result import module

policy = module("final_test")
state = policy.states


class FinalTestPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / "out"
        self.store = state.Store(self.out, create=True)
        with self.store.lock():
            state.init(self.store, Args(score_max="100", baseline_score="50"))
        self.initial, self.final = self.root / "initial", self.root / "final"
        for folder, version in ((self.initial, 1), (self.final, 3)):
            folder.mkdir()
            (folder / "system_config.yaml").write_text(f"version: {version}\nmodel: fixed\n")
            (folder / "AGENTS.md").write_text(state.render_text({}, block=True))
            (folder / "memory.txt").write_text("")
        self.before, self.after = self.root / "before.json", self.root / "after.json"
        self.conditions = {"binding": "synthetic-campaign", "runtime": {"model": "fixed"},
                           "benchmark": {"revision": "synthetic"}, "execution": {"timeout": 3600}}
        for path in (self.before, self.after):
            path.write_bytes(policy.canonical(self.conditions))
        self.h0 = {"score": "50", "evaluation_id": "synthetic-H0"}

    def no_action(self):
        with self.store.lock():
            state.no_action(self.store, state.load_verified(self.store), Args(reason="synthetic"))

    def record(self, iterations=1):
        return policy.make_record(self.out, self.initial, self.final, self.before, self.after,
                                  self.h0, "synthetic-campaign", iterations)

    def proposal(self):
        with self.store.lock():
            for n in range(4):
                self.store.atomic(f"raw/{n}.txt", "synthetic trace")
            self.store.atomic("proposals/proposal.json", state.dump({
                "action": "create", "skill": "synthetic", "rationale": "synthetic",
                "evidence": [f"raw/{n}.txt" for n in range(4)],
                "skill_md": "---\nname: synthetic\ndescription: synthetic\n---\nSynthetic action\n",
                "purpose_md": "Synthetic rationale"}))
            state.stage(self.store, state.load_verified(self.store), Args(proposal="proposals/proposal.json"))

    def decide(self, score):
        with self.store.lock():
            state.decide(self.store, state.load_verified(self.store),
                         Args(candidate="c0001", score=score, evaluation_id="synthetic-validation"))

    def test_no_action_reuses_one_measurement_without_fabricated_score(self):
        self.no_action()
        result = self.record()
        self.assertEqual(result["status"], "skipped_unchanged_state")
        self.assertIsNone(result["test_final"])
        self.assertIsNone(result["delta"])
        self.assertEqual(result["additional_target_trials"], 0)
        policy.verify_record(result, self.out, self.h0, "synthetic-campaign", 1)

    def test_rejected_candidate_can_skip_but_pending_cannot(self):
        self.proposal()
        with self.assertRaisesRegex(ValueError, "pending"):
            self.record()
        self.decide("50")
        self.assertEqual(self.record()["status"], "skipped_unchanged_state")

    def test_accepted_skill_survives_later_no_action_and_requires_final_test(self):
        self.proposal()
        self.decide("60")
        self.no_action()
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.record(2)

    def test_k2_second_rejection_does_not_discard_first_accepted_skill(self):
        self.proposal()
        self.decide("60")
        with self.store.lock():
            proposal = self.store.json("proposals/proposal.json")
            proposal["skill"] = "second-synthetic"
            proposal["skill_md"] = proposal["skill_md"].replace("name: synthetic", "name: second-synthetic")
            self.store.atomic("proposals/second.json", state.dump(proposal))
            state.stage(self.store, state.load_verified(self.store), Args(proposal="proposals/second.json"))
            state.decide(self.store, state.load_verified(self.store),
                         Args(candidate="c0002", score="55", evaluation_id="synthetic-second-validation"))
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.record(2)

    def test_unfinished_iterations_cannot_skip(self):
        self.no_action()
        with self.assertRaisesRegex(ValueError, "not finished"):
            self.record(2)

    def test_memory_prompt_model_or_tool_changes_refuse_skip(self):
        self.no_action()
        for filename in ("memory.txt", "system_config.yaml", "AGENTS.md", "tool.txt"):
            path = self.final / filename
            original = path.read_bytes() if path.exists() else None
            path.write_bytes((original or b"") + b"changed\n")
            with self.subTest(file=filename), self.assertRaisesRegex(ValueError, "state changed"):
                self.record()
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)

    def test_conditions_or_baseline_cannot_change_after_recording(self):
        self.no_action()
        receipt = self.record()
        changed = {**self.h0, "score": "51"}
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            policy.verify_record(receipt, self.out, changed, "synthetic-campaign", 1)
        self.conditions["execution"]["timeout"] = 1500
        self.after.write_bytes(policy.canonical(self.conditions))
        with self.assertRaisesRegex(ValueError, "conditions changed"):
            self.record()

    def test_symlink_snapshot_refused(self):
        self.no_action()
        (self.final / "linked").symlink_to(self.initial / "memory.txt")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.record()

    def test_spec_campaign_validates_skip_and_counts_only_real_trials(self):
        from test_spec_campaign import SpecCampaignTest, campaign
        fx = SpecCampaignTest()
        fx.setUp()
        self.addCleanup(fx.doCleanups)
        fx.initialize = lambda: campaign.initialize(fx.spec_path, fx.source, fx.folder, fx.home,
                                                    "default_project", "wiki_tb", policy.REUSE)
        phase_fixture = fx.phase_fixture
        fx.phase_fixture = lambda replica, phase, label, *args: None if label == "test-final" else phase_fixture(replica, phase, label, *args)
        config = fx.complete_fixture()
        rep = config["replicas"][0]
        with self.assertRaisesRegex(ValueError, "record skip evidence"):
            campaign.create_plan(fx.folder, "r1", "test", "test-final", 4, state.tree_info({})["sha256"])
        h0dir = Path(rep["plans"]) / "test-H0"
        h0 = campaign.evaluation.aggregate(h0dir / "plan.json", h0dir / "cells")
        self.conditions["binding"] = config["campaign_sha256"]
        for path in (self.before, self.after):
            path.write_bytes(policy.canonical(self.conditions))
        receipt = policy.make_record(rep["out"], self.initial, self.final, self.before, self.after,
                                     h0, config["campaign_sha256"], 1)
        record = Path(rep["out"]).parent / "final-test-decision.json"
        record.write_bytes(policy.canonical(receipt))
        with fx.fake_converter():
            report = campaign.verify_replica(fx.folder, "r1")
            self.assertIsNone(report["final_test"])
            self.assertEqual(report["target_trials"], 6)
            self.assertIsNone(campaign.summarize(fx.folder)["overall"]["delta"]["mean"])
            record.unlink()
            with self.assertRaises(OSError):
                campaign.verify_replica(fx.folder, "r1")


if __name__ == "__main__":
    unittest.main()
