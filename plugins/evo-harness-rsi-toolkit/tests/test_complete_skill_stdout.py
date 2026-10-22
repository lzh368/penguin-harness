"""Finite complete-file read protocol; raw public Reader fixtures, no task answers."""
import copy
import hashlib
import json
from pathlib import Path
import shlex
import unittest

import test_evo

evo = test_evo.evo


class CompleteSkillStdoutTests(unittest.TestCase):
    def setUp(self):
        self.workspace = "/app"
        self.slot = "/state/skills/evo-harness-learned"
        self.target = self.slot + "/SKILL.md"
        self.raw = evo.render_skill([], 16000)

    def accepts(self, cmd, output=None, **options):
        call = {"name": "exec_command", "arguments": json.dumps({"cmd": cmd, **options})}
        return evo.inspection.complete_file_stdout(call, self.raw.decode() if output is None else output,
            self.raw, self.target, self.workspace, self.slot)

    def test_historical_exact_raw_outputs_pass_only_the_new_finite_protocol(self):
        path = Path(__file__).parent / "fixtures/010-full-read-stdout.json"
        for value in json.loads(path.read_text()):
            with self.subTest(label=value["label"]):
                self.assertNotEqual(value["execution_version"], value["review_protocol_version"])
                self.assertEqual(value["review_protocol_version"], "2026.10.22.7")
                raw = value["expected"].encode()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), value["expected_sha256"])
                slot = value["state"] + "/skills/evo-harness-learned"
                self.assertTrue(evo.inspection.complete_file_stdout(value["call"], value["observation"]["output"],
                    raw, slot + "/SKILL.md", value["workspace"], slot))
                for broken in (value["observation"]["output"][:-1], value["observation"]["output"] + "extra\n",
                               "[output truncated: kept first 2 and last 4 of 99999 chars]\n" + value["observation"]["output"]):
                    self.assertFalse(evo.inspection.complete_file_stdout(value["call"], broken, raw,
                        slot + "/SKILL.md", value["workspace"], slot))

    def test_standalone_exact_and_finite_discovery_prefix(self):
        self.assertTrue(self.accepts("cat " + self.target))
        self.assertTrue(self.accepts("cat " + self.target + ";"))
        for cmd in ("pwd && cat " + self.target, "ls -la /state/skills/ && echo '---' && cat " + self.target,
                    "pwd -P && ls /app/input && echo && cat -- " + self.target):
            self.assertTrue(self.accepts(cmd, "/app\n---\n" + self.raw.decode()))
        self.assertTrue(self.accepts("ls /app/input && cat " + self.target))
        self.assertFalse(self.accepts("cat " + self.target, "unexpected\n" + self.raw.decode()))

    def test_redirect_pipe_or_background_dynamic_and_additional_reads_fail(self):
        tail = "cat " + self.target
        for cmd in (tail + " 2>/dev/null", "ls /app/input 2>&1 && " + tail, "pwd | " + tail,
                    "pwd || " + tail, "pwd; " + tail, "pwd && " + tail + ";", tail + " &", "ls && " + tail + " && pwd",
                    "cat /app/instruction.md && " + tail, tail + " && " + tail,
                    "echo 'cat ' && " + tail, "printf -- '---' && " + tail,
                    "echo " + shlex.quote(self.raw.decode()) + " && " + tail,
                    "pwd && cat $SKILL", "ls && cat /other/SKILL.md", "pwd && " + tail + " /app/instruction.md",
                    "python3 -c 'print(1)' && " + tail, "touch /app/answer && " + tail):
            with self.subTest(cmd=cmd[:100]):
                self.assertFalse(self.accepts(cmd, "prefix\n" + self.raw.decode()))
        self.assertFalse(self.accepts(tail, run_in_background=True))

    def test_fake_cat_or_echo_output_cannot_substitute_file_read(self):
        for cmd in ("echo '---'", "echo cat", "head -n 9999 " + self.target,
                    "cat /app/instruction.md", "ls " + self.target):
            with self.subTest(cmd=cmd): self.assertFalse(self.accepts(cmd))
        cmd = "pwd && cat " + self.target
        for output in (self.raw.decode() + self.raw.decode(), "prefix" + self.raw.decode(),
                       "prefix\n" + self.raw.decode()[:-5], "prefix\n" + self.raw.decode() + "\n",
                       "[output archived: /tmp/file]\n" + self.raw.decode()):
            with self.subTest(size=len(output)): self.assertFalse(self.accepts(cmd, output))

    def test_existing_public_permission_and_exact_context_file_set_still_apply(self):
        context = "/state/skills/evo-method-context/SKILL.md"
        call = {"name": "exec_command", "arguments": json.dumps({"cmd": "ls /state/skills && cat " + context})}
        self.assertFalse(evo.inspection.complete_file_stdout(call, self.raw.decode(), self.raw, context,
                         self.workspace, self.slot))
        self.assertTrue(evo.inspection.complete_file_stdout(call, self.raw.decode(), self.raw, context,
                        self.workspace, self.slot, skill_files={context}))
        call["arguments"] = json.dumps({"cmd": "cat /state/private && cat " + context})
        self.assertFalse(evo.inspection.complete_file_stdout(call, self.raw.decode(), self.raw, context,
                         self.workspace, self.slot, skill_files={context}))


if __name__ == "__main__":
    unittest.main()
