#!/usr/bin/env python3

# Copyright 2026 Michael Still and contributors

"""Re-derive shakenfist/data/database_load_budget.yaml from Prometheus.

The budget expresses expected sf-database load as a model:

    expected_qps = per_node_base_qps x nodes
                 + cluster_base_qps
                 + per_instance_qps x standing_instances

This tool fits the per-instance term for every (operation, caller_daemon)
pair from ``database_requests_total``, regressed against the cluster's
standing instance count, and assigns the base term. Run it against a
Prometheus which has scraped an sf-database tier for several days across a
range of instance counts:

    tools/derive-database-load-budget.py \\
        --prometheus http://prometheus:9090 \\
        --start 2026-08-21T00:00:00Z --end 2026-08-24T18:00:00Z \\
        --nodes 6 > shakenfist/data/database_load_budget.yaml

Three things about the output are judgement rather than arithmetic, and all
three are why this writes to stdout for a human to review rather than
editing the shipped file in place.

**The base split is assigned, not fitted.** A single cluster does not change
node count across a measurement window, so the data cannot separate "work
done once per node" from "work done once per cluster". This tool assigns
work by the elected cluster daemon to ``cluster_base_qps`` and everything
else to ``per_node_base_qps``, which is right for the loops we have and
should be re-checked when a new one appears.

**Coverage matters more than the numbers.** ``database_requests_total`` is
incremented by a gRPC server interceptor, so it can only see callers which
go through the tier. Before #3708 a daemon co-located with MariaDB did not,
which on sfcbr hid two nodes of six and the whole cluster daemon whenever
the maintenance lock sat on one of them. A window spanning that change
produces a budget which is wrong in a way that looks like a regression. The
tool refuses windows which start before it.

**A measurement of a bug is not a budget.** Where a pair's load is a known
defect, mark the entry ``provisional`` by hand after generating, with the
issue number. Provisional entries are reported by every consumer and
enforced by none, so the defect does not become the floor that every
detector then protects.

Everything which is judgement rather than measurement is carried forward
from the budget being replaced, named by ``--previous`` and defaulting to
the shipped file: the ``defaults`` block, and each surviving pair's note
and provisional marking. Only a pair which did not exist before gets a
placeholder note, and the tool says on stderr how many did. This used to be
a hand restoration step described in this docstring, which meant running
the command above emitted a file that failed the repository's own tests --
``test_doc_block_records_its_provenance`` and
``test_provisional_entries_name_an_issue_and_are_not_enforced`` both assert
things ``emit()`` never wrote. A generator whose output does not pass is a
generator nobody runs.

A budget entry whose note does not say which loop produces the traffic is a
number nobody can act on when it goes red, so review the placeholders for
any genuinely new pair before committing.
"""

import argparse
import calendar
import collections
import json
import os
import sys
import textwrap
import time
import urllib.parse
import urllib.request

import yaml


# database_requests_total could not see the whole cluster before #3708
# reached it. See the module docstring.
COVERAGE_EPOCH = '2026-08-11T21:00:00Z'

# The budget this tool replaces, relative to the checkout it lives in.
DEFAULT_PREVIOUS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'shakenfist', 'data', 'database_load_budget.yaml')

# A pair is worth an entry if it averages at least this much. It has to sit
# well below the budget's unbudgeted ceiling, because a pair which is
# quiet in the measurement window and louder on a busier cluster would
# otherwise read as brand new traffic. Deriving at 0.30 against a window
# averaging 18 standing instances left five pairs which crossed 0.25/s once
# the same cluster reached 32, and each of those was a standing false alarm
# in the shipped Prometheus rules.
DEFAULT_MIN_MEAN_QPS = 0.10

# A fitted slope becomes a per_instance_qps term when it explains enough of
# the variance and is big enough to matter at a plausible cluster size.
MIN_R2 = 0.30
SLOPE_REFERENCE_INSTANCES = 40
MIN_SLOPE_CONTRIBUTION_QPS = 0.15

