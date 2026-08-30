#!/usr/bin/env python3

# Copyright 2026 Michael Still and contributors

"""Reconstruct where a cluster operation's queue wait actually went.

``tools/queue-wait-report.py`` reports ``wait_seconds`` off the
``execution duration`` event. That is one scalar, and it conflates several
intervals which have entirely different causes. For an operation which ran
with ``defer_count == 1`` it is:

    (created -> first dequeue) + (the defer delay) + (redelivery -> start)

and nothing on that event says which of those terms holds the time. Phase 9
of ``docs/plans/PLAN-queue-performance.md`` observed roughly 400 of 823 first
deferrals sitting at 15-17 seconds and could not explain them, because the
#3916 back-off ladder means a first deferral now costs 0.1 seconds -- so the
time is somewhere else. This tool finds out where, by joining two event
streams which are already in Loki. No new instrumentation is required.

The join
--------

Both streams are ``Added event`` echoes from ``shakenfist/eventlog.py`` (note
that the echo's own message does not survive into the shipped JSON -- pylogrus
merges the caller's fields last and one of them is ``message`` -- which is why
this tool matches on the event messages and why grepping for ``Added event``
finds nothing).

* ``Execution deferred`` from ``BaseClusterOperation.defer()``, whose
  ``extra`` carries ``waiting_on``, ``defer_count`` and (since step 10a of
  PLAN-queue-performance-phase-10-defer-latency.md) ``delay``. Builds
  predating that field are still readable: the delay is interpolated into the
  message, and the parser falls back to it. That fallback is not optional
  garnish -- the retained history this tool was written to measure predates
  the field entirely.
* ``execution duration`` from both dispatchers, whose ``extra`` carries
  ``wait_seconds``, ``seconds``, ``defer_count`` and ``queue_name``.

The join key is the operation uuid, and finding it is the non-obvious part:
an event names the objects it is about as *top level keys*, so the operation
*type* is the field name and the uuid is its value::

    "node_inst_netdesc_op": "4a8a878d-412e-4e49-8adf-883bdebe8fc6"

There is no fixed field to read, and hardcoding one operation type would
silently drop every other kind. This tool takes the single top level key
which is not one of the log record's own fields.

Deriving creation time
----------------------

``execution_duration_extra()`` sets ``wait_seconds = start_time -
created_at`` and ``seconds = time.time() - start_time``, and the event is
emitted immediately after ``op.execute()`` returns. So the event's own
timestamp is *not* the start of execution, it is the end of it::

    created_at = event_ts - seconds - wait_seconds

Dropping the ``seconds`` term is a real error and not a rounding one: on a
long-running operation it places creation after the operation's own defer
events, and the creation-to-first-dequeue interval comes out negative. The
tool counts negative intervals and says so, precisely so that a mistake of
that shape cannot be read as a measurement.

``created_at`` is the enqueue time, not merely a creation time:
``enqueue_cluster_operation()`` writes the ``cluster_operations`` row and the
``work_queue`` row in one transaction, so there is no gap between them to
account for.

The decomposition, then, per operation:

* **total wait** -- ``wait_seconds``, exactly what queue-wait-report reports.
* **created -> first dequeue** -- first defer event timestamp minus
  ``created_at``. The first defer event is emitted by the dispatcher thread
  which first picked the work item up, so it brackets the initial queue sit.
* **summed defer delay** -- the sum of the delays across that operation's
  defer events. This is deliberate wait, on the back-off ladder in
  ``shakenfist/daemons/queues/workitem.py``.
* **unexplained residual** -- what is left. For a deferred operation this is
  redelivery-to-start: the time between the work item becoming visible again
  and a dispatcher claiming and running it. There is no event bracketing that
  interval directly, which is why it is reported as a residual rather than as
  a measurement.

An operation which never deferred has no defer events at all, so it has no
decomposition -- the whole of its wait is one queue sit. Those operations are
the baseline and are reported separately rather than being dropped or being
folded in as zeroes.

Loki rules, which are load bearing
----------------------------------

Loki caps a query at 5000 lines. Asking for more fails the request outright.
Asking for exactly 5000 succeeds and *silently* returns only the most recent
5000 lines, which is the dangerous case: a truncated window looks exactly
like a complete one, and that already caused one wrong measurement in phase
9. So this tool pages ``query_range`` in half-hour chunks, treats a chunk
which comes back holding exactly the limit as truncated, subdivides it and
retries, and reports what it had to do. A chunk still at the ceiling at the
minimum chunk size is reported as a hard truncation, loudly, at the top of
the output where it cannot be missed.

Every window is additionally cross-checked against ``count_over_time``, which
is a metric query and is not subject to the line ceiling at all. A fetched
line count which does not match the metric count means the paged fetch lost
something, and is reported.

``program`` is a field inside the JSON payload and *not* a Loki stream label,
so ``{job="shakenfist", program="sf-queues"}`` selects nothing and returns
zero rather than erroring -- which reads exactly like a real absence. Use
``--program`` here, which filters on the parsed field.

The log records on some clusters carry a ``ts`` which is stamped local time
with a ``Z`` suffix, so it can sit hours ahead of the Loki ingestion
timestamp. That offset is constant and it cancels out of every interval this
tool computes, since both streams come from the same clock. The tool measures
it and prints it rather than correcting for it, because a *varying* offset
would mean the two streams are not comparable and the reader needs to know.

Usage
-----

    tools/operation-timeline.py --start 2026-08-29T00:00:00Z \\
                                --end 2026-08-29T01:00:00Z

    tools/operation-timeline.py --since 6h --tail-threshold 15
"""

import argparse
import collections
import csv
import datetime
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_LOKI_URL = 'http://loki.home.stillhq.com:3100'
DEFAULT_TENANT = 'sfcbr'

# Loki's per-query line ceiling. Asking for more than this fails the request;
# asking for exactly this succeeds and silently returns only the most recent
# LINE_LIMIT lines. Both halves of that sentence matter, and the second half
# is why every chunk is checked against the limit.
LINE_LIMIT = 5000

