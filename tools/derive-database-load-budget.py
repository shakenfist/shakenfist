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

The notes are not derived either: this emits a placeholder note per pair and
expects the person re-deriving to carry the previous file's notes forward
for pairs which still exist. A budget entry whose note does not say which
loop produces the traffic is a number nobody can act on when it goes red.
"""

import argparse
import calendar
import collections
import json
import sys
import textwrap
import time
import urllib.parse
import urllib.request


# database_requests_total could not see the whole cluster before #3708
# reached it. See the module docstring.
COVERAGE_EPOCH = '2026-08-11T21:00:00Z'

# A pair is worth an entry if it averages at least this much. It has to sit
# well below the budget's unbudgeted_fixed_rate_qps, because a pair which is
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

# A few entries are arithmetic about the code rather than a measurement, and
# the measurement would be wrong to commit. GetNodeDaemonState/cluster is the
# only one today: every daemon polls its own state row at
# 1/DAEMON_STATE_POLL_INTERVAL from idle(), but the single elected cluster
# daemon polls from a loop which sleeps lock.lost_event.wait(5), so the
# cluster-wide rate is 0.5 per node less 0.3. Measuring it instead gives
# whatever the cluster is running: before #3874 the elected daemon did not
# poll at all and the pair read 5/6 of its siblings, and a cluster which has
# not yet deployed that fix still does. Overriding here rather than editing
# the generated file by hand means a re-derivation does not silently revert
# to the measurement.
CODE_DERIVED_TERMS = {
    ('GetNodeDaemonState', 'cluster'): {
        'per_node_base_qps': 0.5,
        'cluster_base_qps': -0.3,
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


def emit(entries, by_time, args, out):
    instances = sorted(by_time.values())
    out.write('# Generated by tools/derive-database-load-budget.py. Review '
              'the notes and the\n# provisional markings by hand before '
              'committing: see that tool\'s docstring.\n\nversion: 1\n\n')
    out.write('_doc:\n')
    out.write('  derived_from: %s\n' % args.cluster)
    out.write('  window: %s/%s\n' % (args.start_text, args.end_text))
    out.write('  samples: %d points at %ds, each a %ds rate\n'
              % (len(by_time), args.step, args.step))
    out.write('  cluster_shape:\n    nodes: %d\n' % args.nodes)
    out.write('    standing_instances: %.0f minimum, %.0f peak, %.1f mean\n'
              % (instances[0], instances[-1],
                 sum(instances) / len(instances)))
    out.write('  method: >-\n')
    out.write(textwrap.fill(
        'Per (operation, caller_daemon) pair, an ordinary least squares fit '
        'of the pair\'s rate against the cluster\'s standing instance count '
        'over the window.', width=72, initial_indent='    ',
        subsequent_indent='    ') + '\n')
    out.write('\ndefaults:\n  tolerance_multiplier: 2.0\n'
              '  tolerance_floor_qps: 0.5\n'
              '  unbudgeted_fixed_rate_qps: 0.25\n\nentries:\n')
    for entry in entries:
        out.write('  - operation: %s\n' % entry['operation'])
        out.write('    caller_daemon: %s\n' % entry['caller_daemon'])
        for k in ('per_node_base_qps', 'cluster_base_qps',
                  'per_instance_qps'):
            if k in entry:
                out.write('    %s: %s\n' % (k, entry[k]))
        if entry.get('activity_coupled'):
            out.write('    activity_coupled: true\n')
        out.write('    measured:\n      mean_qps: %s\n      r2: %s\n'
                  % (entry['measured']['mean_qps'], entry['measured']['r2']))
        out.write('    note: >-\n')
        out.write(textwrap.fill(entry['note'], width=70,
                                initial_indent='      ',
                                subsequent_indent='      ') + '\n')


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
    sys.stderr.write(
        '%d pairs kept, %d dropped carrying %.1f/s in total (the largest '
        'dropped pair averages %.2f/s)\n'
        % (len(kept), len(pairs) - len(kept), dropped,
           max((v['mean'] for k, v in pairs.items() if k not in kept),
               default=0.0)))
    sys.stderr.write('Every note is a placeholder. Carry the previous '
                     'budget\'s notes forward before committing.\n')
    emit(entries, by_time, args, sys.stdout)


if __name__ == '__main__':
    main()
