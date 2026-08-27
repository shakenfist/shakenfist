#!/usr/bin/env python3

# Copyright 2026 Michael Still and contributors

"""Summarise cluster operation queue-wait latency from a Shaken Fist log stream.

The queue dispatcher emits one ``execution duration`` event per operation it
runs, carrying the queue-wait latency (``wait_seconds``: the time between the
operation being inserted into ``cluster_operations`` and the dispatcher
claiming it), the execution time (``seconds``), how many times the operation
was deferred before it ran (``defer_count``), and the queue it came from. See
``shakenfist/daemons/queues/workitem.py`` and
``shakenfist/daemons/network/workitem.py``, which emit it, and
``docs/operator_guide/networking/overview.md``, which documents it.

Those events cannot be read back out of the database after the fact. A cluster
operation is hard deleted thirty seconds after it reaches a final state, and
``hard_delete()`` drops the ``event_objects`` rows that join its events to it,
so within a minute of an operation completing there is nothing left to query.
What survives is the log stream: ``eventlog.add_event_multi`` echoes every
event as a log line carrying the whole ``extra`` dict, gated only by
``LOG_EVENTS_TO_LOKI``, which is on by default. Note that the echo's own
message, ``Added event``, does not survive into the emitted JSON -- pylogrus
merges the caller's fields over the record last, and one of those fields is
``message``, so the shipped record's ``message`` is the event's message. That
is why this tool matches on ``execution duration`` and why grepping a log
stream for ``Added event`` finds nothing.

The same event also carries the cross-op coalescing instrumentation, when
the build which emitted it has any: ``coalesce_outcome`` (``ran``, or which
of the three guards skipped the fold), ``coalesce_seconds`` (the wall clock
cost of the ``claim_coalescible_siblings`` call) and ``coalesce_folded`` (how
many sibling operations that call folded away). ``BaseClusterOperation.execute``
records them and both dispatchers copy them onto this event through
``op.execution_duration_extra``. Events without them are from a build
predating the instrumentation and are reported as such rather than counted as
folds which did not happen -- the distinction matters, because a fold which
matches nothing and a fold which never runs look identical otherwise, and that
is exactly how coalescing stayed broken for three months (#3878).

This reads that stream as newline delimited JSON on stdin. The same lines
reach three different places and all three work here:

    loki-query '{job="shakenfist"} |= "execution duration"' \\
        --tenant sfcbr --since 24h --limit 20000 | tools/queue-wait-report.py

    journalctl -u 'sf-*.service' -o cat | tools/queue-wait-report.py

    unzip -p bundle.zip 'journal-*.txt' | tools/queue-wait-report.py

Lines which are not JSON, and JSON objects which are not queue-wait events,
are ignored rather than being an error: every one of those sources carries
other traffic.

Read the numbers with two things in mind, both of which the report prints.
The dispatcher polls with adaptive backoff capped at IDLE_POLL_MAX_SECONDS
(2.0s, ``shakenfist/daemons/daemon.py``), so a p90 at or below that is the
poll interval rather than queueing. And ``wait_seconds`` counts deliberate
deferral too, on a schedule which depends on which dispatcher drained the
queue rather than on the operation (see DEFER_SCHEDULES below), so every
percentile is reported twice, once over all samples and once over the
operations which never deferred.
"""

import argparse
import collections
import json
import re
import sys


# The wait floor. The dispatcher's idle poll backs off to this cap between
# empty polls, so an operation enqueued against an idle worker waits up to
# this long before anybody looks. Kept in step with IDLE_POLL_MAX_SECONDS in
# shakenfist/daemons/daemon.py by hand: this tool deliberately imports
# nothing from shakenfist, so that it runs against a bundle of logs on a
# machine which has no Shaken Fist installed.
IDLE_POLL_MAX_SECONDS = 2.0

# The message which identifies a queue-wait event.
EVENT_MESSAGE = 'execution duration'

