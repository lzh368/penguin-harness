"""Guarded training H0 observations, not merely instructions; no model/network."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_control

control = test_control.control
evo = control.evo


class SolverSkillTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_control.ControlTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.capture_preparation_fixture()
        self.trace = self.f["traces"][0]
        self.rows = [json.loads(row) for row in self.trace.read_text().split("\n") if row]
        self.h0 = copy.deepcopy(self.rows[2:4])
        self.assertEqual(self.h0[0]["payload"]["name"], "read_file")
        self.target = json.loads(self.h0[0]["payload"]["arguments"])["file_path"]
        self.raw = evo.render_skill([], 16000)
        self.feedback = evo.read(self.f["paths"]["output"])
        self.feedback_bytes = Path(self.f["paths"]["output"]).read_bytes()
        self.request = {"op": "capture", "split": "train", "task_id": "train_001", "attempt_id": "attempt-1",
            "train_ids": self.fixture.profile["train_ids"], "answer": self.f["paths"]["answer"],
            "feedback": self.f["paths"]["output"], "judge_request": self.f["paths"]["judge_request"],
            "inputs": self.f["inputs"], "traces": [str(p) for p in self.f["traces"]],
            "binding": self.f["binding"], "output": str(self.fixture.root / "captured.json")}

    def write(self, rows):
        self.trace.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))

    def pair(self, name, args, output="", ident="preparation"):
        return [{"type": "model_msg", "payload": {"type": "tool_call", "name": name,
                "tool_call_id": ident, "arguments": json.dumps(args)}},
            {"type": "model_msg", "payload": {"type": "tool_call_output", "tool_call_id": ident,
                "stop_reason": "completed", "output": output}}]

    def assert_rejected(self, rows, pattern):
        self.write(rows)
        with patch.object(evo.urllib.request, "build_opener") as opener:
            with self.assertRaisesRegex(ValueError, pattern):
                evo.capture(self.request)
            opener.assert_not_called()
        self.assertFalse(Path(self.request["output"]).exists())
        self.assertEqual(Path(self.f["paths"]["output"]).read_bytes(), self.feedback_bytes)

    def test_missing_truncated_foreign_or_failed_h0_read_rejects_before_capture(self):
        variants = [self.rows[:2] + self.rows[4:]]
        for kind in ("truncated", "foreign", "failed", "different_bytes"):
            rows = copy.deepcopy(self.rows)
            if kind == "truncated": rows[3]["payload"]["output"] = rows[3]["payload"]["output"][:150]
            if kind == "foreign": rows[2]["payload"]["arguments"] = json.dumps({"file_path": self.target + ".other"})
            if kind == "failed": rows[3]["payload"]["stop_reason"] = "aborted"
            if kind == "different_bytes": rows[3]["payload"]["output"] += "\nExtra rule"
            variants.append(rows)
        for rows in variants:
            with self.subTest(variant=len(rows), sha=evo.digest(evo.encoded(rows))):
                self.assert_rejected(rows, "frozen H0 Skill was not fully observed")

    def test_business_write_or_wrong_material_before_complete_observation_rejects(self):
        operations = [
            ("exec_command", {"cmd": "curl http://business.invalid/api/records"}, "read-only inspection"),
            ("write_file", {"file_path": self.f["paths"]["answer"], "content": "{}"}, "task actions"),
            ("read_file", {"file_path": str(self.fixture.skill.parent / "other-method/training.json")}, "only granted public"),
            ("read_file", {"file_path": str(self.fixture.workspace / "train_tasks/train_002/instruction.md")}, "only granted public"),
            ("exec_command", {"cmd": "cat " + str(self.fixture.workspace / "train_tasks/train_002/input/other.json")}, "nonpublic files"),
        ]
        for name, args, pattern in operations:
            with self.subTest(name=name, args=args):
                self.assert_rejected(self.rows[:2] + self.pair(name, args) + self.rows[2:], pattern)
        # A parallel write between read request and observed output is still early.
        self.assert_rejected(self.rows[:3] + self.pair("write_file", {"file_path": "/tmp/answer", "content": "x"}) + self.rows[3:], "task actions")

    def test_exact_native_agents_instructions_may_precede_frozen_skill(self):
        path = str(Path(self.target).parent.parent.parent / "AGENTS.md")
        for name, args in (("read_file", {"file_path": path}), ("exec_command", {"cmd": "cat " + path})):
            self.write(self.rows[:2] + self.pair(name, args, "SYNTHETIC native instructions") + self.rows[2:])
            result = evo.capture(self.request)
            evo.validate_contexts([evo.read(result["path"])], ["train_001"])
            Path(result["path"]).unlink()

    def test_granted_public_inspection_and_complete_pages_succeed(self):
        root = self.fixture.workspace / "train_tasks/train_001/input"
        inspection = self.pair("exec_command", {"cmd": f"find {root} -type f | sort | head -n 20"})
        inspection += self.pair("read_file", {"file_path": self.f["inputs"][0]}, "Synthetic public read", "public")
        lines = self.raw.decode().split("\n")[:-1]
        pages = []
        for start in range(0, len(lines), 7):
            end = min(len(lines), start + 7)
            out = [f"{n:6}\t{line}" for n, line in enumerate(lines[start:end], start + 1)]
            if end < len(lines):
                out += [f"[file has {len(lines)} lines total; showing {start + 1}-{end} — call again with offset to continue]"]
            pages += self.pair("read_file", {"file_path": self.target, "offset": start + 1, "limit": 7}, "\n".join(out), f"page-{start}")
        self.write(self.rows[:2] + inspection + pages + self.rows[4:])
        result = evo.capture(self.request)
        self.assertTrue(Path(result["path"]).is_file())
        context = evo.read(result["path"])
        evo.validate_contexts([context], ["train_001"])
        self.assertEqual(context["feedback"], self.feedback)

    def test_exact_foreground_cat_succeeds_but_pipeline_or_wrapped_output_does_not(self):
        exact = self.pair("exec_command", {"cmd": "cat " + self.target}, self.raw.decode(), "cat-h0")
        for command, output in (("cat " + self.target + " | head -n 2000", self.raw.decode()),
                                ("cat " + self.target, "exit code 0\n" + self.raw.decode())):
            self.assert_rejected(self.rows[:2] + self.pair("exec_command", {"cmd": command}, output, "cat-h0") + self.rows[4:], "not fully observed")
        self.write(self.rows[:2] + exact + self.rows[4:])
        evo.capture(self.request)

    def test_finite_combo_completes_read_before_later_normal_shell_commands(self):
        command = "ls -la " + str(Path(self.target).parent.parent) + "/ && echo '---' && cat " + self.target
        observed = self.pair("exec_command", {"cmd": command}, "total 0\n---\n" + self.raw.decode(), "combined-h0")
        # 2>&1 remains outside the pre-read grammar; after the completed H0
        # read this ordinary task inspection is not incorrectly reclassified.
        later = self.pair("exec_command", {"cmd": "ls /app/input 2>&1"}, "SYNTHETIC input files", "later-inspection")
        self.write(self.rows[:2] + observed + later + self.rows[4:])
        context = evo.read(evo.capture(self.request)["path"])
        evo.validate_contexts([context], ["train_001"])
        self.assertEqual(context["feedback"], self.feedback)

    def test_complete_reader_prefix_followed_by_discovery_survives_capture(self):
        command = "cat " + self.target + '; echo "-----"; ls -R ' + str(Path(self.target).parent.parent)
        observed = self.pair("exec_command", {"cmd": command}, self.raw.decode() + "-----\nSYNTHETIC Skill names\n", "prefix-h0")
        self.write(self.rows[:2] + observed + self.rows[4:])
        context = evo.read(evo.capture(self.request)["path"])
        evo.validate_contexts([context], ["train_001"])
        self.assertEqual(context["feedback"], self.feedback)

    def test_business_in_same_call_after_full_cat_is_still_before_model_observation(self):
        for suffix in ('; curl http://business.invalid/items', '; touch /app/answer'):
            observed = self.pair("exec_command", {"cmd": "cat " + self.target + suffix}, self.raw.decode(), "premature-business")
            self.assert_rejected(self.rows[:2] + observed + self.rows[4:], "not fully observed")

    def test_unrecognized_earlier_preparation_reports_specific_evidence_gap(self):
        command = 'for f in /app/input/payloads/*; do echo "=== $f ==="; cat "$f"; echo; done'
        earlier = self.pair("exec_command", {"cmd": command}, "SYNTHETIC public payload display", "unverified-preparation")
        self.write(self.rows[:2] + earlier + self.rows[2:])
        with self.assertRaisesRegex(ValueError, "cannot verify pre-read call unverified-preparation under the finite inspection protocol"):
            evo.capture(self.request)
        self.assertFalse(Path(self.request["output"]).exists())
        self.assertEqual(Path(self.f["paths"]["output"]).read_bytes(), self.feedback_bytes)

    def test_real_actions_before_combo_observation_remain_violations(self):
        command = "ls -la /app/input && echo '---' && cat " + self.target
        observed = self.pair("exec_command", {"cmd": command}, "total 0\n---\n" + self.raw.decode(), "combined-h0")
        for early in (self.pair("write_file", {"file_path": "/app/answer", "content": "x"}),
                      self.pair("exec_command", {"cmd": "curl http://local-service/items"}),
                      self.pair("exec_command", {"cmd": "cat /state/private"})):
            with self.subTest(name=early[0]["payload"]["name"]):
                self.write(self.rows[:2] + early + observed + self.rows[4:])
                with self.assertRaises(ValueError): evo.capture(self.request)
                self.assertFalse(Path(self.request["output"]).exists())
        self.assert_rejected(self.rows[:2] + observed[:1] + self.pair("write_file", {
            "file_path": "/app/answer", "content": "x"}) + observed[1:] + self.rows[4:], "task actions")

    def test_combo_images_and_noncompleted_output_do_not_prove_a_read(self):
        base = self.pair("exec_command", {"cmd": "pwd && cat " + self.target},
                         "/app\n" + self.raw.decode(), "combined-h0")
        for field, value in (("images", ["data:image/png;base64,AA=="]), ("stop_reason", "fatal")):
            changed = copy.deepcopy(base)
            changed[1]["payload"][field] = value
            self.assert_rejected(self.rows[:2] + changed + self.rows[4:], "not fully observed")

    def test_validate_contexts_independently_rechecks_h0_against_original_trace(self):
        evo.capture(self.request)
        context = evo.read(self.request["output"])
        self.write(self.rows[:2] + self.rows[4:])
        # Model a legacy/tampered context consistent with its raw trace: every
        # other evidence and judge check still succeeds; the new gate must not.
        context["trace"] = evo.trace_view(self.request["traces"], self.f["binding"])
        with self.assertRaisesRegex(ValueError, "frozen H0 Skill was not fully observed"):
            evo.validate_contexts([context], ["train_001"])
        self.assertEqual(Path(self.f["paths"]["output"]).read_bytes(), self.feedback_bytes)


if __name__ == "__main__":
    unittest.main()
