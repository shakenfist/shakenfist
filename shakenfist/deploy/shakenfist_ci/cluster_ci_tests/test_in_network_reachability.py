# Copyright 2026 Michael Still and contributors
import json
import time

from testtools import content

from shakenfist_ci import base


class TestInNetworkReachability(base.BaseNamespacedTestCase):
    """An address which answers from outside must answer from inside too.

    Both of Shaken Fist's externally reachable address types are handed
    to a virtual network by the network node, and both used to work only
    for clients which were not on that network. A routed address was
    never routed inside the network's own namespace, so an instance's
    packet reached its default gateway and died there; a floating
    address was DNAT'ed into the network and the reply then travelled
    back over the virtual network's own layer 2, so it never had its
    source rewritten and the caller discarded it (github issue #3662).

    The consequence was that no Shaken Fist address was both stable
    across instance replacement and usable from inside the network --
    which is what a Kubernetes service address, or any other
    load-balanced address, has to be. It stayed hidden because the
    clients which most wanted it (kube-proxy, on a cluster member)
    intercept the address in iptables before routing ever happens.

    These are deliberately black box: the assertion is that an instance
    can reach the address, not that any particular rule or route exists.
    The host side commands are pinned by the unit tests.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'uturn'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4,
                                            sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def _create_instance(self, name):
        inst = self.create_instance(
            name, 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None, side_channels=['sf-agent2'])
        self.addDetail(
            name,
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self.assertIsNotNone(inst['uuid'])
        return inst

    def _guest_device_for_mac(self, instance_uuid, macaddr):
        """Find the guest's name for the interface with this MAC.

        Nothing guarantees the guest calls it eth0, and the API knows the
        MAC it gave the interface, so ask the guest to match them up
        rather than guessing a name.
        """
        results = self._await_command(instance_uuid, 'ip -o link')
        self.addDetail(
            'guest links',
            content.text_content(json.dumps(results, indent=4,
                                            sort_keys=True)))
        for line in results['stdout'].split('\n'):
            if macaddr not in line:
                continue
            # "2: ens3: <BROADCAST,MULTICAST,UP> mtu 7950 ... link/ether ..."
            return line.split(':')[1].strip().split('@')[0]

        self.fail('No interface with MAC %s in guest %s: %s'
                  % (macaddr, instance_uuid, results['stdout']))

    def _await_ping(self, instance_uuid, address, description, timeout=180):
        """Ping from inside an instance until it works, or give up.

        Making an address reachable is asynchronous -- the REST call
        returns once the operation is enqueued -- so the first ping can
        legitimately lose.
        """
        start_time = time.time()
        results = None
        while time.time() - start_time < timeout:
            results = self._await_command(
                instance_uuid, 'ping -c 3 -W 2 %s' % address)
            if results['return-code'] == 0 and ' 0% packet' in results[
                    'stdout']:
                self.addDetail(
                    description,
                    content.text_content(json.dumps(results, indent=4,
                                                    sort_keys=True)))
                return
            time.sleep(5)

        self.addDetail(
            description,
            content.text_content(json.dumps(results, indent=4,
                                            sort_keys=True)))
        self.fail('Could not ping %s (%s) from instance %s'
                  % (address, description, instance_uuid))

    def _await_floating_address(self, interface_uuid):
        start_time = time.time()
        while time.time() - start_time < 120:
            floating = self.test_client.get_interface(
                interface_uuid).get('floating')
            if floating:
                return floating
            time.sleep(5)
        self.fail('No floating address appeared on interface %s'
                  % interface_uuid)

    def test_routed_and_floating_addresses_answer_from_inside(self):
        # Both halves share these two instances. Booting a guest is by
        # far the most expensive thing this test does, and splitting the
        # two scenarios into separate test methods would boot four.
        server = self._create_instance('uturn-server')
        client = self._create_instance('uturn-client')
        self._await_instance_ready(server['uuid'])
        self._await_instance_ready(client['uuid'])

        server_iface = self.test_client.get_instance_interfaces(
            server['uuid'])[0]

        # A routed address. Shaken Fist only routes it to the network;
        # answering for it is the user's job, exactly as metallb does for
        # a Kubernetes service address.
        routed = self.test_client.route_network_address(self.net['uuid'])
        self.addDetail('routed address', content.text_content(routed))

        device = self._guest_device_for_mac(
            server['uuid'], server_iface['macaddr'])
        results = self._await_command(
            server['uuid'], 'ip addr add %s/32 dev %s' % (routed, device))
        self.assertEqual(
            0, results['return-code'],
            'Could not claim %s in the guest: %s'
            % (routed, results['stderr']))

        self._await_ping(client['uuid'], routed, 'routed address ping')

        # The unroute happens here rather than through addCleanup(),
        # because testtools runs cleanups *after* tearDown() -- and
        # BaseNamespacedTestCase.tearDown() ends by deleting the
        # namespace, after which the namespaced client cannot even
        # authenticate, so a cleanup registered on it could only ever
        # raise. Two earlier tests in this repository failed every merge
        # group from the day they landed for exactly that reason, and
        # both carry a comment saying so (database_tier.py and
        # cluster_ci_tests/test_namespace_claims.py).
        #
        # Nothing leaks if the test fails before reaching this line:
        # tearDown deletes the network, and that is what releases the
        # address. The call is therefore coverage rather than cleanup,
        # which is the other reason it belongs in the test body -- the
        # endpoint answers 403 or 404 unless the address really is
        # routed by this network, so returning at all is an assertion
        # about the half of the lifecycle the ping cannot see.
        self.test_client.unroute_network_address(self.net['uuid'], routed)

        # A floating address, which is DNAT'ed rather than routed, and
        # which the instance holding it never sees.
        self.test_client.float_interface(server_iface['uuid'])
        floating = self._await_floating_address(server_iface['uuid'])
        self.addDetail('floating address', content.text_content(floating))

        self._await_ping(client['uuid'], floating, 'floating address ping')
