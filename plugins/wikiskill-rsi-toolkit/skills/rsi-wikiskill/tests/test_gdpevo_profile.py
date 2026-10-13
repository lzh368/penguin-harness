"""Offline tests of the fixed split and local source/output guards.

Runtime selection scenarios:
- A user selects a provider and slash-containing model ID: the CLI freezes those
  values for all roles without changing the split, and refuses a later overwrite.
- A user supplies six different role runtimes: the CLI preserves each selection.
- A runtime is incomplete, contains credentials, or conflicts with CLI flags:
  profile generation fails before creating its output.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/gdpevo_profile.py"
SPEC = importlib.util.spec_from_file_location("gdpevo_profile", SCRIPT)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


class ProfileTests(unittest.TestCase):
    def test_split_counts_and_disjoint_identity(self):
        phases = profile.split_tasks()
        self.assertEqual({p: len(v["tasks"]) for p, v in phases.items()},
                         {"train": 24, "validation": 6, "test": 30})
        sets = [{task["task_name"] for task in phases[p]["tasks"]}
                for p in ("train", "validation", "test")]
        self.assertFalse(sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
        expected = {f"tg{group}-{split}-{i:03d}"
                    for group in ("003", "005", "010", "014", "018", "024")
                    for split in ("train", "test") for i in range(1, 6)}
        self.assertEqual(set.union(*sets), expected)
        identities = {(value["benchmark_id"], task["case_id"])
                      for value in phases.values() for task in value["tasks"]}
        self.assertEqual(len(identities), 60)

    def test_validation_retains_source_train_fifth_cases(self):
        phase = profile.split_tasks()["validation"]
        self.assertEqual(phase["phase"], "validation")
        self.assertEqual(phase["source_split"], "train")
        self.assertEqual(phase["benchmark_id"], "penguinharness-benchmark-gdpevo-train")
        self.assertEqual([t["case_id"] for t in phase["tasks"]], [
            "CASE-005-tg003-train-005", "CASE-010-tg005-train-005",
            "CASE-015-tg010-train-005", "CASE-020-tg014-train-005",
            "CASE-025-tg018-train-005", "CASE-030-tg024-train-005"])

    def test_canonical_paths_and_source_package_mapping(self):
        phases = profile.split_tasks()
        for phase in phases.values():
            for task in phase["tasks"]:
                self.assertEqual(task["task_path"], f"benchmarks/gdpevo/tasks/{task['task_name']}")
                self.assertEqual(task["case_path"], f"packages/{phase['benchmark_id']}/{task['case_id']}")
        self.assertEqual(phases["test"]["tasks"][-1]["case_id"], "CASE-030-tg024-test-005")
        self.assertEqual(phases["train"]["tasks"][4]["case_id"], "CASE-006-tg005-train-001")

    def test_defaults_and_explicit_unverified_status(self):
        result = profile.build_profile()
        self.assertFalse(result["source_verified"])
        self.assertIsNone(result["source_verification"])
        self.assertEqual(result["source_refs"], {
            "benchmark_commit": "e5d58d230bda928d9a98ae775c54c159af8f8363",
            "host_rsi_commit": "4c9c1797156921525b420f7f6d4bfab788059ae7"})
        config = result["config"]
        self.assertEqual(config["replicas"], ["r1", "r2", "r3"])
        self.assertTrue(config["independent_replicas"])
        self.assertFalse(config["share_learned_state"])
        self.assertEqual((config["runs_per_case"], config["max_iterations"]), (1, 2))
        self.assertIn("not_paper_default", config["iteration_budget_basis"])
        for model in config["role_models"].values():
            self.assertEqual(model, {"provider": "deepseek", "model_id": "deepseek-flash", "thinking_level": "xhigh"})

    def test_deterministic_bytes_and_self_excluding_hash(self):
        first, second = profile.build_profile(), profile.build_profile()
        self.assertEqual(profile.canonical_bytes(first), profile.canonical_bytes(second))
        digest = first.pop("profile_sha256")
        self.assertEqual(hashlib.sha256(profile.canonical_bytes(first)).hexdigest(), digest)
        self.assertNotEqual(digest, profile.build_profile(3)["profile_sha256"])

    def test_invalid_iteration_budgets(self):
        for value in (0, -1, True, 1.0, "2", None):
            with self.subTest(value=value), self.assertRaises(profile.InvalidProfile):
                profile.build_profile(value)

    def test_selected_runtimes_change_identity_without_changing_the_split(self):
        original = profile.build_profile()
        runtimes = {role: {"provider": "openrouter", "model_id": "deepseek/deepseek-v4.1-flash",
                           "thinking_level": "high"} for role in profile.ROLES}
        selected = profile.build_profile(role_models=runtimes)
        self.assertEqual(selected["phases"], original["phases"])
        self.assertEqual(selected["config"]["role_models"], runtimes)
        self.assertNotEqual(selected["profile_sha256"], original["profile_sha256"])
        runtimes["controller"]["provider"] = "changed-after-freeze"
        self.assertEqual(selected["config"]["role_models"]["controller"]["provider"], "openrouter")

    def test_invalid_or_secret_bearing_runtime_configuration_is_refused(self):
        original = profile.build_profile()["config"]["role_models"]
        for edit in (
                lambda roles: roles.pop("controller"),
                lambda roles: roles.update(unexpected=roles["target"]),
                lambda roles: roles["target"].update(provider=""),
                lambda roles: roles["target"].update(provider="open/router"),
                lambda roles: roles["target"].update(model_id="model\nname"),
                lambda roles: roles["target"].update(model_id="--flag"),
                lambda roles: roles["target"].update(model_id=None),
                lambda roles: roles["target"].update(thinking_level="off"),
                lambda roles: roles["target"].update(api_key="synthetic-secret")):
            selected = deepcopy(original)
            edit(selected)
            with self.subTest(runtime=selected), self.assertRaises(profile.InvalidProfile):
                profile.build_profile(role_models=selected)

    def test_cli_selects_one_runtime_or_explicit_roles_and_refuses_conflicts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "profile.json"
            command = [sys.executable, "-B", str(SCRIPT), "--out", str(output)]
            runtime_args = ["--provider", "openrouter", "--model-id", "deepseek/deepseek-v4.1-flash",
                            "--thinking", "high"]
            subprocess.run(command + runtime_args, check=True, capture_output=True)
            frozen = output.read_bytes()
            selected = json.loads(frozen)
            runtime = {"provider": "openrouter", "model_id": "deepseek/deepseek-v4.1-flash", "thinking_level": "high"}
            self.assertTrue(all(value == runtime for value in selected["config"]["role_models"].values()))
            refused = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(output.read_bytes(), frozen)

            roles = deepcopy(selected["config"]["role_models"])
            roles["controller"] = {"provider": "custom", "model_id": "another/model", "thinking_level": "low"}
            role_file = root / "roles.json"
            role_file.write_text(json.dumps(roles))
            separate = root / "separate.json"
            base = [sys.executable, "-B", str(SCRIPT), "--out", str(separate)]
            subprocess.run(base + ["--role-models", str(role_file)], check=True, capture_output=True)
            self.assertEqual(json.loads(separate.read_bytes())["config"]["role_models"], roles)
            separate.unlink()
            for args in (["--provider", "openrouter"], ["--thinking", "high"],
                         ["--role-models", str(role_file), *runtime_args]):
                refused = subprocess.run(base + args, capture_output=True, text=True)
                self.assertNotEqual(refused.returncode, 0)
                self.assertFalse(separate.exists())
            role_file.write_text('{"controller":{},"controller":{}}')
            refused = subprocess.run(base + ["--role-models", str(role_file)], capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertFalse(separate.exists())

    def test_output_is_idempotent_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "plan.json"
            plan = profile.build_profile()
            profile.write_profile(output, plan)
            data, mtime = output.read_bytes(), output.stat().st_mtime_ns
            profile.write_profile(output, plan)
            self.assertEqual(output.stat().st_mtime_ns, mtime)
            with self.assertRaises(profile.InvalidProfile):
                profile.write_profile(output, profile.build_profile(3))
            self.assertEqual(output.read_bytes(), data)
            self.assertEqual({p.name for p in Path(folder).iterdir()}, {"plan.json"})

    def test_output_rejects_symlink_file_and_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            real = root / "real"
            real.mkdir()
            profile.write_profile(real / "plan.json", profile.build_profile())
            (root / "linked.json").symlink_to(real / "plan.json")
            (root / "linked-dir").symlink_to(real, target_is_directory=True)
            for output in (root / "linked.json", root / "linked-dir/other.json"):
                with self.assertRaises(profile.InvalidProfile):
                    profile.write_profile(output, profile.build_profile())

    def test_cli_offline_profile_and_nonoverwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "plan.json"
            command = [sys.executable, "-B", str(SCRIPT), "--out", str(output)]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            summary = json.loads(result.stdout)
            self.assertFalse(summary["source_verified"])
            self.assertEqual(summary["counts"], {"train": 24, "validation": 6, "test": 30})
            repeated = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, repeated.stdout)
            refused = subprocess.run(command + ["--iterations", "3"], capture_output=True, text=True)
            self.assertEqual(refused.returncode, 1)
            self.assertIn("refusing to overwrite", refused.stderr)


@unittest.skipUnless(shutil.which("git"), "local Git is required for source verification tests")
class SourceVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory()
        cls.fixture = Path(cls.fixture_dir.name) / "repository"
        cls.fixture.mkdir()
        for phase in profile.split_tasks().values():
            config = cls.fixture / f"packages/{phase['benchmark_id']}/benchmark_config.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text("# synthetic offline fixture\n")
            for task in phase["tasks"]:
                files = [f"{task['task_path']}/{name}" for name in profile.TASK_FILES]
                index = task["task_name"].rsplit("-", 1)[1]
                grader = f"{task['task_path']}/tests/task_group/{phase['source_split']}_tasks/{index}"
                files.extend(f"{grader}/{name}" for name in ("eval/eval.sh", "output/answer.json"))
                files.extend(f"{task['case_path']}/{kind}/README.md" for kind in ("statement", "rubric"))
                for relative in files:
                    path = cls.fixture / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"synthetic opaque fixture; never a real benchmark answer\n")
        cls.run_git(cls.fixture, "init", "--quiet")
        cls.run_git(cls.fixture, "add", ".")
        cls.run_git(cls.fixture, "-c", "user.name=Offline Test", "-c", "user.email=offline@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "fixture")
        cls.commit = cls.run_git(cls.fixture, "rev-parse", "HEAD").decode().strip()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    @staticmethod
    def run_git(root, *args):
        return subprocess.run(["git", "-C", str(root), *args], check=True,
                              capture_output=True).stdout

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repository"
        shutil.copytree(self.fixture, self.root)
        self.pin = patch.object(profile, "BENCHMARK_COMMIT", self.commit)
        self.pin.start()
        self.addCleanup(self.pin.stop)

    def test_verified_profile_is_location_independent(self):
        first = profile.build_profile(benchmark_root=self.root)
        second = profile.build_profile(benchmark_root=self.fixture)
        self.assertTrue(first["source_verified"])
        self.assertEqual(first, second)
        self.assertGreater(first["source_verification"]["file_count"], 1000)
        self.assertNotIn(str(self.root), profile.canonical_bytes(first).decode())

    def test_wrong_commit_is_rejected(self):
        with patch.object(profile, "BENCHMARK_COMMIT", "0" * 40):
            with self.assertRaisesRegex(profile.InvalidProfile, "HEAD"):
                profile.build_profile(benchmark_root=self.root)

    def test_tracked_dirty_file_is_rejected_without_content_output(self):
        path = self.root / "benchmarks/gdpevo/tasks/tg003-train-001/instruction.md"
        path.write_text("DO_NOT_PRINT_PRIVATE_CONTENT")
        with self.assertRaisesRegex(profile.InvalidProfile, "tracked") as raised:
            profile.build_profile(benchmark_root=self.root)
        self.assertNotIn("DO_NOT_PRINT_PRIVATE_CONTENT", str(raised.exception))

    def test_missing_tracked_file_is_rejected(self):
        (self.root / "packages/penguinharness-benchmark-gdpevo-train/CASE-005-tg003-train-005/rubric/README.md").unlink()
        with self.assertRaises(profile.InvalidProfile):
            profile.build_profile(benchmark_root=self.root)

    def test_missing_required_structure_in_committed_tree_is_rejected(self):
        path = "packages/penguinharness-benchmark-gdpevo-train/CASE-005-tg003-train-005/rubric/README.md"
        self.run_git(self.root, "rm", path)
        self.run_git(self.root, "-c", "user.name=Offline Test", "-c", "user.email=offline@example.invalid",
                     "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "remove required fixture")
        new_pin = self.run_git(self.root, "rev-parse", "HEAD").decode().strip()
        with patch.object(profile, "BENCHMARK_COMMIT", new_pin):
            with self.assertRaisesRegex(profile.InvalidProfile, "required task/case structure"):
                profile.build_profile(benchmark_root=self.root)

    def test_untracked_file_inside_task_is_rejected(self):
        (self.root / "benchmarks/gdpevo/tasks/tg003-train-001/injected.txt").write_text("untracked")
        with self.assertRaisesRegex(profile.InvalidProfile, "untracked files"):
            profile.build_profile(benchmark_root=self.root)

    def test_symlink_anywhere_in_source_is_rejected(self):
        (self.root / "untracked-link").symlink_to(self.root / "benchmarks")
        with self.assertRaisesRegex(profile.InvalidProfile, "symlink"):
            profile.build_profile(benchmark_root=self.root)

    def test_source_root_symlink_is_rejected(self):
        link = Path(self.temp.name) / "linked-root"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(profile.InvalidProfile, "symlink"):
            profile.build_profile(benchmark_root=link)

    def test_nonroot_checkout_path_is_rejected(self):
        with self.assertRaisesRegex(profile.InvalidProfile, "repository root"):
            profile.build_profile(benchmark_root=self.root / "benchmarks")

    def test_hash_verification_catches_assume_unchanged_file(self):
        relative = "benchmarks/gdpevo/tasks/tg003-train-001/solution/answer.json"
        self.run_git(self.root, "update-index", "--assume-unchanged", relative)
        (self.root / relative).write_text("opaque dirty bytes hidden from git status")
        with self.assertRaisesRegex(profile.InvalidProfile, "file bytes differ"):
            profile.build_profile(benchmark_root=self.root)


if __name__ == "__main__":
    unittest.main()
