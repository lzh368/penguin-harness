"""Synthetic native-hook requests; no model, Harbor or benchmark service calls."""
import importlib.util
from functools import lru_cache
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from test_evo import h0_read_rows
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "skills/evo-harness-rsi-toolkit/scripts/control.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("evo_control_test", SCRIPT)
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


@lru_cache(maxsize=1)
def native_dependency_checkout():
    """Optional read-only local SDK/YAML dependency reuse, never another method.

    Fresh offline worktrees may have no node_modules. An override is accepted
    only at the same commit with byte-identical tracked core/server sources and
    dependency manifests; production method code still comes from SCRIPT.
    """
    current = SCRIPT.parents[5]
    target = Path(os.environ.get("EVO_NATIVE_REPO", current)).resolve()
    if target == current.resolve():
        return current
    def git(root, *args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
    if git(current, "rev-parse", "HEAD") != git(target, "rev-parse", "HEAD"):
        raise ValueError("offline native dependency checkout must have the same source commit")
    groups = ("packages/core/src", "packages/server/src", "packages/core/package.json",
              "packages/server/package.json", "package.json", "pnpm-lock.yaml")
    tracked = git(current, "ls-files", *groups).splitlines()
    if tracked != git(target, "ls-files", *groups).splitlines() or not tracked:
        raise ValueError("offline native dependency source inventory differs")
    for relative in tracked:
        if (current / relative).read_bytes() != (target / relative).read_bytes():
            raise ValueError("offline native dependency source differs: " + relative)
    return target


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
        self.profile = {"guard_version": 2, "reflection_mode": "solver-session", "train_ids": [f"train_{n:03d}" for n in range(1, 6)],
            "attempt_id": "attempt-1", "config_sha256": "a" * 64, "private_learning": str(self.private),
            "answer_dir": str(self.workspace / "submission/answers"), "feedback_dir": str(self.private / "feedback"),
            "workspace": str(self.workspace), "runtime": self.runtime, "training_budget_usd": 1,
            "solving_timeout_sec": 21600, "compilation_reserve_sec": 3600,
            "limits": {"general": 5, "topic": 5}, "max_bytes": 16000}
        self.write(self.skill / "training.json", self.profile)
        for task in self.profile["train_ids"]:
            file = self.workspace / "train_tasks" / task / "instruction.md"
            file.parent.mkdir(parents=True)
            file.write_text("Synthetic public task. No real answers.\n")
        (self.workspace / "environment_access.md").write_text("Synthetic public service documentation.\n")
        self.library = self.root / "library.json"
        self.library.write_text("[]")
        control.evo.export_skill({"library": str(self.library), "limits": {"general": 5, "topic": 5},
            "max_bytes": 16000, "output": str(self.state / "skills/evo-harness-learned")})
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
        self.assertIn('"task_id": "train_001"', control.evo.native_prompt(call))
        self.assertIn('"op": "prepare-judge-request"', control.evo.native_prompt(call))
        self.assertIn("makes zero judge calls", control.evo.native_prompt(call))
        self.assertIn("read-material", control.evo.native_prompt(call))
        self.assertIn(str(self.private / "roles/solver.md"), control.evo.native_prompt(call))
        self.assertEqual(self.tool("run_subagent", call)["decision"], "deny")
        common = {"journal": prepared["journal"], "config_sha256": self.profile["config_sha256"]}
        control.evo.journal({**common, "action": "init", "controller_session_id": self.meta["session_id"], "budget_usd": 1})
        reserve = {**common, "action": "reserve", "controller_trace": str(self.trace), "reserve_usd": 0.2, "role": "synthetic"}
        control.evo.journal({**reserve, "operation_id": "controller", "slot": "controller", "request_sha256": "controller"})
        control.evo.journal({**reserve, **{k: prepared[k] for k in ("operation_id", "slot", "request_sha256")}})
        self.assertEqual(self.tool("run_subagent", call), {})
        self.assertEqual(self.tool("run_subagent", call, "call-2")["decision"], "deny")
        altered = {**call, "prompt": control.evo.native_prompt(call) + "Try again"}
        self.assertEqual(self.tool("run_subagent", altered)["decision"], "deny")

    def test_next_solver_and_evolver_require_real_prior_contexts(self):
        for fields in ({"role": "solver", "task_id": "train_002"}, {"role": "evolver"}):
            with self.assertRaises((OSError, ValueError)):
                self.request(op="role-request", **fields)

    def test_long_role_task_is_immutable_file_not_model_recopied_text(self):
        # Reproduce the real 10K+ role request and its five-character copy loss,
        # using only synthetic instructions. No dispatcher or model is mocked
        # into succeeding here: preparation itself makes no native call.
        long_role = "SYNTHETIC role\r\n" + ("Unicode 刘 😀 word\n" * 1000)
        with patch.object(control, "role_text", return_value=long_role):
            prepared = self.request(op="role-request", role="solver", task_id="train_001")
            again = self.request(op="role-request", role="solver", task_id="train_001")
        call = prepared["call"]
        self.assertEqual(prepared, again)
        self.assertNotIn("prompt", call)
        self.assertLess(len(control.canonical(prepared)), 2000)
        text = control.evo.native_prompt(call)
        self.assertIn(long_role, text)
        raw = Path(call["prompt_file"]).read_bytes()
        self.assertEqual(raw.decode("utf-8"), text)
        self.assertEqual(control.evo.digest(raw), call["prompt_sha256"])
        # The previous manual-copy failure no longer appears in the native
        # arguments. A real file mutation must still fail closed.
        Path(call["prompt_file"]).write_bytes(raw.replace(b" word", b"", 1))
        with self.assertRaisesRegex(ValueError, "SHA256 changed"):
            control.evo.native_prompt(call)
        with patch.object(control, "role_text", return_value=long_role):
            with self.assertRaisesRegex(ValueError, "role prompt changed"):
                self.request(op="role-request", role="solver", task_id="train_001")

    def test_prompt_transport_rejects_mixed_hashless_and_noncanonical_files(self):
        prepared = self.request(op="role-request", role="solver", task_id="train_001")
        call = prepared["call"]
        variants = [{**call, "prompt": "do something else"},
                    {k: v for k, v in call.items() if k != "prompt_sha256"},
                    {**call, "prompt_sha256": "0" * 64}]
        link = self.private / "linked-prompt.txt"
        link.symlink_to(call["prompt_file"])
        variants.append({**call, "prompt_file": str(link)})
        for altered in variants:
            with self.subTest(fields=sorted(altered)):
                with self.assertRaises(ValueError):
                    control.evo.native_prompt(altered)
        self.assertEqual(control.evo.native_prompt({"prompt": "original inline task"}), "original inline task")

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
        for index, role in enumerate(("evolver",)):
            path = self.private / "requests" / f"audit-{index}.json"
            self.write(path, {"op": "audit-role", "role": role,
                "materials": [f"{self.private}/material-{n}.json" for n in range(2000 if large else 1)]})
            audits.append(str(path))
        contexts = [{"task_id": task, "negative": False,
                     "reflection": {"mode": "solver-session", "status": "not-required"}}
                    for task in self.profile["train_ids"]]
        fixture = patch.object(control, "completed_context", side_effect=lambda t, task, parent:
                               next(c for c in contexts if c["task_id"] == task))
        fixture.start()
        self.addCleanup(fixture.stop)
        return audits

    def capture_preparation_fixture(self):
        """Real capture evidence and native dispatch; synthetic HTTP/model only."""
        evo, task = control.evo, "train_001"
        for name in ("input/payloads/answer_template.json", "input/payloads/ticket_batch.csv", "input/prompt.txt"):
            path = self.workspace / "train_tasks" / task / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Synthetic public material.\n")
        prepared = self.request(op="role-request", role="solver", task_id=task)
        common = {"journal": prepared["journal"], "config_sha256": self.profile["config_sha256"]}
        evo.journal({**common, "action": "init", "controller_session_id": self.meta["session_id"], "budget_usd": 1})
        reserve = {**common, "action": "reserve", "controller_trace": str(self.trace), "reserve_usd": 0.1, "role": "synthetic"}
        evo.journal({**reserve, "operation_id": "controller", "slot": "controller", "request_sha256": "controller"})
        evo.journal({**reserve, **{key: prepared[key] for key in ("operation_id", "slot", "request_sha256")}})
        self.assertEqual(self.tool("run_subagent", prepared["call"], "launch-solver"), {})
        session, handle = "synthetic-solver-one", "subagent-synthetic-one"
        self.append_trace(self.trace, [
            {"type": "model_msg", "payload": {"type": "tool_call", "name": "run_subagent", "tool_call_id": "launch-solver", "arguments": json.dumps(prepared["call"])}},
            {"type": "event_msg", "payload": {"type": "subagent", "session_id": session}},
            {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "launch-solver", "output": f"[subagent running in background with subagent_id {handle}; completion will arrive]"}}])
        binding = {**self.meta, "session_id": session, "source": "subagent", "parent_trace": str(self.trace)}
        grants = [value["evo_task"] for value in evo.json_values(control.evo.native_prompt(prepared["call"]))
                  if isinstance(value, dict) and "evo_task" in value]
        self.assertEqual(len(grants), 1)
        helper = self.skill / "scripts/evo.py"
        paths = evo.judge_paths(self.profile, task, "attempt-1")
        self.write(Path(paths["answer"]), {"synthetic": task})
        with patch.object(evo, "__file__", str(helper)):
            generated = evo.prepare_judge_request({"task_id": task, "url": "http://127.0.0.1:9003/api/judge"})
        count = 0
        def pair(output, command):
            nonlocal count
            count += 1
            return [{"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command",
                     "tool_call_id": f"child-{count}", "arguments": json.dumps({"cmd": command})}},
                    {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": f"child-{count}",
                     "stop_reason": "completed", "output": json.dumps(output)}}]
        def reads(materials, request_dir=None):
            rows = []
            for path in materials:
                for offset in range(0, max(1, len(Path(path).read_text())), 4000):
                    request = {"op": "read-material", "path": path, "offset": offset}
                    name = evo.digest((str(path) + str(offset)).encode())[:16]
                    request_path = (request_dir or self.private / "requests") / ("read-" + name + ".json")
                    self.write(request_path, request)
                    rows += [{"type": "model_msg", "payload": {"type": "tool_call", "name": "write_file",
                        "tool_call_id": "request-write-" + name, "arguments": json.dumps({"file_path": str(request_path), "content": json.dumps(request)})}},
                        {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": "request-write-" + name, "stop_reason": "completed", "output": "written"}}]
                    rows += pair(evo.material({"path": path, "offset": offset}),
                                 f"python3 -B {helper} {request_path}")
            return rows
        first = self.trace.parent / (session + "_001.jsonl")
        second = self.trace.parent / (session + "_002.jsonl")
        self.append_trace(first, [{"type": "session_meta", "payload": binding},
            {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": control.evo.native_prompt(prepared["call"])}}]
            + h0_read_rows(binding, self.profile["max_bytes"])
            + reads([str(self.private / "roles/solver.md"), *grants[0]["inputs"]]))
        active = pair({}, generated["command"])
        self.append_trace(second, [{"type": "session_meta", "payload": binding}, active[0]])
        with patch.object(evo, "__file__", str(helper)), patch.dict(os.environ, PENGUIN_SESSION_ID=session):
            with patch.object(evo.urllib.request, "build_opener") as opener:
                response = opener.return_value.open.return_value.__enter__.return_value
                response.status = 200
                response.read.return_value = b'{"score": 1, "correct": true}'
                feedback = evo.judge(evo.read(generated["judge_request"]))
                self.assertEqual(opener.return_value.open.call_count, 1)
        active[1]["payload"]["output"] = json.dumps(evo.judge_stdout(feedback))
        self.append_trace(second, [active[1]]
            + reads([paths["output"]], self.private / "roles" / task / "requests")
            + [{"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": "Synthetic Solver complete."}},
               {"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}}])
        self.append_trace(self.trace, [{"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "harness",
            "text": f"[background_task_done]\nkind: subagent\nid: {handle}\nstatus: completed\n[/background_task_done]\nSynthetic result."}}])
        return {"prepared": prepared, "binding": binding, "traces": [first, second], "inputs": grants[0]["inputs"], "paths": paths}

    def test_prepare_capture_derives_exact_grant_and_segments_then_original_capture_runs(self):
        fixture = self.capture_preparation_fixture()
        wrong = self.private / "requests/capture-train_001.json"
        self.write(wrong, {"inputs": [str(self.workspace / "train_tasks/train_001/input/instruction.md")]})
        evidence = [self.trace, *fixture["traces"], wrong, *(Path(path) for path in fixture["paths"].values())]
        before = {str(path): path.read_bytes() for path in evidence}
        caller = self.private / "requests/prepare-capture.json"
        self.write(caller, {"op": "prepare-capture", "trace_path": str(self.trace), "task_id": "train_001"})
        self.assertEqual(self.tool("exec_command", {"cmd": f"python3 -B {control.CONTROL} {caller}"}), {})
        with patch.object(control.evo.urllib.request, "build_opener", side_effect=AssertionError("no new judge")):
            receipt = control.execute(control.evo.read(caller))
            prepared = Path(receipt["request_path"])
            request = control.evo.read(prepared)
            self.assertEqual(request["inputs"], fixture["inputs"])
            self.assertIn(str(self.workspace / "train_tasks/train_001/instruction.md"), request["inputs"])
            self.assertEqual(request["traces"], [str(path) for path in fixture["traces"]])
            self.assertEqual(request["binding"], fixture["binding"])
            self.assertFalse(Path(request["output"]).exists())
            self.assertFalse(receipt["captured"])
            self.assertTrue(receipt["prepared"])
            self.assertEqual(receipt["model_calls"], receipt["judge_calls"], 0)
            self.assertLess(len(json.dumps(receipt)), 2048)
            stamp = prepared.stat().st_mtime_ns
            self.assertEqual(control.execute(control.evo.read(caller)), receipt)
            self.assertEqual(prepared.stat().st_mtime_ns, stamp)
            self.assertEqual(self.tool("exec_command", {"cmd": receipt["command"]}), {})
            control.evo.capture(request)
            control.completed_context(self.profile, "train_001", self.meta)
        self.assertEqual({path: Path(path).read_bytes() for path in before}, before)

    def test_prepare_capture_rejects_overrides_and_preserves_conflicting_request(self):
        self.capture_preparation_fixture()
        with self.assertRaises(ValueError):
            self.request(op="prepare-capture", task_id="train_001", inputs=[])
        receipt = self.request(op="prepare-capture", task_id="train_001")
        destination = Path(receipt["request_path"])
        changed = control.evo.read(destination)
        changed["inputs"].reverse()
        self.write(destination, changed)
        original = destination.read_bytes()
        with self.assertRaisesRegex(ValueError, "existing capture request differs"):
            self.request(op="prepare-capture", task_id="train_001")
        self.assertEqual(destination.read_bytes(), original)
        self.assertFalse((self.private / "contexts/train_001.json").exists())

    def test_prepare_capture_checks_real_evidence_before_publishing_metadata(self):
        fixture = self.capture_preparation_fixture()
        output = self.private / "requests/capture-train_001-attempt-1.prepared.json"
        second = fixture["traces"][1]
        original = second.read_bytes()
        rows = [json.loads(line) for line in original.splitlines()]
        second.write_text("\n".join(json.dumps(row) for row in rows[:3]) + "\n")
        with self.assertRaises(ValueError):
            self.request(op="prepare-capture", task_id="train_001")
        self.assertFalse(output.exists())
        self.assertFalse((self.private / "contexts/train_001.json").exists())
        second.write_bytes(original)
        self.assertTrue(self.request(op="prepare-capture", task_id="train_001")["prepared"])

    def test_prepare_capture_requires_registered_original_child_and_completed_notice(self):
        fixture = self.capture_preparation_fixture()
        root_bytes = self.trace.read_bytes()
        root_rows = [json.loads(line) for line in root_bytes.splitlines()]
        for excluded in ("tool_call", "subagent", "text"):
            self.trace.write_text("\n".join(json.dumps(row) for row in root_rows
                if row.get("payload", {}).get("type") != excluded) + "\n")
            with self.subTest(excluded=excluded), self.assertRaises(ValueError):
                self.request(op="prepare-capture", task_id="train_001")
        self.trace.write_bytes(root_bytes)
        ticket = control.evo.read(fixture["prepared"]["ticket"])
        claim = Path(fixture["prepared"]["ticket"]).parent / (control.evo.digest(ticket["slot"].encode()) + ".claimed.json")
        data = control.evo.read(claim)
        self.write(claim, {**data, "request_sha256": "0" * 64})
        with self.assertRaisesRegex(ValueError, "claim"):
            self.request(op="prepare-capture", task_id="train_001")
        self.assertFalse((self.private / "requests/capture-train_001-attempt-1.prepared.json").exists())

    def test_prepare_capture_rejects_foreign_ambiguous_and_linked_child_evidence(self):
        fixture = self.capture_preparation_fixture()
        first = fixture["traces"][0]
        original = first.read_bytes()
        rows = [json.loads(line) for line in original.splitlines()]
        for field, value in (("source", "cli"), ("provider", "foreign"),
                             ("agent_state", str(self.root / "foreign-state")),
                             ("workspace", str(self.root / "foreign-workspace"))):
            changed = json.loads(json.dumps(rows))
            changed[0]["payload"][field] = value
            first.write_text("\n".join(json.dumps(row) for row in changed) + "\n")
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "identity changed"):
                self.request(op="prepare-capture", task_id="train_001")
        first.write_bytes(original)
        other = self.trace.parent / "synthetic-another_001.jsonl"
        changed = json.loads(json.dumps(rows[:2]))
        changed[0]["payload"]["session_id"] = "synthetic-another"
        self.append_trace(other, changed)
        self.append_trace(self.trace, [{"type": "event_msg", "payload": {"type": "subagent", "session_id": "synthetic-another"}}])
        with self.assertRaisesRegex(ValueError, "unambiguous"):
            self.request(op="prepare-capture", task_id="train_001")
        other.unlink()
        outside = self.private / "outside-child.jsonl"
        first.rename(outside)
        first.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "outside allowed"):
            self.request(op="prepare-capture", task_id="train_001")
        self.assertFalse((self.private / "requests/capture-train_001-attempt-1.prepared.json").exists())

    def test_prepare_capture_resolves_old_parent_segment_and_revalidates_unchanged_request(self):
        fixture = self.capture_preparation_fixture()
        resumed = self.trace.parent / "synthetic-root_002.jsonl"
        self.append_trace(resumed, [{"type": "session_meta", "payload": self.meta}])
        request = {"op": "prepare-capture", "trace_path": str(resumed), "task_id": "train_001"}
        receipt = control.execute(request)
        data = control.evo.read(receipt["request_path"])
        self.assertEqual(data["binding"]["parent_trace"], str(self.trace))
        prepared = Path(receipt["request_path"])
        before = prepared.read_bytes()
        second = fixture["traces"][1]
        original = second.read_bytes()
        rows = [json.loads(line) for line in original.splitlines()]
        second.write_text("\n".join(json.dumps(row) for row in rows[:3]) + "\n")
        with self.assertRaises(ValueError):
            control.execute(request)
        self.assertEqual(prepared.read_bytes(), before)
        self.assertEqual(list(self.private.glob("capture-check-*")), [])
        self.assertFalse((self.private / "contexts/train_001.json").exists())
        second.write_bytes(original)
        self.assertEqual(control.execute(request), receipt)

    def test_prepare_capture_never_omits_damaged_original_solver_or_parent_segments(self):
        fixture = self.capture_preparation_fixture()
        destination = self.private / "requests/capture-train_001-attempt-1.prepared.json"
        for session, content in ((fixture["binding"]["session_id"], b'{"partial":'),
                                 (self.meta["session_id"], b'{"partial":'),
                                 (self.meta["session_id"], (json.dumps({"type": "session_meta", "payload": self.meta}) + '\n{"damaged":\n').encode())):
            broken = self.trace.parent / (session + "_003.jsonl")
            broken.write_bytes(content)
            with self.subTest(session=session, content=content), self.assertRaises(ValueError):
                self.request(op="prepare-capture", task_id="train_001")
            self.assertFalse(destination.exists())
            self.assertEqual(broken.read_bytes(), content)
            broken.unlink()
        # A different live Session's incomplete initial write is not evidence for
        # this Solver and cannot cause its valid original segments to be omitted.
        (self.trace.parent / "unrelated_003.jsonl").write_bytes(b'{"partial":')
        self.assertTrue(self.request(op="prepare-capture", task_id="train_001")["prepared"])

    def test_prepare_capture_recovery_revalidates_existing_context_without_second_write(self):
        fixture = self.capture_preparation_fixture()
        receipt = self.request(op="prepare-capture", task_id="train_001")
        self.assertFalse(receipt["context_exists"])
        control.evo.capture(control.evo.read(receipt["request_path"]))
        context = Path(receipt["context_path"])
        evidence = [context, Path(receipt["request_path"]), self.trace, *fixture["traces"]]
        before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in evidence}
        with patch.object(control.evo.urllib.request, "build_opener", side_effect=AssertionError("no judge")):
            recovered = self.request(op="prepare-capture", task_id="train_001")
        self.assertTrue(recovered["prepared"] and recovered["context_exists"] and recovered["context_verified"])
        self.assertFalse(recovered["captured"])
        self.assertIsNone(recovered["command"])
        self.assertEqual(recovered["context_sha256"], control.sha(context))
        self.assertEqual({str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in evidence}, before)
        changed = control.evo.read(context)
        changed["negative"] = True
        self.write(context, changed)
        conflicting = context.read_bytes()
        with self.assertRaisesRegex(ValueError, "existing context differs"):
            self.request(op="prepare-capture", task_id="train_001")
        self.assertEqual(context.read_bytes(), conflicting)
        self.assertEqual(Path(receipt["request_path"]).read_bytes(), before[receipt["request_path"]][0])

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

    def format_fixture(self, completed=True, from_file=False):
        config = self.root / "format-config.json"
        self.write(config, {"deployment": {"node": shutil.which("node"), "penguin_checkout": str(native_dependency_checkout())}})
        mode = {"mode": "controller", "session_id": self.meta["session_id"], "config": str(config), "config_sha256": control.sha(config),
                "completion_request": str(self.scratch / "evo-guard/completion-request.json")}
        self.write(self.scratch / control.MARKER, mode)
        self.worker_cell = {"protocol_version": 1, "status": "ok", "case_id": "CASE-001-synthetic", "run": 1,
            "agent_id": "synthetic-target", "expected_version": 1, **{k: self.runtime[k] for k in ("provider", "model_id", "thinking_level")},
            "score": 50, "cost": None, "duration_ms": 1, "session_id": "harbor:synthetic-trial"}
        prompt = "Read the unchanged shared evaluation.md\n" + "\n".join(f"{k}: {v}" for k, v in
            {"case_id": "CASE-001-synthetic", "run": 1, "test_agent_id": "synthetic-target", "expected_version": 1,
             **{k: self.runtime[k] for k in ("provider", "model_id", "thinking_level")}}.items())
        call = {"description": "SYNTHETIC evaluator", "prompt": prompt, **self.runtime, "run_in_background": True}
        if from_file:
            raw = call.pop("prompt").encode("utf-8")
            task = self.scratch / "evo-guard/worker-prompt.txt"
            control.evo.write_once(task, raw)
            call.update(prompt_file=str(task), prompt_sha256=control.evo.digest(raw))
        ticket = control.write_ticket(self.scratch, mode, call, "h0/CASE-001-synthetic/1", "h0")
        dispatch = Path(ticket["ticket"]).parent
        self.write(dispatch / (control.evo.digest(ticket["slot"].encode()) + ".claimed.json"),
                   {"tool_call_id": "launch-worker", "request_sha256": ticket["request_sha256"]})
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
        self.append_trace(child, [
            {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": control.evo.native_prompt(call)}},
            {"type": "event_msg", "payload": {"type": "request_begin"}},
            {"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": "```yaml\n" + json.dumps(self.worker_cell) + "\n```"}},
            {"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}},
        ])
        return mode, child

    def finish_format(self, child, request, text=None, complete=True):
        self.append_trace(self.trace, [{"type": "model_msg", "payload": {"type": "tool_call", "name": "input_subagent",
            "arguments": json.dumps(request["call"]), "tool_call_id": "call-1"}}])
        rows = [
            {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": control.FORMAT_PROMPT}},
            {"type": "event_msg", "payload": {"type": "request_begin"}},
            {"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": json.dumps(self.worker_cell) if text is None else text}},
        ]
        if complete:
            rows.append({"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}})
        self.append_trace(child, rows)

    def test_format_repair_uses_same_completed_native_evaluator_and_never_dispatches_itself(self):
        mode, child = self.format_fixture()
        request = self.request(op="format-request", subagent_id="subagent-12345678")
        self.assertFalse(request["automatic_dispatch"])
        self.assertEqual(self.tool("input_subagent", request["call"]), {})
        self.assertEqual(self.tool("input_subagent", {**request["call"], "prompt": "Rerun the benchmark"})["decision"], "deny")
        self.assertEqual(self.tool("input_subagent", request["call"], "repeat-repair")["decision"], "deny")
        with self.assertRaises(ValueError):
            control.verify_format_repairs(mode)
        self.finish_format(child, request)
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

    def test_file_dispatched_worker_can_resend_existing_yaml_without_new_tools(self):
        mode, child = self.format_fixture(from_file=True)
        before = self.trace.read_bytes()
        request = self.request(op="format-request", subagent_id="subagent-12345678")
        self.assertEqual(self.trace.read_bytes(), before)
        self.assertFalse(request["automatic_dispatch"])
        self.assertEqual(self.tool("input_subagent", request["call"]), {})
        self.finish_format(child, request)
        control.verify_format_repairs(mode)
        self.assertEqual(self.tool("input_subagent", request["call"], "duplicate-format")["decision"], "deny")

    def test_format_repair_cannot_repeat_fences_or_change_existing_values(self):
        mode, child = self.format_fixture()
        request = self.request(op="format-request", subagent_id="subagent-12345678")
        self.assertEqual(self.tool("input_subagent", request["call"]), {})
        before = child.read_bytes()
        for text in ("```yaml\n" + json.dumps(self.worker_cell) + "\n```",
                     json.dumps({**self.worker_cell, "score": 99}),
                     json.dumps({**self.worker_cell, "session_id": "harbor:another-trial"}),
                     json.dumps({**self.worker_cell, "cost": 0}),
                     "protocol_version: 1\nstatus: ok",
                     json.dumps(self.worker_cell) + "\n---\n" + json.dumps(self.worker_cell)):
            with self.subTest(text=text):
                child.write_bytes(before)
                # The same original repair call is sufficient for every synthetic variant.
                parent = self.trace.read_bytes()
                self.finish_format(child, request, text)
                with self.assertRaises(ValueError):
                    control.verify_format_repairs(mode)
                self.trace.write_bytes(parent)

    def test_format_repair_requires_new_request_completion_not_old_or_inflight_text(self):
        mode, child = self.format_fixture()
        request = self.request(op="format-request", subagent_id="subagent-12345678")
        self.assertEqual(self.tool("input_subagent", request["call"]), {})
        self.finish_format(child, request, complete=False)
        with self.assertRaisesRegex(ValueError, "completed native request"):
            control.verify_format_repairs(mode)
        self.append_trace(child, [{"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}}])
        control.verify_format_repairs(mode)

    def test_validate_workers_returns_readonly_format_diagnostic_then_canonical_cells(self):
        mode, child = self.format_fixture(from_file=True)
        registration = {"test": {"case_ids": [self.worker_cell["case_id"]]}}
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch.object(control, "registered_benchmarks", return_value=registration):
            result = self.request(op="validate-workers", phase="h0")
        self.assertFalse(result["complete"])
        self.assertTrue(result["workers"][0]["format_required"])
        self.assertTrue(result["workers"][0]["repair_available"])
        self.assertEqual(result["cells"], [])
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        request = self.request(op="format-request", subagent_id="subagent-12345678")
        self.assertEqual(self.tool("input_subagent", request["call"]), {})
        self.finish_format(child, request)
        with patch.object(control, "registered_benchmarks", return_value=registration):
            result = self.request(op="validate-workers", phase="h0")
        self.assertTrue(result["complete"])
        self.assertEqual(result["cells"], [self.worker_cell])
        cells = self.root / "cells.json"; self.write(cells, [self.worker_cell])
        self.assertTrue(control.verify_worker_matrix(mode, "h0", {"cells": str(cells)})["complete"])
        self.write(cells, [{**self.worker_cell, "score": 99}])
        with self.assertRaisesRegex(ValueError, "differs from matrix"):
            control.verify_worker_matrix(mode, "h0", {"cells": str(cells)})

    def test_worker_yaml_accepts_comments_quotes_order_and_rejects_ambiguous_documents(self):
        mode, _ = self.format_fixture()
        ticket = control.phase_tickets(self.scratch, mode, "h0")[0]
        text = "# harmless comment mentioning ```\n---\n" + "\n".join(
            f"'{key}': {json.dumps(value)} # legal comment" for key, value in reversed(list(self.worker_cell.items()))) + "\n"
        self.assertEqual(control.validate_worker_record(control.worker_yaml(text, mode), ticket), self.worker_cell)
        for bad in (text + "\nstatus: ok\n", text + "\n---\nstatus: ok\n",
                    text + "\nextra: unknown\n", text.replace('50 # legal', '.nan # legal')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                control.validate_worker_record(control.worker_yaml(bad, mode), ticket)

    def test_validate_workers_never_calls_pending_or_identity_errors_format_repairs(self):
        mode, child = self.format_fixture(completed=False)
        registration = {"test": {"case_ids": [self.worker_cell["case_id"]]}}
        with patch.object(control, "registered_benchmarks", return_value=registration):
            result = self.request(op="validate-workers", phase="h0")
        self.assertFalse(result["complete"])
        self.assertTrue(result["workers"][0]["pending"])
        self.assertFalse(result["workers"][0]["format_required"])
        self.append_trace(self.trace, [{"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "harness",
            "text": "[background_task_done]\nkind: subagent\nid: subagent-12345678\nstatus: completed\n[/background_task_done]"}}])
        rows = [json.loads(line) for line in child.read_text().splitlines()]
        rows[-2]["payload"]["text"] = json.dumps({**self.worker_cell, "expected_version": 2})
        child.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        with patch.object(control, "registered_benchmarks", return_value=registration):
            result = self.request(op="validate-workers", phase="h0")
        self.assertFalse(result["workers"][0]["pending"])
        self.assertFalse(result["workers"][0]["repair_available"])
        self.assertFalse(result["workers"][0]["format_required"])

    def test_repeated_fenced_repair_is_terminal_and_stale_reply_cannot_satisfy_it(self):
        mode, child = self.format_fixture()
        request = self.request(op="format-request", subagent_id="subagent-12345678")
        self.assertEqual(self.tool("input_subagent", request["call"]), {})
        self.finish_format(child, request, "```yaml\n" + json.dumps(self.worker_cell) + "\n```")
        with patch.object(control, "registered_benchmarks", return_value={"test": {"case_ids": [self.worker_cell["case_id"]]}}):
            result = self.request(op="validate-workers", phase="h0")
        self.assertFalse(result["complete"])
        self.assertFalse(result["workers"][0]["repair_available"])
        with self.assertRaises(ValueError):
            self.request(op="format-request", subagent_id="subagent-12345678")
        rows = [json.loads(line) for line in child.read_text().splitlines()]
        rows[-2]["payload"]["text"] = json.dumps(self.worker_cell)
        del rows[-3]  # New text with no request_begin cannot borrow the old completed request.
        child.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        with self.assertRaisesRegex(ValueError, "fresh native request"):
            control.verify_format_repairs(mode)

    def test_failed_stopped_or_unregistered_worker_is_never_pending_or_format_repair(self):
        mode, child = self.format_fixture()
        registration = {"test": {"case_ids": [self.worker_cell["case_id"]]}}
        original = self.trace.read_bytes()
        for status in ("failed", "stopped"):
            self.trace.write_bytes(original.replace(b"status: completed", ("status: " + status).encode()))
            with patch.object(control, "registered_benchmarks", return_value=registration):
                result = self.request(op="validate-workers", phase="h0")
            self.assertFalse(result["workers"][0]["pending"])
            self.assertFalse(result["workers"][0]["format_required"])
            self.assertIn("terminated", result["workers"][0]["reason"])
        self.trace.write_bytes(original)
        self.append_trace(child, [{"type": "model_msg", "payload": {"type": "text", "role": "user",
            "sender": "parent_agent", "text": "SYNTHETIC unregistered steering"}}])
        with patch.object(control, "registered_benchmarks", return_value=registration):
            result = self.request(op="validate-workers", phase="h0")
        self.assertFalse(result["workers"][0]["pending"])
        self.assertFalse(result["workers"][0]["format_required"])
        with self.assertRaisesRegex(ValueError, "unregistered follow-up"):
            self.request(op="format-request", subagent_id="subagent-12345678")

    def test_earlier_worker_commentary_and_text_fence_can_be_repaired_without_changing_result(self):
        mode, child = self.format_fixture()
        rows = [json.loads(line) for line in child.read_text().splitlines()]
        rows[-2]["payload"]["text"] = rows[-2]["payload"]["text"].replace("```yaml", "```text")
        rows[2:2] = [
            {"type": "event_msg", "payload": {"type": "request_begin"}},
            {"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": "SYNTHETIC early work explanation"}},
            {"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "arguments": "{}", "tool_call_id": "initial-work"}},
            {"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}},
        ]
        child.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        with patch.object(control, "registered_benchmarks", return_value={"test": {"case_ids": [self.worker_cell["case_id"]]}}):
            result = self.request(op="validate-workers", phase="h0")
        self.assertTrue(result["workers"][0]["format_required"])
        request = self.request(op="format-request", subagent_id="subagent-12345678")
        self.assertEqual(self.tool("input_subagent", request["call"]), {})
        self.finish_format(child, request)
        control.verify_format_repairs(mode)

    def test_candidate_namespaces_and_limits_reach_fresh_role_prompts(self):
        solver = control.evo.native_prompt(self.request(op="role-request", role="solver", task_id="train_001")["call"])
        self.assertIn("train_001-attempt-1-c01", solver)
        self.assertIn("In-session reflection", solver)
        self.assertIn('"evo_role_seal"', solver)
        self.assertIn(str(self.private / "roles/train_001/requests/seal.json"), solver)
        self.assertNotIn(str(self.private / "roles/reflector.md"), solver)
        with self.assertRaisesRegex(ValueError, "no independent Reflector"):
            self.request(op="role-request", role="reflector", task_id="train_001")
        contexts = [{"task_id": task, "attempt_id": "attempt-1", "negative": False,
                     "feedback": {"source": "penguin-evaluation"},
                     "reflection": {"mode": "solver-session", "status": "not-required"}}
                    for task in self.profile["train_ids"]]
        with patch.object(control, "completed_context", side_effect=lambda t, task, parent: next(c for c in contexts if c["task_id"] == task)):
            contexts[0]["negative"] = True
            with self.assertRaisesRegex(ValueError, "negative task requires"):
                self.request(op="role-request", role="evolver")
            contexts[0]["negative"] = False
            evolver = control.evo.native_prompt(self.request(op="role-request", role="evolver")["call"])
            self.assertIn('"limits": {"general": 5, "topic": 5}', evolver)
            self.assertIn('"max_bytes": 16000', evolver)
            self.assertIn('"op": "validate-decisions"', evolver)
            self.assertIn("UTF-8", evolver)

    def test_reflection_must_be_same_native_solver_and_not_parent_supplied(self):
        binding = {"session_id": "original-solver"}
        audit = {"op": "audit-role", "role": "reflector", "task_id": "train_001",
                 "binding": binding, "traces": ["terminal.jsonl"], "require_role_seal": True}
        context = {"task_id": "train_001", "negative": True,
                   "trace": {"binding": binding, "sources": [{"path": "terminal.jsonl"}]},
                   "reflection": {"mode": "solver-session", "status": "sealed", "audit_request": audit}}
        self.assertEqual(control.reflection_audits([context]), [audit])
        for replacement in ({**audit, "binding": {"session_id": "new-reflector"}},
                            {**audit, "traces": ["earlier.jsonl"]},
                            {**audit, "task_id": "train_002"}):
            with self.assertRaisesRegex(ValueError, "original Solver reflection"):
                control.reflection_audits([{**context, "reflection": {**context["reflection"], "audit_request": replacement}}])
        with self.assertRaisesRegex(ValueError, "not caller input"):
            self.request(op="role-request", role="evolver", reflectors=[audit])

    def test_training_input_subagent_only_polls_and_does_not_accept_prompt_file(self):
        self.assertEqual(self.tool("input_subagent", {"subagent_id": "original", "prompt": ""}), {})
        for args in ({"subagent_id": "original", "prompt": "Reflect now"},
                     {"subagent_id": "original", "prompt_file": "/tmp/reflection.txt"},
                     {"subagent_id": "original", "prompt_sha256": "a" * 64}):
            with self.subTest(args=args):
                self.assertEqual(self.tool("input_subagent", args)["decision"], "deny")

    def test_full_sequential_batch_same_solver_reflections_compiles_exports_and_seals(self):
        self.full_sequential_fixture(negative_count=2)

    def test_full_successful_batch_still_runs_one_evolver_and_legitimate_empty_export(self):
        self.full_sequential_fixture(negative_count=0)

    def test_negative_batch_can_seal_justified_empty_candidates_without_skipping_reflection(self):
        self.full_sequential_fixture(negative_count=2, empty_candidates=True)

    def full_sequential_fixture(self, negative_count, empty_candidates=False):
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
                     "stop_reason": "completed", "output": json.dumps(output)}}]
        def write_observation(path, value):
            self.write(path, value)
            return pair("write_file", "written", {"file_path": str(path), "content": json.dumps(value)})
        def reads(materials, request_dir=None):
            rows = []
            for material in materials:
                for offset in range(0, max(1, len(Path(material).read_text())), 4000):
                    request = {"op": "read-material", "path": str(material), "offset": offset}
                    name = evo.digest((str(material) + str(offset)).encode())[:16]
                    request_path = (request_dir or self.private / "requests") / ("read-" + name + ".json")
                    rows += write_observation(request_path, request)
                    rows += pair("exec_command", evo.material(request), {"cmd": f"python3 -B {helper} {request_path}"})
            return rows
        def delegate(prepared, session_id):
            call_id = "dispatch-" + session_id
            evo.journal({**reserve, **{key: prepared[key] for key in ("operation_id", "slot", "request_sha256")}})
            self.assertEqual(self.tool("run_subagent", prepared["call"], call_id), {})
            self.append_trace(self.trace, [
                {"type": "model_msg", "payload": {"type": "tool_call", "name": "run_subagent", "tool_call_id": call_id,
                    "arguments": json.dumps(prepared["call"])}},
                {"type": "event_msg", "payload": {"type": "subagent", "session_id": session_id}},
                {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call_id,
                    "output": f"[subagent running in background with subagent_id subagent-{session_id}; completion will arrive]"}},
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
                {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": control.evo.native_prompt(prepared["call"])}}]
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
            inputs = [str(self.workspace / "train_tasks" / task / "instruction.md"), str(self.workspace / "environment_access.md")]
            rows = [{"type": "session_meta", "payload": binding},
                    {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": control.evo.native_prompt(prepared["call"])}}]
            rows += h0_read_rows(binding, self.profile["max_bytes"])
            rows += reads([str(self.private / "roles/solver.md"), *inputs])
            trace = self.trace.parent / (binding["session_id"] + "_001.jsonl")
            active = pair("exec_command", {}, {"cmd": generated["command"]})
            self.append_trace(trace, rows + [active[0]])
            with patch.object(evo, "__file__", str(helper)), patch.dict(os.environ, PENGUIN_SESSION_ID=binding["session_id"]):
                with patch.object(evo.urllib.request, "build_opener") as opener:
                    response = opener.return_value.open.return_value.__enter__.return_value
                    response.status = 200
                    response.read.return_value = json.dumps({"score": 0.5 if n <= negative_count else 1, "correct": n > negative_count}).encode()
                    feedback = evo.judge(request)
                    self.assertEqual(opener.return_value.open.call_count, 1)
            active[1]["payload"]["output"] = json.dumps(evo.judge_stdout(feedback))
            self.append_trace(trace, [active[1]] + reads([paths["output"]], self.private / "roles" / task / "requests"))
            if n <= negative_count:
                candidate_path = self.private / "roles" / task / "candidates.json"
                values = [{"id": task + "-attempt-1-c01", "task_id": task, "attempt_id": "attempt-1",
                    "lesson": "SYNTHETIC inspect all required fields before submission", "trigger": "SYNTHETIC incomplete record",
                    "scope_hint": "SYNTHETIC documents", "evidence": [{"kind": "feedback", "claim": "Partial aggregate feedback", "epistemic": "observed"}]}]
                if empty_candidates:
                    values = []
                self.append_trace(trace, write_observation(candidate_path, values))
                seal_request = {"op": "seal-role-output", "role": "reflector", "task_id": task}
                seal_path = candidate_path.parent / "requests/seal.json"
                self.append_trace(trace, write_observation(seal_path, seal_request))
                active = pair("exec_command", {}, {"cmd": f"python3 -B {helper} {seal_path}"})
                self.append_trace(trace, [active[0]])
                with patch.object(evo, "__file__", str(helper)), patch.dict(os.environ, PENGUIN_SESSION_ID=binding["session_id"]):
                    sealed_reflection = evo.execute(seal_request, str(seal_path))
                self.assertTrue(sealed_reflection["complete"])
                active[1]["payload"]["output"] = json.dumps(sealed_reflection)
                self.append_trace(trace, [active[1]])
            self.append_trace(trace, [{"type": "model_msg", "payload": {"type": "text", "role": "assistant", "text": "Synthetic Solver complete."}},
                {"type": "event_msg", "payload": {"type": "request_end", "status": "completed"}}])
            self.append_trace(self.trace, [{"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "harness",
                "text": f"[background_task_done]\nkind: subagent\nid: subagent-{binding['session_id']}\nstatus: completed\n[/background_task_done]"}}])
            output = self.private / "contexts" / (task + ".json")
            capture = {"op": "capture", "split": "train", "task_id": task, "attempt_id": "attempt-1", "train_ids": self.profile["train_ids"],
                       "answer": paths["answer"], "feedback": paths["output"], "judge_request": generated["judge_request"],
                       "inputs": inputs, "traces": [str(trace)], "binding": binding, "output": str(output)}
            receipt = self.request(op="prepare-capture", task_id=task)
            self.assertEqual(evo.read(receipt["request_path"]), capture)
            self.assertFalse(output.exists())
            self.assertEqual(self.tool("exec_command", {"cmd": receipt["command"]}), {})
            evo.capture(evo.read(receipt["request_path"]))
            contexts.append(str(output))
            if n <= negative_count:
                roles.append(evo.read(output)["reflection"]["audit_request"])
        evolver = self.request(op="role-request", role="evolver")
        evolved_binding = delegate(evolver, "fixture-evolver-batch-1")
        self.assertIn('"max_bytes": 16000', control.evo.native_prompt(evolver["call"]))
        candidates = self.private / "candidates.json"
        decisions = self.private / "decisions.json"
        ids = [candidate["id"] for candidate in evo.read(candidates)]
        self.assertEqual(len(set(ids)), 0 if empty_candidates else negative_count)
        operations = [{"op": "ADD", "candidate_ids": ids, "reason": "SYNTHETIC supported procedure", "entry": {
            "id": "fixture-field-review", "level": "general", "description": "Review publicly required form fields.", "trigger": "SYNTHETIC required-field task", "scope": "SYNTHETIC forms",
            "body": "Before submission, compare the public required fields with the current document and fill any omitted public fields."}}]
        self.write(decisions, operations if ids else [])
        before_decisions = decisions.read_bytes()
        checked = evo.execute({"op": "validate-decisions", "train_ids": self.profile["train_ids"], "contexts": contexts,
            "candidates": str(candidates), "decisions": str(decisions), "library": str(self.private / "prebatch-library.json"),
            "limits": self.profile["limits"], "max_bytes": 16000})
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
        evo.export_skill({**compile_request, "library": str(compiled), "output": str(skill), "max_bytes": 16000})
        seal = {**compile_request, "op": "seal-training", "private_learning": str(self.private), "skill": str(skill),
            "roles": roles, "runtime": self.runtime, "max_bytes": 16000, "output": str(self.private / "training-export.json")}
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
        for index, role in enumerate(roles[-1:]):
            path = self.private / "requests" / f"completed-audit-{index}.json"
            self.write(path, {"op": "audit-role", **role})
            role_requests.append(str(path))
        receipt = self.request(op="prepare-seal-training", role_requests=role_requests)
        seal = evo.read(receipt["request_path"])
        self.assertEqual(self.tool("exec_command", {"cmd": receipt["command"]}), {})
        sealed = evo.seal_training(seal)
        self.assertEqual(sealed["runtime"], self.runtime)
        self.assertEqual(len(sealed["members"]), 5)
        self.assertEqual(len(sealed["roles"]), negative_count + 1)
        self.assertEqual(evo.validate_package(skill)["empty"], not bool(ids))
        ledger = evo.read(journal)
        self.assertEqual(len(ledger["operations"]), 7)
        self.assertFalse(any(op["slot"].startswith("reflector/") for op in ledger["operations"].values()))
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