# How small a chunk may get while chasing a truncated one. A chunk still at
# the ceiling at this width is reported as a hard truncation rather than
# subdivided forever.
MIN_CHUNK_SECONDS = 60

DEFER_SELECTOR = '{job="shakenfist"} |= "Execution deferred"'
EXECUTION_SELECTOR = '{job="shakenfist"} |= "execution duration"'

DEFER_MESSAGE_PREFIX = 'Execution deferred'
EXECUTION_MESSAGE = 'execution duration'

# The delay as it appears in the defer event's message. Only used when
# extra['delay'] is absent, which is every event emitted before step 10a of
# PLAN-queue-performance-phase-10-defer-latency.md -- that is, all of the
# retained history this tool was written to measure.
DEFER_DELAY_RE = re.compile(r'deferred for ([0-9]+(?:\.[0-9]+)?) seconds')

# The log record's own fields, as emitted by shakenfist_utilities' pylogrus
# formatter plus the fields eventlog.add_event_multi attaches. Everything in
# the record which is *not* one of these is an object label: the key is the
# object type and the value is its uuid. That is the join key, and there is
# no other field which records it.
RECORD_FIELDS = frozenset([
    'logger_name', 'ts', 'level', 'thread_name', 'pid', 'module', 'function',
    'message', 'exception_class', 'stack_trace', 'event_type', 'duration',
    'extra', 'program', 'fqdn', 'request_id', 'name', 'levelname', 'msg',
    'hostname', 'host', 'job', 'filename', 'lineno', 'process',
])

UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')

# Kept in step with tools/queue-wait-report.py by hand, which is the source
# of truth for what a queue name means. Both tools deliberately import
# nothing -- not from shakenfist, and not from each other, since a script
# with a hyphen in its name is not importable anyway.
QUEUE_NAME_RE = re.compile(
    r'^(?P<target>.+)-(?P<family>clusteroperation|network)-(?P<lane>[^-]+)$')

CLASS_NETWORKNODE = 'networknode'
CLASS_ANY_NODE = 'any-node'
CLASS_PER_NODE_CLUSTER_OP = 'per-node (cluster op)'
CLASS_PER_NODE_NETWORK = 'per-node (network)'
CLASS_UNKNOWN = 'unknown'

NEVER_DEFERRED = '(never deferred)'
NO_WAITING_ON = '(no waiting_on recorded)'

# Interval labels, used as table row labels and as CSV column names, so that
# a reader moving between the two does not have to translate.
TOTAL_WAIT = 'total wait'
CREATED_TO_DEQUEUE = 'created -> first dequeue'
SUMMED_DELAY = 'summed defer delay'
RESIDUAL = 'unexplained residual'


class LokiError(Exception):
    pass


def parse_timestamp(value):
    """Parse an ISO8601 timestamp into a unix epoch float.

    Accepts a trailing 'Z' as well as an explicit offset, and accepts a
    naive timestamp, which is read as UTC.
    """
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    stamp = datetime.datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp.timestamp()


DURATION_RE = re.compile(r'^([0-9]+(?:\.[0-9]+)?)([smhd])$')
DURATION_UNITS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}


def parse_duration(value):
    """Parse a Loki style duration ('30m', '6h', '2d') into seconds."""
    match = DURATION_RE.match(value.strip().lower())
    if not match:
        raise ValueError(
            f'cannot read "{value}" as a duration, expected something like '
            '30m, 6h or 2d')
    return float(match.group(1)) * DURATION_UNITS[match.group(2)]


def format_timestamp(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class ChunkReport:
    """What one paged fetch had to do to stay under the line ceiling."""

    def __init__(self, selector, start, end):
        self.selector = selector
        self.start = start
        self.end = end
        self.requests = 0
        self.subdivided = 0
        self.truncated = []
        self.lines = 0
        self.metric_total = None
        self.metric_error = None

    @property
    def is_truncated(self):
        return bool(self.truncated)

    @property
    def count_mismatch(self):
        if self.metric_total is None:
            return None
        return self.lines - self.metric_total


class LokiClient:
    def __init__(self, url, tenant, timeout=120):
        self.url = url.rstrip('/')
        self.tenant = tenant
        self.timeout = timeout

    def _get(self, path, params):
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f'{self.url}{path}?{query}',
            headers={'X-Scope-OrgID': self.tenant})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as body:
                payload = json.load(body)
        except urllib.error.HTTPError as e:
            # A missing X-Scope-OrgID header is a 401 here, and it is by far
            # the most common way to get nothing back, so name it.
            raise LokiError(
                f'Loki returned HTTP {e.code} for {path}: {e.reason}. If this '
                'is a 401, check --tenant.') from e
        except urllib.error.URLError as e:
            raise LokiError(f'cannot reach Loki at {self.url}: {e}') from e
        except ValueError as e:
            raise LokiError(f'Loki returned a response which is not JSON: {e}') from e

        if payload.get('status') != 'success':
            raise LokiError(f'Loki reported failure: {payload}')
        return payload.get('data', {})

    def query_range(self, selector, start, end, limit):
        data = self._get('/loki/api/v1/query_range', {
            'query': selector,
            'limit': str(limit),
            'start': str(int(start * 1e9)),
            'end': str(int(end * 1e9)),
            'direction': 'forward',
        })
        entries = []
        for stream in data.get('result', []):
            for stamp, line in stream.get('values', []):
                entries.append((int(stamp), line))
        return entries

    def count(self, selector, start, end):
        """Total matching lines, via a metric query.

        ``count_over_time`` is not subject to the line ceiling, so this is
        the only honest way to know whether a paged fetch saw everything.
        """
        window = max(1, int(round(end - start)))
        data = self._get('/loki/api/v1/query', {
            'query': f'sum(count_over_time({selector} [{window}s]))',
            'time': str(int(end * 1e9)),
        })
        result = data.get('result', [])
        if not result:
            return 0
        return int(float(result[0]['value'][1]))

    def fetch(self, selector, start, end, chunk_seconds):
        """Page a window, subdividing any chunk which hits the line ceiling.

        Returns (entries, report). Entries are deduplicated on (timestamp,
        line) because chunk boundaries are not guaranteed to be exclusive on
        both sides and a duplicated event would be counted twice.
        """
        report = ChunkReport(selector, start, end)
        seen = set()
        entries = []

        pending = collections.deque()
        edge = start
        while edge < end:
            stop = min(edge + chunk_seconds, end)
            pending.append((edge, stop))
            edge = stop

        while pending:
            chunk_start, chunk_end = pending.popleft()
            report.requests += 1
            got = self.query_range(selector, chunk_start, chunk_end, LINE_LIMIT)

            if len(got) >= LINE_LIMIT:
                width = chunk_end - chunk_start
                if width > MIN_CHUNK_SECONDS:
                    report.subdivided += 1
                    middle = chunk_start + width / 2
                    pending.appendleft((middle, chunk_end))
                    pending.appendleft((chunk_start, middle))
                    continue
                report.truncated.append((chunk_start, chunk_end, len(got)))

            for stamp, line in got:
                key = (stamp, line)
                if key in seen:
                    continue
                seen.add(key)
                entries.append((stamp, line))

        entries.sort()
        report.lines = len(entries)
        try:
            report.metric_total = self.count(selector, start, end)
        except LokiError as e:
            report.metric_error = str(e)
        return entries, report


