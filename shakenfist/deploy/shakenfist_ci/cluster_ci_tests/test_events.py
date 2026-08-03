import json
import time

from shakenfist_client import apiclient
from testtools import content

from shakenfist_ci import base


class TestEventsQueryParameters(base.BaseTestCase):
    """The events endpoints' body parameters must be validated.

    Deliberately not a BaseNamespacedTestCase: nothing here is
    namespace or network scoped, and TestEvents.setUp allocates a
    VXLAN network and blocks on _await_networks_ready per test method.

    These read node events through system_client, which is the one
    events endpoint reachable without creating anything first.
    """

    def _node_name(self):
        nodes = self.system_client.get_nodes()
        self.assertNotEqual(0, len(nodes))
        return nodes[0]['name']

    def _events(self, node_name, **body):
        # The client helpers always send limit as an int, so drive the
        # body parameter through _request_url directly -- the same
        # pattern test_artifacts.py and test_vdi_tokens.py use.
        return self.system_client._request_url(
            'GET', '/nodes/' + node_name + '/events', data=body).json()

    def _expect_400(self, node_name, **body):
        exc = self.assertRaises(
            apiclient.RequestMalformedException,
            self.system_client._request_url,
            'GET', '/nodes/' + node_name + '/events',
            data=body)
        # Use the attributes the client sets explicitly. str(exc) only
        # happens to include the response body because APIException does
        # not override __str__, which is an accident of the client's
        # exception shape rather than a contract.
        self.assertEqual(400, exc.status_code)
        return exc

    def test_string_limit_is_coerced(self):
        # Issue 3609: the API layer merges JSON body values into handler
        # kwargs verbatim, so a caller sending {'limit': '5'} delivers a
        # str. That used to surface as a 400 leaking an interpreter
        # TypeError ("'<=' not supported between instances of 'str' and
        # 'int'"); the API must instead coerce numeric strings.
        node_name = self._node_name()

        # Control fetch with an integer limit. Asserting only
        # "len(events) <= 5" would pass vacuously on a quiet cluster
        # even if limit were ignored entirely, so compare against what
        # the node actually has.
        control = self._events(node_name, limit=100)
        events = self._events(node_name, limit='5')
        self.addDetail('events', content.text_content(json.dumps(
            events, indent=4, sort_keys=True)))

        self.assertNotEqual(0, len(events))
        if len(control) > 5:
            self.assertEqual(5, len(events))
        else:
            self.assertEqual(len(control), len(events))

    def test_oversized_limit_is_capped(self):
        # A limit beyond int32 was coerced successfully and then
        # overflowed the protobuf limit field when the gRPC request was
        # built, escaping as a 500 with a ValueError repr in the body.
        # Only the functional path proves this: the unit tests mock
        # mariadb away, so they never build a real request.
        node_name = self._node_name()
        events = self._events(node_name, limit=2 ** 40)
        self.assertNotEqual(0, len(events))

    def test_non_numeric_limit_is_a_clean_400(self):
        node_name = self._node_name()
        exc = self._expect_400(node_name, limit='banana')
        self.assertNotIn('not supported between instances', exc.text)
        self.assertIn('limit must be an integer', exc.text)

    def test_non_string_event_type_is_a_clean_400(self):
        # event_type reaches the same helper through the same
        # unvalidated body merge and lands in a protobuf string field,
        # so it leaked an interpreter message by the same route.
        node_name = self._node_name()
        exc = self._expect_400(node_name, event_type=5)
        self.assertNotIn('has type int', exc.text)
        self.assertIn('event_type must be a string', exc.text)


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
