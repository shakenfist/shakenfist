import json
import random
import time

from testtools import content

from shakenfist_ci import base
from shakenfist_ci import process


class StrayVxlanHostMixin:
    """Host-side helpers shared by the stray vxlan tests.

    Every helper acts on ``self.node``, which each test class sets in
    setUp to whichever node it needs to plant devices on.
    """

    def _node_config_value(self, option, default):
        """Read a SHAKENFIST_ option from the node's /etc/sf/config."""
        out, _ = self._node_exec(
            self.node, ['grep', '-h', '^SHAKENFIST_%s=' % option,
                        '/etc/sf/config'],
            sudo=True, check_exit_code=[0, 1, 2])
        for line in out.splitlines():
            if line.startswith('SHAKENFIST_%s=' % option):
                return line.split('=', 1)[1].strip().strip('"')
        return default

    def _unused_vxid(self):
        """A vxid no network holds and no device on the node is named for.

        Deleting a device which turned out to belong to a live network
        would be an outage, so this deliberately refuses to guess.
        """
        in_use = set()
        for net in self.system_client.get_networks(all=True):
            if net.get('vxlan_id'):
                in_use.add(int(net['vxlan_id']))

        links = set(self._node_link_names(self.node))
        for _ in range(20):
            candidate = random.randint(1, 16777215)
            if candidate in in_use:
                continue
            if 'vxlan-%06x' % candidate in links:
                continue
            if 'br-vxlan-%06x' % candidate in links:
                continue
            return candidate

        self.fail('Could not find an unused vxid after 20 attempts')

    def _remove_devices(self, vxid, extra=None):
        devices = ['br-vxlan-%06x' % vxid, 'vxlan-%06x' % vxid]
        devices.extend(extra or [])
        for device in devices:
            try:
                self._node_exec(
                    self.node, ['ip', 'link', 'delete', device],
                    sudo=True, check_exit_code=False)
            except process.ProcessExecutionError:
                pass

    def _plant_orphan(self, vxid, mesh_nic, with_bridge=True):
        """Create a vxlan device, and usually its bridge, for a vxid.

        ``with_bridge=False`` plants the residue an interrupted
        hypervisor teardown leaves behind: the bridge is deleted before
        the vxlan interface, so a teardown which died in the middle
        leaves the interface with no bridge.
        """
        self._node_exec(
            self.node,
            ['ip', 'link', 'add', 'vxlan-%06x' % vxid, 'mtu', '1400',
             'type', 'vxlan', 'id', str(vxid), 'dev', mesh_nic,
             'dstport', '0'],
            sudo=True)
        if with_bridge:
            self._node_exec(
                self.node,
                ['ip', 'link', 'add', 'br-vxlan-%06x' % vxid,
                 'type', 'bridge'],
                sudo=True)