# Queue names are '<target>-<family>-<priority>'. Both families are drained
# per node, by different dispatchers: 'clusteroperation' by sf-queues (via
# Daemon.dequeue_job) and 'network' by sf-net. The target is a node uuid,
# the literal 'networknode' (the cluster-wide queue which only the elected
# network node's sf-net drains), or the literal 'any' (an artifact fetch
# which any node may take, see
# shakenfist/schema/operations/artifact_fetch_op.py) -- and it is the uuids
# which make raw queue names useless for aggregation. There is no
# per-network queue family; see get_node_network_queues() in
# shakenfist/operations/baseoperation.py, which keys the 'network' family by
# node uuid.
QUEUE_NAME_RE = re.compile(
    r'^(?P<target>.+)-(?P<family>clusteroperation|network)-(?P<lane>[^-]+)$')

UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

CLASS_NETWORKNODE = 'networknode'
CLASS_ANY_NODE = 'any-node'
CLASS_PER_NODE_CLUSTER_OP = 'per-node (cluster op)'
CLASS_PER_NODE_NETWORK = 'per-node (network)'
CLASS_UNKNOWN = 'unknown'

# What one deferral costs, by queue class. A dependency wait re-enqueues the
# operation into the future, and the delay is the dispatcher's choice rather
# than the operation's: sf-queues calls op.defer() with no argument and takes
# its flat fifteen second default (shakenfist/daemons/queues/workitem.py),
# while sf-net backs off from INITIAL_DEFER_DELAY doubling to MAX_DEFER_DELAY
# (0.1s to a 15s cap, shakenfist/daemons/network/workitem.py). So a
# defer_count of 3 is 45s of deliberate wait on one and under a second on the
# other, which is worth knowing before reading a row's defers column as
# fifteen seconds a piece.
SF_QUEUES_DEFER = 'drained by sf-queues, a flat 15s per defer'
SF_NET_DEFER = 'drained by sf-net, 0.1s per defer doubling to a 15s cap'

DEFER_SCHEDULES = collections.OrderedDict([
    (CLASS_NETWORKNODE, SF_NET_DEFER),
    (CLASS_PER_NODE_NETWORK, SF_NET_DEFER),
    (CLASS_PER_NODE_CLUSTER_OP, SF_QUEUES_DEFER),
    (CLASS_ANY_NODE, SF_QUEUES_DEFER),
])


class Sample:
    """One queue-wait observation."""

    def __init__(self, wait, execution, defer_count, queue_class, lane,
                 operation_type, program, timestamp,
                 coalesce_outcome=None, coalesce_seconds=None,
                 coalesce_folded=None):
        self.wait = wait
        self.execution = execution
        self.defer_count = defer_count
        self.queue_class = queue_class
        self.lane = lane
        self.operation_type = operation_type
        self.program = program
        self.timestamp = timestamp
        # None on every event emitted by a build predating the
        # coalescing instrumentation. Those samples are still perfectly
        # good queue-wait observations, so they stay in every other
        # table and are simply absent from the coalescing one -- which
        # is not the same as counting them as a zero.
        self.coalesce_outcome = coalesce_outcome
        self.coalesce_seconds = coalesce_seconds
        self.coalesce_folded = coalesce_folded


def classify_queue(queue_name):
    """Return (queue_class, lane) for a queue name.

    The class is one of 'networknode' (the cluster-wide queue which only the
    elected network node drains), 'any-node' (work any node may claim),
    'per-node (cluster op)' (a node's own cluster operation queue),
    'per-node (network)' (a node's own network queue), or 'unknown' for
    anything which does not parse. The lane is the priority suffix.

    Both per-node classes are keyed by *node* uuid -- the split between them
    is which dispatcher drains the queue, not what the work is about. There
    is no per-network queue.
    """
    if not queue_name:
        return CLASS_UNKNOWN, CLASS_UNKNOWN

    match = QUEUE_NAME_RE.match(queue_name)
    if not match:
        return CLASS_UNKNOWN, CLASS_UNKNOWN

    target = match.group('target')
    family = match.group('family')
    lane = match.group('lane')

    if family == 'network':
        return CLASS_PER_NODE_NETWORK, lane
    if target == 'networknode':
        return CLASS_NETWORKNODE, lane
    if target == 'any':
        return CLASS_ANY_NODE, lane
    if UUID_RE.match(target):
        return CLASS_PER_NODE_CLUSTER_OP, lane
    return CLASS_UNKNOWN, lane


