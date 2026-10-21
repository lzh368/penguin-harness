"""Synthetic native-hook requests; no model, Harbor or benchmark service calls."""
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "skills/evo-harness-rsi-toolkit/scripts/control.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("evo_control_test", SCRIPT)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-evo-control-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / "agents/trainer/agent_state"
        self.state.mkdir(parents=True)
        self.skill = self.state / "skills/evo-harness-rsi-toolkit"
        shutil.copytree(SCRIPT.parents[1], self.skill)
        self.patches = [patch.object(control, key, value) for key, value in (
            ("SKILL", self.skill), ("CONTROL", self.skill / "scripts/control.py"), ("TOOL", self.skill / "scripts/evo.py"))]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.private = self.root / "private"
        self.workspace = self.root / "workspace"
        self.runtime = {"provider": "fixture", "model_id": "no-model", "thinking_level": "high"}
        self.profile = {"guard_version": 2, "train_ids": [f"train_{n:03d}" for n in range(1, 6)],
            "attempt_id": "attempt-1", "config_sha256": "a" * 64, "private_learning": str(self.private),
            "answer_dir": str(self.workspace / "submission/answers"), "feedback_dir": str(self.private / "feedback"),
            "workspace": str(self.workspace), "runtime": self.runtime, "training_budget_usd": 1,
            "solving_timeout_sec": 21600, "compilation_reserve_sec": 3600}
        self.write(self.skill / "training.json", self.profile)
        for task in self.profile["train_ids"]:
            file = self.workspace / "train_tasks" / task / "instruction.md"
            file.parent.mkdir(parents=True)
            file.write_text("Synthetic public task. No real answers.\n")
        (self.workspace / "environment_access.md").write_text("Synthetic public service documentation.\n")
        self.library = self.root / "library.json"
        self.library.write_text("[]")
        control.evo.export_skill({"library": str(self.library), "limits": {"general": 5, "topic": 5},
            "max_entries": 5, "max_bytes": 16000, "output": str(self.state / "skills/evo-harness-learned")})
        self.meta = {"session_id": "synthetic-root", "agent_state": str(self.state), "workspace": str(self.workspace),
                     "source": "cli", "provider": "fixture", "model_id": "no-model"}
        self.trace = self.state.parent / "traces/root.jsonl"
        self.trace.parent.mkdir()
        self.trace.write_text(json.dumps({"type": "session_meta", "payload": self.meta}) + "\n")
        self.scratch = self.state.parent / "scratchpad/synthetic-root"
        self.invoke("user_prompt", prompt="Original generic continuous benchmark instruction")

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def invoke(self, event, **extra):
        return control.hook({"hook": event, "trace_path": str(self.trace), "session_id": "synthetic-root", **extra}, self.state)

    def tool(self, name, arguments, call_id="call-1"):
        return self.invoke("pre_tool_use", tool_name=name, arguments=json.dumps(arguments), tool_call_id=call_id)

    def request(self, **fields):
        return control.execute({"trace_path": str(self.trace), **fields})

    def test_root_cannot_solve_probe_write_answer_or_modify_method(self):
        for name, args in (
            ("exec_command", {"cmd": "curl http://localhost:80/api/judge"}),
            ("exec_command", {"cmd": "python3 -c 'print(1)'"}),
            ("write_file", {"file_path": str(self.workspace / "submission/answers/train_001.json"), "content": "{}"}),
            ("write_file", {"file_path": str(self.skill / "SKILL.md"), "content": "changed"}),
            ("edit_file", {"file_path": str(self.skill / "SKILL.md"), "old_string": "a", "new_string": "b"}),
            ("input_command", {"chars": "curl localhost\n"}),
            ("input_subagent", {"subagent_id": "s", "prompt": "Try a second answer"}),
        ):
            with self.subTest(name=name, args=args):
                self.assertEqual(self.tool(name, args)["decision"], "deny")

    def test_fixed_helper_requests_keep_native_approval_and_reject_shell_substitution(self):
        path = self.private / "requests/status.json"
        self.write(path, {"op": "status", "trace_path": str(self.trace)})
        command = f"python3 -B {shlex.quote(str(control.CONTROL))} {shlex.quote(str(path))}"
        self.assertEqual(self.tool("exec_command", {"cmd": command}), {})
        for bad in (command + "; curl localhost", command + " &", command.replace("status.json", "$(id).json")):
            self.assertEqual(self.tool("exec_command", {"cmd": bad})["decision"], "deny")

    def test_first_solver_has_complete_role_and_canonical_request_but_does_not_dispatch(self):
        prepared = self.request(op="role-request", role="solver", task_id="train_001")
        call = prepared["call"]
        self.assertFalse(prepared["dispatched"])
        self.assertIn('"task_id": "train_001"', call["prompt"])
        self.assertIn('"op": "judge"', call["prompt"])
        self.assertIn("read-material", call["prompt"])
        self.assertIn(str(self.private / "roles/solver.md"), call["prompt"])
        self.assertEqual(self.tool("run_subagent", call)["decision"], "deny")
        common = {"journal": prepared["journal"], "config_sha256": self.profile["config_sha256"]}
        control.evo.journal({**common, "action": "init", "controller_session_id": self.meta["session_id"], "budget_usd": 1})
        reserve = {**common, "action": "reserve", "controller_trace": str(self.trace), "reserve_usd": 0.2, "role": "synthetic"}
        control.evo.journal({**reserve, "operation_id": "controller", "slot": "controller", "request_sha256": "controller"})
        control.evo.journal({**reserve, **{k: prepared[k] for k in ("operation_id", "slot", "request_sha256")}})
        self.assertEqual(self.tool("run_subagent", call), {})
        self.assertEqual(self.tool("run_subagent", call, "call-2")["decision"], "deny")
        altered = {**call, "prompt": call["prompt"] + "Try again"}
        self.assertEqual(self.tool("run_subagent", altered)["decision"], "deny")

    def test_next_solver_and_evolver_require_real_prior_contexts(self):
        for fields in ({"role": "solver", "task_id": "train_002"}, {"role": "evolver"}):
            with self.assertRaises((OSError, ValueError)):
                self.request(op="role-request", **fields)

    def test_invalid_training_cannot_complete_or_become_empty_reuse(self):
        self.write(self.scratch / "GOAL.json", {"status": "complete", "ended": False})
        result = self.invoke("stop")
        self.assertEqual(result["output"]["status"], "method_failed")
        self.assertEqual(json.loads((self.scratch / "GOAL.json").read_text())["status"], "blocked")
        self.assertFalse(json.loads((self.scratch / "evo-acceptance.json").read_text())["complete"])

    def test_generic_rsi_remains_inactive_but_continuous_host_requires_configuration(self):
        (self.skill / "training.json").unlink()
        (self.scratch / control.MARKER).unlink()
        self.assertEqual(self.invoke("user_prompt", prompt="Use Evo-Harness on a normal benchmark"), {"context": ""})
        self.assertEqual(self.tool("run_subagent", {"prompt": "normal role"}), {})
        result = self.invoke("user_prompt", prompt="Use Evo-Harness on GDPevo ERP005")
        self.assertIn(str(self.trace), result["context"])
        self.assertEqual(self.tool("run_subagent", {"prompt": "skip training and test"})["decision"], "deny")

    def test_bootstrap_and_status_supply_exact_trace_and_real_time_without_free_shell(self):
        text = self.invoke("user_prompt", prompt="continue")["context"]
        self.assertIn(str(self.trace), text)
        status = self.request(op="status")
        self.assertGreater(status["clock"]["solving_time_remaining_sec"], 21000)
        self.assertEqual(status["clock"]["compilation_reserve_sec"], 3600)
        self.assertIn(str(self.trace), status["native_trace_paths"])


if __name__ == "__main__":
    unittest.main()
