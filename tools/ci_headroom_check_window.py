#!/usr/bin/env python3
# Copyright 2019 Michael Still and contributors
"""Recompute the CI headroom band gate's arming figures from committed data.

The band gate in ``tools/ci_headroom_report.py`` fails a cluster CI job when
the cluster-wide committed-CPU p90 sits above ``BAND_UPPER``. Arming a
merge-blocking gate rested on an argument about a particular window of
``merge_group`` runs: that the gate would have failed none of them, that
every one of them carried enough samples for the gate to have an opinion at
all, and that none of them would have had the gate withheld.

That argument was first written down as a table of numbers in a plan, and a
typed table is not checkable. The runs it was computed from are GitHub
artifacts, which expire ninety days after their run, so a reader who wanted
to check the arithmetic a quarter later could not. The window is now
committed as
``docs/plans/data/ci-cloud-sizing-baseline/records-warn-window.jsonl`` and
this tool is what checks it: the claims are stated once, as data, and
recomputed from the records every time it runs. It exits non-zero and says
which claim moved if any of them stops holding.

Run it with no arguments to check the committed dataset:

    python3 tools/ci_headroom_check_window.py

The three parts of that argument are not equally easy to check, and the
difference is worth stating rather than glossing. The committed warn window
is record version 4, so it banks ``cluster.committed_cpu.n_fraction`` and
``verdict.gates``/``verdict.gate_withheld`` directly. This tool nevertheless
recomputes all three from the underlying sample counters, and then compares
its own answer against the banked verdict, so a stored verdict which
disagrees with its own inputs is a failure rather than the thing being
trusted. The two older datasets in the same directory carry none of those
fields -- they arrived at record version 3 -- and so the gate question
cannot be asked of them at all. A record which does not carry what the
question needs is reported as unanswered: an absent field must never read
as a satisfied condition.

The dataset directory holds three files written by three different versions
of the report's record schema, and this tool also checks the one field whose
*meaning* changed across that boundary. ``verdict.refusal_warning`` reads
False in the older files both for a census which saw the scheduler and
tallied no capacity-stage drop and for a census which matched no scheduler
event at all -- an instrument which stopped looking, which the newer schema
writes as None instead. Anything pooling the three files has to re-derive
that reading from fields every version carries rather than trust the stored
flag, so the derivation lives here, in one place, with the check that says
whether any record is actually in the ambiguous state.
"""

import argparse
import collections
import json
import os
import sys


DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'docs', 'plans', 'data', 'ci-cloud-sizing-baseline')

WARN_WINDOW = os.path.join(DATA_DIR, 'records-warn-window.jsonl')

# Every committed dataset in that directory, in the order they were
# harvested. Listed explicitly rather than globbed: a check that silently
# covers whatever files happen to be present cannot tell a new dataset from
# a missing one, and the record counts below are part of what is asserted.
DATASETS = (
    ('records.jsonl', 217),
    ('records-addendum.jsonl', 32),
    ('records-warn-window.jsonl', 50),
)


# The cluster-wide band the gate judges against. Duplicated from
# ci_headroom_report.py rather than imported, because the question this tool
# answers is whether the window supports the bound the gate was armed on,
# and reading the bound out of the gate would make that question vacuous:
# move the constant and the check would follow it instead of failing.
BAND_LOWER = 0.35
BAND_UPPER = 0.70

# The sample floor a band violation may rest on, duplicated for the same
# reason. A series shorter than this has the gate withheld.
BAND_GATE_MIN_SAMPLES = 20


JobClaim = collections.namedtuple(
    'JobClaim',
    ['job', 'topology', 'runs', 'min_p90', 'max_p90', 'within', 'oversized',
     'oversubscribed', 'per_node_above'])


# The per-job figures the arming argument was made from, one row per entry
# of the merge matrix the window covers. Rounded to three places, which is
# how they were reported; the records carry full precision and ROUNDING
# below is where the comparison is made.
JOB_CLAIMS = (
    JobClaim('Debian 12 cluster', 'slim-primary', 10,
             0.296, 0.370, 2, 8, 0, 2),
    JobClaim('Ubuntu 24.04 cluster', 'slim-primary', 10,
             0.259, 0.407, 3, 7, 0, 3),
    JobClaim('Guests', 'slim-primary', 10,
             0.222, 0.296, 0, 10, 0, 4),
    JobClaim('Debian 12 tier', 'slim-tier', 10,
             0.292, 0.417, 5, 5, 0, 1),
)

