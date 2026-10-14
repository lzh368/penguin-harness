"""Offline synthetic evidence: never benchmark data, real traces or model calls."""
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_harbor_result as fixtures
from test_harbor_result import ROOT, converter, module, sha

trace = module("trace_binding")
builder = module("harbor_receipt")


class RuntimeEvidenceTest(unittest.TestCase):
    setUp = fixtures.HarborResultTest.setUp
    write = fixtures.HarborResultTest.write

    def prepare(self):
        self.block = trace.BEGIN + "\nsynthetic skill\n" + trace.END
        self.receipt["schema_version"] = 2
        for binding in self.receipt["state_binding"].values():
            binding["injected_block_sha256"] = sha(self.block.encode())
        binding = self.receipt["state_binding"]["expected"]
        self.payload = {"session_id": "inner-penguin-session", "provider": "deepseek",
                        "model_id": "deepseek-flash", "source": "cli",
                        "agent_state": binding["container_agent_state"], "workspace": "/app",
                        "system_prompt": "synthetic prefix\n" + self.block}
        self.traces = self.trial / "agent/penguin/traces/wiki-target"
        self.traces.mkdir(parents=True)
        self.shard = self.traces / "root-000.jsonl"
        self.put_shard(self.shard)
        self.write()
        self.observe()

    def put_shard(self, path, payload=None, extra=None):
        records = [{"type": "session_meta", "payload": payload or self.payload}]
        records += extra or []
        path.write_text("\n".join(json.dumps(row) for row in records) + "\n")

    def observe(self):
        raw = builder.encoded(trace.observe(self.result_path))
        (self.private / "runtime-observation.json").write_bytes(raw)
        self.receipt["runtime_evidence"] = {"artifact": "runtime-observation.json", "sha256": sha(raw)}
        self.write()

    def test_all_root_shards_are_recomputed_and_no_prompt_is_exported(self):
        self.prepare()
        self.put_shard(self.traces / "root-001.jsonl")
        self.observe()
        observed = json.loads((self.private / "runtime-observation.json").read_bytes())
        self.assertEqual(len(observed["root_shards"]), 2)
        self.assertNotIn("synthetic skill", json.dumps(observed))
        self.assertEqual(converter.convert(self.result_path, self.receipt_path)["score"], Decimal("75.56"))

    def test_legacy_requires_explicit_historical_mode(self):
        self.write()
        with self.assertRaisesRegex(ValueError, "legacy"):
            converter.convert(self.result_path, self.receipt_path)
        self.assertEqual(converter.convert(self.result_path, self.receipt_path, True)["status"], "ok")

    def test_copied_expected_hash_cannot_pass_wrong_injection(self):
        self.prepare()
        self.payload["system_prompt"] = self.block.replace("synthetic skill", "wrong skill")
        self.put_shard(self.shard)
        self.observe()  # even a newly hashed observation must match the frozen binding
        with self.assertRaisesRegex(ValueError, "binding mismatch"):
            converter.convert(self.result_path, self.receipt_path)

    def test_wrong_second_shard_model_is_rejected(self):
        self.prepare()
        self.put_shard(self.traces / "root-001.jsonl", {**self.payload, "model_id": "other"})
        with self.assertRaises(ValueError):
            trace.observe(self.result_path)

    def test_changed_deleted_or_added_shard_invalidates_receipt(self):
        self.prepare()
        self.put_shard(self.traces / "root-001.jsonl")
        self.observe()
        self.shard.unlink()
        with self.assertRaises(ValueError):
            converter.convert(self.result_path, self.receipt_path)
        self.put_shard(self.shard)
        self.put_shard(self.traces / "root-002.jsonl")
        with self.assertRaises(ValueError):
            converter.convert(self.result_path, self.receipt_path)

    def test_duplicate_missing_reversed_injection_blocks_fail(self):
        self.prepare()
        for prompt in ("no marker", self.block * 2, trace.END + trace.BEGIN):
            self.put_shard(self.shard, {**self.payload, "system_prompt": prompt})
            with self.assertRaises(ValueError):
                trace.observe(self.result_path)

    def test_forwarded_meta_cannot_impersonate_root(self):
        self.prepare()
        self.shard.write_text(json.dumps({"type": "session_meta", "origin": "parent",
                                         "payload": self.payload}) + "\n")
        with self.assertRaises(ValueError):
            trace.observe(self.result_path)

    def test_child_export_required_but_child_prompt_is_not_root_prompt(self):
        self.prepare()
        child = {**self.payload, "session_id": "child", "source": "subagent", "system_prompt": "child instructions"}
        self.put_shard(self.shard, extra=[{"type": "event_msg", "payload": {"type": "subagent", "session_id": "child"}}])
        with self.assertRaises(ValueError):
            trace.observe(self.result_path)
        self.put_shard(self.traces / "child-000.jsonl", child)
        self.observe()
        self.assertEqual(converter.convert(self.result_path, self.receipt_path)["status"], "ok")

    def test_orphan_and_nested_missing_child_are_rejected(self):
        self.prepare()
        child = {**self.payload, "session_id": "child", "source": "subagent"}
        child_path = self.traces / "child-000.jsonl"
        self.put_shard(child_path, child)
        with self.assertRaisesRegex(ValueError, "unrelated"):
            trace.observe(self.result_path)
        pointer = lambda sid: {"type": "event_msg", "payload": {"type": "subagent", "session_id": sid}}
        self.put_shard(self.shard, extra=[pointer("child")])
        self.put_shard(child_path, child, [pointer("grandchild")])
        with self.assertRaisesRegex(ValueError, "no exported"):
            trace.observe(self.result_path)
        self.put_shard(child_path, child, [pointer("inner-penguin-session")])
        with self.assertRaisesRegex(ValueError, "cyclic"):
            trace.observe(self.result_path)

    def test_stop_hook_child_must_be_exported_and_is_not_an_orphan(self):
        self.prepare()
        hook = {"type": "event_msg", "payload": {"type": "hook", "hook": "stop",
                                                   "output": {"session_id": "hook-child"}}}
        self.put_shard(self.shard, extra=[hook])
        with self.assertRaisesRegex(ValueError, "no exported"):
            trace.observe(self.result_path)
        self.put_shard(self.traces / "hook.jsonl", {**self.payload, "session_id": "hook-child", "source": "subagent"})
        self.observe()
        self.assertEqual(converter.convert(self.result_path, self.receipt_path)["status"], "ok")

    def test_invalid_json_symlinks_and_hardlinks_refused(self):
        self.prepare()
        self.shard.write_text('{"type":"session_meta","type":"model_msg"}\n')
        with self.assertRaises(ValueError):
            trace.observe(self.result_path)
        self.put_shard(self.shard)
        link = self.traces / "bad.jsonl"
        link.symlink_to(self.shard)
        with self.assertRaises(ValueError):
            trace.observe(self.result_path)

    def builder_inputs(self):
        self.prepare()
        for name in ("runtime-observation.json", "receipt.json"):
            (self.private / name).unlink()
        binding = self.receipt["state_binding"]["expected"]
        values = {"request.json": self.request, "expected.json": binding,
                  "observed.json": {key: binding[key] for key in
                    ("archive_sha256", "deployed_state_sha256", "state_version")}}
        for name, value in values.items():
            (self.private / name).write_text(json.dumps(value))
        return (self.result_path, self.private, self.private / "request.json",
                self.private / "expected.json", self.private / "observed.json")

    def test_builder_requires_separate_state_measurement_and_refuses_overwrite(self):
        args = self.builder_inputs()
        output = builder.build(*args)
        self.assertEqual(output["schema_version"], 2)
        self.assertEqual(converter.convert(self.result_path, self.receipt_path)["status"], "ok")
        with self.assertRaises(ValueError):
            builder.build(*args)

    def test_builder_never_upgrades_unknown_audit_to_pass(self):
        args = self.builder_inputs()
        (self.private / "audit/network.json").write_text('{"check":"network","verdict":"unknown"}')
        with self.assertRaises(ValueError):
            builder.build(*args)
        self.assertFalse(self.receipt_path.exists())

    def test_plain_cli_does_not_write_plugin_bytecode(self):
        # --help imports the sibling helpers; no -B or environment workaround.
        result = subprocess.run([sys.executable, str(ROOT / "scripts/harbor_receipt.py"), "--help"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(list((ROOT / "scripts").rglob("*.pyc")))


if __name__ == "__main__":
    unittest.main()