def operation_type_of(record):
    """Return the operation type for an event record, or None.

    An event names the objects it is about as top level keys, so a cluster
    operation event carries exactly one key ending in '_op' whose value is
    the operation's uuid. There is no join to do and no other field which
    records the operation's class.
    """
    for key, value in record.items():
        if key.endswith('_op') and isinstance(value, str):
            return key
    return None


def load_json_object(line):
    """Return the first decodable JSON object in a line, or None.

    Retries at each subsequent '{' rather than giving up on the first,
    because some log viewers print a label set ahead of the line itself --
    Grafana's logcli emits '<ts> {job="shakenfist", host="..."} <line>', so
    the first brace opens something which is not the record at all.
    """
    start = line.find('{')
    while start >= 0:
        try:
            record = json.loads(line[start:])
        except (ValueError, TypeError):
            start = line.find('{', start + 1)
            continue
        return record if isinstance(record, dict) else None
    return None


def parse_line(line):
    """Turn one log line into a Sample, or None if it is not one.

    Tolerates anything: journal prefixes ahead of the JSON, Loki label sets
    ahead of the JSON, blank lines, plain text log lines, and JSON which is
    not an event.
    """
    record = load_json_object(line)
    if record is None:
        return None

    if record.get('message') != EVENT_MESSAGE:
        return None

    extra = record.get('extra')
    if not isinstance(extra, dict):
        return None

    wait = extra.get('wait_seconds')
    if not isinstance(wait, (int, float)) or isinstance(wait, bool):
        return None

    execution = extra.get('seconds')
    if not isinstance(execution, (int, float)) or isinstance(execution, bool):
        execution = None

    defer_count = extra.get('defer_count')
    if not isinstance(defer_count, int) or isinstance(defer_count, bool):
        defer_count = 0

    queue_class, lane = classify_queue(extra.get('queue_name'))

    coalesce_outcome = extra.get('coalesce_outcome')
    if not isinstance(coalesce_outcome, str):
        coalesce_outcome = None

    coalesce_seconds = extra.get('coalesce_seconds')
    if (not isinstance(coalesce_seconds, (int, float))
            or isinstance(coalesce_seconds, bool)):
        coalesce_seconds = None

    coalesce_folded = extra.get('coalesce_folded')
    if not isinstance(coalesce_folded, int) or isinstance(
            coalesce_folded, bool):
        coalesce_folded = None

    return Sample(
        wait=float(wait),
        execution=None if execution is None else float(execution),
        defer_count=defer_count,
        queue_class=queue_class,
        lane=lane,
        operation_type=operation_type_of(record) or 'unknown',
        program=record.get('program') or 'unknown',
        timestamp=record.get('ts'),
        coalesce_outcome=coalesce_outcome,
        coalesce_seconds=(
            None if coalesce_seconds is None else float(coalesce_seconds)),
        coalesce_folded=coalesce_folded)


def read_samples(stream):
    samples = []
    for line in stream:
        sample = parse_line(line)
        if sample is not None:
            samples.append(sample)
    return samples


def percentile(values, fraction):
    """Percentile over an unsorted list, without interpolating.

    Returns None for an empty list, and otherwise the value at the nearest
    index to ``fraction * (n - 1)``. Every value printed is therefore a
    value which was actually observed -- which matters when reading a tail
    made of a handful of samples, where an interpolated p99 is a number
    nothing measured.
    """
    if not values:
        return None
    ordered = sorted(values)
    rank = int(round(fraction * (len(ordered) - 1)))
    return ordered[rank]


def format_seconds(value):
    if value is None:
        return '-'
    return f'{value:.2f}'


