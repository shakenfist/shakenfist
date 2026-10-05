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
and 2 when the dataset, or the report tool the sample floor is read from,
could not be read at all.
"""

import argparse
import collections
import datetime
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
# every bundle it writes: once the probe was running, 32 of 32, and in the
# ten newest merge runs on 2026-10-05, 31 of the 31 whose jobs were not
# cancelled. Cancelled jobs are set aside before this is computed (see
# set_aside() below), so the slack here is not for them; it is for a gap
# nobody has characterised yet, which at the harvest limit the scheduled
# job uses is up to three or four records.
SERIES_PRESENT_FLOOR = 0.90

# The fewest records, once cancelled jobs are set aside, a harvest may
# yield before the window itself is suspect. The modes which produce no
# record at all -- a run that banked no bundle, a collect which could not
# reach the primary -- leave nothing for a per-record check to find, so they
# are only visible as a count.
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

# How old the newest harvested run may be, in days, before the window is
# not the one the harvest was asked for.
#
# The scheduled harvest asks for the ten newest merge runs and trusts the
# answer. On 2026-10-05 the runs listing twice, minutes apart, served a
# stale window as the newest runs: once a single short page whose newest
# run was 2026-09-11, so the harvest stopped paginating, and once a full
# walk whose newest was 2026-09-24. Each read as the current window.
# Nothing per-record can see that: old records are well-formed records.
# Merges land several times a day, so a week would do; two weeks keeps a
# quiet holiday from reading as a broken instrument.
MAX_WINDOW_AGE_DAYS = 14

# How many records from gated lanes may carry a withheld verdict.
#
# The committed datasets predate the field, so this is the one threshold
# measured only against a fresh harvest: the ten newest merge runs on
# 2026-10-05, in which none of the 24 records from gated lanes was
# withheld. That is one window rather than a distribution, so this is the
# threshold most likely to need its flag; the first few scheduled runs are
# its real measurement.
MAX_GATE_WITHHELD = 0

# The lanes the band gate is armed on, as the record labels name them:
# '<topology> <stestr config>'. These are MEASURED_SHAPES in
# shakenfist/tests/test_headroom_gate_workflow_seams.py, which is where
# arming a shape happens, and a test pins this set to that one.
#
# The band gate's sample floor and its withheld verdict are only claims
# about a lane the gate judges. The Ansible modules lane carries the probe
# but is not armed, and its job probes for about three and a half minutes:
# in that same window its seven records held 9 to 15 usable samples against
# a floor of 20, and every one was withheld for it. That is the gate
# correctly declining to judge a short job, not an instrument failing, and
# a check which reported it would fail every week until somebody stopped
# reading it.
GATED_LABELS = frozenset((
    'slim-primary cluster-ci.conf',
    'slim-primary guest-ci.conf',
    'slim-tier cluster-ci.conf',
))

# The fewest usable samples a record from a lane the gate is not armed on
# may carry. Not the band gate's floor, which that lane can never reach; low
# enough to clear the shortest job in the measured window (9 samples) and
# high enough that a probe which died in its first minute still fails.
MIN_SAMPLES_UNGATED = 5

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
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        # Executing another file can raise anything at all -- a missing
        # path, a syntax error, an import it cannot satisfy -- and every one
        # of them is the same answer to the caller: there is no floor to
        # read, so the exit-two contract applies rather than a traceback.
        raise HealthError('could not load the report tool from %s: %s: %s'
                          % (path, type(e).__name__, e))
    return module


def band_gate_min_samples(path):
    """The band gate's sample floor, read out of the report tool at path."""
    floor = getattr(load_report(path), 'BAND_GATE_MIN_SAMPLES', None)
    if not isinstance(floor, int) or isinstance(floor, bool):
        raise HealthError('%s does not define an integer BAND_GATE_MIN_SAMPLES (found %r)'
                          % (path, floor))
    return floor


