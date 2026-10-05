#!/usr/bin/env python3
# Copyright 2019 Michael Still and contributors
"""Assert that the CI headroom instrument is still alive, and fail if it is not.

Every failure in the probe path is swallowed on purpose. The probe samples
``/admin/resources`` from inside the functional cluster job it measures, so an
instrument which could fail that job would change the failure surface it
exists to observe: ``ci_headroom_launch.sh`` in ``shakenfist/actions`` ends
``exit 0`` unconditionally, its remote heredoc is wrapped ``|| true``, and
``ci_headroom_report.py`` prints an unreadable series rather than raising.

The price is that **a dead probe and a healthy cluster produce the same green
job**. A probe which never started, one which authenticated against nothing,
a collect which could not reach the primary, or a verdict withheld because
the series was too thin are all recorded honestly in the fields the harvest
writes -- and until this tool existed, nothing read them. Three things trust
that output: the committed sizing baseline, the topology reshape fitted to
it, and the merge-blocking band gate.

So this is the consumer. It reads a harvest written by
``tools/ci_headroom_harvest.py`` and returns non-zero when the records say
the instrument is not working, naming the records that say so.

**It is allowed to fail loudly only because it runs on a schedule.** The
exit-zero discipline in the probe path is about an instrument not failing the
job it measures; this tool runs in no pull request's path and measures
nothing, so there is nothing for its failure to perturb, and a check which
cannot fail is the problem it was written to fix. Do not add a
``pull_request`` or ``merge_group`` trigger to the workflow which calls it:
the moment a sick instrument can redden somebody's merge, the instrument is
back to being part of what it is trying to measure, and the honest
exit-zero probe path has been undone through the back door.

Every threshold it asserts against is a module constant below, with the
measurement behind it, and every one has a command line flag. They are
numbers somebody will need to change -- a probe interval change moves the
cadence, a matrix change moves the record count -- and a number that can
only be changed by editing the tool gets edited by whoever is in a hurry.

Example:

    tools/ci_headroom_harvest.py --limit 10 -o /tmp/headroom.jsonl
    tools/ci_headroom_health.py /tmp/headroom.jsonl

Exit status is 0 when every check passed, 1 when an invariant was violated,
and 2 when the dataset could not be read at all.
"""

import argparse
import collections
import importlib.util
import json
import os
import sys


# The lowest fraction of harvested records which may carry a series before
# this tool calls the instrument broken.
#
# This is an absolute floor and not a comparison against the committed
# baseline, which is the honest shape for the data that exists. The committed
# ``records.jsonl`` carries a series on 204 of 217 records (0.940), but 12 of
# those 13 gaps are runs which predate the probe landing in
# ``shakenfist/actions`` at all, so its rate measures the instrument's
# arrival rather than its health; and the confirmation window beside it is
# 32 records over a day and a half, which its README says plainly is too
# small to recompute a distribution from. Neither is a trend to have not
# dropped from.
#
# What both do establish is that a working instrument banks a series on
# every bundle it writes: once the probe was running, 32 of 32. The slack
# below that is for the one gap a healthy instrument still produces -- a job
# cancelled mid-run, which is one record in the whole baseline -- which at
# the harvest limit the scheduled job uses is up to four records.
SERIES_PRESENT_FLOOR = 0.90

# The fewest records a harvest may yield before the window itself is
# suspect. The modes which produce no record at all -- a run that banked no
# bundle, a collect which could not reach the primary -- leave nothing for a
# per-record check to find, so they are only visible as a count.
#
# Scaled to the ten newest merge runs the scheduled job harvests. Four
# instrumented jobs per run is 40 records; the baseline window yielded 217
# over 66 runs, or 3.3 per run, the shortfall being runs whose functional
# jobs were skipped because nothing relevant changed. This floor tolerates
# four of ten runs contributing nothing and still fails a window where the
# bundles stopped arriving.
MIN_RECORDS = 20

# No sample may fail. A failed sample is an HTTP or parse error against a
# cluster which was up enough to run the suite, and the banked datasets have
# zero of them across all 236 summarised records, so this is an observed
# invariant rather than a tolerance.
MAX_SAMPLES_FAILED = 0