def load_json_object(line):
    """Return the first decodable JSON object in a line, or None.

    Retries at each subsequent '{' rather than giving up on the first,
    because some log viewers print a label set ahead of the line itself.
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


def object_label_of(record):
    """Return (object_type, object_uuid) for an event record, or None.

    An event names the objects it is about as top level keys, so the type is
    the field name and the uuid is its value. Everything the log record
    itself contributes is in RECORD_FIELDS; anything else whose value looks
    like a uuid is an object label. An event about several objects has
    several such keys and cannot be joined on one operation, so it is
    skipped rather than guessed at.
    """
    found = []
    for key, value in record.items():
        if key in RECORD_FIELDS:
            continue
        if isinstance(value, str) and UUID_RE.match(value):
            found.append((key, value))
    if len(found) != 1:
        return None
    return found[0]


def record_timestamp(record):
    """The event's own emission time, as a unix epoch float, or None.

    The echo is written synchronously inside ``add_event_multi``, so this is
    the time the event happened rather than the time it shipped.
    """
    raw = record.get('ts')
    if not isinstance(raw, str):
        return None
    try:
        return parse_timestamp(raw)
    except ValueError:
        return None


def classify_queue(queue_name):
    """Return (queue_class, lane) for a queue name.

    Queue names embed node uuids, so raw queue names are useless for
    aggregation -- every busy node would be its own row.
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


class DeferEvent:
    """One ``Execution deferred`` observation."""

    def __init__(self, timestamp, operation_type, uuid, delay, defer_count,
                 waiting_on, delay_from_message, program):
        self.timestamp = timestamp
        self.operation_type = operation_type
        self.uuid = uuid
        self.delay = delay
        self.defer_count = defer_count
        self.waiting_on = waiting_on
        self.delay_from_message = delay_from_message
        self.program = program

    @property
    def waiting_on_types(self):
        types = set()
        for entry in self.waiting_on or []:
            if isinstance(entry, (list, tuple)) and entry:
                types.add(str(entry[0]))
        return types


# Why a line matching a selector could not be turned into an observation.
# Every one of these is counted and reported: a line which matched the
# selector and then vanished is exactly the silent loss which makes a
# truncated measurement look like a complete one.
DROP_NOT_AN_EVENT = 'not one of these events'
DROP_NO_OBJECT = 'no single operation uuid to join on'
DROP_NO_TIMESTAMP = 'no readable timestamp'
DROP_NO_WAIT = 'no wait_seconds (never dequeued by a dispatcher)'


def parse_defer_line(line):
    """Return (DeferEvent, None) or (None, reason)."""
    record = load_json_object(line)
    if record is None:
        return None, DROP_NOT_AN_EVENT

    message = record.get('message')
    if not isinstance(message, str) or not message.startswith(DEFER_MESSAGE_PREFIX):
        return None, DROP_NOT_AN_EVENT

    label = object_label_of(record)
    if label is None:
        return None, DROP_NO_OBJECT
    timestamp = record_timestamp(record)
    if timestamp is None:
        return None, DROP_NO_TIMESTAMP

    extra = record.get('extra')
    if not isinstance(extra, dict):
        extra = {}

    # Prefer the field, fall back to the prose. The field only exists on
    # builds carrying step 10a; every event older than that has the delay in
    # the message and nowhere else.
    delay = extra.get('delay')
    from_message = False
    if not isinstance(delay, (int, float)) or isinstance(delay, bool):
        match = DEFER_DELAY_RE.search(message)
        delay = float(match.group(1)) if match else None
        from_message = delay is not None

    defer_count = extra.get('defer_count')
    if not isinstance(defer_count, int) or isinstance(defer_count, bool):
        defer_count = None

    waiting_on = extra.get('waiting_on')
    if not isinstance(waiting_on, list):
        waiting_on = None

    return DeferEvent(
        timestamp=timestamp,
        operation_type=label[0],
        uuid=label[1],
        delay=None if delay is None else float(delay),
        defer_count=defer_count,
        waiting_on=waiting_on,
        delay_from_message=from_message,
        program=record.get('program') or 'unknown'), None


