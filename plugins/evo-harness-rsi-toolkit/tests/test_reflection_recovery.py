"""R14 phase-aware preparation/recovery; synthetic execution, no paid APIs."""
import json
from pathlib import Path
import shlex
import subprocess
import unittest

import test_solver_reflection as fixtures

evo = fixtures.evo


class ReflectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.SolverReflectionTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def before_judge(self, rows):
        at = next(i for i, row in enumerate(self.f.rows)
                  if row.get("payload", {}).get("tool_call_id") == "judge-once")
        self.f.rows[at:at] = rows

    def move_preparation(self, writer):
        count = len(self.f.rows)
        writer()
        rows = self.f.rows[count:]
        self.f.rows = self.f.rows[:count]
        self.before_judge(rows)

    def capture_valid(self):
        receipt = self.f.completed()
        context = evo.read(receipt["path"])
        evo.validate_contexts([context], [self.f.task])
        return context

    def failed_seal(self, expected):
        try:
            self.f.seal()
        except expected as exc:
            active = self.f.rows[-1]["payload"]
            self.f.rows.append({"type": "model_msg", "payload": {
                "type": "tool_call_output", "tool_call_id": active["tool_call_id"],
                "stop_reason": "completed", "output": json.dumps({"error": str(exc)})}})
            self.f.flush()
            return exc
        self.fail("seal unexpectedly passed")

    def test_prejudge_role_and_input_requests_in_reflection_directory_are_legal(self):
        self.move_preparation(lambda: self.f.read_material(self.f.role, "r14-role"))
        self.move_preparation(lambda: self.f.read_material(self.f.input, "r14-input"))
        self.capture_valid()

    def test_prejudge_prepare_judge_json_is_data_not_postjudge_operation(self):
        request = {"op": "prepare-judge-request", "task_id": self.f.task,
                   "url": "http://127.0.0.1:9005/api/judge"}
        path = self.f.requests / "prepare-judge.json"
        def prepare():
            self.f.write(path, request)
            self.f.tool("exec_command", {"cmd": shlex.join(["python3", "-B", str(self.f.helper), str(path)])},
                        evo.prepare_judge_request(request))
        self.move_preparation(prepare)
        self.capture_valid()

    def test_unexecuted_prepared_request_can_be_corrected_before_judge(self):
        path = self.f.requests / "not-executed.json"
        def prepare():
            self.f.write(path, {"op": "read-material", "path": str(self.f.role), "offset": 0})
            self.f.write(path, {"op": "read-material", "path": str(self.f.input), "offset": 0})
        self.move_preparation(prepare)
        self.capture_valid()

    def test_executed_prepared_request_remains_immutable(self):
        self.move_preparation(lambda: self.f.read_material(self.f.role, "immutable"))
        self.move_preparation(lambda: self.f.write(self.f.requests / "immutable-0.json",
                            {"op": "read-material", "path": str(self.f.input), "offset": 0}))
        self.f.read_feedback(); self.f.candidate_write()
        error = self.failed_seal(evo.ReflectionBoundaryError)
        self.assertEqual(error.phase, "pre_judge")
        self.assertIn("cannot be changed", str(error))

    def test_prepared_role_read_cannot_execute_after_judge(self):
        path = self.f.requests / "role-later.json"
        request = {"op": "read-material", "path": str(self.f.role), "offset": 0}
        self.move_preparation(lambda: self.f.write(path, request))
        self.f.read_feedback()
        call = self.f.tool("exec_command", {"cmd": shlex.join(["python3", "-B", str(self.f.helper), str(path)])},
                           evo.material(request))
        self.f.candidate_write()
        error = self.failed_seal(evo.ReflectionBoundaryError)
        self.assertEqual((error.phase, error.tool_call_id), ("post_judge", call))

    def test_postjudge_unexecuted_request_format_and_op_can_be_corrected(self):
        path = self.f.requests / "feedback-0.json"
        self.f.write(path, '{"op":', raw=True)
        self.f.write(path, {"op": "read-material", "path": str(self.f.feedback_path), "offset": -1})
        self.f.write(path, {"op": "prepare-judge-request", "task_id": self.f.task})
        # read_feedback rewrites the still-unexecuted file with the exact
        # authorized request, then performs the first actual helper operation.
        self.capture_valid()

    def test_postjudge_wrong_op_actual_execution_is_still_rejected(self):
        path = self.f.requests / "wrong-op.json"
        self.f.write(path, {"op": "prepare-judge-request", "task_id": self.f.task})
        call = self.f.tool("exec_command", {"cmd": shlex.join(["python3", "-B", str(self.f.helper), str(path)])},
                           {"error": "SYNTHETIC forbidden operation"})
        error = self.failed_seal(evo.ReflectionBoundaryError)
        self.assertEqual((error.phase, error.tool_call_id), ("post_judge", call))

    def test_first_postjudge_boundary_error_survives_retry_and_missing_feedback(self):
        call = self.f.tool("exec_command", {"cmd": "sha256sum " + str(self.f.input)}, "SYNTHETIC")
        self.f.tool("exec_command", {"cmd": "grep -n validate_candidates " + str(self.f.helper)}, "SYNTHETIC")
        self.f.candidate_write()
        first = self.failed_seal(evo.ReflectionBoundaryError)
        second = self.failed_seal(evo.ReflectionBoundaryError)
        self.assertEqual((first.phase, first.tool_call_id), ("post_judge", call))
        self.assertEqual(str(first), str(second))

    def test_postjudge_schema_source_queries_are_not_permitted_recovery(self):
        for name, arguments in (("exec_command", {"cmd": "sed -n '1,20p' " + str(self.f.helper)}),
                                ("read_file", {"file_path": str(self.f.helper)}),
                                ("read_file", {"file_path": str(self.f.trace)})):
            with self.subTest(name=name):
                rows = list(self.f.rows)
                call = self.f.tool(name, arguments, "SYNTHETIC")
                error = self.failed_seal(evo.ReflectionBoundaryError)
                self.assertEqual(error.tool_call_id, call)
                self.f.rows = rows

    def test_missing_feedback_without_candidate_is_recoverable(self):
        self.assertFalse(self.f.seal()["complete"])
        self.capture_valid()

    def test_schema_error_can_repair_only_unsealed_draft(self):
        self.f.read_feedback()
        self.f.candidate_write([{"id": self.f.task + "-attempt-1-c01"}])
        error = self.f.seal()
        self.assertFalse(error["complete"])
        self.assertEqual(error["code"], "candidate_draft_invalid")
        self.f.candidate_write()
        self.assertTrue(self.f.seal()["complete"])
        self.f.finish()
        context = evo.read(self.f.capture()["path"])
        evo.validate_contexts([context], [self.f.task])

    def test_contract_has_exact_public_hashes_and_no_business_contents(self):
        contract = evo.candidate_contract(self.f.task, "attempt-1", [str(self.f.input)])
        self.assertEqual(contract["input_refs"], [{"path": str(self.f.input),
            "sha256": evo.digest(self.f.input.read_bytes()), "bytes": len(self.f.input.read_bytes())}])
        self.assertEqual(contract["candidate_fields"]["task_id"], {"const": self.f.task})
        self.assertNotIn(self.f.input.read_text(), json.dumps(contract))
        example = contract["evidence_examples_by_kind"]["input"]
        self.assertEqual(example["kind"], "input")
        self.assertIn("sha256", example)
        self.assertNotIn("input", example)

    def cli_seal(self):
        request = {"op": "seal-role-output", "role": "reflector", "task_id": self.f.task}
        path = self.f.requests / "seal.json"
        self.f.write(path, request)
        command = ["python3", "-B", str(self.f.helper), str(path)]
        self.f.rows.append({"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
            "tool_call_id": "native-cli-seal", "arguments": json.dumps({"cmd": shlex.join(command)})}})
        self.f.flush()
        result = subprocess.run(command, capture_output=True, text=True)
        return result, json.loads(result.stdout if result.returncode == 0 else result.stderr)

    def test_real_helper_cli_marks_boundary_error_fatal_with_original_call(self):
        call = self.f.tool("exec_command", {"cmd": "grep -n candidate " + str(self.f.helper)}, "SYNTHETIC")
        result, error = self.cli_seal()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(error["code"], "reflection_boundary_violation")
        self.assertIs(error["recoverable"], False)
        self.assertEqual(error["phase"], "post_judge")
        self.assertEqual(error["tool_call_id"], call)
        self.assertIn("Stop this attempt", error["next_action"])

    def test_real_helper_cli_marks_schema_error_only_draft_repair(self):
        self.f.read_feedback(); self.f.candidate_write([{"id": self.f.task + "-attempt-1-c01"}])
        result, error = self.cli_seal()
        self.assertEqual(result.returncode, 0)
        self.assertFalse(error["complete"])
        self.assertFalse(error["sealed"])
        self.assertEqual(error["code"], "candidate_draft_invalid")
        self.assertIs(error["recoverable"], True)
        self.assertIn("unsealed candidate JSON", error["next_action"])

    def test_native_public_input_read_can_reference_prepared_hash_without_hash_query(self):
        contract = evo.candidate_contract(self.f.task, "attempt-1", [str(self.f.input)])
        for index, row in enumerate(self.f.rows):
            p = row.get("payload", {})
            if p.get("type") == "tool_call_output" and str(self.f.input) in p.get("output", ""):
                call = self.f.rows[index - 1]["payload"]
                call.update(name="read_file", arguments=json.dumps({"file_path": str(self.f.input), "limit": 2000}))
                lines = self.f.input.read_text().splitlines()
                p["output"] = "\n".join(f"{n:6}\t{line}" for n, line in enumerate(lines, 1))
        self.f.candidate["evidence"] = [{"kind": "input", "sha256": contract["input_refs"][0]["sha256"],
                                        "claim": "SYNTHETIC documented input fact", "epistemic": "observed"}]
        self.capture_valid()


if __name__ == "__main__":
    unittest.main()
