import json
import time

from testtools import content

from shakenfist_ci import base


# NOTE(mikal): yes, I know not all objects are represented here yet.
class TestEvents(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'events'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net_one = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net-one' % self.namespace,
            provide_dns=True)
        self.addDetail(
            'net_one',
            content.text_content(json.dumps(self.net_one, indent=4,
                                            sort_keys=True)))
        self._await_networks_ready([self.net_one['uuid']])

    # NOTE(mikal): needs ArtifactsUrlRefEndpoint implemented first
    # def test_artifact_events(self):
    #     a = self.test_client.get_artifact(base.CLUSTER_CI_IMAGE)
    #     self.assertNotEqual(0, len(self.test_client.get_artifact_events(a['uuid'])))

    def test_network_events(self):
        self.assertNotEqual(
            0, len(self.test_client.get_network_events(self.net_one['uuid'])))

    def test_network_events_are_newest_first(self):
        """Events come back ordered, which the storage layer has to arrange.

        The order is produced by an ORDER BY against a denormalised copy
        of each event's timestamp on its object-reference row, because
        that is the only column an index can carry behind the object key.
        Nothing else checks that the copy is written: a read whose rows
        all sat at the column default would still return a plausible
        looking page, just not in time order.
        """
        # Events are eventually consistent: a daemon enqueues into a
        # local spool which its drainer ships in ~100 ms batches, so
        # "the network is ready" does not mean every event for it is
        # queryable yet. Poll for more than one so there is an order to
        # check.
        events = []
        deadline = time.time() + 30
        while time.time() < deadline:
            events = self.test_client.get_network_events(
                self.net_one['uuid'])
            if len(events) > 1:
                break
            time.sleep(1)

        self.addDetail('events', content.text_content(json.dumps(
            events, indent=4, sort_keys=True, default=str)))
        self.assertTrue(
            len(events) > 1,
            'network emitted fewer than two queryable events')

        timestamps = [e['timestamp'] for e in events]
        self.assertEqual(
            sorted(timestamps, reverse=True), timestamps,
            'network events are not newest first')

    def test_instance_events(self):
        inst1 = self.create_instance(
            'test-instance-events', 1, 1024,
            [
                {
                    'network_uuid': self.net_one['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self.addDetail(
            'inst1',
            content.text_content(json.dumps(inst1, indent=4, sort_keys=True)))

        # Wait for the instance agent to report in
        self._await_instance_ready(inst1['uuid'])

        events = self.test_client.get_instance_events(inst1['uuid'])
        self.assertNotEqual(0, len(events))

        # A booting instance emits well over a hundred events, so this is
        # the one object in the suite whose default page is genuinely a
        # page rather than its whole history.
        timestamps = [e['timestamp'] for e in events]
        self.assertEqual(
            sorted(timestamps, reverse=True), timestamps,
            'instance events are not newest first')

        # The filtered read takes a different query plan to the
        # unfiltered one, so its ordering is a separate claim.
        mutations = self.test_client.get_instance_events(
            inst1['uuid'], event_type='mutate', limit=1000)
        mutation_timestamps = [e['timestamp'] for e in mutations]
        self.assertEqual(
            sorted(mutation_timestamps, reverse=True), mutation_timestamps,
            'filtered instance events are not newest first')