def format_milliseconds(value):
    """Render a duration in milliseconds, to one decimal place.

    The wait tables' two decimal places of seconds are right for waits
    measured in seconds, and useless for the coalescing fold: a fold
    which costs five milliseconds and a fold which costs fifty both
    print as '0.00'. The point of measuring the fold at all is to
    confirm or correct the ~200 ms claim in baseoperation.py, and a
    table which can only say "under ten milliseconds" confirms nothing.
    """
    if value is None:
        return '-'
    return f'{value * 1000:.1f}'


class Group:
    """The samples in one row of one table."""

    def __init__(self, label):
        self.label = label
        self.samples = []

    def add(self, sample):
        self.samples.append(sample)

    @property
    def undeferred(self):
        return [s for s in self.samples if s.defer_count == 0]

    @property
    def deferred_count(self):
        return len([s for s in self.samples if s.defer_count > 0])

    def row(self):
        waits = [s.wait for s in self.samples]
        undeferred_waits = [s.wait for s in self.undeferred]
        executions = [s.execution for s in self.samples
                      if s.execution is not None]
        return [
            self.label,
            str(len(self.samples)),
            format_seconds(percentile(waits, 0.5)),
            format_seconds(percentile(waits, 0.9)),
            format_seconds(percentile(waits, 0.99)),
            format_seconds(max(waits) if waits else None),
            str(len(undeferred_waits)),
            format_seconds(percentile(undeferred_waits, 0.5)),
            format_seconds(percentile(undeferred_waits, 0.9)),
            format_seconds(percentile(undeferred_waits, 0.99)),
            format_seconds(max(undeferred_waits) if undeferred_waits else None),
            format_seconds(percentile(executions, 0.5)),
            format_seconds(percentile(executions, 0.9)),
            str(self.deferred_count),
        ]


HEADINGS = [
    '', 'n', 'p50', 'p90', 'p99', 'max',
    'n', 'p50', 'p90', 'p99', 'max',
    'p50', 'p90', 'defers'
]

# Which heading each column group belongs under, for the banner row. The
# spans must sum to len(HEADINGS). The execution columns are over every
# sample in the row while the wait columns are reported both ways, so the
# banner says so: deferral inflates queue wait, not execution time.
BANNERS = [
    ('', 1),
    ('wait_seconds (all)', 5),
    ('wait_seconds (defer_count == 0)', 5),
    ('exec (all)', 2),
    ('', 1),
]


def column_widths(rows):
    """Column widths wide enough for both the cells and the banner labels.

    A banner label longer than the columns it spans would otherwise push
    everything to its right out of alignment with its own heading, so any
    deficit is shared out across the columns of that span.
    """
    widths = [len(h) for h in HEADINGS]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    column = 0
    for label, span in BANNERS:
        span_width = sum(widths[column:column + span]) + (span - 1)
        deficit = len(label) - span_width
        for i in range(deficit):
            widths[column + (i % span)] += 1
        column += span

    return widths


def print_table(title, groups, footnote=None):
    print()
    print(title)
    print('-' * len(title))

    rows = [g.row() for g in groups]
    if not rows:
        print('  (no samples)')
        if footnote:
            print('  ' + footnote)
        return

    widths = column_widths(rows)

    banner = []
    column = 0
    for label, span in BANNERS:
        width = sum(widths[column:column + span]) + (span - 1)
        banner.append(label.center(width) if label else ' ' * width)
        column += span
    print(('  ' + ' '.join(banner)).rstrip())

    header = [HEADINGS[0].ljust(widths[0])]
    header.extend(h.rjust(widths[i]) for i, h in enumerate(HEADINGS)
                  if i > 0)
    print('  ' + ' '.join(header))

    for row in rows:
        line = [row[0].ljust(widths[0])]
        line.extend(cell.rjust(widths[i]) for i, cell in enumerate(row)
                    if i > 0)
        print('  ' + ' '.join(line))

    if footnote:
        print('  ' + footnote)


def grouped(samples, key):
    groups = collections.OrderedDict()
    for sample in samples:
        label = key(sample)
        if label not in groups:
            groups[label] = Group(label)
        groups[label].add(sample)
    return sorted(groups.values(), key=lambda g: -len(g.samples))