ROUNDING = 3

# The headline claims, which are the ones the decision to arm actually
# turned on.
CLAIMED_JOB_RUNS = 40
CLAIMED_MERGE_RUNS = 10
CLAIMED_MAX_P90 = 0.417
CLAIMED_OVERSIZED = 30
CLAIMED_MIN_N_FRACTION = 67
CLAIMED_GATE_FAILURES = 0
CLAIMED_GATE_WITHHELD = 0

# The two Debian under-cloud lanes were renamed to Debian 13 after this
# window ran, so the job names above are the ones the window's own bundles
# carry and will not match a window harvested today. A rename is a reason to
# add a row here, never to edit one: the records are what they are.


class CheckFailure(Exception):
    """A claim about the committed window which no longer recomputes."""


def load(path):
    """Every record in one JSONL dataset."""
    with open(path) as handle:
        return [json.loads(line) for line in handle if line.strip()]


def is_usable(record):
    """Whether this record carries a committed-CPU distribution at all.

    A bundle with no series, or one whose every sample failed to produce a
    cluster fraction, is a record rather than a gap -- the harvest writes it
    out on purpose so the count of what could not be measured is itself in
    the dataset -- but it contributes to no distribution and the gate would
    have had nothing to judge.
    """
    summary = record.get('summary')
    if not summary:
        return False
    # Keyed on n rather than n_fraction, because n is in every schema
    # version and n_fraction is not. The two are separate questions: this
    # one is whether anything was measured, and the gate's sample floor
    # below is counted from n_fraction specifically.
    return bool(summary['cluster']['committed_cpu']['n'])


def refusal_warning(summary):
    """The capacity-refusal warning, read the same way in every schema.

    Three states, and the middle one is why this cannot read the stored
    flag. A census which was not collected says nothing. A census which was
    read and matched no scheduler stage event is the *instrument* failing to
    look, not the cluster failing to refuse, and has no answer either. Only
    a census which saw the scheduler can report whether it tallied a
    capacity-stage drop.

    Records written before the report separated the middle case store False
    for it, indistinguishable from an observed-clean run, so pooling them
    with newer records off the stored flag counts an instrument failure as a
    clean result. Computed from ``census.state``, ``census.stage_events``
    and ``census.capacity_shortage_drops``, which every schema version
    carries, it reads the same across the boundary.
    """
    census = summary['census']
    if census['state'] != 'read' or not census['stage_events']:
        return None
    return bool(census['capacity_shortage_drops'])


# The fields a record must carry for the gate question to be answerable
# from it at all, each as a (block, field) pair. A record missing any of
# them cannot support the claim that the gate would have withheld nothing,
# and must not be allowed to read as though it did -- an absent field is
# the absence of an answer, never a satisfied condition. The three series
# counters arrived together after the oldest committed dataset was
# harvested, and n_fraction arrived later still, so the two older files in
# this directory carry none of them: the claim about the gate is checkable
# against the warn window and nothing else.
GATE_FIELDS = (
    ('cluster', 'n_fraction'),
    ('series', 'capacity_degraded_samples'),
    ('series', 'capacity_degraded_absent_samples'),
    ('series', 'ledger_unreadable_samples'),
    ('series', 'ledger_unreadable_prefix_samples'),
)


class NotJudgeable(Exception):
    """A record which does not carry what the gate question needs."""


def missing_gate_fields(summary):
    """Which of GATE_FIELDS this record does not carry."""
    missing = []
    for block, field in GATE_FIELDS:
        holder = (summary['cluster']['committed_cpu'] if block == 'cluster'
                  else summary['series'])
        if field not in holder or holder[field] is None:
            missing.append('%s.%s' % (block, field))
    return missing