def default_report_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'ci_headroom_report.py')


# Every scalar a check compares, counts, hashes or tests for truth, by where
# it sits in a record and the types it may hold. None is allowed for each,
# since an absent field is read as unknown by the check which reads it, but
# nothing else is taken on trust: a string '0' is a true value and a list is
# unhashable, so the wrong type here is not an error a check can be relied
# upon to raise -- it is as likely to be read as a passing answer. Fields
# which are only printed are not listed. bool is refused wherever a number
# is wanted, because JSON true is an int as far as isinstance() is concerned.
RECORD_SCALARS = (
    ((), 'run_created_at', (str,)),
    ((), 'job_conclusion', (str,)),
    ((), 'label', (str,)),
    ((), 'series_present', (bool,)),
    (('summary',), 'record_version', (int,)),
    (('summary', 'series'), 'samples_usable', (int,)),
    (('summary', 'series'), 'samples_failed', (int,)),
    (('summary', 'series'), 'window_seconds', (int, float)),
    (('summary', 'census'), 'state', (str,)),
    (('summary', 'census'), 'stage_events', (int,)),
    (('summary', 'census'), 'capacity_shortage_drops', (int,)),
)


def scalar_type_error(container, key, types):
    """Why container[key] is neither absent nor one of types, or None."""
    value = container.get(key)
    if value is None:
        return None
    if isinstance(value, types) and (bool in types or not isinstance(value, bool)):
        return None
    return '%s is %r, not %s' % (key, value, ' or '.join(t.__name__ for t in types))


def record_shape_error(record):
    """Why a parsed line is not shaped like a harvest record, or None.

    A record is an object, and a summary, where there is one, is an object
    holding a ``series`` object and, where it has them, ``census`` and
    ``verdict`` objects. Every scalar in RECORD_SCALARS is the type it is
    listed as or absent, and ``verdict.gate_withheld``, where present, is a
    list of strings, since the withheld check joins it. A line which parses
    but is the wrong shape is a truncated or foreign file, and must exit two
    and name the line, not crash a check half way through with a traceback
    -- or, worse, be read by one as an answer.
    """
    if not isinstance(record, dict):
        return 'is not a JSON object'
    summary = record.get('summary')
    if summary is not None:
        if not isinstance(summary, dict):
            return 'has a summary which is not a JSON object'
        if not isinstance(summary.get('series'), dict):
            return 'has a summary with no series object'
        for key in ('census', 'verdict'):
            if summary.get(key) is not None and not isinstance(summary[key], dict):
                return 'has a summary whose %s is not a JSON object' % key

    for path, key, types in RECORD_SCALARS:
        container = record
        for step in path:
            container = container.get(step)
            if container is None:
                break
        if container is None:
            continue
        problem = scalar_type_error(container, key, types)
        if problem:
            return 'has %s' % '.'.join(path + (problem,))

    withheld = ((summary or {}).get('verdict') or {}).get('gate_withheld')
    if withheld is not None and (not isinstance(withheld, list)
                                 or not all(isinstance(reason, str) for reason in withheld)):
        return 'has summary.verdict.gate_withheld %r, not a list of strings' % (withheld,)
    return None


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
                    record = json.loads(line)
                except ValueError as e:
                    raise HealthError('%s line %d is not JSON: %s' % (path, number, e))
                problem = record_shape_error(record)
                if problem:
                    raise HealthError('%s line %d %s' % (path, number, problem))
                records.append(record)
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


def gated(record):
    """Whether the band gate is armed on the lane this record came from.

    Read from the label the collect script writes, so a record with no label
    -- one predating it, or a job cancelled before it was written -- is
    treated as ungated. That cannot hide a broken label file: a window in
    which no record is gated has no verdict the withheld check can judge,
    and that check fails it.
    """
    return record.get('label') in GATED_LABELS


