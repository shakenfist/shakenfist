# Copyright 2026 Michael Still and contributors

"""Tests for tools/operation-timeline.py.

The timeline tool exists to answer one question -- of the time between an
operation being enqueued and it running, which interval actually holds it --
and there are exactly three ways it can answer that question wrongly while
still printing a table full of plausible numbers.

The first is the join. The operation uuid is the *value* of a field whose
*name* is the operation type, so there is no fixed key to read; a parser
which guesses wrong silently drops whole operation types and reports the
remainder as if it were everything.

The second is the derivation of ``created_at``. The ``execution duration``
event is emitted *after* the operation runs, so its timestamp is the end of
execution and not the start of it. Forget to subtract the execution time and
creation lands after the operation's own defer events -- which is not a
rounding error, it is a negative interval, and against real data it happens
to most of them.

The third is truncation. Loki silently returns only the most recent 5000
lines for a query which asks for exactly 5000, so a truncated window looks
exactly like a complete one. That already caused one wrong measurement in
phase 9 of PLAN-queue-performance.md, so the paging, the subdivision and the
reporting of a chunk which cannot be subdivided any further are all covered
here.
"""

import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
from unittest import mock

from shakenfist.tests import base


def _load_tool(name):
    # These are standalone scripts rather than importable modules, so that
    # they run against a bundle of logs on a machine which has no Shaken
    # Fist installed. A script with a hyphen in its name cannot be imported
    # by name at all, hence the explicit loader.
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    path = os.path.join(root, 'tools', f'{name}.py')
    spec = importlib.util.spec_from_file_location(
        name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


timeline = _load_tool('operation-timeline')
queue_wait_report = _load_tool('queue-wait-report')


OP_UUID = '4a8a878d-412e-4e49-8adf-883bdebe8fc6'
OTHER_UUID = '63b660e0-c1a2-4d3c-bc69-d9d67feeb5ff'
DEP_UUID = '17e76c3b-18e9-4918-bf42-eb242e700ac2'
QUEUE = 'f6b7e913-e1ce-4635-bf91-a0a3651b8168-clusteroperation-user_waiting'


def defer_line(timestamp, uuid=OP_UUID, operation_type='node_inst_netdesc_op',
               delay=0.1, defer_count=1, waiting_on=None,
               delay_in_extra=True, program='sf-queues'):
    """Build an 'Execution deferred' echo the way eventlog.py ships one."""
    extra = {
        'waiting_on': (waiting_on if waiting_on is not None
                       else [['artifact_fetch_op', DEP_UUID]]),
        'defer_count': defer_count,
    }
    if delay_in_extra:
        extra['delay'] = delay
    record = {
        'logger_name': 'shakenfist.eventlog',
        'ts': timestamp,
        'level': 'INFO',
        'module': 'eventlog',
        'function': 'add_event_multi',
        'message': f'Execution deferred for {delay} seconds',
        'event_type': 'status',
        'duration': None,
        'extra': extra,
        operation_type: uuid,
        'program': program,
    }
    return json.dumps(record)


def execution_line(timestamp, uuid=OP_UUID,
                   operation_type='node_inst_netdesc_op', wait=1.0,
                   seconds=0.5, defer_count=0, queue_name=QUEUE,
                   program='sf-queues'):
    """Build an 'execution duration' echo the way eventlog.py ships one."""
    record = {
        'logger_name': 'shakenfist.eventlog',
        'ts': timestamp,
        'level': 'INFO',
        'module': 'eventlog',
        'function': 'add_event_multi',
        'message': 'execution duration',
        'event_type': 'usage',
        'duration': None,
        'extra': {
            'seconds': seconds,
            'wait_seconds': wait,
            'defer_count': defer_count,
            'queue_name': queue_name,
        },
        operation_type: uuid,
        'program': program,
    }
    return json.dumps(record)


def entries(*lines):
    """Wrap log lines as (loki nanosecond timestamp, line) pairs."""
    out = []
    for line in lines:
        stamp = timeline.parse_timestamp(json.loads(line)['ts'])
        out.append((int(stamp * 1e9), line))
    return out


class TimelineParseTestCase(base.ShakenFistTestCase):
    def test_reads_the_delay_from_extra(self):
        defer, reason = timeline.parse_defer_line(
            defer_line('2026-08-29T10:09:58.197Z', delay=0.4))
        self.assertIsNone(reason)
        self.assertEqual(0.4, defer.delay)
        self.assertFalse(defer.delay_from_message)
        self.assertEqual('node_inst_netdesc_op', defer.operation_type)
        self.assertEqual(OP_UUID, defer.uuid)
        self.assertEqual(1, defer.defer_count)
        self.assertEqual({'artifact_fetch_op'}, defer.waiting_on_types)

    def test_falls_back_to_the_message_prose(self):
        # Every event retained from before step 10a of
        # PLAN-queue-performance-phase-10-defer-latency.md has the delay in
        # the message and nowhere else, and that is the entire window this
        # tool was written to measure. The fallback is not garnish.
        defer, reason = timeline.parse_defer_line(
            defer_line('2026-08-29T10:09:58.197Z', delay=1.6,
                       delay_in_extra=False))
        self.assertIsNone(reason)
        self.assertEqual(1.6, defer.delay)
        self.assertTrue(defer.delay_from_message)

    def test_the_operation_type_is_the_field_name(self):
        # The join key is discovered, not hardcoded: hardcoding one
        # operation type would silently drop every other kind.
        defer, _ = timeline.parse_defer_line(
            defer_line('2026-08-29T10:09:58.197Z',
                       operation_type='node_blob_op',
                       uuid=OTHER_UUID))
        self.assertEqual('node_blob_op', defer.operation_type)
        self.assertEqual(OTHER_UUID, defer.uuid)

    def test_an_event_about_two_objects_cannot_be_joined(self):
        record = json.loads(defer_line('2026-08-29T10:09:58.197Z'))
        record['instance'] = OTHER_UUID
        defer, reason = timeline.parse_defer_line(json.dumps(record))
        self.assertIsNone(defer)
        self.assertEqual(timeline.DROP_NO_OBJECT, reason)

    def test_created_at_subtracts_the_execution_time(self):
        # The event is emitted after execute() returns, so its timestamp is
        # the end of the operation. wait_seconds is measured from the start.
        operation, reason = timeline.parse_execution_line(
            execution_line('2026-08-29T10:00:10.000Z', wait=4.0, seconds=6.0))
        self.assertIsNone(reason)
        executed = timeline.parse_timestamp('2026-08-29T10:00:10.000Z')
        self.assertAlmostEqual(executed - 6.0, operation.started_at, places=3)
        self.assertAlmostEqual(executed - 10.0, operation.created_at, places=3)

    def test_an_operation_never_dequeued_has_no_wait_to_decompose(self):
        record = json.loads(execution_line('2026-08-29T10:00:10.000Z'))
        del record['extra']['wait_seconds']
        operation, reason = timeline.parse_execution_line(json.dumps(record))
        self.assertIsNone(operation)
        self.assertEqual(timeline.DROP_NO_WAIT, reason)

    def test_tolerates_prefixed_lines(self):
        line = execution_line('2026-08-29T10:00:10.000Z')
        journal = f'Aug 29 10:00:10 sf-3 sf-queues[1903915]: {line}'
        logcli = f'2026-08-29T10:00:10Z {{job="shakenfist", host="sf-3"}} {line}'
        for candidate in (journal, logcli):
            operation, reason = timeline.parse_execution_line(candidate)
            self.assertIsNone(reason)
            self.assertEqual(OP_UUID, operation.uuid)

    def test_ignores_lines_which_are_not_events(self):
        for line in ('starting sf-queues',
                     '{"message": "Executing command", "command": "whoami"}',
                     '{"message": "execution duration", "extra": {"wait'):
            operation, reason = timeline.parse_execution_line(line)
            self.assertIsNone(operation)
            self.assertEqual(timeline.DROP_NOT_AN_EVENT, reason)


class TimelineJoinTestCase(base.ShakenFistTestCase):
    def test_decomposes_a_deferred_operation(self):
        # Created at 10:00:00, first dequeued at 10:00:01 (1.0s of initial
        # queue sit), deferred 0.1s then 0.2s, redelivered and started at
        # 10:00:03. Execution took 0.5s so the event lands at 10:00:03.5,
        # and wait_seconds is 3.0.
        defers = entries(
            defer_line('2026-08-29T10:00:01.000Z', delay=0.1, defer_count=1),
            defer_line('2026-08-29T10:00:01.500Z', delay=0.2, defer_count=2))
        executions = entries(
            execution_line('2026-08-29T10:00:03.500Z', wait=3.0, seconds=0.5,
                           defer_count=2))

        join = timeline.join_streams(defers, executions)
        self.assertEqual(1, len(join.operations))
        operation = join.operations[0]

        self.assertTrue(operation.joined)
        self.assertAlmostEqual(1.0, operation.created_to_first_dequeue,
                               places=3)
        self.assertAlmostEqual(0.3, operation.summed_defer_delay, places=3)
        self.assertAlmostEqual(1.7, operation.residual, places=3)
        self.assertEqual('artifact_fetch_op', operation.waiting_on_signature)

    def test_a_never_deferred_operation_has_no_components(self):
        join = timeline.join_streams(
            [], entries(execution_line('2026-08-29T10:00:03.500Z', wait=3.0)))
        operation = join.operations[0]
        self.assertTrue(operation.joined)
        self.assertIsNone(operation.created_to_first_dequeue)
        self.assertIsNone(operation.summed_defer_delay)
        self.assertIsNone(operation.residual)
        self.assertEqual(timeline.NEVER_DEFERRED,
                         operation.waiting_on_signature)

    def test_missing_defer_events_leave_the_join_incomplete(self):
        # The execution event says the operation deferred twice and only one
        # defer event is in the window, so the decomposition would silently
        # attribute the missing delay to the residual. Flagged, not guessed.
        join = timeline.join_streams(
            entries(defer_line('2026-08-29T10:00:01.000Z', delay=0.1,
                               defer_count=2)),
            entries(execution_line('2026-08-29T10:00:03.500Z', wait=3.0,
                                   defer_count=2)))
        self.assertFalse(join.operations[0].joined)

    def test_defer_events_with_no_execution_are_counted(self):
        join = timeline.join_streams(
            entries(defer_line('2026-08-29T10:00:01.000Z', uuid=OTHER_UUID)),
            entries(execution_line('2026-08-29T10:00:03.500Z')))
        self.assertEqual(1, join.uuids_without_execution)
        self.assertEqual(1, join.defers_without_execution)

    def test_the_program_filter_reads_the_parsed_field(self):
        # 'program' is a field inside the JSON and not a Loki stream label,
        # so {program="sf-queues"} selects nothing and returns zero rather
        # than erroring -- which reads exactly like a real absence.
        executions = entries(
            execution_line('2026-08-29T10:00:03.500Z', program='sf-net'),
            execution_line('2026-08-29T10:00:04.500Z', uuid=OTHER_UUID,
                           program='sf-queues'))
        join = timeline.join_streams([], executions, program='sf-queues')
        self.assertEqual([OTHER_UUID], [o.uuid for o in join.operations])

    def test_unusable_lines_are_counted_rather_than_vanishing(self):
        record = json.loads(execution_line('2026-08-29T10:00:03.500Z'))
        del record['extra']['wait_seconds']
        join = timeline.join_streams([], entries(json.dumps(record)))
        self.assertEqual(
            1, join.dropped_execution_lines[timeline.DROP_NO_WAIT])

    def test_a_leg_served_early_explains_a_negative_residual(self):
        # A work item which comes back before its delay has elapsed makes
        # the residual negative legitimately. Distinguishing that from a
        # broken join is the difference between a measurement and a bug.
        defers = entries(
            defer_line('2026-08-29T10:00:01.000Z', delay=1.0, defer_count=1))
        executions = entries(
            execution_line('2026-08-29T10:00:02.000Z', wait=1.5, seconds=0.5,
                           defer_count=1))
        operation = timeline.join_streams(defers, executions).operations[0]
        self.assertAlmostEqual(1.0, operation.created_to_first_dequeue,
                               places=3)
        self.assertAlmostEqual(-0.5, operation.residual, places=3)
        self.assertAlmostEqual(0.5, operation.early_redelivery, places=3)
        self.assertEqual([(1.0, 0.5)],
                         [(d, round(s, 3)) for d, s in operation.legs])


class StubLoki(timeline.LokiClient):
    """A LokiClient whose transport is a canned set of lines.

    Subclassed rather than mocked so that the paging, the deduplication and
    the ceiling detection in ``fetch()`` are the code under test.
    """

    def __init__(self, lines):
        super().__init__('http://loki.example.com', 'sfcbr')
        self.lines = sorted(lines)
        self.requests = []

    def query_range(self, selector, start, end, limit):
        # Loki truncates to the most recent ``limit`` lines and says
        # nothing about having done so. That silence is the behaviour
        # under test, so it is reproduced exactly here.
        self.requests.append((start, end))
        matched = [(stamp, line) for stamp, line in self.lines
                   if start * 1e9 <= stamp < end * 1e9]
        return matched[-limit:] if len(matched) > limit else matched

    def count(self, selector, start, end):
        return len([1 for stamp, _ in self.lines
                    if start * 1e9 <= stamp < end * 1e9])


class TimelineFetchTestCase(base.ShakenFistTestCase):
    def _lines(self, count, base_epoch):
        return [(int((base_epoch + i) * 1e9), f'line {i}')
                for i in range(count)]

    def test_pages_a_window_without_truncating(self):
        base_epoch = timeline.parse_timestamp('2026-08-29T00:00:00Z')
        client = StubLoki(self._lines(100, base_epoch))
        got, report = client.fetch(
            'selector', base_epoch, base_epoch + 3600, 1800)
        self.assertEqual(100, len(got))
        self.assertEqual(0, report.subdivided)
        self.assertFalse(report.is_truncated)
        self.assertEqual(0, report.count_mismatch)

    def test_subdivides_a_chunk_which_hits_the_ceiling(self):
        # A chunk which comes back holding exactly the limit was silently
        # cut short, so it is halved and refetched until it fits. The real
        # ceiling is 5000; it is lowered here so the test does not have to
        # manufacture five thousand log lines to reach it.
        base_epoch = timeline.parse_timestamp('2026-08-29T00:00:00Z')
        client = StubLoki(self._lines(600, base_epoch))
        with mock.patch.object(timeline, 'LINE_LIMIT', 400):
            got, report = client.fetch(
                'selector', base_epoch, base_epoch + 3600, 3600)
        self.assertEqual(600, len(got))
        self.assertLess(0, report.subdivided)
        self.assertFalse(report.is_truncated)
        self.assertEqual(0, report.count_mismatch)

    def test_reports_a_chunk_it_cannot_subdivide_any_further(self):
        # More lines inside the minimum chunk width than the ceiling
        # allows. Nothing can be done about that, so it is reported rather
        # than passed off as a complete window -- the whole point of the
        # cross-check is that the operator is told, not left to remember.
        base_epoch = timeline.parse_timestamp('2026-08-29T00:00:00Z')
        lines = [(int((base_epoch + i / 100.0) * 1e9), f'line {i}')
                 for i in range(500)]
        client = StubLoki(lines)
        with mock.patch.object(timeline, 'LINE_LIMIT', 10):
            _, report = client.fetch(
                'selector', base_epoch, base_epoch + 60, 60)
        self.assertTrue(report.is_truncated)
        self.assertEqual(500, report.metric_total)
        self.assertLess(report.count_mismatch, 0)

    def test_deduplicates_across_chunk_boundaries(self):
        base_epoch = timeline.parse_timestamp('2026-08-29T00:00:00Z')
        duplicated = self._lines(10, base_epoch) * 2
        client = StubLoki(duplicated)
        got, _ = client.fetch('selector', base_epoch, base_epoch + 60, 30)
        self.assertEqual(10, len(got))


class TimelineWindowTestCase(base.ShakenFistTestCase):
    def test_parses_durations(self):
        self.assertEqual(1800.0, timeline.parse_duration('30m'))
        self.assertEqual(21600.0, timeline.parse_duration('6h'))
        self.assertEqual(172800.0, timeline.parse_duration('2d'))
        self.assertRaises(ValueError, timeline.parse_duration, 'a while')

    def test_since_is_measured_back_from_the_end(self):
        args = timeline.build_argument_parser().parse_args(
            ['--since', '2h', '--end', '2026-08-29T12:00:00Z'])
        start, end = timeline.resolve_window(args)
        self.assertEqual(7200.0, end - start)

    def test_a_backwards_window_is_an_error(self):
        args = timeline.build_argument_parser().parse_args(
            ['--start', '2026-08-29T12:00:00Z',
             '--end', '2026-08-29T11:00:00Z'])
        self.assertRaises(ValueError, timeline.resolve_window, args)


class TimelineQueueClassTestCase(base.ShakenFistTestCase):
    def test_classification_agrees_with_queue_wait_report(self):
        # The classifier is a hand copy, because a script with a hyphen in
        # its name is not importable. This is the drift guard for that copy:
        # the two tools are read side by side and a queue class which means
        # different things in each is worse than no classification at all.
        for name in (
                'networknode-clusteroperation-user_facing',
                'any-clusteroperation-background',
                'f6b7e913-e1ce-4635-bf91-a0a3651b8168-clusteroperation-'
                'user_waiting',
                '963d4df9-2a67-4abc-ae8e-96f5c29ab5b2-network-background',
                'nonsense', ''):
            self.assertEqual(queue_wait_report.classify_queue(name),
                             timeline.classify_queue(name), name)


class TimelineMainTestCase(base.ShakenFistTestCase):
    def _run(self, client, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = timeline.main(argv, client=client)
        return code, out.getvalue()

    def test_an_empty_window_exits_cleanly(self):
        code, output = self._run(
            StubLoki([]),
            ['--start', '2026-08-29T00:00:00Z',
             '--end', '2026-08-29T00:30:00Z'])
        self.assertEqual(0, code)
        self.assertIn('No operations with a decomposable wait', output)

    def test_reports_the_decomposition_and_the_truncation_check(self):
        lines = entries(
            defer_line('2026-08-29T00:00:01.000Z', delay=0.1, defer_count=1),
            defer_line('2026-08-29T00:00:01.500Z', delay=0.2, defer_count=2),
            execution_line('2026-08-29T00:00:03.500Z', wait=3.0, seconds=0.5,
                           defer_count=2),
            execution_line('2026-08-29T00:00:04.000Z', uuid=OTHER_UUID,
                           wait=0.75))
        code, output = self._run(
            StubLoki(lines),
            ['--start', '2026-08-29T00:00:00Z',
             '--end', '2026-08-29T00:30:00Z', '--tail-threshold', '1'])
        self.assertEqual(0, code)
        self.assertIn('no chunk reached the 5000 line ceiling', output)
        self.assertIn('count_over_time', output)
        self.assertIn(timeline.CREATED_TO_DEQUEUE, output)
        self.assertIn(timeline.SUMMED_DELAY, output)
        self.assertIn(timeline.RESIDUAL, output)
        self.assertIn('Defer redelivery fidelity', output)
        self.assertIn('Tail by what it was waiting on', output)
        self.assertNotIn('not self consistent', output)

    def test_writes_a_csv_when_asked(self):
        lines = entries(
            execution_line('2026-08-29T00:00:04.000Z', wait=0.75))
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory)
        path = os.path.join(directory, 'timeline.csv')
        code, _ = self._run(
            StubLoki(lines),
            ['--start', '2026-08-29T00:00:00Z',
             '--end', '2026-08-29T00:30:00Z', '--csv', path])
        self.assertEqual(0, code)
        with open(path) as f:
            content = f.read()
        self.assertIn('created_to_first_dequeue', content)
        self.assertIn(OP_UUID, content)
