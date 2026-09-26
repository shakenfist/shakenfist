# Copyright 2019 Michael Still and contributors
"""The headroom report must never be the reason a CI run has no data.

``tools/ci_headroom_report.py`` reads a JSONL series written by a poller
which is routinely killed mid-write, beside a Loki census which may not have
been collected at all, and prints a summary into a CI job log. Phase 1 of the
CI cloud sizing plan says the report exits zero whatever it finds (D15),
because an instrument which can fail the job changes the thing it is
measuring -- and it would do so during the very baseline window phase 2 means
to read.

So the first thing covered here is that none of the malformed inputs the real
world produces raises: an empty file, a truncated final line, a sample which
recorded an error instead of a payload, a census which is missing, and a
census which is not JSON.

The rest of the coverage is about readings which are wrong in a way that
looks right, each of which the plan calls out by name:

* A census which was never collected must not print as zero refusals. "We
  did not look" and "nothing was refused" are different findings and the
  second is the one the whole plan is hunting for.
* An all-false ``cpu_committed_row_present`` across every node in a sample
  is ``_capacity_by_node()`` swallowing a read failure, not an idle cluster,
  so that sample must not be averaged into the committed figures as zeros.
* ``no memory_max in node metrics`` is missing data, not a memory shortage.
  Counting it as one would read a stale metrics row as evidence the cloud is
  too small.
* A stage string the tool has never seen must still be tallied and printed.
  The scheduler's stage names are bare literals with no enumeration, so a
  hardcoded list in a parser drifts silently -- the plan itself had already
  drifted, naming three capacity stages where there are four (D10).
* The count of node-samples which fell back from the capacity row's
  ``cpu_limit`` to the derived ``cpu_hard_max`` is a deliverable, because D7
  asks phase 2 to reconcile the two ledgers and a run which is entirely
  fallback answers that question differently.

The tool is loaded by path: CI tools in ``tools/`` are not importable as a
package, and this one deliberately imports nothing from shakenfist so that it
runs under stock python3 on a runner.
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import tempfile

import fixtures

from shakenfist.tests import base


REPORT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'tools', 'ci_headroom_report.py')

NODE_ONE = '11111111-1111-1111-1111-111111111111'
NODE_TWO = '22222222-2222-2222-2222-222222222222'
NODE_THREE = '33333333-3333-3333-3333-333333333333'


def _load_report():
    spec = importlib.util.spec_from_file_location(
        'ci_headroom_report_under_test', REPORT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = _load_report()


def node_payload(cpu_measured=4, cpu_committed=6, cpu_limit=10,
                 cpu_hard_max=12, ram_max=32000, ram_available=24000,
                 row_present=True):
    """One node's slice of a /admin/resources per_node payload."""
    return {
        'cpu_max_per_instance': 4,
        'cpu_schedulable': 8,
        'cpu_committed_row_present': row_present,
        'cpu_hard_max': cpu_hard_max,
        'cpu_measured': cpu_measured,
        'cpu_committed': cpu_committed,
        'cpu_limit': cpu_limit,
        'cpu_available': cpu_hard_max - max(cpu_measured, cpu_committed),
        'cpu_load_1': 1.0,
        'cpu_load_5': 1.0,
        'cpu_load_15': 1.0,
        'memory_reserved_mb': 2048,
        'ram_max_per_instance': 12000,
        'ram_max': ram_max,
        'ram_available': ram_available,
        'disk_available': 100,
        'instances_total': 2,
        'instances_active': 1,
    }


def roster_entry(uuid, fqdn, is_hypervisor=True):
    return {
        'uuid': uuid,
        'fqdn': fqdn,
        'is_hypervisor': is_hypervisor,
        'is_network_node': False,
        'is_database_node': False,
    }


def sample(per_node, nodes=None, sampled_at=1756000000.0,
           capacity_degraded=None):
    total = {
        'cpu_available': sum(n['cpu_available'] for n in per_node.values()),
        'ram_available': sum(n['ram_available'] for n in per_node.values()),
    }
    # Absent by default, which is what a bundle built before step 2a
    # published the flag looks like. Pass True or False to be one of the
    # builds which does.
    if capacity_degraded is not None:
        total['capacity_degraded'] = capacity_degraded
    record = {
        'sampled_at': sampled_at,
        'resources': {'total': total, 'per_node': per_node},
    }
    if nodes is not None:
        record['nodes'] = nodes
    return record


def census_event(message, dropped=None):
    record = {'message': message, 'extra': {'candidates': ['x']}}
    if dropped:
        record['extra']['dropped'] = dropped
    return ['1756000000000000000', json.dumps(record)]


def census_payload(events):
    return {
        'status': 'success',
        'data': {
            'resultType': 'streams',
            'result': [{'stream': {'job': 'shakenfist'}, 'values': events}],
        },
    }


def wait_event(test_id='pkg.mod.TestCase.test_something', instance_name='i1',
               node='n1', roster_key='n1', cpus=2, memory_mb=1024, disk_gb=8,
               binding_dimension=None, seconds_waited=12.5, mode='informed',
               attempt_number=1, headroom_at_first_refusal=0,
               headroom_at_admission=2):
    """One line of the capacity-wait trace create_instance() writes (D14).

    These keys must stay identical to the record
    BaseTestCase._append_capacity_wait_trace() builds in
    shakenfist/deploy/shakenfist_ci/base.py. A fixture which drifts from
    the writer tests the parser against a line shape nothing emits,
    which is the one failure this report is supposed to survive and the
    one its tests would then not notice. The drift is not hypothetical:
    this fixture said 'attempts' while the writer said 'attempt_number',
    and omitted three fields the writer has always written.
    """
    return {
        'test_id': test_id,
        'instance_name': instance_name,
        'node': node,
        'roster_key': roster_key,
        'cpus': cpus,
        'memory_mb': memory_mb,
        'disk_gb': disk_gb,
        'binding_dimension': binding_dimension,
        'seconds_waited': seconds_waited,
        'mode': mode,
        'attempt_number': attempt_number,
        'headroom_at_first_refusal': headroom_at_first_refusal,
        'headroom_at_admission': headroom_at_admission,
    }


