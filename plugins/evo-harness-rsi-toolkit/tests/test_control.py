"""Synthetic native-hook requests; no model, Harbor or benchmark service calls."""
import importlib.util
import json
import os
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
        self.runtime = {"provider": "fixture", "model_id": "no-model", "thinking_level": "xhigh",
                        "effective_reasoning_effort": "high", "api_host": "fixture.invalid", "client_type": "fixture-official"}
        self.profile = {"guard_version": 2, "train_ids": [f"train_{n:03d}" for n in range(1, 6)],
            "attempt_id": "attempt-1", "config_sha256": "a" * 64, "private_learning": str(self.private),
            "answer_dir": str(self.workspace / "submission/answers"), "feedback_dir": str(self.private / "feedback"),
            "workspace": str(self.workspace), "runtime": self.runtime, "training_budget_usd": 1,
            "solving_timeout_sec": 21600, "compilation_reserve_sec": 3600,
            "limits": {"general": 5, "topic": 5}, "max_entries": 5, "max_bytes": 16000}
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
        self.trace = self.state.parent / "traces/synthetic-root_001.jsonl"
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

    def test_native_jsonl_preserves_literal_unicode_line_separators(self):
        observation = {"type": "model_msg", "payload": {"type": "tool_call_output",
            "tool_call_id": "native-observation", "output": "first\u0085second\u2028third\u2029last"}}
        with self.trace.open("a") as handle:
            handle.write(json.dumps(observation, ensure_ascii=False) + "\n")
            handle.write('{"unfinished_live_row":')
        meta, scratch, rows = control.trace_context(self.trace, self.state)
        self.assertEqual(meta, self.meta)
        self.assertEqual(scratch, self.scratch)
        self.assertEqual(rows[-1], observation)
        self.assertEqual(len(rows), 2)

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
        self.assertIn('"op": "prepare-judge-request"', call["prompt"])
        self.assertIn("makes zero judge calls", call["prompt"])
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

    def seal_preparation_fixture(self, large=False):
        shutil.copyfile(self.skill / "training.json", self.private / "frozen-training.json")
        audits = []
        for index, role in enumerate(("reflector", "evolver")):
            path = self.private / "requests" / f"audit-{index}.json"
            self.write(path, {"op": "audit-role", "role": role,
                "materials": [f"{self.private}/material-{n}.json" for n in range(2000 if large else 1)]})
            audits.append(str(path))
        return audits

    def test_prepare_seal_copies_frozen_profile_without_large_stdout_or_dispatch(self):
        audits = self.seal_preparation_fixture(large=True)
        before = {str(p): p.read_bytes() for p in (self.trace, *(Path(p) for p in audits))}
        request = {"op": "prepare-seal-training", "trace_path": str(self.trace), "role_requests": audits}
        caller = self.private / "requests/prepare-seal.json"
        self.write(caller, request)
        self.assertEqual(self.tool("exec_command", {"cmd": f"python3 -B {control.CONTROL} {caller}"}), {})
        receipt = control.execute(request)
        prepared = Path(receipt["request_path"])
        self.assertGreater(prepared.stat().st_size, 100000)
        self.assertLess(len(json.dumps(receipt)), 2048)
        self.assertEqual(receipt["model_calls"], 0)
        self.assertEqual(receipt["judge_calls"], 0)
        self.assertFalse(receipt["automatic_dispatch"])
        self.assertEqual(receipt["request_sha256"], control.sha(prepared))
        data = control.evo.read(prepared)
        self.assertEqual(data["runtime"], self.runtime)
        self.assertEqual(data["roles"], [control.evo.read(p) for p in audits])
        self.assertEqual(data["contexts"], [str(self.private / "contexts" / (task + ".json")) for task in self.profile["train_ids"]])
        self.assertEqual(self.tool("exec_command", {"cmd": receipt["command"]}), {})
        stat = prepared.stat()
        self.assertEqual(control.execute(request), receipt)
        self.assertEqual(prepared.stat().st_mtime_ns, stat.st_mtime_ns)
        self.assertEqual({path: Path(path).read_bytes() for path in before}, before)
        self.assertFalse(Path(data["output"]).exists())

    def test_prepare_seal_preserves_conflicts_and_rejects_changed_authority_or_outside_roles(self):
        audits = self.seal_preparation_fixture()
        receipt = self.request(op="prepare-seal-training", role_requests=audits)
        prepared = Path(receipt["request_path"])
        conflict = control.evo.read(prepared)
        conflict["runtime"].pop("client_type")
        self.write(prepared, conflict)
        before = prepared.read_bytes()
        with self.assertRaisesRegex(ValueError, "existing seal request differs"):
            self.request(op="prepare-seal-training", role_requests=audits)
        self.assertEqual(prepared.read_bytes(), before)
        for outside in (self.private / "audit-outside-requests.json", self.private / "requests/link.json"):
            if outside.name == "link.json":
                outside.symlink_to(audits[0])
            else:
                self.write(outside, control.evo.read(audits[0]))
            with self.assertRaises(ValueError):
                self.request(op="prepare-seal-training", role_requests=[str(outside)])
        (self.private / "frozen-training.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            self.request(op="prepare-seal-training", role_requests=audits)
        self.assertEqual(prepared.read_bytes(), before)

    def test_prepare_seal_cannot_reconstruct_a_missing_request_after_publication(self):
        audits = self.seal_preparation_fixture()
        exported = self.private / "training-export.json"
        exported.write_bytes(b"old sealed bytes\n")
        with self.assertRaisesRegex(ValueError, "export already exists"):
            self.request(op="prepare-seal-training", role_requests=audits)
        self.assertFalse((self.private / "requests/seal-training.json").exists())
        self.assertEqual(exported.read_bytes(), b"old sealed bytes\n")

    def test_guarded_seal_rejects_mixed_or_changed_snapshots_before_any_publication(self):
        evo = control.evo
        contexts = []
        for index in range(2):
            snapshot = self.private / f"snapshot-{index}.json"
            snapshot.write_bytes((self.skill / "training.json").read_bytes())
            contexts.append({"feedback": {"source": "evo-judge", "training_snapshot": str(snapshot),
                                         "training_sha256": control.sha(snapshot)}})
        self.assertEqual(evo.guarded_training_runtime(contexts), self.runtime)
        snapshot = Path(contexts[1]["feedback"]["training_snapshot"])
        changed = control.evo.read(snapshot)
        changed["runtime"]["client_type"] = "another-client"
        self.write(snapshot, changed)
        with self.assertRaisesRegex(ValueError, "snapshot changed"):
            evo.guarded_training_runtime(contexts)
        contexts[1]["feedback"]["training_sha256"] = control.sha(snapshot)
        with self.assertRaisesRegex(ValueError, "snapshots differ"):
            evo.guarded_training_runtime(contexts)
        changed["guard_version"] = 1
        self.write(snapshot, changed)
        contexts[1]["feedback"]["training_sha256"] = control.sha(snapshot)
        with self.assertRaisesRegex(ValueError, "every context"):
            evo.guarded_training_runtime(contexts)

    def append_trace(self, path, rows):
        with path.open("a") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")

    def format_fixture(self, completed=True):
        mode = {"mode": "controller", "session_id": self.meta["session_id"], "config": "synthetic-unused",
                "completion_request": str(self.scratch / "evo-guard/completion-request.json")}
        self.write(self.scratch / control.MARKER, mode)
        call = {"description": "SYNTHETIC evaluator", "prompt": "Read the unchanged shared evaluation.md", **self.runtime, "run_in_background": True}
        ticket = control.write_ticket(self.scratch, mode, call, "h0/CASE-001-synthetic/1", "h0")
        dispatch = Path(ticket["ticket"]).parent
        self.write(dispatch / (control.evo.digest(ticket["slot"].encode()) + ".claimed.json"), {"tool_call_id": "launch-worker"})
        rows = [
            {"type": "model_msg", "payload": {"type": "tool_call", "name": "run_subagent", "tool_call_id": "launch-worker", "arguments": json.dumps(call)}},
            {"type": "event_msg", "payload": {"type": "subagent", "session_id": "session-fixture-12345678"}},
            {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "launch-worker",
              "output": "[subagent running in background with subagent_id subagent-12345678; completion will arrive]"}},
        ]
        if completed:
            rows.append({"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "harness",
                "text": "[background_task_done]\nkind: subagent\nid: subagent-12345678\nstatus: completed\n[/background_task_done]\n\nHere is fenced YAML from the existing result."}})
        self.append_trace(self.trace, rows)
        child = self.trace.parent / "session-fixture-12345678_001.jsonl"
        child.write_text(json.dumps({"type": "session_meta", "payload": {**self.meta, "session_id": "session-fixture-12345678", "source": "subagent"}}) + "\n")
        self.append_trace(child, [{"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": call["prompt"]}}])
        return mode, child

    def test_format_repair_uses_same_completed_native_evaluator_and_never_dispatches_itself(self):
        mode, child = self.format_fixture()
        request = self.request(op="format-request", subagent_id="subagent-12345678")
        self.assertFalse(request["automatic_dispatch"])
        self.assertEqual(self.tool("input_subagent", request["call"]), {})
        self.assertEqual(self.tool("input_subagent", {**request["call"], "prompt": "Rerun the benchmark"})["decision"], "deny")
        self.assertEqual(self.tool("input_subagent", request["call"], "repeat-repair")["decision"], "deny")
        with self.assertRaises(ValueError):
            control.verify_format_repairs(mode)
        self.append_trace(child, [
            {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": control.FORMAT_PROMPT}},
            {"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": "protocol_version: 1\nstatus: ok"}},
        ])
        control.verify_format_repairs(mode)
        self.append_trace(child, [{"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "arguments": "{}", "tool_call_id": "forbidden-new-work"}}])
        with self.assertRaisesRegex(ValueError, "issued tools"):
            control.verify_format_repairs(mode)

    def test_format_repair_rejects_running_unknown_worker_and_training_roles(self):
        self.format_fixture(completed=False)
        with self.assertRaisesRegex(ValueError, "completed notification"):
            self.request(op="format-request", subagent_id="subagent-12345678")
        with self.assertRaises(ValueError):
            self.request(op="format-request", subagent_id="99999999")
        self.write(self.scratch / control.MARKER, {"mode": "training", "session_id": self.meta["session_id"], "private_learning": str(self.private)})
        with self.assertRaisesRegex(ValueError, "controller-only"):
            self.request(op="format-request", subagent_id="subagent-12345678")
        self.assertEqual(self.tool("input_subagent", {"subagent_id": "subagent-12345678", "prompt": control.FORMAT_PROMPT})["decision"], "deny")

    def test_candidate_namespaces_and_limits_reach_fresh_role_prompts(self):
        # The binding validator has its own full trace tests; this checks the material
        # each freshly spawned role actually receives, including non-default budgets.
        contexts = [{"task_id": task, "attempt_id": "attempt-1", "negative": False,
                     "feedback": {"source": "penguin-evaluation"}} for task in self.profile["train_ids"]]
        with patch.object(control, "completed_context", side_effect=lambda t, task, parent: next(c for c in contexts if c["task_id"] == task)):
            contexts[0]["negative"] = True
            reflector = self.request(op="role-request", role="reflector", task_id="train_001")["call"]["prompt"]
            self.assertIn("train_001-attempt-1-c01", reflector)
            self.assertIn('"op": "validate-candidates"', reflector)
            with self.assertRaisesRegex(ValueError, "completed Reflector audit"):
                self.request(op="role-request", role="evolver")
            contexts[0]["negative"] = False
            evolver = self.request(op="role-request", role="evolver")["call"]["prompt"]
            self.assertIn('"limits": {"general": 5, "topic": 5}', evolver)
            self.assertIn('"max_bytes": 16000', evolver)
            self.assertIn('"op": "validate-decisions"', evolver)
            self.assertIn("UTF-8", evolver)

    def test_full_sequential_batch_two_reflectors_compiles_exports_and_seals(self):
        # The HTTP responder and native model messages are fixtures; every method,
        # trace, budget, candidate, compilation and seal check below is real.
        evo = control.evo
        helper = self.skill / "scripts/evo.py"
        journal = str(self.private / "journal.json")
        common = {"journal": journal, "config_sha256": self.profile["config_sha256"]}
        evo.journal({**common, "action": "init", "controller_session_id": self.meta["session_id"], "budget_usd": 1})
        reserve = {**common, "action": "reserve", "controller_trace": str(self.trace), "role": "fixture", "reserve_usd": 0.05}
        evo.journal({**reserve, "operation_id": "controller", "slot": "controller", "request_sha256": "fixture"})
        counter = 0
        def pair(name, output, arguments):
            nonlocal counter
            counter += 1
            call_id = f"fixture-tool-{counter}"
            return [{"type": "model_msg", "payload": {"type": "tool_call", "name": name,
                     "tool_call_id": call_id, "arguments": json.dumps(arguments)}},
                    {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id,
                     "output": json.dumps(output)}}]
        def reads(materials):
            rows = []
            for material in materials:
                for offset in range(0, max(1, len(Path(material).read_text())), 4000):
                    rows.extend(pair("exec_command", evo.material({"path": str(material), "offset": offset}),
                        {"cmd": f"python3 -B {helper} {self.private / 'fixture-read-request.json'}"}))
            return rows
        def delegate(prepared, session_id):
            call_id = "dispatch-" + session_id
            evo.journal({**reserve, **{key: prepared[key] for key in ("operation_id", "slot", "request_sha256")}})
            self.assertEqual(self.tool("run_subagent", prepared["call"], call_id), {})
            self.append_trace(self.trace, [
                {"type": "model_msg", "payload": {"type": "tool_call", "name": "run_subagent", "tool_call_id": call_id,
                    "arguments": json.dumps(prepared["call"])}},
                {"type": "event_msg", "payload": {"type": "subagent", "session_id": session_id}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id, "output": "SYNTHETIC native child registered"}},
            ])
            evo.journal({**common, "action": "bind", "operation_id": prepared["operation_id"], "external_id": session_id})
            return {**self.meta, "session_id": session_id, "source": "subagent", "parent_trace": str(self.trace)}
        def seal_leaf(prepared, binding, materials, role, task=None):
            trace = self.trace.parent / (binding["session_id"] + "_001.jsonl")
            request = {"op": "seal-role-output", "role": role, **({"task_id": task} if task else {})}
            request_path = self.private / "requests" / (binding["session_id"] + "-seal.json")
            self.write(request_path, request)
            active = pair("exec_command", {}, {"cmd": f"python3 -B {helper} {request_path}"})
            self.append_trace(trace, [{"type": "session_meta", "payload": binding},
                {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": prepared["call"]["prompt"]}}]
                + reads(materials) + [active[0]])
            with patch.object(evo, "__file__", str(helper)), patch.dict(os.environ, PENGUIN_SESSION_ID=binding["session_id"]):
                sealed = evo.execute(request, str(request_path))
            self.assertTrue(sealed["complete"])
            active[1]["payload"]["output"] = json.dumps(sealed)
            self.append_trace(trace, [active[1]])
            return trace
        contexts, roles = [], []
        for n, task in enumerate(self.profile["train_ids"], 1):
            prepared = self.request(op="role-request", role="solver", task_id=task)
            binding = delegate(prepared, "fixture-solver-" + task)
            paths = evo.judge_paths(self.profile, task, "attempt-1")
            self.write(Path(paths["answer"]), {"synthetic": task})
            with patch.object(evo, "__file__", str(helper)):
                generated = evo.prepare_judge_request({"task_id": task, "url": "http://127.0.0.1:9003/api/judge"})
                request = evo.read(generated["judge_request"])
                with patch.object(evo.urllib.request, "build_opener") as opener:
                    response = opener.return_value.open.return_value.__enter__.return_value
                    response.status = 200
                    response.read.return_value = json.dumps({"score": 0.5 if n <= 2 else 1, "correct": n > 2}).encode()
                    feedback = evo.judge(request)
            inputs = [str(self.workspace / "train_tasks" / task / "instruction.md"), str(self.workspace / "environment_access.md")]
            rows = [{"type": "session_meta", "payload": binding},
                    {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": prepared["call"]["prompt"]}}]
            rows += reads(inputs)
            rows += pair("exec_command", evo.judge_stdout(feedback), {"cmd": generated["command"]})
            rows += reads([paths["output"]])
            trace = self.trace.parent / (binding["session_id"] + ".jsonl")
            self.append_trace(trace, rows)
            output = self.private / "contexts" / (task + ".json")
            capture = {"op": "capture", "split": "train", "task_id": task, "attempt_id": "attempt-1", "train_ids": self.profile["train_ids"],
                       "answer": paths["answer"], "feedback": paths["output"], "judge_request": generated["judge_request"],
                       "inputs": inputs, "traces": [str(trace)], "binding": binding, "output": str(output)}
            evo.capture(capture)
            contexts.append(str(output))
            if n <= 2:
                reflector = self.request(op="role-request", role="reflector", task_id=task)
                reflect_binding = delegate(reflector, "fixture-reflector-" + task)
                candidate_path = self.private / "roles" / task / "candidates.json"
                self.write(candidate_path, [{"id": task + "-attempt-1-c01", "task_id": task, "attempt_id": "attempt-1",
                    "lesson": "SYNTHETIC inspect all required fields before submission", "trigger": "SYNTHETIC incomplete record",
                    "scope_hint": "SYNTHETIC documents", "evidence": [{"kind": "feedback", "claim": "Partial aggregate feedback", "epistemic": "observed"}]}])
                draft_before = candidate_path.read_bytes()
                checked = evo.execute({"op": "validate-candidates", "contexts": [str(output)], "train_ids": [task],
                    "candidates": str(candidate_path), "candidate_prefix": task + "-attempt-1-"})
                self.assertTrue(checked["complete"])
                self.assertEqual(candidate_path.read_bytes(), draft_before)
                materials = [str(self.private / "roles/reflector.md"), str(output)]
                reflect_trace = seal_leaf(reflector, reflect_binding, materials, "reflector", task)
                audit = {"role": "reflector", "task_id": task, "role_text_path": materials[0], "traces": [str(reflect_trace)],
                         "binding": reflect_binding, "materials": materials, "outputs": [str(candidate_path)]}
                evo.audit_role(audit)
                roles.append(audit)
        evolver = self.request(op="role-request", role="evolver", reflectors=roles)
        evolved_binding = delegate(evolver, "fixture-evolver-batch-1")
        self.assertIn('"max_bytes": 16000', evolver["call"]["prompt"])
        candidates = self.private / "candidates.json"
        decisions = self.private / "decisions.json"
        ids = [candidate["id"] for candidate in evo.read(candidates)]
        self.assertEqual(len(set(ids)), 2)
        self.write(decisions, [{"op": "ADD", "candidate_ids": ids, "reason": "SYNTHETIC supported procedure", "entry": {
            "id": "fixture-field-review", "level": "general", "trigger": "SYNTHETIC required-field task", "scope": "SYNTHETIC forms",
            "body": "Before submission, compare the public required fields with the current document and fill any omitted public fields."}}])
        before_decisions = decisions.read_bytes()
        checked = evo.execute({"op": "validate-decisions", "train_ids": self.profile["train_ids"], "contexts": contexts,
            "candidates": str(candidates), "decisions": str(decisions), "library": str(self.private / "prebatch-library.json"),
            "limits": self.profile["limits"], "max_entries": 5, "max_bytes": 16000})
        self.assertTrue(checked["complete"])
        self.assertEqual(decisions.read_bytes(), before_decisions)
        materials = [str(self.private / "roles/evolver.md"), str(candidates), str(self.private / "prebatch-library.json"), *contexts]
        evolved_trace = seal_leaf(evolver, evolved_binding, materials, "evolver")
        roles.append({"role": "evolver", "role_text_path": materials[0], "traces": [str(evolved_trace)], "binding": evolved_binding,
                      "materials": materials, "outputs": [str(decisions)]})
        compiled = self.private / "compiled.json"
        compile_request = {"train_ids": self.profile["train_ids"], "contexts": contexts, "candidates": str(candidates), "decisions": str(decisions),
            "library": str(self.private / "prebatch-library.json"), "limits": self.profile["limits"], "output": str(compiled)}
        evo.compile_batch(compile_request)
        skill = self.workspace / "submission/skill"
        evo.export_skill({**compile_request, "library": str(compiled), "output": str(skill), "max_entries": 5, "max_bytes": 16000})
        seal = {**compile_request, "op": "seal-training", "private_learning": str(self.private), "skill": str(skill),
            "roles": roles, "runtime": self.runtime, "max_entries": 5, "max_bytes": 16000, "output": str(self.private / "training-export.json")}
        evidence_before = {str(path): path.read_bytes() for directory in (self.trace.parent, self.private / "feedback")
                           for path in directory.rglob("*") if path.is_file()}
        bad_runtimes = [{k: v for k, v in self.runtime.items() if k not in ("effective_reasoning_effort", "client_type")},
                        {**self.runtime, "undeclared": "extra"}, None]
        bad_runtimes += [{**self.runtime, key: "changed"} for key in self.runtime]
        for runtime in bad_runtimes:
            with self.subTest(runtime=runtime):
                rejected = {**seal, "runtime": runtime}
                rejection_request = self.private / "requests/rejected-seal.json"
                self.write(rejection_request, rejected)
                self.assertEqual(self.tool("exec_command", {"cmd": f"python3 -B {helper} {rejection_request}"})["decision"], "deny")
                with self.assertRaisesRegex(ValueError, "complete frozen runtime"):
                    evo.seal_training(rejected)
                self.assertFalse(Path(seal["output"]).exists())
        role_requests = []
        for index, role in enumerate(roles):
            path = self.private / "requests" / f"completed-audit-{index}.json"
            self.write(path, {"op": "audit-role", **role})
            role_requests.append(str(path))
        receipt = self.request(op="prepare-seal-training", role_requests=role_requests)
        seal = evo.read(receipt["request_path"])
        self.assertEqual(self.tool("exec_command", {"cmd": receipt["command"]}), {})
        sealed = evo.seal_training(seal)
        self.assertEqual(sealed["runtime"], self.runtime)
        self.assertEqual(len(sealed["members"]), 5)
        self.assertEqual(len(sealed["roles"]), 3)
        self.assertTrue(self.request(op="verify-training")["complete"])
        self.assertTrue(self.invoke("stop")["output"]["complete"])
        self.assertEqual({path: Path(path).read_bytes() for path in evidence_before}, evidence_before)
        before_export = Path(seal["output"]).read_bytes()
        with self.assertRaises(FileExistsError):
            evo.seal_training(seal)
        self.assertEqual(Path(seal["output"]).read_bytes(), before_export)

    def test_host_can_prepare_launch_documentation_but_cannot_execute_experiment_directly(self):
        mode, _ = self.format_fixture()
        for command in (
            "python3 - <<'PY'\nfrom pathlib import Path\nPath('/tmp/fixture-statement.md').write_text('harbor run --task tg003')\nPY",
            "cat <<'EOF' > /tmp/fixture-statement.md\nharbor run --task tg003\npenguin run --message test\nEOF",
            "echo 'harbor run --task tg003'", "python3 -c \"print('penguin run')\"",
        ):
            with self.subTest(command=command):
                self.assertEqual(self.tool("exec_command", {"cmd": command}), {})
        for command in ("harbor run --task tg003", "uvx --from harbor==0.23.0 harbor run --task tg003",
                        "/opt/evo/bin/penguin run --message test", "python3 -m harbor run --task tg003",
                        "python3 - <<'PY'\nimport subprocess\nsubprocess.run(['harbor', 'run', '--task', 'tg003'])\nPY",
                        "bash -lc 'harbor run --task tg003'", "sudo -u agent harbor run --task tg003",
                        "env -u PYTHONPATH harbor run --task tg003", "penguin --server http://127.0.0.1:4040 run --message test"):
            with self.subTest(command=command):
                self.assertEqual(self.tool("exec_command", {"cmd": command})["decision"], "deny")


if __name__ == "__main__":
    unittest.main()
