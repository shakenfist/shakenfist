# Copyright 2019 Michael Still and contributors
"""A check which cannot fail is the problem this tool was written to fix.

``tools/ci_headroom_health.py`` is the only consumer of the CI headroom
records, and the only part of that instrument which is allowed to return
non-zero. Everything else in the probe path swallows its own failures on
purpose, so each mode below is invisible until this tool names it:

* a window whose records stopped carrying a series,
* a record whose probe authenticated against nothing, or died early,
* a census whose log filter stopped matching the scheduler -- which the
  stored refusal flag reads as a cluster that refused nothing on records
  written before that flag learned the difference, so the warning is
  re-derived here and the tests pin that it is,
* a verdict the band gate withheld,
* a probe interval which changed, or samples which went missing,
* a dataset which could not be read at all, which must not read as healthy.

The gate-withheld coverage is weighted towards the record version boundary.
The field is absent below the version which introduced it, and absence is
not an empty list: a window of older records must be reported as unable to
answer rather than passed, because the committed datasets are exactly that
and a consumer which passed them would pass a harvest pointed at the wrong
window too.

The tool is loaded by path, as the other headroom tools' tests load theirs:
``tools/`` is not a package.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile

from shakenfist.tests import base


TOOLS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'tools')
HEALTH_PATH = os.path.join(TOOLS, 'ci_headroom_health.py')
REPORT_PATH = os.path.join(TOOLS, 'ci_headroom_report.py')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


health = _load('ci_headroom_health_under_test', HEALTH_PATH)
report = _load('ci_headroom_report_for_health', REPORT_PATH)

PROBE_INTERVAL = 15.0


def record(run_id=33000000000, record_version=4, series_present=True, samples_usable=80,
           samples_failed=0, cadence=PROBE_INTERVAL, census_state='read', stage_events=500,
           capacity_shortage_drops=3, refusal_warning=True, gate_withheld=(),
           absent_reason=None, job='Debian 12 cluster'):
    """One harvest record, carrying only the fields this tool reads.

    A real record is 3.7 KB of distributions; everything the health tool
    looks at is here, and nothing else, so a test which passes because a
    field was renamed is not possible.
    """
    framing = {
        'harvest_version': 1,
        'run_id': run_id,
        'job': job,
        'artifact_name': 'bundle-shakenfist-full-debian-13-slim-primary',
        'series_present': series_present,
        'absent_reason': absent_reason,
        'summary': None,
    }
    if not series_present:
        framing['absent_reason'] = absent_reason or (
            'the bundle carries no traces/headroom.jsonl, so the probe never ran')
        return framing

    verdict = {
        'p90_cpu_fraction': 0.33,
        'band': 'OVERSIZED',
        'refusal_warning': refusal_warning,
    }
    # Absent below the version which introduced it, which is the shape the
    # committed datasets have and the one the tool has to tell from an empty
    # list.
    if record_version >= health.GATE_WITHHELD_RECORD_VERSION:
        verdict['gates'] = not gate_withheld
        verdict['gate_withheld'] = list(gate_withheld)
    framing['summary'] = {
        'record_version': record_version,
        'series': {
            'samples_usable': samples_usable,
            'samples_failed': samples_failed,
            'failed_sample_reasons': {'http 503': samples_failed} if samples_failed else {},
            'window_seconds': (samples_usable - 1) * cadence if samples_usable > 1 else 0,
        },
        'census': {
            'state': census_state,
            'stage_events': stage_events,
            'capacity_shortage_drops': capacity_shortage_drops,
        },
        'verdict': verdict,
    }
    return framing


def healthy_window(count=32, **kwargs):
    return [record(run_id=33000000000 + index, **kwargs) for index in range(count)]


class HealthTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.mkdtemp(prefix='ci-headroom-health-tests-')
        self.addCleanup(shutil.rmtree, self.tempdir)

    def dataset(self, records, name='records.jsonl'):
        path = os.path.join(self.tempdir, name)
        with open(path, 'w') as handle:
            for entry in records:
                handle.write(json.dumps(entry, separators=(',', ':')) + '\n')
        return path

    def run_health(self, records, extra=None, path=None):
        """Returns (exit status, stdout).

        Every caller asserts the status as a literal rather than through the
        tool's own exit constants. The number is the entire interface to the
        workflow step which runs this -- zero is a green job -- so a test
        which read the constant would pass happily with the unhealthy status
        set to zero, which is exactly the regression worth catching.
        """
        argv = list(extra or []) + [path or self.dataset(records)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = health.main(argv)
        return status, out.getvalue()


class HealthyWindowTestCase(HealthTestCase):
    def test_a_healthy_window_passes_every_check(self):
        status, out = self.run_health(healthy_window())
        self.assertEqual(0, status, out)
        self.assertNotIn('FAIL', out)

    def test_every_check_is_printed_even_when_it_passed(self):
        # A check with nothing to say is indistinguishable from a check
        # somebody deleted, and this output is what a human reads when the
        # job finally does fail.
        status, out = self.run_health(healthy_window())
        self.assertEqual(0, status, out)
        for name in ('records harvested', 'series present rate', 'usable samples per record',
                     'failed samples', 'census stage events', 'sample cadence',
                     'gate withheld'):
            self.assertIn(name, out)

    def test_a_cancelled_job_in_an_otherwise_healthy_window_is_tolerated(self):
        # The one gap a working instrument still produces. One record in the
        # whole committed baseline is a job cancelled mid-run.
        records = healthy_window(count=31) + [record(series_present=False)]
        status, out = self.run_health(records)
        self.assertEqual(0, status, out)


class SeriesPresenceTestCase(HealthTestCase):
    def test_a_window_whose_probe_stopped_running_fails(self):
        records = healthy_window(count=20) + [record(series_present=False) for _ in range(12)]
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL series present rate', out)
        self.assertIn('0.625', out)

    def test_the_series_present_failure_names_the_recorded_reason(self):
        records = healthy_window(count=1) + [
            record(series_present=False, absent_reason='the artifact has expired')
            for _ in range(20)]
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('the artifact has expired', out)

    def test_a_systematically_degraded_window_names_some_records_and_counts_the_rest(self):
        records = [record(series_present=False) for _ in range(40)]
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('and %d more' % (40 - health.MAX_NAMED), out)

    def test_a_window_with_too_few_records_fails(self):
        status, out = self.run_health(healthy_window(count=4))
        self.assertEqual(1, status, out)
        self.assertIn('FAIL records harvested', out)


class SampleTestCase(HealthTestCase):
    def test_a_probe_which_authenticated_against_nothing_fails(self):
        records = healthy_window(count=31) + [
            record(run_id=1234, samples_usable=0, samples_failed=97)]
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL usable samples per record', out)
        self.assertIn('FAIL failed samples', out)
        self.assertIn('1234', out)

    def test_a_single_failed_sample_fails_the_job(self):
        records = healthy_window(count=31) + [record(samples_failed=1)]
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL failed samples', out)
        self.assertIn('http 503', out)

    def test_a_series_too_thin_to_judge_fails(self):
        records = healthy_window(count=31) + [record(run_id=4321, samples_usable=11)]
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL usable samples per record', out)
        self.assertIn('11 usable samples', out)

    def test_the_sample_floor_is_the_band_gates_own_floor(self):
        floor = report.BAND_GATE_MIN_SAMPLES
        status, out = self.run_health(healthy_window(samples_usable=floor))
        self.assertEqual(0, status, out)
        status, out = self.run_health(healthy_window(samples_usable=floor - 1))
        self.assertEqual(1, status, out)
        self.assertIn('floor %d' % floor, out)

    def test_the_sample_floor_is_read_from_the_report_rather_than_copied(self):
        # Pinned by moving the floor in a stand-in report tool: a copy of the
        # number in the health tool would ignore this and pass.
        stub = os.path.join(self.tempdir, 'stub_report.py')
        with open(stub, 'w') as handle:
            handle.write('BAND_GATE_MIN_SAMPLES = 200\n')
        status, out = self.run_health(healthy_window(samples_usable=80),
                                      extra=['--report', stub])
        self.assertEqual(1, status, out)
        self.assertIn('floor 200', out)


class CensusTestCase(HealthTestCase):
    def test_a_census_which_matched_no_scheduler_event_fails(self):
        records = healthy_window(count=31) + [
            record(run_id=5678, stage_events=0, capacity_shortage_drops=0)]
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL census stage events', out)
        self.assertIn('5678', out)

    def test_an_uncollected_census_fails(self):
        records = healthy_window(count=31) + [
            record(census_state='not_collected', stage_events=0, capacity_shortage_drops=0)]
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL census stage events', out)

    def test_the_stored_refusal_flag_is_not_trusted_over_the_census(self):
        # An older record stores False for a census which matched nothing at
        # all -- the same value it stores for a cluster which refused nothing
        # -- so a consumer reading the flag reads a blind instrument as a
        # clean cluster. The warning is re-derived from fields every version
        # carries.
        blind = record(record_version=3, stage_events=0, capacity_shortage_drops=0,
                       refusal_warning=False)
        self.assertIsNone(health.derived_refusal_warning(blind['summary']['census']))
        status, out = self.run_health(healthy_window(count=31) + [blind])
        self.assertEqual(1, status, out)
        self.assertIn('FAIL census stage events', out)

    def test_a_census_which_saw_the_scheduler_refuse_nothing_is_not_a_failure(self):
        # The distinction the derived warning exists for: a cluster which
        # refused nothing is healthy, an instrument which did not look is not.
        seen = record(stage_events=500, capacity_shortage_drops=0)
        self.assertIs(False, health.derived_refusal_warning(seen['summary']['census']))
        status, out = self.run_health(healthy_window(count=31) + [seen])
        self.assertEqual(0, status, out)


class CadenceTestCase(HealthTestCase):
    def test_a_changed_probe_interval_fails(self):
        status, out = self.run_health(healthy_window(cadence=30.0))
        self.assertEqual(1, status, out)
        self.assertIn('FAIL sample cadence', out)
        self.assertIn('30.000s', out)

    def test_missing_samples_stretch_the_cadence_and_fail(self):
        # Half the samples lost over the same window reads as double the
        # interval, because the window is still bounded by the first and last
        # sample the probe managed to write.
        records = healthy_window(count=31) + [record(run_id=999, cadence=17.5)]
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL sample cadence', out)
        self.assertIn('999', out)

    def test_the_tolerance_is_overridable(self):
        status, out = self.run_health(healthy_window(cadence=17.5),
                                      extra=['--cadence-tolerance', '3'])
        self.assertEqual(0, status, out)

    def test_a_window_too_short_to_have_a_cadence_is_not_reported_twice(self):
        # One sample has no spacing to measure. The usable-samples check has
        # already failed on it and saying so again adds nothing.
        status, out = self.run_health(healthy_window(count=32, samples_usable=1))
        self.assertEqual(1, status, out)
        self.assertIn('FAIL usable samples per record', out)
        self.assertNotIn('FAIL sample cadence', out)


class GateWithheldTestCase(HealthTestCase):
    def test_a_withheld_verdict_fails_and_names_the_reason(self):
        withheld = record(run_id=4242, gate_withheld=['only 4 samples produced a fraction'])
        status, out = self.run_health(healthy_window(count=31) + [withheld])
        self.assertEqual(1, status, out)
        self.assertIn('FAIL gate withheld', out)
        self.assertIn('only 4 samples produced a fraction', out)
        self.assertIn('4242', out)

    def test_records_older_than_the_field_are_unassertable_not_passed(self):
        # What the committed datasets are. A window of them says nothing
        # about the gate, and reading absence as an empty list would report a
        # harvest pointed at the wrong window as healthy.
        status, out = self.run_health(healthy_window(record_version=2))
        self.assertEqual(1, status, out)
        self.assertIn('FAIL gate withheld', out)
        self.assertIn('32 too old to say', out)

    def test_an_older_record_beside_current_ones_is_counted_not_fatal(self):
        records = healthy_window(count=31) + [record(record_version=2)]
        status, out = self.run_health(records)
        self.assertEqual(0, status, out)
        self.assertIn('31 records judged, 1 too old to say', out)


class UnreadableDatasetTestCase(HealthTestCase):
    def test_a_missing_dataset_does_not_read_as_healthy(self):
        status, out = self.run_health(
            [], path=os.path.join(self.tempdir, 'never-written.jsonl'))
        self.assertEqual(2, status, out)
        self.assertIn('FAIL unreadable dataset', out)

    def test_an_empty_dataset_does_not_read_as_healthy(self):
        path = os.path.join(self.tempdir, 'empty.jsonl')
        open(path, 'w').close()
        status, out = self.run_health([], path=path)
        self.assertEqual(2, status, out)
        self.assertIn('holds no records', out)

    def test_an_unparseable_line_names_the_line(self):
        path = os.path.join(self.tempdir, 'broken.jsonl')
        with open(path, 'w') as handle:
            handle.write(json.dumps(record()) + '\n')
            handle.write('{truncated mid-write\n')
        status, out = self.run_health([], path=path)
        self.assertEqual(2, status, out)
        self.assertIn('line 2', out)
