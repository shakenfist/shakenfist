# Copyright 2019 Michael Still and contributors

import re

from shakenfist.operations.baseoperation import get_all_network_queues
from shakenfist.operations.baseoperation import get_all_node_queues
from shakenfist.operations.baseoperation import get_node_background_node_queues
from shakenfist.operations.baseoperation import get_node_network_queues
from shakenfist.tests import base


EXAMPLE_UUID = 'aabbccdd-1234-5678-abcd-000000000001'

EXPECTED_SUFFIXES = [
    'user_waiting',
    'user_facing',
    'user_facing_high_io',
    'background',
    'background_high_io',
]


class GetNodeNetworkQueuesTest(base.ShakenFistTestCase):
    def test_returns_five_entries(self):
        queues = get_node_network_queues(EXAMPLE_UUID)
        self.assertEqual(5, len(queues))

    def test_exact_order_and_values(self):
        queues = get_node_network_queues(EXAMPLE_UUID)
        expected = [f'{EXAMPLE_UUID}-network-{suffix}' for suffix in EXPECTED_SUFFIXES]
        self.assertEqual(expected, queues)

    def test_all_entries_are_strings(self):
        queues = get_node_network_queues(EXAMPLE_UUID)
        for q in queues:
            self.assertIsInstance(q, str)

    def test_priority_lane_suffixes(self):
        queues = get_node_network_queues(EXAMPLE_UUID)
        for queue, suffix in zip(queues, EXPECTED_SUFFIXES):
            self.assertTrue(
                queue.endswith(f'-network-{suffix}'),
                f'{queue!r} does not end with -network-{suffix}',
            )

    def test_node_uuid_is_embedded(self):
        queues = get_node_network_queues(EXAMPLE_UUID)
        for queue in queues:
            self.assertTrue(
                queue.startswith(f'{EXAMPLE_UUID}-'),
                f'{queue!r} does not start with node uuid',
            )


class QueueNameFormatTest(base.ShakenFistTestCase):
    """Every dequeue-side queue name must be enqueueable.

    enqueue_cluster_operation() composes queue names as
    '<target>-<family>-<priority>' (shakenfist/schema/operations/util.py),
    so a name the dequeue helpers list without a family segment can never
    match a work_queue row. Issue 3867 found exactly that: a family-less
    '<node_uuid>-background' surviving from the etcd-era work item system.
    The regex here mirrors tools/queue-wait-report.py's parser.
    """

    QUEUE_NAME_RE = re.compile(r'^.+-(clusteroperation|network)-[^-]+$')

    def test_all_node_queue_names_have_a_family_segment(self):
        for queue in (get_all_node_queues(EXAMPLE_UUID) +
                      get_all_network_queues() +
                      get_node_network_queues(EXAMPLE_UUID)):
            self.assertTrue(
                self.QUEUE_NAME_RE.match(queue),
                f'{queue!r} does not parse as <target>-<family>-<priority>, '
                'so nothing can enqueue to it',
            )

    def test_node_background_queues(self):
        self.assertEqual(
            [f'{EXAMPLE_UUID}-clusteroperation-background',
             f'{EXAMPLE_UUID}-clusteroperation-background_high_io'],
            get_node_background_node_queues(EXAMPLE_UUID))
