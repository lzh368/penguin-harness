"""Instruction delivery, not a test of a model's future semantic decisions.

Reuse the complete synthetic five-task fixture with the actual validators. The
optional artifact directory contains only these synthetic inputs and the public
method instructions. No benchmark answers, service, model or GPU is used.
"""
import hashlib
import json
import os
from pathlib import Path
import unittest

import test_control

SCRIPT, control = test_control.SCRIPT, test_control.control


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class InstructionDeliveryTests(unittest.TestCase):
    def test_complete_batch_delivers_full_roles_and_exports_same_reader(self):
        fixture = test_control.ControlTests("test_full_sequential_batch_same_solver_reflections_compiles_exports_and_seals")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        # Model messages and HTTP feedback are synthetic. Reads, budgets,
        # claims, role seals, compile/export and training acceptance are real.
        fixture.test_full_sequential_batch_same_solver_reflections_compiles_exports_and_seals()
        role_source = (fixture.skill / "references/roles.md").read_text()
        prefix, sections = role_source.split("## Solver", 1)[0], {}
        titles = {"solver": "Solver (group training leaf)", "evolver": "Evolver"}
        for role, title in titles.items():
            start = role_source.index("## " + title + "\n")
            end = role_source.find("\n## ", start + 1)
            sections[role] = prefix + role_source[start:end if end >= 0 else None]
            if role == "solver":
                reflection_start = role_source.index("## In-session reflection\n")
                reflection_end = role_source.index("\n## Evolver", reflection_start)
                sections[role] += "\n" + role_source[reflection_start:reflection_end]
            self.assertEqual(control.role_text(role), sections[role])
            self.assertEqual((fixture.private / "roles" / (role + ".md")).read_bytes(), sections[role].encode())

        tickets = [json.loads(path.read_text()) for path in (fixture.private / "dispatch").glob("*.json")]
        tickets = sorted([ticket for ticket in tickets if "call" in ticket], key=lambda ticket: ticket["slot"])
        self.assertEqual([sum(ticket["phase"] == role for ticket in tickets) for role in titles], [5, 1])
        prompts = []
        for ticket in tickets:
            role, call = ticket["phase"], ticket["call"]
            self.assertNotIn("prompt", call)
            self.assertTrue(call["run_in_background"])
            self.assertLess(len(json.dumps(call).encode()), 1500)
            self.assertEqual({key: call[key] for key in ("provider", "model_id", "thinking_level")},
                             {key: fixture.runtime[key] for key in ("provider", "model_id", "thinking_level")})
            raw = Path(call["prompt_file"]).read_bytes()
            text = raw.decode()
            self.assertEqual(sha(raw), call["prompt_sha256"])
            self.assertEqual(control.evo.native_prompt(call), text)
            self.assertEqual(text.count(sections[role]), 1)
            self.assertIn(str(fixture.private / "roles" / (role + ".md")), text)
            if role == "evolver":
                self.assertIn("Supply a concise explicit description for each general entry", text)
                self.assertIn("self-contained method summary", text)
                self.assertNotIn("All rules are read in full", text)
                self.assertNotIn("describing its purpose and applicability", text)
                self.assertIn("general Skill bodies are not loaded", text)
                self.assertIn("do not relabel general entries as topic", text)
                self.assertIn("Either level may be empty", text)
                material_line = next(line for line in text.splitlines() if line.startswith("Read all these files in full: "))
                materials = json.loads(material_line.split(": ", 1)[1])
                self.assertEqual(len(materials), 8)
                self.assertEqual(materials[3:], [str(fixture.private / "contexts" / (task + ".json")) for task in fixture.profile["train_ids"]])
                limit_line = next(line for line in text.splitlines() if line.startswith("Exact frozen compilation/injection limits: "))
                self.assertEqual(json.loads(limit_line.split(": ", 1)[1]),
                                 {key: fixture.profile[key] for key in ("limits", "max_bytes")})
            prompts.append({"role": role, "slot": ticket["slot"], "raw": raw, "call": call})

        cold = fixture.state / "skills/evo-harness-learned"
        final = fixture.workspace / "submission/skill"
        reader = control.evo.READER.encode()
        packages = {}
        for package in (cold, final):
            receipt = control.evo.validate_package(package)
            files = {path: (package / path).read_bytes() for path in receipt["files"]}
            raw = files["SKILL.md"]
            self.assertTrue(raw.startswith(reader))
            manifest = control.evo.parse_reader(raw)
            self.assertEqual(manifest["max_bytes"], 16000)
            self.assertLessEqual(sum(map(len, files.values())), 16000)
            packages["h0" if package == cold else "final"] = files
        self.assertEqual(control.evo.parse_reader(packages["h0"]["SKILL.md"])["entries"], [])
        manifest = control.evo.parse_reader(packages["final"]["SKILL.md"])
        self.assertEqual([entry["id"] for entry in manifest["entries"]], ["fixture-field-review"])
        item = manifest["entries"][0]
        body = "Before submission, compare the public required fields with the current document and fill any omitted public fields."
        self.assertEqual(packages["final"][item["path"]].decode().count(body), 1)
        self.assertNotIn(body.encode(), packages["final"]["SKILL.md"])
        self.assertEqual(item["description"], "Review publicly required form fields.")
        self.assertEqual(item["load_mode"], "description")

        main = (fixture.skill / "SKILL.md").read_bytes()
        main_text = main.decode()
        # A visible method objective precedes the orchestration checklist.
        introduction = main_text.split("# Evo-Harness\n\n", 1)[1].split("\n## Before you start", 1)[0]
        self.assertTrue(introduction.startswith("Learn executable methods for future similar tasks:"))
        self.assertIn("references/algorithm.md", introduction)
        export = json.loads((fixture.private / "training-export.json").read_text())
        self.assertEqual(len(export["members"]), 5)
        self.assertEqual(len(export["roles"]), 3)
        output = os.environ.get("EVO_DELIVERY_ARTIFACT_DIR")
        if output:
            self.save_artifacts(Path(output), fixture, prompts, main, reader, introduction, export, packages)

    def save_artifacts(self, directory, fixture, prompts, main, reader, introduction, export, packages):
        directory.mkdir(parents=True, exist_ok=False)
        artifacts = {}
        def save(name, raw):
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            artifacts[name] = {"sha256": sha(raw), "bytes": len(raw)}
        save("main-SKILL.md", main)
        save("learned-Reader.md", reader)
        for phase, files in packages.items():
            for path, raw in files.items():
                save(phase + "/" + path, raw)
        for role in ("solver", "evolver"):
            save("roles/" + role + ".md", (fixture.private / "roles" / (role + ".md")).read_bytes())
        prepared = []
        for prompt in prompts:
            filename = "prompts/" + prompt["slot"].replace("/", "--") + ".txt"
            save(filename, prompt["raw"])
            prepared.append({"role": prompt["role"], "slot": prompt["slot"], "prompt_artifact": filename,
                             "original_call": prompt["call"], "sha256": sha(prompt["raw"])})
        crlf_entry = {"id": "crlf-native-proof", "level": "general", "description": "Synthetic public-field review.", "trigger": "Synthetic authorized records",
            "scope": "Synthetic fixture only", "body": "SYNTHETIC retain successful steps.\r\nCheck documented fields before changing a step.\r\n"}
        crlf_library = directory / "crlf-source-library.json"
        crlf_library.write_bytes(control.evo.encoded([crlf_entry]))
        crlf_receipt = control.evo.export_skill({"library": str(crlf_library), "limits": {"general": 5, "topic": 5},
            "max_bytes": 16000, "output": str(directory / "crlf")})
        packages["crlf"] = {path: (directory / "crlf" / path).read_bytes() for path in crlf_receipt["files"]}
        assert all(b"\r" not in raw for raw in packages["crlf"].values())
        artifacts["crlf-source-library.json"] = {"sha256": sha(crlf_library.read_bytes()), "bytes": crlf_library.stat().st_size}
        for path, raw in packages["crlf"].items():
            artifacts["crlf/" + path] = {"sha256": sha(raw), "bytes": len(raw)}
        sources = [SCRIPT, SCRIPT.with_name("evo.py"), SCRIPT.with_name("inspection.py"), fixture.skill / "SKILL.md",
                   fixture.skill / "references/roles.md", fixture.skill / "references/algorithm.md"]
        manifest = {"schema": "evo-context-skills-instruction-delivery-v1", "model_calls": 0, "network_calls": 0,
            "scope": "Synthetic native-shaped full batch: five sequential Solvers, two same-Solver reflections, one independent Evolver; actual budget/read/capture/seal/compile/export checks. HTTP response and model messages are fixtures.",
            "limitations": "This proves instruction delivery and mechanics, not that a future model will follow the content or improve scores. Native SDK transport is a separate zero-model probe.",
            "source_hashes": {str(path if path.parent == SCRIPT.parent else SCRIPT.parents[1] / path.relative_to(fixture.skill)): sha(path.read_bytes()) for path in sources},
            "runtime": fixture.runtime, "frozen_limits": {key: fixture.profile[key] for key in ("limits", "max_bytes")},
            "full_batch_accepted": True, "members": len(export["members"]), "sealed_learning_roles": len(export["roles"]),
            "main_method_objective_before_orchestration": introduction,
            "runtime_packages": {phase: {"files": {path: sha(raw) for path, raw in files.items()},
                "bytes": sum(map(len, files.values())),
                "entries": len(control.evo.parse_reader(files["SKILL.md"])["entries"])} for phase, files in packages.items()},
            "selector_calls": 0, "prepared_prompts": prepared, "artifacts": artifacts}
        (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    unittest.main()
