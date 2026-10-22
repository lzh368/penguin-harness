"""Pre-HTTP native observation checks and real CLI draft recovery; no paid calls."""
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest
from unittest.mock import patch

import test_solver_reflection as fixtures

evo = fixtures.evo


class JudgeReadinessTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SolverReflectionTests()
        self.f.defer_judge = True
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def remove_read(self, path):
        ids = {r["payload"]["tool_call_id"] for r in self.f.rows
               if r.get("payload", {}).get("type") == "tool_call_output"
               and any(isinstance(v, dict) and v.get("material_path") == str(path)
                       for v in evo.json_values(r["payload"].get("output", "")))}
        self.f.rows = [r for r in self.f.rows if r.get("payload", {}).get("tool_call_id") not in ids]

    def attempt(self):
        f = self.f
        f.counter += 1
        call_id = "readiness-attempt-" + str(f.counter)
        f.rows.append({"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
            "tool_call_id": call_id, "arguments": json.dumps({"cmd": shlex.join(["python3", "-B", str(f.helper), str(f.judge_path)])})}})
        f.flush()
        value = evo.judge(f.judge_req, str(f.judge_path))
        f.rows.append({"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id,
            "stop_reason": "completed", "output": json.dumps(evo.judge_stdout(value))}})
        f.flush()
        if value.get("source") == "evo-judge":
            f.fb = value
            f.candidate["evidence"][0]["tool_call_id"] = call_id
        return value

    def follow_plan(self, result):
        for item in result["read_plan"]:
            request = item["request"]
            self.f.write(Path(item["request_path"]), request)
            self.f.tool("exec_command", {"cmd": item["command"]}, evo.material(request))
        self.f.flush()

    def mock_judge(self):
        mock = patch.object(evo.urllib.request, "build_opener")
        opener = mock.start()
        self.addCleanup(mock.stop)
        response = opener.return_value.open.return_value.__enter__.return_value
        response.status, response.read.return_value = 200, b'{"score": 0.5, "correct": false}'
        return opener

    def test_missing_real_instruction_blocks_http_then_exact_plan_allows_one_score(self):
        # Mirrors 014: grant points at instruction.md; mistaken /input/instruction.md
        # was fatal. Missing material is repaired before scoring, not after it.
        self.remove_read(self.f.input)
        self.f.tool("read_file", {"file_path": str(self.f.root / "input/instruction.md")}, "File does not exist")
        self.f.rows[-1]["payload"]["stop_reason"] = "fatal"
        opener = self.mock_judge()
        answer_before = self.f.answer.read_bytes()
        result = self.attempt()
        self.assertEqual((result["ready"], result["judge_calls"]), (False, 0))
        self.assertEqual(result["missing_reads"], [{"path": str(self.f.input), "offset": 0}])
        self.assertFalse(self.f.feedback_path.exists())
        opener.assert_not_called()
        self.follow_plan(result)
        self.assertEqual(self.attempt()["status"], "ok")
        self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertEqual(self.f.answer.read_bytes(), answer_before)
        context = evo.read(self.f.completed()["path"])
        evo.validate_contexts([context], [self.f.task])

    def test_role_missing_returns_complete_exact_chunk_plan_without_scoring(self):
        self.remove_read(self.f.role)
        self.f.role.write_text("role paragraph\n" * 800)
        opener = self.mock_judge()
        result = self.attempt()
        self.assertEqual([r["offset"] for r in result["missing_reads"]], [0, 4000, 8000])
        self.assertTrue(all(r["path"] == str(self.f.role) for r in result["missing_reads"]))
        opener.assert_not_called()
        self.follow_plan(result)
        self.assertEqual(self.attempt()["status"], "ok")
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_foreign_native_session_cannot_consume_judge(self):
        opener = self.mock_judge()
        with patch.dict(os.environ, PENGUIN_SESSION_ID="foreign-session"):
            with self.assertRaisesRegex(ValueError, "no original trace"):
                self.attempt()
        opener.assert_not_called()
        self.assertFalse(self.f.feedback_path.exists())

    def test_cross_task_claim_and_grant_cannot_consume_judge(self):
        opener = self.mock_judge()
        ticket = self.f.private / "dispatch/ticket.json"
        value = evo.read(ticket); value["task_id"] = "train_002"
        ticket.write_bytes(evo.encoded(value))
        with self.assertRaisesRegex(ValueError, "registered native role dispatch"):
            self.attempt()
        opener.assert_not_called()
        self.assertFalse(self.f.feedback_path.exists())

    def test_existing_receipt_recovery_does_not_make_another_http_call(self):
        opener = self.mock_judge()
        first = self.attempt()
        with patch.object(evo, "judge_readiness", side_effect=AssertionError("existing receipt is read-only")):
            recovered = evo.judge(self.f.judge_req, str(self.f.judge_path))
        self.assertEqual(first, recovered)
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_forged_unready_receipt_cannot_hide_a_second_judge(self):
        self.remove_read(self.f.input)
        self.mock_judge()
        result = self.attempt()
        self.follow_plan(result)
        self.attempt()
        for row in self.f.rows:
            payload = row.get("payload", {})
            if payload.get("type") == "tool_call_output" and '"op": "judge-readiness"' in payload.get("output", ""):
                forged = json.loads(payload["output"]); forged["judge_calls"] = 1
                payload["output"] = json.dumps(forged)
        self.f.read_feedback(); self.f.candidate_write()
        with self.assertRaisesRegex(ValueError, "readiness receipt differs"):
            self.f.seal()

    def test_public_material_repair_after_actual_score_remains_forbidden(self):
        self.mock_judge(); self.attempt()
        call = self.f.tool("read_file", {"file_path": str(self.f.input)}, self.f.input.read_text())
        self.f.read_feedback(); self.f.candidate_write()
        with self.assertRaises(evo.ReflectionBoundaryError) as caught:
            self.f.seal()
        self.assertEqual(caught.exception.tool_call_id, call)


class NativeDraftRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SolverReflectionTests(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.read_feedback()

    def cli_seal(self):
        f = self.f
        path = f.requests / "seal.json"
        f.write(path, {"op": "seal-role-output", "role": "reflector", "task_id": f.task})
        f.counter += 1
        call_id = "cli-seal-" + str(f.counter)
        argv = ["python3", "-B", str(f.helper), str(path)]
        f.rows.append({"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
            "tool_call_id": call_id, "arguments": json.dumps({"cmd": shlex.join(argv)})}})
        f.flush()
        proc = subprocess.run(argv, capture_output=True, text=True)
        # Native exec derives completion from the real process exit; do not
        # fabricate completed for a process that actually exited 2 (R14 defect).
        f.rows.append({"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id,
            "stop_reason": "completed" if proc.returncode == 0 else "fatal",
            "output": proc.stdout + proc.stderr}})
        f.flush()
        return proc, json.loads(proc.stdout if proc.returncode == 0 else proc.stderr)

    def test_real_cli_missing_top_level_hash_repair_seals_same_solver(self):
        draft = copy.deepcopy(self.f.candidate)
        draft["evidence"] = [{"kind": "input", "input": {"sha256": evo.digest(self.f.input.read_bytes())},
                              "claim": "SYNTHETIC documented field", "epistemic": "observed"}]
        self.f.candidate_write([draft])
        proc, bad = self.cli_seal()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual((bad["complete"], bad["sealed"], bad["recoverable"]), (False, False, True))
        self.assertEqual(bad["code"], "candidate_draft_invalid")
        draft["evidence"][0]["sha256"] = draft["evidence"][0].pop("input")["sha256"]
        self.f.candidate_write([draft])
        proc, good = self.cli_seal()
        self.assertEqual(proc.returncode, 0)
        self.assertTrue(good["complete"])
        self.f.finish()
        context = evo.read(self.f.capture()["path"])
        evo.validate_contexts([context], [self.f.task])

    def test_original_fatal_recoverable_error_stays_failed_no_broad_fatal_exception(self):
        self.f.candidate_write()
        path = self.f.requests / "seal.json"
        self.f.write(path, {"op": "seal-role-output", "role": "reflector", "task_id": self.f.task})
        call = self.f.tool("exec_command", {"cmd": shlex.join(["python3", "-B", str(self.f.helper), str(path)])},
            {"error": "candidate evidence is missing required field: 'sha256'", "code": "candidate_draft_invalid",
             "recoverable": True, "phase": "post_judge"})
        self.f.rows[-1]["payload"]["stop_reason"] = "fatal"
        self.f.candidate_write()
        with self.assertRaises(evo.ReflectionBoundaryError) as caught:
            self.f.seal()
        self.assertEqual(caught.exception.tool_call_id, call)
        self.assertIn("did not complete", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