def gate_withheld(summary):
    """Why the gate could not rest on this series, or [] if it could.

    Recomputed from the counters rather than read out of
    ``verdict.gate_withheld``, so the stored verdict can be checked against
    its own inputs rather than taken on trust -- and so that a record from
    before that field existed is answered by refusal rather than by
    silence. The warm-up prefix of samples taken before the capacity table
    is populated is not a reason to withhold: every cluster job opens with
    one.

    Raises NotJudgeable for a record which does not carry the counters the
    question needs. Returning [] for one would report "nothing withheld"
    about a series nobody measured for it.
    """
    missing = missing_gate_fields(summary)
    if missing:
        raise NotJudgeable(
            'this record carries none of %s, so whether the gate would have '
            'been withheld cannot be read from it' % ', '.join(missing))
    series = summary['series']
    cluster = summary['cluster']['committed_cpu']
    reasons = []
    if cluster['n_fraction'] < BAND_GATE_MIN_SAMPLES:
        reasons.append('only %d samples produced a cluster CPU fraction'
                       % cluster['n_fraction'])
    if series['capacity_degraded_samples']:
        reasons.append('the capacity read reported failing on %d samples'
                       % series['capacity_degraded_samples'])
    if series['capacity_degraded_absent_samples']:
        reasons.append('%d samples carried no capacity_degraded flag'
                       % series['capacity_degraded_absent_samples'])
    late = (series['ledger_unreadable_samples']
            - series['ledger_unreadable_prefix_samples'])
    if late:
        reasons.append('%d samples unreadable after the warm-up prefix'
                       % late)
    return reasons


def would_gate(summary):
    """Whether the band gate would have failed this job-run.

    The gate fires on one condition and one only: a cluster-wide p90 above
    the upper bound, on a series the report could read. A p90 below the
    lower bound is information, not a failure -- gating on it would have
    reddened three quarters of the window.
    """
    ratio = summary['cluster']['committed_cpu']['p90_fraction']
    if ratio is None:
        return False
    # gate_withheld() raises NotJudgeable for a record which cannot answer,
    # and that propagates on purpose: a caller must not get False here for
    # a series nobody measured the withholding conditions on.
    return ratio > BAND_UPPER and not gate_withheld(summary)


def band(summary):
    """The band verdict, recomputed from the fraction and the bounds."""
    ratio = summary['cluster']['committed_cpu']['p90_fraction']
    if ratio is None:
        return None
    if ratio < BAND_LOWER:
        return 'OVERSIZED'
    if ratio > BAND_UPPER:
        return 'OVERSUBSCRIBED'
    return 'WITHIN BAND'


def per_node_above(summary):
    """Whether this job-run's per-node maximum sat above its own bound.

    Published information rather than a gating condition: the statistic
    saturates at its ceiling in a large fraction of healthy job-runs, so it
    is read against the lower tail. Recomputed here because it is part of
    the window's per-job table.
    """
    verdict = summary.get('verdict') or {}
    return verdict.get('per_node_band') == 'ABOVE BAND'


def summarise(records):
    """Everything the claims are checked against, computed once.

    The gate question is answered only for records which carry what it
    needs. ``unjudgeable`` holds the rest, with the reason, so a record the
    gate cannot be read from is counted as unanswered rather than
    disappearing into the clean pile.
    """
    usable = [r for r in records if is_usable(r)]
    fractions = [r['summary']['cluster']['committed_cpu']['p90_fraction']
                 for r in usable]
    by_job = collections.defaultdict(list)
    for record in usable:
        by_job[record['job']].append(record)
    by_topology = collections.defaultdict(collections.Counter)
    for record in usable:
        by_topology[record['topology']][band(record['summary'])] += 1

    counted = []
    gating = []
    withheld = []
    unjudgeable = []
    for record in usable:
        summary = record['summary']
        try:
            reasons = gate_withheld(summary)
        except NotJudgeable as e:
            unjudgeable.append((record, str(e)))
            continue
        counted.append(summary['cluster']['committed_cpu']['n_fraction'])
        if reasons:
            withheld.append(record)
        if would_gate(summary):
            gating.append(record)

    return {
        'records': len(records),
        'usable': usable,
        'merge_runs': len({r['run_id'] for r in records}),
        'fractions': fractions,
        'counted': counted,
        'by_job': by_job,
        'by_topology': by_topology,
        'bands': collections.Counter(band(r['summary']) for r in usable),
        'gating': gating,
        'withheld': withheld,
        'unjudgeable': unjudgeable,
    }