PLACEHOLDER_NOTE = ('TODO: name the loop which produces this traffic, or '
                    'carry the note forward from the previous budget.')

# The shipped budget's file header. Emitted rather than left to be restored
# by hand, for the same reason the caveats below are: what a generator does
# not write, a re-derivation deletes.
FILE_HEADER = """\
# Expected sf-database load, as a model rather than a number.
#
# Shaken Fist's database load is dominated by polling whose rate is set
# by how many things exist rather than by any work performed, so an
# absolute "expect under N queries per second" tells a deployer nothing
# about their own cluster. This file expresses the expectation as a
# decomposition instead:
#
#   expected_qps = per_node_base_qps   x nodes
#                + cluster_base_qps                 (once per cluster)
#                + per_instance_qps   x standing_instances
#
# and every consumer -- the functional CI check, the generated
# Prometheus rules, `sf-ctl database-load`, and our own nightly report
# -- reads this file rather than carrying its own copy. See
# docs/operator_guide/database.md for the operator's view and
# docs/plans/PLAN-database-load-reduction-phase-07-regression-detection.md
# for how the numbers were derived.
#
# DO NOT hand-edit levels to make a check pass. A budget that tracks
# whatever the code currently does is not a budget. If load has moved,
# either it is a regression worth fixing or the model has changed and
# the change belongs in a commit that says so.
#
# Generated by tools/derive-database-load-budget.py. Review the notes on
# any newly added pair before committing: see that tool's docstring.
"""

# These describe the model and the counter, not the measurement, so they
# are the same whichever cluster and window a re-derivation uses.
# test_doc_block_records_its_provenance asserts the budget carries them.
DOC_METHOD = (
    'Per (operation, caller_daemon) pair, an ordinary least squares fit '
    'of the pair\'s rate against the cluster\'s standing instance count '
    'over the window, where the standing instance count is '
    'sum(instances_active) -- running libvirt domains, not instances in '
    'the created state. The fitted slope becomes per_instance_qps where '
    'it is significant (r-squared at least %.2f and contributing at '
    'least %.2f/s at %d instances); the intercept becomes the base term. '
    'Every consumer of this file must count standing instances the same '
    'way or it will evaluate the model against a quantity it was not '
    'fitted against.' % (MIN_R2, MIN_SLOPE_CONTRIBUTION_QPS,
                         SLOPE_REFERENCE_INSTANCES))

DOC_BASE_TERM_CAVEAT = (
    'The split of the base between per_node_base_qps and cluster_base_qps '
    'is assigned from what the loop does, not fitted: a single cluster\'s '
    'node count does not vary across a measurement window, so the data '
    'cannot separate the two. Work done by the elected cluster daemon is '
    'cluster_base_qps; work done by a daemon on every node is '
    'per_node_base_qps. A cluster with a different role mix -- a node '
    'that runs no hypervisor, say -- will read low against the per-node '
    'terms, and that is a modelling limit rather than a fault in the '
    'cluster. The other direction is the one that costs somebody an '
    'afternoon: a cluster-wide singleton mis-assigned to '
    'per_node_base_qps reads high on a cluster smaller than the one this '
    'was derived from, which fails a build and fires an alert rather '
    'than merely under-predicting. Check the assignment before believing '
    'a small cluster is over budget.')

DOC_COVERAGE_CAVEAT = (
    'Only figures taken after 2026-08-11 are usable. Until #3708, '
    'database_requests_total was incremented by a gRPC server interceptor '
    'that could not see daemons co-located with MariaDB, which on sfcbr '
    'was two nodes of six and the whole cluster daemon whenever the '
    'maintenance lock sat on one of them. Coefficients from before that '
    'date undercount, and phase 6 spent a fortnight discovering it.')

# Why the inclusion cut sits where it does. A fact about this tool's
# judgement rather than about any one measurement, so it is stated here and
# appended to the computed coverage text below.
DOC_INCLUSION_CUT_LESSON = (
    'The inclusion cut sits well below that threshold on purpose, because '
    'a pair which is quiet in the measurement window and louder on a '
    'busier cluster would otherwise read as brand new traffic. Deriving '
    'at 0.30 left five pairs which were quiet across a window averaging '
    '18 standing instances and crossed 0.25/s once the same cluster '
    'reached 32, so each was a standing false alarm on the cluster the '
    'budget came from.')

