# Copyright 2026 Michael Still and contributors

"""Tests for tools/derive-database-load-budget.py.

The tool's arithmetic is an ordinary least squares fit and is not very
interesting. What is worth testing is the judgement encoded around it,
because each piece of that judgement came from a way the budget could be
silently wrong: assigning a cluster-wide sweep's cost to a per-node term
would make the model scale with cluster size when the work does not;
deriving from a window which predates #3708 would bake in a two-node
undercount that then reads as a regression; and letting a re-derivation
overwrite a term which is arithmetic about the code rather than a
measurement would quietly revert the one entry a measurement gets wrong.
"""

import importlib.util
import os

from shakenfist.schema import database_load_budget as budget
from shakenfist.tests import base


def _load_tool():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    path = os.path.join(root, 'tools', 'derive-database-load-budget.py')
    spec = importlib.util.spec_from_file_location('derive_budget', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


def stats(slope=0.0, intercept=0.0, r2=0.0, mean=1.0, samples=100):
    return {'slope': slope, 'intercept': intercept, 'r2': r2,
            'mean': mean, 'samples': samples}


class DeriveBudgetTestCase(base.ShakenFistTestCase):
    def test_fit_recovers_a_known_line(self):
        xs = [float(x) for x in range(20)]
        ys = [3.0 + 0.5 * x for x in xs]
        slope, intercept, r2 = tool.fit(xs, ys)
        self.assertAlmostEqual(0.5, slope)
        self.assertAlmostEqual(3.0, intercept)
        self.assertAlmostEqual(1.0, r2)

    def test_fit_of_a_flat_series_has_no_slope(self):
        xs = [float(x) for x in range(20)]
        ys = [7.0] * 20
        slope, intercept, r2 = tool.fit(xs, ys)
        self.assertAlmostEqual(0.0, slope)
        self.assertAlmostEqual(7.0, intercept)

    def test_cluster_sweep_cost_is_not_per_node(self):
        # The elected cluster daemon does its sweeps once for the whole
        # cluster. Dividing that by node count would model it as growing
        # with the cluster when it does not.
        entry = tool.to_entry(('GetBlob', 'cluster'),
                              stats(intercept=1.2, mean=1.2), nodes=6)
        self.assertEqual(1.2, entry['cluster_base_qps'])
        self.assertNotIn('per_node_base_qps', entry)

    def test_daemon_state_poll_is_per_node_even_from_the_cluster_daemon(self):
        # Every node runs a cluster daemon and every one of them polls its
        # own state row, whether or not it holds the maintenance lock.
        entry = tool.to_entry(('GetNodeDaemonState', 'cluster'),
                              stats(intercept=2.49, mean=2.49), nodes=6)
        self.assertIn('per_node_base_qps', entry)

    def test_code_derived_terms_override_the_measurement(self):
        # sfcbr was measured before #3874 was deployed, so the elected
        # daemon was not polling at all and the fitted intercept is wrong.
        entry = tool.to_entry(('GetNodeDaemonState', 'cluster'),
                              stats(intercept=2.49, mean=2.49), nodes=6)
        self.assertEqual(0.5, entry['per_node_base_qps'])
        self.assertEqual(-0.3, entry['cluster_base_qps'])

    def test_code_derived_terms_match_the_committed_budget(self):
        # If these drift apart, a re-derivation silently changes a term the
        # committed file states deliberately.
        for key, terms in tool.CODE_DERIVED_TERMS.items():
            entry = budget.load_budget().get(*key)
            self.assertIsNotNone(entry, '%s/%s is not in the budget' % key)
            for term, value in terms.items():
                self.assertEqual(value, getattr(entry, term),
                                 '%s/%s %s' % (key[0], key[1], term))

    def test_a_significant_slope_becomes_a_per_instance_term(self):
        entry = tool.to_entry(('GetInstanceAttributes', 'net'),
                              stats(slope=0.35, intercept=0.2, r2=0.76,
                                    mean=6.7), nodes=6)
        self.assertEqual(0.35, entry['per_instance_qps'])

    def test_a_poorly_fitting_slope_is_not_a_per_instance_term(self):
        # A slope which explains none of the variance is noise, and
        # committing it would make the budget grow with instance count for
        # a loop which does not.
        entry = tool.to_entry(('GetBlobTransfersForNode', 'transfers'),
                              stats(slope=0.3, intercept=3.05, r2=0.01,
                                    mean=3.06), nodes=6)
        self.assertNotIn('per_instance_qps', entry)

    def test_a_tiny_slope_is_not_a_per_instance_term(self):
        entry = tool.to_entry(('Dequeue', 'queues'),
                              stats(slope=0.0008, intercept=3.07, r2=0.9,
                                    mean=3.2), nodes=6)
        self.assertNotIn('per_instance_qps', entry)

    def test_api_traffic_is_marked_activity_coupled(self):
        entry = tool.to_entry(('GetInstanceAttributes', 'api'),
                              stats(slope=0.31, intercept=1.0, r2=0.74,
                                    mean=6.7), nodes=6)
        self.assertTrue(entry['activity_coupled'])

    def test_every_entry_gets_at_least_one_term(self):
        # A pair with no measurable base and no slope still needs a term,
        # or the budget carries an entry which predicts nothing and the
        # schema refuses to load.
        entry = tool.to_entry(('Quiet', 'net'), stats(mean=0.3), nodes=6)
        self.assertIn('per_node_base_qps', entry)
        budget.BudgetEntry(**{
            k: v for k, v in entry.items() if k != 'measured'})

    def test_notes_are_placeholders_and_say_so(self):
        entry = tool.to_entry(('Quiet', 'net'), stats(mean=0.3), nodes=6)
        self.assertIn('TODO', entry['note'])

    def test_coverage_epoch_is_after_the_counter_saw_everything(self):
        # #3708 reached sfcbr on 2026-08-11 in the evening UTC. A window
        # starting before this is refused without an explicit override.
        self.assertLess(tool.parse_time('2026-08-11T20:00:00Z'),
                        tool.parse_time(tool.COVERAGE_EPOCH))

    def test_parse_time_accepts_both_forms(self):
        self.assertEqual(tool.parse_time('1787270400'),
                         tool.parse_time('2026-08-21T00:00:00Z'))
