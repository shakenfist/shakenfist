# Copyright 2026 Michael Still and contributors
#
# tools/queue-wait-report.py and tools/operation-timeline.py parse the
# events BaseClusterOperation emits: ``execution duration`` from both
# dispatchers and ``Execution deferred`` from ``defer()``. Their own tests
# parse hand-written fixtures, and the emitter's tests pin the key names
# separately, so renaming a key in ``execution_duration_extra()`` or
# ``defer()`` would mean updating one test while both tool suites stayed
# green (#4642). This module closes that gap with one round trip: a real
# NetOp emits each event through the real event echo and the real JSON
# formatter, and the tools' parsers read the result.
#
# The echo shape the tools rely on -- the event message as the record's
# top-level ``message``, the operation as a key named by its type, and
# the payload under ``extra`` -- is a product of the logging library's
# field merge, so it is pinned here as well rather than assumed.
import importlib.util
import json
import logging
import os
from unittest import mock

from shakenfist_utilities import logs  # noreorder

from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist import eventlog
from shakenfist import logship
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.operations.net_op import NetOp
from shakenfist.schema.operations import net_op as net_op_schema
from shakenfist.tests import base


def _load_tool(name):
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    path = os.path.join(root, 'tools', name)
    spec = importlib.util.spec_from_file_location(
        name.replace('-', '_').replace('.py', ''), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


timeline = _load_tool('operation-timeline.py')
report = _load_tool('queue-wait-report.py')


OP_UUID = '11111111-1111-4111-8111-111111111111'
DEP_UUID = '77777777-7777-4777-8777-777777777777'
NETWORK_UUID = '22222222-2222-4222-8222-222222222222'
NODE_UUID = '55555555-5555-4555-8555-555555555555'
QUEUE = f'{NODE_UUID}-network-user_facing'
CREATED_AT = 100.0
STARTED_AT = 160.0
ENDED_AT = 160.5


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


def _make_net_op(uuid=OP_UUID):
    op = NetOp({
        'uuid': uuid,
        'network_uuid': NETWORK_UUID,
        'node_uuid': NODE_UUID,
        'priority': 'user_facing',
        'request_id': None,
        'depends_on': None,
        'runs_after': None,
        'tasks': ['network_ensure_mesh'],
        'version': net_op_schema.current_version,
    })
    op._BaseClusterOperation__created_at = CREATED_AT
    op.queue_name = QUEUE
    return op


class EmitterRoundTripTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # The base class replaces add_event_multi with a mock; the echo
        # under test lives inside the real one.
        self.mock_add_event_multi.stop()

        # The echo is what the log shipper sends to Loki, formatted by
        # the same JsonFormatter the shipper installs. Capture it from a
        # logger of the library's class, wrapped in its adapter, which is
        # what eventlog.LOG is in a daemon.
        self.handler = _CaptureHandler()
        self.handler.setFormatter(logship._build_formatter())
        logger = logs.SyslogLogger('shakenfist.tests.round_trip')
        logger.propagate = False
        logger.addHandler(self.handler)
        for target, value in (
                ('shakenfist.eventlog.LOG', logs.SyslogAdapter(logger)),
                ('shakenfist.eventlog.eventlog_spool.enqueue', mock.MagicMock()),
                ('shakenfist.eventlog.config', mock.MagicMock(
                    LOG_EVENTS_TO_LOKI=True, NODE_NAME='test-node'))):
            patcher = mock.patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.assertIs(eventlog.LOG.logger, logger)

    def _only_line(self):
        self.assertEqual(1, len(self.handler.lines), self.handler.lines)
        return self.handler.lines[0]

    def _emit_execution(self, op):
        with mock.patch('shakenfist.operations.baseoperation.time.time',
                        return_value=ENDED_AT):
            extra = op.execution_duration_extra(STARTED_AT, op.queue_name)
        op.add_event(EVENT_TYPE_USAGE, 'execution duration', extra=extra)
        return extra, self._only_line()

    def _emit_defer(self, op, delay):
        with mock.patch.object(BaseClusterOperation, '_state_update'), \
                mock.patch('shakenfist.operations.baseoperation.mariadb.'
                           'enqueue_work_item'):
            op.defer(waiting_on=[_make_net_op(DEP_UUID)], delay=delay)
        return self._only_line()

    def test_the_echo_has_the_shape_the_tools_assume(self):
        op = _make_net_op()
        _, line = self._emit_execution(op)

        record = json.loads(line)
        # The event's message, not the logger's 'Added event' text, is
        # the record's message: the adapter's fields win the merge.
        self.assertEqual('execution duration', record['message'])
        self.assertEqual(EVENT_TYPE_USAGE, record['event_type'])
        # The operation is a top-level key named by its type, which is
        # the only way either tool can tell which operation an event is
        # about.
        self.assertEqual(OP_UUID, record['net_op'])
        self.assertEqual(('net_op', OP_UUID), timeline.object_label_of(record))
        self.assertIsInstance(record['extra'], dict)
        self.assertIsNotNone(timeline.record_timestamp(record))
        self.assertIn('program', record)

    def test_an_execution_event_round_trips_through_both_tools(self):
        op = _make_net_op()
        op.current_defer_count = 2
        op.coalesce_outcome = 'ran'
        op.coalesce_seconds = 0.25
        op.coalesce_folded = 0
        extra, line = self._emit_execution(op)

        operation, reason = timeline.parse_execution_line(line)
        self.assertIsNone(reason)
        self.assertEqual(OP_UUID, operation.uuid)
        self.assertEqual('net_op', operation.operation_type)
        self.assertEqual(STARTED_AT - CREATED_AT, operation.wait)
        self.assertEqual(ENDED_AT - STARTED_AT, operation.execution)
        self.assertEqual(2, operation.defer_count)
        self.assertEqual(QUEUE, operation.queue_name)
        self.assertEqual(
            (timeline.CLASS_PER_NODE_NETWORK, 'user_facing'),
            (operation.queue_class, operation.lane))

        sample = report.parse_line(line)
        self.assertIsNotNone(sample)
        self.assertEqual(STARTED_AT - CREATED_AT, sample.wait)
        self.assertEqual(ENDED_AT - STARTED_AT, sample.execution)
        self.assertEqual(2, sample.defer_count)
        self.assertEqual('net_op', sample.operation_type)
        self.assertEqual(
            (report.CLASS_PER_NODE_NETWORK, 'user_facing'),
            (sample.queue_class, sample.lane))
        self.assertEqual('ran', sample.coalesce_outcome)
        self.assertEqual(0.25, sample.coalesce_seconds)
        # Zero siblings folded is a measurement, not an absence.
        self.assertEqual(0, sample.coalesce_folded)

        # And nothing the emitter wrote was lost on the way: every key
        # of the payload survived the echo verbatim.
        self.assertEqual(extra, json.loads(line)['extra'])

    def test_a_defer_event_round_trips_through_the_timeline_tool(self):
        op = _make_net_op()
        op.current_defer_count = 2
        line = self._emit_defer(op, delay=0.4)

        defer, reason = timeline.parse_defer_line(line)
        self.assertIsNone(reason)
        self.assertEqual(OP_UUID, defer.uuid)
        self.assertEqual('net_op', defer.operation_type)
        self.assertEqual(0.4, defer.delay)
        # From the field, not recovered from the message prose.
        self.assertFalse(defer.delay_from_message)
        self.assertEqual(3, defer.defer_count)
        self.assertEqual({'net_op'}, defer.waiting_on_types)
        self.assertIsNotNone(defer.timestamp)

    def test_each_tool_ignores_the_other_tools_event(self):
        # The selectors overlap in nothing: a defer event is not a
        # sample to the report, and is not an execution to the timeline.
        op = _make_net_op()
        line = self._emit_defer(op, delay=0.1)

        self.assertIsNone(report.parse_line(line))
        operation, reason = timeline.parse_execution_line(line)
        self.assertIsNone(operation)
        self.assertEqual(timeline.DROP_NOT_AN_EVENT, reason)
