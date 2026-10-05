# Copyright 2019 Michael Still and contributors
"""The headroom probe's contract is that it always produces a readable line.

``tools/ci_headroom_probe.py`` runs unattended for the length of a
functional job, against a cluster the job is actively hammering, and its
output is the only input phase 2 has. Everything below is about the
promise the module docstring makes -- that neither a failed sample nor a
failed write ends the series -- because ``tools/ci_headroom_report.py``
is written against exactly that record shape, and a poller which dies
silently mid-run produces a short series which looks like a quiet one.

These properties are covered:

* A failed sample is a record with ``error`` and *without* ``resources``
  or ``nodes``. The report distinguishes an errored sample from an empty
  one, and would count a record carrying ``resources: null`` as a
  successful sample of nothing.
* The roster is reduced to exactly five keys. ``ci_cloud_sizing.md``
  documents that reduction as load-bearing, because a reader needs the
  role booleans to tell the four reasons a node can be missing from
  ``per_node`` apart.
* The write is guarded on the same terms as the sample. A value the json
  module cannot serialise degrades to an error line rather than to a
  dead poller.
* A write which failed is counted into the next record which lands, so a
  gap in the series says how many samples it swallowed. A series short of
  two lines and a series of a quiet cluster are otherwise the same file.
* The orchestration around those two, which is where the promises about
  cadence actually live: the loop stops at ``--max-seconds``, an overrun
  sample resumes the cadence from now rather than firing catch-up samples
  back to back, a non-positive ``--interval`` is corrected rather than
  spun on, and the output is appended to rather than replaced.

The loop is driven by a scripted clock (``FakeClock``) rather than by
real time, so every case below is deterministic and runs instantly. The
clock is installed by setting the module attribute, not by a
string-target patch: ``tools/`` is not a package and this module is
loaded under a name of this file's own choosing, so
``mock.patch('ci_headroom_probe.time')`` cannot reach it.

The tool is loaded by path, as ``test_ci_claims_headroom.py`` does,
because ``tools/`` is not an importable package. It imports
``shakenfist_client`` at module scope and that is not a test dependency
of this repository, so a stub is installed in ``sys.modules`` first --
the probe only ever calls two methods on it, both of which are replaced
here anyway.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types

import fixtures

from shakenfist.tests import base


PROBE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'tools', 'ci_headroom_probe.py')


def _load_probe():
    stub = types.ModuleType('shakenfist_client')
    stub.apiclient = types.ModuleType('shakenfist_client.apiclient')
    stub.apiclient.Client = object
    saved = {name: sys.modules.get(name)
             for name in ('shakenfist_client', 'shakenfist_client.apiclient')}
    sys.modules['shakenfist_client'] = stub
    sys.modules['shakenfist_client.apiclient'] = stub.apiclient
    try:
        spec = importlib.util.spec_from_file_location(
            'ci_headroom_probe_under_test', PROBE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in saved.items():
            if value is None:
                del sys.modules[name]
            else:
                sys.modules[name] = value


probe = _load_probe()


class FakeClient:
    """A client which answers, or fails, exactly as scripted."""

    def __init__(self, resources=None, nodes=None, raises=None):
        self.resources = resources if resources is not None else {'per_node': {}}
        self.nodes = nodes if nodes is not None else []
        self.raises = raises

    def get_cluster_resources(self):
        if self.raises:
            raise self.raises
        return self.resources

    def get_nodes(self):
        if self.raises:
            raise self.raises
        return self.nodes


class FakeClock:
    """A scripted stand-in for the ``time`` module the loop paces itself by.

    ``time()`` and ``sleep()`` are the only two functions the sampling
    loop uses, and its entire behaviour is a function of what they
    return, so scripting them is enough to exercise a 45 minute run in no
    time at all. ``sleep()`` advances the clock by exactly what it was
    asked to wait and records the request, which is what the pacing
    assertions read.

    ``max_reads`` is a safety valve rather than a detail. A loop whose
    deadline never advances does not fail a test, it hangs it -- which is
    exactly what the non-positive ``--interval`` guard prevents -- so the
    clock refuses to be read indefinitely and says why.
    """

    def __init__(self, max_reads=10000):
        self.now = 0.0
        self.sleeps = []
        self.reads = 0
        self.max_reads = max_reads

    def time(self):
        self.reads += 1
        if self.reads > self.max_reads:
            raise AssertionError(
                'the sampling loop read the clock %d times without reaching '
                '--max-seconds, so it is spinning rather than pacing itself'
                % self.reads)
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class CostlyClient(FakeClient):
    """A client whose answers take time off the fake clock.

    A sample is not free on a busy cluster, and the overrun branch of the
    loop exists for exactly the sample which takes longer than the
    interval it was allotted. ``costs`` is how many seconds each
    successive sample spends, with the last value repeating.
    """

    def __init__(self, clock, costs=(0.0,), **kwargs):
        super().__init__(**kwargs)
        self.clock = clock
        self.costs = list(costs) or [0.0]

    def _charge(self):
        cost = self.costs[0]
        if len(self.costs) > 1:
            self.costs.pop(0)
        self.clock.now += cost

    def get_cluster_resources(self):
        self._charge()
        return super().get_cluster_resources()


class FailingFile:
    def __init__(self, exc):
        self.exc = exc

    def write(self, _data):
        raise self.exc

    def flush(self):
        pass

    def fileno(self):
        raise self.exc


class TakeSampleTestCase(base.ShakenFistTestCase):
    def test_a_good_sample_carries_the_payload_verbatim(self):
        resources = {'total': {'cpu_available': 4}, 'per_node': {'a': {}}}
        client = FakeClient(resources=resources, nodes=[])
        record = probe.take_sample(client)
        self.assertEqual(resources, record['resources'])
        self.assertNotIn('error', record)
        self.assertIsInstance(record['sampled_at'], float)

    def test_a_failed_sample_omits_resources_rather_than_nulling_it(self):
        """The report tells an errored sample from a successful empty one.

        A record carrying resources: null would be read as a sample which
        saw a cluster with no nodes, which is a finding about the
        cluster. An absent key is a finding about the probe.
        """
        record = probe.take_sample(FakeClient(raises=OSError('connection refused')))
        self.assertIn('error', record)
        self.assertIn('connection refused', record['error'])
        self.assertNotIn(
            'resources', record,
            'A failed sample carried a resources key. The report reads '
            'that as a successful sample of an empty cluster.')
        self.assertNotIn('nodes', record)

    def test_take_sample_never_raises(self):
        """Whatever the client does, the loop must survive it.

        Not just network errors: an unexpected response shape reaches
        this code as a TypeError or AttributeError from inside the
        comprehension, and the probe must write that down and continue
        rather than end the run.
        """
        for failure in (OSError('down'), ValueError('bad json'),
                        KeyError('missing'), RuntimeError('boom')):
            record = probe.take_sample(FakeClient(raises=failure))
            self.assertIn('error', record)

    def test_a_roster_entry_is_reduced_to_the_five_documented_keys(self):
        """ci_cloud_sizing.md calls this reduction load-bearing for phase 2."""
        client = FakeClient(nodes=[{
            'uuid': 'u1', 'fqdn': 'node1.local', 'is_hypervisor': True,
            'is_network_node': False, 'is_database_node': True,
            'state': 'created', 'ip': '10.0.0.1', 'release': '0.8',
        }])
        record = probe.take_sample(client)
        self.assertEqual(
            {'uuid', 'fqdn', 'is_hypervisor', 'is_network_node',
             'is_database_node'},
            set(record['nodes'][0].keys()))
        self.assertTrue(record['nodes'][0]['is_database_node'])

    def test_a_roster_entry_missing_keys_reads_as_none_not_an_error(self):
        """An older node record must not cost the whole sample.

        The role booleans become None, which print_absences already
        treats as 'unexplained' rather than as 'not a hypervisor' -- the
        distinction the tri-state exists for.
        """
        record = probe.take_sample(FakeClient(nodes=[{'uuid': 'u1'}]))
        self.assertNotIn('error', record)
        self.assertIsNone(record['nodes'][0]['is_hypervisor'])
        self.assertIsNone(record['nodes'][0]['fqdn'])


class WriteRecordTestCase(base.ShakenFistTestCase):
    """Written against real files: write_record fsyncs, so a fake fd will not do."""

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.path = os.path.join(self.tempdir, 'headroom.jsonl')

    def _write(self, record):
        with open(self.path, 'a') as f:
            written = probe.write_record(f, record)
        with open(self.path) as f:
            return written, f.read()

    def test_a_good_record_is_written_as_one_json_line(self):
        written, body = self._write({'sampled_at': 1.0})
        self.assertTrue(written)
        self.assertEqual({'sampled_at': 1.0}, json.loads(body.strip()))
        self.assertTrue(body.endswith('\n'),
                        'Records must be newline terminated or the next '
                        'sample continues this one and both are lost.')

    def test_an_unserialisable_record_degrades_to_an_error_line(self):
        """A poller which dies on one bad value loses every later sample."""
        written, body = self._write({'sampled_at': 2.0, 'resources': object()})
        self.assertTrue(written)
        record = json.loads(body.strip())
        self.assertEqual(2.0, record['sampled_at'])
        self.assertIn(
            'could not be serialised', record['error'],
            'An unserialisable sample should be written down as an error '
            'record, so the gap in the series says why it is there.')
        self.assertNotIn('resources', record)

    def test_a_failing_write_returns_false_rather_than_raising(self):
        """A full /srv costs one line, not the rest of the run."""
        self.assertFalse(
            probe.write_record(FailingFile(OSError('No space left on device')),
                               {'sampled_at': 3.0}))

    def test_the_degraded_error_line_keeps_the_failure_count(self):
        """The count has to survive the record it was attached to.

        An unserialisable sample is rewritten as a minimal error record,
        and that rewrite is the only line which will reach the file for
        this sample. Dropping the running count there would lose every
        earlier failed write as well, which is the one thing the count
        exists to carry forward.
        """
        written, body = self._write(
            {'sampled_at': 4.0, 'writes_failed': 3, 'resources': object()})
        self.assertTrue(written)
        record = json.loads(body.strip())
        self.assertIn('could not be serialised', record['error'])
        self.assertEqual(
            3, record['writes_failed'],
            'The degraded line dropped the count, so three lost samples '
            'became invisible at the one point they could have been '
            'reported.')


class MainLoopTestCase(base.ShakenFistTestCase):
    """The orchestration, which is where the cadence promises live.

    ``take_sample()`` and ``write_record()`` each keep their own
    promise in isolation, and neither of them paces anything. The loop
    is what turns them into a series with a cadence, a cap and an
    accounted-for gap, and it is driven entirely by the clock and the
    arguments -- so no cluster is involved in any of this.
    """

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tempdir, True)
        self.path = os.path.join(self.tempdir, 'headroom.jsonl')

    def _records(self):
        with open(self.path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def _main(self, clock, client, *argv):
        """Run main() on a scripted clock: (code, records, stdout).

        The clock and the client are installed by setting module
        attributes rather than by patching an import path, because this
        module was loaded by path under a name of the test file's own
        choosing and no import path names it.
        """
        self.useFixture(fixtures.MockPatchObject(probe, 'time', clock))
        self.useFixture(fixtures.MockPatchObject(
            probe.apiclient, 'Client', lambda **kwargs: client))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = probe.main([self.path] + list(argv))
        return code, self._records(), out.getvalue()

    def test_the_loop_stops_once_max_seconds_has_elapsed(self):
        """The cap is the whole reason --max-seconds has no default.

        A cancelled CI job never runs the step which would stop this
        poller and nothing else tears the cluster down, so a loop which
        overran its cap would keep polling a leaked VM until the reaper
        found it.
        """
        clock = FakeClock()
        code, records, _ = self._main(
            clock, CostlyClient(clock), '--interval', '15',
            '--max-seconds', '45')

        self.assertEqual(0, code)
        self.assertEqual(
            [0.0, 15.0, 30.0], [r['sampled_at'] for r in records],
            'A 45 second run at a 15 second interval samples at 0, 15 and '
            '30. A fourth sample means the cap is checked after the sample '
            'rather than before it.')
        self.assertEqual([15.0, 15.0, 15.0], clock.sleeps)

    def test_a_sample_which_overran_its_slot_fires_no_catch_up_samples(self):
        """The deadline is reset to now, not left behind to be caught up.

        One sample here takes 60 seconds against a 15 second interval, so
        the loop finishes it three slots late. Resuming the cadence from
        now costs the series those three samples. Leaving the deadline
        where it was would instead fire them back to back the moment the
        slow sample returned -- hammering the API hardest exactly when the
        cluster was already too busy to answer it on time, and banking
        four samples which share a timestamp and describe one instant.
        """
        clock = FakeClock()
        code, records, _ = self._main(
            clock, CostlyClient(clock, costs=[60.0, 0.0]), '--interval', '15',
            '--max-seconds', '120')

        self.assertEqual(0, code)
        self.assertEqual(
            [0.0, 60.0, 75.0, 90.0, 105.0], [r['sampled_at'] for r in records],
            'The overrun was not absorbed. Repeated or duplicated '
            'timestamps here are catch-up samples, which is the burst the '
            'reset exists to prevent.')
        self.assertEqual(
            len(records), len(set(r['sampled_at'] for r in records)),
            'Two samples claim the same instant, so the series describes '
            'that instant twice and the percentiles read from it are '
            'weighted towards whatever the cluster was doing when it was '
            'too busy to be sampled.')
        self.assertEqual([15.0, 15.0, 15.0, 15.0], clock.sleeps)

    def test_a_non_positive_interval_is_corrected_rather_than_spun_on(self):
        """An interval of zero would otherwise poll as fast as the API answers.

        Corrected in code rather than refused by argparse because nothing
        this instrument does may fail the build it is measuring: a
        mistyped workflow argument has to degrade to the default, not
        take the job with it.
        """
        for interval in ('0', '-15'):
            # A file of its own per case: the output is opened for append,
            # so a shared one would carry the first case's samples into
            # the second's assertions.
            self.path = os.path.join(
                self.tempdir, 'interval%s.jsonl' % interval)
            clock = FakeClock()
            code, records, printed = self._main(
                clock, CostlyClient(clock), '--interval', interval,
                '--max-seconds', '45')

            self.assertEqual(0, code)
            self.assertIn('--interval must be positive', printed)
            self.assertEqual(
                [0.0, 15.0, 30.0], [r['sampled_at'] for r in records],
                'An interval of %s did not fall back to the 15 second '
                'default.' % interval)
            self.assertEqual([15.0, 15.0, 15.0], clock.sleeps)

    def test_a_healthy_run_records_a_zero_rather_than_omitting_the_count(self):
        """Zero and absent are different findings about the series.

        A reader of a record written by a probe which predates the
        counter cannot tell a run which lost no lines from one which was
        not counting, so the healthy case writes the zero explicitly and
        absence is left to mean "not counted".
        """
        clock = FakeClock()
        _, records, _ = self._main(
            clock, CostlyClient(clock), '--interval', '15',
            '--max-seconds', '45')

        self.assertEqual(3, len(records))
        for record in records:
            self.assertIn(
                'writes_failed', record,
                'A healthy record omitted the count, so absence can no '
                'longer be read as "this probe did not count".')
            self.assertEqual(0, record['writes_failed'])

    def test_a_write_which_failed_is_counted_into_the_next_record(self):
        """A gap in the series has to say how wide it is.

        A write which fails writes nothing, so the failure can only be
        recorded by the next record which lands. Without the count a
        series short of two samples and a series of a cluster nobody was
        using are the same file, and nothing computes cadence.
        """
        clock = FakeClock()
        real_write = probe.write_record
        attempted = []

        def flaky(f, record):
            attempted.append(record)
            if len(attempted) <= 2:
                return False
            return real_write(f, record)

        self.useFixture(fixtures.MockPatchObject(probe, 'write_record', flaky))
        _, records, _ = self._main(
            clock, CostlyClient(clock), '--interval', '15',
            '--max-seconds', '45')

        self.assertEqual(
            [0, 1, 2], [r['writes_failed'] for r in attempted],
            'The running count did not advance with each failed write, so '
            'the record which lands after a gap understates it.')
        self.assertEqual(
            [2], [r['writes_failed'] for r in records],
            'The two lost lines are not accounted for in the one record '
            'which reached the file.')

    def test_an_existing_series_is_appended_to_rather_than_replaced(self):
        """The launcher may start a probe beside a series already on disk.

        Opening the output for writing would make a restart -- or a second
        probe started by a re-run of the step -- destroy the samples
        already banked for that job, which is the one outcome the
        instrument cannot recover from.
        """
        with open(self.path, 'w') as f:
            f.write('{"sampled_at": -1.0}\n')

        clock = FakeClock()
        _, records, _ = self._main(
            clock, CostlyClient(clock), '--interval', '15',
            '--max-seconds', '15')

        self.assertEqual(
            [-1.0, 0.0], [r['sampled_at'] for r in records],
            'The pre-existing series was truncated rather than appended '
            'to.')

    def test_max_seconds_has_no_default_and_is_required(self):
        """The cap is required because a cancelled job cannot be relied on.

        Giving it a default would make the one argument which stops a
        leaked poller the easiest one to forget.
        """
        with open(os.devnull, 'w') as devnull:
            with contextlib.redirect_stderr(devnull):
                self.assertRaises(SystemExit, probe.main, [self.path])