class Operation:
    """One operation's reconstructed timeline."""

    def __init__(self, uuid, operation_type, program, queue_name, executed_at,
                 wait, execution, defer_count):
        self.uuid = uuid
        self.operation_type = operation_type
        self.program = program
        self.queue_name = queue_name
        self.queue_class, self.lane = classify_queue(queue_name)
        self.executed_at = executed_at
        self.wait = wait
        self.execution = execution
        self.defer_count = defer_count
        self.defers = []

        # How far this record's own clock runs ahead of Loki's ingestion
        # clock, filled in by join_streams. Every interval below is a
        # difference of two log-clock stamps, so the offset cancels and
        # none of them need it. Comparing a derived time against the
        # *window* does need it, because the window is in Loki's clock:
        # on this cluster the two are ten hours apart, which silently
        # makes every such comparison false. See created_at_ingested.
        self.clock_skew = 0.0

        # The event is emitted after execute() returns, so the event's
        # timestamp is the end of the operation and not the start of it.
        self.started_at = executed_at - (execution or 0.0)
        self.created_at = self.started_at - wait

    @property
    def created_at_ingested(self):
        """created_at moved onto Loki's clock, for comparison with the window.

        Only use this against a window boundary or another Loki-clock
        time. Every interval between two events stays in the log clock,
        where the offset cancels out.
        """
        return self.created_at - self.clock_skew

    @property
    def joined(self):
        """Whether the defer events found account for the recorded count."""
        return len(self.defers) == self.defer_count

    @property
    def created_to_first_dequeue(self):
        if not self.defers:
            return None
        return self.defers[0].timestamp - self.created_at

    @property
    def summed_defer_delay(self):
        if not self.defers:
            return None
        if any(d.delay is None for d in self.defers):
            return None
        return sum(d.delay for d in self.defers)

    @property
    def residual(self):
        first = self.created_to_first_dequeue
        summed = self.summed_defer_delay
        if first is None or summed is None:
            return None
        return self.wait - first - summed

    @property
    def legs(self):
        """(requested delay, served interval) for each of this op's deferrals.

        A deferral's served interval runs from the defer event to whichever
        came next: the following defer event, or -- for the last deferral --
        the start of execution. So every leg is directly bracketed by
        events, and the only interval in the whole timeline which is not is
        the tail of the last leg.

        The served interval should never be shorter than the requested
        delay, since ``enqueue_work_item`` hides the work item for that
        long. Where it is, redelivery beat the delay, and the excess shows
        up as a negative residual. That is a measurement, not a defect in
        this tool, and ``early_redelivery`` quantifies it so the two can be
        told apart.
        """
        out = []
        for i, defer in enumerate(self.defers):
            if i + 1 < len(self.defers):
                served = self.defers[i + 1].timestamp - defer.timestamp
            else:
                served = self.started_at - defer.timestamp
            out.append((defer.delay, served))
        return out

    @property
    def early_redelivery(self):
        """Seconds by which redelivery beat the requested delay, summed."""
        total = 0.0
        for delay, served in self.legs:
            if delay is not None and served < delay:
                total += delay - served
        return total

    @property
    def waiting_on_signature(self):
        if not self.defers:
            return NEVER_DEFERRED
        types = set()
        for defer in self.defers:
            types |= defer.waiting_on_types
        if not types:
            return NO_WAITING_ON
        return '+'.join(sorted(types))


def parse_execution_line(line):
    """Return (Operation, None) or (None, reason)."""
    record = load_json_object(line)
    if record is None:
        return None, DROP_NOT_AN_EVENT

    if record.get('message') != EXECUTION_MESSAGE:
        return None, DROP_NOT_AN_EVENT

    extra = record.get('extra')
    if not isinstance(extra, dict):
        return None, DROP_NOT_AN_EVENT

    wait = extra.get('wait_seconds')
    if not isinstance(wait, (int, float)) or isinstance(wait, bool):
        # An operation constructed outside the dispatch path carries no
        # created_at and so no wait to decompose.
        return None, DROP_NO_WAIT

    label = object_label_of(record)
    if label is None:
        return None, DROP_NO_OBJECT
    timestamp = record_timestamp(record)
    if timestamp is None:
        return None, DROP_NO_TIMESTAMP

    execution = extra.get('seconds')
    if not isinstance(execution, (int, float)) or isinstance(execution, bool):
        execution = None

    defer_count = extra.get('defer_count')
    if not isinstance(defer_count, int) or isinstance(defer_count, bool):
        defer_count = 0

    return Operation(
        uuid=label[1],
        operation_type=label[0],
        program=record.get('program') or 'unknown',
        queue_name=extra.get('queue_name'),
        executed_at=timestamp,
        wait=float(wait),
        execution=None if execution is None else float(execution),
        defer_count=defer_count), None


class Join:
    """The result of joining the two streams, and how well it went."""

    def __init__(self):
        self.operations = []
        self.defers = []
        self.defers_without_execution = 0
        self.uuids_without_execution = 0
        self.multi_execution_uuids = 0
        self.delays_from_message = 0
        self.delays_unknown = 0
        self.skews = []
        self.dropped_defer_lines = collections.Counter()
        self.dropped_execution_lines = collections.Counter()


def join_streams(defer_lines, execution_lines, program=None):
    join = Join()

    by_uuid = collections.defaultdict(list)
    for stamp, line in defer_lines:
        defer, reason = parse_defer_line(line)
        if defer is None:
            join.dropped_defer_lines[reason] += 1
            continue
        if program and defer.program != program:
            continue
        join.defers.append(defer)
        join.skews.append(defer.timestamp - stamp / 1e9)
        by_uuid[defer.uuid].append(defer)
        if defer.delay is None:
            join.delays_unknown += 1
        elif defer.delay_from_message:
            join.delays_from_message += 1

    for defers in by_uuid.values():
        defers.sort(key=lambda d: d.timestamp)

    claimed = set()
    for stamp, line in execution_lines:
        operation, reason = parse_execution_line(line)
        if operation is None:
            join.dropped_execution_lines[reason] += 1
            continue
        if program and operation.program != program:
            continue
        operation.clock_skew = operation.executed_at - stamp / 1e9
        join.skews.append(operation.clock_skew)
        if operation.uuid in claimed:
            # An operation which defers itself from *inside* execute()
            # (artifact_fetch_op, net_op, node_blob_op) is dispatched
            # again afterwards, and the dispatcher emits one execution
            # event per delivery. So a uuid appearing more than once is
            # expected rather than anomalous, and each appearance is a
            # separate delivery to decompose.
            join.multi_execution_uuids += 1
        claimed.add(operation.uuid)

        # Only the defers which had already happened when this delivery
        # began belong to it. The event carries the defer_count it was
        # *delivered* with, so handing every delivery the uuid's whole
        # defer list makes all but the last one fail the join-integrity
        # check and be reported as events lost in shipping.
        operation.defers = [
            d for d in by_uuid.get(operation.uuid, [])
            if d.timestamp <= operation.started_at]
        join.operations.append(operation)

    for uuid, defers in by_uuid.items():
        if uuid not in claimed:
            join.uuids_without_execution += 1
            join.defers_without_execution += len(defers)

    return join


