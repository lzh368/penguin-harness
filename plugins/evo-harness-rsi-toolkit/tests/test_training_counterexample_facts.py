"""Facts for the guidance walkthrough, not a semantic model or score test."""
import unittest


class TrainingCounterexampleFactsTests(unittest.TestCase):
    def test_shared_exclusion_union_does_not_redefine_reason_specific_outputs(self):
        rows = [
            {'id': 'withdrawn-only', 'status': 'Cancelled', 'duplicate_of': None},
            {'id': 'linked', 'status': 'Closed', 'duplicate_of': 'canonical'},
            {'id': 'duplicate-status', 'status': 'Duplicate', 'duplicate_of': None},
            {'id': 'included', 'status': 'Closed', 'duplicate_of': None},
        ]
        duplicates = {r['id'] for r in rows if r['status'] == 'Duplicate' or r['duplicate_of'] is not None}
        withdrawn = {r['id'] for r in rows if r['status'] == 'Cancelled'}
        excluded = duplicates | withdrawn
        self.assertEqual(duplicates, {'linked', 'duplicate-status'})
        self.assertEqual(withdrawn, {'withdrawn-only'})
        self.assertNotIn('withdrawn-only', duplicates)
        self.assertIn('withdrawn-only', excluded)
        self.assertEqual(len(rows) - len(excluded), 1)

    def test_task_objective_supports_a_decision_without_an_extra_label_lookup(self):
        plans = [{'id': 'a', 'capacity': 5, 'cost': 10},
                 {'id': 'b', 'capacity': 8, 'cost': 15},
                 {'id': 'c', 'capacity': 8, 'cost': 12}]
        required_capacity = 8
        feasible = [p for p in plans if p['capacity'] >= required_capacity]
        self.assertEqual(min(feasible, key=lambda p: p['cost'])['id'], 'c')
        self.assertNotEqual(min(plans, key=lambda p: p['cost'])['id'], 'c')


if __name__ == '__main__':
    unittest.main()