# The probe's poll interval, and how far a window's mean sample spacing may
# sit from it.
#
# The tolerance looks generous and is not: mean cadence over the 236
# summarised records in the banked datasets is 15.000 seconds to within ten
# microseconds, because the probe sleeps to a schedule rather than for an
# interval. Only two things move it. A changed interval moves it in a step,
# and lost samples move it up, because the window is still bounded by the
# first and last sample the probe managed to write. A second of slack keeps
# this from firing on a rounding change while catching either.
PROBE_INTERVAL_SECONDS = 15.0
CADENCE_TOLERANCE_SECONDS = 1.0

# The report record version which first published whether the band gate was
# allowed to judge the series. Below it ``verdict.gate_withheld`` is absent,
# which is not the same claim as an empty list, so those records are counted
# as unassertable rather than passed.
GATE_WITHHELD_RECORD_VERSION = 3

EXIT_OK = 0
EXIT_UNHEALTHY = 1
EXIT_UNREADABLE = 2

# How many offending records a failing check names before it says how many
# more there were. A systematically degraded instrument fails every record
# in the window, and forty copies of the same line buries the check which
# only failed once.
MAX_NAMED = 5


class HealthError(Exception):
    pass


Check = collections.namedtuple('Check', ['name', 'ok', 'detail', 'offenders'])


def load_report(path):
    """Load tools/ci_headroom_report.py by path.

    The tools/ directory is not a package, so there is no import path to
    reach it by. The harvest and the report's own tests load it the same way.
    Only one constant is read from it, and reading it rather than copying it
    is the point: the sample floor this tool asserts is the band gate's own
    floor, and two copies of that number would drift.
    """
    spec = importlib.util.spec_from_file_location('ci_headroom_report', path)
    if spec is None or spec.loader is None:
        raise HealthError('could not load the report tool from %s' % path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def default_report_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'ci_headroom_report.py')


def read_records(path):
    """One compact JSON object per line, as the harvest writes it."""
    records = []
    try:
        with open(path) as handle:
            for number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError as e:
                    raise HealthError('%s line %d is not JSON: %s' % (path, number, e))
    except OSError as e:
        raise HealthError('could not read %s: %s' % (path, e))
    if not records:
        raise HealthError(
            '%s holds no records. The harvest refuses to write an empty '
            'dataset, so an empty file here means it never ran, or ran '
            'against a different path.' % path)
    return records


def record_name(record):
    """Enough to find the job again in the Actions UI."""
    return '%s %s' % (record.get('run_id'), record.get('artifact_name') or record.get('job'))


def summarised(records):
    """The records which carry a report summary, paired with it.

    A record without one is not a gap in the dataset -- the harvest keeps it,
    with the reason it has no series, precisely so that what could not be
    measured is counted. It is the series-present check's business, not the
    per-record checks'.
    """
    return [(record, record['summary']) for record in records if record.get('summary')]


def mean_cadence(series):
    """Mean seconds between the usable samples of one window, or None.

    From the window the report bounds with the first and last sample it could
    read, so a probe which died half way through a job reports the cadence of
    the half it managed rather than an inflated average over the whole job.
    """
    samples = series.get('samples_usable') or 0
    seconds = series.get('window_seconds')
    if samples < 2 or not seconds:
        return None
    return seconds / (samples - 1)


def derived_refusal_warning(census):
    """Whether this census observed the cluster refusing on capacity.

    Derived rather than read out of ``verdict.refusal_warning``, which does
    not mean the same thing in every record version: before version 4 it read
    False both for a census which saw the scheduler refuse nothing and for a
    census whose filter matched no scheduler event at all. The second is the
    instrument failing to look, and pooling the two counts a blind instrument
    as a clean cluster. The fields below are in every version.
    """
    observed = census.get('state') == 'read' and census.get('stage_events')
    if not observed:
        return None
    return bool(census.get('capacity_shortage_drops'))


def check_record_count(records, args):
    ok = len(records) >= args.min_records
    return Check(
        'records harvested',
        ok,
        '%d records, floor %d' % (len(records), args.min_records),
        [])


