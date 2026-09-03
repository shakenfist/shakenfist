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
import shutil
import tempfile

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


def sample(per_node, nodes=None, sampled_at=1756000000.0):
    total = {
        'cpu_available': sum(n['cpu_available'] for n in per_node.values()),
        'ram_available': sum(n['ram_available'] for n in per_node.values()),
    }
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


class HeadroomReportTestCase(base.ShakenFistTestCase):
    """Every test drives main() and reads the printed report."""

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(self._remove_tempdir)

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

    def _run(self, *argv):
        """Run the tool, returning (exit code, stdout)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = report.main(list(argv))
        return code, out.getvalue()


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
        self.assertEqual(0, code)
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
        path = self._series([
            sample({NODE_ONE: node_payload(
                cpu_measured=9, cpu_committed=9, cpu_limit=10)}),
        ])
        code, output = self._run('--series', path)
        self.assertEqual(0, code)
        self.assertIn('OVERSUBSCRIBED', output)
        self.assertIn(
            'PROVISIONAL', output,
            'The band bounds were printed without saying they are '
            'provisional. Phase 0 set 0.35 and 0.70 with no distribution to '
            'check them against and phase 2 replaces them; a verdict which '
            'does not say so invites phase 2 to trust them.')

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
        self.assertEqual(0, code)
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
