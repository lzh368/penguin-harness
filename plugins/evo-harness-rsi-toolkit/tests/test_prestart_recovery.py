"""One-shot original-worker engineering recovery; no model/network calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/evo-harness-rsi-toolkit/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("recovery_control", SCRIPTS / "control.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.call = {"subagent_id": "subagent-original", "prompt": "Fixed pre-start repair only"}
        self.mode = {"mode": "controller", "session_id": "parent", "config_sha256": "frozen"}
        self.result = self.root / "failed.json"
        self.write(self.result, {"finished_at": "done", "exception_info": {"type": "network"},
                                 "agent_execution": None, "verifier_result": None, "agent_result": {}})
        self.ticket = self.root / "ticket.json"
        self.write(self.ticket, {"slot": "h0/case/1", "phase": "h0"})
        self.grant = {"call": self.call, "phase": "h0", "slot": "h0/case/1",
                      "worker_ticket": str(self.ticket), "worker_session": "original",
                      "failed_trials": [{"result": str(self.result), "sha256": c.sha(self.result)}],
                      "operation_id": "extra-reservation"}
        self.ledger = self.root / "journal.json"
        self.write(self.ledger, {"controller_session_id": "parent", "config_sha256": "frozen",
            "budget_usd": 10, "known_cost_usd": 3, "operations": {"extra-reservation": {
                "status": "reserved", "reserve_usd": 1,
                "request_sha256": c.evo.digest(c.canonical(self.call).encode())}}})
        self.patches = [patch.object(c, "native_worker", return_value={"session_id": "original", "handle": "subagent-original"}),
                        patch.object(c, "freeze"), patch.object(c, "journal_path", return_value=self.ledger)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def write(self, p, v):
        p.write_text(json.dumps(v))

    def invoke(self, call=None, call_id="once"):
        c.authorize_engineering_recovery(call or self.call, {}, self.root, self.mode, call_id, self.grant)

    def test_original_worker_one_shot_and_no_budget_reset(self):
        before = self.ledger.read_bytes()
        self.invoke()
        self.invoke()  # Same native call id is idempotent.
        with self.assertRaisesRegex(ValueError, "already dispatched"):
            self.invoke(call_id="second-call")
        self.assertEqual(self.ledger.read_bytes(), before)
        c.freeze.assert_called_with(self.mode, "h0")

    def test_altered_worker_or_prompt_denied(self):
        for fields in ({"subagent_id": "another"}, {"prompt": "Change the answer"}, {"abort": True}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.invoke({**self.call, **fields})

    def test_executed_graded_or_ambiguous_attempt_denied(self):
        for field, value in (("agent_execution", {"started_at": "earlier"}),
                             ("verifier_result", {"rewards": {"reward": 0.1}}),
                             ("agent_result", {"model_usage": {"model": {"input": 1}}}),
                             ("finished_at", None)):
            result = json.loads(self.result.read_bytes())
            result[field] = value
            self.write(self.result, result)
            self.grant["failed_trials"][0]["sha256"] = c.sha(self.result)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.invoke()
            self.set_result()

    def set_result(self):
        self.write(self.result, {"finished_at": "done", "exception_info": {"type": "network"},
                                 "agent_execution": None, "verifier_result": None, "agent_result": {}})
        self.grant["failed_trials"][0]["sha256"] = c.sha(self.result)

    def test_changed_original_evidence_denied(self):
        self.result.write_text("{}")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.invoke()

    def test_manifest_must_be_operator_hash_bound(self):
        manifest = self.root / "recovery.json"
        self.write(manifest, {"schema": "evo-prestart-recovery-v1", "session_id": "parent",
                             "config_sha256": "frozen", "grants": [self.grant]})
        mode = {**self.mode, "engineering_recovery": {"manifest": str(manifest), "sha256": c.sha(manifest)}}
        self.assertEqual(c.engineering_recovery_grant(self.root, mode, "subagent-original"), self.grant)
        self.assertIsNone(c.engineering_recovery_grant(self.root, mode, "unregistered"))
        manifest.write_text("{}")
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            c.engineering_recovery_grant(self.root, mode, "subagent-original")

    def test_latest_result_preserves_failed_request_in_trace(self):
        def request(text):
            return [{"payload": {"type": "request_begin"}},
                    {"payload": {"type": "text", "role": "assistant", "text": text}},
                    {"payload": {"type": "request_end", "status": "completed"}}]
        rows = request("status: failed") + request("status: ok")
        self.assertEqual(c.worker_text(rows, last_request=True), "status: ok")
        self.assertIn("status: failed", c.worker_text(rows))


if __name__ == "__main__":
    unittest.main()
