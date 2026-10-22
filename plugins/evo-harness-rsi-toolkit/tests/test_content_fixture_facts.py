"""Arithmetic/counterexample checks for author-reviewed synthetic scenarios.

These checks deliberately do not parse lesson prose, blacklist keywords, act as
an LLM judge or claim that any future generated skill will satisfy the method.
The semantic walkthrough and its uncertainties are retained in the fixture.
"""
from datetime import datetime
import json
from pathlib import Path
import unittest

CASES = json.loads((Path(__file__).parent / "fixtures/instruction_content_cases.json").read_text())


class ContentFixtureFactsTests(unittest.TestCase):
    def setUp(self):
        self.cases = {case["id"]: case for case in CASES["cases"]}

    def test_correct_aggregate_has_a_distinct_explicit_output_gap(self):
        case = self.cases["correct_step_with_low_score"]
        facts = case["authorized_facts"]
        total = sum(row["amount"] for row in facts["rows"] if row["region"] == facts["required_region"])
        self.assertEqual(total, case["original_answer"]["total"])
        self.assertEqual(total, case["expected"]["total"])
        missing = sorted(set(facts["required_fields"]) - set(case["original_answer"]))
        self.assertEqual(missing, case["expected"]["missing_required_fields"])
        self.assertIsNone(case["external_feedback"]["field_attribution"])

    def test_threshold_is_absent_not_a_zero_or_default(self):
        case = self.cases["unknown_threshold"]
        self.assertIsNone(case["authorized_facts"]["policy_threshold_minutes"])
        self.assertIsNone(case["expected"]["cutoff"])
        self.assertGreater(case["authorized_facts"]["latency_minutes"], 0)
        self.assertIsNone(case["external_feedback"]["field_attribution"])

    def test_explicit_independent_field_definitions_admit_opposite_flags(self):
        case = self.cases["field_definition_not_cross_field_precondition"]
        record = case["authorized_facts"]["record"]
        eligible = record["credit_balance"] > 0
        overdue = record["due_day"] < record["as_of_day"] and record["unpaid_amount"] > 0
        self.assertEqual(eligible, case["expected"]["credit_eligible"])
        self.assertEqual(overdue, case["expected"]["payment_overdue"])
        self.assertNotEqual(eligible, overdue)

    def test_event_count_and_calendar_day_count_differ(self):
        case = self.cases["runs_are_not_days"]
        failed = [run for run in case["authorized_facts"]["runs"] if run["status"] == "failed"]
        dates = {datetime.fromisoformat(run["time"].replace("Z", "+00:00")).date() for run in failed}
        self.assertEqual(len(failed), case["expected"]["failed_runs"])
        self.assertEqual(len(dates), case["expected"]["distinct_failed_days"])
        self.assertNotEqual(len(failed), len(dates))

    def test_dropped_join_precondition_changes_total(self):
        case = self.cases["merge_preserves_condition"]
        facts = case["authorized_facts"]
        keys = [(row["customer"], row["entity"], row["period"]) for row in facts["counterexample_mapping"]]
        total = sum(order["amount"] for order in facts["orders"])
        naive = sum(order["amount"] for order in facts["orders"] for key in keys
                    if key == (order["customer"], order["entity"], order["period"]))
        self.assertEqual(total, case["expected"]["original_total"])
        self.assertEqual(naive, case["expected"]["unconditional_join_total"])
        self.assertEqual(len(keys) == len(set(keys)), case["expected"]["mapping_unique"])
        self.assertNotEqual(total, naive)


if __name__ == "__main__":
    unittest.main()