DOC_REDERIVE = (
    'tools/derive-database-load-budget.py, pointed at a Prometheus that '
    'has scraped an sf-database tier for several days across a range of '
    'instance counts. Re-derive after any change that removes a polling '
    'loop, and never from a window shorter than the slowest loop.')

# Used only when there is no previous budget to carry a defaults block
# forward from. Tuning belongs in the file, not here: emitting these
# unconditionally is what silently reverted a tuned tolerance.
FALLBACK_DEFAULTS = collections.OrderedDict([
    ('tolerance_multiplier', 2.0),
    ('tolerance_floor_qps', 0.5),
    ('unbudgeted_fixed_rate_qps', 0.25),
    ('unbudgeted_fixed_rate_per_node_qps', 0.05),
])

# A few entries are arithmetic about the code rather than a measurement, and
# the measurement would be wrong to commit. GetNodeDaemonState/cluster is the
# only one today: every daemon polls its own state row at
# 1/DAEMON_STATE_POLL_INTERVAL from idle(), but the single elected cluster
# daemon polls from a loop which sleeps for ELECTED_LOOP_POLL_SECONDS, so
# the
# cluster-wide rate is 0.5 per node less 0.3. Measuring it instead gives
# whatever the cluster is running: before #3874 the elected daemon did not
# poll at all and the pair read 5/6 of its siblings, and a cluster which has
# not yet deployed that fix still does. Overriding here rather than editing
# the generated file by hand means a re-derivation does not silently revert
# to the measurement.
#
# Written as the arithmetic rather than as the two numbers it comes to,
# because the numbers are a claim about two constants in the server and
# nothing here can import them. test_code_derived_terms_match_the_daemons
# pins both against the real ones.
DAEMON_STATE_POLL_INTERVAL = 2.0      # shakenfist/daemons/daemon.py
ELECTED_LOOP_POLL_SECONDS = 5.0       # shakenfist/daemons/cluster/main.py
CODE_DERIVED_TERMS = {
    ('GetNodeDaemonState', 'cluster'): {
        'per_node_base_qps': round(1.0 / DAEMON_STATE_POLL_INTERVAL, 3),
        'cluster_base_qps': round(
            1.0 / ELECTED_LOOP_POLL_SECONDS
            - 1.0 / DAEMON_STATE_POLL_INTERVAL, 3),
    },
}


def parse_time(value):
    if value.isdigit():
        return int(value)
    return calendar.timegm(time.strptime(value, '%Y-%m-%dT%H:%M:%SZ'))


def query_range(prometheus, query, start, end, step, timeout):
    url = '%s/api/v1/query_range?%s' % (
        prometheus.rstrip('/'),
        urllib.parse.urlencode({'query': query, 'start': start, 'end': end,
                                'step': step}))
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    if payload.get('status') != 'success':
        raise RuntimeError('prometheus refused the query: %s'
                           % json.dumps(payload)[:400])
    return payload['data']['result']


