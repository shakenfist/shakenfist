# Copyright 2019 Michael Still and contributors
"""The band gate's arming figures must keep recomputing from the tree.

``tools/ci_headroom_check_window.py`` recomputes the figures the cluster CI
band gate was armed against from
``docs/plans/data/ci-cloud-sizing-baseline/records-warn-window.jsonl``. A
checker nobody runs is worth no more than the typed table it replaced, so
the real assertion here is the first one: the committed dataset is loaded
and every claim is recomputed from it, in the test suite, on every run.

The rest of the coverage is about the checker's ability to *fail*. A check
which cannot tell a moved number from an unmoved one passes identically on
both, and would be worse than nothing because it reads as evidence. So each
of the three claims the arming decision actually turned on -- the gate
failing nothing, nothing having the gate withheld, and no job-run near the
sample floor -- is driven through a record mutated to violate it, and the
failure is required to name the claim rather than merely to be non-empty.

The derivation of the capacity-refusal warning gets its own tests because
it is the one place this directory's three datasets disagree about the
*meaning* of a stored field rather than its presence. Both sides of that
boundary are exercised, including the state the older schema cannot
represent.

The tool is loaded by path because ``tools/`` is not a package.
"""

import copy
import importlib.util
import json
import os

import fixtures

from shakenfist.tests import base


TOOLS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'tools')
CHECK_PATH = os.path.join(TOOLS, 'ci_headroom_check_window.py')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load('ci_headroom_check_window_under_test', CHECK_PATH)


class CommittedWindowTestCase(base.ShakenFistTestCase):
    """The committed dataset still supports the decision made on it."""

    def setUp(self):
        super().setUp()
        self.records = check.load(check.WARN_WINDOW)

    def test_every_arming_figure_recomputes(self):
        failures = check.check_window(self.records)
        self.assertEqual(
            [], failures,
            'the committed warn window no longer produces the figures the '
            'band gate was armed on. This is a disagreement to investigate '
            'rather than a number to adjust:\n  %s' % '\n  '.join(failures))

    def test_every_committed_dataset_is_the_size_it_claims(self):
        datasets = [(os.path.join(check.DATA_DIR, name), count)
                    for name, count in check.DATASETS]
        failures, mix = check.check_schema_mix(datasets)
        self.assertEqual([], failures, '\n  '.join(failures))
        # Three schema versions in one directory, which is the whole reason
        # the warning derivation below exists. If this ever reads as one
        # version the datasets have been re-harvested and the version-mix
        # handling is no longer being exercised by real data.
        self.assertEqual(3, len(mix['versions']), mix['versions'])

    def test_no_committed_record_is_in_the_ambiguous_census_state(self):
        # A census which was read and matched no scheduler stage event
        # cannot report a refusal warning in any schema version. None of the
        # committed records is in that state, which is what makes pooling
        # the three datasets safe; a dataset added later which is not needs
        # saying out loud rather than discovering.
        datasets = [(os.path.join(check.DATA_DIR, name), count)
                    for name, count in check.DATASETS]
        _, mix = check.check_schema_mix(datasets)
        self.assertEqual(0, mix['ambiguous'])

    def test_the_window_covers_the_whole_merge_matrix_it_measured(self):
        jobs = {r['job'] for r in self.records if r['summary']}
        self.assertEqual({claim.job for claim in check.JOB_CLAIMS}, jobs)


