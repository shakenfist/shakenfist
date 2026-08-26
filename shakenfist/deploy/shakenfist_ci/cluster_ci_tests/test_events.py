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

        # The admission primitive emits an "instance placed" audit event
        # from Instance._admit_placement() on every successful
        # placement, carrying the node it landed on and the node's
        # post-admit capacity counters. Poll for it rather than reading
        # once: events are eventually consistent (see the comment in
        # test_network_events), and _await_instance_ready() returning
        # does not imply the audit event has reached sf-eventlog.
        events = []
        placed = []
        deadline = time.time() + 30
        while time.time() < deadline:
            events = self.test_client.get_instance_events(inst1['uuid'])
            placed = [e for e in events
                      if str(e.get('message', '')) == 'instance placed']
            if placed:
                break
            time.sleep(1)

        self.addDetail('events', content.text_content(json.dumps(
            events, indent=4, sort_keys=True)))
        self.assertNotEqual(0, len(events))
        # Tolerant of extra events, and of more than one placement: a
        # preflight redirect or a cleaner rewrite-to-local legitimately
        # places the same instance again.
        self.assertNotEqual(
            0, len(placed),
            'instance create emitted no "instance placed" audit event')

    def test_instance_domain_xml_event(self):
        """The XML libvirt was actually handed carries free page reporting.

        A unit test asserts the template in this repository has
        freePageReporting on its memballoon. That is not the same claim as
        the hypervisor having rendered it: the template is an ansible file
        copied onto each node, so a deploy that ships a stale copy, or a
        node whose copy did not update, produces domains without it and
        nothing else notices. Instance._create_domain_xml() emits the XML it
        generated as a mutate event, which is the only view of that from
        outside the hypervisor.
        """
        inst = self.test_client.create_instance(
            'test-instance-domain-xml', 1, 1024,
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

        self.addDetail('inst', content.text_content(json.dumps(
            inst, indent=4, sort_keys=True)))
        self._await_instance_ready(inst['uuid'])

        # Filter to mutate events rather than reading the default page of
        # everything: a booting instance emits well over a hundred events
        # and the domain XML is written early, so an unfiltered read can
        # legitimately not contain it. Poll for the same eventual
        # consistency reason as test_network_events.
        xml_events = []
        deadline = time.time() + 30
        while time.time() < deadline:
            xml_events = [
                e for e in self.test_client.get_instance_events(
                    inst['uuid'], event_type='mutate', limit=1000)
                if str(e.get('message', '')) == 'libvirt domain XML']
            if xml_events:
                break
            time.sleep(1)

        self.addDetail('domain xml events', content.text_content(json.dumps(
            xml_events, indent=4, sort_keys=True, default=str)))
        self.assertNotEqual(
            0, len(xml_events),
            'instance create emitted no "libvirt domain XML" mutate event')

        # Events come back newest first, and a power on retry can generate
        # more than one, so [0] is the XML libvirt was most recently handed.
        # "extra" can be present and null, hence the or rather than a default.
        xml = (xml_events[0].get('extra') or {}).get('xml', '')
        self.assertIn(
            "freePageReporting='on'", xml,
            'the domain XML this hypervisor generated has no free page '
            'reporting on its balloon, so the guest cannot hand freed '
            'memory back to the host (issue 3920). The most likely cause '
            'is a hypervisor running an older copy of libvirt.tmpl.')