def apply_min_samples(groups, min_samples):
    """Split groups into those to print and a note about what was dropped.

    Nothing is dropped silently: a rare queue starved by a busy one is
    exactly the low-n row a reader raising --min-samples would hide without
    meaning to, so each table says what it left out.
    """
    kept = [g for g in groups if len(g.samples) >= min_samples]
    dropped = [g for g in groups if len(g.samples) < min_samples]
    if not dropped:
        return kept, None

    samples = sum(len(g.samples) for g in dropped)
    rows = 'row' if len(dropped) == 1 else 'rows'
    return kept, (f'({len(dropped)} {rows} with fewer than {min_samples} '
                  f'samples omitted, {samples} in total)')


# Every value BaseClusterOperation.execute records for coalesce_outcome.
# 'ran' means the fold's SQL was issued; the other four each name the
# guard which skipped it. 'type_not_coalescible' is an operation type
# which declares no coalescing at all -- most cluster operations, and
# the boring case -- while 'no_coalescible_tasks' is a type which could
# have coalesced and this time had nothing to. See
# shakenfist/operations/baseoperation.py, whose guard chain is the
# source of truth these strings are checked against by
# test_every_outcome_the_code_records_is_reported.
COALESCE_OUTCOMES = [
    'ran',
    'batch_size_one',
    'not_cluster_wide',
    'no_coalescible_tasks',
    'type_not_coalescible',
]


class CoalesceGroup:
    """The coalescing observations in one row."""

    def __init__(self, label):
        self.label = label
        self.samples = []

    def add(self, sample):
        self.samples.append(sample)

    @property
    def ran(self):
        return [s for s in self.samples if s.coalesce_outcome == 'ran']

    def row(self):
        durations = [s.coalesce_seconds for s in self.ran
                     if s.coalesce_seconds is not None]
        folded = sum(s.coalesce_folded for s in self.ran
                     if s.coalesce_folded is not None)
        counts = collections.Counter(
            s.coalesce_outcome for s in self.samples)
        return ([
            self.label,
            str(len(self.samples)),
            format_milliseconds(percentile(durations, 0.5)),
            format_milliseconds(percentile(durations, 0.9)),
            format_milliseconds(percentile(durations, 0.99)),
            format_milliseconds(max(durations) if durations else None),
            str(folded),
        ] + [str(counts.get(outcome, 0)) for outcome in COALESCE_OUTCOMES])


COALESCE_HEADINGS = (
    ['', 'n', 'p50 ms', 'p90 ms', 'p99 ms', 'max ms', 'folded']
    + COALESCE_OUTCOMES)


def print_coalescing_table(title, groups, footnote=None):
    """Render the coalescing table.

    Deliberately not the banner-and-span renderer the wait tables use:
    the columns here are a duration distribution and a set of outcome
    counts, which do not divide into the same column groups.
    """
    print()
    print(title)
    print('-' * len(title))

    rows = [g.row() for g in groups]
    if not rows:
        # Not "no instrumentation" -- print_coalescing_report returns
        # early in that case and never reaches here, so the only way to
        # empty this table is --min-samples, which the footnote below
        # then quantifies.
        print('  (every row fell below --min-samples)')
        if footnote:
            print('  ' + footnote)
        return

    widths = [len(h) for h in COALESCE_HEADINGS]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    header = [COALESCE_HEADINGS[0].ljust(widths[0])]
    header.extend(h.rjust(widths[i])
                  for i, h in enumerate(COALESCE_HEADINGS) if i > 0)
    print('  ' + ' '.join(header))

    for row in rows:
        line = [row[0].ljust(widths[0])]
        line.extend(cell.rjust(widths[i])
                    for i, cell in enumerate(row) if i > 0)
        print('  ' + ' '.join(line))

    if footnote:
        print('  ' + footnote)


def coalescing_groups(samples, key):
    groups = collections.OrderedDict()
    for sample in samples:
        if sample.coalesce_outcome is None:
            continue
        label = key(sample)
        if label not in groups:
            groups[label] = CoalesceGroup(label)
        groups[label].add(sample)
    return sorted(groups.values(), key=lambda g: -len(g.samples))