def check_series_present(records, args):
    present = [record for record in records if record.get('series_present')]
    rate = len(present) / len(records)
    offenders = [
        '%s: %s' % (record_name(record), record.get('absent_reason') or 'no reason recorded')
        for record in records if not record.get('series_present')]
    return Check(
        'series present rate',
        rate >= args.series_present_floor,
        '%d of %d records carry a series (%.3f), floor %.3f'
        % (len(present), len(records), rate, args.series_present_floor),
        offenders)


def check_samples_usable(records, args):
    """Every summarised record carries enough samples to say anything.

    The floor is the band gate's own sample floor, which is where the number
    comes from. Note that clearing it is necessary and not sufficient for the
    gate: the gate counts the samples which produced a CPU fraction, and the
    first two or three minutes of a cluster's life produce usable samples
    whose capacity table is still empty. The gate says so itself, which is
    what the withheld check below reads.
    """
    offenders = []
    worst = None
    for record, summary in summarised(records):
        usable = summary['series'].get('samples_usable') or 0
        worst = usable if worst is None else min(worst, usable)
        if usable < args.min_samples_usable:
            offenders.append('%s: %d usable samples' % (record_name(record), usable))
    return Check(
        'usable samples per record',
        not offenders,
        'fewest %s, floor %d' % ('n/a' if worst is None else worst, args.min_samples_usable),
        offenders)


def check_samples_failed(records, args):
    offenders = []
    total = 0
    for record, summary in summarised(records):
        failed = summary['series'].get('samples_failed') or 0
        total += failed
        if failed > args.max_samples_failed:
            offenders.append(
                '%s: %d failed samples %s'
                % (record_name(record), failed,
                   json.dumps(summary['series'].get('failed_sample_reasons') or {})))
    return Check(
        'failed samples',
        not offenders,
        '%d failed samples over %d summarised records, ceiling %d per record'
        % (total, len(summarised(records)), args.max_samples_failed),
        offenders)


def check_stage_events(records, args):
    """The census matched the scheduler's stage events at all.

    Zero of them is the mode worth having a check for: the census is a log
    query, the query lives in another repository, and a filter which stops
    matching reports a cluster which refused nothing rather than an
    instrument which did not look.
    """
    offenders = []
    observed = 0
    for record, summary in summarised(records):
        census = summary.get('census') or {}
        if derived_refusal_warning(census) is None:
            offenders.append(
                '%s: census state %r, %s stage events'
                % (record_name(record), census.get('state'), census.get('stage_events')))
        else:
            observed += 1
    return Check(
        'census stage events',
        not offenders,
        '%d of %d summarised records observed the scheduler'
        % (observed, len(summarised(records))),
        offenders)


def check_cadence(records, args):
    offenders = []
    cadences = []
    for record, summary in summarised(records):
        cadence = mean_cadence(summary['series'])
        if cadence is None:
            # Fewer than two samples, which the usable-samples check above
            # has already failed on. Reporting it twice says nothing new.
            continue
        cadences.append(cadence)
        if abs(cadence - args.probe_interval) > args.cadence_tolerance:
            offenders.append('%s: %.3fs mean cadence' % (record_name(record), cadence))
    detail = 'no window had two samples to measure'
    if cadences:
        detail = ('%.3fs to %.3fs mean cadence, expected %.1fs +/- %.1fs'
                  % (min(cadences), max(cadences), args.probe_interval, args.cadence_tolerance))
    return Check('sample cadence', not offenders, detail, offenders)


def check_gate_withheld(records, args):
    """No verdict was withheld, and some record was able to say so.

    Read with ``.get()`` against the record version, because the field is
    absent below the version which introduced it and absence is not an empty
    list. A window in which no record can answer the question is itself a
    failure: the scheduled harvest reads the ten newest merge runs, so
    records which predate the gate mean the harvest is not reading what it
    thinks it is.
    """
    offenders = []
    asserted = 0
    unassertable = 0
    for record, summary in summarised(records):
        verdict = summary.get('verdict') or {}
        version = summary.get('record_version') or 0
        withheld = verdict.get('gate_withheld')
        if version < GATE_WITHHELD_RECORD_VERSION or withheld is None:
            unassertable += 1
            continue
        asserted += 1
        if withheld:
            offenders.append('%s: %s' % (record_name(record), '; '.join(withheld)))
    detail = '%d records judged, %d too old to say' % (asserted, unassertable)
    if not asserted and summarised(records):
        return Check(
            'gate withheld', False,
            detail + '. Not one record in this window publishes whether the '
            'gate was allowed to judge it', offenders)
    return Check('gate withheld', not offenders, detail, offenders)