class CheckerFailsOnMovedFiguresTestCase(base.ShakenFistTestCase):
    """Each claim is driven through data that violates it.

    The mutations are made on deep copies of a real record, so what is being
    exercised is the checker reading a genuine record shape rather than a
    fixture guessing at one.
    """

    def setUp(self):
        super().setUp()
        self.records = check.load(check.WARN_WINDOW)
        self.usable = [r for r in self.records if check.is_usable(r)]

    def mutated(self, index, mutate):
        records = copy.deepcopy(self.records)
        target = [r for r in records if check.is_usable(r)][index]
        mutate(target)
        return records

    def test_a_gating_job_run_is_reported_by_name(self):
        def oversubscribe(record):
            record['summary']['cluster']['committed_cpu']['p90_fraction'] = 0.9

        records = self.mutated(0, oversubscribe)
        failures = '\n'.join(check.check_window(records))
        self.assertIn('job-runs the gate would have failed', failures)
        self.assertIn(check.describe(self.usable[0]), failures)

    def test_a_withheld_gate_is_reported_with_its_reason(self):
        def starve(record):
            record['summary']['cluster']['committed_cpu']['n_fraction'] = 4
            record['summary']['cluster']['committed_cpu']['n'] = 4

        failures = '\n'.join(check.check_window(self.mutated(0, starve)))
        self.assertIn('gate withheld', failures)
        self.assertIn('produced a cluster CPU fraction', failures)

    def test_a_degraded_capacity_read_withholds_the_gate(self):
        def degrade(record):
            record['summary']['series']['capacity_degraded_samples'] = 2

        failures = '\n'.join(check.check_window(self.mutated(0, degrade)))
        self.assertIn('gate withheld', failures)
        self.assertIn('capacity read reported failing', failures)

    def test_a_late_unreadable_sample_withholds_the_gate(self):
        def late(record):
            series = record['summary']['series']
            series['ledger_unreadable_samples'] = (
                series['ledger_unreadable_prefix_samples'] + 3)

        failures = '\n'.join(check.check_window(self.mutated(0, late)))
        self.assertIn('gate withheld', failures)
        self.assertIn('after the warm-up prefix', failures)

    def test_an_oversubscribed_series_the_report_could_not_read_is_withheld(self):
        # The interaction, which neither half pins on its own. Nothing in
        # the committed window is both over the bound and unreadable, so a
        # checker that dropped the withheld term from its gating test would
        # agree with this dataset anyway. A band computed from a series
        # whose capacity read was failing is the instrument talking about
        # itself, and must be counted as withheld rather than as a failure.
        def both(record):
            record['summary']['cluster']['committed_cpu']['p90_fraction'] = 0.9
            record['summary']['series']['capacity_degraded_samples'] = 2

        failures = '\n'.join(check.check_window(self.mutated(0, both)))
        self.assertIn('job-runs with the gate withheld', failures)
        self.assertNotIn('job-runs the gate would have failed', failures)

    def test_a_moved_sample_floor_is_reported(self):
        # Shortened on the job-run that holds the floor, so the claim about
        # the smallest sample count is the one that moves.
        records = copy.deepcopy(self.records)
        target = min(
            (r for r in records if check.is_usable(r)),
            key=lambda r: r['summary']['cluster']['committed_cpu']['n_fraction'])
        target['summary']['cluster']['committed_cpu']['n_fraction'] -= 5
        target['summary']['cluster']['committed_cpu']['n'] -= 5
        failures = '\n'.join(check.check_window(records))
        self.assertIn('smallest sample count', failures)

    def test_n_fraction_drifting_from_n_is_reported(self):
        def drift(record):
            record['summary']['cluster']['committed_cpu']['n'] += 1

        failures = '\n'.join(check.check_window(self.mutated(0, drift)))
        self.assertIn('n_fraction equals n', failures)

    def test_a_moved_per_job_figure_is_reported_against_its_job(self):
        records = copy.deepcopy(self.records)
        target = [r for r in records
                  if check.is_usable(r) and r['job'] == 'Guests'
                  and not check.per_node_above(r['summary'])][0]
        target['summary']['cluster']['committed_cpu']['p90_fraction'] = 0.5
        target['summary']['verdict']['per_node_band'] = 'ABOVE BAND'
        failures = '\n'.join(check.check_window(records))
        self.assertIn("job 'Guests'", failures)
        self.assertIn('max p90', failures)
        self.assertIn('per-node ABOVE BAND', failures)

    def test_a_dropped_job_run_is_reported(self):
        # The quietest way this dataset could rot: one job-run missing, the
        # arithmetic over the rest still internally consistent.
        records = copy.deepcopy(self.records)
        records.remove(
            [r for r in records if check.is_usable(r)][-1])
        failures = '\n'.join(check.check_window(records))
        self.assertIn('job-runs carrying a usable series', failures)

    def test_a_dataset_with_nothing_usable_says_so_once(self):
        records = copy.deepcopy(self.records)
        for record in records:
            record['summary'] = None
        failures = check.check_window(records)
        self.assertIn('no record in the dataset carries a usable series',
                      '\n'.join(failures))
        # The claim-by-claim arithmetic is not attempted on an empty
        # population: a checker which went on to report forty separate
        # disagreements would bury the one fact that matters.
        self.assertEqual(2, len(failures), failures)