def percentile(values, fraction):
    """Percentile over an unsorted list, without interpolating.

    Every value printed is therefore a value which was actually observed,
    which matters when reading a tail made of a handful of samples.
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


def format_percent(value):
    if value is None:
        return '-'
    return f'{value * 100:.1f}%'


def print_table(title, headings, rows, footnote=None, empty='(no samples)'):
    """Render one table. ``footnote`` is a string or a list of them."""
    if footnote is None:
        footnotes = []
    elif isinstance(footnote, str):
        footnotes = [footnote]
    else:
        footnotes = [f for f in footnote if f]

    print()
    print(title)
    print('-' * len(title))

    if not rows:
        print(f'  {empty}')
        for line in footnotes:
            print(f'  {line}')
        return

    widths = [len(h) for h in headings]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    header = [headings[0].ljust(widths[0])]
    header.extend(h.rjust(widths[i]) for i, h in enumerate(headings) if i > 0)
    print('  ' + ' '.join(header))

    for row in rows:
        line = [row[0].ljust(widths[0])]
        line.extend(cell.rjust(widths[i]) for i, cell in enumerate(row) if i > 0)
        print('  ' + ' '.join(line))

    for line in footnotes:
        print(f'  {line}')


DECOMPOSITION_HEADINGS = ['interval', 'n', 'p50', 'p90', 'p99', 'max', 'sum', 'share']


INTERVAL_GETTERS = [
    (TOTAL_WAIT, lambda o: o.wait),
    (CREATED_TO_DEQUEUE, lambda o: o.created_to_first_dequeue),
    (SUMMED_DELAY, lambda o: o.summed_defer_delay),
    (RESIDUAL, lambda o: o.residual),
]

SHARE_FOOTNOTE = (
    'share is each interval\'s summed seconds over the summed wait of the '
    'operations it is defined for (the n column),')
SHARE_FOOTNOTE_2 = (
    'not over the whole population: an undeferred operation has no '
    'components and would dilute every share to nothing.')


def interval_series(operations):
    """For each interval, the values and the wait they should be read against.

    The denominator is deliberately the summed wait of the operations the
    interval is *defined for*, not of the whole population. An operation
    which never deferred has no components at all, so including its wait in
    the denominator would drive every component's share towards zero and
    make a table full of real time look like a table full of nothing. That
    is not a presentation preference: the tail is exactly where undeferred
    operations dominate the summed wait, so it is the one place the wrong
    denominator would silently invert the answer.
    """
    series = []
    for label, getter in INTERVAL_GETTERS:
        values = []
        denominator = 0.0
        for operation in operations:
            value = getter(operation)
            if value is None:
                continue
            values.append(value)
            denominator += operation.wait
        series.append((label, values, denominator))
    return series


def decomposition_rows(operations):
    """Rows for the interval decomposition table.

    ``share`` is the number that answers "which interval holds the time"; a
    percentile cannot, because the percentiles of three intervals are not
    percentiles of the same operations.
    """
    rows = []
    for label, values, denominator in interval_series(operations):
        if not values:
            rows.append([label, '0', '-', '-', '-', '-', '-', '-'])
            continue
        summed = sum(values)
        rows.append([
            label,
            str(len(values)),
            format_seconds(percentile(values, 0.5)),
            format_seconds(percentile(values, 0.9)),
            format_seconds(percentile(values, 0.99)),
            format_seconds(max(values)),
            format_seconds(summed),
            format_percent(summed / denominator) if denominator else '-',
        ])
    return rows


BREAKDOWN_HEADINGS = [
    '', 'n', 'wait p50', 'wait p90', 'wait max',
    'dequeue p50', 'delay p50', 'resid p50',
    'dequeue%', 'delay%', 'resid%']


def breakdown_row(label, operations):
    waits = [o.wait for o in operations]
    series = {name: (values, denominator)
              for name, values, denominator in interval_series(operations)}

    def median(name):
        return format_seconds(percentile(series[name][0], 0.5))

    def share(name):
        values, denominator = series[name]
        if not values or not denominator:
            return '-'
        return format_percent(sum(values) / denominator)

    return [
        label,
        str(len(operations)),
        format_seconds(percentile(waits, 0.5)),
        format_seconds(percentile(waits, 0.9)),
        format_seconds(max(waits) if waits else None),
        median(CREATED_TO_DEQUEUE),
        median(SUMMED_DELAY),
        median(RESIDUAL),
        share(CREATED_TO_DEQUEUE),
        share(SUMMED_DELAY),
        share(RESIDUAL),
    ]


def grouped(operations, key):
    groups = collections.OrderedDict()
    for operation in operations:
        label = key(operation)
        groups.setdefault(label, []).append(operation)
    return sorted(groups.items(), key=lambda item: -len(item[1]))


def breakdown_rows(operations, key, min_samples, top):
    """Rows for a tail breakdown, plus a footnote about what was left out.

    Nothing is dropped silently: a rare operation type stuck behind a busy
    one is exactly the low-n row a reader would hide without meaning to.
    """
    groups = grouped(operations, key)
    kept = [g for g in groups if len(g[1]) >= min_samples]
    dropped = [g for g in groups if len(g[1]) < min_samples]

    truncated = []
    if top and len(kept) > top:
        truncated = kept[top:]
        kept = kept[:top]

    rows = [breakdown_row(label, ops) for label, ops in kept]

    notes = []
    if dropped:
        samples = sum(len(ops) for _, ops in dropped)
        rows_word = 'row' if len(dropped) == 1 else 'rows'
        notes.append(f'{len(dropped)} {rows_word} below --min-samples omitted '
                     f'({samples} operations)')
    if truncated:
        samples = sum(len(ops) for _, ops in truncated)
        notes.append(f'{len(truncated)} further rows omitted by --top '
                     f'({samples} operations)')
    return rows, ('(' + '; '.join(notes) + ')') if notes else None


def print_fetch_integrity(reports, join, args):
    print()
    print('Fetch integrity')
    print('---------------')

    hard = [r for r in reports if r.is_truncated]
    for report in reports:
        print(f'  {report.selector}')
        requests = ('1 query_range request' if report.requests == 1
                    else f'{report.requests} query_range requests')
        print(f'    {report.lines} lines from {requests} over '
              f'{format_timestamp(report.start)} to '
              f'{format_timestamp(report.end)}')
        if report.subdivided:
            print(f'    {report.subdivided} chunks hit the {LINE_LIMIT} line '
                  'ceiling and were subdivided and refetched')
        else:
            print(f'    no chunk reached the {LINE_LIMIT} line ceiling')
        if report.metric_error:
            print('    count_over_time cross-check unavailable: '
                  f'{report.metric_error}')
        else:
            delta = report.count_mismatch
            verdict = 'matches' if delta == 0 else f'DIFFERS BY {delta}'
            print(f'    count_over_time says {report.metric_total} -- '
                  f'{verdict}')

    if hard:
        print()
        print('  *** TRUNCATED: the following chunks returned exactly '
              f'{LINE_LIMIT} lines at the minimum chunk width of '
              f'{MIN_CHUNK_SECONDS}s,')
        print('  *** so they were silently cut short by Loki and this report '
              'is missing events.')
        for report in hard:
            for start, end, count in report.truncated:
                print(f'  ***   {report.selector}: '
                      f'{format_timestamp(start)} to {format_timestamp(end)} '
                      f'({count} lines)')

    if join.skews:
        low = min(join.skews)
        high = max(join.skews)
        print()
        print(f'  Log clock offset from Loki ingestion: {low:.3f}s to '
              f'{high:.3f}s across {len(join.skews)} events.')
        print('  A constant offset cancels out of every interval below, '
              'since both streams share the clock. A wide')
        print('  spread would not, and would mean the two streams cannot be '
              'compared.')

    print()
    print(f'  Chunk width {args.chunk_minutes} minutes; defer events fetched '
          f'from {args.lookback_minutes} minutes before the window so that an '
          'operation')
    print('  created before it still has its early defer events.')


def print_join_integrity(join, complete, incomplete, clipped, defer_window_start):
    print()
    print('Join integrity')
    print('--------------')
    print(f'  {len(join.operations)} execution events carrying wait_seconds, '
          f'{len(join.defers)} defer events')
    for label, counts in (('execution', join.dropped_execution_lines),
                          ('defer', join.dropped_defer_lines)):
        for reason, count in sorted(counts.items()):
            print(f'  {count} {label} lines matched the selector but were '
                  f'not usable: {reason}')
    print(f'  {len(complete)} operations whose defer events account for their '
          f'recorded defer_count')

    if incomplete:
        print(f'  {len(incomplete)} operations whose defer events do NOT '
              'account for it, and are excluded from every table below:')
        print(f'    {clipped} were created before '
              f'{format_timestamp(defer_window_start)}, so their early defer '
              'events are outside the fetched window')
        print(f'    {len(incomplete) - clipped} are unexplained -- events lost '
              'in shipping, or an operation type which defers without '
              'emitting the event')

    if join.uuids_without_execution:
        print(f'  {join.uuids_without_execution} deferred operations '
              f'({join.defers_without_execution} defer events) have no '
              'execution event in the window -- they ran after it, or not yet')
    if join.multi_execution_uuids:
        print(f'  {join.multi_execution_uuids} executions were a repeat '
              'delivery of a uuid already seen in the window (an operation '
              'which defers itself from')
        print('  inside execute); each delivery is decomposed separately, '
              'against the defer events which preceded it')
    if join.delays_from_message:
        print(f'  {join.delays_from_message} defer delays were read from the '
              'message prose rather than extra["delay"] (a build predating '
              'step 10a)')
    if join.delays_unknown:
        print(f'  {join.delays_unknown} defer events carry no readable delay '
              'at all; operations holding one are excluded from the summed '
              'delay and residual')

    negative_dequeue = [o for o in complete
                        if o.created_to_first_dequeue is not None
                        and o.created_to_first_dequeue < -0.01]
    negative_residual = [o for o in complete
                         if o.residual is not None and o.residual < -0.01]
    # A negative residual is not automatically a broken join. The residual
    # is the sum of every leg's slack against its requested delay, so an
    # operation redelivered *earlier* than it asked for produces one
    # legitimately. Separating the two is the difference between a
    # measurement and a bug, so it is done here rather than left to the
    # reader.
    explained = [o for o in negative_residual
                 if o.early_redelivery >= -o.residual - 0.01]
    explained_ids = {id(o) for o in explained}
    unexplained = [o for o in negative_residual
                   if id(o) not in explained_ids]
    no_execution_time = [o for o in complete if o.execution is None]

    print()
    if negative_dequeue or unexplained:
        print('  *** The decomposition is not self consistent:')
        if negative_dequeue:
            print(f'  ***   {len(negative_dequeue)} operations have a negative '
                  'created -> first dequeue interval, which means created_at '
                  'is being derived wrongly')
        if unexplained:
            print(f'  ***   {len(unexplained)} operations have a negative '
                  'residual which early redelivery does not account for, so '
                  'the join has attributed defer events wrongly')
        print('  *** Treat every number below as suspect until that is '
              'explained.')
    else:
        print('  Creation precedes every operation\'s first defer event, so '
              'created_at is being derived consistently.')

    if explained:
        print(f'  {len(explained)} operations have a negative residual which '
              'is fully accounted for by redelivery beating the requested '
              'delay')
        print('  (see the redelivery fidelity table); the residual is the '
              'summed slack of every leg, so a leg served early makes it '
              'negative.')

    if no_execution_time:
        print(f'  {len(no_execution_time)} operations carry no execution '
              'time, so their creation time is derived from the event '
              'timestamp alone')


def write_csv(path, operations):
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'uuid', 'operation_type', 'program', 'queue_name', 'queue_class',
            'lane', 'created_at', 'started_at', 'wait_seconds',
            'execution_seconds', 'defer_count', 'created_to_first_dequeue',
            'summed_defer_delay', 'residual', 'waiting_on'])
        for o in sorted(operations, key=lambda x: -x.wait):
            writer.writerow([
                o.uuid, o.operation_type, o.program, o.queue_name or '',
                o.queue_class, o.lane,
                f'{o.created_at:.3f}', f'{o.started_at:.3f}',
                f'{o.wait:.3f}',
                '' if o.execution is None else f'{o.execution:.3f}',
                o.defer_count,
                '' if o.created_to_first_dequeue is None
                else f'{o.created_to_first_dequeue:.3f}',
                '' if o.summed_defer_delay is None
                else f'{o.summed_defer_delay:.3f}',
                '' if o.residual is None else f'{o.residual:.3f}',
                o.waiting_on_signature])


FIDELITY_HEADINGS = [
    'requested delay', 'legs', 'served p50', 'served min', 'served max',
    'slack p50', 'slack min', 'served early']


def fidelity_rows(operations):
    """Rows for the redelivery fidelity table.

    Grouped by the requested delay, because the back-off ladder means every
    operation walks the same sequence of values and the interesting question
    is whether each rung is honoured.
    """
    by_delay = collections.OrderedDict()
    for operation in operations:
        for delay, served in operation.legs:
            if delay is None:
                continue
            by_delay.setdefault(delay, []).append(served)

    rows = []
    for delay in sorted(by_delay):
        served = by_delay[delay]
        slack = [value - delay for value in served]
        rows.append([
            format_seconds(delay),
            str(len(served)),
            format_seconds(percentile(served, 0.5)),
            format_seconds(min(served)),
            format_seconds(max(served)),
            format_seconds(percentile(slack, 0.5)),
            format_seconds(min(slack)),
            str(len([v for v in slack if v < -0.001])),
        ])
    return rows


def print_notes():
    print()
    print('Notes')
    print('-----')
    print('  The residual is queue sit after redelivery: it is the summed '
          'slack of every leg, where a leg runs')
    print('  from a defer event to the next one (or to the start of '
          'execution) and its slack is how much longer')
    print('  that took than the delay which was asked for. A negative '
          'residual therefore means the work item came')
    print('  back early, not that the arithmetic is wrong -- the redelivery '
          'fidelity table separates the two.')
    print('  A residual at or below the dispatcher\'s 2.0s idle poll cap '
          '(IDLE_POLL_MAX_SECONDS in')
    print('  shakenfist/daemons/daemon.py) is the poll interval rather than '
          'contention.')
    print('  Operations which never deferred have no defer events and so no '
          'decomposition; the whole of')
    print('  their wait is one queue sit, and they are reported separately as '
          'the baseline.')


def build_argument_parser():
    parser = argparse.ArgumentParser(
        description=('Reconstruct a per operation timeline from Loki and '
                     'decompose the queue wait into the intervals '
                     'wait_seconds conflates.'))
    parser.add_argument(
        '--start', help='Window start, ISO8601 (e.g. 2026-08-29T00:00:00Z).')
    parser.add_argument(
        '--end', help='Window end, ISO8601. Defaults to now.')
    parser.add_argument(
        '--since', help=('Window length back from --end, as a duration '
                         '(30m, 6h, 2d). An alternative to --start.'))
    parser.add_argument(
        '--loki-url', default=DEFAULT_LOKI_URL,
        help=f'Loki base URL (default {DEFAULT_LOKI_URL}).')
    parser.add_argument(
        '--tenant', default=DEFAULT_TENANT,
        help=(f'Value for the X-Scope-OrgID header (default {DEFAULT_TENANT}). '
              'Omitting it entirely is an HTTP 401, not an empty result.'))
    parser.add_argument(
        '--chunk-minutes', type=float, default=30.0,
        help=('Width of each query_range page (default 30). A page which '
              f'comes back holding exactly {LINE_LIMIT} lines was silently '
              'truncated by Loki, and is subdivided and refetched.'))
    parser.add_argument(
        '--lookback-minutes', type=float, default=60.0,
        help=('How far before the window to fetch defer events (default 60), '
              'so an operation created before the window still has its early '
              'defer events.'))
    parser.add_argument(
        '--tail-threshold', type=float, default=10.0,
        help=('Operations waiting at least this many seconds form the high '
              'wait tail (default 10).'))
    parser.add_argument(
        '--program', help=('Only consider events from this program, e.g. '
                           'sf-queues. This is a JSON field and not a stream '
                           'label, so it is filtered here rather than in the '
                           'selector.'))
    parser.add_argument(
        '--min-samples', type=int, default=1,
        help='Omit breakdown rows with fewer than this many operations.')
    parser.add_argument(
        '--top', type=int, default=15,
        help='Show at most this many rows per breakdown table (0 for all).')
    parser.add_argument(
        '--csv',
        help=('Also write the per operation timeline to this path, for the '
              'operations whose join is complete (see Join integrity).'))
    return parser


def resolve_window(args, now=None):
    now = now if now is not None else datetime.datetime.now(
        datetime.timezone.utc).timestamp()
    end = parse_timestamp(args.end) if args.end else now
    if args.start:
        start = parse_timestamp(args.start)
    elif args.since:
        start = end - parse_duration(args.since)
    else:
        raise ValueError('one of --start or --since is required')
    if start >= end:
        raise ValueError('the window start must be before its end')
    return start, end


def main(argv=None, client=None):
    args = build_argument_parser().parse_args(argv)

    try:
        start, end = resolve_window(args)
    except ValueError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1

    if client is None:
        client = LokiClient(args.loki_url, args.tenant)

    chunk_seconds = max(1.0, args.chunk_minutes * 60)
    defer_start = start - args.lookback_minutes * 60

    print(f'Window:  {format_timestamp(start)} to {format_timestamp(end)} '
          f'({(end - start) / 3600:.2f}h)')
    print(f'Loki:    {args.loki_url} as tenant {args.tenant}')
    if args.program:
        print(f'Program: {args.program} (filtered on the parsed field, not '
              'the stream label)')

    try:
        defer_lines, defer_report = client.fetch(
            DEFER_SELECTOR, defer_start, end, chunk_seconds)
        execution_lines, execution_report = client.fetch(
            EXECUTION_SELECTOR, start, end, chunk_seconds)
    except LokiError as e:
        print(f'error: {e}', file=sys.stderr)
        return 1

    join = join_streams(defer_lines, execution_lines, program=args.program)
    print_fetch_integrity([defer_report, execution_report], join, args)

    if not join.operations:
        print()
        print('No operations with a decomposable wait in this window.')
        print()
        print(f'Looking for events whose message is "{EXECUTION_MESSAGE}" '
              'and whose "extra" carries "wait_seconds".')
        print('An empty result means the window is quiet, the tenant is '
              'wrong, or LOG_EVENTS_TO_LOKI is off on the cluster.')
        return 0

    complete = [o for o in join.operations if o.joined]
    incomplete = [o for o in join.operations if not o.joined]
    clipped = len([o for o in incomplete
                   if o.created_at_ingested < defer_start])
    print_join_integrity(join, complete, incomplete, clipped, defer_start)

    if not complete:
        print()
        print('No operation in this window has a decomposable timeline.')
        print()
        print('Every execution event found was excluded by the join '
              'integrity check above: the defer')
        print('events which would account for its defer_count are not in '
              'the fetched data. Raise')
        print('--lookback-minutes so operations created before the window '
              'still have their early defer')
        print('events, or widen the window so operations are not clipped '
              'at its leading edge.')
        return 0

    deferred = [o for o in complete if o.defers]
    undeferred = [o for o in complete if not o.defers]

    print_table(
        f'Decomposition of wait_seconds -- operations which deferred at least '
        f'once (n={len(deferred)})',
        DECOMPOSITION_HEADINGS, decomposition_rows(deferred),
        footnote=[SHARE_FOOTNOTE, SHARE_FOOTNOTE_2],
        empty='(no operation in this window deferred)')

    print_table(
        f'Baseline -- operations which never deferred (n={len(undeferred)})',
        DECOMPOSITION_HEADINGS, decomposition_rows(undeferred),
        footnote=('the whole of this wait is one queue sit, so there is '
                  'nothing to decompose'),
        empty='(every operation in this window deferred)')

    # Sorted numerically rather than by label: phase 9's finding was about
    # first deferrals specifically, so the reader wants the counts in order
    # and 'defer_count 10' sorts before 'defer_count 2' as a string.
    by_count = sorted(grouped(complete, lambda o: o.defer_count),
                      key=lambda item: item[0])
    print_table(
        'By defer count', BREAKDOWN_HEADINGS,
        [breakdown_row(f'defer_count {count}', ops)
         for count, ops in by_count if len(ops) >= args.min_samples],
        footnote=[SHARE_FOOTNOTE, SHARE_FOOTNOTE_2])

    print_table(
        'Defer redelivery fidelity -- was each rung of the back-off ladder '
        'honoured?',
        FIDELITY_HEADINGS, fidelity_rows(deferred),
        footnote=[
            'a leg runs from a defer event to whatever came next: the '
            'following defer event, or the start of execution.',
            'slack is served minus requested, so it is the queue sit after '
            'redelivery -- and the residual above is the summed slack.',
            'a negative slack means the work item came back before its delay '
            'had elapsed.'],
        empty='(no operation in this window deferred)')

    tail = [o for o in complete if o.wait >= args.tail_threshold]
    print()
    print(f'High wait tail: {len(tail)} of {len(complete)} operations waited '
          f'at least {args.tail_threshold:.2f}s '
          f'({len(tail) / len(complete) * 100:.1f}%)')

    if not tail:
        print('  Nothing in this window reached the threshold; lower '
              '--tail-threshold to see the shape of what there is.')
    else:
        print_table(
            f'Tail decomposition (wait >= {args.tail_threshold:.2f}s, '
            f'n={len(tail)})',
            DECOMPOSITION_HEADINGS, decomposition_rows(tail),
            footnote=[SHARE_FOOTNOTE, SHARE_FOOTNOTE_2])

        for title, key in (
                ('Tail by operation type', lambda o: o.operation_type),
                ('Tail by queue class and priority lane',
                 lambda o: f'{o.queue_class} / {o.lane}'),
                ('Tail by what it was waiting on',
                 lambda o: o.waiting_on_signature)):
            rows, footnote = breakdown_rows(
                tail, key, args.min_samples, args.top)
            print_table(title, BREAKDOWN_HEADINGS, rows,
                        footnote=[footnote, SHARE_FOOTNOTE, SHARE_FOOTNOTE_2])

    print_notes()

    if args.csv:
        write_csv(args.csv, complete)
        print()
        print(f'Wrote {len(complete)} operation timelines to {args.csv}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