class TestStrayVxlanReaping(StrayVxlanHostMixin, base.BaseNamespacedTestCase):
    """The network maintainer must delete vxlan devices which belong to no
    network, and must leave every other device alone.

    This is the one thing the unit tests for the reaper structurally
    cannot cover: they mock the host away, so they prove the decision
    logic and nothing about whether a real device is actually removed --
    for a change whose failure mode is taking a live network down, that
    is the wrong half to test in isolation.

    The test plants an orphan on the network node: a vxlan device and its
    bridge, named for a vxid no network holds. Nothing in the cluster has
    any legitimate use for it, so it is exactly what maintain should
    reap. The same node's live networks are the control -- the network
    node carries a device for every active network, so if the reaper is
    too eager the control network's bridge disappears with the orphan.

    A second orphan is planted with a foreign device enslaved to its
    bridge, standing in for a guest tap. The database view of that
    orphan is identical to the first one's, so it is the host side
    cross-check and nothing else which has to save it. It costs no
    extra wall clock, because both orphans age out over the same wait.

    A third orphan is planted as a bare vxlan device with no bridge at
    all. That is not an exotic shape: hypervisor teardown deletes the
    bridge before the vxlan interface, so an interrupted teardown leaves
    exactly this, and it is the case where a host side cross-check which
    treated a missing bridge as unanswerable would protect the residue
    forever.

    The wait is real: maintain only acts once a device has been stray for
    MAINTAIN_STRAY_VXLAN_GRACE_SECONDS (five minutes by default), which
    is why this test is slow. A deployment which sets that option lower
    makes it correspondingly faster.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'strayvxlan'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()

        self.node = self._network_node()
        self._require_node_exec(self.node)

    def test_orphan_vxlan_is_reaped_and_live_network_survives(self):
        # A live network for the control assertion. The network node
        # carries every active network, so its bridge must exist on this
        # node and must still be there when the orphan has been reaped.
        net = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail('net', content.text_content(
            json.dumps(net, indent=4, sort_keys=True)))
        self._await_networks_ready([net['uuid']])

        live_bridge = 'br-vxlan-%06x' % net['vxlan_id']
        self.assertIn(
            live_bridge, self._node_link_names(self.node),
            'The network node should carry a bridge for every active '
            'network, but %s is missing' % live_bridge)

        # Plant the orphan.
        mesh_nic = self._node_config_value('NODE_MESH_NIC', 'eth0')
        vxid = self._unused_vxid()
        self.addCleanup(self._remove_devices, vxid)
        self._plant_orphan(vxid, mesh_nic)

        # And a second orphan which is identical as far as the database
        # is concerned, but which has a device Shaken Fist did not
        # create enslaved to its bridge -- a stand in for a guest tap.
        # Only the host side cross-check can save this one.
        occupied_vxid = self._unused_vxid()
        # A veth pair rather than a dummy device: Shaken Fist already
        # creates veths on every node, so the module is known to be
        # there, and deleting one end removes both.
        tap = 'sfci-t-%06x' % occupied_vxid
        self.addCleanup(
            self._remove_devices, occupied_vxid, extra=[tap])
        self._plant_orphan(occupied_vxid, mesh_nic)
        self._node_exec(
            self.node,
            ['ip', 'link', 'add', tap, 'type', 'veth',
             'peer', 'name', 'sfci-p-%06x' % occupied_vxid],
            sudo=True)
        self._node_exec(
            self.node,
            ['ip', 'link', 'set', tap, 'master',
             'br-vxlan-%06x' % occupied_vxid],
            sudo=True)

        # And a third with no bridge at all -- what an interrupted
        # hypervisor teardown actually leaves behind, because the bridge
        # is deleted before the vxlan interface. Rediscovery keys on the
        # interface, so maintain sees this one on every pass; it must
        # still be reaped even though there is no bridge to interrogate.
        bridgeless_vxid = self._unused_vxid()
        self.addCleanup(self._remove_devices, bridgeless_vxid)
        self._plant_orphan(bridgeless_vxid, mesh_nic, with_bridge=False)

        links = self._node_link_names(self.node)
        self.assertIn('vxlan-%06x' % vxid, links)
        self.assertIn('br-vxlan-%06x' % vxid, links)
        self.assertIn('vxlan-%06x' % occupied_vxid, links)
        self.assertIn('vxlan-%06x' % bridgeless_vxid, links)
        self.assertNotIn('br-vxlan-%06x' % bridgeless_vxid, links)

        # Maintain only acts after the grace period, and then only on its
        # next pass, so allow the grace period plus a few passes.
        grace = int(self._node_config_value(
            'MAINTAIN_STRAY_VXLAN_GRACE_SECONDS', 300))
        deadline = time.time() + grace + 180

        reapable = ['vxlan-%06x' % vxid, 'vxlan-%06x' % bridgeless_vxid]
        while time.time() < deadline:
            links = self._node_link_names(self.node)
            if not [d for d in reapable if d in links]:
                break
            time.sleep(10)

        links = self._node_link_names(self.node)
        self.addDetail('links_at_end', content.text_content(
            json.dumps(sorted(links), indent=4)))

        # Diagnostics only. get_node_events() is not used by any other
        # test in this suite, and the client is a separately versioned
        # package -- a missing method or a changed signature here must
        # read as a harness problem, not as a stray vxlan regression, so
        # it must not be able to fail the test before the assertions
        # below run.
        try:
            events = self.system_client.get_node_events(
                self.node['name'], limit=100)
            self.addDetail('node_events', content.text_content(
                json.dumps(events, indent=4, sort_keys=True, default=str)))
        except Exception as e:
            self.addDetail('node_events_error', content.text_content(
                '%s: %s' % (type(e).__name__, e)))

        self.assertNotIn(
            'vxlan-%06x' % vxid, links,
            'Orphaned vxlan device was not reaped within %d seconds'
            % (grace + 180))
        self.assertNotIn(
            'br-vxlan-%06x' % vxid, links,
            'Orphaned vxlan bridge was not reaped within %d seconds'
            % (grace + 180))

        # The residue an interrupted teardown leaves: no bridge to ask
        # about, so a host side cross-check which cannot tell "the
        # bridge is absent" from "I could not ask" protects this
        # forever.
        self.assertNotIn(
            'vxlan-%06x' % bridgeless_vxid, links,
            'Orphaned vxlan device with no bridge was not reaped within '
            '%d seconds' % (grace + 180))

        # The control: a live network must be untouched by the reap.
        self.assertIn(
            live_bridge, links,
            'The reaper removed the bridge of a live network')

        # The second control: an orphan the database cannot tell apart
        # from the first, saved only by the device enslaved to its
        # bridge. This is the guard against a missing placement record
        # taking a live domain's network away.
        self.assertIn(
            'vxlan-%06x' % occupied_vxid, links,
            'The reaper removed a vxlan whose bridge still had a foreign '
            'device enslaved to it')
        self.assertIn(
            'br-vxlan-%06x' % occupied_vxid, links,
            'The reaper removed a bridge which still had a foreign device '
            'enslaved to it')


class TestStrayVxlanInstanceProtection(StrayVxlanHostMixin,
                                       base.BaseNamespacedTestCase):
    """On a hypervisor, whether a claimed stray may be torn down turns on
    a single SQL query: "which vxids do the instances placed on this node
    use?".

    That query joins this node's placement references to
    network_interfaces to networks, and it spans both of the project's
    uuid storage conventions (references hold the dashed form, the static
    tables the undashed one) and two enum spellings. Every way of getting
    it wrong returns fewer rows than it should, and a row it fails to
    return is a live instance's network being torn down underneath it.
    The unit tests run the same SQL, but against SQLite -- only a real
    cluster exercises MariaDB's types, collation and indexes.

    So the test plants a stray on a hypervisor for a network which
    genuinely exists but which nothing on that node uses, alongside an
    instance hosted there on a different network. The stray must go and
    the instance's plumbing must stay: the first assertion fails if the
    query returns too much, the second if it returns too little.

    As with the reaping test the wait is real, because maintain only acts
    on a device which has been stray for
    MAINTAIN_STRAY_VXLAN_GRACE_SECONDS.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'strayvxlanprot'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()

        # The network node is excluded deliberately: it carries a device
        # for every active network whatever it hosts, and the reaper
        # never tears a stray down there, so it cannot show this.
        candidates = self._hypervisor_nodes(exclude_network_node=True)
        if not candidates:
            self.skipTest(
                'Need a hypervisor which is not the network node; the '
                'cluster reports none.')
        self.node = candidates[0]
        self._require_node_exec(self.node)

    def test_stray_torn_down_while_a_hosted_network_survives(self):
        # The network the hypervisor legitimately carries, because it
        # hosts an instance on it. Its devices exist only for as long as
        # the placement query can see that instance.
        hosted_net = self.test_client.allocate_network(
            '192.168.244.0/24', True, True, '%s-hosted' % self.namespace)
        self.addDetail('hosted_net', content.text_content(
            json.dumps(hosted_net, indent=4, sort_keys=True)))
        self._await_networks_ready([hosted_net['uuid']])

        inst = self.test_client.create_instance(
            'strayvxlanprot', 1, 1024,
            [{'network_uuid': hosted_net['uuid']}],
            [{'size': 8, 'base': base.CLUSTER_CI_IMAGE, 'type': 'disk'}],
            None, None, force_placement=self.node['name'])
        self.addDetail('instance', content.text_content(
            json.dumps(inst, indent=4, sort_keys=True)))
        # If the scheduler did not honour the placement the protection
        # assertion below would be testing the wrong node.
        self.assertEqual(self.node['uuid'], inst['node'])
        self._await_instance_create(inst['uuid'])

        hosted_vxlan = 'vxlan-%06x' % hosted_net['vxlan_id']
        hosted_bridge = 'br-vxlan-%06x' % hosted_net['vxlan_id']
        self.assertIn(
            hosted_vxlan, self._node_link_names(self.node),
            'A hypervisor hosting an instance should carry that '
            'network, but %s is missing' % hosted_vxlan)

        # A second network which exists but which nothing on this node
        # uses. Its vxid is claimed, so the reaper cannot delete the
        # device itself -- it has to decide, from the placement query
        # alone, that no instance here needs it and enqueue a teardown.
        stray_net = self.test_client.allocate_network(
            '192.168.245.0/24', True, True, '%s-stray' % self.namespace)
        self.addDetail('stray_net', content.text_content(
            json.dumps(stray_net, indent=4, sort_keys=True)))
        self._await_networks_ready([stray_net['uuid']])

        stray_vxid = stray_net['vxlan_id']
        mesh_nic = self._node_config_value('NODE_MESH_NIC', 'eth0')
        self.addCleanup(self._remove_devices, stray_vxid)
        self._plant_orphan(stray_vxid, mesh_nic)

        links = self._node_link_names(self.node)
        self.assertIn('vxlan-%06x' % stray_vxid, links)
        self.assertIn('br-vxlan-%06x' % stray_vxid, links)

        grace = int(self._node_config_value(
            'MAINTAIN_STRAY_VXLAN_GRACE_SECONDS', 300))
        deadline = time.time() + grace + 180
        while time.time() < deadline:
            links = self._node_link_names(self.node)
            if 'vxlan-%06x' % stray_vxid not in links:
                break
            time.sleep(10)

        links = self._node_link_names(self.node)
        self.addDetail('links_at_end', content.text_content(
            json.dumps(sorted(links), indent=4)))

        self.assertNotIn(
            'vxlan-%06x' % stray_vxid, links,
            'A claimed stray no instance on this node uses was not torn '
            'down within %d seconds' % (grace + 180))

        # The control, and the assertion which actually guards the
        # query: the hosted network's plumbing must be untouched. A
        # placement join which matches nothing looks exactly like "no
        # instance here uses this network" for this network too.
        self.assertIn(
            hosted_vxlan, links,
            'The reaper removed the vxlan of a network an instance on '
            'this node is attached to')
        self.assertIn(
            hosted_bridge, links,
            'The reaper removed the bridge of a network an instance on '
            'this node is attached to')
