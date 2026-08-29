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

import copy
import importlib.util
import io
import os
import types

import yaml

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

    def test_the_shipped_budget_says_what_its_generator_says(self):
        """The committed _doc prose is the generator's, word for word.

        The rules file has test_committed_rules_match_the_generator and so
        cannot drift from what produces it. The budget had nothing, and it
        had already drifted: the shipped _doc.method described the fit
        without naming the regressor, which is precisely the question
        AGENTS.md sends a new consumer to _doc.method to answer.

        Only the prose is compared. derived_from, window, samples and
        cluster_shape are facts about a particular measurement, and
        coverage_of_total is half computed from one, so the fixed half of
        that is checked by suffix.
        """
        doc = yaml.safe_load(io.StringIO(budget.budget_text()))['_doc']
        for key, expected in (('method', tool.DOC_METHOD),
                              ('base_term_caveat', tool.DOC_BASE_TERM_CAVEAT),
                              ('coverage_caveat', tool.DOC_COVERAGE_CAVEAT),
                              ('rederive', tool.DOC_REDERIVE)):
            self.assertEqual(
                ' '.join(expected.split()), ' '.join(doc[key].split()),
                '_doc.%s is not what tools/derive-database-load-budget.py '
                'writes' % key)
        self.assertTrue(
            ' '.join(doc['coverage_of_total'].split()).endswith(
                ' '.join(tool.DOC_INCLUSION_CUT_LESSON.split())),
            '_doc.coverage_of_total does not end with the generator\'s '
            'explanation of where the inclusion cut sits')

    def test_the_shipped_budget_carries_the_generators_header(self):
        # The file says "DO NOT hand-edit levels to make a check pass" and
        # names the tool which rewrites it. Neither survives a
        # re-derivation unless the generator emits them, so assert the
        # committed file is carrying the generator's copy rather than one
        # somebody pasted.
        self.assertTrue(budget.budget_text().startswith(tool.FILE_HEADER))

    def test_code_derived_terms_match_the_daemons(self):
        # The tool is standalone and cannot import the server, so it
        # restates the two intervals the GetNodeDaemonState/cluster entry
        # is arithmetic about. Restated is fine; drifted is not, and the
        # drift would show up as a budget term rather than as an error.
        from shakenfist.daemons.cluster import main as cluster_main
        from shakenfist.daemons import daemon

        self.assertEqual(float(daemon.DAEMON_STATE_POLL_INTERVAL),
                         tool.DAEMON_STATE_POLL_INTERVAL)
        self.assertEqual(float(cluster_main.ELECTED_LOOP_POLL_SECONDS),
                         tool.ELECTED_LOOP_POLL_SECONDS)

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


class EmitRoundTripTestCase(base.ShakenFistTestCase):
    """What this tool writes has to be a budget this repository accepts.

    It was not. emit() wrote five of the ten _doc keys, no provisional
    block and no notes, so running the command in the tool's own docstring
    produced a file which failed test_doc_block_records_its_provenance and
    test_provisional_entries_name_an_issue_and_are_not_enforced. The
    docstring described restoring some of that by hand and did not mention
    the rest. A generator whose output does not pass is a generator nobody
    runs, and a budget nobody can regenerate is a budget which drifts.
    """

    def setUp(self):
        super().setUp()
        self.previous = yaml.safe_load(budget.budget_text())
        self.args = types.SimpleNamespace(
            cluster='sfcbr', start_text='2026-08-21T00:00:00Z',
            end_text='2026-08-24T18:00:00Z', step=1800, nodes=6,
            roles='6 hypervisors, of which 1 also network and 2 also '
                  'database')
        self.by_time = {t: 8.0 + (t % 40) for t in range(181)}
        self.coverage = {'kept': 105, 'dropped': 296, 'dropped_qps': 4.1,
                         'cut_qps': 0.10, 'kept_percent': 97.2,
                         'largest_dropped_qps': 0.099}

    def emit(self, previous):
        # Re-measuring every shipped pair is what a re-derivation of the
        # same cluster looks like: the same pairs, freshly fitted terms,
        # and a placeholder note for every one of them.
        entries = []
        for e in self.previous['entries']:
            entry = dict(e)
            entry['note'] = tool.PLACEHOLDER_NOTE
            entry.pop('provisional', None)
            entries.append(entry)

        out = io.StringIO()
        tool.emit(entries, self.by_time, self.args, out,
                  coverage=self.coverage, previous=previous)
        return out.getvalue()

    def test_output_is_a_budget_this_repository_accepts(self):
        raw = yaml.safe_load(io.StringIO(self.emit(self.previous)))
        parsed = budget.DatabaseLoadBudget.model_validate(raw)
        self.assertEqual(len(self.previous['entries']), len(parsed.entries))

    def test_output_carries_the_doc_block_the_budget_test_demands(self):
        # The same key list as test_doc_block_records_its_provenance, so
        # the generator and the assertion on the shipped file cannot
        # drift apart.
        doc = yaml.safe_load(io.StringIO(self.emit(self.previous)))['_doc']
        for key in ('derived_from', 'window', 'samples', 'cluster_shape',
                    'method', 'base_term_caveat', 'coverage_caveat',
                    'rederive'):
            self.assertIn(key, doc)
        self.assertIn('3708', doc['coverage_caveat'])

    def test_notes_and_provisional_markings_survive_a_rederivation(self):
        # Both are judgement which lives only in the budget file, and both
        # were dropped on the floor by a re-derivation.
        parsed = budget.DatabaseLoadBudget.model_validate(
            yaml.safe_load(io.StringIO(self.emit(self.previous))))
        self.assertEqual(
            0, len([e for e in parsed.entries
                    if tool.PLACEHOLDER_NOTE in e.note]))
        self.assertNotEqual(
            0, len([e for e in parsed.entries if e.provisional is not None]))

    def test_a_tuned_default_is_not_reverted_to_the_tools_own_value(self):
        # emit() used to write the three defaults as literals, so tuning
        # one in the budget and then re-deriving silently put it back.
        previous = copy.deepcopy(self.previous)
        previous['defaults']['tolerance_multiplier'] = 3.5
        raw = yaml.safe_load(io.StringIO(self.emit(previous)))
        self.assertEqual(3.5, raw['defaults']['tolerance_multiplier'])

    def test_a_new_pair_keeps_its_placeholder_note(self):
        # Carrying notes forward must not invent one for a pair which has
        # never been seen before; that pair is exactly the one a human
        # needs to look at.
        previous = copy.deepcopy(self.previous)
        dropped = previous['entries'].pop(0)
        parsed = budget.DatabaseLoadBudget.model_validate(
            yaml.safe_load(io.StringIO(self.emit(previous))))
        entry = parsed.get(dropped['operation'], dropped['caller_daemon'])
        self.assertIn('TODO', entry.note)

    def test_with_nothing_to_carry_forward_every_note_is_a_placeholder(self):
        # The no-previous-budget path still has to emit something which
        # parses, so that a first derivation is reviewable rather than
        # broken.
        raw = yaml.safe_load(io.StringIO(self.emit(None)))
        parsed = budget.DatabaseLoadBudget.model_validate(raw)
        self.assertEqual(
            len(parsed.entries),
            len([e for e in parsed.entries
                 if tool.PLACEHOLDER_NOTE in e.note]))
        self.assertEqual(tool.FALLBACK_DEFAULTS['tolerance_multiplier'],
                         raw['defaults']['tolerance_multiplier'])
