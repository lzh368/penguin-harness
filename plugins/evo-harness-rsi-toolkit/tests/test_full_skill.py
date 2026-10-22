"""All context bodies and general descriptions, independent native Skills."""
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
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-group-skills-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.entries = [{"id": f"method-{number}", "level": "general" if number < 3 else "topic",
            **({"topic": "synthetic-context"} if number >= 3 else {"description": f"Synthetic general purpose {number}."}),
            "trigger": f"Synthetic task need {number}", "scope": "Authorized synthetic records only",
            "body": f"SYNTHETIC rule {number}: inspect the defined records, retain conditions, and report the computed result."}
            for number in range(6)]
        self.library = self.root / "library.json"
        self.save(self.entries)
        self.req = {"library": str(self.library), "limits": {"general": 5, "topic": 5},
                    "max_bytes": 16000, "output": str(self.root / "skill")}

    def save(self, entries):
        self.library.write_text(json.dumps(entries, ensure_ascii=False))

    def files(self, root=None):
        root = root or Path(self.req["output"])
        return {path: (root / path).read_bytes() for path in evo.filemap(root)}

    def test_crlf_source_normalizes_before_hashes_without_changing_library(self):
        entries = copy.deepcopy(self.entries)
        entries[0]["body"] = "SYNTHETIC first step.\r\nSecond step retains its condition.\r\n"
        self.save(entries)
        original = self.library.read_bytes()
        receipt = evo.export_skill(self.req)
        canonical = copy.deepcopy(entries)
        canonical[0]["body"] = canonical[0]["body"].replace("\r\n", "\n")
        files = self.files()
        self.assertEqual(files, evo.render_package(canonical, 16000))
        self.assertTrue(all(b"\r" not in raw for raw in files.values()))
        self.assertEqual(self.library.read_bytes(), original)
        self.assertEqual(evo.validate_package(Path(self.req["output"])), receipt)
        with self.assertRaises(ValueError):
            evo.parse_reader(files["SKILL.md"].replace(b"\n", b"\r\n"))

    def test_nul_or_bare_cr_rejected_without_touching_library(self):
        for character in ("\0", "\r"):
            for field in ("trigger", "scope", "body"):
                with self.subTest(character=repr(character), field=field):
                    entry = copy.deepcopy(self.entries[0])
                    entry[field] = "SYNTHETIC first" + character + "second"
                    self.save([entry]); before = self.library.read_bytes()
                    with self.assertRaisesRegex(ValueError, "native-readable UTF-8 text with LF"):
                        evo.export_skill(self.req)
                    self.assertFalse(Path(self.req["output"]).exists())
                    self.assertEqual(self.library.read_bytes(), before)

    def test_all_entries_are_independent_first_level_native_skills(self):
        self.save(list(reversed(self.entries)))
        receipt = evo.export_skill(self.req)
        files = self.files()
        reader = files["SKILL.md"]
        manifest = evo.parse_reader(reader)
        state = evo.package_state_files(files)
        self.assertEqual([entry["id"] for entry in manifest["entries"]], [entry["id"] for entry in self.entries])
        self.assertEqual(receipt["entries"], 6)
        self.assertEqual(receipt["format"], "evo-multi-skill-v1")
        self.assertFalse(receipt["empty"])
        self.assertEqual(len(files), 7)
        self.assertEqual(receipt["files"], {path: hashlib.sha256(raw).hexdigest() for path, raw in files.items()})
        self.assertEqual(state["skills/evo-harness-learned/SKILL.md"], reader)
        for original, item in zip(self.entries, manifest["entries"]):
            self.assertEqual(files[item["path"]], state[item["state_path"]])
            self.assertEqual(len(Path(item["state_path"]).parts), 3)
            self.assertEqual(files[item["path"]].decode().count(original["body"]), 1)
            self.assertNotIn(original["body"].encode(), reader)
            if original["level"] == "general":
                self.assertEqual(item["load_mode"], "description")
                self.assertEqual(item["description"], original["description"])
                self.assertIn(original["description"].encode(), reader)
            else:
                self.assertEqual(item["load_mode"], "body")
                self.assertNotIn("description", item)
        self.assertEqual(files, evo.render_package(self.entries, 16000))
        self.assertEqual(receipt, evo.validate_package(Path(self.req["output"])))

    def test_h0_single_entry_and_empty_layer_require_no_artificial_output(self):
        self.assertEqual(set(evo.render_package([], 16000)), {"SKILL.md"})
        self.assertEqual(evo.parse_reader(evo.render_skill([], 16000))["entries"], [])
        for entry in (self.entries[0], self.entries[-1]):
            files = evo.render_package([entry], 16000)
            self.assertEqual(len(files), 2)
            self.assertEqual([item["level"] for item in evo.parse_reader(files["SKILL.md"])["entries"]], [entry["level"]])

    def test_general_description_is_explicit_and_never_derived_from_body(self):
        entry = copy.deepcopy(self.entries[0])
        for value in (None, "", {}, 7):
            with self.subTest(value=value), self.assertRaises(ValueError):
                evo.render_package([{**entry, "description": value}], 16000)
        del entry["description"]
        with self.assertRaisesRegex(ValueError, "general description"):
            evo.render_package([entry], 16000)
        # No new arbitrary short-description length limit. Semantic concision
        # is an Evolver requirement, not a keyword or character-count oracle.
        files = evo.render_package([{**entry, "description": "SYNTHETIC purpose " + "x" * 350}], 16000)
        self.assertNotIn(entry["body"].encode(), files["SKILL.md"])

    def test_stable_native_names_preserve_original_identity(self):
        entry = {**self.entries[-1], "id": "UPPER_LONG_ORIGINAL_ID_" + "x" * 70}
        files = evo.render_package([entry], 16000)
        item = evo.parse_reader(files["SKILL.md"])["entries"][0]
        self.assertEqual(item["id"], entry["id"])
        self.assertRegex(item["name"], r"^evo-method-[0-9a-f]{20}$")
        self.assertIn(("# " + entry["id"]).encode(), files[item["path"]])
        self.assertEqual(evo.package_state_files(files)[item["state_path"]], files[item["path"]])

    def test_total_utf8_budget_counts_reader_manifest_frontmatter_and_all_bodies(self):
        entry = copy.deepcopy(self.entries[-1])
        def candidate(size):
            return {**entry, "body": "SYNTHETIC UTF-8 刘 😀.\n" + (("x" * 79 + "\n") * (size // 80 + 1))[:size]}
        low, high = 0, 16001
        while low + 1 < high:
            mid = (low + high) // 2
            try:
                evo.render_package([candidate(mid)], 16000); low = mid
            except ValueError:
                high = mid
        good, bad = candidate(low), candidate(low + 1)
        files = evo.render_package([good], 16000)
        self.assertEqual(sum(map(len, files.values())), 16000)
        self.assertIn("刘 😀".encode(), b"".join(files.values()))
        self.save([good]); evo.export_skill(self.req)
        self.assertEqual(self.files(), files)
        self.save([bad]); original = self.library.read_bytes()
        with self.assertRaises(ValueError):
            evo.export_skill({**self.req, "output": str(self.root / "oversized")})
        self.assertEqual(self.library.read_bytes(), original)
        self.assertEqual(self.files(), files)

    def test_combined_rules_over_budget_rejected_without_subset_or_rewrite(self):
        entries = [{**entry, "body": f"Synthetic {entry['id']} method line.\n" * 100} for entry in self.entries]
        self.assertTrue(all(len(entry["body"].encode()) < 16000 for entry in entries))
        self.save(entries); original = self.library.read_bytes()
        with self.assertRaises(ValueError): evo.export_skill(self.req)
        self.assertFalse(Path(self.req["output"]).exists())
        self.assertEqual(self.library.read_bytes(), original)

    def test_package_rejects_missing_truncated_tampered_or_unlisted_files(self):
        files = evo.render_package(self.entries, 16000)
        path = next(path for path in files if path != "SKILL.md")
        variants = [{key: raw for key, raw in files.items() if key != path},
                    {**files, path: files[path][:-1]}, {**files, path: files[path] + b"extra"},
                    {**files, "other/SKILL.md": b"extra"},
                    {**files, "SKILL.md": files["SKILL.md"][:-1]}]
        for changed in variants:
            with self.subTest(paths=list(changed)), self.assertRaises(ValueError):
                evo.validate_package_files(changed)

    def test_manifest_rejects_loading_level_mismatch_mapping_alias_and_false_budget(self):
        files = evo.render_package(self.entries, 16000)
        manifest = evo.parse_reader(files["SKILL.md"])
        for mutation in (lambda m: m["entries"][0].update(load_mode="body"),
                         lambda m: m["entries"][0].update(state_path="skills/foreign/SKILL.md"),
                         lambda m: m.update(max_bytes=10),
                         lambda m: m["entries"].append(copy.deepcopy(m["entries"][0]))):
            changed = copy.deepcopy(manifest); mutation(changed)
            raw = evo.READER.encode() + evo.FULL_SKILL_OPEN.encode() + evo.encoded(changed) + evo.FULL_SKILL_CLOSE.encode()
            with self.assertRaises(ValueError): evo.parse_reader(raw)

    def test_frozen_context_path_allowlist_does_not_allow_general_or_other_files(self):
        slot = "/state/skills/evo-harness-learned"
        context = "/state/skills/evo-method-01234567890123456789/SKILL.md"
        general = "/state/skills/evo-method-12345678901234567890/SKILL.md"
        def call(command):
            return {"name": "exec_command", "arguments": json.dumps({"cmd": command})}
        self.assertEqual(evo.inspection._readonly_commands(call("cat " + context), "/app", slot,
                         skill_files={context})[3], [context])
        for command in ("cat " + general, "cat /state/skills/foreign/SKILL.md", "cat /app/hidden.md",
                        "find /app/input -type f | sort -r", "cat " + context + " > /tmp/copy"):
            with self.subTest(command=command), self.assertRaises(ValueError):
                evo.inspection._readonly_commands(call(command), "/app", slot, skill_files={context})
        evo.inspection._readonly_commands(call("find /app/input -type f | sort"), "/app", slot,
                                          skill_files={context})

    def test_directory_rejects_selector_index_extra_directory_and_symlink(self):
        evo.export_skill(self.req); package = Path(self.req["output"])
        for extra in ("index.json", "select.py", "general"):
            path = package / extra
            if extra == "general": path.mkdir()
            else: path.write_text("unlisted")
            with self.assertRaises(ValueError): evo.validate_package(package)
            if path.is_dir(): path.rmdir()
            else: path.unlink()
        skill = package / "SKILL.md"; moved = self.root / "foreign.md"
        skill.rename(moved); skill.symlink_to(moved)
        with self.assertRaises(ValueError): evo.validate_package(package)


if __name__ == "__main__":
    unittest.main()
