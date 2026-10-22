"""Original native Solver history/seal regression; entirely synthetic, no model calls."""
import copy
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

import test_evo as fixtures

evo, TOOL = fixtures.evo, fixtures.TOOL


class SolverReflectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-solver-reflection-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "native/agents/trainer/agent_state"
        self.helper = self.state / "skills/evo-harness-rsi-toolkit/scripts/evo.py"
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(TOOL.read_bytes())
        self.helper.with_name("inspection.py").write_bytes(TOOL.with_name("inspection.py").read_bytes())
        self.private = self.root / "private"
        self.private.mkdir()
        (self.root / "answers").mkdir()
        self.task, self.session, self.parent_id = "train_001", "solver-1", "training-parent"
        self.training = {"train_ids": [self.task], "attempt_id": "attempt-1", "config_sha256": "a" * 64,
            "private_learning": str(self.private), "answer_dir": str(self.root / "answers"),
            "feedback_dir": str(self.private / "feedback"), "guard_version": 2,
            "reflection_mode": "solver-session", "max_bytes": 16000, "limits": {"general": 5, "topic": 5},
            "runtime": {"provider": "fixture", "model_id": "no-model", "thinking_level": "xhigh"}}
        (self.helper.parent.parent / "training.json").write_bytes(evo.encoded(self.training))
        self.contract = evo.role_contract(self.training, "reflector", self.task)
        self.role = Path(self.contract["materials"][0]); self.role.parent.mkdir(parents=True)
        self.role.write_text("SYNTHETIC original Solver solves, reads full feedback, then reflects in its own history.\n")
        self.output = Path(self.contract["output"]); self.output.parent.mkdir(parents=True)
        self.requests = Path(self.contract["request_dir"]); self.requests.mkdir()
        self.input = self.root / "input.txt"; self.input.write_text("SYNTHETIC current ledger: sum rows with documented active status.\n")
        self.answer = self.root / "answers" / (self.task + ".json"); self.answer.write_text('{"synthetic": true}')
        self.traces = self.state.parent / "traces"; self.traces.mkdir()
        self.trace = self.traces / (self.session + "_001.jsonl")
        self.parent = self.traces / (self.parent_id + "_001.jsonl")
        self.binding = {"session_id": self.session, "source": "subagent", "provider": "fixture", "model_id": "no-model",
                        "agent_state": str(self.state), "workspace": str(self.root), "parent_trace": str(self.parent)}
        self.prompt = json.dumps({"evo_task": {"split": "train", "task_id": self.task, "attempt_id": "attempt-1", "inputs": [str(self.input)]}}) + "\n" + json.dumps({"evo_role_seal": {**self.contract, "parent_session_id": self.parent_id}})
        self.call_args = {"prompt": self.prompt, "description": "SYNTHETIC Solver", "provider": "fixture", "model_id": "no-model", "thinking_level": "xhigh", "run_in_background": True}
        ticket = {"session_id": self.parent_id, "phase": "solver", "task_id": self.task,
                  "slot": "solver/" + self.task + "/attempt-1", "call": self.call_args}
        dispatch = self.private / "dispatch"; dispatch.mkdir()
        (dispatch / "ticket.json").write_bytes(evo.encoded(ticket))
        (dispatch / (evo.digest(ticket["slot"].encode()) + ".claimed.json")).write_bytes(evo.encoded({
            "tool_call_id": "parent-dispatch", "request_sha256": evo.digest(json.dumps(self.call_args, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())}))
        self.parent_rows = [{"type": "session_meta", "payload": {**self.binding, "session_id": self.parent_id, "source": "cli"}},
            {"type": "model_msg", "payload": {"type": "tool_call", "name": "run_subagent", "tool_call_id": "parent-dispatch", "arguments": json.dumps(self.call_args)}},
            {"type": "event_msg", "payload": {"type": "subagent", "session_id": self.session}},
            {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "parent-dispatch", "output": "started subagent_id synthetic-handle;"}}]
        self.rows = [{"type": "session_meta", "payload": self.binding}, {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": self.prompt}}]
        self.rows.extend(fixtures.h0_read_rows(self.binding))
        self.counter = 0
        self.extra_traces = []
        installed = patch.object(evo, "__file__", str(self.helper)); installed.start(); self.addCleanup(installed.stop)
        session_env = patch.dict(os.environ, PENGUIN_SESSION_ID=self.session); session_env.start(); self.addCleanup(session_env.stop)
        self.read_material(self.role, "role", prepare=False)
        self.read_material(self.input, "input", prepare=False)
        self.judge_req, self.judge_path = fixtures.judge_request(self.root, self.training, self.task)
        self.feedback_path = Path(self.judge_req["output"])
        if not getattr(self, "defer_judge", False):
            self.rows.append({"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                "tool_call_id": "judge-once", "arguments": json.dumps({"cmd": shlex.join(["python3", "-B", str(self.helper), str(self.judge_path)])})}})
            self.flush()
            with patch.object(evo.urllib.request, "build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.status, response.read.return_value = 200, b'{"score": 0.5, "correct": false, "feedback": "SYNTHETIC aggregate score only"}'
                self.fb = evo.judge(self.judge_req)
            self.rows.append({"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "judge-once",
                "stop_reason": "completed", "output": json.dumps(evo.judge_stdout(self.fb))}})
            self.feedback_path = Path(self.fb["feedback_path"])
        self.candidate = {"id": self.task + "-attempt-1-c01", "task_id": self.task, "attempt_id": "attempt-1",
            "lesson": "SYNTHETIC retain documented entity and time boundaries when aggregating.", "trigger": "current ledger aggregation",
            "scope_hint": "the documented ledger model", "evidence": [{"kind": "tool", "tool_call_id": "judge-once", "epistemic": "observed", "claim": "aggregate feedback does not prove a field-specific error"}]}
        self.flush()

    def tool(self, name, args, value, call_id=None):
        self.counter += 1
        call_id = call_id or "tool-" + str(self.counter)
        self.rows.extend([{"type": "model_msg", "payload": {"type": "tool_call", "name": name, "tool_call_id": call_id, "arguments": json.dumps(args)}},
                          {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id, "stop_reason": "completed", "output": value if isinstance(value, str) else json.dumps(value)}}])
        return call_id

    def write(self, path, value, raw=False):
        text = value if raw else evo.encoded(value).decode()
        Path(path).write_text(text)
        self.tool("write_file", {"file_path": str(path), "content": text}, "Wrote file")

    def read_material(self, path, label, prepare=True):
        for offset in range(0, max(len(Path(path).read_text()), 1), 4000):
            request = self.requests / f"{label}-{offset}.json"
            value = {"op": "read-material", "path": str(path), "offset": offset}
            if prepare:
                self.write(request, value)
            else:
                request.write_bytes(evo.encoded(value))
            self.tool("exec_command", {"cmd": shlex.join(["python3", "-B", str(self.helper), str(request)])}, evo.material(value))

    def read_feedback(self):
        self.read_material(self.feedback_path, "feedback")

    def candidate_write(self, candidates=None):
        self.write(self.output, [self.candidate] if candidates is None else candidates)

    def flush(self):
        self.trace.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in self.rows))
        self.parent.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in self.parent_rows))

    def seal(self):
        req = {"op": "seal-role-output", "role": "reflector", "task_id": self.task}
        path = self.requests / "seal.json"
        self.write(path, req)
        self.counter += 1; call_id = "seal-" + str(self.counter)
        self.rows.append({"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "tool_call_id": call_id,
            "arguments": json.dumps({"cmd": shlex.join(["python3", "-B", str(self.helper), str(path)])})}})
        self.flush()
        value = evo.seal_role_output(req, str(path))
        self.rows.append({"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id, "stop_reason": "completed", "output": json.dumps(value)}})
        self.flush()
        return value

    def finish(self, status="completed"):
        self.rows.extend([{"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": "SYNTHETIC answer and reflection sealed."}},
                          {"type": "event_msg", "payload": {"type": "request_end", "status": status}}])
        self.parent_rows.append({"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "harness", "text": "[background_task_done]\nkind: subagent\nid: synthetic-handle\nstatus: " + status + "\n[/background_task_done]"}})
        self.flush()

    def capture(self, name="context"):
        self.flush()
        request = {"op": "capture", "split": "train", "task_id": self.task, "attempt_id": "attempt-1", "train_ids": [self.task],
            "answer": str(self.answer), "feedback": str(self.feedback_path), "judge_request": str(self.judge_path),
            "inputs": [str(self.input)], "traces": [str(self.trace), *self.extra_traces], "binding": self.binding, "output": str(self.private / (name + ".json"))}
        return evo.capture(request)

    def completed(self, candidates=None):
        self.read_feedback(); self.candidate_write(candidates); self.seal(); self.finish()
        return self.capture()

    def set_feedback(self, score, correct, detail="SYNTHETIC aggregate feedback"):
        self.fb.update(score=score, correct=correct,
                       raw_response=json.dumps({"score": score, "correct": correct, "feedback": detail}))
        self.feedback_path.write_bytes(evo.encoded(self.fb))
        for row in self.rows:
            payload = row.get("payload", {})
            if payload.get("type") == "tool_call_output" and payload.get("tool_call_id") == "judge-once":
                payload["output"] = json.dumps(evo.judge_stdout(self.fb))

    def test_success_observes_feedback_without_forced_reflection(self):
        self.set_feedback(1, True)
        self.read_feedback(); self.finish()
        context = evo.read(self.capture()["path"])
        self.assertEqual(context["reflection"]["status"], "not-required")
        self.assertNotIn("audit_request", context["reflection"])
        self.assertFalse(self.output.exists())
        evo.validate_contexts([context], [self.task])

    def test_large_full_feedback_must_precede_candidate(self):
        self.set_feedback(0.5, False, "SYNTHETIC authorized feedback " * 900)
        receipt = self.completed()
        context = evo.read(receipt["path"])
        chunks = [p for p in context["trace"]["pairs"] if '"material_path":' in p["observation"]["payload"]["output"]
                  and str(self.feedback_path) in p["observation"]["payload"]["output"]]
        self.assertGreater(len(chunks), 5)
        evo.validate_contexts([context], [self.task])

    def test_trace_split_and_compaction_preserve_original_history(self):
        self.read_feedback(); self.candidate_write(); self.seal(); self.finish()
        boundary = next(i + 1 for i, r in enumerate(self.rows) if r.get("payload", {}).get("type") == "tool_call_output"
                        and r["payload"].get("tool_call_id") == "judge-once")
        second = self.traces / (self.session + "_002.jsonl")
        tail = [{"type": "session_meta", "payload": self.binding},
                {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "harness",
                 "text": "[context_summary]SYNTHETIC native compaction retains the same Solver run.[/context_summary]"}}] + self.rows[boundary:]
        self.rows = self.rows[:boundary] + [{"type": "event_msg", "payload": {"type": "compaction_end", "status": "completed", "mode": "summarize"}}]
        second.write_text(''.join(json.dumps(row) + '\n' for row in tail))
        self.extra_traces = [str(second)]
        context = evo.read(self.capture()["path"])
        self.assertEqual(len(context["trace"]["sources"]), 2)
        evo.validate_contexts([context], [self.task])
        with self.assertRaisesRegex(ValueError, "all original Solver trace segments|material not fully read"):
            self.extra_traces = []
            self.capture("omitted-segment")

    def test_parent_notification_in_later_segment_is_prefix_bound(self):
        self.read_feedback(); self.candidate_write(); self.seal(); self.finish()
        notice = self.parent_rows.pop()
        second = self.traces / (self.parent_id + "_002.jsonl")
        second.write_text(json.dumps(self.parent_rows[0]) + "\n" + json.dumps(notice) + "\n")
        context = evo.read(self.capture()["path"])
        self.assertEqual(len(context["completion"]["sources"]), 2)
        with second.open("a") as stream:
            stream.write(json.dumps({"type": "event_msg", "payload": {"type": "later-task"}}) + "\n")
        evo.validate_contexts([context], [self.task])
        text = second.read_text().replace("synthetic-handle", "foreign-handle")
        second.write_text(text)
        with self.assertRaisesRegex(ValueError, "prefix changed|prefix is incomplete"):
            evo.validate_contexts([context], [self.task])

    def test_original_solver_history_feedback_and_seal_are_bound(self):
        receipt = self.completed()
        context = evo.read(receipt["path"])
        self.assertEqual(context["reflection"]["session_id"], self.session)
        self.assertEqual(context["reflection"]["audit_request"]["binding"], context["trace"]["binding"])
        self.assertEqual(context["completion"]["subagent_id"], "synthetic-handle")
        self.assertTrue(evo.audit_role(context["reflection"]["audit_request"])["complete"])
        evo.validate_contexts([context], [self.task])

    def test_negative_empty_candidates_require_real_original_seal(self):
        receipt = self.completed([])
        self.assertEqual(evo.read(receipt["path"])["reflection"]["status"], "sealed")

    def test_negative_without_reflection_cannot_be_empty_success(self):
        self.read_feedback(); self.finish()
        with self.assertRaisesRegex(ValueError, "explicit candidate"):
            self.capture()

    def test_candidate_preceding_complete_feedback_is_rejected(self):
        self.candidate_write(); self.read_feedback()
        with self.assertRaisesRegex(ValueError, "before candidate"):
            self.seal()

    def test_postjudge_business_answer_edits_or_other_materials_are_rejected(self):
        for name, args in (("exec_command", {"cmd": "curl http://127.0.0.1:9005/api/orders"}),
                           ("write_file", {"file_path": str(self.answer), "content": "{}"}),
                           ("read_file", {"file_path": str(self.private / "roles/train_002/candidates.json")}),
                           ("run_subagent", {"prompt": "new reflection"})):
            with self.subTest(name=name):
                rows = copy.deepcopy(self.rows)
                self.read_feedback(); self.tool(name, args, "forbidden"); self.candidate_write()
                with self.assertRaises(ValueError):
                    self.seal()
                self.rows = rows

    def test_prejudge_explicit_other_task_private_and_test_reads_are_rejected(self):
        original = copy.deepcopy(self.rows)
        paths = [self.private / "roles/train_000/candidates.json",
                 self.private / "prebatch-library.json",
                 self.root / "answers/train_002.json",
                 self.root / "train_tasks/train_002/input.json",
                 self.root / "test_tasks/tg001-test-001/input.json"]
        for foreign in paths:
            with self.subTest(path=str(foreign)):
                self.rows = copy.deepcopy(original)
                judge_rows = self.rows[-2:]; self.rows = self.rows[:-2]
                self.tool("read_file", {"file_path": str(foreign)}, "     1\tSYNTHETIC forbidden material")
                self.rows.extend(judge_rows)
                self.read_feedback(); self.candidate_write()
                with self.assertRaisesRegex(ValueError, "cannot read"):
                    self.seal()

    def test_prejudge_read_material_cannot_load_other_task_candidate(self):
        foreign = self.private / "roles/train_000/candidates.json"
        foreign.parent.mkdir(parents=True); foreign.write_text("[]")
        judge_rows = self.rows[-2:]; self.rows = self.rows[:-2]
        self.read_material(foreign, "foreign", prepare=False)
        self.rows.extend(judge_rows)
        self.read_feedback(); self.candidate_write()
        with self.assertRaisesRegex(ValueError, "cannot read"):
            self.seal()

    def test_postjudge_request_cannot_read_other_task_or_library(self):
        self.read_feedback()
        path = self.requests / "foreign.json"
        self.write(path, {"op": "read-material", "path": str(self.private / "prebatch-library.json")})
        # A request file is only data; the actual forbidden invocation is the
        # boundary violation. Unexecuted request drafts can be corrected.
        self.tool("exec_command", {"cmd": shlex.join(["python3", "-B", str(self.helper), str(path)])},
                  "SYNTHETIC forbidden read attempt")
        self.candidate_write()
        with self.assertRaisesRegex(ValueError, "restricted"):
            self.seal()

    def test_seal_and_capture_do_not_allow_answer_mutation_or_second_judge(self):
        self.read_feedback(); self.candidate_write(); self.seal(); self.finish()
        self.answer.write_text('{"synthetic": false}')
        with self.assertRaisesRegex(ValueError, "another answer"):
            self.capture()
        self.answer.write_text('{"synthetic": true}')
        # Explicit duplicate canonical judge, with a distinct native call id.
        self.tool("exec_command", {"cmd": shlex.join(["python3", "-B", str(self.helper), str(self.judge_path)])}, evo.judge_stdout(self.fb), "judge-again")
        with self.assertRaises(ValueError):
            self.capture()

    def test_parent_steering_and_fresh_reflector_impersonation_rejected(self):
        self.read_feedback(); self.candidate_write()
        self.rows.append({"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": "Produce different candidates."}})
        with self.assertRaisesRegex(ValueError, "without parent steering"):
            self.seal()

    def test_capture_requires_native_completed_notification(self):
        self.read_feedback(); self.candidate_write(); self.seal()
        with self.assertRaisesRegex(ValueError, "terminal"):
            self.capture()
        self.finish(); self.parent_rows.pop(); self.flush()
        with self.assertRaisesRegex(ValueError, "completed native background"):
            self.capture()

    def test_terminal_trace_immutable_parent_prefix_append_allowed(self):
        context = evo.read(self.completed()["path"])
        self.parent_rows.append({"type": "event_msg", "payload": {"type": "fixture-next-task"}}); self.flush()
        evo.validate_contexts([context], [self.task])
        self.rows.append({"type": "event_msg", "payload": {"type": "fixture-after-capture"}}); self.flush()
        with self.assertRaisesRegex(ValueError, "trace changed"):
            evo.validate_contexts([context], [self.task])

    def test_different_parent_or_task_identity_rejected(self):
        self.read_feedback(); self.candidate_write()
        original = self.rows[1]["payload"]["text"]
        self.rows[1]["payload"]["text"] = original.replace('"task_id": "train_001"', '"task_id": "train_002"')
        with self.assertRaises(ValueError):
            self.seal()

    def test_complete_raw_feedback_cannot_be_replaced_by_compact_receipt(self):
        self.candidate_write()
        # A candidate already written before full feedback is an immutable
        # ordering violation, not a missing-read repair opportunity.
        with self.assertRaises(evo.ReflectionBoundaryError) as failure:
            self.seal()
        self.assertEqual(failure.exception.phase, "post_judge")
        self.assertIsNotNone(failure.exception.tool_call_id)

    def test_request_write_cannot_be_replaced_after_execution(self):
        self.read_feedback()
        path = self.requests / "feedback-0.json"
        self.write(path, {"op": "read-material", "path": str(self.feedback_path), "offset": 4000})
        self.candidate_write()
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            self.seal()

    def test_already_sealed_output_cannot_be_revised(self):
        self.read_feedback(); self.candidate_write(); self.seal()
        self.candidate["lesson"] += " silently revised"
        self.candidate_write(); self.finish()
        with self.assertRaisesRegex(ValueError, "differs|after successful"):
            self.capture()

    def test_repeated_reflection_seal_fails_before_returning_another_success(self):
        self.read_feedback(); self.candidate_write(); self.seal()
        with self.assertRaisesRegex(ValueError, "already sealed"):
            self.seal()


if __name__ == "__main__":
    unittest.main()
