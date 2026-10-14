"""Offline planning contract; synthetic metadata only, no benchmark access.

An explicit plan can be frozen and checked without a working adapter. The tool
must never confuse that with source verification or runnable experiment status.
Changed task names must not hide held-out identity/duplicate contamination.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/experiment_spec.py"
SPEC = importlib.util.spec_from_file_location("experiment_spec", SCRIPT)
spec = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec)


def example():
    tasks = [{"case_id": f"case-{i}", "original_id": f"source/task-{i}",
              "path": f"tasks/task-{i}", "content_sha256": hashlib.sha256(f"fixture-{i}".encode()).hexdigest(),
              "category": "software", "duplicate_family": None} for i in range(6)]
    return {
        "schema_version": 1, "algorithm": "wikiskill",
        "source": {"repository": "https://example.invalid/fixture/tasks", "revision": "a" * 40},
        "adapter": {"id": "synthetic-unimplemented", "version": "0.1"},
        "tasks": tasks,
        "phases": {"train": [f"case-{i}" for i in range(4)], "validation": ["case-4"], "test": ["case-5"]},
        "learning_unit": {"id": "shared-software"},
        "config": {
            "train_feedback_mode": "score_only",
            "role_models": {role: {"provider": "chosen-provider", "model_id": "chosen/model",
                                    "thinking_level": "chosen-level"} for role in spec.ROLES},
            "max_iterations": 1, "runs_per_case": 2, "replicas": ["replica-a"],
            "independent_replicas": True, "concurrency": 3, "cell_timeout_seconds": 720, "max_turns": 42},
        "scoring": {"metric": "reward", "raw_min": 0, "raw_max": 1,
                    "direction": "higher_is_better", "normalization": "linear_to_0_100",
                    "aggregation": "mean", "success_rule": "normalized_score_equals_100"}}


class ExperimentSpecTests(unittest.TestCase):
    def test_freeze_retains_explicit_choices_and_only_claims_planning(self):
        original = example()
        frozen = spec.freeze(original)
        self.assertEqual(frozen["spec"], original)
        self.assertEqual(frozen["status"], "planning_only")
        self.assertEqual(frozen["spec_sha256"], hashlib.sha256(spec.canonical(original)).hexdigest())
        self.assertEqual(spec.validate_frozen(frozen), frozen)
        summary = spec.summary(frozen)
        self.assertEqual(summary["counts"], {"train": 4, "validation": 1, "test": 1})
        self.assertFalse(summary["source_verified"])
        self.assertFalse(summary["runtime_verified"])
        self.assertEqual(summary["evaluation_scope"], "held_out_test_planned")
        original["phases"]["train"].reverse()
        self.assertNotEqual(frozen["spec"], original, "frozen values must not alias mutable caller input")

    def test_validation_only_plan_is_not_reported_as_a_test(self):
        value = example()
        value["tasks"].pop()
        value["phases"]["test"] = []
        frozen = spec.freeze(value)
        self.assertEqual(spec.summary(frozen)["evaluation_scope"], "validation_only")
        self.assertEqual(spec.summary(frozen)["counts"]["test"], 0)

    def test_categories_may_recur_and_need_not_cover_every_phase(self):
        value = example()
        value["tasks"][0]["category"] = "systems"
        value["tasks"][4]["category"] = "database"
        value["tasks"][5]["category"] = "frontend"
        spec.freeze(value)
        # Similar tasks deliberately stay together; families do not have to be
        # one task each, and a shared environment/domain is not leakage by itself.
        value["tasks"][0]["duplicate_family"] = "shared-variant"
        value["tasks"][1]["duplicate_family"] = "shared-variant"
        spec.freeze(value)

    def test_changed_case_name_cannot_hide_original_identity_or_path(self):
        for key in ("original_id", "path"):
            value = example()
            value["tasks"][5][key] = value["tasks"][0][key]
            with self.subTest(key=key), self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)

    def test_exact_content_or_declared_variant_family_cannot_cross_splits(self):
        for phase_index in (4, 5):
            for key in ("content_sha256", "duplicate_family"):
                value = example()
                if key == "duplicate_family":
                    value["tasks"][0][key] = "variant-family"
                value["tasks"][phase_index][key] = value["tasks"][0][key]
                with self.subTest(key=key, task=phase_index), self.assertRaises(spec.InvalidSpec):
                    spec.freeze(value)
        value = example()
        value["tasks"][5]["duplicate_family"] = "validation-variant"
        value["tasks"][4]["duplicate_family"] = "validation-variant"
        with self.assertRaises(spec.InvalidSpec):
            spec.freeze(value)

    def test_case_and_phase_catalog_integrity(self):
        changes = (
            lambda p: p["tasks"][5].update(case_id="case-0"),
            lambda p: p["phases"]["test"].append("case-5"),
            lambda p: p["phases"]["train"].append("case-5"),
            lambda p: p["phases"]["train"].append("unknown"),
            lambda p: p["phases"].update(test=[]),
            lambda p: p["phases"].update(validation=[]),
            lambda p: p["phases"]["train"].pop(),
            lambda p: p["phases"].update(test="case-5"),
            lambda p: p["tasks"].append(deepcopy(p["tasks"][0])),
        )
        for change in changes:
            value = example()
            change(value)
            with self.subTest(change=change), self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)

    def test_four_distinct_train_tasks_is_current_proposer_protocol_constraint(self):
        value = example()
        value["phases"]["validation"].append(value["phases"]["train"].pop())
        with self.assertRaisesRegex(spec.InvalidSpec, "Penguin Proposer"):
            spec.freeze(value)

    def test_unsafe_or_noncanonical_repository_paths_are_rejected(self):
        for path in ("/tasks/one", "../task", "tasks/../task", "tasks/./task", "tasks//one",
                     "tasks/one/", ".", "", "C:/task", "tasks\\one", "tasks/one\x00", "tasks/one\n"):
            value = example()
            value["tasks"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)

    def test_source_requires_immutable_commit_and_noncredential_url(self):
        for revision in ("main", "v4.0", "abcdef1", "A" * 40, "g" * 40, None, True):
            value = example()
            value["source"]["revision"] = revision
            with self.subTest(revision=revision), self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)
        for url in ("https://user:secret@example.invalid/repo", "https://example.invalid/repo?token=secret",
                    "https://example.invalid/repo#main", "file:///private/tasks", "https:///repo", "https://example.invalid",
                    "https://[invalid/repo", "https://example.invalid:bad/repo"):
            value = example()
            value["source"]["repository"] = url
            with self.subTest(url=url), self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)
        value = example()
        value["source"]["revision"] = "a" * 64
        spec.freeze(value)

    def test_unknown_fields_and_missing_values_cannot_gain_defaults(self):
        paths = ((), ("source",), ("adapter",), ("learning_unit",), ("config",), ("scoring",),
                 ("tasks", 0), ("config", "role_models", "target"), ("phases",))
        for path in paths:
            for operation in ("extra", "missing"):
                value = example()
                section = value
                for part in path:
                    section = section[part]
                if operation == "extra":
                    section["unexpected"] = "unaccepted"
                else:
                    section.pop(next(iter(section)))
                with self.subTest(path=path, operation=operation), self.assertRaises(spec.InvalidSpec):
                    spec.freeze(value)
        for field in ("schema_version", "algorithm"):
            value = example()
            value[field] = True
            with self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)

    def test_explicit_runtime_choices_have_no_vendor_or_thinking_whitelist(self):
        value = example()
        value["config"]["role_models"]["target"] = {"provider": "other", "model_id": "other/model", "thinking_level": "disabled"}
        frozen = spec.freeze(value)
        self.assertEqual(frozen["spec"]["config"]["role_models"]["target"]["thinking_level"], "disabled")
        for key in ("provider", "model_id", "thinking_level"):
            for invalid in ("", " ", "bad\nvalue", "--option", None, True):
                bad = example()
                bad["config"]["role_models"]["target"][key] = invalid
                with self.subTest(key=key, invalid=invalid), self.assertRaises(spec.InvalidSpec):
                    spec.freeze(bad)
        for change in (
                lambda p: p["config"]["role_models"].pop("evaluator"),
                lambda p: p["config"]["role_models"]["target"].update(provider="bad/provider"),
                lambda p: p["config"]["role_models"]["target"].update(api_key="never-accept-this")):
            value = example()
            change(value)
            with self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)

    def test_budget_numbers_are_explicit_positive_integers(self):
        keys = ("max_iterations", "runs_per_case", "concurrency", "cell_timeout_seconds", "max_turns")
        for key in keys:
            for invalid in (0, -1, True, 1.0, "2", None):
                value = example()
                value["config"][key] = invalid
                with self.subTest(key=key, invalid=invalid), self.assertRaises(spec.InvalidSpec):
                    spec.freeze(value)
        for invalid in ([], ["r1", "r1"], ["../r1"], [True], "r1"):
            value = example()
            value["config"]["replicas"] = invalid
            with self.subTest(replicas=invalid), self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)
        for invalid in (False, 1, "true", None):
            value = example()
            value["config"]["independent_replicas"] = invalid
            with self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)

    def test_score_contract_rejects_nonsense_and_unsupported_normalization(self):
        edits = ({"raw_min": 1}, {"raw_min": True}, {"raw_min": float("nan")},
                 {"raw_max": float("inf")}, {"raw_max": 10 ** 1000}, {"raw_max": -1}, {"raw_max": "1"},
                 {"direction": "lower_is_better"}, {"normalization": "identity"},
                 {"aggregation": "best"}, {"success_rule": "score_above_zero"})
        for edit in edits:
            value = example()
            value["scoring"].update(edit)
            with self.subTest(edit=edit), self.assertRaises(spec.InvalidSpec):
                spec.freeze(value)

    def test_semantic_configuration_changes_change_frozen_identity(self):
        baseline = spec.freeze(example())["spec_sha256"]
        for edit in (
                lambda p: p["config"].update(train_feedback_mode="with_labels"),
                lambda p: p["learning_unit"].update(id="another-learning-unit"),
                lambda p: p["adapter"].update(version="0.2"),
                lambda p: p["phases"].update(validation=["case-5"], test=["case-4"]),
                lambda p: p["config"].update(max_iterations=2)):
            value = example()
            edit(value)
            self.assertNotEqual(spec.freeze(value)["spec_sha256"], baseline)

    def test_frozen_payload_and_status_cannot_be_rewritten_as_verified(self):
        for edit in (
                lambda p: p.update(status="verified"),
                lambda p: p.update(schema_version=True),
                lambda p: p.update(spec_sha256="b" * 64),
                lambda p: p.update(runtime_verified=True),
                lambda p: p["spec"]["config"].update(max_iterations=2)):
            frozen = spec.freeze(example())
            edit(frozen)
            with self.assertRaises(spec.InvalidSpec):
                spec.validate_frozen(frozen)


class ExperimentSpecCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wikiskill-spec-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.input = self.root / "input.json"
        self.output = self.root / "frozen.json"
        self.input.write_text(json.dumps(example()))

    def cli(self, *args, succeeds=True, script=SCRIPT, plain=False):
        command = [sys.executable, *([] if plain else ["-B"]), str(script), *map(str, args)]
        env = dict(os.environ)
        if plain:
            env.pop("PYTHONDONTWRITEBYTECODE", None)
            env.pop("PYTHONPYCACHEPREFIX", None)
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def create(self, **kwargs):
        return self.cli("freeze", "--input", self.input, "--out", self.output, **kwargs)

    def test_cli_roundtrip_idempotence_and_no_readside_modifications(self):
        first = self.create()
        frozen_bytes, mtime = self.output.read_bytes(), self.output.stat().st_mtime_ns
        self.assertEqual(self.create(), first)
        self.assertEqual(self.output.stat().st_mtime_ns, mtime)
        self.assertEqual(self.cli("check", "--spec", self.output), first)
        self.assertEqual(self.output.read_bytes(), frozen_bytes)
        self.assertEqual({path.name for path in self.root.iterdir()}, {"input.json", "frozen.json"})
        value = example()
        value["config"]["max_iterations"] += 1
        self.input.write_text(json.dumps(value))
        self.create(succeeds=False)
        self.assertEqual(self.output.read_bytes(), frozen_bytes)

    def test_check_never_treats_raw_or_legacy_profile_as_a_frozen_spec(self):
        self.cli("check", "--spec", self.input, succeeds=False)
        self.input.write_text(json.dumps({"schema_version": 1, "algorithm": "wikiskill", "benchmark": "GDPevo",
                                        "profile_sha256": "a" * 64, "source_verified": True}))
        self.create(succeeds=False)
        self.assertFalse(self.output.exists())

    def test_duplicate_json_keys_and_nonfinite_numbers_fail_without_output(self):
        original = json.dumps(example())
        for content in (original.replace('"max_iterations": 1', '"max_iterations": 1, "max_iterations": 2'),
                        original.replace('"raw_max": 1', '"raw_max": NaN'),
                        original.replace('"raw_max": 1', '"raw_max": Infinity'),
                        original.replace('"raw_max": 1', '"raw_max": 1e999'),
                        "{bad json"):
            self.input.write_text(content)
            with self.subTest(content=content[:40]):
                self.create(succeeds=False)
                self.assertFalse(self.output.exists())

    def test_linked_input_output_and_parent_are_refused(self):
        input_link = self.root / "input-link.json"
        input_link.symlink_to(self.input)
        self.cli("freeze", "--input", input_link, "--out", self.output, succeeds=False)
        self.assertFalse(self.output.exists())
        self.output.symlink_to(self.input)
        self.create(succeeds=False)
        self.output.unlink()
        folder = self.root / "real"
        folder.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(folder, target_is_directory=True)
        self.cli("freeze", "--input", self.input, "--out", alias / "output.json", succeeds=False)
        self.assertFalse((folder / "output.json").exists())

    def test_errors_do_not_echo_injected_credentials(self):
        value = example()
        value["config"]["role_models"]["target"]["api_key"] = "NEVER_PRINT_SECRET"
        self.input.write_text(json.dumps(value))
        error = self.create(succeeds=False)
        self.assertNotIn("NEVER_PRINT_SECRET", error)

    def test_duplicate_identity_error_is_actionable_without_echoing_input(self):
        value = example()
        value["tasks"][0]["original_id"] = "PRIVATE-original-task"
        value["tasks"][5]["original_id"] = "PRIVATE-original-task"
        value["config"]["role_models"]["target"]["model_id"] = "NEVER_PRINT_CREDENTIAL"
        self.input.write_text(json.dumps(value))
        error = self.create(succeeds=False)
        self.assertEqual(error, "experiment plan refused: original task identity reused under multiple cases\n")
        for private in ("PRIVATE-original-task", "NEVER_PRINT_CREDENTIAL", value["source"]["repository"],
                        value["tasks"][0]["case_id"], str(self.input)):
            self.assertNotIn(private, error)
        self.assertFalse(self.output.exists())

    def test_standalone_plain_python_does_not_need_gdpevo_or_generate_plugin_bytes(self):
        plugin_scripts = self.root / "standalone"
        plugin_scripts.mkdir()
        copied = plugin_scripts / SCRIPT.name
        shutil.copyfile(SCRIPT, copied)
        before = {path.name: path.read_bytes() for path in plugin_scripts.iterdir()}
        self.create(script=copied, plain=True)
        self.assertEqual({path.name: path.read_bytes() for path in plugin_scripts.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
