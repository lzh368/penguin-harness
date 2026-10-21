"""Synthetic fixtures only: no models, real benchmark answers or reported scores."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import subprocess
import shlex
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
TOOL = Path(__file__).resolve().parents[1] / "skills/evo-harness-rsi-toolkit/scripts/evo.py"
spec = importlib.util.spec_from_file_location("evo", TOOL)
evo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evo)


def install_training(root, train):
    """Install a real helper copy; every data value is synthetic."""
    helper = root / "method/scripts/evo.py"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(TOOL.read_bytes())
    training = {"train_ids": train, "attempt_id": "attempt-1", "config_sha256": "a" * 64,
                "private_learning": str(root / "private"), "answer_dir": str(root / "answers"),
                "feedback_dir": str(root / "private/feedback")}
    (helper.parent.parent / "training.json").write_bytes(evo.encoded(training))
    (root / "answers").mkdir()
    return helper, training


def judge_request(root, training, task, url="http://127.0.0.1:9005/api/judge"):
    paths = evo.judge_paths(training, task, training["attempt_id"])
    req = {"op": "judge", "split": "train", "task_id": task, "attempt_id": training["attempt_id"],
           "train_ids": training["train_ids"], "url": url, "answer": paths["answer"], "output": paths["output"]}
    path = Path(paths["judge_request"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(evo.encoded(req))
    return req, path


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-evo-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.train = [f"train_{n:03d}" for n in range(1, 6)]
        self.helper, self.training = install_training(self.root, self.train)
        installed = patch.object(evo, "__file__", str(self.helper))
        installed.start()
        self.addCleanup(installed.stop)
        self.contexts = []
        self.candidates = []
        self.parent = self.root / "parent.jsonl"
        self.parent.write_text("\n".join(json.dumps({"type": "event_msg", "payload": {
            "type": "subagent", "session_id": f"solver-{n}"}}) for n in range(1, 6)) + "\n")
        for n, task in enumerate(self.train, 1):
            answer = self.root / "answers" / f"{task}.json"
            answer.write_text('{"synthetic": true}')
            judge_req, judge_path = judge_request(self.root, self.training, task)
            with patch.object(evo.urllib.request, "build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.status, response.read.return_value = 200, b'{"score": 0.5, "correct": false}'
                fb = evo.judge(judge_req)
            feedback = Path(judge_req["output"])
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
                     "arguments": json.dumps({"cmd": shlex.join(["python3", "-B", str(self.helper), str(self.root / 'read-material.json')])}), "tool_call_id": "read-1"}},
                    {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "read-1",
                     "output": json.dumps(evo.material({"path": str(inp)}))}},
                    {"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                     "arguments": json.dumps({"cmd": shlex.join(["python3", "-B", str(self.helper), str(judge_path)])}),
                     "tool_call_id": "judge-1"}},
                    {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "judge-1",
                     "output": "exit code 0\n" + json.dumps(fb)}}]
            trace.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
            dest = self.root / f"{task}-context.json"
            req = {"split": "train", "task_id": task, "attempt_id": "attempt-1", "train_ids": self.train,
                   "answer": str(answer), "feedback": str(feedback), "inputs": [str(inp)],
                   "judge_request": str(judge_path),
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
             "attempt_id": "attempt-1", "train_ids": self.train, "answer": str(self.root / "answers" / f"{task}.json"),
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


    def rewrite_solver_trace(self, change):
        context = evo.read(self.contexts[0])
        path = Path(context["trace"]["sources"][0]["path"])
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        change(rows)
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        # Updating the capture and its hashes still must not turn a bad native
        # operation into valid learning evidence.
        context["trace"] = evo.trace_view([path], context["trace"]["binding"])
        Path(self.contexts[0]).write_bytes(evo.encoded(context))

    def test_echoed_receipt_or_shell_wrapped_judge_is_not_native_judge_evidence(self):
        original = Path(self.root / "train_001.jsonl").read_text()
        for command in ("printf '%s' synthetic-fabricated-receipt", "python3 -c 'print(1)'",
                        "echo before; python3 -B irrelevant request.json"):
            with self.subTest(command=command):
                (self.root / "train_001.jsonl").write_text(original)
                self.rewrite_solver_trace(lambda rows: rows[4]["payload"].update(arguments=json.dumps({"cmd": command})))
                with self.assertRaisesRegex(ValueError, "canonical exec|canonical judge"):
                    evo.compile_batch(self.req)

    def test_repeated_canonical_judge_calls_are_not_valid_learning(self):
        def duplicate(rows):
            pair = copy.deepcopy(rows[-2:])
            for row in pair:
                row["payload"]["tool_call_id"] = "judge-2"
            rows.extend(pair)
        self.rewrite_solver_trace(duplicate)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            evo.compile_batch(self.req)

    def test_raw_http_probe_before_canonical_judge_invalidates_attempt(self):
        def probe(rows):
            rows[2:2] = [
                {"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                    "arguments": json.dumps({"cmd": "curl -X POST http://127.0.0.1:9005/api/judge"}),
                    "tool_call_id": "probe"}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "probe",
                    "output": '{"score": 0.2, "correct": false}'}}]
        self.rewrite_solver_trace(probe)
        with self.assertRaisesRegex(ValueError, "direct judge probe"):
            evo.compile_batch(self.req)

    def test_business_response_score_fields_are_not_proof_of_a_judge_call(self):
        def probe(rows):
            rows.extend([
                {"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                    "arguments": json.dumps({"cmd": "python3 synthetic-probe.py"}), "tool_call_id": "probe"}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "probe",
                    "output": '{"score": 0.8, "correct": false}'}}])
        self.rewrite_solver_trace(probe)
        self.assertEqual(evo.compile_batch(self.req)["entries"], 1)

    def test_crm_request_writer_format_is_data_then_one_canonical_judge(self):
        # Regression for CRM003 call_00_jMMd3QpH8YABkHnU2A5M7475. Preserve the
        # real command shape, but never commit the task answer or original trace.
        context = evo.read(self.contexts[0])
        request_path = context["judge_request"]["path"]
        answer_path = str(self.root / "answers/train_001.json")
        request = evo.read(request_path)
        command = (f"cd {shlex.quote(str(self.root))}\npython3 - <<'EOF'\n"
                   "import json,os\n"
                   f"answer=json.load(open({answer_path!r}))\n"
                   "print(len(answer))\n"
                   f"request={request!r}\n"
                   f"os.makedirs(os.path.dirname({request_path!r}),exist_ok=True)\n"
                   f"json.dump(request,open({request_path!r},'w'))\n"
                   "print(json.dumps({'prepared':True}))\nEOF\n"
                   f"python3 -B {shlex.quote(str(self.helper))} reqs/digest_answer.json")
        self.assertIn("/api/judge", command)
        self.assertFalse(evo.direct_judge_probe(command))
        def writer(rows):
            rows[4:4] = [
                {"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                    "arguments": json.dumps({"cmd": command}), "tool_call_id": "request-writer"}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "request-writer",
                    "output": '{"prepared": true}'}}]
        self.rewrite_solver_trace(writer)
        self.assertEqual(evo.compile_batch(self.req)["entries"], 1)

    def test_noncanonical_short_receipt_echo_is_rejected(self):
        receipt = evo.judge_stdout(evo.read(self.contexts[0])["feedback"])
        def echo(rows):
            rows.extend([
                {"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                    "arguments": json.dumps({"cmd": "printf fabricated"}), "tool_call_id": "extra"}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "extra",
                    "output": json.dumps(receipt)}}])
        self.rewrite_solver_trace(echo)
        with self.assertRaisesRegex(ValueError, "canonical exec"):
            evo.compile_batch(self.req)

    def test_role_prevalidation_preserves_draft_and_checks_export_budget(self):
        request = {**self.req, "max_entries": 5, "max_bytes": 16000}
        before = Path(request["decisions"]).read_bytes()
        result = evo.validate_decisions(request)
        self.assertTrue(result["complete"])
        self.assertEqual(Path(request["decisions"]).read_bytes(), before)
        self.assertFalse(Path(self.req["output"]).exists())
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            evo.validate_decisions({**request, "max_bytes": 5})
        with self.assertRaisesRegex(ValueError, "namespace"):
            evo.validate_candidate_file({**self.req, "candidate_prefix": "train_001-attempt-1-"})
        self.assertTrue(evo.validate_candidate_file(self.req)["complete"])

    def test_unreadable_index_metadata_is_rejected_before_decisions_are_sealed(self):
        self.decisions[0]["entry"]["trigger"] = "x" * 2100
        self.save("decisions", self.decisions)
        request = {**self.req, "max_entries": 5, "max_bytes": 16000}
        before = Path(request["decisions"]).read_bytes()
        with self.assertRaisesRegex(ValueError, "2000 UTF-16.*shorten trigger/scope"):
            evo.validate_decisions(request)
        self.assertEqual(Path(request["decisions"]).read_bytes(), before)
        self.assertFalse(Path(request["output"]).exists())

    def test_actual_serialized_index_line_limit_matches_native_utf16_boundaries(self):
        # Measure the emitted framing rather than assuming field length or
        # forgetting that sorted JSON places trigger last (without a comma).
        empty_line = next(line for line in evo.encoded({"entries": [{"id": "synthetic", "trigger": ""}]}).decode().split("\n")
                          if '"trigger":' in line)
        overhead = len(empty_line.encode("utf-16-le")) // 2
        capacity = 2000 - overhead
        values = [("ascii", "x" * capacity, "x" * (capacity + 1)),
                  ("emoji", "😀" * (capacity // 2) + "x" * (capacity % 2),
                   "😀" * (capacity // 2 + 1)),
                  ("escaped_quote", '"' * (capacity // 2) + "x" * (capacity % 2),
                   '"' * (capacity // 2 + 1)),
                  ("unicode_line_separator", "x" + "\u2028" * (capacity - 1), "x" + "\u2028" * capacity)]
        for label, valid, invalid in values:
            with self.subTest(label=label):
                self.save("index-library", [{**self.entry, "trigger": valid}])
                base = {"library": str(self.root / "index-library.json"), "limits": self.req["limits"],
                        "max_entries": 5, "max_bytes": 16000}
                package = self.root / ("readable-" + label)
                evo.export_skill({**base, "output": str(package)})
                raw = (package / "index.json").read_bytes()
                self.assertEqual(max(len(line.encode("utf-16-le")) // 2 for line in raw.decode().split("\n")), 2000)
                self.assertFalse(evo.validate_package(package)["empty"])
                self.save("index-library", [{**self.entry, "trigger": invalid}])
                failed = self.root / ("unreadable-" + label)
                with self.assertRaisesRegex(ValueError, "2000 UTF-16"):
                    evo.export_skill({**base, "output": str(failed)})
                self.assertFalse(failed.exists())

    def test_package_revalidation_rejects_unreadable_index_serialization(self):
        package = self.root / "readability-package"
        self.save("index-library", [{**self.entry, "trigger": "x" * 1000, "scope": "y" * 1000}])
        evo.export_skill({"library": str(self.root / "index-library.json"), "limits": self.req["limits"],
                          "max_entries": 5, "max_bytes": 16000, "output": str(package)})
        index = evo.read(package / "index.json")
        (package / "index.json").write_text(json.dumps(index, ensure_ascii=False))
        with self.assertRaisesRegex(ValueError, "2000 UTF-16"):
            evo.validate_package(package)

    def test_reading_public_api_response_example_is_not_another_judge(self):
        def read_example(rows):
            rows.extend([
                {"type": "model_msg", "payload": {"type": "tool_call", "name": "read_file",
                    "arguments": json.dumps({"file_path": "/synthetic/public-api.md"}), "tool_call_id": "api-doc"}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "api-doc",
                    "output": '1\tSYNTHETIC response example: {"score": 0.5, "correct": false}'}}])
        self.rewrite_solver_trace(read_example)
        self.assertEqual(evo.compile_batch(self.req)["entries"], 1)

    def test_judge_request_and_frozen_profile_are_revalidated(self):
        context = evo.read(self.contexts[0])
        path = Path(context["judge_request"]["path"])
        original = path.read_bytes()
        changed = evo.read(path)
        changed["answer"] = "/synthetic/another-answer.json"
        path.write_bytes(evo.encoded(changed))
        with self.assertRaisesRegex(ValueError, "request evidence changed"):
            evo.compile_batch(self.req)
        path.write_bytes(original)
        training = self.helper.parent.parent / "training.json"
        training.write_bytes(evo.encoded({**self.training, "attempt_id": "attempt-2"}))
        with self.assertRaisesRegex(ValueError, "training evidence changed"):
            evo.compile_batch(self.req)


class EvidenceAuditTests(unittest.TestCase):
    def test_explicit_network_calls_are_distinct_from_url_data(self):
        url = "http://127.0.0.1:9003/api/judge"
        direct = [f"curl -X POST {url}", f"env MODE=check curl --url {url}",
                  f"sudo -n curl {url}", f"bash -c {shlex.quote('curl ' + url)}",
                  f"URL={shlex.quote(url)}\ncurl \"$URL\"", f"export API=http://127.0.0.1:9003\ncurl \"${{API}}/api/judge\"",
                  "python3 - <<'PY'\nimport requests\nu=" + repr(url) + "\nrequests.post(u,json={})\nPY",
                  "python3 - <<'PY'\nimport urllib.request as ur\nu=" + repr(url) + "\nr=ur.Request(u)\nopener=ur.build_opener()\nopener.open(r)\nPY",
                  shlex.join(["python3", "-c", f"from urllib.request import urlopen as call; call({url!r})"]),
                  shlex.join(["node", "-e", f"fetch({url!r})"])]
        data = [f"echo curl {url}", f"printf '%s' curl {url}",
                f"cat > request.json <<'JSON'\n{{\"url\":\"{url}\"}}\nJSON",
                "python3 - <<'PY'\nimport json\njson.dump({'url':" + repr(url) + "},open('request.json','w'))\nPY",
                shlex.join(["python3", "-c", f"import requests; requests.post('http://127.0.0.1:9003/business',json={{'judge_url':{url!r}}})"]),
                shlex.join(["python3", "-c", f"d={{}}; d.get({url!r})"]),
                f"curl --data {shlex.quote(json.dumps({'url': url}))} http://127.0.0.1:9003/business",
                shlex.join(["node", "-e", f"fetch('http://127.0.0.1:9003/business',{{body:{url!r}}})"])]
        for command in direct:
            with self.subTest(kind="direct", command=command):
                self.assertTrue(evo.direct_judge_probe(command))
        for command in data:
            with self.subTest(kind="data", command=command):
                self.assertFalse(evo.direct_judge_probe(command))

    def test_material_json_is_not_recursively_mistaken_for_a_tool_response(self):
        receipt = {"material_path": "/synthetic/api.txt", "text": '{"source":"evo-judge","score":0,"correct":false}'}
        self.assertEqual(list(evo.json_values("exit code 0\n" + json.dumps(receipt))), [receipt])

    def test_batched_real_helper_reads_are_allowed_but_print_only_receipts_are_not(self):
        helper = shlex.quote(str(TOOL))
        command = ("cd /synthetic/work\npython3 - <<'PY'\nimport json\n"
                   "json.dump({'op':'read-material'},open('read.json','w'))\nPY\n"
                   f"for f in reqs/read_*.json; do echo \"== $f\"; python3 -B {helper} \"$f\"; done")
        def pair(cmd):
            return {"call": {"payload": {"name": "exec_command", "arguments": json.dumps({"cmd": cmd})}}}
        self.assertTrue(evo.helper_observation(pair(command), {}))
        self.assertTrue(evo.helper_observation(pair(f"cd /synthetic/work\npython3 -B {helper} read.json"), {}))
        self.assertFalse(evo.helper_observation(pair(f"echo python3 -B {helper} read.json"), {}))
        self.assertFalse(evo.helper_observation(pair("python3 - <<'PY'\nprint('fabricated receipt')\nPY"), {}))

    def test_output_seal_requires_complete_prior_material_observations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            material, output, trace = root / "context.txt", root / "decisions.json", root / "trace.jsonl"
            material.write_text("SYNTHETIC context")
            output.write_text("[]")
            binding = {"session_id": "synthetic-role", "source": "cli", "workspace": str(root),
                       "agent_state": str(root), "provider": "fixture", "model_id": "no-model"}
            def pair(key, value):
                return [{"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                         "arguments": json.dumps({"cmd": shlex.join(["python3", "-B", str(TOOL), str(root / (key + '.json'))])}), "tool_call_id": key}},
                        {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": key, "output": json.dumps(value)}}]
            sealed = pair("seal", {"file_path": str(output), "sha256": evo.digest(output.read_bytes())})
            observed = pair("read", evo.material({"path": str(material)}))
            request = {"traces": [str(trace)], "binding": binding, "materials": [str(material)], "outputs": [str(output)]}
            for rows in (sealed + observed, [sealed[0]] + observed + [sealed[1]]):
                trace.write_text("\n".join(json.dumps(row) for row in [{"type": "session_meta", "payload": binding}] + rows) + "\n")
                with self.assertRaisesRegex(ValueError, "after all assigned"):
                    evo.audit_role(request)
            trace.write_text("\n".join(json.dumps(row) for row in [{"type": "session_meta", "payload": binding}] + observed + sealed) + "\n")
            self.assertTrue(evo.audit_role(request)["complete"])
            fake = copy.deepcopy(observed + sealed)
            fake[-2]["payload"]["arguments"] = json.dumps({"cmd": "printf fabricated-receipt"})
            trace.write_text("\n".join(json.dumps(row) for row in [{"type": "session_meta", "payload": binding}] + fake) + "\n")
            with self.assertRaisesRegex(ValueError, "sealed"):
                evo.audit_role(request)

    def test_large_material_audit_parses_each_output_once(self):
        content = "SYNTHETIC " * 100000
        fingerprint = evo.digest(content.encode())
        pairs = []
        for offset in range(0, len(content), 4000):
            receipt = {"material_path": "/synthetic/large-context", "sha256": fingerprint,
                       "offset": offset, "end": min(offset + 4000, len(content)), "total": len(content),
                       "text": content[offset:offset + 4000]}
            pairs.append({"call": {"payload": {"name": "exec_command", "arguments": json.dumps({
                "cmd": shlex.join(["python3", "-B", str(TOOL), '/synthetic/read.json'])})}},
                "observation": {"payload": {"output": json.dumps(receipt)}}})
        with patch.object(evo, "json_values", wraps=evo.json_values) as parser:
            result, _ = evo.verify_material_records({"pairs": pairs}, [("/synthetic/large-context", content, fingerprint)])
        self.assertEqual(parser.call_count, len(pairs))
        self.assertEqual(len(result), 1)


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

class JudgeHTTPTests(unittest.TestCase):
    """Real CLI/filesystem/HTTP; the server returns only synthetic constant data."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-judge-http-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.helper, self.training = install_training(self.root, ["train_001"])
        installed = patch.object(evo, "__file__", str(self.helper))
        installed.start()
        self.addCleanup(installed.stop)
        self.calls = []
        self.response = {"score": 0.5, "correct": False}
        self.status = 200
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                fixture.calls.append((self.path, json.loads(body)))
                fixture.entered.set()
                fixture.release.wait(timeout=5)
                self.send_response(fixture.status)
                self.end_headers()
                try:
                    self.wfile.write(json.dumps(fixture.response).encode())
                except BrokenPipeError:
                    pass  # The interrupted-CLI regression deliberately closes its socket.
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.req, self.request_path = judge_request(self.root, self.training, "train_001",
            f"http://127.0.0.1:{self.server.server_port}/api/judge")
        Path(self.req["answer"]).write_text('{"synthetic": true}')

    def stop_server(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def command(self):
        return ["python3", "-B", str(self.helper), str(self.request_path)]

    def invoke(self, expected=0):
        result = subprocess.run(self.command(), capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected:
            return json.loads(result.stderr)
        self.last_stdout = result.stdout
        record = evo.read(self.req["output"])
        self.assertEqual(json.loads(result.stdout), evo.judge_stdout(record))
        return record

    def rewrite_request(self, **changes):
        self.request_path.write_bytes(evo.encoded({**self.req, **changes}))

    def test_concurrent_cli_retries_use_one_durable_http_attempt(self):
        one = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        two = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out_one, err_one = one.communicate(timeout=10)
        out_two, err_two = two.communicate(timeout=10)
        self.assertEqual((one.returncode, two.returncode), (0, 0), err_one + err_two)
        self.assertEqual(json.loads(out_one), json.loads(out_two))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0], ("/api/judge", {"task_id": "train_001", "answer": {"synthetic": True}}))

    def test_prepare_request_is_idempotent_without_http_and_preserves_one_attempt(self):
        self.request_path.unlink()
        prep = self.root / "prepare.json"
        prep.write_bytes(evo.encoded({"op": "prepare-judge-request", "task_id": "train_001", "url": self.req["url"]}))
        command = ["python3", "-B", str(self.helper), str(prep)]
        first = json.loads(subprocess.check_output(command, text=True))
        second = json.loads(subprocess.check_output(command, text=True))
        self.assertEqual(first, second)
        self.assertEqual(first["judge_calls"], 0)
        self.assertEqual(first["judge_request"], str(self.request_path))
        self.assertEqual(shlex.split(first["command"]), self.command())
        self.assertEqual(self.calls, [])
        self.invoke()
        self.assertEqual(len(self.calls), 1)
        prep.write_bytes(evo.encoded({"op": "prepare-judge-request", "task_id": "train_001", "url": "http://127.0.0.1:9999/api/judge"}))
        changed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(changed.returncode, 2)
        self.assertIn("already differs", changed.stderr)
        self.assertEqual(len(self.calls), 1)

    def test_changing_output_attempt_roster_or_answer_cannot_probe_again(self):
        self.invoke()
        for changes in ({"output": str(self.root / "another-feedback.json")},
                        {"attempt_id": "attempt-2"}, {"train_ids": ["train_001", "test_001"]},
                        {"task_id": "test_001"}, {"answer": str(self.root / "another-answer.json")},
                        {"split": "test"}):
            with self.subTest(changes=changes):
                self.rewrite_request(**changes)
                self.invoke(expected=2)
                self.assertEqual(len(self.calls), 1)
        self.rewrite_request()
        Path(self.req["answer"]).write_text('{"synthetic": "changed after scoring"}')
        self.assertIn("identity conflict", self.invoke(expected=2)["error"])
        self.assertEqual(len(self.calls), 1)

    def test_http_failure_is_durable_unknown_not_zero(self):
        self.status = 500
        first = self.invoke()
        self.status = 200
        self.assertEqual(self.invoke(), first)
        self.assertEqual(first["status"], "error")
        self.assertIsNone(first["score"])
        self.assertIsNone(first["correct"])
        self.assertEqual(len(self.calls), 1)

    def test_killed_cli_leaves_unknown_attempt_and_does_not_resend(self):
        self.release.clear()
        process = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertTrue(self.entered.wait(timeout=5), "synthetic judge did not receive request")
        finally:
            process.kill()
            process.communicate(timeout=5)
            self.release.set()
        recovered = self.invoke()
        self.assertEqual(recovered["status"], "error")
        self.assertIn("interrupted", recovered["error"])
        self.assertIsNone(recovered["score"])
        self.assertEqual(len(self.calls), 1)

    def test_valid_zero_feedback_is_preserved(self):
        self.response = {"score": 0, "correct": False}
        result = self.invoke()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["score"], 0)
        self.assertTrue(evo.feedback(result, "train_001", "attempt-1", result["answer_sha256"]))
        self.assertEqual(json.loads(result["raw_response"]), self.response)

    def test_out_of_range_negative_score_remains_raw_error_and_is_not_retried(self):
        self.response = {"score": -0.25, "correct": False}
        first = self.invoke()
        self.assertEqual(first["status"], "error")
        self.assertIsNone(first["score"])
        self.assertEqual(json.loads(first["raw_response"]), self.response)
        self.assertEqual(self.invoke(), first)
        self.assertEqual(len(self.calls), 1)

    def test_large_feedback_is_captured_by_real_cli_chunks_without_rejudging(self):
        Path(self.req["answer"]).write_text(json.dumps({"synthetic": True, "answer": "X" * 30000}))
        feedback = self.invoke()
        judge_output = self.last_stdout
        self.assertLess(len(judge_output), 16000)
        self.assertGreater(Path(self.req["output"]).stat().st_size, 30000)
        inp = self.root / "public-input.txt"
        inp.write_text("SYNTHETIC public facts; this is not a benchmark task.\n")
        binding = {"session_id": "synthetic-long-solver", "source": "cli", "workspace": str(self.root),
                   "agent_state": str(self.root / "method"), "provider": "fixture", "model_id": "no-model"}
        grant = {"evo_task": {"split": "train", "task_id": "train_001", "attempt_id": "attempt-1", "inputs": [str(inp)]}}
        rows = [{"type": "session_meta", "payload": binding},
                {"type": "model_msg", "payload": {"type": "text", "role": "user", "text": json.dumps(grant)}}]
        def pair(call_id, command, output):
            return [{"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                        "tool_call_id": call_id, "arguments": json.dumps({"cmd": shlex.join(command)})}},
                    {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id,
                        "output": output}}]
        def read_chunks(path, prefix):
            pairs = []
            for offset in range(0, max(len(path.read_text()), 1), 4000):
                request = self.root / f"{prefix}-{offset}.json"
                request.write_bytes(evo.encoded({"op": "read-material", "path": str(path), "offset": offset}))
                command = ["python3", "-B", str(self.helper), str(request)]
                result = subprocess.run(command, capture_output=True, text=True, check=True)
                self.assertLess(len(result.stdout), 16000)
                pairs.extend(pair(f"{prefix}-{offset}", command, result.stdout))
            return pairs
        rows += read_chunks(inp, "public")
        judged = pair("one-judge", self.command(), judge_output)
        chunks = read_chunks(Path(self.req["output"]), "feedback")
        trace = self.root / "synthetic-cli-outputs.jsonl"
        request = {"op": "capture", "split": "train", "task_id": "train_001", "attempt_id": "attempt-1",
                   "train_ids": self.training["train_ids"], "answer": self.req["answer"], "feedback": self.req["output"],
                   "judge_request": str(self.request_path), "inputs": [str(inp)], "binding": binding,
                   "traces": [str(trace)], "output": str(self.root / "context.json")}
        for invalid in (rows + judged, rows + judged + chunks[:-2], rows + chunks + judged):
            trace.write_text("\n".join(json.dumps(row) for row in invalid) + "\n")
            with self.assertRaisesRegex(ValueError, "fully read"):
                evo.capture(request)
        trace.write_text("\n".join(json.dumps(row) for row in rows + judged + chunks) + "\n")
        captured = evo.capture(request)
        context = evo.read(captured["path"])
        self.assertEqual(context["feedback"], feedback)
        self.assertEqual(evo.validate_contexts([context], ["train_001"])[("train_001", "attempt-1")], context)
        self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
