"""Offline Terminal-Bench preparation: synthetic tasks, no models or Docker."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/terminal_bench.py"
MODULE = importlib.util.spec_from_file_location("terminal_bench_tool", SCRIPT)
tool = importlib.util.module_from_spec(MODULE)
MODULE.loader.exec_module(tool)


class TerminalBenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        categories = ["Systems"] * 5 + ["Frontend"] * 4 + ["Data"] * 3 + ["Algorithms"] * 3 + ["Databases"] * 3
        for index, subcategory in enumerate(categories):
            self.task(f"software-{index:02d}", "Software", subcategory)
        self.task("science-only", "Science", "Biology")
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic pinned metadata")
        self.revision = self.git("rev-parse", "HEAD").strip()

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout

    def task(self, name, category, subcategory):
        directory = self.root / "tasks" / name
        directory.mkdir(parents=True)
        (directory / "task.toml").write_text(f'''version = "1.0"
[metadata]
category = "{category}"
subcategory = "{subcategory}"
[environment]
cpus = 2
memory_mb = 4096
storage_mb = 10240
skills_dir = "/app/.agents/skills"
[agent]
timeout_sec = 28800
[verifier]
timeout_sec = 300
environment_mode = "separate"
[verifier.env]
PRIVATE_SYNTHETIC_TOKEN = "never-output-this-value"
''')
        (directory / "instruction.md").write_text("PRIVATE TASK TEXT " + name)
        (directory / "environment").mkdir()
        (directory / "environment/Dockerfile").write_text("FROM fixture.invalid/image@sha256:" + "a" * 64)
        (directory / "tests").mkdir()
        (directory / "tests/test.sh").write_text("PRIVATE VERIFIER TEXT " + name)

    def prepare(self, **kwargs):
        return tool.build_spec(self.root, repository="https://example.invalid/fixture/tasks", revision=self.revision,
                               provider="chosen", model_id="chosen/model", thinking="xhigh", **kwargs)

    def test_inventory_reads_category_and_only_safe_resource_metadata(self):
        inventory = tool.inventory(self.root, self.revision)
        self.assertEqual(len(inventory), 18)
        self.assertNotIn("science-only", {item["original_id"] for item in inventory})
        output = json.dumps(inventory)
        for private in ("never-output-this-value", "PRIVATE TASK TEXT", "PRIVATE VERIFIER TEXT", "PRIVATE_SYNTHETIC_TOKEN"):
            self.assertNotIn(private, output)
        self.assertEqual(inventory[0]["environment"], {"cpus": 2, "memory_mb": 4096, "storage_mb": 10240})
        self.assertTrue(inventory[0]["declares_builtin_skills"])
        self.assertEqual(inventory[0]["verifier_environment_mode"], "separate")
        self.assertEqual(inventory[0]["environment_network_mode"], "public")
        self.assertIsNone(inventory[0]["agent_network_mode"])

    def test_stratification_is_reproducible_and_covers_every_selected_task_once(self):
        inventory = tool.inventory(self.root, self.revision)
        split = tool.stratify(inventory)
        self.assertEqual([len(split[phase]) for phase in tool.PHASES], [8, 5, 5])
        self.assertEqual(tool.stratify(list(reversed(inventory))), split)
        self.assertEqual(len(set(sum(split.values(), []))), 18)
        self.assertNotEqual(tool.stratify(inventory, seed="different"), split)

    def test_counts_must_cover_catalog_and_train_reading_minimum(self):
        inventory = tool.inventory(self.root, self.revision)
        for counts in ((8, 5, 4), (3, 7, 8), (8, 10, 0), (8, 10), (8, 5, True)):
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                tool.stratify(inventory, counts)

    def test_prepare_binds_source_tasks_and_explicit_runtime(self):
        frozen, inventory = self.prepare(iterations=2, concurrency=7, cell_timeout=900, max_turns=60)
        self.assertEqual(frozen["status"], "planning_only")
        self.assertEqual(frozen["spec"]["adapter"], {"id": "penguin-harbor", "version": "0.23.0"})
        self.assertEqual(len(frozen["spec"]["tasks"]), 18)
        self.assertEqual(frozen["spec"]["config"]["concurrency"], 7)
        self.assertEqual(frozen["spec"]["config"]["max_iterations"], 2)
        self.assertEqual(frozen["spec"]["config"]["max_turns"], 60)
        self.assertEqual(frozen["spec"]["config"]["role_models"]["target"],
                         {"provider": "chosen", "model_id": "chosen/model", "thinking_level": "xhigh"})
        self.assertEqual(frozen["spec"]["source"]["revision"], self.revision)
        for task in frozen["spec"]["tasks"]:
            self.assertEqual(task["content_sha256"], tool.binding().task_digest(self.root, task["path"]))
        self.assertEqual(tool.binding().benchmark_id(frozen), "wikiskill-" + frozen["spec_sha256"][:16])

    def test_explicit_split_is_retained_without_reshuffling(self):
        inventory = tool.inventory(self.root, self.revision)
        names = [item["original_id"] for item in inventory]
        split = {"train": list(reversed(names[:8])), "validation": names[8:13], "test": names[13:]}
        frozen, _ = self.prepare(split=split, seed="ignored")
        self.assertEqual(frozen["spec"]["phases"], split)
        bad = deepcopy(split)
        bad["test"][0] = bad["train"][0]
        with self.assertRaises(ValueError):
            self.prepare(split=bad)

    def test_all_task_timeout_margins_checked_before_freezing(self):
        for timeout in (30, 28800):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.prepare(cell_timeout=timeout)

    def test_wrong_revision_dirty_or_non_repository_source_fails(self):
        with self.assertRaises(ValueError):
            tool.inventory(self.root, "0" * 40)
        with self.assertRaises(ValueError):
            tool.inventory(self.root / "tasks", self.revision)
        (self.root / "tasks/software-00/instruction.md").write_text("modified")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_unknown_category_and_linked_task_directory_fail(self):
        with self.assertRaises(ValueError):
            tool.inventory(self.root, self.revision, "missing")
        (self.root / "tasks/linked").symlink_to(self.root / "tasks/software-00", target_is_directory=True)
        with self.assertRaises(ValueError):
            tool.inventory(self.root, self.revision)

    def test_plan_is_exact_complete_and_smoke_is_only_one_train_case(self):
        frozen, _ = self.prepare()
        plan = tool.phase_plan(frozen, "validation", "wiki_target_r1", 3)
        self.assertEqual(set(plan), {"phase", "benchmark_id", "case_ids", "runs_per_case", "agent_id",
                                     "expected_version", "provider", "model_id", "thinking_level"})
        self.assertEqual(plan["case_ids"], frozen["spec"]["phases"]["validation"])
        smoke = frozen["spec"]["phases"]["train"][0]
        self.assertEqual(tool.phase_plan(frozen, "train", "wiki_target_smoke", 2, smoke)["case_ids"], [smoke])
        with self.assertRaises(ValueError):
            tool.phase_plan(frozen, "validation", "wiki_target_smoke", 2, smoke)
        with self.assertRaises(ValueError):
            tool.phase_plan(frozen, "train", "../unsafe", 2)
        with self.assertRaises(ValueError):
            tool.phase_plan(frozen, "train", "wiki_target_smoke", 0)

    def command_args(self):
        base = Path(self.temp.name)
        adapter = base / "adapter"
        (adapter / "agents/penguin_agent").mkdir(parents=True)
        (adapter / "agents/penguin_agent/agent.py").write_text("not executed")
        state = base / "state archive.tar.gz"
        state.write_bytes(b"fixture state")
        bundle = base / "bundle.tar.gz"
        bundle.write_bytes(b"fixture bundle")
        home = base / "home"
        home.mkdir()
        return dict(adapter_root=adapter, agent_id="wiki_target_r1", state_archive=state, bundle=bundle,
                    host_home=home, project_id="default_project", api_host="api.example.invalid",
                    job_name="test-r1-case-1", jobs_dir=base / "jobs")

    def test_command_reuses_existing_adapter_and_explicit_model_without_running(self):
        frozen, _ = self.prepare()
        case = frozen["spec"]["phases"]["train"][0]
        result = tool.command(frozen, self.root, case, **self.command_args())
        argv = result["argv"]
        self.assertFalse(result["executed"])
        self.assertFalse(result["runtime_verified"])
        self.assertIn("penguin_agent:PenguinAgent", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "chosen/chosen/model")
        self.assertEqual(argv[argv.index("-i") + 1], case)
        self.assertIn("thinking=xhigh", argv)
        self.assertFalse(any(value.startswith("max_turns=") for value in argv))
        self.assertEqual(result["required_state_max_turns"], 200)
        self.assertTrue(result["network_preflight_required"])
        self.assertEqual(result["source_agent_network_mode"], "public")
        self.assertIn("run_timeout=1500s", argv)
        self.assertIn("--allow-agent-host", argv)
        self.assertIn("No dollar cap", " ".join(result["notes"]))
        self.assertNotIn("PRIVATE", json.dumps(result))

    def test_command_refuses_missing_state_unknown_task_and_wildcard_host(self):
        frozen, _ = self.prepare()
        case = frozen["spec"]["phases"]["train"][0]
        kwargs = self.command_args()
        for changes in ({"api_host": "*"}, {"api_host": "https://api.example.invalid"},
                        {"state_archive": Path(self.temp.name) / "missing"}, {"job_name": "../../outside"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tool.command(frozen, self.root, case, **{**kwargs, **changes})
        with self.assertRaises(ValueError):
            tool.command(frozen, self.root, "unknown", **kwargs)

    def test_prepare_cli_and_plan_cli_emit_separate_immutable_outputs(self):
        output = Path(self.temp.name) / "spec.json"
        proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "prepare", "--source-root", str(self.root),
            "--revision", self.revision, "--repository", "https://example.invalid/fixture/tasks",
            "--provider", "chosen", "--model-id", "chosen/model", "--thinking", "xhigh", "--output", str(output)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        summary = json.loads(proc.stdout)
        self.assertTrue(summary["source_verified"])
        self.assertFalse(summary["runtime_verified"])
        self.assertEqual(sum(map(len, summary["phases"].values())), 18)
        plan_path = Path(self.temp.name) / "plan.json"
        proc = subprocess.run([sys.executable, "-B", str(SCRIPT), "plan", "--spec", str(output),
            "--source-root", str(self.root), "--phase", "test", "--agent-id", "wiki_target_r1",
            "--expected-version", "3", "--output", str(plan_path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(plan_path.read_text())["case_ids"], summary["phases"]["test"])
        self.assertEqual(json.loads(output.read_text())["spec_sha256"], summary["spec_sha256"])


if __name__ == "__main__":
    unittest.main()
