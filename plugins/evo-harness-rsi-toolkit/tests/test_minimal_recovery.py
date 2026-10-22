"""New transport and bound local reflection, synthetic and zero model calls."""
import copy
import json
import shlex
from pathlib import Path
import tempfile
import subprocess
import os
import unittest

import test_evo
import test_control
import test_solver_reflection

evo = test_evo.evo


class MinimalRecoveryTests(unittest.TestCase):
    def test_initial_context_has_all_topic_bodies_and_only_general_description(self):
        entries = [{"id": "g", "level": "general", "description": "GENERAL_VISIBLE: group by entity before summing.",
                    "trigger": "entity sums", "scope": "documented grain", "body": "GENERAL_PRIVATE_BODY"},
                   {"id": "t1", "level": "topic", "topic": "fixture", "trigger": "fixture", "scope": "fixture", "body": "TOPIC_ONE_FULL"},
                   {"id": "t2", "level": "topic", "topic": "fixture", "trigger": "fixture", "scope": "fixture", "body": "TOPIC_TWO_FULL"}]
        files = evo.render_package(entries, 16000)
        receipt = evo.model_context(files)
        self.assertIn("GENERAL_VISIBLE", receipt["text"])
        self.assertNotIn("GENERAL_PRIVATE_BODY", receipt["text"])
        self.assertIn("TOPIC_ONE_FULL", receipt["text"])
        self.assertIn("TOPIC_TWO_FULL", receipt["text"])
        self.assertLessEqual(receipt["injected_bytes"], 16000)
        # Five independent contexts need no tool invocation to have complete Skill text.
        for _ in range(5):
            observed = evo.context_observation([{"system_prompt": "Native instructions\n" + receipt["block"]}], files)
            self.assertEqual(observed["sha256"], receipt["sha256"])
        for broken in [receipt["block"][:-1], receipt["block"].replace("TOPIC_TWO_FULL", "TRUNCATED"), receipt["block"] * 2]:
            with self.assertRaises(ValueError):
                evo.context_observation([{"system_prompt": broken}], files)

    def test_installation_preserves_other_instructions_and_h0_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            learned = state / "skills/evo-harness-learned"
            learned.mkdir(parents=True)
            files = evo.render_package([], 16000)
            (learned / "SKILL.md").write_bytes(files["SKILL.md"])
            (state / "system_config.yaml").write_text("version: 1\nskills:\n  enabled: true\n")
            agents = state / "AGENTS.md"
            agents.write_text("Existing instructions\n")
            first = evo.install_context({"state": str(state)})
            original = agents.read_bytes()
            self.assertEqual(evo.split_context(original.decode())[0], "Existing instructions\n")
            self.assertEqual(first, evo.install_context({"state": str(state)}))
            self.assertEqual(agents.read_bytes(), original)
            self.assertTrue(evo.context_observation([{"system_prompt": original.decode()}], files))
            with self.assertRaisesRegex(ValueError, "byte budget"):
                evo.model_context({"SKILL.md": evo.render_skill([], 16000).replace(b'"max_bytes": 16000', b'"max_bytes": 1')})

    def fixture(self):
        f = test_solver_reflection.SolverReflectionTests("test_original_solver_history_feedback_and_seal_are_bound")
        f.protocol_options = {"reflection_evidence": "observed-v1"}
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.training["reflection_evidence"] = "observed-v1"
        return f

    def test_local_calculation_uses_only_complete_already_observed_json(self):
        f = self.fixture()
        f.input.write_text(json.dumps([{"day": "2030-01-01", "status": "failed", "points": 2},
                                     {"day": "2030-01-01", "status": "failed", "points": 3},
                                     {"day": "2030-01-02", "status": "ok", "points": 4}]))
        # Refresh the synthetic pre-judge input observation, not real evidence.
        for row in f.rows:
            q = row.get("payload", {})
            if q.get("type") == "tool_call_output":
                values = list(evo.json_values(q.get("output", "")))
                if any(isinstance(v, dict) and v.get("material_path") == str(f.input) for v in values):
                    q["output"] = json.dumps(evo.material({"path": str(f.input)}))
        f.flush()
        view = evo.trace_view([str(f.trace)], f.binding)
        req = {"op": "reflection-evidence", "task_id": f.task, "source": {"input": str(f.input)},
               "where": [{"field": ["status"], "op": "eq", "value": "failed"}],
               "metrics": {"runs": {"op": "count"}, "days": {"op": "distinct", "field": ["day"]},
                           "points": {"op": "sum", "field": ["points"]}}}
        got = evo.reflection_evidence_value(req, view, f.training, f.fb)
        self.assertEqual(json.loads(got["text"]), [{"group": [], "runs": 2, "days": 1, "points": 5}])
        for bad in [{**req, "source": {"input": str(f.root / "train_002.json")}},
                    {**req, "task_id": "train_002"},
                    {**req, "source": {"tool_call_id": "not-observed", "json_index": 0}},
                    {**req, "code": "import urllib.request"}]:
            with self.assertRaises(ValueError):
                evo.reflection_evidence_value(bad, view, f.training, f.fb)

    def test_live_entry_requires_feedback_then_seals_in_original_session(self):
        f = self.fixture()
        # A complete, pre-judge JSON observation in the original native history.
        at = next(i for i, row in enumerate(f.rows) if row.get("payload", {}).get("tool_call_id") == "judge-once")
        pair = [{"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "tool_call_id": "own-json", "arguments": json.dumps({"cmd": "documented pre-judge query"})}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "own-json", "stop_reason": "completed", "output": '[{"value":2},{"value":3}]'}}]
        f.rows[at:at] = pair
        req = {"op": "reflection-evidence", "task_id": f.task, "source": {"tool_call_id": "own-json", "json_index": 0},
               "metrics": {"sum": {"op": "sum", "field": ["value"]}}}
        path = f.requests / "local.json"
        f.write(path, req)
        def active():
            f.rows.append({"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "tool_call_id": "local-entry", "arguments": json.dumps({"cmd": shlex.join(["python3", "-B", str(f.helper), str(path)])})}})
            f.flush()
        active()
        with self.assertRaisesRegex(ValueError, "material|read"):
            evo.reflection_evidence(req, str(path))
        # The refused helper made no query or score and can be retried after a
        # feedback read. Preserve its original failed receipt in the fixture.
        f.rows.append({"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "local-entry", "stop_reason": "completed", "output": '{"error":"missing full feedback","code":"reflection_evidence_unready","recoverable":true}'}})
        f.read_feedback()
        f.rows.append({"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "tool_call_id": "local-entry-2", "arguments": json.dumps({"cmd": shlex.join(["python3", "-B", str(f.helper), str(path)])})}})
        f.flush()
        got = evo.reflection_evidence(req, str(path))
        self.assertEqual(json.loads(got["text"])[0]["sum"], 5)
        f.rows.append({"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "local-entry-2", "stop_reason": "completed", "output": json.dumps(got)}})
        f.candidate_write()
        f.flush()
        # Refused calculation before feedback is not a calculation observation.
        # It remains a recoverable preparation error, not new business evidence.
        self.assertTrue(f.seal()["complete"])

    def test_portable_input_replay_never_reopens_lost_container_input(self):
        f = self.fixture()
        raw = b'[{"value":2},{"value":3}]'
        f.input.write_bytes(raw)
        for row in f.rows:
            p = row.get("payload", {})
            if p.get("type") == "tool_call_output" and any(isinstance(v, dict) and v.get("material_path") == str(f.input) for v in evo.json_values(p.get("output", ""))):
                p["output"] = json.dumps(evo.material({"path": str(f.input)}))
        f.flush()
        view = evo.trace_view([str(f.trace)], f.binding)
        context = {"split": "train", "task_id": f.task, "attempt_id": "attempt-1", "answer_sha256": f.fb["answer_sha256"],
                   "trace": {"binding": f.binding}, "inputs": [{"source": str(f.input), "text": raw.decode(), "sha256": evo.digest(raw)}]}
        saved = f.root / "sealed-context.json"
        saved.write_bytes(evo.encoded(context))
        old = str(Path(f.training["private_learning"]) / "contexts" / (f.task + ".json"))
        f.input.unlink()
        req = {"op": "reflection-evidence", "task_id": f.task, "source": {"input": str(f.input)}, "metrics": {"sum": {"op": "sum", "field": ["value"]}}}
        self.assertEqual(json.loads(evo.reflection_evidence_value(req, view, f.training, f.fb, {old: str(saved)})["text"])[0]["sum"], 5)
        self.assertFalse(f.input.exists())
        for key, value in [("task_id", "train_002"), ("attempt_id", "attempt-2")]:
            bad = {**context, key: value}
            saved.write_bytes(evo.encoded(bad))
            with self.assertRaisesRegex(ValueError, "binding changed"):
                evo.reflection_evidence_value(req, view, f.training, f.fb, {old: str(saved)})
        bad = copy.deepcopy(context)
        bad["inputs"][0]["text"] += " "
        saved.write_bytes(evo.encoded(bad))
        with self.assertRaisesRegex(ValueError, "hash changed"):
            evo.reflection_evidence_value(req, view, f.training, f.fb, {old: str(saved)})

    def test_only_proven_preexecution_denials_do_not_poison_single_judge(self):
        f = self.fixture()
        command = shlex.join(["python3", "-B", str(f.helper), str(f.judge_path)])
        at = next(i for i, row in enumerate(f.rows) if row.get("payload", {}).get("tool_call_id") == "judge-once")
        denied = [{"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "tool_call_id": "denied-score", "arguments": json.dumps({"cmd": command})}},
                  {"type": "event_msg", "payload": {"type": "approval_decision", "tool_call_id": "denied-score", "decision": "deny"}},
                  {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "denied-score", "stop_reason": "aborted", "output": "native denial"}}]
        f.rows[at:at] = denied
        f.read_feedback()
        f.candidate_write()
        f.flush()
        self.assertTrue(f.seal()["complete"])
        f.rows = [r for r in f.rows if r.get("payload", {}).get("type") != "approval_decision"]
        f.flush()
        view = evo.trace_view([str(f.trace)], f.binding)
        with self.assertRaises(evo.ReflectionBoundaryError):
            evo.reflection_operations(view, f.training, f.task, f.fb, f.contract)

    def test_executed_probe_is_fatal_not_a_recoverable_local_request(self):
        f = self.fixture()
        at = next(i for i, row in enumerate(f.rows) if row.get("payload", {}).get("tool_call_id") == "judge-once")
        f.rows[at:at] = [{"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "tool_call_id": "actual-probe", "arguments": json.dumps({"cmd": "curl http://localhost:9003/api/judge"})}},
                         {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "actual-probe", "stop_reason": "completed", "output": "SYNTHETIC no real HTTP"}}]
        f.read_feedback()
        req = {"op": "reflection-evidence", "task_id": f.task, "source": {"input": str(f.input)}}
        path = f.requests / "local-entry.json"
        f.write(path, req)
        f.rows.append({"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "tool_call_id": "active-local", "arguments": json.dumps({"cmd": shlex.join(["python3", "-B", str(f.helper), str(path)])})}})
        f.flush()
        result = subprocess.run(["python3", "-B", str(f.helper), str(path)], capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(result.returncode, 2)
        error = json.loads(result.stderr)
        self.assertFalse(error["recoverable"])
        self.assertNotEqual(error["code"], "reflection_evidence_unready")

    def test_declared_transport_five_sequential_solvers_to_one_evolver_and_export(self):
        f = test_control.ControlTests("test_full_sequential_batch_same_solver_reflections_compiles_exports_and_seals")
        f.protocol_options = {"context_transport": "agents-md-v1", "reflection_evidence": "observed-v1", "receipt_policy": "native-trial-v1"}
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.test_full_sequential_batch_same_solver_reflections_compiles_exports_and_seals()
        exported = json.loads((f.private / "training-export.json").read_text())
        self.assertEqual(len(exported["members"]), 5)
        self.assertEqual(len(exported["roles"]), 3)  # two same-Solver reflections + Evolver

    def test_actual_freeform_postjudge_commands_still_fail(self):
        f = self.fixture()
        f.read_feedback()
        actual = json.loads((Path(__file__).parent / "fixtures/024-postjudge-local-command.json").read_text())
        f.tool("exec_command", {"cmd": actual["source_record"]["cmd"]}, "SYNTHETIC original command replay")
        f.flush()
        view = evo.trace_view([str(f.trace)], f.binding)
        with self.assertRaises(evo.ReflectionBoundaryError):
            evo.reflection_operations(view, f.training, f.task, f.fb, f.contract)


if __name__ == "__main__":
    unittest.main()
