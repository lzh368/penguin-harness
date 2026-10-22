"""Synthetic native leaf seal fixtures; zero providers, model calls or judge traffic."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import unittest
from unittest.mock import patch

import test_evo as fixtures
TOOL, evo = fixtures.TOOL, fixtures.evo


class RoleSealTests(unittest.TestCase):
    def setUp(self):
        install = fixtures.install_training
        def native_install(root, train):
            old, training = install(root, train)
            helper = root / "native/agents/trainer/agent_state/skills/evo-harness-rsi-toolkit/scripts/evo.py"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(TOOL.read_bytes())
            helper.with_name("inspection.py").write_bytes(TOOL.with_name("inspection.py").read_bytes())
            training.update(guard_version=2, runtime={"provider": "fixture", "model_id": "no-model"},
                limits={"general": 5, "topic": 5}, max_bytes=16000)
            (helper.parent.parent / "training.json").write_bytes(evo.encoded(training))
            return helper, training
        self.batch = fixtures.BatchTests(methodName="runTest")
        with patch.object(fixtures, "install_training", native_install):
            self.batch.setUp()
        self.addCleanup(self.batch.doCleanups)
        self.root = self.batch.root
        self.helper = self.batch.helper
        self.state = self.helper.parents[3]
        self.profile = self.batch.training
        self.private = Path(self.profile["private_learning"])
        self.private.mkdir(exist_ok=True)
        for path in (self.helper.parent.parent / "training.json", self.private / "frozen-training.json"):
            path.write_bytes(evo.encoded(self.profile))
        self.task = self.profile["train_ids"][0]
        self.contract = evo.role_contract(self.profile, "reflector", self.task)
        self.role = Path(self.contract["materials"][0]);self.role.parent.mkdir(parents=True)
        self.role.write_text("SYNTHETIC role instructions\n" * 500)
        context = Path(self.contract["materials"][1]);context.parent.mkdir(parents=True)
        context.write_bytes(Path(self.batch.contexts[0]).read_bytes())
        self.output = Path(self.contract["output"]);self.output.parent.mkdir(parents=True)
        candidate = copy.deepcopy(self.batch.candidates[0]);candidate["id"] = self.task + "-attempt-1-c01"
        self.output.write_bytes(evo.encoded([candidate]))
        self.session = "synthetic-role-leaf"
        self.parent_id = "synthetic-training-parent"
        self.parent = self.state.parent / "traces" / (self.parent_id + "_001.jsonl")
        self.parent.parent.mkdir()
        self.trace = self.parent.parent / (self.session + "_001.jsonl")
        prompt = json.dumps({"evo_role_seal": {**self.contract, "parent_session_id": self.parent_id}})
        call = {"prompt": prompt, "description": "SYNTHETIC reflector", "provider": "fixture", "model_id": "no-model"}
        ticket = {"session_id": self.parent_id, "phase": "reflector", "task_id": self.task,
                  "slot": "reflector/" + self.task + "/attempt-1", "call": call}
        dispatch = self.private / "dispatch";dispatch.mkdir()
        (dispatch / "ticket.json").write_bytes(evo.encoded(ticket))
        canonical = json.dumps(call, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        claim = {"tool_call_id": "parent-dispatch", "request_sha256": evo.digest(canonical.encode())}
        (dispatch / (evo.digest(ticket["slot"].encode()) + ".claimed.json")).write_bytes(evo.encoded(claim))
        self.binding = {"session_id": self.session, "source": "subagent", "provider": "fixture", "model_id": "no-model",
                        "agent_state": str(self.state), "workspace": str(self.root), "parent_trace": str(self.parent)}
        parent_rows = [{"type": "session_meta", "payload": {**self.binding, "session_id": self.parent_id, "source": "cli"}},
            {"type": "model_msg", "payload": {"type": "tool_call", "name": "run_subagent", "tool_call_id": "parent-dispatch", "arguments": json.dumps(call)}},
            {"type": "event_msg", "payload": {"type": "subagent", "session_id": self.session}}]
        self.parent.write_text(''.join(json.dumps(r) + '\n' for r in parent_rows))
        self.rows = [{"type": "session_meta", "payload": self.binding},
                     {"type": "model_msg", "payload": {"type": "text", "role": "user", "sender": "parent_agent", "text": prompt}}]
        self.counter = 0
        self.request = {"op": "seal-role-output", "role": "reflector", "task_id": self.task}
        self.request_path = self.private / "seal.json";self.request_path.write_bytes(evo.encoded(self.request))
        for item in (patch.object(evo, "__file__", str(self.helper)), patch.dict(os.environ, PENGUIN_SESSION_ID=self.session)):
            item.start();self.addCleanup(item.stop)

    def call(self, command):
        self.counter += 1
        return {"type": "model_msg", "payload": {"type": "tool_call", "name": "exec_command", "tool_call_id": "fixture-" + str(self.counter), "arguments": json.dumps({"cmd": command})}}

    def result(self, call, value):
        return {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": call["payload"]["tool_call_id"], "output": json.dumps(value)}}

    def flush(self):
        self.trace.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in self.rows))

    def read_all(self):
        for path in self.contract["materials"]:
            for off in range(0, len(Path(path).read_text()), 4000):
                call = self.call(shlex.join(["python3", "-B", str(self.helper), str(self.private / 'read.json')]))
                self.rows.extend([call, self.result(call, evo.material({"path": path, "offset": off}))])

    def seal(self):
        call = self.call(shlex.join(["python3", "-B", str(self.helper), str(self.request_path)]))
        self.rows.append(call);self.flush()
        try:
            value = evo.execute(self.request, str(self.request_path))
        except (ValueError, KeyError, OSError, TypeError) as error:
            self.rows.append(self.result(call, {"error": str(error)}));self.flush()
            raise
        self.rows.append(self.result(call, value));self.flush()
        return value

    def audit(self):
        return evo.audit_role({"role": "reflector", "task_id": self.task, "role_text_path": str(self.role),
            "materials": self.contract["materials"], "outputs": [str(self.output)], "binding": self.binding, "traces": [str(self.trace)]})

    def test_same_leaf_missing_reads_then_literal_recovery_and_real_seal(self):
        missing = self.seal()
        self.assertFalse(missing["complete"]);self.assertFalse(missing["sealed"])
        self.assertIn({"path": str(self.role), "offset": 0}, missing["missing_reads"])
        self.read_all()
        result = self.seal()
        self.assertTrue(result["complete"])
        self.assertEqual(result["file_path"], str(self.output))
        self.assertEqual(result["session_id"], self.session)
        self.assertTrue(self.audit()["complete"])

    def test_missing_read_diagnostics_remain_bounded_for_large_materials(self):
        self.role.write_text("SYNTHETIC " * 30000)
        missing = self.seal()
        self.assertFalse(missing["complete"])
        self.assertGreater(missing["missing_read_count"], 64)
        self.assertTrue(missing["more_missing_reads"])
        self.assertEqual(len(missing["missing_reads"]), 64)
        self.assertLess(len(json.dumps(missing)), 16000)

    def test_another_unpaired_action_is_never_ignored(self):
        self.read_all();self.rows.append(self.call("echo unfinished"))
        with self.assertRaisesRegex(ValueError, "only the current seal"):
            self.seal()

    def test_bound_role_output_structure_is_checked_before_sealing(self):
        self.read_all();self.output.write_text('[{"id":"not-a-valid-candidate"}]')
        with self.assertRaises((ValueError, KeyError)):
            self.seal()

    def test_same_leaf_repairs_only_its_unsealed_draft_then_seals(self):
        self.read_all();original=self.output.read_bytes()
        self.output.write_text('[{"id":"invalid"}]')
        with self.assertRaises((ValueError, KeyError)):
            self.seal()
        self.output.write_bytes(original)
        self.assertTrue(self.seal()["complete"])
        self.assertTrue(self.audit()["complete"])

    def test_a_successful_seal_cannot_be_silently_replaced(self):
        self.read_all();self.assertTrue(self.seal()["complete"])
        candidate=json.loads(self.output.read_text())
        candidate[0]["lesson"] += " Changed after sealing."
        self.output.write_bytes(evo.encoded(candidate))
        with self.assertRaisesRegex(ValueError, "changed after successful"):
            self.seal()
        with self.assertRaisesRegex(ValueError, "first sealed"):
            self.audit()

    def test_file_digest_cannot_replace_guarded_seal(self):
        self.read_all()
        call = self.call(shlex.join(["python3", "-B", str(self.helper), str(self.private / 'digest.json')]))
        self.rows.extend([call, self.result(call, {"file_path": str(self.output), "sha256": evo.digest(self.output.read_bytes())})]);self.flush()
        with self.assertRaisesRegex(ValueError, "sealed"):
            self.audit()

    def test_foreign_session_or_request_scope_is_rejected(self):
        with patch.dict(os.environ, PENGUIN_SESSION_ID="another-session"):
            with self.assertRaisesRegex(ValueError, "no original trace"):
                self.seal()
        with self.assertRaisesRegex(ValueError, "accepts only"):
            evo.execute({**self.request, "materials": []}, str(self.request_path))

    def test_variable_or_batch_reads_are_not_certified_by_new_contract(self):
        self.read_all()
        for row in self.rows:
            if row.get("payload", {}).get("type") == "tool_call":
                row["payload"]["arguments"] = json.dumps({"cmd": "H=" + str(self.helper) + "\npython3 -B $H " + str(self.private / 'read.json')})
        self.assertTrue(self.seal()["missing_reads"])

    def test_single_literal_command_allows_outer_whitespace_but_no_composition(self):
        command = shlex.join(["python3", "-B", str(self.helper), str(self.request_path)])
        for suffix in ("", "\n", "  "):
            call=self.call("  " + command + suffix)
            self.assertEqual(evo.standalone_helper_request({"call":call}, {"binding":self.binding}), str(self.request_path))
        for suffix in ("; echo copied", "\necho copied", " | cat", " &"):
            self.assertIsNone(evo.standalone_helper_request({"call":self.call(command+suffix)}, {"binding":self.binding}))

    def test_actual_batch_truncation_shape_has_missing_json_receipts(self):
        text = '"' * 96000
        path = '/synthetic/escaped-context.json';finger=evo.digest(text.encode())
        values=[{"material_path":path,"sha256":finger,"offset":off,"end":off+4000,"total":len(text),"text":text[off:off+4000]} for off in range(0,len(text),4000)]
        raw=''.join(json.dumps(v)+'\n'for v in values)
        bounded=raw[:52096]+f'\n[output truncated: kept first 52096 and last 52096 of {len(raw)} chars]\n'+raw[-52096:]
        parsed=list(evo.json_values(bounded))
        self.assertLess(len(parsed),len(values))
        call=self.call('for off in $(seq 0 4000 92000); do python3 -B '+str(self.helper)+' /synthetic/request.json; done')
        view={"binding":self.binding,"pairs":[{"call":call,"observation":{"payload":{"output":bounded}}}]}
        _,_,missing=evo.material_read_status(view,[(path,text,finger)])
        self.assertTrue(missing)
        _,_,strict=evo.material_read_status(view,[(path,text,finger)],standalone=True)
        self.assertEqual(len(strict),24)

if __name__ == '__main__':
    unittest.main()
