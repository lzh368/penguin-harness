"""Single-file runtime contract: all rules, complete bytes, no selector."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import test_evo

evo = test_evo.evo


class FullSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-full-skill-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.entries = [{"id": f"method-{number}", "level": "general" if number < 3 else "topic",
            **({"topic": "synthetic-topic"} if number >= 3 else {}),
            "trigger": f"Synthetic task need {number}", "scope": "Authorized synthetic records only",
            "body": f"SYNTHETIC rule {number}: inspect the defined records, retain conditions, and report the computed result."}
            for number in range(6)]
        self.library = self.root / "library.json"
        self.save(self.entries)
        self.req = {"library": str(self.library), "limits": {"general": 5, "topic": 5},
                    "max_bytes": 16000, "output": str(self.root / "skill")}

    def save(self, entries):
        self.library.write_text(json.dumps(entries, ensure_ascii=False))

    def test_crlf_source_normalizes_deterministically_before_section_hashes(self):
        entries = copy.deepcopy(self.entries)
        entries[0]["body"] = "SYNTHETIC first step.\r\nSecond step retains its condition.\r\n"
        self.save(entries)
        original_library = self.library.read_bytes()
        receipt = evo.export_skill(self.req)
        raw = (Path(self.req["output"]) / "SKILL.md").read_bytes()
        canonical = copy.deepcopy(entries)
        canonical[0]["body"] = canonical[0]["body"].replace("\r\n", "\n")
        self.assertEqual(raw, evo.render_skill(canonical, 16000))
        self.assertNotIn(b"\r", raw)
        self.assertEqual(self.library.read_bytes(), original_library)
        self.assertEqual(evo.validate_package(Path(self.req["output"])), receipt)
        view = {"binding": {"workspace": "/app"}, "pairs": [{
            "call": {"payload": {"name": "read_file", "tool_call_id": "read", "arguments": json.dumps({"file_path": "/state/SKILL.md"})}},
            "observation": {"payload": {"stop_reason": "completed", "output": "\n".join(
                f"{n:6}\t{line}" for n, line in enumerate(raw.decode().split("\n")[:-1], 1))}}}]}
        self.assertIsNotNone(evo.native_file_read(view, "/state/SKILL.md", raw.decode()))
        # Frozen package bytes must already be canonical. No read-time rewrite.
        with self.assertRaises(ValueError):
            evo.parse_skill(raw.replace(b"\n", b"\r\n"))

    def test_nul_or_bare_cr_rejected_before_export_without_touching_library(self):
        for character in ("\0", "\r"):
            for field in ("trigger", "scope", "body"):
                with self.subTest(character=repr(character), field=field):
                    entry = copy.deepcopy(self.entries[0])
                    entry[field] = "SYNTHETIC first" + character + "second"
                    self.save([entry])
                    before = self.library.read_bytes()
                    with self.assertRaisesRegex(ValueError, "native-readable UTF-8 text with LF"):
                        evo.export_skill(self.req)
                    self.assertFalse(Path(self.req["output"]).exists())
                    self.assertEqual(self.library.read_bytes(), before)

    def test_more_than_five_rules_all_reach_the_same_single_file(self):
        self.save(list(reversed(self.entries)))
        receipt = evo.export_skill(self.req)
        directory = Path(self.req["output"])
        self.assertEqual([p.name for p in directory.iterdir()], ["SKILL.md"])
        raw = (directory / "SKILL.md").read_bytes()
        manifest = evo.parse_skill(raw)
        self.assertEqual([entry["id"] for entry in manifest["entries"]], [entry["id"] for entry in self.entries])
        self.assertEqual(receipt["entries"], 6)
        self.assertEqual(receipt["format"], "evo-full-skill-v1")
        self.assertFalse(receipt["empty"])
        self.assertEqual(set(receipt["files"]), {"SKILL.md"})
        self.assertEqual(receipt["files"]["SKILL.md"], hashlib.sha256(raw).hexdigest())
        for entry in self.entries:
            self.assertEqual(raw.decode().count(entry["body"]), 1)
            self.assertIn(entry["trigger"], raw.decode())
        self.assertEqual(raw, evo.render_skill(self.entries, 16000))
        self.assertEqual(receipt, evo.validate_package(directory))

    def test_total_utf8_budget_includes_reader_framing_and_all_rules(self):
        entry = copy.deepcopy(self.entries[0])
        base = "SYNTHETIC UTF-8 刘 😀 procedure.\n"
        # A bounded binary search finds the largest readable ASCII padding;
        # each physical line remains short. No learned content is trimmed.
        def candidate(size):
            item = {**entry, "body": base + (("x" * 79 + "\n") * (size // 80 + 1))[:size]}
            return item
        low, high = 0, 16001
        while low + 1 < high:
            mid = (low + high) // 2
            try:
                evo.render_skill([candidate(mid)], 16000)
                low = mid
            except ValueError:
                high = mid
        good, bad = candidate(low), candidate(low + 1)
        raw = evo.render_skill([good], 16000)
        self.assertEqual(len(raw), 16000)
        self.assertGreater(len(raw), len(good["body"].encode()))
        self.assertIn("刘 😀".encode(), raw)
        self.save([good])
        evo.export_skill(self.req)
        self.assertEqual((Path(self.req["output"]) / "SKILL.md").read_bytes(), raw)
        self.save([bad])
        before = self.library.read_bytes()
        rejected = self.root / "oversized"
        with self.assertRaises(ValueError):
            evo.export_skill({**self.req, "output": str(rejected)})
        self.assertFalse(rejected.exists())
        self.assertEqual(self.library.read_bytes(), before)
        self.assertEqual((Path(self.req["output"]) / "SKILL.md").read_bytes(), raw)

    def test_combined_rules_over_budget_are_rejected_without_picking_a_subset(self):
        large = [{**entry, "body": (f"Synthetic {entry['id']} method line.\n" * 100)} for entry in self.entries]
        self.assertTrue(all(len(entry["body"].encode()) < 16000 for entry in large))
        self.save(large)
        original = self.library.read_bytes()
        with self.assertRaises(ValueError):
            evo.export_skill(self.req)
        self.assertFalse(Path(self.req["output"]).exists())
        self.assertEqual(self.library.read_bytes(), original)

    def test_one_file_parser_rejects_tampering_truncation_extra_content_and_false_budget(self):
        raw = evo.render_skill(self.entries, 16000)
        variants = [raw[:-1], raw + b"unlisted text\n", raw.replace(b"SYNTHETIC rule 0", b"UNSUPPORTED rule 0", 1),
                    raw.replace(b'"max_bytes": 16000', b'"max_bytes": 10', 1)]
        for damaged in variants:
            with self.subTest(sha256=hashlib.sha256(damaged).hexdigest()):
                self.assertNotEqual(damaged, raw)
                with self.assertRaises(ValueError):
                    evo.parse_skill(damaged)

    def test_runtime_directory_rejects_legacy_selector_index_extra_directory_and_link(self):
        evo.export_skill(self.req)
        package = Path(self.req["output"])
        for extra in ("index.json", "select.py", "general"):
            with self.subTest(extra=extra):
                path = package / extra
                if extra == "general":
                    path.mkdir()
                else:
                    path.write_text("legacy or unlisted data")
                with self.assertRaises(ValueError):
                    evo.validate_package(package)
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
        skill = package / "SKILL.md"
        moved = self.root / "foreign.md"
        skill.rename(moved)
        skill.symlink_to(moved)
        with self.assertRaises(ValueError):
            evo.validate_package(package)


if __name__ == "__main__":
    unittest.main()