def check_window(records):
    """Recompute every claim the arming decision rested on.

    Returns the list of disagreements, each a sentence naming the claim, the
    figure it asserted and the figure the data now produces. An empty list
    means the decision still recomputes from the committed records.
    """
    stats = summarise(records)
    usable = stats['usable']
    failures = []

    def disagree(what, claimed, got):
        failures.append('%s: claimed %s, recomputed %s' % (what, claimed, got))

    if len(usable) != CLAIMED_JOB_RUNS:
        disagree('job-runs carrying a usable series', CLAIMED_JOB_RUNS,
                 len(usable))
    if stats['merge_runs'] != CLAIMED_MERGE_RUNS:
        disagree('merge runs in the window', CLAIMED_MERGE_RUNS,
                 stats['merge_runs'])

    if not usable:
        failures.append(
            'no record in the dataset carries a usable series, so nothing '
            'below could be recomputed at all')
        return failures

    highest = round(max(stats['fractions']), ROUNDING)
    if highest != CLAIMED_MAX_P90:
        disagree('highest cluster-wide p90 fraction', CLAIMED_MAX_P90,
                 highest)
    if highest >= BAND_UPPER:
        failures.append(
            'the highest p90 fraction (%s) is not below the upper bound '
            '(%s), so the window no longer supports the claim that the gate '
            'would have failed nothing in it' % (highest, BAND_UPPER))

    # Before either gate figure is believed: every job-run must carry the
    # fields the gate question is answered from. The claim is that none of
    # the 40 would have had the gate withheld, and a record which cannot
    # say must be reported as unanswered rather than counted as clean.
    if stats['unjudgeable']:
        failures.append(
            '%d of %d job-runs cannot answer the gate question at all, so '
            'the claim that none would have had the gate withheld is not '
            'checkable against this dataset: %s'
            % (len(stats['unjudgeable']), len(usable),
               '; '.join('%s (%s)' % (describe(record), reason)
                         for record, reason in stats['unjudgeable'])))

    if len(stats['gating']) != CLAIMED_GATE_FAILURES:
        disagree('job-runs the gate would have failed', CLAIMED_GATE_FAILURES,
                 '%d (%s)' % (len(stats['gating']),
                              ', '.join(describe(r) for r in stats['gating'])))
    if len(stats['withheld']) != CLAIMED_GATE_WITHHELD:
        disagree('job-runs with the gate withheld', CLAIMED_GATE_WITHHELD,
                 '%d (%s)' % (len(stats['withheld']),
                              '; '.join(
                                  '%s: %s' % (describe(r),
                                              ', '.join(gate_withheld(r['summary'])))
                                  for r in stats['withheld'])))

    # The stored verdict, checked against the recomputation rather than
    # taken on trust. verdict.gates and verdict.gate_withheld arrived at
    # record version 3, so neither of the two older datasets in this
    # directory carries them and this cross-check is only possible on a
    # version 3 or later window -- which is precisely the point of having
    # harvested this one with the current report.
    for record in usable:
        summary = record['summary']
        verdict = summary.get('verdict') or {}
        if 'gate_withheld' not in verdict:
            continue
        try:
            reasons = gate_withheld(summary)
        except NotJudgeable:
            continue
        if bool(verdict['gate_withheld']) != bool(reasons):
            disagree('%s: stored gate_withheld' % describe(record),
                     verdict['gate_withheld'], reasons)
        if 'gates' in verdict and bool(verdict['gates']) != would_gate(summary):
            disagree('%s: stored verdict.gates' % describe(record),
                     verdict['gates'], would_gate(summary))

    oversized = stats['bands']['OVERSIZED']
    if oversized != CLAIMED_OVERSIZED:
        disagree('job-runs below the lower bound', CLAIMED_OVERSIZED,
                 oversized)

    # The sample floor is expressed in samples which produced a fraction,
    # and the argument that no job-run came near it was made on that field
    # directly. Both halves are checked: that the field agrees with the
    # usable-sample count, which is how the two older datasets -- which do
    # not carry n_fraction at all -- were read against the same floor, and
    # that the smallest one is where it was.
    mismatched = [
        r for r in usable
        if 'n_fraction' in r['summary']['cluster']['committed_cpu']
        and (r['summary']['cluster']['committed_cpu']['n_fraction']
             != r['summary']['cluster']['committed_cpu']['n'])]
    if mismatched:
        disagree('job-runs where n_fraction equals n', 'all %d' % len(usable),
                 '%d disagree (%s)'
                 % (len(mismatched),
                    ', '.join(describe(r) for r in mismatched)))
    if stats['counted']:
        smallest = min(stats['counted'])
        if smallest != CLAIMED_MIN_N_FRACTION:
            disagree('smallest sample count producing a fraction',
                     CLAIMED_MIN_N_FRACTION, smallest)
        if smallest < BAND_GATE_MIN_SAMPLES:
            failures.append(
                'the smallest sample count (%d) is below the gate floor '
                '(%d), so the window no longer supports the claim that '
                'nothing in it would have had the gate withheld'
                % (smallest, BAND_GATE_MIN_SAMPLES))
    else:
        failures.append(
            'not one job-run carries n_fraction, so the gate sample floor '
            'cannot be checked against this dataset at all')

    for claim in JOB_CLAIMS:
        failures.extend(check_job(stats['by_job'].get(claim.job, []), claim))

    return failures


