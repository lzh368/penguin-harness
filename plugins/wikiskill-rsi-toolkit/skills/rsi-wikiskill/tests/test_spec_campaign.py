"""Synthetic campaign orchestration: real Git/spec/state, no model or Docker.

The result converter is stubbed only in state replay tests; its schema-3 audit
and runtime validation have separate end-to-end fixture coverage.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_experiment_spec import example
from test_harbor_result import module, ROOT

campaign = module("spec_campaign")
specs = module("experiment_spec")
adapter = module("harbor_spec")


class SpecCampaignTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.value = example()
        self.value["adapter"] = {"id": "penguin-harbor", "version": "0.23.0"}
        self.value["config"].update(replicas=["r1"], runs_per_case=1)
        for task in self.value["tasks"]:
            folder = self.source / task["path"]
            folder.mkdir(parents=True)
            (folder / "task.toml").write_text("# Synthetic fixture " + task["case_id"] + "\n")
            task["content_sha256"] = adapter.task_digest(self.source, task["path"])
        for args in (("init", "-q"), ("add", "tasks"),
                     ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(self.source), *args], check=True, capture_output=True)
        self.value["source"]["revision"] = subprocess.check_output(
            ["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True).strip()
        self.spec_path = self.root / "input.json"
        self.spec_path.write_bytes(specs.canonical(specs.freeze(self.value)))
        self.folder = self.root / "campaign"
        self.home = self.root / "penguin-home"

    def initialize(self):
        return campaign.initialize(self.spec_path, self.source, self.folder, self.home, "default_project", "wiki_tb")

    def test_init_uses_declared_replicas_without_creating_agents_or_baselines(self):
        first = self.initialize()
        self.assertEqual(first, self.initialize())
        self.assertEqual([r["id"] for r in first["replicas"]], ["r1"])
        self.assertEqual(first["replicas"][0]["target_id"], "wiki_tb_target_r1")
        self.assertFalse(self.home.exists())
        self.assertFalse((self.folder / "replicas/r1/out").exists())
        self.assertFalse(list(self.folder.rglob("state.json")))

    def test_changed_frozen_choices_or_source_cannot_be_reused(self):
        self.initialize()
        self.value["config"]["role_models"]["target"]["thinking_level"] = "changed"
        self.spec_path.write_bytes(specs.canonical(specs.freeze(self.value)))
        with self.assertRaisesRegex(ValueError, "different initialization"):
            self.initialize()
        (self.source / self.value["tasks"][0]["path"] / "task.toml").write_text("changed")
        with self.assertRaisesRegex(ValueError, "not clean"):
            campaign.load_campaign(self.folder)

    def test_phase_plan_binds_full_catalog_and_cannot_overwrite(self):
        self.initialize()
        planned = campaign.create_plan(self.folder, "r1", "train", "train-r0001", 2, "a" * 64)
        plan = json.loads(Path(planned["plan"]).read_text())
        self.assertEqual(plan["case_ids"], self.value["phases"]["train"])
        self.assertEqual(plan["model_id"], "chosen/model")
        self.assertEqual(plan["expected_version"], 2)
        self.assertEqual(planned["deployment"]["experiment_spec_sha256"], specs.sha(self.value))
        with self.assertRaisesRegex(ValueError, "exists"):
            campaign.create_plan(self.folder, "r1", "train", "train-r0001", 3, "b" * 64)
        with self.assertRaisesRegex(ValueError, "reserved"):
            campaign.create_plan(self.folder, "r1", "train", "test-final", 3, "b" * 64)

    def test_empty_test_is_not_misrepresented_as_completed_test_campaign(self):
        self.value["tasks"].pop()
        self.value["phases"]["test"] = []
        self.spec_path.write_bytes(specs.canonical(specs.freeze(self.value)))
        with self.assertRaisesRegex(ValueError, "held-out test"):
            self.initialize()
        self.assertFalse(self.folder.exists())

    def complete_fixture(self, candidate=False, defer_decision=False):
        config = self.initialize()
        replica = config["replicas"][0]
        store = campaign.states.Store(replica["out"], create=True)
        with store.lock():
            campaign.states.init(store, SimpleNamespace(score_max="100", baseline_score="50"))
            if candidate:
                for index in range(4):
                    store.atomic(f"raw/evidence-{index}.txt", f"Synthetic trace {index}")
                proposal = {"action": "create", "skill": "synthetic-skill", "rationale": "Synthetic fixture",
                            "evidence": [f"raw/evidence-{i}.txt" for i in range(4)],
                            "skill_md": "---\nname: synthetic-skill\ndescription: Synthetic fixture\n---\nUse only in this fixture.\n",
                            "purpose_md": "Private synthetic rationale; never inject this into Target."}
                store.atomic("proposals/p.json", campaign.states.dump(proposal))
                campaign.states.stage(store, campaign.states.load_verified(store), SimpleNamespace(proposal="proposals/p.json"))
            else:
                campaign.states.no_action(store, campaign.states.load_verified(store), SimpleNamespace(reason="Synthetic no action"))
        candidate_files = store.tree("candidates/c0001/skills") if candidate else {}
        phases = [("validation", "validation-H0", 2, {}, 50), ("train", "train-r0001", 2, {}, 50)]
        if candidate:
            phases.append(("validation", "validation-r0001", 3, candidate_files, 75))
        for phase, label, version, files, score in phases:
            self.phase_fixture(replica, phase, label, version, files, score)
        if candidate and not defer_decision:
            folder = Path(replica["plans"]) / "validation-r0001"
            aggregate = campaign.evaluation.aggregate(folder / "plan.json", folder / "cells")
            with store.lock():
                campaign.states.decide(store, campaign.states.load_verified(store), SimpleNamespace(
                    candidate="c0001", score="75", evaluation_id=aggregate["evaluation_id"]))
        if not defer_decision:
            campaign.freeze_skills(self.folder, "r1")
            self.phase_fixture(replica, "test", "test-H0", 4 if candidate else 2, {}, 50)
            self.phase_fixture(replica, "test", "test-final", 5 if candidate else 2, candidate_files, 75 if candidate else 50)
        return config

    def phase_fixture(self, replica, phase, label, version, files, score):
        skill_hash = campaign.states.tree_info(files)["sha256"]
        planned = campaign.create_plan(self.folder, "r1", phase, label, version, skill_hash)
        plan = json.loads(Path(planned["plan"]).read_text())
        sources = []
        for index, case in enumerate(plan["case_ids"]):
            private = Path(replica["out"]) / "private" / label / str(index)
            private.mkdir(parents=True)
            (private / "plan.json").write_bytes(specs.canonical(plan))
            receipt = {"purpose": "evaluation", "synthetic_fixture": True,
                       "state_binding": {"expected": {"injected_block_sha256": campaign.states.sha(
                           campaign.states.render_text(files, block=True))}}}
            (private / "receipt.json").write_bytes(specs.canonical(receipt))
            cell = {"protocol_version": 1, "status": "ok", "case_id": case, "run": 1,
                    "agent_id": plan["agent_id"], "expected_version": version,
                    "provider": plan["provider"], "model_id": plan["model_id"],
                    "thinking_level": plan["thinking_level"], "score": score, "cost": None,
                    "duration_ms": 1, "session_id": f"synthetic:{label}:{case}"}
            result = {"cell": cell, "agent_execution": {"started_at": datetime.now(timezone.utc).isoformat()}}
            (private / "result.json").write_bytes(specs.canonical(result))
            (Path(planned["cells"]) / f"{index:03d}.json").write_bytes(specs.canonical(cell))
            sources.append({"case_id": case, "run": 1, "result": str(private / "result.json"),
                            "receipt": str(private / "receipt.json")})
        Path(planned["sources"]).write_bytes(specs.canonical(sources))
        return Path(planned["plan_dir"])

    def real_runtime_fixture(self, wrong_h0=False, candidate=False, early_test=False):
        """Actual schema-3 conversion of entirely synthetic traces and audits."""
        from test_runtime_evidence import RuntimeEvidenceTest, converter, trace
        from test_harbor_result import sha
        for runtime in self.value["config"]["role_models"].values():
            runtime["thinking_level"] = "xhigh"
        self.spec_path.write_bytes(specs.canonical(specs.freeze(self.value)))
        config = self.complete_fixture(candidate=candidate, defer_decision=candidate)
        frozen = json.loads((self.folder / "spec-frozen.json").read_text())
        replica = config["replicas"][0]
        store = campaign.states.Store(replica["out"])
        files_by_hash = {campaign.states.tree_info({})["sha256"]: {}}
        if candidate:
            files = store.tree("candidates/c0001/skills")
            files_by_hash[campaign.states.tree_info(files)["sha256"]] = files
        folders = list(Path(replica["plans"]).iterdir())
        last_learning_folder = folders[-1] if candidate else None
        for folder in folders:
            plan = json.loads((folder / "plan.json").read_text())
            deployment = json.loads((folder / "deployment.json").read_text())
            block = campaign.states.render_text(files_by_hash[deployment["skill_sha256"]], block=True)
            if wrong_h0 and folder.name == "validation-H0":
                block = trace.BEGIN + "\nUNDECLARED SYNTHETIC SKILL PRESENT IN H0\n" + trace.END
            sources = []
            for index, case in enumerate(plan["case_ids"]):
                task = next(item for item in frozen["spec"]["tasks"] if item["case_id"] == case)
                fx = RuntimeEvidenceTest("test_all_root_shards_are_recomputed_and_no_prompt_is_exported")
                fx.setUp()
                try:
                    fx.prepare()
                    target_path = "/opt/penguin-bench/home/default_project/agents/" + plan["agent_id"] + "/agent_state"
                    old = fx.trial
                    fx.trial = old.with_name(Path(task["path"]).name + "__" + folder.name + str(index))
                    old.rename(fx.trial)
                    fx.result_path = fx.trial / "result.json"
                    fx.traces = fx.trial / "agent/penguin/traces/wiki-target"
                    fx.shard = fx.traces / "root-000.jsonl"
                    fx.plan = plan
                    fx.request.update(case_id=case, benchmark_id=plan["benchmark_id"], test_agent_id=plan["agent_id"],
                                      expected_version=plan["expected_version"], provider=plan["provider"], model_id=plan["model_id"])
                    fx.receipt.update(schema_version=3, benchmark_revision=frozen["spec"]["source"]["revision"],
                                      thinking_level=plan["thinking_level"], task_name=Path(task["path"]).name,
                                      trial_dir=fx.trial.name, experiment_spec_sha256=frozen["spec_sha256"], purpose="evaluation")
                    for binding in fx.receipt["state_binding"].values():
                        binding.update(container_agent_state=target_path, injected_block_sha256=sha(block.encode()),
                                       state_version=plan["expected_version"])
                    fx.result.update(task_name=Path(task["path"]).name, trial_name=fx.trial.name,
                                     task_id={"path": str(self.source / task["path"])})
                    fx.result["agent_result"]["metadata"].update(provider=plan["provider"], model_id=plan["model_id"], thinking=plan["thinking_level"])
                    fx.result["agent_result"]["model_usage"] = {plan["provider"] + "/" + plan["model_id"]: {"n_input_tokens": 100, "n_output_tokens": 10}}
                    fx.result["verifier_result"]["rewards"]["reward"] = 0.75 if candidate and folder.name in ("validation-r0001", "test-final") else 0.5
                    stamp = "2000-01-01T00:00:00+00:00" if early_test and plan["phase"] == "test" else datetime.now(timezone.utc).isoformat()
                    fx.result["agent_execution"].update(started_at=stamp, finished_at=stamp)
                    fx.result["started_at"] = stamp
                    fx.payload.update(provider=plan["provider"], model_id=plan["model_id"], source="benchmark",
                                      agent_state=target_path, system_prompt="synthetic prefix\n" + block)
                    fx.put_shard(fx.shard)
                    fx.write()
                    fx.observe()
                    private = Path(replica["out"]) / "private" / folder.name / str(index)
                    for existing in private.iterdir():
                        existing.unlink()
                    shutil.copytree(fx.private, private / "receipt-evidence")
                    shutil.copytree(fx.trial, private / fx.trial.name)
                    result, receipt = private / fx.trial.name / "result.json", private / "receipt-evidence/receipt.json"
                    cell = converter.convert(result, receipt, spec_path=self.folder / "spec-frozen.json", source_root=self.source)
                    (folder / "cells" / f"{index:03d}.json").write_text(converter.cell_json(cell))
                    sources.append({"case_id": case, "run": 1, "result": str(result), "receipt": str(receipt)})
                finally:
                    fx.doCleanups()
            (folder / "sources.json").write_bytes(specs.canonical(sources))
            if folder == last_learning_folder:
                validation = Path(replica["plans"]) / "validation-r0001"
                aggregate = campaign.evaluation.aggregate(validation / "plan.json", validation / "cells")
                with store.lock():
                    campaign.states.decide(store, campaign.states.load_verified(store), SimpleNamespace(
                        candidate="c0001", score="75", evaluation_id=aggregate["evaluation_id"]))
                campaign.freeze_skills(self.folder, "r1")
                folders.append(self.phase_fixture(replica, "test", "test-H0", 4, {}, 50))
                folders.append(self.phase_fixture(replica, "test", "test-final", 5, files, 75))

    def test_real_schema3_correct_empty_skills_pass_full_wrapup(self):
        self.real_runtime_fixture()
        self.assertEqual(campaign.verify_replica(self.folder, "r1")["status"], "verified")

    def test_real_schema3_hidden_skill_in_h0_fails_even_when_receipt_and_trace_agree(self):
        self.real_runtime_fixture(wrong_h0=True)
        with self.assertRaisesRegex(ValueError, "actual expected Skill files"):
            campaign.verify_replica(self.folder, "r1")

    def test_real_schema3_accepted_candidate_and_restored_empty_h0_are_bound_to_content(self):
        self.real_runtime_fixture(candidate=True)
        result = campaign.verify_replica(self.folder, "r1")
        self.assertEqual(Decimal(result["delta"]), Decimal(25))
        self.assertEqual(result["target_trials"], 8)

    def test_real_schema3_test_before_final_skill_freeze_is_rejected(self):
        self.real_runtime_fixture(candidate=True, early_test=True)
        with self.assertRaisesRegex(ValueError, "before the final Skill freeze"):
            campaign.verify_replica(self.folder, "r1")

    def test_initial_perfect_baseline_still_requires_explicit_freeze_before_test_plan(self):
        config = self.initialize()
        store = campaign.states.Store(config["replicas"][0]["out"], create=True)
        with store.lock():
            campaign.states.init(store, SimpleNamespace(score_max="100", baseline_score="100"))
        empty = campaign.states.tree_info({})["sha256"]
        with self.assertRaises(OSError):
            campaign.create_plan(self.folder, "r1", "test", "test-H0", 2, empty)
        frozen = campaign.freeze_skills(self.folder, "r1")
        self.assertEqual(frozen, campaign.freeze_skills(self.folder, "r1"))
        campaign.create_plan(self.folder, "r1", "test", "test-H0", 2, empty)

    def test_freeze_cannot_be_reused_after_state_changes(self):
        self.complete_fixture()
        out = self.folder / "replicas/r1/out"
        state = json.loads((out / "state.json").read_text())
        state["baseline_score"] = state["best_score"] = 51
        (out / "state.json").write_text(campaign.states.dump(state))
        with self.assertRaisesRegex(ValueError, "freeze changed"):
            campaign.freeze_skills(self.folder, "r1")

    def fake_converter(self):
        real_sibling = campaign.sibling
        def sibling(name):
            if name == "harbor_result":
                return SimpleNamespace(convert=lambda result, receipt, **kwargs: json.loads(Path(result).read_text())["cell"])
            return real_sibling(name)
        return patch.object(campaign, "sibling", side_effect=sibling)

    def test_complete_no_action_round_replays_real_state_and_all_source_cells(self):
        self.complete_fixture()
        with self.fake_converter():
            result = campaign.verify_replica(self.folder, "r1")
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["target_trials"], 7)
            self.assertEqual(result["delta"], "0")
            summary = campaign.summarize(self.folder)
            self.assertEqual(summary["replica_count"], 1)
            self.assertEqual(summary["overall"]["delta"]["mean"], "0")

    def test_published_cell_score_cannot_override_underlying_result(self):
        self.complete_fixture()
        cell = self.folder / "replicas/r1/plans/test-final/cells/000.json"
        data = json.loads(cell.read_text())
        data["score"] = 100
        cell.write_bytes(specs.canonical(data))
        with self.fake_converter(), self.assertRaisesRegex(ValueError, "audited result"):
            campaign.verify_replica(self.folder, "r1")

    def test_smoke_receipt_cannot_enter_formal_summary(self):
        self.complete_fixture()
        receipt = self.folder / "replicas/r1/out/private/test-final/0/receipt.json"
        receipt.write_text('{"purpose":"smoke"}')
        with self.fake_converter(), self.assertRaisesRegex(ValueError, "smoke evidence"):
            campaign.verify_replica(self.folder, "r1")

    def test_sources_cannot_cross_replica_or_escape_private_directory(self):
        self.complete_fixture()
        manifest = self.folder / "replicas/r1/plans/test-final/sources.json"
        data = json.loads(manifest.read_text())
        data[0]["result"] = str(self.root / "outside-result.json")
        manifest.write_bytes(specs.canonical(data))
        with self.fake_converter(), self.assertRaisesRegex(ValueError, "OUT/private"):
            campaign.verify_replica(self.folder, "r1")

    def test_extra_or_incomplete_formal_phase_cannot_pass_wrapup(self):
        self.complete_fixture()
        (self.folder / "replicas/r1/plans/test-final/cells/000.json").unlink()
        with self.fake_converter(), self.assertRaises(ValueError):
            campaign.verify_replica(self.folder, "r1")

    def test_source_manifest_must_cover_every_cell_once(self):
        self.complete_fixture()
        manifest = self.folder / "replicas/r1/plans/train-r0001/sources.json"
        data = json.loads(manifest.read_text())
        data[1] = deepcopy(data[0])
        manifest.write_bytes(specs.canonical(data))
        with self.fake_converter(), self.assertRaisesRegex(ValueError, "repeated source"):
            campaign.verify_replica(self.folder, "r1")


if __name__ == "__main__":
    unittest.main()
