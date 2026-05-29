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
        self._await_networks_ready([self.net_one['uuid']])

    # NOTE(mikal): needs ArtifactsUrlRefEndpoint implemented first
    # def test_artifact_events(self):
    #     a = self.test_client.get_artifact(base.CLUSTER_CI_IMAGE)
    #     self.assertNotEqual(0, len(self.test_client.get_artifact_events(a['uuid'])))

    def test_network_events(self):
        # Events are eventually consistent: emitting daemons (sf-api
        # gunicorn workers, sf-net, sf-queues) enqueue into a local
        # spool that the per-process drainer ships to sf-eventlog in
        # ~100 ms batches. "Network is created" (which is what
        # _await_networks_ready returns on) does not imply "all
        # events for the network are queryable yet". Poll for a few
        # seconds so the assertion does not race the drainer.
        events = []
        deadline = time.time() + 30
        while time.time() < deadline:
            events = self.test_client.get_network_events(self.net_one['uuid'])
            if events:
                break
            time.sleep(1)
        self.addDetail('events', content.text_content(json.dumps(
            events, indent=4, sort_keys=True)))
        self.assertNotEqual(0, len(events))

    def test_instance_events(self):
        inst1 = self.test_client.create_instance(
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

        self.addDetail('inst1', content.text_content(json.dumps(
            inst1, indent=4, sort_keys=True)))

        # Wait for the instance agent to report in
        self._await_instance_ready(inst1['uuid'])

        events = self.test_client.get_instance_events(inst1['uuid'])
        self.addDetail('events', content.text_content(json.dumps(
            events, indent=4, sort_keys=True)))
        self.assertNotEqual(0, len(events))
