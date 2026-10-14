"""Synthetic fixtures based on Harbor 0.23.0 and the pinned Penguin adapter.

No live model/container runs or benchmark answers. Audit reports are explicitly
fixture attestations, not evidence of a real execution or of isolation.

Runtime-selection scenarios:
- The plan, receipt, adapter metadata and usage all name an alternative runtime:
  conversion preserves its identity and the cell can be aggregated.
- Only the receipt or only the adapter changes runtime: conversion refuses the
  inconsistent evidence rather than treating the changed model as interchangeable.
"""
import copy
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


converter = module("harbor_result")
aggregator = module("aggregate")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class HarborResultTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wikiskill-harbor-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.trial = self.root / "tg003-train-001__fixtureA"
        self.trial.mkdir()
        self.result_path = self.trial / "result.json"
        self.private = self.root / "private"
        (self.private / "audit").mkdir(parents=True)
        self.receipt_path = self.private / "receipt.json"
        self.request = {"protocol_version": 1, "case_id": "CASE-001-tg003-train-001", "run": 1,
                        "expected_version": 2, "test_agent_id": "wiki-target",
                        "benchmark_id": "penguinharness-benchmark-gdpevo-train",
                        "provider": "deepseek", "model_id": "deepseek-flash"}
        self.plan = {"phase": "train", "benchmark_id": self.request["benchmark_id"],
                     "case_ids": [self.request["case_id"]], "runs_per_case": 1,
                     "agent_id": "wiki-target", "expected_version": 2, "provider": "deepseek",
                     "model_id": "deepseek-flash", "thinking_level": "medium"}
        binding = {"deployed_state_sha256": "a" * 64, "archive_sha256": "b" * 64,
                   "injected_block_sha256": "c" * 64,
                   "container_agent_state": "/opt/penguin-bench/home/default_project/agents/wiki-target/agent_state",
                   "container_workspace": "/app", "state_version": 2}
        self.receipt = {"schema_version": 1, "benchmark_revision": converter.BENCHMARK_REVISION,
                        "request": self.request, "thinking_level": "medium", "task_name": "tg003-train-001",
                        "trial_dir": self.trial.name, "trial_id": "fa42e20c-9721-4a51-8ab5-15beecb787bc",
                        "result_sha256": "0" * 64, "phase_plan": {"artifact": "plan.json", "sha256": "0" * 64},
                        "state_binding": {"expected": binding, "observed": copy.deepcopy(binding)}, "audit": []}
        for check in converter.AUDIT_CHECKS:
            data = json.dumps({"synthetic_fixture": True, "check": check, "verdict": "pass",
                               "detail": "redacted audit fixture"}).encode()
            (self.private / "audit" / f"{check}.json").write_bytes(data)
            self.receipt["audit"].append({"check": check, "verdict": "pass", "artifact": f"audit/{check}.json",
                                          "artifact_sha256": sha(data)})
        self.result = {"id": self.receipt["trial_id"], "task_name": "tg003-train-001", "trial_name": self.trial.name,
                       "task_id": {"path": "/checkout/benchmarks/gdpevo/tasks/tg003-train-001"},
                       "exception_info": None, "step_results": None,
                       "agent_result": {"cost_usd": 0.018507,
                                        "model_usage": {"deepseek/deepseek-flash": {"n_input_tokens": 100, "n_output_tokens": 10}},
                                        "metadata": {"penguin_version": "0.2.13", "agent_state": "custom", "provider": "deepseek", "model_id": "deepseek-flash",
                                                     "thinking": "medium", "status": "completed", "requests": 24,
                                                     "server_ready": True, "timed_out": False, "session_id": "inner-penguin-session"}},
                       "verifier_result": {"rewards": {"reward": 0.75555}},
                       "agent_execution": {"started_at": "2026-10-12T01:00:00.000000Z",
                                           "finished_at": "2026-10-12T01:00:01.234500Z"}}

    def write(self):
        self.result_path.write_text(json.dumps(self.result), encoding="utf-8")
        self.receipt["result_sha256"] = sha(self.result_path.read_bytes())
        plan_data = json.dumps(self.plan).encode()
        (self.private / "plan.json").write_bytes(plan_data)
        self.receipt["phase_plan"]["sha256"] = sha(plan_data)
        self.receipt_path.write_text(json.dumps(self.receipt), encoding="utf-8")

    def convert(self):
        self.write()
        return converter.convert(self.result_path, self.receipt_path, allow_legacy=True)

    def invalid(self):
        with self.assertRaises((converter.InvalidTrial, ValueError, OSError)):
            self.convert()

    def test_success_rounding_identity_and_aggregate_compatibility(self):
        cell = self.convert()
        self.assertEqual(len(cell), 13)
        self.assertEqual(cell["score"], Decimal("75.56"))
        self.assertEqual(cell["duration_ms"], 1235)
        self.assertEqual(cell["session_id"], "harbor:" + self.trial.name)
        results = self.root / "cells"
        results.mkdir()
        (results / "one.json").write_text(converter.cell_json(cell))
        value = aggregator.aggregate(self.private / "plan.json", results)
        self.assertEqual(value["score"], "75.56")

    def test_real_timeout_missing_answer_is_scored_zero(self):
        self.result["agent_result"]["metadata"].update(status="timeout", timed_out=True)
        self.result["verifier_result"]["rewards"]["reward"] = 0
        self.assertEqual(self.convert()["score"], 0)

    def test_alternative_model_is_bound_to_plan_adapter_and_usage(self):
        self.request.update(provider="openrouter", model_id="deepseek/deepseek-v4.1-flash")
        # A receipt alone must not override the phase plan.
        self.invalid()
        self.plan.update(provider=self.request["provider"], model_id=self.request["model_id"])
        # The original adapter execution still names another runtime.
        self.invalid()
        self.result["agent_result"]["metadata"].update(provider=self.request["provider"], model_id=self.request["model_id"])
        self.invalid()
        usage = self.result["agent_result"]["model_usage"].pop("deepseek/deepseek-flash")
        self.result["agent_result"]["model_usage"]["openrouter/deepseek/deepseek-v4.1-flash"] = usage
        cell = self.convert()
        self.assertEqual((cell["provider"], cell["model_id"]), ("openrouter", "deepseek/deepseek-v4.1-flash"))
        results = self.root / "cells"
        results.mkdir()
        (results / "one.json").write_text(converter.cell_json(cell))
        self.assertEqual(aggregator.aggregate(self.private / "plan.json", results)["score"], "75.56")

    def test_malformed_runtime_ids_are_rejected_without_emitting_cells(self):
        for key, value in (("provider", ""), ("provider", "provider/other"),
                           ("model_id", None), ("model_id", "model\nname"), ("model_id", "--flag")):
            with self.subTest(key=key, value=value):
                original = self.request[key]
                self.request[key] = value
                self.invalid()
                self.request[key] = original

    def test_cost_null_remains_null_and_precision_is_preserved(self):
        self.result["agent_result"]["cost_usd"] = None
        self.assertIsNone(self.convert()["cost"])
        self.result["agent_result"]["cost_usd"] = -1
        self.invalid()

    def test_decimal_cost_is_not_rounded_via_a_float(self):
        self.write()
        raw = self.result_path.read_text().replace('"cost_usd": 0.018507',
                                                  '"cost_usd": 0.000000000000000000123456789')
        self.result_path.write_text(raw)
        self.receipt["result_sha256"] = sha(self.result_path.read_bytes())
        self.receipt_path.write_text(json.dumps(self.receipt))
        cell = converter.convert(self.result_path, self.receipt_path, allow_legacy=True)
        self.assertEqual(cell["cost"], Decimal("0.000000000000000000123456789"))
        self.assertIn('"cost":0.000000000000000000123456789', converter.cell_json(cell))

    def test_invalid_reward_is_never_synthetic_zero(self):
        for reward in (None, "0.7", True, -0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(reward=reward):
                self.result["verifier_result"]["rewards"]["reward"] = reward
                self.invalid()
        self.result["verifier_result"]["rewards"] = {}
        self.invalid()

    def test_setup_failures_and_unstarted_requests_rejected(self):
        meta = self.result["agent_result"]["metadata"]
        for status in ("server_failed", "config_failed", "aborted", "running", None):
            with self.subTest(status=status):
                meta["status"] = status
                self.invalid()
        meta["status"] = "completed"
        for count in (0, None, True, "1", -1):
            with self.subTest(requests=count):
                meta["requests"] = count
                self.invalid()

    def test_exception_and_multistep_rejected(self):
        self.result["exception_info"] = {"exception_type": "RuntimeError"}
        self.invalid()
        self.result["exception_info"] = None
        self.result["step_results"] = [{"step_name": "unsupported"}]
        self.invalid()

    def test_contradictory_cli_exit_and_verifier_error_rejected(self):
        meta = self.result["agent_result"]["metadata"]
        meta["exit_code"] = 1
        self.invalid()
        meta["exit_code"] = 0
        meta["run_status"] = "running"
        self.invalid()
        meta["run_status"] = "completed"
        self.result["verifier_result"]["error"] = "grading failed"
        self.invalid()

    def test_runtime_identity_stock_state_and_unknown_usage_rejected(self):
        meta = self.result["agent_result"]["metadata"]
        for key, value in (("agent_state", "stock"), ("thinking", "max"), ("provider", "other"),
                           ("model_id", "another"), ("server_ready", False), ("session_id", "")):
            with self.subTest(key=key):
                previous = meta[key]
                meta[key] = value
                self.invalid()
                meta[key] = previous
        self.result["agent_result"]["model_usage"]["another/model"] = {}
        self.invalid()

    def test_all_state_binding_fields_and_hashes_are_checked(self):
        observed = self.receipt["state_binding"]["observed"]
        for key in converter.BINDING_FIELDS:
            with self.subTest(key=key):
                original = observed[key]
                observed[key] = 3 if key == "state_version" else "different"
                self.invalid()
                observed[key] = original

    def test_task_case_uuid_and_trial_identity_rejected(self):
        for key, wrong in (("task_name", "tg005-train-001"), ("id", "other"), ("trial_name", "other")):
            with self.subTest(key=key):
                old = self.result[key]
                self.result[key] = wrong
                self.invalid()
                self.result[key] = old
        self.request["case_id"] = "CASE-006-tg003-train-001"
        self.invalid()

    def test_validation_membership_comes_from_explicit_plan(self):
        self.plan["phase"] = "validation"
        self.invalid()
        task = "tg003-train-005"
        self.request["case_id"] = "CASE-005-" + task
        self.plan["case_ids"] = ["CASE-005-tg003-train-005"]
        self.receipt["task_name"] = task
        self.result["task_name"] = task
        self.result["task_id"]["path"] = "/checkout/benchmarks/gdpevo/tasks/" + task
        trial = self.root / (task + "__fixtureA")
        self.trial.rename(trial)
        self.trial = trial
        self.result_path = trial / "result.json"
        self.result["trial_name"] = self.receipt["trial_dir"] = trial.name
        self.assertEqual(self.convert()["status"], "ok")
        self.plan["phase"] = "train"
        self.invalid()

    def test_mixed_phase_cases_in_plan_are_rejected(self):
        self.plan["case_ids"].append("CASE-005-tg003-train-005")
        self.invalid()
        self.plan["case_ids"][-1] = "CASE-001-tg003-test-001"
        self.invalid()

    def test_test_case_cannot_be_relabelled_train_or_validation(self):
        task = "tg003-test-001"
        self.request.update(case_id="CASE-001-" + task,
                            benchmark_id="penguinharness-benchmark-gdpevo-test")
        self.plan.update(phase="test", case_ids=[self.request["case_id"]],
                         benchmark_id=self.request["benchmark_id"])
        self.receipt["task_name"] = self.result["task_name"] = task
        self.result["task_id"]["path"] = "/checkout/benchmarks/gdpevo/tasks/" + task
        trial = self.root / (task + "__fixtureA")
        self.trial.rename(trial)
        self.trial = trial
        self.result_path = trial / "result.json"
        self.result["trial_name"] = self.receipt["trial_dir"] = trial.name
        self.assertEqual(self.convert()["status"], "ok")
        self.plan["phase"] = "train"
        self.invalid()
        self.plan["phase"] = "validation"
        self.invalid()

    def test_unknown_or_missing_audit_is_rejected(self):
        for verdict in ("unknown", "fail", "unverified", True):
            with self.subTest(verdict=verdict):
                self.receipt["audit"][0]["verdict"] = verdict
                self.invalid()
        self.receipt["audit"][0]["verdict"] = "pass"
        self.receipt["audit"].pop()
        self.invalid()

    def test_runtime_version_missing_or_mismatched_is_rejected(self):
        metadata = self.result["agent_result"]["metadata"]
        for value in (None, "999.0.0", "0.2.12"):
            with self.subTest(value=value):
                metadata["penguin_version"] = value
                self.invalid()
        del metadata["penguin_version"]
        self.invalid()

    def test_audit_report_must_agree_with_receipt(self):
        evidence = self.private / "audit" / "network.json"
        for report in ({}, [], {"check": "network", "verdict": "fail"},
                       {"check": "trace_complete", "verdict": "pass"},
                       {"check": "network"}):
            with self.subTest(report=report):
                data = json.dumps(report).encode()
                evidence.write_bytes(data)
                self.receipt["audit"][0]["artifact_sha256"] = sha(data)
                self.invalid()

    def test_tampered_or_missing_audit_and_traversal(self):
        evidence = self.private / "audit" / "network.json"
        evidence.write_text("changed")
        self.invalid()
        evidence.unlink()
        self.invalid()
        self.receipt["audit"][0]["artifact"] = "../.vault.toml"
        self.invalid()

    def test_symlink_and_hard_link_evidence_are_rejected(self):
        evidence = self.private / "audit" / "network.json"
        other = self.root / "outside.json"
        other.write_bytes(evidence.read_bytes())
        evidence.unlink()
        evidence.symlink_to(other)
        self.invalid()
        evidence.unlink()
        os.link(other, evidence)
        self.invalid()

    def test_result_and_plan_bytes_are_bound(self):
        self.write()
        self.result_path.write_text(self.result_path.read_text() + "\n")
        with self.assertRaises(converter.InvalidTrial):
            converter.convert(self.result_path, self.receipt_path, allow_legacy=True)
        self.write()
        (self.private / "plan.json").write_text("{}")
        with self.assertRaises(converter.InvalidTrial):
            converter.convert(self.result_path, self.receipt_path, allow_legacy=True)

    def test_duplicate_json_keys_and_wrong_receipt_schema(self):
        self.write()
        self.receipt_path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaises(converter.InvalidTrial):
            converter.convert(self.result_path, self.receipt_path, allow_legacy=True)
        self.receipt["unexpected"] = "value"
        self.invalid()

    def test_timing_missing_naive_backwards_or_invalid_is_rejected(self):
        for value in (None, "bad", "2026-10-12T01:00:01", "2026-10-12T00:59:59Z"):
            with self.subTest(value=value):
                self.result["agent_execution"]["finished_at"] = value
                self.invalid()

    def test_cli_error_never_emits_a_normal_cell(self):
        self.result["verifier_result"] = None
        self.write()
        execution = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "harbor_result.py"),
                                    "--result", str(self.result_path), "--receipt", str(self.receipt_path), "--allow-legacy"],
                                   capture_output=True, text=True)
        self.assertEqual(execution.returncode, 2)
        self.assertEqual(execution.stdout, "")
        self.assertIn("invalid Harbor trial", execution.stderr)


if __name__ == "__main__":
    unittest.main()