def set_aside(record):
    """Whether this record says nothing about the instrument at all.

    A job the merge queue cancelled -- because the group it was testing was
    superseded, which is routine -- uploads whatever it had, often a bundle
    with no series in it. That is a fact about the queue, not about the
    probe, and counted as a gap it would push the series-present rate
    towards its floor on an ordinary busy week: one cancelled run in the
    window measured on 2026-10-05 took the rate from 1.000 to 0.912.
    """
    return record.get('job_conclusion') == 'cancelled'


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


def check_window_age(records, args):
    """The newest run harvested is recent, so the window is the one asked for."""
    created = [stamp for stamp in (parse_timestamp(record.get('run_created_at'))
                                   for record in records) if stamp is not None]
    if not created:
        return Check('window age', False,
                     'no record says when its run was created', [])
    newest = max(created)
    age = args.now - newest
    limit = datetime.timedelta(days=args.max_window_age_days)
    return Check(
        'window age',
        age <= limit,
        'newest run %s, %.1f days old, ceiling %d days'
        % (newest.strftime('%Y-%m-%dT%H:%M:%SZ'), age.total_seconds() / 86400,
           args.max_window_age_days),
        [])


def parse_timestamp(value):
    """A GitHub ISO 8601 timestamp as an aware datetime, or None."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def check_record_count(records, args):
    ok = len(records) >= args.min_records
    return Check(
        'records harvested',
        ok,
        '%d records, floor %d' % (len(records), args.min_records),
        [])


def check_series_present(records, args):
    """Enough of the window carries a series which was summarised.

    A record which says it carries a series and has no summary fails this
    check outright, whatever the rate. Every per-record check below reads
    the summary, so such a record would otherwise pass all of them: a
    report which failed to summarise a series it was handed is the dead
    instrument this tool exists to catch, one layer further down, and the
    floor's slack is for cancelled jobs rather than for that. The harvest
    does not write that shape today, which is an assumption this check
    states rather than relies on.
    """
    present = []
    unsummarised = []
    absent = []
    for record in records:
        if record.get('series_present') and record.get('summary'):
            present.append(record)
        elif record.get('series_present'):
            unsummarised.append('%s: series present but no summary' % record_name(record))
        else:
            absent.append('%s: %s' % (record_name(record),
                                      record.get('absent_reason') or 'no reason recorded'))
    rate = len(present) / len(records) if records else 0.0
    detail = ('%d of %d records carry a summarised series (%.3f), floor %.3f'
              % (len(present), len(records), rate, args.series_present_floor))
    if unsummarised:
        detail += '; %d claim a series and have no summary' % len(unsummarised)
    return Check(
        'series present rate',
        rate >= args.series_present_floor and not unsummarised,
        detail,
        unsummarised + absent)


def check_samples_usable(records, args):
    """Every summarised record carries enough samples to say anything.

    On a lane the gate is armed on, the floor is the band gate's own sample
    floor, which is where the number comes from; on any other lane it is
    MIN_SAMPLES_UNGATED, for the reason given beside it. Note that clearing
    it is necessary and not sufficient for the gate: the gate counts the
    samples which produced a CPU fraction, and the first two or three
    minutes of a cluster's life produce usable samples whose capacity table
    is still empty. The gate says so itself, which is what the withheld
    check below reads.
    """
    offenders = []
    worst = {True: None, False: None}
    for record, summary in summarised(records):
        usable = summary['series'].get('samples_usable') or 0
        lane = gated(record)
        floor = args.min_samples_usable if lane else args.min_samples_ungated
        worst[lane] = usable if worst[lane] is None else min(worst[lane], usable)
        if usable < floor:
            offenders.append('%s: %d usable samples, floor %d' % (record_name(record), usable, floor))
    return Check(
        'usable samples per record',
        not offenders,
        'fewest %s on gated lanes (floor %d), %s on ungated lanes (floor %d)'
        % ('n/a' if worst[True] is None else worst[True], args.min_samples_usable,
           'n/a' if worst[False] is None else worst[False], args.min_samples_ungated),
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
        if (summary['series'].get('samples_usable') or 0) < 2:
            # Fewer than two samples have no spacing to measure, and the
            # usable-samples check above has already failed on them.
            # Reporting it twice says nothing new.
            continue
        cadence = mean_cadence(summary['series'])
        if cadence is None:
            # Two or more samples and no window to divide: the series is
            # not saying when it was sampled, which is not the same as
            # saying it was sampled on time.
            offenders.append('%s: %d usable samples and no window to measure them over'
                             % (record_name(record), summary['series'].get('samples_usable')))
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
    ungated = 0
    for record, summary in summarised(records):
        if not gated(record):
            ungated += 1
            continue
        verdict = summary.get('verdict') or {}
        version = summary.get('record_version') or 0
        withheld = verdict.get('gate_withheld')
        if version < GATE_WITHHELD_RECORD_VERSION or withheld is None:
            unassertable += 1
            continue
        asserted += 1
        if withheld:
            offenders.append('%s: %s' % (record_name(record), '; '.join(withheld)))
    detail = ('%d records judged, %d too old to say, %d on ungated lanes, %d withheld, ceiling %d'
              % (asserted, unassertable, ungated, len(offenders), args.max_gate_withheld))
    if not asserted and summarised(records):
        return Check(
            'gate withheld', False,
            detail + '. Not one record in this window publishes whether the '
            'gate was allowed to judge it', offenders)
    return Check('gate withheld', len(offenders) <= args.max_gate_withheld, detail, offenders)


# Each of these reads the records left once set_aside() has removed the
# ones which say nothing about the instrument. The window's age is the
# exception, below, because it is a question about the listing the harvest
# read rather than about any one job.
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
    judged = [record for record in records if not set_aside(record)]
    return [check_window_age(records, args)] + [check(judged, args) for check in CHECKS]


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


def build_parser():
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
              "gate's own sample floor, BAND_GATE_MIN_SAMPLES in the report tool, read "
              'from it rather than copied.'))
    parser.add_argument(
        '--min-samples-ungated', type=int, default=MIN_SAMPLES_UNGATED,
        help=('Fewest usable samples a record from a lane the band gate is not armed on '
              'may carry (default: %(default)s).'))
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
        '--max-gate-withheld', type=int, default=MAX_GATE_WITHHELD,
        help=('Most records from gated lanes whose verdict may be withheld '
              '(default: %(default)s).'))
    parser.add_argument(
        '--max-window-age-days', type=int, default=MAX_WINDOW_AGE_DAYS,
        help=('Oldest the newest harvested run may be, in days, before the window is '
              'treated as not the one asked for (default: %(default)s).'))
    parser.add_argument(
        '--report', default=default_report_path(),
        help='Path to tools/ci_headroom_report.py, whose sample floor this reads.')
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    args.now = datetime.datetime.now(datetime.timezone.utc)

    # Only the report tool which will actually be used is loaded, and only
    # when its floor is needed, so neither --help nor a --report override
    # depends on the default one loading.
    if args.min_samples_usable is None:
        try:
            args.min_samples_usable = band_gate_min_samples(args.report)
        except HealthError as e:
            print('FAIL unreadable report tool: %s' % e, file=sys.stdout)
            return EXIT_UNREADABLE

    try:
        records = read_records(args.dataset)
    except HealthError as e:
        print('FAIL unreadable dataset: %s' % e, file=sys.stdout)
        return EXIT_UNREADABLE

    aside = sum(1 for record in records if set_aside(record))
    print('Checking %d records from %s, %d set aside because their job was cancelled'
          % (len(records) - aside, args.dataset, aside))
    if report_checks(evaluate(records, args), sys.stdout):
        return EXIT_OK
    return EXIT_UNHEALTHY


if __name__ == '__main__':
    sys.exit(main())