def check_job(records, claim):
    """One row of the per-job table, recomputed."""
    failures = []
    where = 'job %r' % claim.job
    if len(records) != claim.runs:
        return ['%s: claimed %d job-runs, recomputed %d'
                % (where, claim.runs, len(records))]

    topologies = {r['topology'] for r in records}
    if topologies != {claim.topology}:
        failures.append('%s: claimed topology %r, recomputed %s'
                        % (where, claim.topology, sorted(topologies)))

    fractions = [r['summary']['cluster']['committed_cpu']['p90_fraction']
                 for r in records]
    for what, claimed, got in (
            ('min p90', claim.min_p90, round(min(fractions), ROUNDING)),
            ('max p90', claim.max_p90, round(max(fractions), ROUNDING))):
        if claimed != got:
            failures.append('%s: claimed %s %s, recomputed %s'
                            % (where, what, claimed, got))

    bands = collections.Counter(band(r['summary']) for r in records)
    for verdict, claimed in (('WITHIN BAND', claim.within),
                             ('OVERSIZED', claim.oversized),
                             ('OVERSUBSCRIBED', claim.oversubscribed)):
        if bands[verdict] != claimed:
            failures.append('%s: claimed %d %s, recomputed %d'
                            % (where, claimed, verdict, bands[verdict]))

    above = sum(1 for r in records if per_node_above(r['summary']))
    if above != claim.per_node_above:
        failures.append('%s: claimed %d per-node ABOVE BAND, recomputed %d'
                        % (where, claim.per_node_above, above))
    return failures


def check_schema_mix(datasets):
    """What a reader pooling the three committed files has to know.

    Two things, and the second is the one that bites. The record counts are
    asserted so that a dataset which was truncated or regrown announces
    itself here rather than quietly changing a distribution. The field whose
    meaning changed is then re-derived for every record in all three files,
    and the count of records actually in the ambiguous state is reported: it
    is zero today, which is what makes pooling them safe, and a dataset
    added later which is not zero needs saying out loud.
    """
    failures = []
    ambiguous = 0
    total = 0
    versions = collections.Counter()
    for path, claimed in datasets:
        records = load(path)
        if len(records) != claimed:
            failures.append('%s: claimed %d records, counted %d'
                            % (os.path.basename(path), claimed, len(records)))
        for record in records:
            summary = record.get('summary')
            if not summary:
                continue
            total += 1
            versions[summary['record_version']] += 1
            census = summary['census']
            if census['state'] == 'read' and not census['stage_events']:
                ambiguous += 1
                continue
            # Where the stored flag and the re-derivation agree, they must
            # agree exactly. A record where they do not, outside the
            # ambiguous state, would mean the derivation here has drifted
            # from the one the report publishes.
            stored = summary['verdict'].get('refusal_warning')
            derived = refusal_warning(summary)
            if stored is not None and bool(stored) != derived:
                failures.append(
                    '%s run %s %s: stored refusal_warning %r, re-derived %r'
                    % (os.path.basename(path), record['run_id'],
                       record['job'], stored, derived))
    return failures, {'summarised': total, 'ambiguous': ambiguous,
                      'versions': versions}


