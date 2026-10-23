"""Frozen cross-run Base provenance; no models, fabricated workers or rescores."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from test_control import control


class BaselineReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cells = [{"case_id": "CASE-001", "run": 1, "score": 37}]
        self.matrix = {"identity": {"agent_id": "target", "expected_version": 1},
                       "case_ids": ["CASE-001"], "runs": 1,
                       "cells": self.save("original-cells.json", self.cells)}
        matrix_path = self.save("original-matrix.json", self.matrix)
        audit = self.save("original-injection.json", {"complete": True, "audit_passed": True})
        self.raw = {"AGENTS.md": b"unchanged native context", "system_config.yaml": b"unchanged tools"}
        self.proof = {"schema": "evo-reviewed-h0-reference-v1", "complete": True,
                      "source_experiment": "original", "workers": 1, "runtime": {"thinking_level": "max"},
                      "model_capacity": {"max_output_tokens": 131072}, "bundle_sha256": "bundle",
                      "test_ids": ["task-001"], "runs": 1, "matrix_request": matrix_path,
                      "injection_review": audit, "source_files": {p: control.sha(p) for p in
                          (matrix_path, self.matrix["cells"], audit)},
                      "h0_files": {k: control.evo.digest(v) for k, v in self.raw.items()}}
        self.proof_path = self.save("review.json", self.proof)
        self.config = {"runtime": self.proof["runtime"], "test_ids": ["task-001"], "test_repeats": 1,
                       "model_capacity": self.proof["model_capacity"],
                       "deployment": {"bundle_sha256": "bundle"}}
        self.rebind()
        self.mode = {"config": str(self.root / "config.json"), "protocol": str(self.root / "protocol.py"),
                     "freeze_request": self.save("freeze.json", {"h0_snapshot": "h0.tar.gz"})}
        self.acceptance = SimpleNamespace(snapshot=lambda _: {"files": self.raw})
        self.module = SimpleNamespace(helper=lambda _: self.acceptance, matrix=lambda _: {"complete": True})
        self.mock = patch.object(control, "protocol_module", return_value=self.module)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def save(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return str(path)

    def rebind(self):
        self.config["h0_reference"] = {"receipt": self.proof_path, "sha256": control.sha(self.proof_path)}
        self.save("config.json", self.config)

    def test_original_scores_and_workers_are_referenced_without_new_dispatch(self):
        result = control.verify_worker_matrix(self.mode, "h0", self.matrix)
        self.assertTrue(result["reused"])
        self.assertEqual(result["workers"], 1)
        self.assertEqual(json.loads(Path(self.matrix["cells"]).read_text()), self.cells)
        with patch.object(control, "registered_benchmarks", return_value={}), \
             patch.object(control, "runtime_call"):
            with self.assertRaisesRegex(ValueError, "duplicate Base"):
                control.worker_request({"phase": "h0", "call": {"run_in_background": True}}, self.root,
                                       {**self.mode, "mode": "controller"})

    def test_changed_review_or_original_evidence_is_rejected(self):
        Path(self.proof_path).write_text("{}")
        with self.assertRaisesRegex(ValueError, "review changed"):
            control.referenced_h0(self.mode, self.matrix)
        self.save("review.json", self.proof)
        Path(self.matrix["cells"]).write_text("[]")
        with self.assertRaisesRegex(ValueError, "source evidence changed"):
            control.referenced_h0(self.mode, self.matrix)

    def test_changed_runtime_or_actual_context_and_tools_are_rejected(self):
        self.config["runtime"] = {"thinking_level": "high"}
        self.rebind()
        with self.assertRaisesRegex(ValueError, "conditions differ"):
            control.referenced_h0(self.mode, self.matrix)
        self.config["runtime"] = self.proof["runtime"]
        self.rebind()
        self.raw["system_config.yaml"] = b"changed tools"
        with self.assertRaisesRegex(ValueError, "State/tools/context differ"):
            control.referenced_h0(self.mode, self.matrix)

    def test_new_matrix_cannot_change_original_score_or_omit_a_cell(self):
        altered = {**self.matrix, "cells": self.save("new-cells.json", [])}
        with self.assertRaisesRegex(ValueError, "matrix differs"):
            control.referenced_h0(self.mode, altered)

    def test_failed_original_audit_cannot_be_reused(self):
        self.save("original-injection.json", {"complete": False})
        self.proof["source_files"][self.proof["injection_review"]] = control.sha(self.proof["injection_review"])
        self.save("review.json", self.proof)
        self.rebind()
        with self.assertRaisesRegex(ValueError, "injection audit failed"):
            control.referenced_h0(self.mode, self.matrix)


if __name__ == "__main__":
    unittest.main()
