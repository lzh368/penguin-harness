"""External-spec integration on synthetic Git tasks/traces, no model or Docker."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_experiment_spec import example
from test_harbor_result import converter, module, sha
import test_runtime_evidence as fixtures


binding = module("harbor_spec")
schema = module("experiment_spec")
builder = module("harbor_receipt")


class HarborSpecEvidenceTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wikiskill-spec-evidence-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        value = example()
        for task in value["tasks"]:
            folder = self.source / task["path"]
            folder.mkdir(parents=True)
            (folder / "instruction.md").write_text("SYNTHETIC TASK " + task["original_id"])
            (folder / "task.toml").write_text('[metadata]\ncategory="Software"\n')
            task["content_sha256"] = binding.task_digest(self.source, task["path"])
        for args in (("init", "-q"), ("add", "."),
                     ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "-c", "commit.gpgsign=false", "commit", "-qm", "synthetic tasks")):
            subprocess.run(["git", "-C", str(self.source), *args], check=True, capture_output=True)
        value["source"]["revision"] = subprocess.check_output(
            ["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True).strip()
        value["adapter"] = {"id": "penguin-harbor", "version": "0.23.0"}
        for runtime in value["config"]["role_models"].values():
            runtime.update(provider="deepseek", model_id="deepseek-flash", thinking_level="medium")
        value["config"].update(runs_per_case=1, replicas=["r1"])
        self.frozen = schema.freeze(value)
        self.spec_path = self.root / "spec.json"
        schema.write_frozen(self.spec_path, self.frozen)

    def cell_fixture(self, index=0, phase="train", purpose="evaluation"):
        fixture = fixtures.RuntimeEvidenceTest(methodName="runTest")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.prepare()
        task = self.frozen["spec"]["tasks"][index]
        new_trial = fixture.trial.with_name(f"task-{index}__synthetic")
        fixture.trial.rename(new_trial)
        fixture.trial = new_trial
        fixture.result_path = new_trial / "result.json"
        fixture.traces = new_trial / "agent/penguin/traces/wiki-target"
        fixture.shard = fixture.traces / "root-000.jsonl"
        fixture.payload["session_id"] = f"synthetic-root-{index}"
        fixture.payload["source"] = "benchmark"
        fixture.put_shard(fixture.shard, extra=[
            {"type": "model_msg", "payload": {"type": "text", "role": "user", "text": f"SYNTHETIC TASK {index}"}},
            {"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": "SYNTHETIC RESULT"}}])
        fixture.request.update(case_id=task["case_id"], benchmark_id=binding.benchmark_id(self.frozen))
        cases = list(self.frozen["spec"]["phases"][phase]) if purpose != "smoke" else [task["case_id"]]
        fixture.plan.update(phase=phase, benchmark_id=fixture.request["benchmark_id"], case_ids=cases)
        fixture.result.update(task_name=f"task-{index}", trial_name=new_trial.name,
                              task_id={"path": str(self.source / task["path"])})
        fixture.result["agent_result"]["metadata"]["session_id"] = fixture.payload["session_id"]
        fixture.receipt.update(schema_version=3, benchmark_revision=self.frozen["spec"]["source"]["revision"],
                               experiment_spec_sha256=self.frozen["spec_sha256"], purpose=purpose,
                               task_name=f"task-{index}", trial_dir=new_trial.name)
        fixture.write()
        fixture.observe()
        return fixture

    def convert(self, fixture):
        fixture.write()
        return converter.convert(fixture.result_path, fixture.receipt_path,
                                 spec_path=self.spec_path, source_root=self.source)

    def test_external_receipt_preserves_actual_scoring_and_trace_bytes(self):
        fixture = self.cell_fixture()
        before = fixture.shard.read_bytes()
        cell = self.convert(fixture)
        self.assertEqual(cell["score"], Decimal("75.56"))
        self.assertEqual(cell["case_id"], "case-0")
        self.assertEqual(before, fixture.shard.read_bytes())

    def test_external_receipt_cannot_fall_back_to_legacy_checks(self):
        fixture = self.cell_fixture()
        with self.assertRaisesRegex(ValueError, "requires --spec"):
            converter.convert(fixture.result_path, fixture.receipt_path, allow_legacy=True)
        fixture.receipt["schema_version"] = 2
        with self.assertRaises(ValueError):
            self.convert(fixture)

    def test_held_out_task_cannot_be_relabeled_train(self):
        fixture = self.cell_fixture(index=5, phase="test")
        self.convert(fixture)
        fixture.plan.update(phase="train")
        with self.assertRaisesRegex(ValueError, "exact frozen phase"):
            self.convert(fixture)

    def test_formal_partial_matrix_rejected_but_smoke_is_explicit(self):
        fixture = self.cell_fixture(purpose="smoke")
        self.convert(fixture)
        fixture.receipt["purpose"] = "evaluation"
        with self.assertRaisesRegex(ValueError, "exact frozen phase"):
            self.convert(fixture)

    def test_wrong_source_and_wrong_experiment_are_rejected(self):
        fixture = self.cell_fixture()
        fixture.receipt["experiment_spec_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "spec binding"):
            self.convert(fixture)
        fixture.receipt["experiment_spec_sha256"] = self.frozen["spec_sha256"]
        fixture.result["task_id"]["path"] = str(self.source / "tasks/task-5")
        with self.assertRaisesRegex(ValueError, "task path"):
            self.convert(fixture)

    def test_changed_task_bytes_are_rejected(self):
        fixture = self.cell_fixture()
        (self.source / "tasks/task-0/instruction.md").write_text("changed")
        with self.assertRaisesRegex(ValueError, "not clean"):
            self.convert(fixture)

    def test_missing_trace_or_rehashed_wrong_injection_is_not_accepted(self):
        fixture = self.cell_fixture()
        fixture.payload["system_prompt"] = fixture.block.replace("synthetic skill", "wrong skill")
        fixture.put_shard(fixture.shard)
        fixture.observe()
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            self.convert(fixture)
        fixture.shard.unlink()
        with self.assertRaises(ValueError):
            self.convert(fixture)

    def test_native_builder_creates_schema_three_only_after_audits(self):
        fixture = self.cell_fixture()
        expected = fixture.receipt["state_binding"]["expected"]
        expected_path = fixture.root / "expected.json"
        observed_path = fixture.private / "observed.json"
        request_path = fixture.private / "request.json"
        expected_path.write_text(json.dumps(expected))
        observed_path.write_text(json.dumps({key: expected[key] for key in
            ("deployed_state_sha256", "archive_sha256", "state_version")}))
        request_path.write_text(json.dumps(fixture.request))
        fixture.receipt_path.unlink()
        (fixture.private / "runtime-observation.json").unlink()
        report = builder.build(fixture.result_path, fixture.private, request_path,
                               expected_path, observed_path, self.spec_path, self.source)
        self.assertEqual(report["schema_version"], 3)
        actual = json.loads(fixture.receipt_path.read_bytes())
        self.assertEqual(actual["experiment_spec_sha256"], self.frozen["spec_sha256"])
        self.assertEqual(converter.convert(fixture.result_path, fixture.receipt_path,
                         spec_path=self.spec_path, source_root=self.source)["status"], "ok")

    def test_complete_external_train_batch_exports_only_train_traces(self):
        fixtures_ = [self.cell_fixture(i) for i in range(4)]
        cells = self.root / "cells"
        cells.mkdir()
        sources = []
        for i, fixture in enumerate(fixtures_):
            (cells / f"{i}.json").write_text(converter.cell_json(self.convert(fixture)))
            sources.append({"case_id": f"case-{i}", "run": 1,
                            "result": str(fixture.result_path), "receipt": str(fixture.receipt_path)})
        sources_path = self.root / "sources.json"
        sources_path.write_text(json.dumps(sources))
        out = self.root / "views"
        report = module("train_views").build(fixtures_[0].private / "plan.json", cells,
                                             sources_path, out, 7, self.spec_path, self.source)
        self.assertEqual(report["experiment_spec_sha256"], self.frozen["spec_sha256"])
        self.assertEqual(len(report["entries"]), 4)
        for entry in report["entries"]:
            self.assertIn("SYNTHETIC TASK", (out / entry["view"]).read_text())
        self.assertFalse((out / "receipt.json").exists())

    def test_smoke_batch_cannot_become_training(self):
        fixture = self.cell_fixture(purpose="smoke")
        cells = self.root / "cells"
        cells.mkdir()
        (cells / "0.json").write_text(converter.cell_json(self.convert(fixture)))
        sources = self.root / "sources.json"
        sources.write_text(json.dumps([{"case_id": "case-0", "run": 1,
            "result": str(fixture.result_path), "receipt": str(fixture.receipt_path)}]))
        with self.assertRaises(ValueError):
            module("train_views").build(fixture.private / "plan.json", cells, sources,
                                         self.root / "views", 0, self.spec_path, self.source)
        self.assertFalse((self.root / "views").exists())