class HeadroomReportTestCase(base.ShakenFistTestCase):
    """Every test drives main() and reads the printed report."""

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(self._remove_tempdir)

        # GitHub Actions sets $GITHUB_STEP_SUMMARY for every step, and
        # main() appends a verdict block to whatever file it names. Left
        # ambient, every test here which produces a record would write to
        # the unit-test job's own summary page when run in CI, and would
        # behave differently there than it does locally. Unset it for
        # every test; the ones which exercise the summary point it at a
        # file of their own with _set_step_summary().
        self.useFixture(fixtures.EnvironmentVariable('GITHUB_STEP_SUMMARY'))

    def _remove_tempdir(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _write(self, name, body):
        path = os.path.join(self.tempdir, name)
        with open(path, 'w') as f:
            f.write(body)
        return path

    def _series(self, records, trailing=None):
        body = ''.join(json.dumps(r) + '\n' for r in records)
        if trailing is not None:
            body += trailing
        return self._write('headroom.jsonl', body)

    def _census(self, events):
        return self._write('census.json', json.dumps(census_payload(events)))

    def _waits(self, records, trailing=None):
        body = ''.join(json.dumps(r) + '\n' for r in records)
        if trailing is not None:
            body += trailing
        return self._write('instance-waits.jsonl', body)

    def _run(self, *argv):
        """Run the tool, returning (exit code, stdout)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = report.main(list(argv))
        return code, out.getvalue()

    def _readable(self, per_node, count=None, start=1756000000.0):
        """The same cluster sampled often enough for a violation to gate.

        A single sample never gates (BAND_GATE_MIN_SAMPLES), so a test
        about the gate itself needs a series at least that long, and a
        sample without the capacity_degraded flag never gates either, so
        every sample here carries it. Returned as records rather than a
        path so a test can add to it first.
        """
        if count is None:
            count = report.BAND_GATE_MIN_SAMPLES
        return [sample(per_node, sampled_at=start + 15.0 * i,
                       capacity_degraded=False)
                for i in range(count)]

    def _assert_rendered(self, code, output):
        """The report ran to completion, and this fixture did not gate.

        main() swallows a raise inside report() and returns 0, so the
        status alone cannot tell a clean run from a broken one. The text
        main() prints when it swallows one can.

        Several callers build a short fixture above the upper bound, and
        return 0 only because gate_withheld_reasons() withholds it -- a
        one-sample series, or one without the capacity_degraded flag.
        Those tests are not about the gate, so if one starts returning
        BAND_VIOLATION_EXIT the message says the fixture is the thing to
        change, rather than leaving it to read as a regression.
        """
        self.assertNotIn(
            'failed to render', output,
            'The report raised, and main() swallowed it.')
        self.assertNotEqual(
            report.BAND_VIOLATION_EXIT, code,
            'This fixture gated. Tests using _assert_rendered() are not '
            'about the gate; a short above-band fixture relied on '
            'gate_withheld_reasons() withholding it (BAND_GATE_MIN_SAMPLES, '
            'or an absent capacity_degraded flag), and a change to those '
            'rules means the fixture needs to be made to not gate.')
        self.assertEqual(0, code)


class RobustnessTestCase(HeadroomReportTestCase):
    def test_an_empty_series_is_not_an_error(self):
        """A run whose poller never wrote a sample still exits zero.

        D15: nothing this phase adds may fail a job, because the phase
        exists to observe CI's failure surface and an instrument which
        can fail the job changes what is being measured.
        """
        path = self._series([])
        code, output = self._run('--series', path)
        self.assertEqual(
            0, code, 'An empty series made the report exit non-zero, which '
                     'would let the instrument fail the job it is measuring '
                     '(D15).')
        self.assertIn('0 usable', output)

    def test_a_truncated_final_line_is_counted_not_fatal(self):
        """A killed poller leaves half a line, which must still parse the rest.

        The workflow sets cancel-in-progress, so the step which stops the
        poller is not guaranteed to run and the last line is routinely
        half written.
        """
        path = self._series(
            [sample({NODE_ONE: node_payload()})],
            trailing='{"sampled_at": 1756000015.0, "resources": {"per_no')
        code, output = self._run('--series', path)
        self.assertEqual(
            0, code, 'A truncated final line made the report exit non-zero; a '
                     'cancelled job leaves one on every run.')
        self.assertIn('1 usable', output)
        self.assertIn('1 unparseable line', output)

    def test_a_failed_sample_is_reported_as_a_failure(self):
        """An 'error' record is a sample which failed, not a cluster at rest.

        The probe never raises: a failed poll writes an error record and
        carries on. If those counted as usable samples the report would
        average an API outage in as headroom.
        """
        path = self._series([
            sample({NODE_ONE: node_payload()}),
            {'sampled_at': 1756000015.0, 'error': 'connection refused'},
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn('1 usable, 1 failed', output)
        self.assertIn(
            'connection refused', output,
            'The error text of a failed sample was dropped, so a run whose '
            'API was down would look the same as one which was merely quiet.')

    def test_a_series_file_which_does_not_exist_is_reported(self):
        code, output = self._run(
            '--series', os.path.join(self.tempdir, 'absent.jsonl'))
        self.assertEqual(
            0, code, 'A missing series file made the report exit non-zero.')
        self.assertIn('could not be read', output)

    def test_a_missing_census_file_is_never_zero_refusals(self):
        """An absent census must read as unknown, never as nothing refused.

        This is the dangerous reading the plan calls out by name: the
        census depends on log shipping being healthy (D11), so a broken
        shipper looks exactly like a cluster with room to spare unless
        the difference is stated.
        """
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run(
            '--series', path,
            '--census', os.path.join(self.tempdir, 'absent.json'))
        self.assertEqual(0, code)
        self.assertIn(
            'NO CENSUS IS AVAILABLE', output,
            'A census file which does not exist was not reported as absent, '
            'so the report reads as though nothing was ever refused.')
        self.assertIn('never as zero refusals', output)

    def test_a_census_which_is_not_json_is_reported(self):
        path = self._series([sample({NODE_ONE: node_payload()})])
        census = self._write('census.json', 'this is not json {{{')
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(
            0, code, 'A census which is not valid JSON made the report exit '
                     'non-zero (D15).')
        self.assertIn('NO CENSUS IS AVAILABLE', output)
        self.assertIn('unparseable', output)

    def test_no_census_argument_says_nothing_was_looked_at(self):
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'NO CENSUS WAS SUPPLIED', output,
            'With no --census the report must say nothing was looked at, '
            'rather than printing a refusal count of zero.')

    def test_a_usage_error_still_exits_zero(self):
        """Even argparse may not fail the job (D15)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with open(os.devnull, 'w') as devnull:
                with contextlib.redirect_stderr(devnull):
                    code = report.main(['--not-an-argument'])
        self.assertEqual(
            0, code, 'A usage error exited non-zero, so a typo in the '
                     'workflow step would fail a CI job this phase promised '
                     'it could not fail (D15).')


class LedgerTestCase(HeadroomReportTestCase):
    def test_the_fallback_count_is_reported_and_correct(self):
        """D7 needs to know how often cpu_limit was missing.

        The ledger is the capacity row's limit where there is a row and
        the derived cpu_hard_max where there is not. A run which is
        entirely fallback tells phase 2 something quite different about
        the 12-versus-10 discrepancy than a mixed one does, so the count
        is a deliverable rather than a diagnostic.
        """
        path = self._series([
            sample({NODE_ONE: node_payload(cpu_limit=10),
                    NODE_TWO: node_payload(cpu_limit=None)}),
            sample({NODE_ONE: node_payload(cpu_limit=10),
                    NODE_TWO: node_payload(cpu_limit=None)},
                   sampled_at=1756000015.0),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'Node-samples with a capacity row (cpu_limit):     2', output,
            'The count of node-samples denominated in the capacity row is '
            'wrong, so D7 cannot be answered from this report.')
        self.assertIn(
            'Node-samples which fell back to cpu_hard_max:     2', output,
            'The fallback count is wrong. Phase 2 reads it to tell a run '
            'which saw both ledgers from one which never saw a capacity row '
            'at all.')

    def test_an_entirely_fallback_run_says_so(self):
        path = self._series([
            sample({NODE_ONE: node_payload(cpu_limit=None, row_present=True)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'EVERY node-sample fell back', output,
            'A run in which no capacity row was ever visible did not say so, '
            'so phase 2 would read its ledger as the row limit when it is '
            'the derived twin.')

    def test_the_ledger_uses_the_row_limit_where_there_is_one(self):
        """cpu_limit and cpu_hard_max deliberately disagree; the row wins.

        Publishing the derived twin while admission uses the real row is
        exactly the gap that let a 12-versus-10 discrepancy sit
        unexplained (D12), so the report must denominate in the row.
        """
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=5, cpu_committed=5, cpu_limit=10,
                cpu_hard_max=12)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            '0.500', output,
            'Committed 5 against a row limit of 10 should read as 0.500. A '
            '0.417 here means the report denominated in cpu_hard_max (12) '
            'while admission uses the row (D7).')


class LedgerUnreadableTestCase(HeadroomReportTestCase):
    def test_an_all_false_sample_is_unreadable_not_idle(self):
        """_capacity_by_node() swallows a read failure and returns empty.

        Every node's cpu_committed is then zero and every
        cpu_committed_row_present is false at once, which is
        indistinguishable from an idle cluster unless the report says so.
        Averaging such a sample in as zeros would understate committed
        vCPU and push the band verdict towards "oversized" -- which is a
        recommendation to shrink the cloud, made on a failed read.
        """
        good = sample({
            NODE_ONE: node_payload(cpu_measured=8, cpu_committed=8),
            NODE_TWO: node_payload(cpu_measured=8, cpu_committed=8)})
        blind = sample({
            NODE_ONE: node_payload(
                cpu_measured=0, cpu_committed=0, cpu_limit=None,
                row_present=False),
            NODE_TWO: node_payload(
                cpu_measured=0, cpu_committed=0, cpu_limit=None,
                row_present=False)},
            sampled_at=1756000015.0)
        path = self._series([good, blind])
        code, output = self._run('--series', path)
        self._assert_rendered(code, output)
        self.assertIn(
            'LEDGER UNREADABLE: 1 of 2 samples', output,
            'A sample whose every node reported no capacity row was not '
            'flagged, so a failed ledger read is being reported as an idle '
            'cluster.')
        self.assertIn(
            'NOT that the cluster was idle', output,
            'The report does not say what an all-false sample means, which '
            'is the whole reason it is detected.')
        # One usable sample survives, and it is the busy one, so the
        # cluster row must show n=1 rather than averaging in the zeros.
        self.assertIn(
            'committed vCPU        1', output,
            'The ledger-unreadable sample was averaged into the committed '
            'vCPU figures as zeros, which is the reading the detection '
            'exists to prevent.')

    def test_memory_survives_a_ledger_unreadable_sample(self):
        """Memory comes from node metrics, not the capacity row.

        Excluding an unreadable sample from the memory figures too would
        throw away data which was never in doubt.
        """
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_limit=None, row_present=False,
                ram_max=32000, ram_available=24000)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'committed memory (MB) 1', output,
            'The memory figures were discarded along with the CPU ones for a '
            'ledger-unreadable sample, but ram_max and ram_available come '
            'from node metrics and are unaffected by a capacity read.')


class CapacityDegradedTestCase(HeadroomReportTestCase):
    """Which of the two readings an unreadable ledger was (issue 4087).

    An all-absent capacity map means either a table which could not be read
    or a table which is not populated yet, and the count of unreadable
    samples cannot tell them apart. Step 2a's ``capacity_degraded`` and the
    shape of the run can: a warm-up is an unbroken prefix at the start of
    the series with a healthy read throughout, a fault is neither. Phase 2's
    confirmation window had to read both facts out of raw series inside
    bundles which expire ninety days after their run, and D22 does not
    commit those series, so they are counted into the record instead.
    """

    def _blind(self, sampled_at, capacity_degraded=None):
        per_node = {
            NODE_ONE: node_payload(cpu_measured=0, cpu_committed=0,
                                   cpu_limit=None, row_present=False),
            NODE_TWO: node_payload(cpu_measured=0, cpu_committed=0,
                                   cpu_limit=None, row_present=False),
        }
        return sample(per_node, sampled_at=sampled_at,
                      capacity_degraded=capacity_degraded)

    def _busy(self, sampled_at, capacity_degraded=None):
        per_node = {
            NODE_ONE: node_payload(cpu_measured=8, cpu_committed=8),
            NODE_TWO: node_payload(cpu_measured=8, cpu_committed=8),
        }
        return sample(per_node, sampled_at=sampled_at,
                      capacity_degraded=capacity_degraded)

    def test_a_warm_up_prefix_is_counted_and_measured(self):
        path = self._series([
            self._blind(1756000000.0, capacity_degraded=False),
            self._blind(1756000015.0, capacity_degraded=False),
            self._blind(1756000030.0, capacity_degraded=False),
            self._busy(1756000045.0, capacity_degraded=False),
            self._busy(1756000060.0, capacity_degraded=False)])
        series = report.summary_record(path)['series']
        self.assertEqual(3, series['ledger_unreadable_samples'])
        self.assertEqual(3, series['ledger_unreadable_prefix_samples'])
        self.assertEqual(30.0, series['ledger_unreadable_prefix_seconds'])
        self.assertEqual(0, series['capacity_degraded_samples'])
        self.assertEqual(0, series['capacity_degraded_absent_samples'])

    def test_a_warm_up_is_named_in_the_printed_report(self):
        path = self._series([
            self._blind(1756000000.0, capacity_degraded=False),
            self._busy(1756000015.0, capacity_degraded=False)])
        code, output = self._run('--series', path)
        self._assert_rendered(code, output)
        self.assertIn(
            'empty table rather than a failed read', output,
            'The report flagged an unreadable ledger without saying which of '
            'the two it was, when capacity_degraded says so directly.')

    def test_a_failing_read_is_not_reported_as_a_warm_up(self):
        path = self._series([
            self._busy(1756000000.0, capacity_degraded=False),
            self._blind(1756000015.0, capacity_degraded=True),
            self._busy(1756000030.0, capacity_degraded=False)])
        record = report.summary_record(path)
        series = record['series']
        self.assertEqual(1, series['ledger_unreadable_samples'])
        self.assertEqual(1, series['capacity_degraded_samples'])
        # Nothing at the start of the series, so nothing in the prefix. This
        # is the assertion that keeps the prefix from being a second name
        # for the count.
        self.assertEqual(0, series['ledger_unreadable_prefix_samples'])
        self.assertIsNone(series['ledger_unreadable_prefix_seconds'])
        code, output = self._run('--series', path)
        self._assert_rendered(code, output)
        self.assertIn(
            'THE CAPACITY READ WAS FAILING', output,
            'A sample which reported capacity_degraded was folded in with '
            'the warm-up samples, which is the reading issue 4087 was closed '
            'against.')

    def test_the_prefix_stops_at_the_first_readable_sample(self):
        # Unreadable, readable, unreadable: two unreadable samples but a
        # prefix of one. A run shaped like this is a fault, not a warm-up,
        # even though every sample reports a healthy read.
        path = self._series([
            self._blind(1756000000.0, capacity_degraded=False),
            self._busy(1756000015.0, capacity_degraded=False),
            self._blind(1756000030.0, capacity_degraded=False)])
        series = report.summary_record(path)['series']
        self.assertEqual(2, series['ledger_unreadable_samples'])
        self.assertEqual(1, series['ledger_unreadable_prefix_samples'])
        # One sample spans no time. Null rather than zero: a prefix of one
        # has no measurable duration, and a zero would read as one which
        # lasted no time at all.
        self.assertIsNone(series['ledger_unreadable_prefix_seconds'])
        code, output = self._run('--series', path)
        self._assert_rendered(code, output)
        self.assertIn(
            'that is not all of them: 1 more is unreadable', output,
            'The report described a series with unreadable samples after '
            'the prefix as though the prefix accounted for all of them.')

    def test_a_bundle_predating_the_flag_says_so_rather_than_guessing(self):
        # The retrospective half of the baseline. Reading an absent flag as
        # a healthy read would put a confirmation that step 2a's instrument
        # never made into the dataset.
        path = self._series([
            self._blind(1756000000.0), self._busy(1756000015.0)])
        record = report.summary_record(path)
        self.assertEqual(0, record['series']['capacity_degraded_samples'])
        self.assertEqual(
            2, record['series']['capacity_degraded_absent_samples'])
        code, output = self._run('--series', path)
        self._assert_rendered(code, output)
        self.assertIn(
            'predate the capacity_degraded flag', output,
            'A bundle with no capacity_degraded flag was reported as though '
            'the read had been confirmed healthy.')


class MemoryTestCase(HeadroomReportTestCase):
    def test_committed_memory_is_derived_from_the_published_fields(self):
        """There is no committed-memory field, so it is ram_max - ram_available."""
        path = self._series([
            sample({NODE_ONE: node_payload(ram_max=32000, ram_available=24000)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            '8000.0', output,
            'Committed memory should be ram_max minus ram_available '
            '(32000 - 24000). A different figure means the derivation '
            'changed and the memory dimension no longer measures what D5 '
            'asked for.')

    def test_a_node_with_no_memory_ledger_is_counted_not_divided_by(self):
        """ram_max of zero is a node with no memory ledger, not a crash."""
        path = self._series([
            sample({NODE_ONE: node_payload(ram_max=0, ram_available=0),
                    NODE_TWO: node_payload(ram_max=32000, ram_available=16000)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(
            0, code, 'A node publishing ram_max of zero made the report fail, '
                     'which is a division by zero the plan asked to be '
                     'counted instead.')
        self.assertIn(
            'node-samples published no ram_max', output,
            'A node with no memory ledger was dropped silently rather than '
            'counted, so the memory figures would cover fewer nodes than '
            'they appear to.')


class NodeAbsenceTestCase(HeadroomReportTestCase):
    def test_a_non_hypervisor_absence_is_explained_by_the_roster(self):
        """This is what the roster is sampled for (survey finding 4).

        summarize_resources() omits non-hypervisors, nodes with metrics
        over 120s old, and nodes with an overlong queue, and says which
        for none of them.
        """
        path = self._series([
            sample({NODE_ONE: node_payload()},
                   nodes=[roster_entry(NODE_ONE, 'sf1'),
                          roster_entry(NODE_THREE, 'sf3', is_hypervisor=False)]),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'not a hypervisor (the roster says so)', output,
            'A node the roster says is not a hypervisor was not classified, '
            'so an ordinary network node reads as an unexplained absence.')

    def test_an_absent_hypervisor_is_unexplained_not_dropped(self):
        """A hypervisor missing from per_node has three possible meanings.

        Stale metrics, an overlong queue, or gone. The roster cannot say
        which, so the report must say unexplained rather than drop it --
        otherwise "the cluster had one hypervisor" gets inferred from a
        sample which merely could not see the second.
        """
        path = self._series([
            sample({NODE_ONE: node_payload()},
                   nodes=[roster_entry(NODE_ONE, 'sf1'),
                          roster_entry(NODE_TWO, 'sf2')]),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'UNEXPLAINED: roster says hypervisor', output,
            'A hypervisor absent from per_node was dropped silently, so the '
            'report would claim a cluster size it only failed to observe.')
        self.assertIn(NODE_TWO, output)

    def test_a_sample_with_no_roster_is_reported(self):
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'UNEXPLAINED: the sample recorded no roster', output,
            'A sample carrying no roster was treated as though every '
            'absence had been accounted for.')

    def test_visible_node_counts_are_not_called_a_cluster_size(self):
        path = self._series([
            sample({NODE_ONE: node_payload()},
                   nodes=[roster_entry(NODE_ONE, 'sf1'),
                          roster_entry(NODE_TWO, 'sf2')]),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'is not the same as', output,
            'The report states a visible node count without the caveat that '
            'it is what the samples could see rather than the cluster size.')


class CapacityCoverageTestCase(HeadroomReportTestCase):
    """Time-to-first-capacity-row (PLAN-transient-capacity-refusals D6).

    The offset from the first sample to the first sample in which every
    hypervisor in the roster carries cpu_committed_row_present true. The
    case the plan calls out by name is the one where that never happens:
    reporting zero there would read as an instant window, which is exactly
    backwards, so it must be reported as absent instead.
    """

    def test_full_coverage_in_the_first_sample_is_near_zero(self):
        roster = [roster_entry(NODE_ONE, 'sf1'), roster_entry(NODE_TWO, 'sf2')]
        path = self._series([
            sample({NODE_ONE: node_payload(row_present=True),
                    NODE_TWO: node_payload(row_present=True)}, nodes=roster),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            '0 seconds after the first sample', output,
            'Every hypervisor already had a capacity row in the first '
            'sample, so the figure should read as an (almost) instant '
            'window rather than as absent.')
        self.assertNotIn('NEVER OBSERVED', output)

    def test_a_row_which_never_appears_is_reported_as_absent_not_zero(self):
        """This is the trap the figure exists to avoid.

        A row which never appears must never render as 0 seconds -- that
        would read as "the window was instant" when the truth is the
        opposite: the window never closed for the whole series.
        """
        roster = [roster_entry(NODE_ONE, 'sf1'), roster_entry(NODE_TWO, 'sf2')]
        path = self._series([
            sample({NODE_ONE: node_payload(row_present=True),
                    NODE_TWO: node_payload(row_present=False)},
                   nodes=roster, sampled_at=1756000000.0 + 15 * i)
            for i in range(4)
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'NEVER OBSERVED', output,
            'A hypervisor which never got a capacity row was not reported '
            'as absent.')
        self.assertNotIn('0 seconds after the first sample', output)

    def test_the_offset_is_measured_from_the_first_sample(self):
        """The figure is an offset from the series start, not from the row's own timestamp."""
        roster = [roster_entry(NODE_ONE, 'sf1')]
        path = self._series([
            sample({NODE_ONE: node_payload(row_present=False)},
                   nodes=roster, sampled_at=1756000000.0),
            sample({NODE_ONE: node_payload(row_present=False)},
                   nodes=roster, sampled_at=1756000015.0),
            sample({NODE_ONE: node_payload(row_present=True)},
                   nodes=roster, sampled_at=1756000045.0),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            '45 seconds after the first sample', output,
            'The offset must be measured from the first sample in the '
            'series, not from whichever sample first achieved coverage.')

    def test_a_hypervisor_absent_from_per_node_is_not_covered(self):
        """A missing node is not the same as a present-but-unguarded one.

        summarize_resources() omits a hypervisor entirely when its metrics
        are stale, its queue is too long, or it is gone -- it does not
        publish cpu_committed_row_present false for it. A roster logic
        which only checked the flag on nodes present in per_node, and
        never checked whether a rostered hypervisor was there at all,
        would read this sample as fully covered.
        """
        roster = [roster_entry(NODE_ONE, 'sf1'), roster_entry(NODE_TWO, 'sf2')]
        path = self._series([
            # NODE_TWO is a hypervisor per the roster but never appears in
            # per_node at all.
            sample({NODE_ONE: node_payload(row_present=True)}, nodes=roster),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'NEVER OBSERVED', output,
            'A rostered hypervisor missing from per_node entirely was '
            'silently treated as covered, making the figure optimistic.')

    def test_a_sample_with_no_roster_does_not_count_as_covered(self):
        """Mirrors how absences() treats a sample with no roster recorded.

        Without a roster there is no way to confirm every hypervisor has a
        row, so the sample must not count towards coverage.
        """
        path = self._series([sample({NODE_ONE: node_payload(row_present=True)})])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn('NEVER OBSERVED', output)

    def test_a_bundle_predating_the_flag_is_unknown_not_never(self):
        """An old bundle must not read as a permanent regression.

        A cluster whose /admin/resources predates
        cpu_committed_row_present publishes the key nowhere, so every
        sample parses as not covered. Scoring that as NEVER OBSERVED
        would report the warm-up window as never having closed, when the
        truth is that this bundle cannot answer the question at all.
        """
        roster = [roster_entry(NODE_ONE, 'sf1')]
        payload = node_payload()
        del payload['cpu_committed_row_present']
        path = self._series([
            sample({NODE_ONE: payload}, nodes=roster,
                   sampled_at=1756000000.0 + 15 * i)
            for i in range(3)
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'NOT MEASURABLE', output,
            'A bundle which never carried cpu_committed_row_present was '
            'scored as if the row never appeared, which reads as a '
            'regression rather than as an unmeasurable payload.')
        self.assertNotIn('NEVER OBSERVED', output)

    def test_a_flag_present_and_false_is_still_never_observed(self):
        """The other side of the distinction above.

        A payload which carries the flag and says false is a real
        measurement of a real unguarded node, and must keep reading as
        NEVER OBSERVED rather than being softened into "unmeasurable".
        """
        roster = [roster_entry(NODE_ONE, 'sf1')]
        path = self._series([
            sample({NODE_ONE: node_payload(row_present=False)}, nodes=roster,
                   sampled_at=1756000000.0 + 15 * i)
            for i in range(3)
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn('NEVER OBSERVED', output)
        self.assertNotIn('NOT MEASURABLE', output)


class CensusTestCase(HeadroomReportTestCase):
    def test_an_unknown_stage_string_is_tallied_and_printed(self):
        """No hardcoded stage list (D10).

        The scheduler's stage names are bare literals with no
        enumeration anywhere, so a list held in a parser drifts silently
        the first time one is added. A stage this tool has never heard of
        must still appear with its count.
        """
        census = self._census([
            census_event('schedule at stage a_stage_invented_next_year',
                         {NODE_ONE: {'reason': 'something entirely new'}}),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'a_stage_invented_next_year', output,
            'A stage string the tool has not been told about was discarded, '
            'which means the census is filtered by a hardcoded list and will '
            'drift the first time the scheduler gains a stage (D10).')
        self.assertIn(
            'something entirely new', output,
            'The drop reason from an unknown stage was discarded.')

    def test_an_aborting_stage_is_counted_as_a_stage(self):
        """Both message forms carry the stage, and both must be read.

        A green run records its refusals in full through the surviving
        form; the aborting form is the one which turned into a 507.
        """
        census = self._census([
            census_event(
                'schedule has no candidates at stage sufficient_idle_cpu, '
                'aborting',
                {NODE_ONE: {'reason': 'would exceed hard max CPUs'}}),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'sufficient_idle_cpu', output,
            'The aborting form of the stage message was not recognised, so '
            'the refusals which actually failed a create are the ones being '
            'missed.')

    def test_the_missing_data_reason_is_never_a_shortage(self):
        """'no memory_max in node metrics' is a stale metrics row.

        Counting it as a memory refusal would read missing data as
        evidence the cloud is too small, which is the precise error this
        plan exists to avoid making (D10).
        """
        census = self._census([
            census_event('schedule at stage sufficient_idle_memory',
                         {NODE_ONE: {'reason': 'no memory_max in node metrics'},
                          NODE_TWO: {'reason': 'no memory_max in node metrics'}}),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'MISSING DATA, not a shortage', output,
            'The missing-data reason was not flagged as such in the reason '
            'breakdown.')
        self.assertIn(
            'no capacity-stage drops in the census window', output,
            'Two drops carrying "no memory_max in node metrics" raised a '
            'capacity shortage warning. That reads a stale metrics row as '
            'evidence the cloud is too small, which is exactly the error '
            'this plan exists to avoid.')

    def test_the_three_memory_reasons_are_reported_separately(self):
        census = self._census([
            census_event('schedule at stage sufficient_idle_memory',
                         {NODE_ONE: {'reason': 'insufficient memory'},
                          NODE_TWO: {'reason': 'KSM overcommit ratio exceeded'},
                          NODE_THREE: {'reason': 'no memory_max in node metrics'}}),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        for reason in ('insufficient memory', 'KSM overcommit ratio exceeded',
                       'no memory_max in node metrics'):
            self.assertIn(
                reason, output,
                'The memory stage carries three distinct reasons and %r was '
                'not reported on its own. Summing them hides that one of the '
                'three is missing data (D10).' % reason)
        self.assertIn(
            'Refusal warning: YES. 2 candidate drops', output,
            'The capacity shortage count should be two of the three memory '
            'drops, with the missing-data one excluded.')

    def test_a_capacity_refusal_is_a_warning_of_its_own(self):
        """D3: any capacity-stage refusal warns, whatever the ratio says.

        A fifteen second poll cannot see a refusal, which begins and ends
        between samples, so the two instruments answer separately (D9).
        """
        census = self._census([
            census_event('schedule at stage sufficient_free_disk',
                         {NODE_ONE: {'reason': 'insufficient disk'}}),
        ])
        path = self._series([
            sample({NODE_ONE: node_payload(cpu_measured=1, cpu_committed=1)}),
        ])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn('OVERSIZED', output)
        self.assertIn(
            'Refusal warning: YES', output,
            'A capacity-stage refusal did not raise its own warning. D3 '
            'makes a refusal in an otherwise-idle-looking run a warning '
            'independent of the ratio, and this run is both at once.')

    def test_the_refusal_warning_is_framed_as_calibration_not_undersizing(self):
        """D5: the job-log prose, not just the annotation, carries the frame.

        Step 5c relabelled the GitHub annotation and step summary; this
        closes the gap left in print_verdict()'s stdout, which is what
        lands in the job log a human actually reads.
        """
        census = self._census([
            census_event('schedule at stage sufficient_free_disk',
                         {NODE_ONE: {'reason': 'insufficient disk'}}),
        ])
        path = self._series([
            sample({NODE_ONE: node_payload(cpu_measured=1, cpu_committed=1)}),
        ])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'not evidence this cloud is too small', output,
            'D5: the refusal warning prose must say the count is not '
            'evidence of undersizing.')
        self.assertIn(
            'close to invariant under node size', output,
            'D5: the prose must say the refusal count is close to '
            'invariant under node size -- phase 4 doubled a ledger and '
            'the refusal rate held essentially steady.')
        self.assertIn(
            'SCHEDULER_DEMAND_PER_VCPU', output,
            'D5: the prose must name the mechanism -- expected_demand '
            'accumulating cpus x SCHEDULER_DEMAND_PER_VCPU per placement '
            '-- that makes the guard admit until its bound fills and '
            'then refuse.')
        self.assertIn(
            'earliest sign the demand estimator has drifted', output,
            'D5: the prose must say what the count IS good for -- the '
            'earliest signal that the demand estimator has drifted.')

    def test_the_four_capacity_stages_are_named_in_the_output(self):
        """Including the disk distinction, which the plan itself got wrong.

        sufficient_idle_disk is disk BANDWIDTH, a rate predicate; the
        stage which means the cluster ran out of disk is
        sufficient_free_disk.
        """
        census = self._census([
            census_event('schedule at stage sufficient_idle_disk',
                         {NODE_ONE: {'reason': 'disk bandwidth saturated'}}),
            census_event('schedule at stage sufficient_free_disk',
                         {NODE_ONE: {'reason': 'insufficient disk'}}),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'disk BANDWIDTH', output,
            'sufficient_idle_disk was not distinguished from disk capacity. '
            'It is a rate predicate against a busy-time threshold, and no '
            'amount of extra hardware in the same shape addresses it.')
        self.assertIn(
            'disk space', output,
            'sufficient_free_disk was not identified as the disk capacity '
            'stage.')
        self.assertIn(
            'sufficient_idle_cpu, sufficient_idle_memory', output,
            'The capacity stages which were not observed should be named, so '
            'a reader can tell "never refused" from "never looked".')


class BandVerdictTestCase(HeadroomReportTestCase):
    def test_a_busy_cluster_reads_as_oversubscribed(self):
        path = self._series(self._readable({NODE_ONE: node_payload(
            cpu_measured=9, cpu_committed=9, cpu_limit=10)}))
        code, output = self._run('--series', path)
        self.assertEqual(
            report.BAND_VIOLATION_EXIT, code,
            'A cluster above the upper bound did not return the band '
            'violation status. 5f armed this bound after a window in which '
            'nothing came within 0.28 of it, and shakenfist/actions only '
            'fails a job on this one status.')
        self.assertIn('OVERSUBSCRIBED', output)
        self.assertIn(
            'This verdict gates', output,
            'The band verdict was printed without saying it gates. A reader '
            'who sees OVERSUBSCRIBED in a red job needs the log to say that '
            'is why, rather than leaving them to hunt a test failure.')
        self.assertNotIn(
            'PROVISIONAL', output,
            'The band is no longer provisional (D2): phase 2 defended both '
            'bounds against a 204 job-run distribution, so the verdict must '
            'not still call them provisional.')

    def test_committed_cpu_is_the_larger_of_measured_and_committed(self):
        """Admission charges max(measured, committed), so the report does too.

        Reporting the measurement alone reads a node whose ledger is full
        but whose instances are still fetching images as idle -- the case
        which cost merge CI a whole suite of creates on 2026-08-14.
        """
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=2, cpu_committed=8, cpu_limit=10)}),
        ])
        code, output = self._run('--series', path)
        self._assert_rendered(code, output)
        self.assertIn(
            '0.800', output,
            'Committed vCPU read as something other than max(cpu_measured, '
            'cpu_committed) against the ledger. A node whose ledger is full '
            'but which measures as idle must not read as headroom.')

    def test_no_usable_samples_gives_no_verdict_rather_than_zero(self):
        path = self._series([])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'NO VERDICT', output,
            'A run with no samples produced a band verdict anyway. A ratio '
            'of zero would read as "oversized", which is a recommendation to '
            'shrink the cloud made on no data at all.')

    def test_a_node_pinned_at_its_ledger_reads_above_the_per_node_band(self):
        """D4: the per-node maximum is judged against PER_NODE_BAND_UPPER.

        One node sits at its own ledger for the whole run, well above
        0.85, while the cluster-wide figure alone would call the run
        healthy.
        """
        records = []
        for i in range(10):
            records.append(sample({
                NODE_ONE: node_payload(cpu_measured=6, cpu_committed=6,
                                       cpu_limit=6, cpu_hard_max=6),
                NODE_TWO: node_payload(cpu_measured=0, cpu_committed=0,
                                       cpu_limit=18, cpu_hard_max=18),
            }, sampled_at=1756000000.0 + 15 * i))
        path = self._series(records)
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'Per-node maximum p90, D4 bound 0.85: 1.000', output,
            'The per-node maximum and the D4 bound it is judged against '
            'were not both printed.')
        self.assertIn(
            'ABOVE BAND', output,
            'A per-node maximum of 1.000 is above the D4 bound of 0.85 and '
            'must read as such.')
        self.assertIn(
            'saturated', output,
            'The per-node verdict must say the statistic is saturated -- '
            'phase 2 found it sitting at its ceiling in a large fraction of '
            'passing job-runs, so it is read as what a topology should '
            'achieve, not as a per-run alarm (D4).')
        self.assertIn(
            'never gates', output,
            'D4 excludes the per-node bound from ever gating; the printed '
            'verdict must say so.')

    def test_a_node_well_under_its_ledger_reads_within_the_per_node_band(self):
        """The lower tail is where the per-node bound has information (D4)."""
        path = self._series([
            sample({NODE_ONE: node_payload(cpu_measured=1, cpu_committed=1,
                                           cpu_limit=10, cpu_hard_max=10)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'Per-node maximum p90, D4 bound 0.85: 0.100', output)
        self.assertIn('WITHIN BAND', output)
        self.assertNotIn('ABOVE BAND', output)


class GithubAnnotationsTestCase(HeadroomReportTestCase):
    """D3: a ``::warning::`` for each band violation, plus a step summary.

    F4 is why this exists at all: the report's exit code is discarded
    twice between here and a human, by ``|| true`` in
    ``ci_headroom_collect.sh`` and by ``continue-on-error: true`` in the
    workflow, so a warning that only changed the exit code would be
    invisible. GitHub reads annotations from stdout regardless of exit
    code, which is why every test here drives ``main()`` through
    ``_run()`` and reads its stdout, the same as every other test in this
    file.
    """

    def _set_step_summary(self, path):
        """Point $GITHUB_STEP_SUMMARY at path for one test, or unset it.

        setUp() has already unset it for every test in this file; this
        layers on top of that, and the fixture restores whatever the
        process started with.
        """
        self.useFixture(fixtures.EnvironmentVariable(
            'GITHUB_STEP_SUMMARY', path))

    def test_the_ambient_step_summary_is_never_inherited(self):
        """setUp() unsets it, so no test here writes to CI's own summary."""
        self.assertNotIn(
            'GITHUB_STEP_SUMMARY', os.environ,
            'A test in this file can see the $GITHUB_STEP_SUMMARY GitHub '
            'Actions sets for the unit-test step, so every test which '
            'produces a record appends a verdict block to that job\'s '
            'real summary page.')

    def test_encode_workflow_command_value_escapes_percent_cr_and_newline(self):
        """Percent must be escaped first, or the escapes re-escape themselves."""
        self.assertEqual(
            '100%25 chance%0D%0Aof rain',
            report._encode_workflow_command_value('100% chance\r\nof rain'))
        self.assertEqual(
            'a%25b%0Ac',
            report._encode_workflow_command_value('a%b\nc'),
            'Escaping "%" after "\\n"/"\\r" would turn the "%0A" this '
            'function itself writes into "%250A".')

    def test_a_within_band_run_emits_no_warning_annotations(self):
        """The common case: no violation, no annotation, no step summary noise."""
        self._set_step_summary(None)
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertNotIn(
            '::warning', output,
            'A run within every band emitted a GitHub annotation anyway.')

    def test_an_oversubscribed_run_emits_a_warning_annotation(self):
        self._set_step_summary(None)
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=9, cpu_committed=9, cpu_limit=10)}),
        ])
        code, output = self._run('--series', path, '--label', 'slim-tier')
        self._assert_rendered(code, output)
        self.assertIn(
            '::warning title=CI headroom%3A cluster OVERSUBSCRIBED::', output,
            'An OVERSUBSCRIBED band verdict did not raise a GitHub '
            'annotation (D3).')
        self.assertIn('above the upper bound of 0.70', output)
        self.assertIn(
            '(slim-tier)', output,
            'The run label was not carried into the annotation message.')

    def test_an_oversized_run_emits_a_warning_annotation(self):
        self._set_step_summary(None)
        path = self._series([
            sample({NODE_ONE: node_payload(cpu_measured=1, cpu_committed=1)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            '::warning title=CI headroom%3A cluster OVERSIZED::', output,
            'An OVERSIZED band verdict did not raise a GitHub annotation '
            '(D3).')
        self.assertIn('below the lower bound of 0.35', output)
        self.assertIn(
            'tools/ci_headroom_harvest.py', output,
            'D8: a single-run OVERSIZED annotation must point at the '
            'harvest tool as the way to ask the operational question, '
            'rather than reading as actionable on its own.')

    def test_a_saturated_node_emits_a_per_node_band_warning_annotation(self):
        records = []
        for i in range(10):
            records.append(sample({
                NODE_ONE: node_payload(cpu_measured=6, cpu_committed=6,
                                       cpu_limit=6, cpu_hard_max=6),
                NODE_TWO: node_payload(cpu_measured=0, cpu_committed=0,
                                       cpu_limit=18, cpu_hard_max=18),
            }, sampled_at=1756000000.0 + 15 * i))
        self._set_step_summary(None)
        path = self._series(records)
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            '::warning title=CI headroom%3A per-node maximum above band::',
            output,
            'A per-node maximum above PER_NODE_BAND_UPPER did not raise a '
            'GitHub annotation (D4).')
        self.assertIn('never gates', output)
        self.assertIn(
            'This bound never gates', output,
            'D4: the per-node annotation must say the bound never gates, '
            'the same as the printed verdict does.')

    def test_a_capacity_refusal_emits_a_warning_annotation_about_calibration(self):
        """D5: phrased as a calibration signal, never as undersizing evidence."""
        census = self._census([
            census_event('schedule at stage sufficient_free_disk',
                         {NODE_ONE: {'reason': 'insufficient disk'}}),
        ])
        self._set_step_summary(None)
        path = self._series([
            sample({NODE_ONE: node_payload(cpu_measured=5, cpu_committed=5,
                                           cpu_limit=10)}),
        ])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            '::warning title=CI headroom%3A capacity-stage refusals observed::',
            output,
            'A capacity-stage refusal did not raise its own GitHub '
            'annotation (D3).')
        self.assertIn(
            "demand estimator's calibration", output,
            'D5: the refusal annotation must be worded as an observation '
            'about the demand estimator\'s calibration.')
        self.assertIn(
            'not evidence the cloud is too small', output,
            'D5: the refusal annotation must explicitly say a refusal is '
            'not evidence the cloud is undersized -- phase 4 doubled a '
            "cluster's ledger and the refusal rate stayed essentially the "
            'same.')

    def test_the_step_summary_variable_is_left_alone_when_unset(self):
        """No file, no exception, when $GITHUB_STEP_SUMMARY is not set."""
        self._set_step_summary(None)
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=9, cpu_committed=9, cpu_limit=10)}),
        ])
        code, output = self._run('--series', path)
        # An unset $GITHUB_STEP_SUMMARY must not raise: this tool is also
        # run by hand over a downloaded bundle, where the variable is
        # never set.
        self._assert_rendered(code, output)
        self.assertIn('::warning', output)

    def test_the_step_summary_file_is_written_when_the_variable_is_set(self):
        summary_path = os.path.join(self.tempdir, 'step-summary.md')
        self._set_step_summary(summary_path)
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=9, cpu_committed=9, cpu_limit=10)}),
        ])
        code, output = self._run('--series', path, '--label', 'slim-tier')
        self._assert_rendered(code, output)
        with open(summary_path) as f:
            contents = f.read()
        self.assertIn('### CI headroom band verdict -- slim-tier', contents)
        self.assertIn('OVERSUBSCRIBED', contents)
        # This fixture's single node is also above the D4 per-node band
        # (0.900 > 0.85), so both annotations fire.
        self.assertIn('2 GitHub annotations raised on this run.', contents)

    def test_the_step_summary_file_is_appended_to_not_overwritten(self):
        """Other steps in the same job write their own sections first."""
        summary_path = os.path.join(self.tempdir, 'step-summary.md')
        with open(summary_path, 'w') as f:
            f.write('### An earlier step\n\nSome other content.\n')
        self._set_step_summary(summary_path)
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, _ = self._run('--series', path)
        self.assertEqual(0, code)
        with open(summary_path) as f:
            contents = f.read()
        self.assertIn(
            'An earlier step', contents,
            'Opening $GITHUB_STEP_SUMMARY truncated a section an earlier '
            'step in the same job had already written.')
        self.assertIn('CI headroom band verdict', contents)

    def test_the_heading_is_not_glued_to_an_unterminated_last_line(self):
        """An earlier writer need not have ended its section with a newline."""
        summary_path = os.path.join(self.tempdir, 'step-summary.md')
        with open(summary_path, 'w') as f:
            f.write('Some other content without a newline')
        self._set_step_summary(summary_path)
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, _ = self._run('--series', path)
        self.assertEqual(0, code)
        with open(summary_path) as f:
            lines = f.read().split('\n')
        self.assertIn(
            'Some other content without a newline', lines,
            'The verdict heading was appended onto the last line an '
            'earlier step left unterminated, so it will not render as a '
            'heading.')
        self.assertTrue(
            any(line.startswith('### CI headroom band verdict')
                for line in lines))

    def test_an_unwritable_step_summary_path_does_not_raise(self):
        """A path whose parent directory does not exist is tolerated (D15)."""
        summary_path = os.path.join(
            self.tempdir, 'no-such-directory', 'step-summary.md')
        self._set_step_summary(summary_path)
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=9, cpu_committed=9, cpu_limit=10)}),
        ])
        code, output = self._run('--series', path)
        # An unwritable $GITHUB_STEP_SUMMARY path must not raise either:
        # nothing this tool does may fail the job it is measuring (D15).
        self._assert_rendered(code, output)
        self.assertFalse(os.path.exists(summary_path))
        self.assertIn(
            '::warning', output,
            'A failure writing the step summary must not suppress the '
            'stdout annotations, which are a separate surface.')

    def test_the_step_summary_reports_no_verdict_for_an_empty_series(self):
        summary_path = os.path.join(self.tempdir, 'step-summary.md')
        self._set_step_summary(summary_path)
        path = self._series([])
        code, _ = self._run('--series', path)
        self.assertEqual(0, code)
        with open(summary_path) as f:
            contents = f.read()
        self.assertIn('Cluster-wide p90: NO VERDICT', contents)
        self.assertIn('Per-node maximum p90: NO VERDICT', contents)
        self.assertIn('Refusal warning: UNKNOWN', contents)