def print_coalescing_report(samples, min_samples):
    """Report what the cross-op coalescing fold cost and whether it ran.

    The outcome counts matter as much as the durations. Coalescing was
    dead for three months (#3878) with a green test suite and a report
    which could not have shown it, because a fold that matches nothing
    and a fold that never runs both produce no evidence at all. Here
    they are different columns.
    """
    instrumented = [s for s in samples if s.coalesce_outcome is not None]
    if not instrumented:
        print()
        print('Coalescing')
        print('----------')
        print('  (no samples carrying coalescing instrumentation -- every '
              'event in this stream predates it)')
        return

    for title, key in (
            ('Coalescing, by operation type',
             lambda s: s.operation_type),
            ('Coalescing, by queue class and priority lane',
             lambda s: f'{s.queue_class} / {s.lane}')):
        kept, footnote = apply_min_samples(
            coalescing_groups(samples, key), min_samples)
        print_coalescing_table(title, kept, footnote)

    uninstrumented = len(samples) - len(instrumented)
    if uninstrumented:
        print()
        print(f'  {uninstrumented} of {len(samples)} samples carry no '
              'coalescing instrumentation and are absent from the tables '
              'above.')
        print('  Those events come from a build predating it; they are not '
              'counted as folds which did not happen.')


def print_window(samples):
    stamps = sorted(s.timestamp for s in samples if s.timestamp)
    print(f'Samples: {len(samples)}')
    if stamps:
        print(f'Window:  {stamps[0]} to {stamps[-1]}')
    else:
        print('Window:  unknown (no timestamps in the sampled lines)')
    programs = collections.Counter(s.program for s in samples)
    print('Emitters: ' + ', '.join(
        f'{name} ({count})' for name, count in programs.most_common()))


def print_notes():
    print()
    print(f'Idle poll floor: p90 at or below {IDLE_POLL_MAX_SECONDS:.1f}s is '
          'the dispatcher poll cap (IDLE_POLL_MAX_SECONDS), not queue wait.')
    print('Deferral: a dependency wait re-enqueues an operation into the '
          'future, so read the defer_count == 0 columns before calling a')
    print('tail a queueing problem. What one defer costs is the dispatcher\'s '
          'choice, so it follows the queue class rather than the op:')
    for queue_class, schedule in DEFER_SCHEDULES.items():
        print(f'  {queue_class}: {schedule}')
    print('Backpressure: background *_high_io queues are gated off entirely '
          'while the local disk is busy, which is designed backpressure')
    print('rather than starvation -- check the node\'s disk busy metric '
          'before attributing that tail to queue order.')


def main(argv=None, stream=None):
    parser = argparse.ArgumentParser(
        description=(
            'Summarise cluster operation queue-wait latency from a stream '
            'of Shaken Fist JSON log lines on stdin.'))
    parser.add_argument(
        '--min-samples', type=int, default=1,
        help=('Omit table rows with fewer than this many samples. Rows made '
              'of a handful of observations have unreadable percentiles; '
              'each table reports how many rows and samples it omitted.'))
    args = parser.parse_args(argv)

    samples = read_samples(sys.stdin if stream is None else stream)
    if not samples:
        print('No queue-wait samples found on stdin.')
        print()
        print(f'Looking for JSON log lines whose message is '
              f'"{EVENT_MESSAGE}" and whose "extra" carries "wait_seconds". '
              'See this file\'s docstring for how to capture them.')
        return 0

    print_window(samples)

    for title, key in (
            ('By queue class and priority lane',
             lambda s: f'{s.queue_class} / {s.lane}'),
            ('By operation type', lambda s: s.operation_type),
            ('By priority lane', lambda s: s.lane)):
        kept, footnote = apply_min_samples(
            grouped(samples, key), args.min_samples)
        print_table(title, kept, footnote)

    print_coalescing_report(samples, args.min_samples)
    print_notes()
    return 0


if __name__ == '__main__':
    sys.exit(main())
