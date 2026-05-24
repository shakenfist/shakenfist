# Copyright 2019 Michael Still and contributors

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