class PercentileTestCase(base.ShakenFistTestCase):
    """The helper is copied from queue-wait-report.py and keeps its semantics."""

    def test_an_empty_list_is_none(self):
        self.assertIsNone(
            report.percentile([], 0.9),
            'percentile() over an empty list must be None so callers print a '
            'dash rather than a zero which nothing measured.')

    def test_the_result_is_a_value_which_was_observed(self):
        values = [1.0, 2.0, 3.0, 4.0, 100.0]
        self.assertIn(
            report.percentile(values, 0.9), values,
            'percentile() interpolated. A tail made of a handful of samples '
            'then reports a number nothing ever measured, which is why the '
            'original in queue-wait-report.py does not interpolate.')


def unledgered_node_payload(cpu_committed=8):
    """A node with committed vCPU and neither ledger field.

    Both cpu_limit and cpu_hard_max are absent, which is the shape that
    produced a cluster fraction above 1.0: committed vCPU with nothing to
    divide it by.
    """
    payload = node_payload(cpu_measured=cpu_committed,
                           cpu_committed=cpu_committed)
    del payload['cpu_limit']
    del payload['cpu_hard_max']
    payload['cpu_available'] = 0
    return payload


class ReviewFixesTestCase(HeadroomReportTestCase):
    """Readings which looked right and were not, found in review of phase 1."""

    def test_an_unledgered_node_cannot_push_the_fraction_above_one(self):
        """The fraction's two sides must be summed over the same nodes.

        cluster_committed_cpu summed every node while cluster_cpu_ledger
        summed only the ledgered ones, so a node publishing neither
        cpu_limit nor cpu_hard_max landed in the numerator alone. Two
        nodes at 5 committed vCPU, one with a ledger of 10, gave 10/10
        rather than 5/10: a cluster sitting at half its ledger reported
        as exactly full, and a WITHIN BAND run reported as
        OVERSUBSCRIBED -- that is, as a case for a bigger cloud built on
        one absent field.
        """
        path = self._series([
            sample({
                NODE_ONE: node_payload(cpu_measured=5, cpu_committed=5,
                                       cpu_limit=10, cpu_hard_max=10),
                NODE_TWO: unledgered_node_payload(cpu_committed=5),
            }),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertNotIn(
            'OVERSUBSCRIBED', output,
            'A node with no ledger at all was allowed to push the cluster '
            'fraction to 1.0 and the verdict to OVERSUBSCRIBED, when the '
            'ledgered node was sitting at half its limit.')
        self.assertIn(
            '0.500', output,
            'The cluster CPU fraction should be 5/10 over the one node '
            'which has a ledger, not 10/10 over a numerator drawn from '
            'both nodes and a denominator drawn from one.')
        self.assertIn(
            'no ledger', output,
            'The excluded node was dropped silently. Which nodes the '
            'fraction could not use is exactly what a reader needs to '
            'judge whether the fraction means anything.')

    def test_a_lone_node_without_a_capacity_row_still_reports(self):
        """One node with no row is a per-node fact, not a failed read.

        ledger_unreadable was 'every row_present is False', which a
        single-hypervisor topology satisfies whenever its one node has no
        capacity row yet. Every sample was then discarded and the run
        printed NO VERDICT -- on slim-primary, the first topology this
        phase's definition of done names.
        """
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=6, cpu_committed=6, cpu_limit=None,
                cpu_hard_max=12, row_present=False)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertNotIn(
            'NO VERDICT', output,
            'A single-hypervisor sample whose one node had no capacity '
            'row was treated as a failed capacity read, discarding the '
            'whole CPU series on the topology phase 1 must report on.')
        self.assertIn('0.500', output)
        self.assertIn(
            'one visible node and no capacity row', output,
            'The one-node case should be described as what it is rather '
            'than silently folded in with healthy samples.')

    def test_two_nodes_with_no_rows_are_still_a_failed_read(self):
        """The original inference must survive where it is meaningful."""
        path = self._series([
            sample({
                NODE_ONE: node_payload(row_present=False),
                NODE_TWO: node_payload(row_present=False),
            }),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'NO VERDICT', output,
            'An all-false sample across more than one node is still '
            '_capacity_by_node() swallowing a read failure, and must '
            'still not be averaged in as an idle cluster.')

    def test_a_census_at_its_limit_is_reported_as_maybe_truncated(self):
        """Loki gives no signal that it cut a response short.

        A response holding exactly max_entries_limit_per_query looks
        identical to a complete one, and a census cut short reads as a
        cluster with room -- the one misreading this tool exists to
        prevent.
        """
        census = self._census([
            census_event('schedule at stage sufficient_idle_cpu',
                         {NODE_ONE: {'reason': 'insufficient cpu'}}),
            census_event('schedule at stage sufficient_idle_memory',
                         {NODE_ONE: {'reason': 'insufficient memory'}}),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run(
            '--series', path, '--census', census, '--census-limit', '2')
        self.assertEqual(0, code)
        self.assertIn(
            'CENSUS MAY BE TRUNCATED', output,
            'A census which returned exactly as many entries as it was '
            'allowed was reported as complete.')
        self.assertIn('LOWER BOUND', output)

    def test_a_census_below_its_limit_is_not_called_truncated(self):
        census = self._census([
            census_event('schedule at stage sufficient_idle_cpu'),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run(
            '--series', path, '--census', census, '--census-limit', '5000')
        self.assertEqual(0, code)
        self.assertNotIn('CENSUS MAY BE TRUNCATED', output)

    def test_drops_at_an_unclassified_stage_are_named_in_the_verdict(self):
        """D10 keeps them out of the warning; they must not vanish from it.

        capacity_shortage_drops counts only stages in
        CAPACITY_STAGE_NOTES, so a stage added to the scheduler after
        this tool was written is tallied in the census table and
        contributes nothing to the verdict. A reader who skips to the
        verdict then sees 'no capacity-stage drops' above a table full
        of them.
        """
        census = self._census([
            census_event('schedule has no candidates at stage '
                         'sufficient_flux_capacitors, aborting',
                         {NODE_ONE: {'reason': 'insufficient flux'}}),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'does not classify', output,
            'A drop at an unknown stage was tallied in the table but the '
            'verdict said nothing about it, so the verdict reads as '
            '"nothing was refused" while the table shows a refusal.')

    def test_the_disk_bandwidth_caveat_is_absent_when_disk_did_not_drop(self):
        """Stating it about drops which did not happen dilutes it.

        The distinction is real and the plan itself got it wrong, so the
        note earns its place -- but only over drops that are actually at
        sufficient_idle_disk.
        """
        census = self._census([
            census_event('schedule at stage sufficient_idle_cpu',
                         {NODE_ONE: {'reason': 'insufficient cpu'}}),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn('Refusal warning: YES', output)
        self.assertNotIn(
            'disk BANDWIDTH', output,
            'The disk bandwidth caveat printed for a run whose only drops '
            'were CPU, inviting the reader to attribute the warning to '
            'disk I/O.')

    def test_the_disk_bandwidth_caveat_is_present_when_disk_dropped(self):
        census = self._census([
            census_event('schedule at stage sufficient_idle_disk',
                         {NODE_ONE: {'reason': 'insufficient disk bandwidth'}}),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn('disk BANDWIDTH', output)

    def test_a_read_failure_is_not_counted_as_a_ledger_fallback(self):
        """D7 reads the fallback count as evidence about the two ledgers.

        print_ledger_provenance walked every sample, so node-samples from
        a ledger-unreadable sample were counted as having fallen back to
        cpu_hard_max. That inflates the one number D7's reconciliation
        asks phase 2 to read with failures which say nothing about it.
        """
        path = self._series([
            sample({
                NODE_ONE: node_payload(cpu_limit=None, cpu_hard_max=12),
                NODE_TWO: node_payload(cpu_limit=None, cpu_hard_max=12),
            }),
            sample({
                NODE_ONE: node_payload(cpu_limit=None, cpu_hard_max=12,
                                       row_present=False),
                NODE_TWO: node_payload(cpu_limit=None, cpu_hard_max=12,
                                       row_present=False),
            }, sampled_at=1756000015.0),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn('Node-samples which fell back to cpu_hard_max:     2',
                      output)
        self.assertIn('Fallbacks inside ledger-unreadable samples:       2',
                      output)
        self.assertIn(
            'D7 should read the second line alone', output,
            'The two causes of a fallback were reported as one number, '
            'so a reader reconciling the ledgers cannot tell a real '
            'fallback from a failed capacity read.')


def dimension(name, limit, used, requested, exceeded, shortfall=None,
              cpu_load_1=None, expected_demand=None):
    """One CapacityDimensionDetailDict as the guard's event carries it."""
    detail = {
        'dimension': name,
        'limit': limit,
        'used': used,
        'requested': requested,
        'exceeded': exceeded,
    }
    if shortfall is not None:
        detail['shortfall'] = shortfall
    if cpu_load_1 is not None:
        detail['cpu_load_1'] = cpu_load_1
    if expected_demand is not None:
        detail['expected_demand'] = expected_demand
    return detail


def denial_event(failing_stage='node', dimensions=None, enforce=True):
    record = {
        'message': 'instance placement denied',
        'extra': {
            'node': NODE_ONE,
            'failing_stage': failing_stage,
            'dimensions': dimensions if dimensions is not None else [],
            'enforce': enforce,
        },
    }
    return ['1756000000000000000', json.dumps(record)]


def claim_event(namespace='ci-namespace', claim_dimensions=None):
    record = {
        'message': 'placement admitted over namespace capacity claim',
        'extra': {
            'node': NODE_ONE,
            'namespace': namespace,
            'claim_dimensions': claim_dimensions or [],
        },
    }
    return ['1756000000000000000', json.dumps(record)]


def forced_write_event(failing_stage='node', dimensions=None):
    record = {
        'message': 'placement recorded despite exceeding capacity guard',
        'extra': {
            'node': NODE_ONE,
            'failing_stage': failing_stage,
            'dimensions': dimensions if dimensions is not None else [],
        },
    }
    return ['1756000000000000000', json.dumps(record)]


class GuardCensusTestCase(HeadroomReportTestCase):
    """The capacity guard's own refusals, below the stage layer.

    The stage census stops above the guard, so a run in which every
    stage passed and the guard then refused every candidate reads as a
    clean run with no refusals -- which is the shape of issue 3772 and
    the reason this census exists.
    """

    def test_a_clean_stage_census_with_guard_refusals_is_not_clean(self):
        census = self._census([
            census_event('schedule at stage sufficient_idle_cpu'),
            denial_event(dimensions=[
                dimension('cpus', 6.0, 1.0, 1.0, False),
                dimension('demand', 1.5, 2.9, 0.6, True,
                          cpu_load_1=2.4, expected_demand=0.5)]),
            denial_event(dimensions=[
                dimension('demand', 1.5, 2.9, 0.6, True,
                          cpu_load_1=2.4, expected_demand=0.5)]),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'Placements refused by the guard: 2', output,
            'A run whose every scheduler stage passed and whose guard then '
            'refused every candidate still read as a clean run, which is '
            'exactly the 3772 shape this census was added to see.')
        self.assertIn('Guard refusals: YES', output)
        self.assertIn(
            'Refusal warning: no capacity-stage drops', output,
            'The guard refusals were folded into the stage census warning, '
            'so a reader can no longer tell which instrument saw what.')

    def test_a_claim_exceedance_is_never_counted_as_a_refusal(self):
        """An admitted placement over an advisory claim is not a refusal.

        CLAIM_ENFORCEMENT_HARD is False on purpose so exceedances are
        observed before they are refused. Counting one as a refusal would
        manufacture refusals on a cluster which did what it was asked.
        """
        census = self._census([
            claim_event(claim_dimensions=[
                dimension('cpus', 1.0, 1.0, 1.0, True, shortfall=1.0)]),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn('Placements refused by the guard: 0', output)
        self.assertIn('Claim exceedances (ADMITTED, never refused): 1', output)
        self.assertIn(
            'ci-namespace', output,
            'The namespace whose claim was exceeded was not reported, so the '
            'calibration signal D9 asks for names no claim to calibrate.')
        self.assertNotIn(
            'Guard refusals: YES', output,
            'A claim exceedance was reported as a refusal. It is an ADMITTED '
            'placement; advisory mode did what the operator asked.')

    def test_a_forced_write_is_counted_apart_from_refusals(self):
        """The P5 forced ground-truth write is a recorded placement.

        The collector ships it -- issue 4087's rule-out of the P5
        mechanism needed exactly this event -- so a report which dropped
        it would leave the durable record unable to tell a forced write
        from the never-reconciled window once the bundles expire.
        """
        census = self._census([
            denial_event(dimensions=[
                dimension('cpus', 3.0, 3.0, 1.0, True, shortfall=1.0)]),
            forced_write_event(dimensions=[
                dimension('cpus', 3.0, 3.0, 1.0, True, shortfall=1.0)]),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        target = os.path.join(self.tempdir, 'summary.json')
        code, output = self._run('--series', path, '--census', census,
                                 '--json', target)
        self.assertEqual(0, code)
        self.assertIn('Placements refused by the guard: 1', output)
        self.assertIn('Forced ground-truth writes past the guard (P5): 1',
                      output)
        with open(target) as f:
            record = json.load(f)
        guard = record['guard']
        self.assertEqual(1, guard['denials'])
        self.assertEqual(
            1, guard['forced_writes'],
            'The forced write was folded into another count or dropped. '
            'It is neither a refusal nor a claim exceedance.')
        self.assertEqual({'node': 1}, guard['forced_write_stages'])
        self.assertEqual({'cpus': 1}, guard['forced_write_exceeded'])
        self.assertEqual(
            2, record['census']['guard_events'],
            'The forced write did not count as a guard event, so it reads '
            'as an unmatched log line rather than a guard fact.')

    def test_a_forced_write_alone_is_a_collected_census(self):
        """One forced write with no refusals must not read as not_collected.

        'No guard events' is a statement about the collector's filter,
        and a census carrying only forced writes was collected by a
        filter which matches all three guard messages.
        """
        census = self._census([
            forced_write_event(dimensions=[
                dimension('quantum_flux', 1.0, 9.0, 1.0, True)]),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertNotIn('NO CAPACITY GUARD EVENTS IN THIS CENSUS.', output)
        self.assertIn('Forced ground-truth writes past the guard (P5): 1',
                      output)
        self.assertIn('Placements refused by the guard: 0', output)
        self.assertIn(
            'quantum_flux', output,
            'A dimension this tool has not been told about was dropped '
            'from a forced write; the no-hardcoded-list rule (D10) applies '
            'to every guard message equally.')
        self.assertIn('Counted but unrecognised', output)

    def test_an_unknown_stage_and_dimension_are_tallied_not_dropped(self):
        """The same no-hardcoded-list rule the stage census follows (D10)."""
        census = self._census([
            denial_event(failing_stage='a_guard_stage_from_next_year',
                         dimensions=[
                             dimension('quantum_flux', 1.0, 9.0, 1.0, True)]),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'a_guard_stage_from_next_year', output,
            'A failing_stage this tool has not been told about was dropped, '
            'so the census is filtered by a hardcoded list and drifts the '
            'first time the guard gains a stage.')
        self.assertIn('quantum_flux', output)
        self.assertIn('Counted but unrecognised', output)

    def test_no_guard_events_is_unknown_rather_than_zero(self):
        """The collector's filter decides whether there is anything to count.

        A census whose LogQL query selects only the stage messages holds
        no guard event whatever the guard did, and printing zero there
        would be the same dangerous reading as printing zero for a census
        which was never collected at all.
        """
        census = self._census([
            census_event('schedule at stage sufficient_idle_cpu'),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn('NO CAPACITY GUARD EVENTS IN THIS CENSUS', output)
        self.assertIn('Guard refusals: NOT COLLECTED', output)
        self.assertIn(
            'instance placement denied', output,
            'The report did not name the message the census filter has to '
            'match, so a reader cannot tell the query from the cluster.')

    def test_a_malformed_guard_event_is_counted_not_fatal(self):
        """D15 again: nothing about an event shape may fail the job."""
        broken = ['1756000000000000000', json.dumps({
            'message': 'instance placement denied', 'extra': 'not a dict'})]
        census = self._census([broken])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(
            0, code, 'A guard event whose extra was not a dict made the '
                     'report exit non-zero (D15).')
        self.assertIn('Placements refused by the guard: 1', output)
        self.assertIn('carried no usable dimensions list', output)

    def test_the_demand_split_reads_the_comparison_the_guard_made(self):
        """The demand clause does not charge the incoming placement.

        Since phase 4a the demand guard compares cpu_load_1 plus
        expected_demand against the limit and leaves `requested` out, so
        a split which added `requested` would report a comparison the
        guard never made -- and would call an estimator defect a busy
        node.
        """
        census = self._census([
            # Measured load is inside the limit; the feedforward estimate
            # is what carries the sum over it.
            denial_event(dimensions=[
                dimension('demand', 2.0, 2.5, 4.0, True,
                          cpu_load_1=1.0, expected_demand=1.5)]),
            # Measured load alone is already over.
            denial_event(dimensions=[
                dimension('demand', 2.0, 3.5, 4.0, True,
                          cpu_load_1=3.0, expected_demand=0.5)]),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            '    1  measured CPU load alone was already over the limit',
            output)
        self.assertIn(
            '    1  the D13 feedforward estimate is what carried it over',
            output,
            'The demand split counted `requested` into the comparison, which '
            'the guard does not since phase 4a, so an estimator defect reads '
            'as a node which was genuinely busy.')

    def test_a_reported_shortfall_is_printed_and_never_recomputed(self):
        """G3 puts the definition of shortfall server side, in one place."""
        census = self._census([
            denial_event(dimensions=[
                dimension('cpus', 4.0, 4.0, 1.0, True, shortfall=1.0)]),
            denial_event(dimensions=[
                dimension('cpus', 4.0, 6.0, 1.0, True, shortfall=3.0)]),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn('cpus         3.000', output)

    def test_a_series_without_shortfalls_says_so_rather_than_zero(self):
        census = self._census([
            denial_event(dimensions=[dimension('cpus', 4.0, 4.0, 1.0, True)]),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'No refused dimension carried a shortfall field', output,
            'A census predating the shortfall field reported a shortfall of '
            'zero, which reads as a refusal that was not actually over.')

    def test_a_claim_shortfall_is_not_reported_as_a_refusal_shortfall(self):
        """The two shortfalls answer different questions.

        A refusal's shortfall is how far short of the ledger a create
        which did not happen fell. A claim's is how far past a declared
        footprint an ADMITTED placement went. Printing the second under
        the first heading invents a refusal on a dimension nothing
        refused, which is the conflation the whole census is built to
        avoid.
        """
        census = self._census([
            denial_event(dimensions=[
                dimension('demand', 1.5, 2.9, 0.6, True,
                          cpu_load_1=2.4, expected_demand=0.5)]),
            claim_event(claim_dimensions=[
                dimension('cpus', 1.0, 0.0, 2.0, True, shortfall=1.0)]),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn(
            'No refused dimension carried a shortfall field', output,
            'The claim exceedance supplied a shortfall for a dimension no '
            'refusal exceeded, and it was printed as a refusal shortfall.')
        self.assertIn('Worst amount over the claim', output)
        self.assertEqual(
            1, output.count('cpus         1.000'),
            'The claim shortfall appears twice, so it is being printed under '
            'both headings rather than only the claim one.')

    def test_an_empty_dimensions_list_is_not_a_malformed_event(self):
        """A readable but empty list is a different fact from an unreadable one."""
        census = self._census([denial_event(dimensions=[])])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn('carried a readable but EMPTY dimensions list', output)
        self.assertNotIn('carried no usable dimensions list', output)

    def test_an_unenforced_denial_is_counted_apart(self):
        """A ground-truth writer's denial refuses nothing a caller asked for."""
        census = self._census([
            denial_event(dimensions=[dimension('cpus', 4.0, 4.0, 1.0, True)],
                         enforce=False),
        ])
        path = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', path, '--census', census)
        self.assertEqual(0, code)
        self.assertIn('1 of those had enforce=false', output)
        self.assertIn('The ledger refused 1 placement, of which 0', output)


def cells_under(output, heading, next_heading, first_cell):
    """The cells of one table row, found by its first column.

    The report's tables are whitespace aligned, so a row is read back by
    splitting on whitespace. Deliberately re-derived here rather than asked
    of the tool: a test which formats a row with the tool's own helpers and
    then compares it to the tool's output proves only that the tool is
    deterministic.
    """
    block = output.split(heading)[1].split(next_heading)[0]
    for line in block.splitlines():
        cells = line.split()
        if cells and cells[0] == first_cell:
            return cells
    raise AssertionError('no row for %r under %r' % (first_cell, heading))


def as_number(value, places=1):
    return '-' if value is None else '%.*f' % (places, value)


def as_ledger(low, high):
    if low is None:
        return '-'
    if low == high:
        return '%.1f' % low
    return '%.1f-%.1f' % (low, high)


def metric_cells(block):
    """The six cells a metric block renders as, derived from the record alone."""
    return [
        str(block['n']),
        as_number(block['p90']),
        as_number(block['peak']),
        as_ledger(block['ledger_min'], block['ledger_max']),
        as_number(block['p90_fraction'], 3),
        as_number(block['peak_fraction'], 3),
    ]


class SummaryRecordTestCase(HeadroomReportTestCase):
    """The machine-readable record, and the prose rendered from it (D18).

    Phase 2's harvest calls ``summary_record()`` over several hundred banked
    bundles and phase 5's guardrail reads the ``--json`` file, so the
    alternative -- both of them parsing the printed tables -- would make
    every future change to a heading a silent break of the dataset. The
    report now renders its prose from the record, and these tests exist to
    keep that true: if a future change recomputes a figure in a printer
    instead of reading it from the record, the two can drift, and the
    numbers in the job log and in the baseline dataset would disagree while
    both looked plausible.

    The second thing covered here is the one the plan is most insistent
    about. A census which was never collected must leave nulls in the
    record, never zeros: "we did not look" and "nothing was refused" are
    different findings, the second is the one the whole plan is hunting
    for, and a zero in a dataset is indistinguishable from a measurement
    (D20).
    """

    def _rich_series(self):
        """A series with everything the awkward paths need.

        A ledger which moves during the run, a node which never has a
        capacity row, a sample whose capacity read failed for every node at
        once, a node the roster explains as a non-hypervisor, and a failed
        sample.
        """
        roster = [roster_entry(NODE_ONE, 'sf1'), roster_entry(NODE_TWO, 'sf2'),
                  roster_entry(NODE_THREE, 'sf3', is_hypervisor=False)]
        records = []
        for i in range(11):
            records.append(sample({
                NODE_ONE: node_payload(
                    cpu_measured=i, cpu_committed=i + 1, cpu_limit=10 + (i % 3),
                    ram_available=24000 - 500 * i),
                NODE_TWO: node_payload(
                    cpu_measured=2, cpu_committed=0, cpu_limit=None,
                    cpu_hard_max=6, ram_max=16000, ram_available=8000),
            }, nodes=roster, sampled_at=1756000000.0 + 15 * i))
        records.append(sample({
            NODE_ONE: node_payload(cpu_measured=0, cpu_committed=0,
                                   cpu_limit=None, row_present=False),
            NODE_TWO: node_payload(cpu_measured=0, cpu_committed=0,
                                   cpu_limit=None, row_present=False),
        }, nodes=roster, sampled_at=1756000200.0))
        records.append({'sampled_at': 1756000215.0, 'error': 'HTTP 503'})
        return self._series(records)

    def _rich_census(self):
        return self._census([
            census_event('schedule at stage sufficient_idle_cpu'),
            census_event('schedule at stage sufficient_idle_cpu',
                         dropped={NODE_ONE: {'reason': 'would exceed hard max CPUs'}}),
            census_event('schedule has no candidates at stage '
                         'sufficient_idle_cpu, aborting',
                         dropped={NODE_TWO: {'reason': 'would exceed hard max CPUs'}}),
            census_event('schedule at stage sufficient_idle_memory',
                         dropped={NODE_ONE: {'reason': 'no memory_max in node metrics'}}),
            denial_event(dimensions=[dimension('cpus', 6.0, 6.0, 2.0, True,
                                               shortfall=2.0)]),
        ])

    def _record_and_output(self, *extra):
        path = self._rich_series()
        census = self._rich_census()
        target = os.path.join(self.tempdir, 'summary.json')
        code, output = self._run('--series', path, '--census', census,
                                 '--label', 'slim-primary', '--json', target,
                                 *extra)
        # Asserted rather than ignored: a report which raised would still
        # return 0 (main() swallows it), and every caller of this helper
        # would then read a record which is not the one the run produced.
        self._assert_rendered(code, output)
        with open(target) as f:
            return json.load(f), output

    def test_every_printed_figure_is_the_figure_in_the_record(self):
        """The prose and the record cannot disagree, because both are the record.

        This walks the printed tables and the printed scalars and checks
        each against the record which produced them. A figure recomputed in
        a printer rather than read from the record fails here -- which is
        the drift D18 exists to make impossible, because the baseline in
        the master plan and the numbers in the job log a reader checks it
        against would then be different numbers.
        """
        record, output = self._record_and_output()

        series = record['series']
        self.assertIn(
            '  Samples:           %d usable, %d failed (an "error" record), '
            '%d unparseable line'
            % (series['samples_usable'], series['samples_failed'],
               series['unparseable_lines']), output,
            'The sample counts in the record are not the sample counts in '
            'the printed report.')
        self.assertIn(
            '  LEDGER UNREADABLE: %d of %d samples'
            % (series['ledger_unreadable_samples'], series['samples_usable']),
            output,
            'The count of ledger-unreadable samples disagrees between the '
            'record and the report. That count is how many samples are '
            'excluded from every committed-CPU figure, so a reader who '
            'trusts one and not the other is reading a different run.')

        provenance = record['ledger_provenance']
        for line, key in (
                ('  Node-samples with a capacity row (cpu_limit):     %d',
                 'node_samples_with_row'),
                ('  Node-samples which fell back to cpu_hard_max:     %d',
                 'node_samples_fallback'),
                ('  Fallbacks inside ledger-unreadable samples:       %d',
                 'node_samples_fallback_unreadable'),
                ('  Node-samples with no CPU ledger at all:           %d',
                 'node_samples_without_ledger')):
            self.assertIn(line % provenance[key], output,
                          'The D7 ledger provenance count %s is not the one '
                          'printed. D7 is settled from these figures.' % key)

        cluster = record['cluster']
        cluster_block = (output.split('Cluster-wide headroom')[1]
                         .split('Committed vCPU, per node')[0])
        for prefix, key, why in (
                ('  committed vCPU ', 'committed_cpu',
                 'That row is the one D3\'s band verdict is read from.'),
                ('  committed memory (MB) ', 'committed_memory_mb',
                 'Whether memory ever binds is what decides phase 0\'s D5.')):
            rows = [line for line in cluster_block.splitlines()
                    if line.startswith(prefix)]
            self.assertEqual(1, len(rows),
                             'The cluster table has %d rows starting %r.'
                             % (len(rows), prefix))
            self.assertEqual(
                metric_cells(cluster[key]),
                rows[0].split()[len(prefix.split()):],
                'The cluster-wide %s row does not match the record. %s'
                % (key, why))

        for node, blocks in record['per_node'].items():
            self.assertEqual(
                [node] + metric_cells(blocks['committed_cpu']),
                cells_under(output, 'Committed vCPU, per node',
                            'Committed memory (MB), per node', node),
                'Node %s\'s committed vCPU row does not match the record. '
                'The per-node figures are what D21 is argued from: a '
                'cluster-wide fraction inside the band above a node pinned '
                'at 1.000.' % node)
            self.assertEqual(
                [node] + metric_cells(blocks['committed_memory_mb']),
                cells_under(output, 'Committed memory (MB), per node',
                            'Nodes absent from per_node', node),
                'Node %s\'s committed memory row does not match the record.'
                % node)

        for classification, entry in record['absences']['classifications'].items():
            self.assertIn('%s %d' % (classification, entry['node_samples']),
                          ' '.join(output.split()),
                          'The absence classification %r is counted '
                          'differently in the record and the report. A node '
                          'missing from per_node has four possible meanings '
                          'and only the count says how often it happened.'
                          % classification)

        census = record['census']
        self.assertIn(
            '  Log records read:  %d (%d were schedule stage events, '
            '%d were capacity guard events'
            % (census['records'], census['stage_events'],
               census['guard_events']), output,
            'The census record counts disagree with the printed ones.')
        for stage, tally in census['stages'].items():
            cells = cells_under(output, 'Refusal census', 'Capacity guard census',
                                stage)
            self.assertEqual(
                [str(tally['events']), str(tally['aborts']),
                 str(tally['dropped'])], cells[1:4],
                'The census tally for stage %r does not match the record. '
                'The per-stage refusal census is the corpus phase 3 checks '
                'its inventory of signatures against.' % stage)

        guard = record['guard']
        self.assertIn('  Placements refused by the guard: %d' % guard['denials'],
                      output,
                      'The guard refusal count in the record is not the one '
                      'printed. A guard denial is a create which did not '
                      'happen, which is the #3772 shape.')

        verdict = record['verdict']
        self.assertIn(
            '  p90 committed vCPU / ledger, cluster wide: %s'
            % as_number(verdict['p90_cpu_fraction'], 3), output,
            'The ratio the band verdict was reached from is not the ratio '
            'printed beside it.')
        self.assertIn('  Verdict: %s' % verdict['band'], output,
                      'The band verdict in the record is not the verdict '
                      'printed.')
        self.assertEqual(
            verdict['p90_cpu_fraction'],
            record['cluster']['committed_cpu']['p90_fraction'],
            'The verdict judged a different number from the one the '
            'cluster-wide table publishes. There must be exactly one p90 '
            'committed-vCPU fraction in this report.')

    def test_an_absent_census_is_null_in_the_record_and_never_zero(self):
        """A census nobody collected must not read as a run which refused nothing.

        This is the misreading the whole plan is most exposed to. A zero in
        a dataset is indistinguishable from a measurement, and the baseline
        phase 2 publishes is read by phases 3, 4 and 5 -- so an uncollected
        census which recorded itself as zero refusals would be evidence for
        shrinking a cloud that had in fact been refusing creates (D20).
        """
        path = self._series([sample({NODE_ONE: node_payload()})])
        target = os.path.join(self.tempdir, 'summary.json')
        code, output = self._run('--series', path, '--json', target)
        self.assertEqual(0, code)
        with open(target) as f:
            record = json.load(f)

        census = record['census']
        self.assertEqual('not requested', census['state'])
        self.assertFalse(census['available'])
        for key in ('records', 'stage_events', 'guard_events', 'stages',
                    'capacity_shortage_drops', 'unclassified_shortage_drops',
                    'disk_bandwidth_drops', 'missing_data_drops', 'truncated'):
            self.assertIsNone(
                census[key],
                'census[%r] is %r rather than null for a census which was '
                'never collected. Zero reads as a measurement.'
                % (key, census[key]))

        self.assertEqual(
            'no_census', record['guard']['state'],
            'The guard census state must say no census was supplied. Phase '
            '5\'s guardrail reads this field to decide whether it may draw '
            'any conclusion at all about refusals.')
        self.assertIsNone(record['guard']['denials'])
        self.assertIsNone(record['guard']['claims'])
        self.assertIsNone(record['guard']['forced_writes'])
        self.assertIsNone(
            record['verdict']['refusal_warning'],
            'The refusal half of the band verdict must be unknown rather '
            'than False when no census was read (D3).')
        self.assertIn('NO CENSUS WAS SUPPLIED', output)

    def test_a_census_carrying_no_guard_events_is_not_collected_not_zero(self):
        """The retrospective window's blind spot, recorded as a blind spot.

        Until D20 widens the collector's LogQL filter, every banked census
        matches the scheduler's stage messages only, so it carries no guard
        event whatever the guard did. That is a fact about the query before
        it is a fact about the cluster, and the record has to say so.
        """
        census = self._census([
            census_event('schedule at stage sufficient_idle_cpu')])
        path = self._series([sample({NODE_ONE: node_payload()})])
        target = os.path.join(self.tempdir, 'summary.json')
        code, _ = self._run('--series', path, '--census', census,
                            '--json', target)
        self.assertEqual(0, code)
        with open(target) as f:
            record = json.load(f)

        self.assertEqual('read', record['census']['state'])
        self.assertEqual(1, record['census']['stage_events'])
        self.assertEqual(
            'not_collected', record['guard']['state'],
            'A census which carried stage events and no guard events must '
            'record the guard census as not collected. Reporting zero '
            'refusals here would be reporting the filter, not the cluster.')
        self.assertIsNone(record['guard']['denials'])

    def test_an_unreadable_census_is_unavailable_rather_than_empty(self):
        """A broken log shipping path looks exactly like a cluster with room."""
        census = self._write('census.json', 'this is not json')
        path = self._series([sample({NODE_ONE: node_payload()})])
        target = os.path.join(self.tempdir, 'summary.json')
        code, _ = self._run('--series', path, '--census', census,
                            '--json', target)
        self.assertEqual(0, code)
        with open(target) as f:
            record = json.load(f)
        self.assertEqual('unparseable', record['census']['state'])
        self.assertIsNone(record['census']['capacity_shortage_drops'])
        self.assertEqual('census_unavailable', record['guard']['state'])

    def test_the_per_node_maximum_fraction_is_recorded(self):
        """D21: the cluster-wide fraction averages a full node against an empty one.

        The shape here is the one merge run 33944911413 actually recorded:
        a cluster-wide p90 comfortably inside the band while one node sat
        pinned at its own ledger for the whole run. A band written only
        cluster-wide calls that run healthy, which is the precise failure
        this plan exists to stop making, so the per-node maximum is a
        first-class figure in the record.
        """
        records = []
        for i in range(10):
            records.append(sample({
                NODE_ONE: node_payload(cpu_measured=6, cpu_committed=6,
                                       cpu_limit=6, cpu_hard_max=6),
                NODE_TWO: node_payload(cpu_measured=0, cpu_committed=0,
                                       cpu_limit=18, cpu_hard_max=18),
            }, sampled_at=1756000000.0 + 15 * i))
        path = self._series(records)
        target = os.path.join(self.tempdir, 'summary.json')
        code, _ = self._run('--series', path, '--json', target)
        self.assertEqual(0, code)
        with open(target) as f:
            record = json.load(f)

        self.assertEqual(
            0.25, record['cluster']['committed_cpu']['p90_fraction'],
            'The cluster-wide fraction should be 6 committed over a 24 vCPU '
            'ledger.')
        self.assertEqual(
            'OVERSIZED', record['verdict']['band'],
            'Read cluster-wide this run is under the lower bound, which is '
            'exactly the reading D21 says is misleading.')
        self.assertEqual(
            {'n': 10, 'p90': 1.0, 'peak': 1.0},
            record['per_node_max_cpu_fraction'],
            'The per-node maximum committed fraction is missing or wrong. '
            'One node was full for every sample of this run while the '
            'cluster-wide figure read a quarter, and the scheduler admits '
            'against one node\'s ledger at a time, never against the mean.')
        self.assertEqual(
            1.0, record['verdict']['per_node_max_p90_fraction'],
            'The verdict must carry the per-node maximum beside the '
            'cluster-wide ratio it judged.')
        self.assertEqual(
            'ABOVE BAND', record['verdict']['per_node_band'],
            'D4 judges the per-node maximum against PER_NODE_BAND_UPPER '
            '(0.85). A p90 of 1.0 is above it, and the cluster-wide band '
            'being OVERSIZED must not suppress that -- they are separate '
            'verdicts (D21).')
        self.assertEqual(
            report.PER_NODE_BAND_UPPER, record['verdict']['per_node_band_upper'],
            'The record must carry the bound the per-node verdict was '
            'judged against, not just the verdict, so a reader does not '
            'have to know the constant to check the arithmetic.')

    def test_the_ledger_is_recorded_as_the_range_it_moved_over(self):
        """A ledger which changed mid-run is a finding, not something to average.

        The reconciler rewriting a capacity row is the kind of event D7's
        12-versus-10 discrepancy might turn out to be made of.
        """
        records = [
            sample({NODE_ONE: node_payload(cpu_limit=10)},
                   sampled_at=1756000000.0),
            sample({NODE_ONE: node_payload(cpu_limit=12)},
                   sampled_at=1756000015.0),
        ]
        path = self._series(records)
        target = os.path.join(self.tempdir, 'summary.json')
        code, output = self._run('--series', path, '--json', target)
        self.assertEqual(0, code)
        with open(target) as f:
            record = json.load(f)
        block = record['per_node'][NODE_ONE]['committed_cpu']
        self.assertEqual((10.0, 12.0), (block['ledger_min'], block['ledger_max']))
        self.assertIn('10.0-12.0', output,
                      'A ledger which moved must print as the range the '
                      'record carries, not as one of its ends.')

    def test_the_record_is_the_same_whether_it_is_written_or_returned(self):
        """The harvest calls the function; the guardrail reads the file (D18).

        Phase 2's harvest runs this over several hundred bundles locally, so
        it calls summary_record() rather than shelling out. If the function
        and the --json file could differ, the dataset and the guardrail
        would be measuring different things.
        """
        path = self._rich_series()
        census = self._rich_census()
        target = os.path.join(self.tempdir, 'summary.json')
        code, output = self._run('--series', path, '--census', census,
                                 '--label', 'slim-tier', '--json', target)
        self._assert_rendered(code, output)
        with open(target) as f:
            written = json.load(f)
        returned = report.summary_record(path, census=census, label='slim-tier')
        self.assertEqual(
            json.loads(json.dumps(returned)), written,
            'summary_record() and the --json file disagree, so the harvest '
            'and phase 5\'s guardrail would read different numbers.')
        self.assertEqual('slim-tier', written['label'])
        self.assertEqual(report.RECORD_VERSION, written['record_version'])

    def test_record_version_is_3_and_band_provisional_is_gone(self):
        """Phase 5's D2: the band is defended, not provisional, as of version 3.

        The flag itself is dropped rather than merely flipped to False --
        nothing read it, and a reader wanting to know whether a record's
        band is defended checks record_version instead (see RECORD_VERSION's
        own comment).
        """
        path = self._series([sample({NODE_ONE: node_payload()})])
        record = report.summary_record(path)
        self.assertEqual(3, report.RECORD_VERSION)
        self.assertEqual(3, record['record_version'])
        self.assertNotIn(
            'band_provisional', record['verdict'],
            'band_provisional should no longer be a key in the verdict '
            'record (D2); phase 2 defended the band and nothing reads the '
            'flag.')

    def test_a_json_path_which_cannot_be_written_is_a_warning_not_a_failure(self):
        """D15 holds for the output file as much as for the inputs.

        A full disk or a missing directory must not fail the job, and must
        not lose the printed report either -- the record is written after
        the prose for exactly that reason.
        """
        path = self._series([sample({NODE_ONE: node_payload()})])
        target = os.path.join(self.tempdir, 'no-such-directory', 'summary.json')
        code, output = self._run('--series', path, '--json', target)
        self.assertEqual(
            0, code,
            'An unwritable --json path failed the job. An instrument which '
            'can fail the run it is measuring changes what it measures '
            '(D15).')
        self.assertIn('WARNING: the summary record could not be written',
                      output)
        self.assertIn('Shaken Fist CI headroom report', output,
                      'The printed report was lost when the record could not '
                      'be written.')

    def test_a_series_which_could_not_be_read_still_yields_a_record(self):
        """The harvest must be able to record a job which produced nothing.

        A bundle whose probe never started is a fact about the window --
        phase 2 records it rather than dropping the run, because a
        disappearing job would silently shrink n.
        """
        record = report.summary_record(
            os.path.join(self.tempdir, 'does-not-exist.jsonl'))
        self.assertIsNotNone(record['series']['read_error'])
        self.assertEqual(0, record['series']['samples_usable'])
        self.assertIsNone(
            record['verdict']['band'],
            'A run with no samples must have no band verdict rather than a '
            'verdict computed from nothing.')
        self.assertIsNone(record['cluster']['committed_cpu']['p90'])


class CapacityWaitsTestCase(HeadroomReportTestCase):
    """--waits summarises the JSONL trace create_instance() writes (D14).

    An absent or empty file must read as "unknown", never as "0 seconds
    waited" -- printing a zero there is precisely the misreading a broken
    instrument would produce, which is why census_record() and now
    waits_record() both refuse to say it. A malformed line among good ones
    is skipped and counted, not fatal, because a crashed stestr worker
    leaves one on every run that hits it.
    """

    def test_a_populated_file_reports_the_five_figures(self):
        path = self._waits([
            wait_event(test_id='t.test_a', seconds_waited=10.0,
                       mode='informed'),
            wait_event(test_id='t.test_b', seconds_waited=90.0,
                       mode='degraded'),
            wait_event(test_id='t.test_c', seconds_waited=5.0,
                       mode='informed'),
        ])
        series = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', series, '--waits', path)
        self.assertEqual(0, code)

        self.assertIn('Waits:             3', output)
        self.assertIn('Total time waited: 105.0s', output)
        self.assertIn('Longest wait:      90.0s (t.test_b)', output,
                      'The longest wait and the test it belongs to were not '
                      'both printed.')
        self.assertIn('Mode split:        2 informed, 1 degraded', output)

    def test_an_absent_waits_file_is_never_zero_seconds(self):
        series = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run(
            '--series', series,
            '--waits', os.path.join(self.tempdir, 'does-not-exist.jsonl'))
        self.assertEqual(0, code)
        self.assertIn('NO CAPACITY WAIT DATA IS AVAILABLE', output)
        self.assertIn('never as zero waits', output)
        self.assertNotIn('0 seconds waited', output)
        self.assertNotIn('Total time waited', output)

    def test_an_empty_waits_file_is_never_zero_seconds(self):
        path = self._write('instance-waits.jsonl', '')
        series = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', series, '--waits', path)
        self.assertEqual(0, code)
        self.assertIn('NO CAPACITY WAIT DATA IS AVAILABLE', output)
        self.assertIn('empty', output)
        self.assertIn('never as zero waits', output)
        self.assertNotIn('Total time waited', output)

    def test_no_waits_argument_says_nothing_was_looked_at(self):
        series = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', series)
        self.assertEqual(0, code)
        self.assertIn('NO WAITS FILE WAS SUPPLIED', output)
        self.assertIn('nothing was looked at', output)

    def test_a_malformed_line_among_good_ones_is_skipped_and_counted(self):
        path = self._waits(
            [wait_event(test_id='t.test_a', seconds_waited=20.0)],
            trailing='not json at all\n')
        series = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', series, '--waits', path)
        self.assertEqual(0, code)
        self.assertIn('Waits:             1 (1 malformed line skipped)', output)
        self.assertIn('Total time waited: 20.0s', output)

    def test_a_file_of_only_malformed_lines_is_unknown_not_zero(self):
        """Every line malformed is a broken writer, not a quiet run.

        A JSONL file with content, none of which parsed, must not print
        '0 seconds waited' either: that reading is indistinguishable from a
        run which genuinely never waited, and the two are different facts.
        """
        path = self._write('instance-waits.jsonl', 'garbage\nmore garbage\n')
        series = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', series, '--waits', path)
        self.assertEqual(0, code)
        self.assertIn('NO CAPACITY WAIT DATA IS AVAILABLE: unparseable', output)
        self.assertIn('every one of the 2 lines in the file was malformed',
                      output)
        self.assertIn('never as zero waits', output)
        self.assertNotIn('Total time waited', output)

    def test_an_all_malformed_file_reads_as_unknown_to_a_machine_too(self):
        """The prose said unknown; the record said 'available, zero waits'.

        waits_record() is what phase 5 and the sizing plan's guardrail
        read, and a consumer which cannot read prose saw 'available: true,
        count: 0' -- indistinguishable from a run which genuinely never
        waited, which is the exact confusion the printed text spends three
        paragraphs guarding against.
        """
        path = self._write('instance-waits.jsonl', 'garbage\nmore garbage\n')
        record = report.waits_record(report.read_waits(path))

        self.assertEqual('unparseable', record['state'])
        self.assertFalse(
            record['available'],
            'A file nothing could be parsed out of carries no data, so it '
            'must not be marked available.')
        self.assertIsNone(
            record['count'],
            'A count of 0 here is a lie: the run may have waited for '
            'hours and lost every line to a broken writer.')
        self.assertIsNone(record['seconds_waited_total'])
        self.assertEqual(
            2, record['malformed_lines'],
            'How much was lost is the one thing which can be said, so it '
            'is still said.')

    def test_a_file_of_good_lines_is_a_real_zero_when_it_is_empty_of_waits(self):
        """The positive control for the state above.

        Malformed lines among good ones are skipped and counted, and the
        file is still read: the unparseable state must not swallow a run
        which produced usable data.
        """
        path = self._waits(
            [wait_event(test_id='t.test_a', seconds_waited=4.0)],
            trailing='{"partial": ')
        record = report.waits_record(report.read_waits(path))

        self.assertEqual('read', record['state'])
        self.assertTrue(record['available'])
        self.assertEqual(1, record['count'])
        self.assertEqual(1, record['malformed_lines'])
        self.assertEqual(4.0, record['seconds_waited_total'])

    def test_an_unrecognised_mode_is_counted_apart_from_the_split(self):
        path = self._waits([
            wait_event(test_id='t.test_a', mode='informed'),
            wait_event(test_id='t.test_b', mode='sideways'),
        ])
        series = self._series([sample({NODE_ONE: node_payload()})])
        code, output = self._run('--series', series, '--waits', path)
        self.assertEqual(0, code)
        self.assertIn('Mode split:        1 informed, 0 degraded', output)
        self.assertIn('1 wait carried a mode this report does not '
                      'recognise', output)

    def test_read_waits_and_waits_record_agree_with_the_printed_report(self):
        """The functions the report is built from, exercised directly.

        Phase 2's harvest is expected to call these the way phase 1's does
        summary_record() -- straight over a downloaded bundle -- so they
        need to work without going through main().
        """
        path = self._waits([
            wait_event(test_id='t.test_a', seconds_waited=1.0, mode='informed'),
            wait_event(test_id='t.test_b', seconds_waited=2.0, mode='degraded'),
        ])
        record = report.waits_record(report.read_waits(path))
        self.assertEqual('read', record['state'])
        self.assertTrue(record['available'])
        self.assertEqual(2, record['count'])
        self.assertEqual(0, record['malformed_lines'])
        self.assertEqual(3.0, record['seconds_waited_total'])
        self.assertEqual(1, record['informed_waits'])
        self.assertEqual(1, record['degraded_waits'])
        self.assertEqual(2.0, record['longest_wait_seconds'])
        self.assertEqual('t.test_b', record['longest_wait_test'])

        not_requested = report.waits_record(report.read_waits(None))
        self.assertEqual('not requested', not_requested['state'])
        self.assertFalse(not_requested['available'])
        self.assertIsNone(not_requested['count'])


class OutputOrderingTestCase(HeadroomReportTestCase):
    """The record is the dataset; the annotation is a convenience.

    ``emit_github_annotations()`` and ``write_github_step_summary()``
    are not guarded locally, so a raise in either is caught by
    ``main()``'s global handler -- which, when they ran first, silently
    cost the run its ``--json`` record while the tool still exited 0 and
    said nothing was wrong. ``ci_headroom_harvest.py`` reads those
    records, so that failure mode costs the dataset rather than one
    annotation.
    """

    def _series_with_a_verdict(self):
        return self._series([
            sample({NODE_ONE: node_payload(cpu_measured=10, cpu_committed=10,
                                           cpu_limit=10, cpu_hard_max=12)}),
        ])

    def test_the_record_survives_an_annotation_which_raises(self):
        path = self._series_with_a_verdict()
        record_path = os.path.join(self.tempdir, 'record.json')

        original = report.emit_github_annotations

        def _explode(record):
            raise RuntimeError('annotation code is unguarded on purpose')

        report.emit_github_annotations = _explode
        try:
            code, _ = self._run('--series', path, '--json', record_path)
        finally:
            report.emit_github_annotations = original

        self.assertEqual(0, code)
        self.assertTrue(
            os.path.exists(record_path),
            'The --json record was not written because the annotation code '
            'raised first. The record is what the harvest reads; an '
            'annotation failing must not cost a run its data.')
        with open(record_path) as f:
            self.assertEqual(3, json.load(f)['record_version'])

    def test_the_record_survives_a_step_summary_which_raises(self):
        path = self._series_with_a_verdict()
        record_path = os.path.join(self.tempdir, 'record.json')

        original = report.write_github_step_summary

        def _explode(record):
            # write_github_step_summary() catches only OSError, and it
            # builds the lines inside the try, so a TypeError or KeyError
            # from step_summary_lines() escapes it.
            raise TypeError('step summary code is unguarded on purpose')

        report.write_github_step_summary = _explode
        try:
            code, _ = self._run('--series', path, '--json', record_path)
        finally:
            report.write_github_step_summary = original

        self.assertEqual(0, code)
        self.assertTrue(os.path.exists(record_path))


class WorkflowCommandPropertyTestCase(HeadroomReportTestCase):
    """A property value needs two escapes an ordinary value does not.

    ':' ends a workflow command's property list and ',' separates its
    properties, so both have to be encoded inside one. Every title this
    tool emits contains a colon.
    """

    def test_property_encoding_covers_colon_and_comma(self):
        self.assertEqual(
            '%3A%2C',
            report._encode_workflow_command_property(':,'))

    def test_property_encoding_still_covers_what_a_value_needs(self):
        # It must not lose the percent-first ordering it builds on: a
        # literal '%3A' in the input has to survive as '%253A'.
        self.assertEqual(
            '%253A%0A100%25',
            report._encode_workflow_command_property('%3A\n100%'))

    def test_emitted_titles_are_property_encoded(self):
        path = self._series([
            sample({NODE_ONE: node_payload(cpu_measured=10, cpu_committed=10,
                                           cpu_limit=10, cpu_hard_max=12)}),
        ])
        code, output = self._run('--series', path)
        self._assert_rendered(code, output)

        titles = [line.split('::')[1][len('warning title='):]
                  for line in output.splitlines()
                  if line.startswith('::warning title=')]
        self.assertNotEqual([], titles, 'no annotation was emitted at all')
        for title in titles:
            with self.subTest(title=title):
                self.assertNotIn(':', title)
                self.assertNotIn(',', title)
                self.assertIn('%3A', title)


class PerNodeNoVerdictTestCase(HeadroomReportTestCase):
    def test_the_per_node_no_verdict_reaches_stdout_as_well(self):
        # The step summary covered this; the job log did not. A reader
        # looking at the log would have seen a per-node bound printed with
        # nothing said about whether it was met.
        path = self._series([])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn(
            'NO VERDICT: no sample produced a per-node committed-vCPU-',
            output,
            'print_verdict() printed the D4 per-node bound without saying '
            'that no verdict could be reached against it.')


class BandGateTestCase(HeadroomReportTestCase):
    """5f armed the cluster-wide upper bound, and nothing else.

    The contract has two halves in two repositories.  Here the report
    returns ``BAND_VIOLATION_EXIT`` for an oversubscribed cluster it could
    read, and zero for everything else it can meet -- including an
    oversubscribed verdict resting on a series too thin or too unreadable
    to trust.  In ``shakenfist/actions``,
    ``tools/ci_headroom_verdict.sh`` fails the job on that status -- but
    only after grepping this file's source for the constant's name, so a
    report too old to implement the contract cannot be read as
    implementing it.  Both halves are asserted below, because the half
    which lives elsewhere is the one nothing here would otherwise catch.
    """

    def _oversubscribed(self):
        return self._series(self._readable({NODE_ONE: node_payload(
            cpu_measured=9, cpu_committed=9, cpu_limit=10)}))

    @staticmethod
    def _busy_pair():
        return {
            NODE_ONE: node_payload(cpu_measured=9, cpu_committed=9,
                                   cpu_limit=10),
            NODE_TWO: node_payload(cpu_measured=9, cpu_committed=9,
                                   cpu_limit=10),
        }

    @staticmethod
    def _blind_pair(sampled_at, capacity_degraded):
        return sample({
            NODE_ONE: node_payload(cpu_measured=0, cpu_committed=0,
                                   cpu_limit=None, row_present=False),
            NODE_TWO: node_payload(cpu_measured=0, cpu_committed=0,
                                   cpu_limit=None, row_present=False),
        }, sampled_at=sampled_at, capacity_degraded=capacity_degraded)

    def _assert_withheld(self, records, reason):
        code, output = self._run('--series', self._series(records))
        self.assertEqual(
            0, code,
            'An OVERSUBSCRIBED verdict the instrument could not trust '
            'failed the job. That is the instrument talking about itself, '
            'which D15 says may never gate.')
        self.assertIn('OVERSUBSCRIBED', output)
        self.assertIn('This verdict would gate (5f), but', output)
        self.assertIn(
            reason, output,
            'The report withheld the gate without saying why.')
        self.assertNotIn('Returning %d' % report.BAND_VIOLATION_EXIT, output)

    def test_an_oversubscribed_cluster_returns_the_band_violation_status(self):
        code, output = self._run('--series', self._oversubscribed())
        self.assertEqual(
            report.BAND_VIOLATION_EXIT, code,
            'A cluster above the upper bound did not return the band '
            'violation status, so the gate in shakenfist/actions can never '
            'fire and the whole of 5f is inert.')
        self.assertIn('OVERSUBSCRIBED', output)

    def _summary(self, series):
        """Exit code, stdout and step summary lines for one series."""
        record_path = os.path.join(self.tempdir, 'record.json')
        code, output = self._run('--series', series, '--json', record_path)
        with open(record_path) as f:
            return code, output, report.step_summary_lines(json.load(f))

    def test_a_gating_verdict_names_itself_on_every_surface(self):
        """A red job's annotation and summary must say the band caused it.

        The job log alone is not enough: the annotation and the step
        summary are what a reader of a failed job sees first.
        """
        code, output, summary = self._summary(self._oversubscribed())
        self.assertEqual(report.BAND_VIOLATION_EXIT, code)
        self.assertIn(
            '::warning title=CI headroom%3A cluster OVERSUBSCRIBED::', output,
            'A verdict which fails the job raised no annotation.')
        self.assertTrue(
            any(line.startswith('* Gate: this verdict returns exit status %d'
                                % report.BAND_VIOLATION_EXIT)
                for line in summary),
            'The step summary of a gating run does not say the band verdict '
            'decided the exit status: %r' % summary)

    def test_a_withheld_verdict_says_so_in_the_step_summary(self):
        """The gate going quiet has to be visible without reading the log."""
        code, _, summary = self._summary(self._series(self._readable(
            {NODE_ONE: node_payload(cpu_measured=9, cpu_committed=9,
                                    cpu_limit=10)}, count=1)))
        self.assertEqual(0, code)
        gate = [line for line in summary if line.startswith('* Gate:')]
        self.assertEqual(1, len(gate), summary)
        self.assertIn('WITHHELD', gate[0])
        self.assertIn(str(report.BAND_GATE_MIN_SAMPLES), gate[0],
                      'The withheld line does not say why.')

    def test_a_verdict_which_cannot_gate_has_no_gate_line(self):
        code, _, summary = self._summary(self._series(self._readable(
            {NODE_ONE: node_payload(cpu_measured=5, cpu_committed=5,
                                    cpu_limit=10)})))
        self.assertEqual(0, code)
        self.assertEqual(
            [], [line for line in summary if line.startswith('* Gate:')])

    def test_the_status_is_three_because_the_other_repository_says_so(self):
        """The number is a cross-repository constant, not a local choice.

        ``ci_headroom_verdict.sh`` hardcodes 3 as ``band_violation_status``.
        Changing this value here without changing it there turns the gate
        off silently: the verdict script reads any other non-zero status as
        the report being unhappy and exits 0.
        """
        self.assertEqual(3, report.BAND_VIOLATION_EXIT)

    def test_the_source_names_the_constant_the_verdict_script_greps(self):
        """The name is the version-skew guard, so it is asserted literally.

        ``ci_headroom_verdict.sh`` believes a status of 3 only when the
        string ``BAND_VIOLATION_EXIT`` appears in this file's source --
        the same feature-detection ``ci_headroom_collect.sh`` already does
        before passing a flag an older report would reject.  Renaming the
        constant is therefore a way to switch the gate off, and doing it
        by accident is the failure this asserts against.
        """
        with open(REPORT_PATH) as f:
            source = f.read()
        # A definition, not an occurrence: the comment above the constant
        # names it several times, and a rename which left that comment in
        # place would otherwise still pass here while the gate had become
        # a status nothing names.
        self.assertIsNotNone(
            re.search(r'^BAND_VIOLATION_EXIT = ', source, re.M),
            'The report source does not define the sentinel '
            'ci_headroom_verdict.sh greps for, so shakenfist/actions will '
            'decline to believe a status of 3 and the gate is off.')

    def test_an_oversized_cluster_does_not_gate(self):
        """D8: the lower bound is information, and gating on it would be bad.

        Not merely undesirable -- 30 of the 40 cluster job-runs 5e measured
        read OVERSIZED, so a status returned here would redden three
        quarters of cluster CI on the first run after this merged.
        """
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=2, cpu_committed=2, cpu_limit=10)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn('OVERSIZED', output)

    def test_a_cluster_within_the_band_does_not_gate(self):
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=5, cpu_committed=5, cpu_limit=10)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn('WITHIN BAND', output)

    def test_a_node_above_the_per_node_bound_alone_does_not_gate(self):
        """D4 and D7: the per-node bound is published and never gates.

        One node pinned at its own ledger while the cluster sits at 0.50.
        The per-node verdict reads ABOVE BAND and the job still passes.
        """
        path = self._series([
            sample({
                NODE_ONE: node_payload(cpu_measured=10, cpu_committed=10,
                                       cpu_limit=10, cpu_hard_max=12),
                NODE_TWO: node_payload(cpu_measured=0, cpu_committed=0,
                                       cpu_limit=10, cpu_hard_max=12),
            }),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(
            0, code,
            'A per-node maximum above its bound failed the job. D4 says '
            'that statistic never gates: phase 2 found it saturated at its '
            'ceiling in plenty of passing job-runs.')
        self.assertIn('ABOVE BAND', output)

    def test_a_series_with_no_verdict_does_not_gate(self):
        code, output = self._run('--series', self._series([]))
        self.assertEqual(
            0, code,
            'A series too thin to produce a fraction gated. There is no '
            'statement about the cloud here to act on.')
        self.assertIn('NO VERDICT', output)

    def test_a_report_which_raises_does_not_gate(self):
        """D15 in full: the instrument's own defects never fail the job.

        This is the case the narrow status exists for.  main() swallows the
        exception, so there is no record, so there is no verdict -- and a
        tool which returned 3 whenever it was confused would be an
        instrument failing the runs it cannot measure.
        """
        path = self._oversubscribed()
        original = report.print_report

        def _explode(record, waits=None):
            raise RuntimeError('the report itself is broken')

        report.print_report = _explode
        self.addCleanup(setattr, report, 'print_report', original)

        code, output = self._run('--series', path)
        self.assertEqual(
            0, code,
            'A report which raised returned the band violation status. '
            'That would fail a job over a bug in the instrument rather '
            'than over the cloud it was measuring.')
        self.assertIn('The headroom report failed to render:', output)

    def test_a_usage_error_does_not_gate(self):
        code, _ = self._run('--not-an-argument')
        self.assertEqual(0, code)

    def test_the_gate_rests_on_at_least_the_minimum_sample_count(self):
        """One sample short of the floor is withheld; the floor itself gates.

        Both sides are asserted so the boundary cannot drift by one in
        either direction without a test naming it.
        """
        per_node = {NODE_ONE: node_payload(
            cpu_measured=9, cpu_committed=9, cpu_limit=10)}
        short = report.BAND_GATE_MIN_SAMPLES - 1
        self._assert_withheld(
            self._readable(per_node, count=short),
            'only %d samples produced a cluster CPU fraction, fewer than '
            'the %d' % (short, report.BAND_GATE_MIN_SAMPLES))

        code, output = self._run(
            '--series', self._series(self._readable(per_node)))
        self.assertEqual(report.BAND_VIOLATION_EXIT, code)
        self.assertIn('This verdict gates (5f)', output)

    def test_the_floor_counts_samples_which_produced_a_fraction(self):
        """The second review's reproduction: usable, but with no ledger.

        Nineteen samples whose one node has a capacity row but publishes
        neither cpu_limit nor cpu_hard_max are usable CPU samples -- they
        count towards the committed_cpu block's n -- but produce no
        fraction, so the p90 rests on the one busy sample alone. Counting
        the floor from n would gate on that one sample.
        """
        ledgerless = node_payload(cpu_measured=9, cpu_committed=9,
                                  cpu_limit=None)
        ledgerless['cpu_hard_max'] = None
        records = [sample({NODE_ONE: ledgerless},
                          sampled_at=1756000000.0 + 15.0 * i,
                          capacity_degraded=False)
                   for i in range(report.BAND_GATE_MIN_SAMPLES - 1)]
        records.extend(self._readable(
            {NODE_ONE: node_payload(cpu_measured=9, cpu_committed=9,
                                    cpu_limit=10)},
            count=1, start=1756000300.0))

        record = report.summary_record(self._series(records))
        block = record['cluster']['committed_cpu']
        self.assertEqual(
            report.BAND_GATE_MIN_SAMPLES, block['n'],
            'The fixture no longer has enough usable samples to reach the '
            'floor, so it cannot tell the two counts apart.')
        self.assertEqual(1, block['n_fraction'])
        self._assert_withheld(
            records, 'only 1 sample produced a cluster CPU fraction')

    def test_a_series_without_the_capacity_degraded_flag_does_not_gate(self):
        """An absent flag is not a healthy read (Sample.capacity_degraded).

        A probe built before step 2a publishes no flag, and cannot say
        whether its capacity read was failing. The realistic route to one
        is a partial rollback of shakenfist/actions, which is reached at
        @main -- and the report elsewhere already says such samples
        predate the flag rather than counting them as clean.
        """
        records = self._readable(self._busy_pair())
        del records[7]['resources']['total']['capacity_degraded']
        self._assert_withheld(
            records, '1 sample carried no capacity_degraded flag')

    def test_a_failing_capacity_read_does_not_gate(self):
        """The review's own reproduction: nine blind samples, one busy.

        The nine are a capacity read which reported failing, and the one
        busy sample is the whole of the p90. Every sample which *could* be
        read says the cluster was full, and it still must not gate.
        """
        records = [self._blind_pair(1756000000.0 + 15.0 * i, True)
                   for i in range(9)]
        records.append(sample(self._busy_pair(), sampled_at=1756000135.0,
                              capacity_degraded=False))
        self._assert_withheld(
            records, 'the capacity read reported failing on 9 samples')

    def test_a_degraded_read_in_an_otherwise_long_series_does_not_gate(self):
        """capacity_degraded withholds on its own, above the sample floor.

        The degraded sample is readable here (every node has its row), so
        neither the floor nor the unreadable count fires: this isolates the
        flag, which is the one fact that separates a failing read from an
        empty table.
        """
        records = self._readable(self._busy_pair())
        records[5]['resources']['total']['capacity_degraded'] = True
        self._assert_withheld(
            records, 'the capacity read reported failing on 1 sample')

    def test_an_unreadable_sample_after_the_warm_up_does_not_gate(self):
        """Issue 4087's other reading: blind *after* the table was readable.

        The flag is False throughout, so this is the unreadable count alone
        -- a bundle whose capacity read went blind mid-run without saying
        so is still not a series to fail a job on.
        """
        records = self._readable(self._busy_pair())
        for record in records:
            record['resources']['total']['capacity_degraded'] = False
        records.insert(10, self._blind_pair(1756000142.0, False))
        self._assert_withheld(
            records, '1 sample was unreadable after the warm-up prefix')

    def test_a_warm_up_prefix_does_not_withhold_the_gate(self):
        """An empty table at the start of a run is healthy, and every run has one.

        Without this the guard would withhold every real cluster job, which
        is the gate switched off by a different route.
        """
        records = [self._blind_pair(1756000000.0 + 15.0 * i, False)
                   for i in range(3)]
        records.extend(self._readable(self._busy_pair(), start=1756000045.0))
        path = self._series(records)
        code, output = self._run('--series', path)
        self.assertIn('LEDGER UNREADABLE: 3 of', output)
        self.assertEqual(
            report.BAND_VIOLATION_EXIT, code,
            'A warm-up prefix withheld the gate. Every cluster job opens '
            'with one, so this would switch the gate off everywhere.')

        record = report.summary_record(path)
        self.assertTrue(record['verdict']['gates'])
        self.assertEqual([], record['verdict']['gate_withheld'])

    def test_the_gate_is_withheld_on_bands_which_could_not_gate(self):
        """Withholding is computed for every band, not only a violation.

        The ci.md window command counts how often the gate was withheld,
        and that count is only an instrument-health figure if a thin series
        reads as withheld whatever its band. Short-circuiting the reasons
        when the band is not OVERSUBSCRIBED would leave the command
        counting violations alone, without anything else changing.
        """
        for cpu_committed, band in ((1, 'OVERSIZED'), (5, 'WITHIN BAND')):
            path = self._series(self._readable(
                {NODE_ONE: node_payload(cpu_measured=cpu_committed,
                                        cpu_committed=cpu_committed,
                                        cpu_limit=10)},
                count=report.BAND_GATE_MIN_SAMPLES - 1))
            verdict = report.summary_record(path)['verdict']
            self.assertEqual(band, verdict['band'])
            self.assertFalse(verdict['gates'])
            self.assertNotEqual(
                [], verdict['gate_withheld'],
                'A %s series too short to gate reported no withhold reason, '
                'so the harvest would count it as a series the gate was '
                'allowed to judge.' % band)
