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
import datetime
import importlib.util
import io
import json
import os
import shutil
import tempfile

from shakenfist.tests import base
from shakenfist.tests import test_headroom_gate_workflow_seams as gate_seams


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


def days_ago(days):
    """A run creation time as GitHub writes it, relative to the real clock.

    The window-age check reads the wall clock, so fixtures are stamped
    relative to it rather than with a literal date which would make every
    test fail a fortnight after it was written.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    return stamp.strftime('%Y-%m-%dT%H:%M:%SZ')


def record(run_id=33000000000, record_version=4, series_present=True, samples_usable=80,
           samples_failed=0, cadence=PROBE_INTERVAL, census_state='read', stage_events=500,
           capacity_shortage_drops=3, refusal_warning=True, gate_withheld=(),
           absent_reason=None, job='Debian 13 cluster', created_days_ago=1,
           label='slim-primary cluster-ci.conf', job_conclusion='success'):
    """One harvest record, carrying only the fields this tool reads.

    A real record is 3.7 KB of distributions; everything the health tool
    looks at is here, and nothing else, so a test which passes because a
    field was renamed is not possible.
    """
    framing = {
        'harvest_version': 1,
        'run_id': run_id,
        'run_created_at': days_ago(created_days_ago),
        'job': job,
        'job_conclusion': job_conclusion,
        'label': label,
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
        for name in ('window age', 'records harvested', 'series present rate', 'usable samples per record',
                     'failed samples', 'census stage events', 'sample cadence',
                     'gate withheld'):
            self.assertIn(name, out)

    def test_one_uncharacterised_gap_in_an_otherwise_healthy_window_is_tolerated(self):
        records = healthy_window(count=31) + [record(series_present=False)]
        status, out = self.run_health(records)
        self.assertEqual(0, status, out)


class CancelledJobTestCase(HealthTestCase):
    def test_cancelled_jobs_are_set_aside_rather_than_counted_as_gaps(self):
        # One superseded merge group in the window measured on 2026-10-05
        # cancelled three cluster jobs before their probes banked anything.
        # Two such runs in a week would have failed the series floor.
        cancelled = [record(run_id=6000 + index, series_present=False,
                            job_conclusion='cancelled') for index in range(8)]
        status, out = self.run_health(healthy_window(count=24) + cancelled)
        self.assertEqual(0, status, out)
        self.assertIn('8 set aside because their job was cancelled', out)
        self.assertIn('24 of 24 records', out)

    def test_a_cancelled_job_with_a_short_series_is_not_judged_on_it(self):
        # Cancelled part way through, so the series is real but thin. The
        # sample floor is a claim about a job which ran, not one which was
        # stopped.
        cut_short = record(run_id=6100, samples_usable=6, job_conclusion='cancelled',
                           gate_withheld=['only 4 samples produced a fraction'])
        status, out = self.run_health(healthy_window(count=31) + [cut_short])
        self.assertEqual(0, status, out)
        self.assertNotIn('6100', out)

    def test_a_window_of_nothing_but_cancelled_jobs_fails(self):
        cancelled = [record(run_id=6200 + index, series_present=False,
                            job_conclusion='cancelled') for index in range(32)]
        status, out = self.run_health(cancelled)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL records harvested', out)

    def test_a_failed_job_is_still_judged(self):
        # A job whose tests failed ran its probe for the whole job, and is
        # as much a measurement as one which passed.
        failed = record(run_id=6300, samples_usable=6, job_conclusion='failure')
        status, out = self.run_health(healthy_window(count=31) + [failed])
        self.assertEqual(1, status, out)
        self.assertIn('6300', out)


class LaneTestCase(HealthTestCase):
    def test_the_gated_labels_are_the_shapes_the_gate_is_armed_on(self):
        # Arming the gate on a shape happens in MEASURED_SHAPES. A shape
        # armed there and not known here would have its records held only to
        # the ungated floor, and never asked whether its verdict was withheld.
        armed = {'%s %s' % (topology, stestr_config)
                 for topology, _tier, _kind, stestr_config in gate_seams.MEASURED_SHAPES}
        self.assertEqual(armed, set(health.GATED_LABELS))

    def test_a_short_ungated_lane_is_held_to_its_own_floor(self):
        # The Ansible modules lane: unarmed, and a job which probes for about
        # three and a half minutes, so 9 to 15 samples, and a verdict the gate
        # always withholds for being thin.
        short = [record(run_id=7000 + index, label='slim-primary ansible-modules',
                        samples_usable=9,
                        gate_withheld=['only 6 samples produced a cluster CPU fraction'])
                 for index in range(7)]
        status, out = self.run_health(healthy_window(count=24) + short)
        self.assertEqual(0, status, out)
        self.assertIn('7 on ungated lanes', out)
        self.assertIn('9 on ungated lanes (floor 5)', out)

    def test_a_probe_which_died_early_on_an_ungated_lane_still_fails(self):
        dead = record(run_id=7100, label='slim-primary ansible-modules', samples_usable=3)
        status, out = self.run_health(healthy_window(count=31) + [dead])
        self.assertEqual(1, status, out)
        self.assertIn('FAIL usable samples per record', out)
        self.assertIn('7100', out)

    def test_the_ungated_floor_is_overridable(self):
        dead = record(run_id=7100, label='slim-primary ansible-modules', samples_usable=3)
        status, out = self.run_health(healthy_window(count=31) + [dead],
                                      extra=['--min-samples-ungated', '3'])
        self.assertEqual(0, status, out)

    def test_an_unlabelled_window_cannot_pass_the_withheld_check(self):
        # The label file is what says a record came from a gated lane. If it
        # stopped being written, every record would read as ungated and skip
        # the withheld check -- which is why a window with no judged record
        # fails it.
        status, out = self.run_health(healthy_window(label=None))
        self.assertEqual(1, status, out)
        self.assertIn('FAIL gate withheld', out)
        self.assertIn('32 on ungated lanes', out)


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

    def test_the_record_floor_is_overridable(self):
        status, out = self.run_health(healthy_window(count=4), extra=['--min-records', '4'])
        self.assertEqual(0, status, out)
        self.assertIn('4 records, floor 4', out)

    def test_the_series_present_floor_is_overridable(self):
        records = healthy_window(count=20) + [record(series_present=False) for _ in range(12)]
        status, out = self.run_health(records, extra=['--series-present-floor', '0.6'])
        self.assertEqual(0, status, out)
        self.assertIn('floor 0.600', out)

    def test_a_series_with_no_summary_fails_even_inside_the_floor(self):
        # One record in 32 is well inside the floor's slack, which is for
        # cancelled jobs. A record claiming a series the report never
        # summarised would otherwise pass every per-record check, because
        # each of them reads the summary.
        unsummarised = record(run_id=7777)
        unsummarised['summary'] = None
        status, out = self.run_health(healthy_window(count=31) + [unsummarised])
        self.assertEqual(1, status, out)
        self.assertIn('FAIL series present rate', out)
        self.assertIn('7777', out)
        self.assertIn('series present but no summary', out)


class WindowAgeTestCase(HealthTestCase):
    def test_a_window_of_stale_runs_fails(self):
        # What a runs listing which served a stale page produced on
        # 2026-10-05: well-formed records, three weeks old, read as the ten
        # newest merge runs.
        status, out = self.run_health(healthy_window(created_days_ago=24))
        self.assertEqual(1, status, out)
        self.assertIn('FAIL window age', out)

    def test_one_recent_run_makes_the_window_current(self):
        # The check is on the newest run, not on every run. A window always
        # spans some time, and its oldest run is supposed to be older.
        records = healthy_window(count=31, created_days_ago=30) + [record()]
        status, out = self.run_health(records)
        self.assertEqual(0, status, out)

    def test_a_window_with_no_creation_times_fails(self):
        records = healthy_window()
        for entry in records:
            del entry['run_created_at']
        status, out = self.run_health(records)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL window age', out)
        self.assertIn('no record says when its run was created', out)

    def test_the_window_age_is_overridable(self):
        status, out = self.run_health(healthy_window(created_days_ago=24),
                                      extra=['--max-window-age-days', '30'])
        self.assertEqual(0, status, out)


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

    def test_samples_with_no_window_to_measure_them_over_fail(self):
        # Not the same as a window too short to have a cadence: there are
        # samples here, and nothing says when they were taken.
        windowless = record(run_id=888)
        windowless['summary']['series']['window_seconds'] = 0
        status, out = self.run_health(healthy_window(count=31) + [windowless])
        self.assertEqual(1, status, out)
        self.assertIn('FAIL sample cadence', out)
        self.assertIn('888', out)
        self.assertIn('no window to measure', out)

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

    def test_a_version_three_record_written_before_the_field_is_unassertable(self):
        # 5f added gate_withheld to version 3 in place rather than bumping
        # the version, so a version 3 record can lack it too.
        early = record(record_version=3)
        del early['summary']['verdict']['gate_withheld']
        status, out = self.run_health(healthy_window(count=31) + [early])
        self.assertEqual(0, status, out)
        self.assertIn('31 records judged, 1 too old to say', out)
        early_window = healthy_window(record_version=3)
        for entry in early_window:
            del entry['summary']['verdict']['gate_withheld']
        status, out = self.run_health(early_window)
        self.assertEqual(1, status, out)
        self.assertIn('FAIL gate withheld', out)

    def test_the_withheld_ceiling_is_overridable(self):
        withheld = [record(run_id=4242 + index, gate_withheld=['a capacity_degraded sample'])
                    for index in range(2)]
        status, out = self.run_health(healthy_window(count=30) + withheld[:1],
                                      extra=['--max-gate-withheld', '1'])
        self.assertEqual(0, status, out)
        status, out = self.run_health(healthy_window(count=30) + withheld,
                                      extra=['--max-gate-withheld', '1'])
        self.assertEqual(1, status, out)
        self.assertIn('2 withheld, ceiling 1', out)

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

    def test_a_line_which_parses_but_is_not_an_object_names_the_line(self):
        for value in ('[1, 2]', 'null', '"a string"'):
            path = os.path.join(self.tempdir, 'foreign.jsonl')
            with open(path, 'w') as handle:
                handle.write(json.dumps(record()) + '\n')
                handle.write(value + '\n')
            status, out = self.run_health([], path=path)
            self.assertEqual(2, status, out)
            self.assertIn('line 2 is not a JSON object', out)

    def test_a_summary_of_the_wrong_shape_names_the_line(self):
        # The containers the checks call into. Each of these used to reach a
        # check and crash it with a traceback.
        def no_series(entry):
            del entry['summary']['series']

        def listed_summary(entry):
            entry['summary'] = [entry['summary']]

        def listed_census(entry):
            entry['summary']['census'] = []

        def listed_verdict(entry):
            entry['summary']['verdict'] = ['OVERSIZED']

        for breakage, message in ((no_series, 'no series object'),
                                  (listed_summary, 'summary which is not a JSON object'),
                                  (listed_census, 'census is not a JSON object'),
                                  (listed_verdict, 'verdict is not a JSON object')):
            broken = record()
            breakage(broken)
            path = self.dataset([record(), record(), broken], name='shape.jsonl')
            status, out = self.run_health([], path=path)
            self.assertEqual(2, status, out)
            self.assertIn('line 3', out)
            self.assertIn(message, out)


class UnreadableReportTestCase(HealthTestCase):
    def test_a_missing_report_tool_exits_two(self):
        status, out = self.run_health(
            healthy_window(), extra=['--report', os.path.join(self.tempdir, 'absent.py')])
        self.assertEqual(2, status, out)
        self.assertIn('FAIL unreadable report tool', out)

    def test_a_report_tool_which_does_not_run_exits_two(self):
        stub = os.path.join(self.tempdir, 'broken_report.py')
        with open(stub, 'w') as handle:
            handle.write('import a_module_which_does_not_exist\n')
        status, out = self.run_health(healthy_window(), extra=['--report', stub])
        self.assertEqual(2, status, out)
        self.assertIn('FAIL unreadable report tool', out)

    def test_a_report_tool_without_the_floor_exits_two(self):
        stub = os.path.join(self.tempdir, 'floorless_report.py')
        with open(stub, 'w') as handle:
            handle.write('SOMETHING_ELSE = 20\n')
        status, out = self.run_health(healthy_window(), extra=['--report', stub])
        self.assertEqual(2, status, out)
        self.assertIn('BAND_GATE_MIN_SAMPLES', out)

    def test_an_explicit_floor_does_not_need_the_report_tool(self):
        # The report tool is read for one number. Given that number, a
        # report tool which cannot load is not a reason to fail.
        status, out = self.run_health(
            healthy_window(), extra=['--min-samples-usable', '20',
                                     '--report', os.path.join(self.tempdir, 'absent.py')])
        self.assertEqual(0, status, out)
