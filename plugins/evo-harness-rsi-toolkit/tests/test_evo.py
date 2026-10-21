"""Synthetic fixtures only: no models, real benchmark answers or reported scores."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1] / "skills/evo-harness-rsi-toolkit/scripts/evo.py"
spec = importlib.util.spec_from_file_location("evo", TOOL)
evo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evo)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-evo-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.train = [f"train_{n:03d}" for n in range(1, 6)]
        self.contexts = []
        self.candidates = []
        self.parent = self.root / "parent.jsonl"
        self.parent.write_text("\n".join(json.dumps({"type": "event_msg", "payload": {
            "type": "subagent", "session_id": f"solver-{n}"}}) for n in range(1, 6)) + "\n")
        for n, task in enumerate(self.train, 1):
            answer = self.root / f"{task}-answer.json"
            answer.write_text('{"synthetic": true}')
            fb = {"task_id": task, "attempt_id": "attempt-1", "answer_sha256": evo.digest(answer.read_bytes()),
                  "status": "ok", "http_status": 200, "score": 0.5, "correct": False,
                  "raw_response": '{"score": 0.5, "correct": false}'}
            feedback = self.save(f"{task}-feedback", fb)
            binding = {"session_id": f"solver-{n}", "provider": "fixture", "model_id": "no-model",
                       "source": "subagent", "workspace": "/synthetic/app", "agent_state": "/synthetic/state",
                       "parent_trace": str(self.parent)}
            trace = self.root / f"{task}.jsonl"
            inp = self.root / f"{task}-input.txt"
            inp.write_text("SYNTHETIC allowed facts")
            grant = {"evo_task": {"split": "train", "task_id": task, "attempt_id": "attempt-1", "inputs": [str(inp)]}}
            rows = [{"type": "session_meta", "payload": binding},
                    {"type": "model_msg", "payload": {"type": "text", "role": "user", "text": json.dumps(grant)}},
                    {"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                     "arguments": '{}', "tool_call_id": "read-1"}},
                    {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "read-1",
                     "output": json.dumps(evo.material({"path": str(inp)}))}},
                    {"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                     "arguments": '{}', "tool_call_id": "judge-1"}},
                    {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "judge-1",
                     "output": "exit code 0\n" + json.dumps(fb)}}]
            trace.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            dest = self.root / f"{task}-context.json"
            req = {"split": "train", "task_id": task, "attempt_id": "attempt-1", "train_ids": self.train,
                   "answer": str(answer), "feedback": str(feedback), "inputs": [str(inp)],
                   "traces": [str(trace)], "binding": binding, "output": str(dest)}
            evo.capture(req)
            self.contexts.append(str(dest))
            self.candidates.append({"id": f"candidate-{n}", "task_id": task, "attempt_id": "attempt-1",
                "lesson": "SYNTHETIC: inspect the current ledger before calculating a summary",
                "trigger": "ledger task", "scope_hint": "synthetic-company only",
                "evidence": [{"kind": "tool", "tool_call_id": "judge-1", "claim": "partial feedback only",
                              "epistemic": "observed"}]})
        self.candidate_file = self.save("candidates", self.candidates)
        self.library = self.save("library", [])
        self.entry = {"id": "ledger-procedure", "level": "topic", "topic": "ledger", "trigger": "ledger task",
                      "scope": "synthetic-company", "body": "Read current ledger, join by documented key, then sum valid rows."}
        self.decisions = [{"op": "ADD", "candidate_ids": [c["id"] for c in self.candidates],
                           "reason": "SYNTHETIC shared procedure", "entry": self.entry}]
        self.req = {"train_ids": self.train, "contexts": self.contexts, "candidates": str(self.candidate_file),
                    "library": str(self.library), "decisions": str(self.save("decisions", self.decisions)),
                    "limits": {"general": 5, "topic": 5}, "output": str(self.root / "compiled.json")}

    def save(self, name, value):
        path = self.root / f"{name}.json"
        path.write_bytes(evo.encoded(value))
        return path

    def test_complete_batch_compiles_exports_and_selects_exact_content(self):
        self.assertEqual(evo.compile_batch(self.req)["candidates"], 5)
        pkg = self.root / "skill"
        receipt = evo.export_skill({"library": self.req["output"], "limits": self.req["limits"],
                                    "max_entries": 5, "max_bytes": 16000, "output": str(pkg)})
        self.assertEqual(receipt, evo.validate_package(pkg))
        selected = json.loads(subprocess.check_output([sys.executable, "-B", str(pkg / "select.py"),
                                                       "--ids", "ledger-procedure"], text=True))
        self.assertIn(self.entry["body"], selected["text"])
        self.assertEqual(selected["bytes"], len(selected["text"].encode()))
        self.assertFalse(any("candidate" in p or "context" in p for p in receipt["files"]))
        self.assertFalse(list(pkg.rglob("__pycache__")))

    def test_partial_batch_cannot_compile(self):
        self.req["contexts"] = self.contexts[:-1]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            evo.compile_batch(self.req)

    def test_same_candidate_can_support_both_harness_levels(self):
        general = {**self.entry, "id": "general-procedure", "level": "general", "scope": "documented ledgers"}
        self.decisions.append({"op": "ADD", "candidate_ids": [c["id"] for c in self.candidates],
                               "reason": "shared structure across topics", "entry": general})
        self.save("decisions", self.decisions)
        self.assertEqual(evo.compile_batch(self.req)["entries"], 2)

    def test_captured_input_and_answer_tampering_rejected(self):
        original = evo.read(self.contexts[0])
        for field in ("inputs", "answer"):
            value = copy.deepcopy(original)
            if field == "inputs":
                value["inputs"][0]["text"] = "SYNTHETIC foreign teaching"
            else:
                value["answer"] = {"modified": True}
            Path(self.contexts[0]).write_bytes(evo.encoded(value))
            with self.assertRaisesRegex(ValueError, "changed"):
                evo.compile_batch(self.req)

    def test_rehashed_input_cannot_replace_solver_observations(self):
        value = evo.read(self.contexts[0])
        value["inputs"][0]["text"] = "SYNTHETIC forged rule with a matching new hash"
        value["inputs"][0]["sha256"] = evo.digest(value["inputs"][0]["text"].encode())
        Path(self.contexts[0]).write_bytes(evo.encoded(value))
        with self.assertRaisesRegex(ValueError, "absent from Solver observations"):
            evo.compile_batch(self.req)

    def workspace_capture_request(self):
        task = self.train[0]
        source = evo.read(self.contexts[0])
        binding = {**source["trace"]["binding"], "source": "cli", "workspace": str(self.root)}
        binding.pop("parent_trace")
        inp = self.root / f"{task}-input.txt"
        rows = [{"type": "session_meta", "payload": binding},
                {"type": "model_msg", "payload": {"type": "text", "role": "user",
                 "text": "Solve the SYNTHETIC public workspace task."}},
                {"type": "model_msg", "payload": {"type": "tool_call", "name": "read_file",
                 "arguments": json.dumps({"file_path": str(inp)}), "tool_call_id": "read-1"}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "read-1",
                 "output": "\n".join(f"{n}\t{line}" for n, line in enumerate(inp.read_text().splitlines(), 1))}}]
        trace = self.root / f"{task}-workspace.jsonl"
        trace.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        self.candidates[0]["evidence"] = [{"kind": "input", "sha256": evo.digest(inp.read_bytes()),
            "claim": "SYNTHETIC public task constraints", "epistemic": "observed"}]
        self.save("candidates", self.candidates)
        identity = {"agent_id": "synthetic-target", "expected_version": 1, "provider": "fixture",
                    "model_id": "no-model", "thinking_level": "high", "run": 1}
        receipt = {"protocol_version": 1, "status": "ok", "case_id": task, "score": 50,
                   "session_id": "solver-1", **identity}
        return {"op": "capture-evaluation", "split": "train", "task_id": task,
             "attempt_id": "attempt-1", "train_ids": self.train, "answer": str(self.root / f"{task}-answer.json"),
             "evaluation": str(self.save("evaluation", receipt)), "evaluation_identity": identity,
             "inputs": [str(inp)], "traces": [str(trace)], "binding": binding,
             "output": str(self.root / "workspace-context.json")}

    def test_native_workspace_receipt_has_no_fake_http_status(self):
        result = evo.capture(self.workspace_capture_request())
        context = evo.read(result["path"])
        self.assertNotIn("http_status", context["feedback"])
        self.assertTrue(context["negative"])
        self.contexts[0] = result["path"]
        self.assertEqual(evo.compile_batch(self.req)["entries"], 1)

    def test_workspace_receipt_rejects_foreign_case_session_and_runtime(self):
        req = self.workspace_capture_request()
        original = evo.read(req["evaluation"])
        for key, value in (("case_id", "test_001"), ("session_id", "other-solver"),
                           ("agent_id", "other-target"),
                           ("provider", "other-provider"), ("model_id", "other-model"),
                           ("expected_version", 2), ("run", 2), ("thinking_level", "low")):
            with self.subTest(field=key):
                self.save("evaluation", {**original, key: value})
                with self.assertRaises(ValueError):
                    evo.capture(req)

    def test_workspace_receipt_requires_complete_expected_identity(self):
        req = self.workspace_capture_request()
        for key in req["evaluation_identity"]:
            with self.subTest(missing=key):
                incomplete = {k: v for k, v in req["evaluation_identity"].items() if k != key}
                with self.assertRaises(ValueError):
                    evo.capture({**req, "evaluation_identity": incomplete})

    def test_workspace_final_artifact_may_be_plain_text(self):
        req = self.workspace_capture_request()
        final_text = "SYNTHETIC final explanation, not a JSON object.\n结果已交付。\n"
        Path(req["answer"]).write_text(final_text)
        result = evo.capture(req)
        context = evo.read(result["path"])
        self.assertEqual(context["answer"], final_text)
        self.assertEqual(context["answer_sha256"], evo.digest(final_text.encode()))
        self.contexts[0] = result["path"]
        self.assertEqual(evo.compile_batch(self.req)["entries"], 1)

    def test_successful_workspace_execution_cannot_generate_candidate(self):
        req = self.workspace_capture_request()
        receipt = evo.read(req["evaluation"])
        self.save("evaluation", {**receipt, "score": 100})
        result = evo.capture(req)
        self.assertFalse(evo.read(result["path"])["negative"])
        self.contexts[0] = result["path"]
        with self.assertRaisesRegex(ValueError, "successful execution"):
            evo.compile_batch(self.req)

    def test_workspace_input_requires_complete_native_read(self):
        req = self.workspace_capture_request()
        Path(req["inputs"][0]).write_text("SYNTHETIC fact absent from the captured read")
        with self.assertRaisesRegex(ValueError, "not fully read"):
            evo.capture(req)

    def test_test_context_and_mismatched_attempt_are_rejected(self):
        path = Path(self.contexts[0])
        context = evo.read(path)
        for field, value in (("split", "test"), ("attempt_id", "attempt-2")):
            changed = copy.deepcopy(context)
            changed[field] = value
            path.write_bytes(evo.encoded(changed))
            with self.assertRaises(ValueError):
                evo.compile_batch(self.req)

    def test_judge_error_never_becomes_zero(self):
        c = evo.read(self.contexts[0])
        c["feedback"].update(status="error", score=None, correct=None)
        Path(self.contexts[0]).write_bytes(evo.encoded(c))
        with self.assertRaisesRegex(ValueError, "judge error"):
            evo.compile_batch(self.req)

    def test_changed_raw_trace_cannot_teach(self):
        with (self.root / "train_001.jsonl").open("a") as handle:
            handle.write('{}\n')
        with self.assertRaisesRegex(ValueError, "changed"):
            evo.compile_batch(self.req)

    def test_known_wrong_tool_evidence_rejected(self):
        self.candidates[0]["evidence"][0]["tool_call_id"] = "another-attempt"
        self.save("candidates", self.candidates)
        with self.assertRaisesRegex(ValueError, "not in attempt"):
            evo.compile_batch(self.req)

    def test_candidates_require_decisions_and_no_duplicate_application(self):
        self.decisions[0]["candidate_ids"] = ["candidate-1"]
        self.save("decisions", self.decisions)
        with self.assertRaisesRegex(ValueError, "every candidate"):
            evo.compile_batch(self.req)
        self.save("decisions", self.decisions * 2)
        with self.assertRaisesRegex(ValueError, "twice"):
            evo.compile_batch(self.req)

    def test_merge_revise_and_skip_without_score_selection(self):
        self.save("library", [self.entry])
        for op in ("MERGE", "REVISE"):
            self.decisions[0]["op"] = op
            self.decisions[0]["entry"]["body"] = f"{op}: SYNTHETIC amended procedure"
            self.save("decisions", self.decisions)
            self.req["output"] = str(self.root / f"{op}.json")
            evo.compile_batch(self.req)
            self.assertIn(op, evo.read(self.req["output"])[0]["body"])
        self.save("decisions", [{"op": "SKIP", "candidate_ids": [c["id"] for c in self.candidates], "reason": "unsupported"}])
        self.req["output"] = str(self.root / "skip.json")
        evo.compile_batch(self.req)
        self.assertEqual(evo.read(self.req["output"]), evo.read(self.library))

    def test_one_task_topic_rule_does_not_need_repeated_support(self):
        decisions = [{"op": "ADD", "candidate_ids": ["candidate-1"], "reason": "single local rule", "entry": self.entry},
                     {"op": "SKIP", "candidate_ids": [f"candidate-{n}" for n in range(2, 6)], "reason": "noise"}]
        self.save("decisions", decisions)
        self.assertEqual(evo.compile_batch(self.req)["entries"], 1)

    def test_entry_slot_limit_rejected_without_silent_truncation(self):
        self.req["limits"]["topic"] = 0
        with self.assertRaisesRegex(ValueError, "budget"):
            evo.compile_batch(self.req)

    def test_empty_harness_is_deterministic_and_cold_starts(self):
        receipts = []
        for name in ("H0", "final"):
            receipts.append(evo.export_skill({"library": str(self.library), "limits": self.req["limits"],
                          "max_entries": 5, "max_bytes": 16000, "output": str(self.root / name)}))
        self.assertEqual(receipts[0], receipts[1])
        output = subprocess.check_output([sys.executable, "-B", str(self.root / "H0/select.py"), "--ids", ""], text=True)
        self.assertEqual(json.loads(output)["text"], "")

    def test_export_refuses_symlink_unlisted_trace_or_modified_code(self):
        pkg = self.root / "skill"
        evo.export_skill({"library": str(self.library), "limits": self.req["limits"],
                          "max_entries": 5, "max_bytes": 16000, "output": str(pkg)})
        (pkg / "raw.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "unlisted"):
            evo.validate_package(pkg)
        (pkg / "raw.json").unlink()
        (pkg / "raw.json").symlink_to(self.library)
        with self.assertRaisesRegex(ValueError, "symlink"):
            evo.validate_package(pkg)

    def test_trace_requires_pairing_parent_binding_and_known_source(self):
        path = self.root / "train_001.jsonl"
        binding = evo.read(self.contexts[0])["trace"]["binding"]
        original = path.read_text()
        path.write_text("\n".join(original.splitlines()[:-1]) + "\n")
        with self.assertRaisesRegex(ValueError, "pairing"):
            evo.trace_view([path], binding)
        path.write_text(original)
        self.parent.write_text("")
        with self.assertRaisesRegex(ValueError, "referenced"):
            evo.trace_view([path], binding)


class JournalTests(unittest.TestCase):
    def test_no_duplicate_dispatch_unknown_cost_reserves_budget_and_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            req = {"journal": str(Path(root) / "journal.json"), "config_sha256": "fixture"}
            evo.journal({**req, "action": "init", "controller_session_id": "synthetic-controller", "budget_usd": 5})
            reserve = {**req, "action": "reserve", "operation_id": "training", "reserve_usd": 4,
                       "request_sha256": "fixture", "controller_trace": "fixture", "role": "group"}
            evo.journal(reserve)
            with self.assertRaisesRegex(ValueError, "already reserved"):
                evo.journal(reserve)
            evo.journal({**req, "action": "bind", "operation_id": "training", "external_id": "native-fixture"})
            evo.journal({**req, "action": "settle", "operation_id": "training", "terminated": True,
                         "cost_usd": None, "evidence": []})
            with self.assertRaisesRegex(ValueError, "budget"):
                evo.journal({**reserve, "operation_id": "test", "request_sha256": "test-request", "reserve_usd": 2})
            evo.journal({**req, "action": "reconcile_cost", "operation_id": "training", "cost_usd": 1, "evidence": []})
            evo.journal({**reserve, "operation_id": "test", "request_sha256": "test-request", "reserve_usd": 2})
            with self.assertRaisesRegex(ValueError, "configuration"):
                evo.journal({**req, "action": "status", "config_sha256": "changed"})

    def test_judge_error_is_durable_and_never_automatically_repeated(self):
        with tempfile.TemporaryDirectory() as root:
            answer = Path(root) / "answer.json"
            answer.write_text('{}')
            req = {"split": "train", "train_ids": ["train_001"], "task_id": "train_001", "attempt_id": "a1",
                   "url": "http://127.0.0.1:9005/api/judge", "answer": str(answer), "output": str(Path(root) / "fb.json")}
            with patch.object(evo.urllib.request, "build_opener") as opener:
                opener.return_value.open.side_effect = OSError("SYNTHETIC network failure")
                one = evo.judge(req)
                two = evo.judge(req)
                self.assertEqual(one, two)
                self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertIsNone(one["score"])
                self.assertEqual(one["status"], "error")
            with self.assertRaises(ValueError):
                evo.judge({**req, "split": "test"})


if __name__ == "__main__":
    unittest.main()
