# Copyright 2026 Michael Still and contributors

"""Tests for tools/queue-wait-report.py.

The report exists to answer one question -- is a queue's wait tail queueing,
or is it something the system did on purpose -- and the way it gets that
question wrong is by conflating the three delays which all land in
``wait_seconds``. So the coverage that matters here is not the arithmetic,
it is the classification: that a deferred operation is excluded from the
undeferred columns, that a per-node queue is not counted as a per-network
one, and that the parser tolerates every kind of line the three capture
paths (Loki, a journal, a CI bundle) put in front of it. A parser that
raises on a plain text log line produces no report at all from a real
capture, and a classifier that reads uuids as distinct queues produces a
table with three hundred rows of one sample each.
"""

import importlib.util
import io
import os

from shakenfist.tests import base


def _load_report():
    # The report is a standalone script rather than an importable module,
    # so that it can be run against a bundle of logs on a machine which has
    # no Shaken Fist installed.
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(os.path.dirname(here))
    path = os.path.join(root, 'tools', 'queue-wait-report.py')
    spec = importlib.util.spec_from_file_location('queue_wait_report', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = _load_report()


NETWORKNODE = (
    '{"logger_name": "shakenfist.eventlog", "ts": "2026-08-23T17:49:41.957Z", '
    '"message": "execution duration", "event_type": "usage", '
    '"extra": {"seconds": 0.1, "wait_seconds": 2.5, "defer_count": 0, '
    '"queue_name": "networknode-clusteroperation-user_facing"}, '
    '"net_op": "1ed3668d-351c-4f86-b1aa-f86547ce1926", "program": "sf-net"}')

PER_NODE = (
    '{"message": "execution duration", "event_type": "usage", '
    '"extra": {"seconds": 0.2, "wait_seconds": 1.0, "defer_count": 0, '
    '"queue_name": "7ce66641-caa2-44ee-bb9b-6a02a21c66d5-clusteroperation-'
    'user_facing"}, '
    '"node_inst_op": "f3613565-9644-4724-9442-45d6cee49cf5", '
    '"program": "sf-queues"}')

PER_NETWORK = (
    '{"message": "execution duration", "event_type": "usage", '
    '"extra": {"seconds": 0.3, "wait_seconds": 1.5, "defer_count": 0, '
    '"queue_name": "963d4df9-2a67-4abc-ae8e-96f5c29ab5b2-network-background"}, '
    '"net_op": "e09596e9-314e-4b3f-9e7e-c4c9fe66be0e", "program": "sf-net"}')

DEFERRED = (
    '{"message": "execution duration", "event_type": "usage", '
    '"extra": {"seconds": 1.7, "wait_seconds": 15.7, "defer_count": 1, '
    '"queue_name": "7ce66641-caa2-44ee-bb9b-6a02a21c66d5-clusteroperation-'
    'user_waiting"}, '
    '"node_inst_netdesc_op": "71730e60-3a09-48d9-ac18-4463513e8bf2", '
    '"program": "sf-queues"}')

# A journal line: the JSON is preceded by a syslog style prefix.
JOURNAL_PREFIXED = (
    'Aug 23 17:49:41 sf-3 sf-queues[1903915]: ' + PER_NODE)

MALFORMED = '{"message": "execution duration", "extra": {"wait'

NOT_AN_EVENT = (
    '{"logger_name": "shakenfist.util.concurrency", '
    '"message": "Executing command", "command": "whoami", '
    '"program": "sf-queues"}')

# An event which is not a dispatcher pickup: emitted from a REST endpoint or
# a unit test, where the operation was never dequeued so has no created_at.
NO_WAIT_FIELDS = (
    '{"message": "execution duration", "event_type": "usage", '
    '"extra": {"seconds": 0.4}, '
    '"net_op": "de364ce9-2a2c-43b7-a0c2-2aef231bbd62", "program": "sf-net"}')

PLAIN_TEXT = 'starting sf-queues'


class QueueWaitParseTestCase(base.ShakenFistTestCase):
    def test_parses_a_dispatcher_event(self):
        sample = report.parse_line(NETWORKNODE)
        self.assertIsNotNone(sample)
        self.assertEqual(2.5, sample.wait)
        self.assertEqual(0.1, sample.execution)
        self.assertEqual(0, sample.defer_count)
        self.assertEqual('networknode', sample.queue_class)
        self.assertEqual('user_facing', sample.lane)
        self.assertEqual('net_op', sample.operation_type)
        self.assertEqual('sf-net', sample.program)

    def test_tolerates_a_journal_prefix(self):
        sample = report.parse_line(JOURNAL_PREFIXED)
        self.assertIsNotNone(sample)
        self.assertEqual('per-node', sample.queue_class)
        self.assertEqual('node_inst_op', sample.operation_type)

    def test_ignores_lines_which_are_not_samples(self):
        for line in (MALFORMED, NOT_AN_EVENT, NO_WAIT_FIELDS, PLAIN_TEXT, ''):
            self.assertIsNone(report.parse_line(line), line)

    def test_a_boolean_is_not_a_wait(self):
        # json.loads turns true into a bool, which is an int in Python and
        # would otherwise be accepted as a wait of 1.0 second.
        line = (
            '{"message": "execution duration", '
            '"extra": {"wait_seconds": true, "queue_name": "a-network-b"}}')
        self.assertIsNone(report.parse_line(line))


class QueueClassifyTestCase(base.ShakenFistTestCase):
    def test_classifies_each_queue_family(self):
        self.assertEqual(
            ('networknode', 'user_facing'),
            report.classify_queue('networknode-clusteroperation-user_facing'))
        self.assertEqual(
            ('per-node', 'background_high_io'),
            report.classify_queue(
                '7ce66641-caa2-44ee-bb9b-6a02a21c66d5-clusteroperation-'
                'background_high_io'))
        self.assertEqual(
            ('per-network', 'background'),
            report.classify_queue(
                '963d4df9-2a67-4abc-ae8e-96f5c29ab5b2-network-background'))

    def test_classifies_the_any_node_queue(self):
        # An artifact fetch which any node may claim is targeted at the
        # literal 'any' rather than a uuid. Reading it as 'unknown' hides a
        # whole class of work in a row nobody trusts.
        self.assertEqual(
            ('any-node', 'user_facing'),
            report.classify_queue('any-clusteroperation-user_facing'))

    def test_unparseable_queue_names_are_not_fatal(self):
        self.assertEqual(('unknown', 'unknown'), report.classify_queue(''))
        self.assertEqual(('unknown', 'unknown'), report.classify_queue(None))
        self.assertEqual(
            ('unknown', 'unknown'), report.classify_queue('rubbish'))


class QueueWaitPercentileTestCase(base.ShakenFistTestCase):
    def test_empty_is_none(self):
        self.assertIsNone(report.percentile([], 0.5))

    def test_single_sample_is_itself_at_every_percentile(self):
        for fraction in (0.5, 0.9, 0.99):
            self.assertEqual(7.0, report.percentile([7.0], fraction))

    def test_percentiles_are_observed_values(self):
        values = list(range(1, 101))
        # Nearest rank, so every answer is a value which was measured.
        self.assertEqual(51, report.percentile(values, 0.5))
        self.assertEqual(90, report.percentile(values, 0.9))
        self.assertEqual(99, report.percentile(values, 0.99))
        self.assertIn(report.percentile(values, 0.5), values)


class QueueWaitGroupTestCase(base.ShakenFistTestCase):
    def _samples(self):
        return report.read_samples(io.StringIO('\n'.join([
            NETWORKNODE, PER_NODE, PER_NETWORK, DEFERRED,
            MALFORMED, NOT_AN_EVENT, PLAIN_TEXT])))

    def test_reads_only_the_samples(self):
        self.assertEqual(4, len(self._samples()))

    def test_deferred_operations_are_excluded_from_the_undeferred_columns(
            self):
        # The whole point of the split: a dependency wait is fifteen
        # seconds of deliberate deferral, and reading it as queue wait is
        # how this report would answer the fairness question wrongly.
        groups = report.grouped(
            self._samples(), lambda s: s.operation_type)
        by_label = {g.label: g for g in groups}

        deferred = by_label['node_inst_netdesc_op']
        self.assertEqual(1, len(deferred.samples))
        self.assertEqual(0, len(deferred.undeferred))
        self.assertEqual(1, deferred.deferred_count)

        row = deferred.row()
        self.assertEqual('15.70', row[2])   # p50 over all samples
        self.assertEqual('0', row[6])       # n with defer_count == 0
        self.assertEqual('-', row[7])       # p50 over those, of which none

    def test_groups_are_ordered_by_sample_count(self):
        lines = '\n'.join([PER_NODE, PER_NODE, PER_NETWORK])
        groups = report.grouped(
            report.read_samples(io.StringIO(lines)),
            lambda s: s.queue_class)
        self.assertEqual(['per-node', 'per-network'],
                         [g.label for g in groups])
