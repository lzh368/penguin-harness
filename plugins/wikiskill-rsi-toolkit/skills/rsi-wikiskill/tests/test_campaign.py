"""Campaign CLI behaviour, using synthetic local files and no model calls.

Given / When / Then scenarios:
- A verified profile is initialized twice: identities stay independent and no
  baseline/state or Penguin home is created; different settings are refused.
- A profile is unverified, corrupted, or changes the agreed experiment: init
  refuses it without creating a campaign.
- A phase is planned: its complete allowlist and declared deployment are bound;
  reusing its label or using a test label in another phase cannot overwrite it.
- A frozen campaign, profile, plan, or deployment is changed: consumers refuse it.
- All six test matrices are complete: summary recomputes paired and domain
  statistics from cells, ignoring a prewritten summary.
- A replica/cell is missing, duplicated, failed, misconfigured or reused: summary
  refuses a partial or misleading three-replica result.
- A source or campaign path is a symlink: no linked location is read or written.
- Campaign and Penguin home overlap, or a project ID escapes its directory:
  initialization refuses the layout before writing any campaign files.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def rehash(value, field):
    value.pop(field, None)
    value[field] = hashlib.sha256(json_bytes(value)).hexdigest()
    return value


class CampaignCLITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "profile.json"
            subprocess.run([sys.executable, "-B", str(SCRIPTS / "gdpevo_profile.py"),
                            "--out", str(output)], check=True, capture_output=True)
            cls.unverified = json.loads(output.read_bytes())
        # A synthetic attestation, used only in this temporary offline fixture.
        # The source-checking CLI has its own real Git fixture coverage.
        cls.verified = deepcopy(cls.unverified)
        cls.verified["source_verified"] = True
        cls.verified["source_verification"] = {
            "method": "pinned_git_and_opaque_file_hashes", "file_count": 1234,
            "source_tree_sha256": "a" * 64}
        rehash(cls.verified, "profile_sha256")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile_path = self.root / "synthetic-profile.json"
        self.profile_path.write_bytes(json_bytes(self.verified))
        self.campaign = self.root / "campaign"
        self.home = self.root / "uncreated-penguin-home"

    def cli(self, *args, succeeds=True):
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "campaign.py"), *map(str, args)],
                                capture_output=True, text=True)
        if succeeds:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        return result.stderr

    def initialize(self, **kwargs):
        return self.cli("init", "--profile", self.profile_path, "--out", self.campaign,
                        "--penguin-home", kwargs.pop("home", self.home),
                        "--project-id", kwargs.pop("project", "offline_project"), **kwargs)

    def plan(self, replica="r1", phase="test", label="test-H0", version=1, skill_hash=None, **kwargs):
        return self.cli("plan", "--campaign", self.campaign, "--replica", replica,
                        "--phase", phase, "--label", label, "--version", version,
                        "--skill-hash", skill_hash or ("0" * 64 if label == "test-H0" else "1" * 64),
                        **kwargs)

    def cells(self, planned, replica_index=0, final=False):
        plan = json.loads(Path(planned["plan"]).read_bytes())
        for index, case_id in enumerate(plan["case_ids"]):
            group = int(case_id.split("-")[1]) - 1
            group //= 5
            row = {"protocol_version": 1, "status": "ok", "case_id": case_id, "run": 1,
                   "agent_id": plan["agent_id"], "expected_version": plan["expected_version"],
                   "provider": plan["provider"], "model_id": plan["model_id"],
                   "thinking_level": plan["thinking_level"],
                   "score": 10 * (replica_index + 1) + 2 * group + ((3 - 3 * replica_index) if final else 0),
                   "cost": None, "duration_ms": 1,
                   "session_id": f"synthetic:{plan['agent_id']}:{Path(planned['plan_dir']).name}:{index}"}
            (Path(planned["cells"]) / f"{index:03d}.json").write_bytes(json_bytes(row))

    def complete(self, replicas=("r1", "r2", "r3")):
        config = self.initialize()
        for index, replica in enumerate(replicas):
            for final, label in ((False, "test-H0"), (True, "test-final")):
                planned = self.plan(replica=replica, label=label, version=2 if final else 1)
                self.cells(planned, index, final)
        return config

    def summary(self, **kwargs):
        return self.cli("summary", "--campaign", self.campaign, **kwargs)

    def test_initialization_is_repeatable_without_fabricating_runtime_state(self):
        first = self.initialize()
        frozen_bytes = (self.campaign / "campaign.json").read_bytes()
        self.assertEqual(first, self.initialize())
        self.assertEqual(len({item["target_id"] for item in first["replicas"]}), 3)
        self.assertEqual(len({item["controller_agent_id"] for item in first["replicas"]}), 3)
        self.assertEqual(len({item["out"] for item in first["replicas"]}), 3)
        self.assertTrue(all(Path(item["plans"]).is_dir() for item in first["replicas"]))
        self.assertFalse(any(Path(item["out"]).exists() for item in first["replicas"]))
        self.assertFalse(self.home.exists())
        self.assertFalse(list(self.campaign.rglob("state.json")))
        self.assertFalse(list(self.campaign.rglob("*baseline*")))
        self.initialize(project="another_project", succeeds=False)
        self.assertEqual((self.campaign / "campaign.json").read_bytes(), frozen_bytes)

    def test_unverified_corrupt_or_changed_experiments_cannot_initialize(self):
        bad = [deepcopy(self.unverified)]
        corrupted = deepcopy(self.verified)
        corrupted["config"]["max_iterations"] = 3
        bad.append(corrupted)
        for edit in (
                lambda p: p["config"]["role_models"]["target"].update(model_id="different-model"),
                lambda p: p["config"].update(runs_per_case=2),
                lambda p: p["config"].update(replicas=["r1", "r1", "r3"]),
                lambda p: p["phases"]["train"]["tasks"].pop(),
                lambda p: p["source_refs"].update(benchmark_commit="0" * 40),
                lambda p: p["source_verification"].update(source_tree_sha256="invalid"),
                lambda p: p["config"].update(max_iterations=True)):
            value = deepcopy(self.verified)
            edit(value)
            bad.append(rehash(value, "profile_sha256"))
        for value in bad:
            with self.subTest(profile=value["profile_sha256"]):
                self.profile_path.write_bytes(json_bytes(value))
                self.initialize(succeeds=False)
                self.assertFalse(self.campaign.exists())

    def test_validation_plan_runs_through_existing_aggregate_and_cannot_be_overwritten(self):
        self.initialize()
        planned = self.plan(phase="validation", label="validation-H0")
        self.cells(planned)
        plan = json.loads(Path(planned["plan"]).read_bytes())
        self.assertEqual(plan["phase"], "validation")
        self.assertTrue(all(case.endswith("train-005") for case in plan["case_ids"]))
        aggregated = subprocess.run([sys.executable, "-B", str(SCRIPTS / "aggregate.py"),
                                    "--plan", planned["plan"], "--results", planned["cells"]],
                                   capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(aggregated.stdout)["cell_count"], 6)
        before = {p.name: p.read_bytes() for p in Path(planned["cells"]).iterdir()}
        self.plan(phase="validation", label="validation-H0", succeeds=False)
        self.assertEqual({p.name: p.read_bytes() for p in Path(planned["cells"]).iterdir()}, before)
        for options in ({"phase": "train", "label": "test-H0"},
                        {"phase": "test", "label": "extra-test"},
                        {"label": "../escape"}, {"version": 0}, {"skill_hash": "bad"}):
            with self.subTest(options=options):
                self.plan(**options, succeeds=False)
        self.assertEqual([p.name for p in Path(planned["plan_dir"]).parent.iterdir()], ["validation-H0"])
        self.assertEqual(planned["deployment"]["binding_status"], "declared")

    def test_modified_campaign_or_frozen_profile_blocks_new_plans(self):
        self.initialize()
        for filename in ("campaign.json", "profile.json"):
            target = self.campaign / filename
            original = target.read_bytes()
            value = json.loads(original)
            if filename == "campaign.json":
                value["replicas"][1]["target_id"] = value["replicas"][0]["target_id"]
                rehash(value, "campaign_sha256")
                target.write_bytes(json_bytes(value))
            else:
                target.write_bytes(original + b"\n")
            self.plan(succeeds=False)
            self.assertFalse((self.campaign / "replicas/r1/plans/test-H0").exists())
            target.write_bytes(original)

    def test_summary_recomputes_complete_paired_and_domain_statistics(self):
        config = self.complete()
        (self.campaign / "summary.json").write_text('{"overall":{"final":{"mean":"100"}}}')
        result = self.summary()
        self.assertEqual(result["campaign_sha256"], config["campaign_sha256"])
        self.assertEqual([Decimal(item["H0"]["score"]) for item in result["paired"]], [15, 25, 35])
        self.assertEqual([Decimal(item["final"]["score"]) for item in result["paired"]], [18, 25, 32])
        self.assertEqual([Decimal(item["delta"]) for item in result["paired"]], [3, 0, -3])
        self.assertEqual(Decimal(result["overall"]["final"]["mean"]), 25)
        self.assertEqual(Decimal(result["overall"]["delta"]["mean"]), 0)
        self.assertAlmostEqual(float(result["overall"]["H0"]["population_std"]), math.sqrt(200 / 3))
        self.assertAlmostEqual(float(result["overall"]["delta"]["population_std"]), math.sqrt(6))
        self.assertEqual(len(result["domains"]), 6)
        self.assertEqual(Decimal(result["domains"]["CRM"]["final"]["mean"]), 20)
        self.assertEqual(Decimal(result["domains"]["Dataanalysis"]["H0"]["mean"]), 30)
        for item in result["paired"]:
            self.assertTrue(item["final"]["evaluation_id"].startswith("sha256:"))
            self.assertEqual(item["final"]["cell_count"], 30)

    def test_missing_replica_cannot_be_reported_as_three_runs(self):
        self.complete(replicas=("r1", "r2"))
        self.summary(succeeds=False)
        self.assertFalse((self.campaign / "summary.json").exists())

    def test_invalid_cells_cannot_be_hidden_by_a_precomputed_summary(self):
        self.complete()
        cells = self.campaign / "replicas/r3/plans/test-final/cells"
        first = cells / "000.json"
        original = first.read_bytes()
        row = json.loads(original)
        other = json.loads((cells / "001.json").read_bytes())
        h0 = json.loads((self.campaign / "replicas/r1/plans/test-H0/cells/000.json").read_bytes())
        mutations = [{**row, "status": "failed"}, {**row, "score": True},
                     {**row, "model_id": "other"}, {**row, "expected_version": 3},
                     {**row, "case_id": other["case_id"]}, {**row, "session_id": h0["session_id"]},
                     {**row, "score": float("nan")}]
        (self.campaign / "summary.json").write_text('{"complete":true,"score":100}')
        for changed in mutations:
            with self.subTest(changed=changed):
                first.write_bytes(json_bytes(changed))
                self.summary(succeeds=False)
        first.unlink()
        self.summary(succeeds=False)
        first.write_bytes(original)
        (cells / "extra.json").write_bytes(original)
        self.summary(succeeds=False)

    def test_changed_test_plan_or_deployment_cannot_change_the_frozen_matrix(self):
        self.complete()
        folder = self.campaign / "replicas/r3/plans/test-final"
        for filename, mutate in (
                ("plan.json", lambda value: value["case_ids"].pop()),
                ("plan.json", lambda value: value.update(phase="validation")),
                ("deployment.json", lambda value: value.update(replica_id="r1")),
                ("deployment.json", lambda value: value.update(skill_sha256="bad"))):
            target = folder / filename
            original = target.read_bytes()
            value = json.loads(original)
            mutate(value)
            if filename == "deployment.json":
                rehash(value, "deployment_sha256")
            target.write_bytes(json_bytes(value))
            self.summary(succeeds=False)
            target.write_bytes(original)

    def test_symlink_profile_and_campaign_paths_are_refused_without_writes(self):
        linked = self.root / "profile-link.json"
        linked.symlink_to(self.profile_path)
        original_profile = self.profile_path
        self.profile_path = linked
        self.initialize(succeeds=False)
        self.assertFalse(self.campaign.exists())
        self.profile_path = original_profile
        self.initialize()
        real_campaign = self.campaign
        self.campaign = self.root / "campaign-link"
        self.campaign.symlink_to(real_campaign, target_is_directory=True)
        self.plan(succeeds=False)
        self.summary(succeeds=False)
        self.assertFalse((real_campaign / "replicas/r1/plans/test-H0").exists())

    def test_duplicate_json_keys_are_refused_before_initialization(self):
        self.profile_path.write_text('{"source_verified":false,"source_verified":true}')
        self.initialize(succeeds=False)
        self.assertFalse(self.campaign.exists())

    def test_unsafe_project_ids_or_nested_homes_are_refused_before_writes(self):
        for project in (".", "..", "../escape", "bad/project", "bad\\project", "\tproject",
                        "bad-project", "UPPER", "1project", "a"):
            with self.subTest(project=project):
                self.initialize(project=project, succeeds=False)
                self.assertFalse(self.campaign.exists())
        for home in (self.campaign, self.campaign / "home", self.root):
            with self.subTest(home=home):
                self.initialize(home=home, succeeds=False)
                self.assertFalse(self.campaign.exists())


if __name__ == "__main__":
    unittest.main()