def fit(xs, ys):
    """Ordinary least squares, returning (slope, intercept, r_squared)."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 0.0
    intercept = mean_y - slope * mean_x
    syy = sum((y - mean_y) ** 2 for y in ys)
    residual = sum((y - (intercept + slope * x)) ** 2
                   for x, y in zip(xs, ys))
    r2 = 1 - residual / syy if syy else 0.0
    return slope, intercept, r2


def collect(args):
    window = '%ds' % args.step
    instances = query_range(
        args.prometheus, 'sum(instances_active)',
        args.start, args.end, args.step, args.timeout)
    if not instances:
        raise RuntimeError(
            'no instances_active series in the window; without a standing '
            'instance count there is nothing to regress against')
    by_time = {int(t): float(v) for t, v in instances[0]['values']}
    spread = max(by_time.values()) - min(by_time.values())
    if spread < args.minimum_instance_spread:
        raise RuntimeError(
            'standing instance count varied by only %.1f across the window; '
            'a per-instance coefficient fitted from that is noise. Use a '
            'longer window, or one which spans some real churn.' % spread)

    series = query_range(
        args.prometheus,
        'sum by (operation, caller_daemon) '
        '(rate(database_requests_total[%s]))' % window,
        args.start, args.end, args.step, args.timeout)

    pairs = {}
    for entry in series:
        metric = entry['metric']
        key = (metric.get('operation'), metric.get('caller_daemon'))
        if None in key:
            continue
        xs, ys = [], []
        for t, v in entry['values']:
            t = int(t)
            if t in by_time:
                xs.append(by_time[t])
                ys.append(float(v))
        if len(xs) < args.minimum_samples:
            continue
        slope, intercept, r2 = fit(xs, ys)
        pairs[key] = {'slope': slope, 'intercept': intercept, 'r2': r2,
                      'mean': sum(ys) / len(ys), 'samples': len(xs)}
    return by_time, pairs


def to_entry(key, stats, nodes):
    operation, caller = key
    # The elected cluster daemon does its maintenance sweeps once for the
    # whole cluster, so its load does not scale with node count. Its own
    # daemon state poll does, because every node runs a cluster daemon
    # whether or not it holds the lock.
    singleton = caller == 'cluster' and operation != 'GetNodeDaemonState'
    base = max(0.0, stats['intercept'])
    entry = collections.OrderedDict()
    entry['operation'] = operation
    entry['caller_daemon'] = caller

    significant = (stats['r2'] >= MIN_R2
                   and stats['slope'] * SLOPE_REFERENCE_INSTANCES
                   >= MIN_SLOPE_CONTRIBUTION_QPS)
    if singleton:
        if base > 0.02:
            entry['cluster_base_qps'] = round(base, 3)
    elif base / nodes > 0.005:
        entry['per_node_base_qps'] = round(base / nodes, 3)
    if significant:
        entry['per_instance_qps'] = round(stats['slope'], 3)
    if not any(k.endswith('_qps') for k in entry):
        entry['per_node_base_qps'] = round(max(base, 0.01) / nodes, 3)

    override = CODE_DERIVED_TERMS.get(key)
    if override:
        for term in ('per_node_base_qps', 'cluster_base_qps',
                     'per_instance_qps'):
            entry.pop(term, None)
        entry.update(override)

    if caller in ('api', 'unknown', 'ctl'):
        entry['activity_coupled'] = True
    entry['measured'] = {'mean_qps': round(stats['mean'], 3),
                         'r2': round(stats['r2'], 3)}
    entry['note'] = PLACEHOLDER_NOTE
    return entry


def wrapped_block(out, key, text, indent):
    """A YAML folded block, wrapped so the file stays readable."""
    out.write('%s%s: >-\n' % (' ' * indent, key))
    out.write(textwrap.fill(
        ' '.join(text.split()), width=72,
        initial_indent=' ' * (indent + 2),
        subsequent_indent=' ' * (indent + 2)) + '\n')


def load_previous(path):
    """The budget being replaced, or None.

    Everything in a budget which is judgement rather than measurement --
    the tuned defaults, the note naming the loop behind each pair, the
    provisional marking saying a pair is a known defect rather than a
    floor -- lives only in this file. A re-derivation which does not read
    it deletes all of it, which is why the tool used to emit something
    that failed the repository's own tests.
    """
    if not path:
        return None
    if not os.path.exists(path):
        sys.stderr.write(
            'WARNING: no previous budget at %s, so every note will be a '
            'placeholder and no provisional marking will survive.\n' % path)
        return None

    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f)


def carried_forward(previous):
    """Per-pair notes and provisional markings from the previous budget."""
    if not previous:
        return {}
    return {(e.get('operation'), e.get('caller_daemon')): e
            for e in previous.get('entries') or []}


def emit(entries, by_time, args, out, coverage=None, previous=None):
    instances = sorted(by_time.values())
    carried = carried_forward(previous)

    out.write(FILE_HEADER)
    out.write('\nversion: 1\n\n')
    out.write('_doc:\n')
    out.write('  derived_from: %s\n' % args.cluster)
    out.write('  window: %s/%s\n' % (args.start_text, args.end_text))
    out.write('  samples: %d points at %ds, each a %ds rate\n'
              % (len(by_time), args.step, args.step))
    out.write('  cluster_shape:\n    nodes: %d\n' % args.nodes)
    if args.roles:
        out.write('    roles: %s\n' % args.roles)
    out.write('    standing_instances: %.0f minimum, %.0f peak, %.1f mean\n'
              % (instances[0], instances[-1],
                 sum(instances) / len(instances)))
    wrapped_block(out, 'method', DOC_METHOD, 2)
    wrapped_block(out, 'base_term_caveat', DOC_BASE_TERM_CAVEAT, 2)
    wrapped_block(out, 'coverage_caveat', DOC_COVERAGE_CAVEAT, 2)
    if coverage:
        wrapped_block(out, 'coverage_of_total', coverage_text(coverage), 2)
    wrapped_block(out, 'rederive', DOC_REDERIVE, 2)

    # The defaults are tuning, so carry the previous file's values rather
    # than reasserting the ones this tool happened to be written with.
    defaults = FALLBACK_DEFAULTS
    if previous and previous.get('defaults'):
        defaults = previous['defaults']
    out.write('\ndefaults:\n')
    for key in FALLBACK_DEFAULTS:
        # A previous file which predates a key still has to produce a
        # complete one, or the re-derivation writes a budget the schema
        # refuses to load.
        out.write('  %s: %s\n' % (key, defaults.get(key,
                                                    FALLBACK_DEFAULTS[key])))

    out.write('\nentries:\n')
    for entry in entries:
        key = (entry['operation'], entry['caller_daemon'])
        before = carried.get(key, {})
        out.write('  - operation: %s\n' % entry['operation'])
        out.write('    caller_daemon: %s\n' % entry['caller_daemon'])
        for k in ('per_node_base_qps', 'cluster_base_qps',
                  'per_instance_qps'):
            if k in entry:
                out.write('    %s: %s\n' % (k, entry[k]))
        if entry.get('activity_coupled'):
            out.write('    activity_coupled: true\n')

        # A provisional marking says "there is an open bug about this pair
        # and its level is not a floor worth defending". That is still
        # true of the pair after a re-measurement, so it survives one.
        provisional = before.get('provisional')
        if provisional:
            out.write('    provisional:\n      issue: %d\n'
                      % int(provisional['issue']))
            wrapped_block(out, 'reason', provisional['reason'], 6)

        out.write('    measured:\n      mean_qps: %s\n      r2: %s\n'
                  % (entry['measured']['mean_qps'], entry['measured']['r2']))
        wrapped_block(out, 'note', before.get('note') or entry['note'], 4)


def coverage_text(coverage):
    """What the inclusion cut kept, in the file itself.

    Computed rather than restated, because it is the one part of the _doc
    block which is a fact about this measurement and would be stale the
    moment the cut or the cluster moved.
    """
    return (
        'These %d pairs are the pairs averaging at least %.2f/s. They '
        'carry %.1f%% of measured load; the remaining %d pairs together '
        'average %.1f/s and none individually exceeds %.2f/s. An '
        'unbudgeted pair sustaining more than the unbudgeted ceiling in '
        'defaults is treated as a new poll: it fails the CI check and '
        'fires ShakenFistUnbudgetedDatabasePolling. '
        % (coverage['kept'], coverage['cut_qps'], coverage['kept_percent'],
           coverage['dropped'], coverage['dropped_qps'],
           coverage['largest_dropped_qps'])) + DOC_INCLUSION_CUT_LESSON


def main():
    parser = argparse.ArgumentParser(
        description='Re-derive the shipped sf-database load budget.')
    parser.add_argument('--prometheus', required=True,
                        help='Base URL of a Prometheus, e.g. http://p:9090')
    parser.add_argument('--start', required=True,
                        help='Window start, RFC3339 UTC or unix seconds.')
    parser.add_argument('--end', required=True, help='Window end.')
    parser.add_argument('--nodes', type=int, required=True,
                        help='Nodes in the measured cluster.')
    parser.add_argument('--cluster', default='unnamed',
                        help='Name recorded in the file\'s _doc block.')
    parser.add_argument('--roles', default='',
                        help='Role mix of the measured cluster, recorded '
                             'in _doc.cluster_shape. The base term split '
                             'is assigned rather than fitted, so a reader '
                             'needs this to judge it.')
    parser.add_argument('--previous', default=DEFAULT_PREVIOUS,
                        help='Budget to carry notes, provisional markings '
                             'and tuned defaults forward from. Pass an '
                             'empty string to carry nothing forward.')
    parser.add_argument('--step', type=int, default=1800,
                        help='Sample interval and rate window, seconds.')
    parser.add_argument('--minimum-mean-qps', type=float,
                        default=DEFAULT_MIN_MEAN_QPS,
                        help='Skip pairs quieter than this.')
    parser.add_argument('--minimum-samples', type=int, default=30,
                        help='Skip pairs with fewer usable samples.')
    parser.add_argument('--minimum-instance-spread', type=float, default=5.0,
                        help='Refuse a window with less instance churn.')
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--allow-pre-coverage-window', action='store_true',
                        help='Derive from a window predating #3708 anyway. '
                             'The result will undercount; see the docstring.')
    args = parser.parse_args()

    args.start_text, args.end_text = args.start, args.end
    args.start = parse_time(args.start)
    args.end = parse_time(args.end)
    if args.start >= args.end:
        parser.error('the window ends before it starts')
    if (args.start < parse_time(COVERAGE_EPOCH)
            and not args.allow_pre_coverage_window):
        parser.error(
            'window starts before %s, when the request counter could not '
            'see daemons co-located with MariaDB (#3708). Numbers from '
            'before then undercount. Pass --allow-pre-coverage-window if '
            'you understand that and want them anyway.' % COVERAGE_EPOCH)

    by_time, pairs = collect(args)
    kept = {k: v for k, v in pairs.items()
            if v['mean'] >= args.minimum_mean_qps}
    entries = [to_entry(k, kept[k], args.nodes)
               for k in sorted(kept, key=lambda k: -kept[k]['mean'])]

    dropped = sum(v['mean'] for k, v in pairs.items() if k not in kept)
    kept_qps = sum(v['mean'] for v in kept.values())
    coverage = {
        'kept': len(kept),
        'dropped': len(pairs) - len(kept),
        'dropped_qps': dropped,
        'cut_qps': args.minimum_mean_qps,
        'kept_percent': (100.0 * kept_qps / (kept_qps + dropped)
                         if kept_qps + dropped else 0.0),
        'largest_dropped_qps': max(
            (v['mean'] for k, v in pairs.items() if k not in kept),
            default=0.0),
    }
    sys.stderr.write(
        '%d pairs kept, %d dropped carrying %.1f/s in total (the largest '
        'dropped pair averages %.2f/s)\n'
        % (coverage['kept'], coverage['dropped'], dropped,
           coverage['largest_dropped_qps']))

    previous = load_previous(args.previous)
    carried = carried_forward(previous)
    new_pairs = [k for k in kept if k not in carried]
    sys.stderr.write(
        '%d pairs carried their note and provisional marking forward from '
        '%s; %d are new and have a placeholder note to write: %s\n'
        % (len(kept) - len(new_pairs), args.previous or 'nothing',
           len(new_pairs),
           ', '.join('%s/%s' % k for k in sorted(new_pairs)) or 'none'))
    emit(entries, by_time, args, sys.stdout, coverage=coverage,
         previous=previous)


if __name__ == '__main__':
    main()
