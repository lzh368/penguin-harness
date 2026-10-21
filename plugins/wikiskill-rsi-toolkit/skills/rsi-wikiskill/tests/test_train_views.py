"""Synthetic train-only learning inputs; no benchmark data or model calls.

Given a long trace, sampled calls retain their matching results.
Given reused call IDs in different sessions, missing counterparts stay unknown.
Given many long tool outputs, the task and delivery retain their own space.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from uuid import NAMESPACE_URL, uuid5

import test_runtime_evidence as runtime_fixtures
from test_harbor_result import converter, module, sha


def message(kind, role, **body):
    return {"type": "model_msg", "payload": {"type": kind, "role": role, **body}}


class TrainViewsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wikiskill-training-views-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cells = self.root / "cells"
        self.cells.mkdir()
        self.plan_path = self.root / "plan.json"
        self.sources_path = self.root / "sources.json"
        self.out = self.root / "raw" / "r0001"
        self.out.parent.mkdir()
        self.views = module("train_views")
        self.fixtures = []

    def matrix(self, count=1, rewards=None):
        tasks = module("gdpevo_profile").split_tasks()["train"]["tasks"][:count]
        plan = None
        for number, task in enumerate(tasks):
            fixture = runtime_fixtures.RuntimeEvidenceTest(methodName="runTest")
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            trial = fixture.trial.with_name(task["task_name"] + f"__fixture{number:04d}")
            fixture.trial.rename(trial)
            fixture.trial = trial
            fixture.result_path = trial / "result.json"
            fixture.request["case_id"] = task["case_id"]
            fixture.receipt.update(task_name=task["task_name"], trial_dir=trial.name,
                                   trial_id=str(uuid5(NAMESPACE_URL, f"train-view-{number}")))
            fixture.result.update(id=fixture.receipt["trial_id"], task_name=task["task_name"],
                                  trial_name=trial.name,
                                  task_id={"path": "/checkout/" + task["task_path"]})
            fixture.result["verifier_result"]["rewards"]["reward"] = (
                rewards[number] if rewards is not None else 0.5)
            fixture.plan["case_ids"] = [row["case_id"] for row in tasks]
            fixture.prepare()
            fixture.result["agent_result"]["metadata"]["session_id"] = f"root-{number:04d}"
            fixture.payload["session_id"] = f"root-{number:04d}"
            fixture.records = [message("text", "user", text=f"TASK-{number}: inspect synthetic records"),
                               message("tool_call", "assistant", name="exec_command",
                                       arguments='{"cmd":"inspect synthetic-input"}', tool_call_id="call-1"),
                               message("tool_call_output", "user", output="SYNTHETIC INPUT OBSERVED",
                                       tool_call_id="call-1", stop_reason="completed"),
                               message("text", "assistant", text="FINAL ARTIFACT WRITTEN")]
            fixture.put_shard(fixture.shard, extra=fixture.records)
            fixture.write()
            fixture.observe()
            self.fixtures.append(fixture)
            plan = fixture.plan
        self.plan = copy.deepcopy(plan)
        self.plan_path.write_text(json.dumps(self.plan))
        self.refresh()

    def refresh(self):
        sources = []
        for number, fixture in enumerate(self.fixtures):
            fixture.put_shard(fixture.shard, extra=fixture.records)
            fixture.observe()
            cell = converter.convert(fixture.result_path, fixture.receipt_path)
            (self.cells / f"{number:04d}.json").write_text(converter.cell_json(cell))
            sources.append({"case_id": fixture.request["case_id"], "run": 1,
                            "result": str(fixture.result_path), "receipt": str(fixture.receipt_path)})
        self.sources = sources
        self.sources_path.write_text(json.dumps(sources))

    def build(self, out=None, seed=0):
        return self.views.build(self.plan_path, self.cells, self.sources_path, out or self.out, seed=seed)

    def reject_without_output(self):
        with self.assertRaises((ValueError, OSError, TypeError, KeyError)):
            self.build()
        self.assertFalse(self.out.exists(), "invalid learning sources must not publish a partial export")

    def test_bound_export_preserves_raw_and_excludes_private_sources(self):
        self.matrix()
        manifest = self.build()
        self.assertEqual(manifest["phase"], "train")
        self.assertEqual(manifest["plan_sha256"], sha(self.plan_path.read_bytes()))
        self.assertEqual(manifest, json.loads((self.out / "manifest.json").read_text()))
        row = manifest["entries"][0]
        index = (self.out / row["entry"]).read_text()
        view = (self.out / row["view"]).read_text()
        self.assertIn("SYNTHETIC INPUT OBSERVED", view)
        self.assertIn("FINAL ARTIFACT WRITTEN", view)
        self.assertEqual(row["view_sha256"], sha((self.out / row["view"]).read_bytes()))
        self.assertEqual(len(row["raw_files"]), 1)
        raw = row["raw_files"][0]
        self.assertEqual(raw["session_id"], "root-0000")
        self.assertEqual((self.out / raw["path"]).read_bytes(), self.fixtures[0].shard.read_bytes())
        self.assertEqual(raw["sha256"], sha(self.fixtures[0].shard.read_bytes()))
        self.assertIn(raw["sha256"], index)
        for private in (str(self.fixtures[0].private), str(self.fixtures[0].result_path)):
            self.assertNotIn(private, json.dumps(manifest) + index + view)

    def test_large_metadata_reasoning_and_forwarded_messages_do_not_consume_view(self):
        self.matrix()
        fixture = self.fixtures[0]
        fixture.payload["system_prompt"] = "META-ONLY-SECRET" * 20_000 + fixture.block
        fixture.records = [
            message("thinking", "assistant", thinking="THINKING-ONLY-SECRET" * 5_000),
            message("text", "user", sender="harness", text="HARNESS-ONLY-CONTROL"),
            message("text", "user", sender="server", text="SERVER-ONLY-CONTROL"),
            message("partial_text", "assistant", text="PARTIAL-ONLY-TEXT"),
            {**message("text", "assistant", text="FORWARDED-ONLY-TEXT"), "origin": ["child-forward"]},
            {"type": "event_msg", "payload": {"type": "tool_list_ready", "tools": [{"name": "TOOL-DEFINITION-ONLY"}]}},
            *fixture.records,
        ]
        fixture.records[-1]["payload"]["fidelity"] = {"opaque": "FIDELITY-ONLY-SECRET"}
        self.refresh()
        manifest = self.build()
        view = (self.out / manifest["entries"][0]["view"]).read_text()
        for excluded in ("META-ONLY-SECRET", "THINKING-ONLY-SECRET", "HARNESS-ONLY-CONTROL",
                         "SERVER-ONLY-CONTROL", "PARTIAL-ONLY-TEXT", "FORWARDED-ONLY-TEXT",
                         "TOOL-DEFINITION-ONLY", "FIDELITY-ONLY-SECRET"):
            self.assertNotIn(excluded, view)
        self.assertIn("TASK-0", view)
        self.assertIn("SYNTHETIC INPUT OBSERVED", view)
        self.assertIn("FINAL ARTIFACT WRITTEN", view)
        self.assertLessEqual(len(view), 15_000)

    def test_sampled_calls_keep_their_results_across_shards(self):
        records = [message("text", "user", text="TASK REQUIREMENT")]
        outputs = []
        for i in range(60):
            records.append(message("tool_call", "assistant", name="inspect",
                                   arguments=f"CALL-{i:03d}", tool_call_id=f"id-{i}"))
            outputs.append(message("tool_call_output", "user",
                                   output=f"RESULT-{i:03d}", tool_call_id=f"id-{i}"))
        outputs.append(message("text", "assistant", text="DELIVERED ANSWER"))
        behavior = []
        for name, shard in (("first.jsonl", records), ("second.jsonl", outputs)):
            behavior.extend(self.views.behavior_records(
                "\n".join(json.dumps(r) for r in shard), name, "root"))
        text, _ = self.views.view(behavior, "synthetic", 1, "50", "root")
        retained = []
        for i in range(60):
            self.assertEqual(f"CALL-{i:03d}" in text, f"RESULT-{i:03d}" in text)
            if f"CALL-{i:03d}" in text:
                retained.append(i)
        self.assertGreater(len(retained), 1)
        self.assertLess(min(retained), 20)
        self.assertGreater(max(retained), 40)
        self.assertIn("TASK REQUIREMENT", text)
        self.assertIn("DELIVERED ANSWER", text)
        self.assertLessEqual(len(text), 15_000)

    def test_same_call_id_in_different_sessions_is_not_a_pair(self):
        a = message("tool_call", "assistant", name="inspect",
                    arguments="FIRST SESSION CALL", tool_call_id="shared-id")
        b = message("tool_call_output", "user", output="SECOND SESSION RESULT",
                    tool_call_id="shared-id")
        behavior = self.views.behavior_records(json.dumps(a), "a.jsonl", "root")
        behavior += self.views.behavior_records(json.dumps(b), "b.jsonl", "child")
        text, _ = self.views.view(behavior, "synthetic", 1, "50", "root")
        self.assertIn("FIRST SESSION CALL", text)
        self.assertIn("SECOND SESSION RESULT", text)
        self.assertEqual(text.count("| unpaired tool record"), 2)

    def test_task_and_delivery_are_not_squeezed_out_by_long_tool_outputs(self):
        task = "INPUT " * 160 + "IMPORTANT TASK CONDITION" + " INPUT" * 160
        answer = "VALUE " * 160 + "IMPORTANT DELIVERED VALUE" + " VALUE" * 160
        records = [message("text", "user", text=task)]
        for i in range(30):
            records.extend([
                message("tool_call", "assistant", name="inspect",
                        arguments="ARGS " * 1000, tool_call_id=str(i)),
                message("tool_call_output", "user", output="ROWS " * 1000,
                        tool_call_id=str(i)),
            ])
        records.append(message("text", "assistant", text=answer))
        behavior = self.views.behavior_records(
            "\n".join(json.dumps(r) for r in records), "a.jsonl", "root")
        text, _ = self.views.view(behavior, "synthetic", 1, "50", "root")
        self.assertIn(task, text)
        self.assertIn(answer, text)
        self.assertLessEqual(len(text), 15_000)

    def test_full_pinned_benchmark_batch_reaches_learning_with_original_bytes(self):
        self.matrix(count=24)
        for fixture in self.fixtures:
            fixture.payload["source"] = "benchmark"
        self.refresh()
        originals = {fixture.payload["session_id"]: fixture.shard.read_bytes() for fixture in self.fixtures}
        manifest = self.build()
        self.assertEqual(len(manifest["entries"]), 24)
        for entry in manifest["entries"]:
            raw = entry["raw_files"][0]
            self.assertEqual((self.out / raw["path"]).read_bytes(), originals[raw["session_id"]])
            self.assertEqual(raw["sha256"], sha(originals[raw["session_id"]]))
            view = (self.out / entry["view"]).read_text()
            self.assertIn("SYNTHETIC INPUT OBSERVED", view)
            self.assertIn("FINAL ARTIFACT WRITTEN", view)

    def test_long_behavior_is_marked_as_excerpt_and_keeps_final_action(self):
        self.matrix()
        fixture = self.fixtures[0]
        fixture.records.insert(2, message("tool_call_output", "user", tool_call_id="call-long",
                                         output="FIRST-EDGE " + "x" * 100_000 + " LAST-EDGE",
                                         stop_reason="fatal"))
        self.refresh()
        manifest = self.build()
        row = manifest["entries"][0]
        view = (self.out / row["view"]).read_text()
        self.assertLessEqual(len(view), 15_000)
        self.assertIn("FINAL ARTIFACT WRITTEN", view)
        self.assertGreater(row["view_stats"]["truncated_records"], 0)
        self.assertEqual(row["view_stats"]["behavior_records"], 5)
        self.assertEqual(row["view_stats"]["included_records"] + row["view_stats"]["omitted_records"], 5)
        self.assertRegex(view.lower(), r"excerpt|truncat|摘录|截断")
        self.assertEqual((self.out / row["raw_files"][0]["path"]).read_bytes(), fixture.shard.read_bytes())

    def test_compaction_internal_messages_are_not_task_behavior(self):
        self.matrix()
        fixture = self.fixtures[0]
        fixture.records[1:1] = [
            {"type": "event_msg", "payload": {"type": "compaction_begin", "reason": "context",
                                               "mode": "summarize", "context": 100_000, "turns": 5}},
            message("text", "user", text="COMPACTION-ONLY-PROMPT"),
            message("tool_call", "assistant", name="write_file", tool_call_id="summary-call",
                    arguments='{"path":"NEVER-DISPATCHED-FILE"}', stop_reason="completed"),
            message("tool_call_output", "user", tool_call_id="summary-call",
                    output="[tool error] the compaction request expects a summary, not tool calls",
                    stop_reason="fatal"),
            message("text", "assistant", text="COMPACTION-ONLY-SUMMARY"),
            {"type": "event_msg", "payload": {"type": "compaction_end", "reason": "context",
                                               "mode": "summarize", "status": "completed"}},
        ]
        self.refresh()
        manifest = self.build()
        row = manifest["entries"][0]
        view = (self.out / row["view"]).read_text()
        for excluded in ("COMPACTION-ONLY-PROMPT", "NEVER-DISPATCHED-FILE",
                         "the compaction request expects a summary", "COMPACTION-ONLY-SUMMARY"):
            self.assertNotIn(excluded, view)
        self.assertIn("TASK-0", view)
        self.assertIn("SYNTHETIC INPUT OBSERVED", view)
        self.assertIn("FINAL ARTIFACT WRITTEN", view)
        self.assertEqual(row["view_stats"]["behavior_records"], 4)
        self.assertEqual((self.out / row["raw_files"][0]["path"]).read_bytes(), fixture.shard.read_bytes())

    def test_success_failure_sampling_is_seeded_and_full_coverage_remains_visible(self):
        self.matrix(count=12, rewards=[1.0] * 4 + [0.5] * 8)
        a = self.build(seed=71)
        b = self.build(out=self.root / "raw" / "replayed", seed=71)
        self.assertEqual(a, b)
        self.assertEqual(len(a["entries"]), 12)
        selected = [row for row in a["entries"] if row["id"] in a["selected"]]
        self.assertEqual(len(selected), 8)
        self.assertEqual(sum(row["outcome"] == "failure" for row in selected), 5)
        self.assertEqual(sum(row["outcome"] == "success" for row in selected), 3)
        self.assertEqual({row["case_id"] for row in a["entries"]}, set(self.plan["case_ids"]))

    def test_only_full_score_is_success(self):
        self.matrix(count=3, rewards=[1, 0.9999, 0])
        manifest = self.build()
        self.assertEqual([row["outcome"] for row in manifest["entries"]], ["success", "failure", "failure"])

    def test_child_own_trace_is_exported_and_parent_forward_is_not_duplicated(self):
        self.matrix()
        fixture = self.fixtures[0]
        fixture.records.append({"type": "event_msg", "payload": {"type": "subagent", "session_id": "child-0"}})
        child_record = message("text", "assistant", text="CHILD SYNTHETIC OBSERVATION")
        fixture.records.append({**child_record, "origin": ["child-0"]})
        child_meta = {**fixture.payload, "session_id": "child-0", "source": "subagent"}
        fixture.put_shard(fixture.traces / "child-000.jsonl", child_meta, [child_record])
        self.refresh()
        manifest = self.build()
        row = manifest["entries"][0]
        self.assertEqual({raw["session_id"] for raw in row["raw_files"]}, {"root-0000", "child-0"})
        self.assertEqual((self.out / row["view"]).read_text().count("CHILD SYNTHETIC OBSERVATION"), 1)

    def test_long_child_cannot_displace_root_task_or_final_delivery(self):
        self.matrix()
        fixture = self.fixtures[0]
        fixture.records.append({"type": "event_msg", "payload": {"type": "subagent", "session_id": "long-child"}})
        child_meta = {**fixture.payload, "session_id": "long-child", "source": "subagent"}
        child_records = [message("text", "assistant", text=f"child-only intermediate observation {i}")
                         for i in range(200)]
        for prefix in ("aaa", "zzz"):
            with self.subTest(child_shard_order=prefix):
                child_path = fixture.traces / f"{prefix}-child.jsonl"
                fixture.put_shard(child_path, child_meta, child_records)
                try:
                    self.refresh()
                    out = self.out.with_name("child-" + prefix)
                    manifest = self.build(out=out)
                    row = manifest["entries"][0]
                    view = (out / row["view"]).read_text()
                    self.assertIn("TASK-0", view)
                    self.assertIn("FINAL ARTIFACT WRITTEN", view)
                    self.assertGreater(row["view_stats"]["omitted_records"], 0)
                    self.assertLessEqual(row["view_stats"]["included_records"], 24)
                    self.assertLessEqual(len(view), 15_000)
                finally:
                    child_path.unlink()

    def test_different_cells_cannot_share_a_child_session(self):
        self.matrix(count=2)
        for fixture in self.fixtures:
            fixture.records.append({"type": "event_msg", "payload": {"type": "subagent", "session_id": "shared-child"}})
            child_meta = {**fixture.payload, "session_id": "shared-child", "source": "subagent"}
            fixture.put_shard(fixture.traces / "shared-child.jsonl", child_meta,
                              [message("text", "assistant", text="shared child observation")])
        self.refresh()
        self.reject_without_output()

    def test_train_cells_must_share_accepted_state_snapshot(self):
        self.matrix(count=2)
        fixture = self.fixtures[1]
        original_binding = copy.deepcopy(fixture.receipt["state_binding"])
        original_prompt = fixture.payload["system_prompt"]
        for field in ("injected_block_sha256", "deployed_state_sha256", "archive_sha256"):
            with self.subTest(changed_snapshot_field=field):
                self.out = self.root / "raw" / field
                fixture.receipt["state_binding"] = copy.deepcopy(original_binding)
                fixture.payload["system_prompt"] = original_prompt
                replacement = sha(("different accepted snapshot: " + field).encode())
                if field == "injected_block_sha256":
                    different_block = fixture.block.replace("synthetic skill", "other accepted skill")
                    fixture.payload["system_prompt"] = "synthetic prefix\n" + different_block
                    replacement = sha(different_block.encode())
                for binding in fixture.receipt["state_binding"].values():
                    binding[field] = replacement
                fixture.write()
                # Each cell is individually valid under its own expected/observed
                # binding, and both still claim the same plan and State version.
                self.refresh()
                self.reject_without_output()

    def test_non_train_plan_is_refused(self):
        self.matrix()
        self.plan["phase"] = "validation"
        self.plan_path.write_text(json.dumps(self.plan))
        self.reject_without_output()

    def test_receipt_must_bind_exact_plan_bytes(self):
        self.matrix()
        self.plan_path.write_text(json.dumps(self.plan, indent=2))
        self.reject_without_output()

    def test_changed_cell_cannot_override_audited_score(self):
        self.matrix()
        path = self.cells / "0000.json"
        cell = json.loads(path.read_text())
        cell["score"] = 99.99
        path.write_text(json.dumps(cell))
        self.reject_without_output()

    def test_incomplete_or_duplicated_sources_are_refused(self):
        self.matrix(count=2)
        for sources in (self.sources[:1], [self.sources[0], self.sources[0]]):
            self.sources_path.write_text(json.dumps(sources))
            self.reject_without_output()

    def test_source_mismatch_is_refused_before_publishing(self):
        self.matrix(count=2)
        self.sources[1]["result"] = self.sources[0]["result"]
        self.sources_path.write_text(json.dumps(self.sources))
        self.reject_without_output()

    def test_source_symlink_and_old_output_are_refused(self):
        self.matrix()
        alias = self.root / "result.json"
        alias.symlink_to(self.fixtures[0].result_path)
        self.sources[0]["result"] = str(alias)
        self.sources_path.write_text(json.dumps(self.sources))
        self.reject_without_output()
        self.refresh()
        self.out.mkdir(parents=True)
        marker = self.out / "existing-evidence.txt"
        marker.write_text("do not overwrite")
        with self.assertRaises((ValueError, OSError)):
            self.build()
        self.assertEqual(marker.read_text(), "do not overwrite")


if __name__ == "__main__":
    unittest.main()
