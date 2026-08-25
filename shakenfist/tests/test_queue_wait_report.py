# Copyright 2026 Michael Still and contributors

"""Tests for tools/queue-wait-report.py.

The report exists to answer one question -- is a queue's wait tail queueing,
or is it something the system did on purpose -- and the way it gets that
question wrong is by conflating the three delays which all land in
``wait_seconds``. So the coverage that matters here is not the arithmetic,
it is the classification: that a deferred operation is excluded from the
undeferred columns, that the two per-node queue families are told apart
(they carry different defer schedules, so mislabelling one is how a reader
mis-attributes its wait), and that the parser tolerates every kind of line
the three capture paths (Loki, a journal, a CI bundle) put in front of it.
A parser that raises on a plain text log line produces no report at all
from a real capture, and a classifier that reads uuids as distinct queues
produces a table with three hundred rows of one sample each.
"""

import importlib.util
import io
import os
import sys

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

PER_NODE_NETWORK = (
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

COALESCED = (
    '{"message": "execution duration", "event_type": "usage", '
    '"extra": {"seconds": 0.1, "wait_seconds": 0.5, "defer_count": 0, '
    '"queue_name": "networknode-clusteroperation-user_facing", '
    '"coalesce_outcome": "ran", "coalesce_seconds": 0.2, '
    '"coalesce_folded": 3}, '
    '"net_op": "2ed3668d-351c-4f86-b1aa-f86547ce1927", "program": "sf-net"}')

COALESCE_RAN_EMPTY = (
    '{"message": "execution duration", "event_type": "usage", '
    '"extra": {"seconds": 0.1, "wait_seconds": 0.5, "defer_count": 0, '
    '"queue_name": "networknode-clusteroperation-user_facing", '
    '"coalesce_outcome": "ran", "coalesce_seconds": 0.1, '
    '"coalesce_folded": 0}, '
    '"net_op": "3ed3668d-351c-4f86-b1aa-f86547ce1928", "program": "sf-net"}')

COALESCE_SKIPPED = (
    '{"message": "execution duration", "event_type": "usage", '
    '"extra": {"seconds": 0.1, "wait_seconds": 0.5, "defer_count": 0, '
    '"queue_name": "networknode-clusteroperation-user_facing", '
    '"coalesce_outcome": "batch_size_one"}, '
    '"net_op": "4ed3668d-351c-4f86-b1aa-f86547ce1929", "program": "sf-net"}')

# A journal line: the JSON is preceded by a syslog style prefix.
JOURNAL_PREFIXED = (
    'Aug 23 17:49:41 sf-3 sf-queues[1903915]: ' + PER_NODE)

# A logcli line: the stream's label set is printed ahead of the record, so
# the first '{' in the line opens the labels rather than the JSON.
LOGCLI_PREFIXED = (
    '2026-08-23T17:49:41Z {job="shakenfist", host="sf-3"} ' + PER_NODE)

# A dispatcher event with no operation object attached, which the report
# attributes to 'unknown' rather than dropping.
NO_OPERATION_KEY = (
    '{"message": "execution duration", "event_type": "usage", '
    '"extra": {"seconds": 0.5, "wait_seconds": 0.75, "defer_count": 0, '
    '"queue_name": "any-clusteroperation-background"}, "program": "sf-net"}')

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
        self.assertEqual('per-node (cluster op)', sample.queue_class)
        self.assertEqual('node_inst_op', sample.operation_type)

    def test_tolerates_a_loki_label_set_before_the_json(self):
        # Grafana's logcli prints the stream's label set ahead of the line,
        # so the first '{' opens something which is not the record. Giving
        # up there loses every line of such a capture rather than one.
        sample = report.parse_line(LOGCLI_PREFIXED)
        self.assertIsNotNone(sample)
        self.assertEqual('per-node (cluster op)', sample.queue_class)
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
        # Both families are keyed by *node* uuid; there is no per-network
        # queue. See get_node_network_queues() in
        # shakenfist/operations/baseoperation.py, whose only argument is a
        # node uuid, and which sf-net drains for its own node.
        self.assertEqual(
            ('networknode', 'user_facing'),
            report.classify_queue('networknode-clusteroperation-user_facing'))
        self.assertEqual(
            ('per-node (cluster op)', 'background_high_io'),
            report.classify_queue(
                '7ce66641-caa2-44ee-bb9b-6a02a21c66d5-clusteroperation-'
                'background_high_io'))
        self.assertEqual(
            ('per-node (network)', 'background'),
            report.classify_queue(
                '963d4df9-2a67-4abc-ae8e-96f5c29ab5b2-network-background'))

    def test_every_queue_class_has_a_defer_schedule(self):
        # A row whose class is missing from DEFER_SCHEDULES prints without
        # the one number needed to read its defers column, so adding a
        # class without a schedule should fail here rather than in a report.
        classes = {
            report.classify_queue(name)[0]
            for name in (
                'networknode-clusteroperation-user_facing',
                'any-clusteroperation-user_facing',
                '7ce66641-caa2-44ee-bb9b-6a02a21c66d5-clusteroperation-'
                'background',
                '963d4df9-2a67-4abc-ae8e-96f5c29ab5b2-network-background')}
        self.assertEqual(classes, set(report.DEFER_SCHEDULES))

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
        # Never interpolated, so every answer is a value which was
        # measured. The index is the nearest to fraction * (n - 1), which
        # is within one rank of the textbook nearest-rank definition.
        self.assertEqual(51, report.percentile(values, 0.5))
        self.assertEqual(90, report.percentile(values, 0.9))
        self.assertEqual(99, report.percentile(values, 0.99))
        self.assertIn(report.percentile(values, 0.5), values)


class QueueWaitGroupTestCase(base.ShakenFistTestCase):
    def _samples(self):
        return report.read_samples(io.StringIO('\n'.join([
            NETWORKNODE, PER_NODE, PER_NODE_NETWORK, DEFERRED,
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
        lines = '\n'.join([PER_NODE, PER_NODE, PER_NODE_NETWORK])
        groups = report.grouped(
            report.read_samples(io.StringIO(lines)),
            lambda s: s.queue_class)
        self.assertEqual(['per-node (cluster op)', 'per-node (network)'],
                         [g.label for g in groups])

    def test_a_group_reports_both_populations(self):
        # A group holding both deferred and undeferred samples is the only
        # one where the two halves of a row can disagree, and disagreeing
        # is the whole reason the second half is printed.
        lines = '\n'.join([PER_NODE, DEFERRED])
        groups = report.grouped(
            report.read_samples(io.StringIO(lines)), lambda s: s.lane)
        merged = report.Group('merged')
        for group in groups:
            for sample in group.samples:
                merged.add(sample)

        row = merged.row()
        self.assertEqual('2', row[1])       # n over all samples
        self.assertEqual('15.70', row[5])   # max over all samples
        self.assertEqual('1', row[6])       # n with defer_count == 0
        self.assertEqual('1.00', row[10])   # max over those
        self.assertEqual('1', row[13])      # how many deferred


class QueueWaitOperationTypeTestCase(base.ShakenFistTestCase):
    def test_an_event_with_no_operation_is_attributed_to_unknown(self):
        # Dropping these would silently shrink the sample count rather than
        # showing an operator that something is emitting the event without
        # naming its operation.
        sample = report.parse_line(NO_OPERATION_KEY)
        self.assertIsNotNone(sample)
        self.assertEqual('unknown', sample.operation_type)
        self.assertEqual('any-node', sample.queue_class)


class QueueWaitReportTestCase(base.ShakenFistTestCase):
    """End to end runs of the program, which is what an operator invokes."""

    def _run(self, lines, argv=None):
        stream = io.StringIO('\n'.join(lines))
        captured = io.StringIO()
        stdout = sys.stdout
        sys.stdout = captured
        try:
            code = report.main(argv=argv or [], stream=stream)
        finally:
            sys.stdout = stdout
        return code, captured.getvalue()

    def test_empty_input_is_not_a_traceback(self):
        # A capture which matched nothing is the most likely first run, and
        # it has to say so rather than failing.
        code, out = self._run([])
        self.assertEqual(0, code)
        self.assertIn('No queue-wait samples found on stdin.', out)

    def test_renders_all_three_tables(self):
        code, out = self._run(
            [NETWORKNODE, PER_NODE, PER_NODE_NETWORK, DEFERRED, PLAIN_TEXT])
        self.assertEqual(0, code)
        self.assertIn('Samples: 4', out)
        self.assertIn('By queue class and priority lane', out)
        self.assertIn('By operation type', out)
        self.assertIn('By priority lane', out)
        self.assertIn('networknode / user_facing', out)
        self.assertIn('per-node (network) / background', out)
        # The defer schedules are what make a defers column readable, so
        # they are printed with the report rather than left to the docs.
        self.assertIn('drained by sf-net', out)
        self.assertIn('drained by sf-queues', out)

    def test_columns_line_up_under_their_banners(self):
        # A banner label wider than the columns it spans used to push every
        # banner to its right off its own columns, which silently mislabels
        # the undeferred half of every row.
        _, out = self._run([NETWORKNODE, PER_NODE])
        lines = out.splitlines()
        index = lines.index('By operation type')
        banner, header = lines[index + 2], lines[index + 3]
        for label, _span in report.BANNERS:
            if not label:
                continue
            self.assertIn(label, banner)
        self.assertGreaterEqual(len(header), len(banner))
        self.assertEqual(banner, banner.rstrip())
        self.assertEqual(header.count('defers'), 1)
        self.assertEqual(
            banner.index('exec (all)') + len('exec (all)') <= len(header),
            True)

    def test_min_samples_says_what_it_dropped(self):
        # Silently dropping rows would hide exactly the rare, starved queue
        # a reader raising this flag did not mean to hide.
        _, out = self._run(
            [NETWORKNODE, PER_NODE, PER_NODE, PER_NODE_NETWORK],
            argv=['--min-samples', '2'])
        self.assertIn('per-node (cluster op) / user_facing', out)
        self.assertNotIn('networknode / user_facing', out)
        self.assertIn(
            '(2 rows with fewer than 2 samples omitted, 2 in total)', out)

    def test_min_samples_can_empty_a_table_without_hiding_that_it_did(self):
        _, out = self._run([NETWORKNODE], argv=['--min-samples', '5'])
        self.assertIn('(no samples)', out)
        self.assertIn(
            '(1 row with fewer than 5 samples omitted, 1 in total)', out)


class CoalescingParseTestCase(base.ShakenFistTestCase):
    def test_reads_the_coalescing_fields(self):
        sample = report.parse_line(COALESCED)
        self.assertEqual('ran', sample.coalesce_outcome)
        self.assertEqual(0.2, sample.coalesce_seconds)
        self.assertEqual(3, sample.coalesce_folded)

    def test_a_skipped_fold_has_an_outcome_but_no_duration(self):
        sample = report.parse_line(COALESCE_SKIPPED)
        self.assertEqual('batch_size_one', sample.coalesce_outcome)
        self.assertIsNone(sample.coalesce_seconds)
        self.assertIsNone(sample.coalesce_folded)

    def test_an_older_event_carries_none_of_them(self):
        # Not zero. An event from a build predating the instrumentation
        # says nothing about coalescing, and reading it as "the fold ran
        # and folded nothing" would invent data.
        sample = report.parse_line(NETWORKNODE)
        self.assertIsNone(sample.coalesce_outcome)
        self.assertIsNone(sample.coalesce_seconds)
        self.assertIsNone(sample.coalesce_folded)

    def test_a_boolean_is_not_a_fold_count(self):
        line = COALESCED.replace('"coalesce_folded": 3',
                                 '"coalesce_folded": true')
        self.assertIsNone(report.parse_line(line).coalesce_folded)

    def test_every_outcome_the_code_records_is_reported(self):
        # The report's outcome columns are written out by hand, so they
        # can fall behind the guards in BaseClusterOperation.execute.
        # Read the values back out of the source of truth.
        from shakenfist.operations import baseoperation
        source = open(baseoperation.__file__).read()
        for outcome in report.COALESCE_OUTCOMES:
            self.assertIn(f"self.coalesce_outcome = '{outcome}'", source)


class CoalescingReportTestCase(base.ShakenFistTestCase):
    def _run(self, lines, argv=None):
        stream = io.StringIO('\n'.join(lines))
        captured = io.StringIO()
        stdout = sys.stdout
        sys.stdout = captured
        try:
            report.main(argv=argv or [], stream=stream)
        finally:
            sys.stdout = stdout
        return captured.getvalue()

    def test_reports_the_distribution_and_the_outcomes(self):
        out = self._run([COALESCED, COALESCE_RAN_EMPTY, COALESCE_SKIPPED])
        self.assertIn('Coalescing, by operation type', out)
        self.assertIn('Coalescing, by queue class and priority lane', out)
        # Three siblings folded across the two folds which ran.
        self.assertIn('folded', out)
        self.assertIn('batch_size_one', out)

    def test_a_fold_which_ran_and_found_nothing_is_not_a_fold_which_never_ran(
            self):
        # The whole point of the outcome column. #3878 was invisible
        # because these two cases produced identical evidence.
        ran = self._run([COALESCE_RAN_EMPTY])
        skipped = self._run([COALESCE_SKIPPED])
        self.assertNotEqual(ran, skipped)

    def test_a_stream_without_the_fields_says_so(self):
        out = self._run([NETWORKNODE, PER_NODE])
        self.assertIn('no samples carrying coalescing instrumentation', out)

    def test_older_events_do_not_change_the_other_tables(self):
        # A stream with none of the new fields must report exactly what
        # it reported before the instrumentation existed, apart from the
        # coalescing section itself.
        out = self._run([NETWORKNODE, PER_NODE, PER_NODE_NETWORK, DEFERRED])
        before_coalescing = out.split('Coalescing')[0]
        self.assertIn('Samples: 4', before_coalescing)
        self.assertIn('By queue class and priority lane', before_coalescing)
        self.assertIn('By operation type', before_coalescing)
        self.assertIn('By priority lane', before_coalescing)

    def test_uninstrumented_samples_are_counted_not_hidden(self):
        out = self._run([COALESCED, NETWORKNODE])
        self.assertIn('1 of 2 samples carry no coalescing instrumentation',
                      out)