def describe(record):
    """A job-run, named the way a reader can go and look at it."""
    return '%s/%s' % (record['run_id'], record['job'])


def report(records, stats, mix):
    """Print what was recomputed, so a passing run is still informative."""
    print('Warn window: %d records, %d merge runs, %d job-runs with a '
          'usable series' % (len(records), stats['merge_runs'],
                             len(stats['usable'])))
    print('Window spans %s to %s'
          % (min(r['run_created_at'] for r in records),
             max(r['run_created_at'] for r in records)))
    print()
    print('%-22s %-13s %5s %7s %7s %7s %7s %7s %7s'
          % ('Job', 'Topology', 'Runs', 'min p90', 'max p90', 'WITHIN',
             'OVER-', 'OVERSUB', 'node>'))
    for claim in JOB_CLAIMS:
        rows = stats['by_job'].get(claim.job, [])
        if not rows:
            print('%-22s %-13s %5s' % (claim.job, claim.topology, 0))
            continue
        fractions = [r['summary']['cluster']['committed_cpu']['p90_fraction']
                     for r in rows]
        bands = collections.Counter(band(r['summary']) for r in rows)
        print('%-22s %-13s %5d %7.3f %7.3f %7d %7d %7d %7d'
              % (claim.job, rows[0]['topology'], len(rows), min(fractions),
                 max(fractions), bands['WITHIN BAND'], bands['OVERSIZED'],
                 bands['OVERSUBSCRIBED'],
                 sum(1 for r in rows if per_node_above(r['summary']))))
    print()
    print('Highest cluster-wide p90 fraction: %.3f against an upper bound '
          'of %.2f' % (max(stats['fractions']), BAND_UPPER))
    print('Job-runs the gate would have failed: %d' % len(stats['gating']))
    print('Job-runs with the gate withheld: %d' % len(stats['withheld']))
    print('Job-runs which cannot answer the gate question: %d'
          % len(stats['unjudgeable']))
    if stats['counted']:
        print('Smallest sample count producing a fraction: %d against a '
              'floor of %d' % (min(stats['counted']), BAND_GATE_MIN_SAMPLES))
    print('Job-runs below the lower bound of %.2f: %d of %d'
          % (BAND_LOWER, stats['bands']['OVERSIZED'], len(stats['usable'])))
    for topology in sorted(stats['by_topology']):
        counts = stats['by_topology'][topology]
        print('  %s: %d of %d below the lower bound'
              % (topology, counts['OVERSIZED'], sum(counts.values())))
    print()
    print('Across all committed datasets: %d summarised records, schema '
          'versions %s' % (mix['summarised'],
                           ', '.join('%s (%d)' % (version, count)
                                     for version, count
                                     in sorted(mix['versions'].items()))))
    print('Records whose census was read but matched no scheduler stage '
          'event, and so can report no refusal warning in any schema '
          'version: %d' % mix['ambiguous'])


def build_parser():
    parser = argparse.ArgumentParser(
        description=('Recompute the CI headroom band gate arming figures '
                     'from the committed datasets.'))
    parser.add_argument(
        '--window', default=WARN_WINDOW,
        help='The committed warn-window dataset to recompute from.')
    parser.add_argument(
        '--data-dir', default=DATA_DIR,
        help='Where the committed datasets live, for the schema-mix check.')
    parser.add_argument(
        '--quiet', action='store_true',
        help='Print only disagreements.')
    return parser


def main(argv=None):
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    records = load(args.window)
    datasets = [(os.path.join(args.data_dir, name), count)
                for name, count in DATASETS]
    failures = check_window(records)
    mix_failures, mix = check_schema_mix(datasets)
    failures.extend(mix_failures)
    if not args.quiet:
        report(records, summarise(records), mix)
        print()
    if failures:
        print('%d claim%s no longer recomputes from the committed data:'
              % (len(failures), '' if len(failures) == 1 else 's'),
              file=sys.stderr)
        for failure in failures:
            print('  %s' % failure, file=sys.stderr)
        print('This is a disagreement to investigate, not a number to '
              'adjust: the committed records are the evidence the gate was '
              'armed on.', file=sys.stderr)
        return 1
    if not args.quiet:
        print('Every arming figure recomputes from the committed records.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