CHECKS = (
    check_record_count,
    check_series_present,
    check_samples_usable,
    check_samples_failed,
    check_stage_events,
    check_cadence,
    check_gate_withheld,
)


def evaluate(records, args):
    return [check(records, args) for check in CHECKS]


def report_checks(checks, handle):
    """Print every check, passed or failed, and return whether all passed.

    Passes are printed too. A check which has nothing to say about a mode is
    indistinguishable from a check which was removed, and this output is read
    by somebody looking at a failed scheduled job wondering what else was
    still being watched.
    """
    failed = [check for check in checks if not check.ok]
    for check in checks:
        print('%-4s %-26s %s' % ('FAIL' if not check.ok else 'ok', check.name, check.detail),
              file=handle)
        if not check.ok:
            for offender in check.offenders[:MAX_NAMED]:
                print('       %s' % offender, file=handle)
            if len(check.offenders) > MAX_NAMED:
                print('       ... and %d more' % (len(check.offenders) - MAX_NAMED), file=handle)
    if failed:
        print('', file=handle)
        print('The CI headroom instrument is not healthy: %d of %d checks failed (%s). '
              'These records are what the sizing baseline, the topology reshape and the '
              'band gate are computed from, so a degraded instrument silently weakens all '
              'three.' % (len(failed), len(checks), ', '.join(check.name for check in failed)),
              file=handle)
    return not failed


def build_parser(min_samples_usable):
    parser = argparse.ArgumentParser(
        description=('Assert that a CI headroom harvest shows a working instrument, and '
                     'fail if it does not.'))
    parser.add_argument(
        'dataset',
        help='A harvest written by tools/ci_headroom_harvest.py, one JSON object per line.')
    parser.add_argument(
        '--min-records', type=int, default=MIN_RECORDS,
        help='Fewest records the harvest may hold (default: %(default)s).')
    parser.add_argument(
        '--series-present-floor', type=float, default=SERIES_PRESENT_FLOOR,
        help=('Lowest fraction of records which may carry a series '
              '(default: %(default)s).'))
    parser.add_argument(
        '--min-samples-usable', type=int, default=None,
        help=('Fewest usable samples a summarised record may carry. Defaults to the band '
              "gate's own sample floor, read from the report tool rather than copied "
              '(currently %d).' % min_samples_usable))
    parser.add_argument(
        '--max-samples-failed', type=int, default=MAX_SAMPLES_FAILED,
        help='Most failed samples a record may carry (default: %(default)s).')
    parser.add_argument(
        '--probe-interval', type=float, default=PROBE_INTERVAL_SECONDS,
        help='The interval the probe samples at, in seconds (default: %(default)s).')
    parser.add_argument(
        '--cadence-tolerance', type=float, default=CADENCE_TOLERANCE_SECONDS,
        help=('How far a window\'s mean cadence may sit from the probe interval, in '
              'seconds (default: %(default)s).'))
    parser.add_argument(
        '--report', default=default_report_path(),
        help='Path to tools/ci_headroom_report.py, whose sample floor this reads.')
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # The report module is loaded before arguments are parsed so that --help
    # can print the floor it is about to use. The sample floor itself is
    # resolved after parsing, so that a --report override still decides it.
    report = load_report(default_report_path())
    parser = build_parser(report.BAND_GATE_MIN_SAMPLES)
    args = parser.parse_args(argv)
    if os.path.abspath(args.report) != os.path.abspath(default_report_path()):
        report = load_report(args.report)
    if args.min_samples_usable is None:
        args.min_samples_usable = report.BAND_GATE_MIN_SAMPLES

    try:
        records = read_records(args.dataset)
    except HealthError as e:
        print('FAIL unreadable dataset: %s' % e, file=sys.stdout)
        return EXIT_UNREADABLE

    print('Checking %d records from %s' % (len(records), args.dataset))
    if report_checks(evaluate(records, args), sys.stdout):
        return EXIT_OK
    return EXIT_UNHEALTHY


if __name__ == '__main__':
    sys.exit(main())