class GateQuestionAnswerabilityTestCase(base.ShakenFistTestCase):
    """An absent field must never read as a satisfied condition.

    The claim being checked is that none of the window's job-runs would
    have had the gate withheld. The counters it is answered from arrived at
    record version 3, and the two older datasets in the same directory
    carry none of them, so a checker which treated a missing counter as a
    zero would report "nothing withheld" about records nobody measured the
    withholding conditions on -- and would do so most confidently about the
    oldest data.
    """

    def setUp(self):
        super().setUp()
        self.summary = [
            r for r in check.load(check.WARN_WINDOW)
            if check.is_usable(r)][0]['summary']

    def test_the_committed_window_can_answer_for_every_job_run(self):
        records = check.load(check.WARN_WINDOW)
        self.assertEqual([], check.summarise(records)['unjudgeable'])

    def test_an_older_record_cannot_answer_and_says_which_field_is_missing(self):
        # Driven with the real version 1 baseline, not a stripped-down
        # fixture, so what is exercised is the shape those records actually
        # have.
        baseline = [r for r in check.load(
            os.path.join(check.DATA_DIR, 'records.jsonl')) if check.is_usable(r)]
        self.assertTrue(baseline)
        e = self.assertRaises(
            check.NotJudgeable, check.gate_withheld, baseline[0]['summary'])
        self.assertIn('cluster.n_fraction', str(e))
        self.assertIn('series.capacity_degraded_samples', str(e))

    def test_a_window_of_older_records_is_reported_as_unanswered(self):
        baseline = [r for r in check.load(
            os.path.join(check.DATA_DIR, 'records.jsonl'))
            if check.is_usable(r)][:40]
        failures = '\n'.join(check.check_window(baseline))
        self.assertIn('cannot answer the gate question', failures)
        # And emphatically not this, which is what a .get() default would
        # have produced.
        self.assertNotIn('job-runs with the gate withheld: claimed 0, '
                         'recomputed 0', failures)

    def test_would_gate_refuses_rather_than_returning_false(self):
        for block, field in check.GATE_FIELDS:
            summary = copy.deepcopy(self.summary)
            holder = (summary['cluster']['committed_cpu'] if block == 'cluster'
                      else summary['series'])
            del holder[field]
            summary['cluster']['committed_cpu']['p90_fraction'] = 0.9
            self.assertRaises(
                check.NotJudgeable, check.would_gate, summary)

    def test_a_stored_verdict_disagreeing_with_its_inputs_is_reported(self):
        # The banked verdict.gate_withheld is cross-checked rather than
        # trusted, which is only possible on a record version that carries
        # it -- so only on this window, not on its two older siblings.
        records = check.load(check.WARN_WINDOW)
        target = [r for r in records if check.is_usable(r)][0]
        target['summary']['verdict']['gate_withheld'] = ['invented']
        failures = '\n'.join(check.check_window(records))
        self.assertIn('stored gate_withheld', failures)
        self.assertIn(check.describe(target), failures)

    def test_a_stored_gates_flag_disagreeing_with_its_inputs_is_reported(self):
        records = check.load(check.WARN_WINDOW)
        target = [r for r in records if check.is_usable(r)][0]
        target['summary']['verdict']['gates'] = True
        failures = '\n'.join(check.check_window(records))
        self.assertIn('stored verdict.gates', failures)


class RefusalWarningTestCase(base.ShakenFistTestCase):
    """The one field whose meaning changed between committed datasets."""

    def summary(self, state, stage_events, drops):
        return {'census': {'state': state, 'stage_events': stage_events,
                           'capacity_shortage_drops': drops}}

    def test_a_census_that_saw_no_drop_reads_clean(self):
        self.assertIs(
            False,
            check.refusal_warning(self.summary('read', 12, 0)))

    def test_a_census_that_tallied_a_drop_reads_warned(self):
        self.assertIs(
            True,
            check.refusal_warning(self.summary('read', 12, 3)))

    def test_a_census_that_matched_no_stage_event_reads_unknown(self):
        # The state the older schema stores as False, indistinguishable
        # from a clean run. It is the instrument failing to look, so there
        # is no answer rather than a negative one.
        self.assertIsNone(check.refusal_warning(self.summary('read', 0, 0)))

    def test_an_uncollected_census_reads_unknown(self):
        self.assertIsNone(
            check.refusal_warning(self.summary('not_collected', 0, 0)))


class MainTestCase(base.ShakenFistTestCase):
    """The command's exit status, which is what makes it usable in a loop."""

    def test_the_committed_data_exits_zero(self):
        self.assertEqual(0, check.main(['--quiet']))

    def test_a_moved_figure_exits_one(self):
        records = check.load(check.WARN_WINDOW)
        usable = [r for r in records if check.is_usable(r)]
        usable[0]['summary']['cluster']['committed_cpu']['p90_fraction'] = 0.95
        broken = os.path.join(
            self.useFixture(fixtures.TempDir()).path, 'window.jsonl')
        with open(broken, 'w') as handle:
            for record in records:
                handle.write(json.dumps(record) + '\n')
        self.assertEqual(1, check.main(['--quiet', '--window', broken]))
