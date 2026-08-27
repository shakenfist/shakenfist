# Copyright 2026 Michael Still and contributors
"""Tests for the privexec floating IP handlers.

These tests construct a `PrivExecJob` with a mock connection and patch the
privexec util command helpers, then confirm that `_add_floating_ip` and
`_remove_floating_ip` mutate the host with the right commands. The
interface naming here is load bearing: the removal path must use the same
outer interface name as the add path or removal silently does nothing and
the floating IP pool is slowly poisoned with stale veth pairs and DNAT
rules (github issues #3378 through #3383).
"""

from unittest import mock

from shakenfist import exceptions
from shakenfist.daemons.privexec import main as privexec_main
from shakenfist.protos import privexec_pb2
from shakenfist.tests import base
from shakenfist.util import concurrency as util_concurrency


NETWORK_UUID = 'ccc652aa-f7b6-4f99-b76d-443ae4d91412'

# 192.168.10.44 as eight hex digits
FLOATING_INTERFACE = 'flt-c0a80a2c'
INNER_INTERFACE = 'flt-c0a80a2c-i'


class CommandRecorder:
    """Record command_helper() invocations, with canned results.

    `results` maps a substring to a (stdout, stderr, returncode) tuple.
    The first key found in the space-joined command wins. Unmatched
    commands succeed with no output.
    """

    def __init__(self, results=None):
        self.calls = []
        self.results = results or {}

    def __call__(self, *command, failure_is_error=True):
        self.calls.append(command)
        joined = ' '.join(command)
        for match, result in self.results.items():
            if match in joined:
                return result
        return ('', '', 0)

    def find(self, substring):
        return [c for c in self.calls if substring in ' '.join(c)]


class PrivExecFloatingIPTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.locate_command = mock.patch(
            'shakenfist.daemons.privexec.util.locate_command',
            side_effect=lambda c: c)
        self.locate_command.start()
        self.addCleanup(self.locate_command.stop)

        self.job = privexec_main.PrivExecJob(mock.MagicMock())

    def patch_commands(self, results=None):
        recorder = CommandRecorder(results)
        patcher = mock.patch(
            'shakenfist.daemons.privexec.util.command_helper',
            side_effect=recorder)
        patcher.start()
        self.addCleanup(patcher.stop)
        return recorder

    def patch_netns_exists(self, exists):
        patcher = mock.patch(
            'shakenfist.daemons.privexec.main.os.path.exists',
            return_value=exists)
        patcher.start()
        self.addCleanup(patcher.stop)


class AddFloatingIPTestCase(PrivExecFloatingIPTestCase):
    def _request(self):
        return privexec_pb2.AddFloatingIPRequest(
            network_uuid=NETWORK_UUID,
            floating_address='192.168.10.44',
            inner_address='10.0.0.56')

    def _patch_utils(self, addresses=None):
        for name, value in [
                ('create_interface', (True, '')),
                ('check_for_interface', True),
                ('get_interface_addresses', addresses or []),
                ('add_address_to_interface', True)]:
            patcher = mock.patch(
                f'shakenfist.daemons.privexec.util.{name}',
                return_value=value)
            setattr(self, f'mock_{name}', patcher.start())
            self.addCleanup(patcher.stop)

    def test_add_fresh_floating_ip(self):
        recorder = self.patch_commands(results={' -C ': ('', '', 1)})
        self._patch_utils()

        reply = self.job._add_floating_ip(self._request())

        # The address check must inspect the inner end of the veth pair
        # inside the network namespace, because that is where the address
        # is added. It previously inspected the outer end in the root
        # namespace, which never holds the address.
        self.mock_get_interface_addresses.assert_called_once_with(
            INNER_INTERFACE, namespace=NETWORK_UUID)
        self.mock_add_address_to_interface.assert_called_once_with(
            INNER_INTERFACE, NETWORK_UUID, '192.168.10.44', '32')
        self.assertEqual([
            ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
             '-t', 'nat', '-A', 'PREROUTING', '-d', '192.168.10.44',
             '-j', 'DNAT', '--to-destination', '10.0.0.56'),
        ], recorder.find(' -A '))
        self.assertEqual(privexec_pb2.AddFloatingIPReply.OK,
                         reply.add_floating_ip_reply.error)

    def test_add_is_idempotent(self):
        # A repeated add with the address and DNAT rule already in place
        # must not add a second address or append a duplicate DNAT rule.
        recorder = self.patch_commands(results={' -C ': ('', '', 0)})
        self._patch_utils(addresses=['192.168.10.44'])

        reply = self.job._add_floating_ip(self._request())

        self.mock_add_address_to_interface.assert_not_called()
        self.assertEqual([], recorder.find(' -A '))
        self.assertEqual(privexec_pb2.AddFloatingIPReply.OK,
                         reply.add_floating_ip_reply.error)

    def test_add_recreates_stranded_pair(self):
        # If the outer end of the veth pair already exists but the inner
        # end is not in the requested network namespace, the pair was left
        # behind by a previous user of this floating IP. The pair must be
        # destroyed and recreated, not reused.
        recorder = self.patch_commands(results={' -C ': ('', '', 1)})
        self._patch_utils()
        self.mock_check_for_interface.return_value = False

        reply = self.job._add_floating_ip(self._request())

        self.mock_check_for_interface.assert_called_once_with(
            INNER_INTERFACE, namespace=NETWORK_UUID)
        self.assertEqual(
            [('ip', 'link', 'del', FLOATING_INTERFACE)],
            recorder.find('link del'))
        self.assertEqual(2, self.mock_create_interface.call_count)
        self.assertEqual(privexec_pb2.AddFloatingIPReply.OK,
                         reply.add_floating_ip_reply.error)

    def test_add_reports_stranded_pair_delete_failure(self):
        self.patch_commands(results={'link del': ('', 'boom', 1)})
        self._patch_utils()
        self.mock_check_for_interface.return_value = False

        reply = self.job._add_floating_ip(self._request())

        self.assertEqual(
            privexec_pb2.AddFloatingIPReply.CREATE_INTERFACE_FAILED,
            reply.add_floating_ip_reply.error)
        self.assertIn('boom', reply.add_floating_ip_reply.error_text)

    def test_add_reports_stranded_recreate_failure(self):
        # The stranded pair is detected (inner end not in the namespace),
        # the delete succeeds, but recreating the pair fails. This surfaces
        # the create failure message including the underlying detail.
        self.patch_commands(results={' -C ': ('', '', 1)})
        self._patch_utils()
        self.mock_check_for_interface.return_value = False
        self.mock_create_interface.side_effect = [
            (True, ''), (False, 'ip link add exited 1: veth boom')]

        reply = self.job._add_floating_ip(self._request())

        self.assertEqual(2, self.mock_create_interface.call_count)
        self.assertEqual(
            privexec_pb2.AddFloatingIPReply.CREATE_INTERFACE_FAILED,
            reply.add_floating_ip_reply.error)
        self.assertIn('failed to create veth pair',
                      reply.add_floating_ip_reply.error_text)
        self.assertIn(FLOATING_INTERFACE,
                      reply.add_floating_ip_reply.error_text)
        self.assertIn('veth boom', reply.add_floating_ip_reply.error_text)

    def test_add_reports_create_failure_with_detail(self):
        # A failed initial create must carry the underlying command
        # failure in the error text -- the flt-* failures in issue 3608
        # were undiagnosable because the ip stderr (which names the
        # errno, e.g. a torn-down namespace) was dropped on the floor.
        self.patch_commands()
        self._patch_utils()
        self.mock_create_interface.return_value = (
            False,
            f'ip link set netns {NETWORK_UUID} exited 1: Cannot open '
            f'network namespace "{NETWORK_UUID}": No such file or '
            f'directory')

        reply = self.job._add_floating_ip(self._request())

        self.assertEqual(
            privexec_pb2.AddFloatingIPReply.CREATE_INTERFACE_FAILED,
            reply.add_floating_ip_reply.error)
        self.assertIn('No such file or directory',
                      reply.add_floating_ip_reply.error_text)

    def test_add_reports_dnat_append_failure(self):
        # The DNAT rule is absent (-C returns non-zero) so it is appended,
        # but the -A append itself fails. This must surface as
        # IPTABLES_FAILED with the address and namespace in the detail.
        self.patch_commands(
            results={' -C ': ('', '', 1), ' -A ': ('', 'append boom', 1)})
        self._patch_utils()

        reply = self.job._add_floating_ip(self._request())

        self.assertEqual(privexec_pb2.AddFloatingIPReply.IPTABLES_FAILED,
                         reply.add_floating_ip_reply.error)
        self.assertIn('192.168.10.44', reply.add_floating_ip_reply.error_text)
        self.assertIn(NETWORK_UUID, reply.add_floating_ip_reply.error_text)
        self.assertIn('append boom', reply.add_floating_ip_reply.error_text)

    def test_add_reports_address_failure(self):
        self.patch_commands()
        self._patch_utils()
        self.mock_add_address_to_interface.return_value = False

        reply = self.job._add_floating_ip(self._request())

        self.assertEqual(privexec_pb2.AddFloatingIPReply.ADD_ADDRESS_FAILED,
                         reply.add_floating_ip_reply.error)
        self.assertIn(INNER_INTERFACE, reply.add_floating_ip_reply.error_text)
        self.assertIn(NETWORK_UUID, reply.add_floating_ip_reply.error_text)

    def test_add_brings_veth_pair_up(self):
        # The floating veth pair is an address anchor and historically
        # worked admin-DOWN via a kernel subtlety (local routes persist
        # on DOWN interfaces). Both ends are now explicitly brought up.
        recorder = self.patch_commands(results={' -C ': ('', '', 1)})
        self._patch_utils()

        self.job._add_floating_ip(self._request())

        self.assertEqual([
            ('ip', 'link', 'set', FLOATING_INTERFACE, 'up'),
        ], recorder.find(f'link set {FLOATING_INTERFACE} up'))
        self.assertEqual([
            ('ip', 'netns', 'exec', NETWORK_UUID, 'ip', 'link', 'set',
             INNER_INTERFACE, 'up'),
        ], recorder.find(f'link set {INNER_INTERFACE} up'))

    def test_add_announces_with_gratuitous_arp(self):
        # Floating addresses are recycled between networks with distinct
        # egress veth MACs, so the add announces the new mapping with a
        # gratuitous ARP out the egress veth (derived from the vxid).
        recorder = self.patch_commands(results={' -C ': ('', '', 1)})
        self._patch_utils()

        request = self._request()
        request.vxid = 0xc1bc81
        with mock.patch(
                'shakenfist.daemons.privexec.main.shutil.which',
                return_value='/usr/sbin/arping'):
            reply = self.job._add_floating_ip(request)

        self.assertEqual([
            ('ip', 'netns', 'exec', NETWORK_UUID, '/usr/sbin/arping',
             '-c', '2', '-U', '-i', 'egr-c1bc81-i', '-S', '192.168.10.44',
             '192.168.10.44'),
        ], recorder.find('arping'))
        self.assertEqual(privexec_pb2.AddFloatingIPReply.OK,
                         reply.add_floating_ip_reply.error)

    def test_add_skips_gratuitous_arp_without_vxid(self):
        # A zero vxid means the caller predates the field; the add works
        # exactly as before, without an announcement.
        recorder = self.patch_commands(results={' -C ': ('', '', 1)})
        self._patch_utils()

        reply = self.job._add_floating_ip(self._request())

        self.assertEqual([], recorder.find('arping'))
        self.assertEqual(privexec_pb2.AddFloatingIPReply.OK,
                         reply.add_floating_ip_reply.error)

    def test_add_gratuitous_arp_failure_is_best_effort(self):
        # Reachability usually works without the announcement, so a
        # failed arping must not fail the float add.
        self.patch_commands(
            results={' -C ': ('', '', 1),
                     'arping': ('', 'arping boom', 1)})
        self._patch_utils()

        request = self._request()
        request.vxid = 0xc1bc81
        with mock.patch(
                'shakenfist.daemons.privexec.main.shutil.which',
                return_value='/usr/sbin/arping'):
            reply = self.job._add_floating_ip(request)

        self.assertEqual(privexec_pb2.AddFloatingIPReply.OK,
                         reply.add_floating_ip_reply.error)

    def test_add_gratuitous_arp_skipped_when_arping_missing(self):
        recorder = self.patch_commands(results={' -C ': ('', '', 1)})
        self._patch_utils()

        request = self._request()
        request.vxid = 0xc1bc81
        with mock.patch(
                'shakenfist.daemons.privexec.main.shutil.which',
                return_value=None):
            reply = self.job._add_floating_ip(request)

        self.assertEqual([], recorder.find('arping'))
        self.assertEqual(privexec_pb2.AddFloatingIPReply.OK,
                         reply.add_floating_ip_reply.error)


class RemoveFloatingIPTestCase(PrivExecFloatingIPTestCase):
    def _request(self):
        return privexec_pb2.RemoveFloatingIPRequest(
            network_uuid=NETWORK_UUID,
            floating_address='192.168.10.44')

    def test_remove_deletes_correctly_named_interface(self):
        # The add path creates the outer end of the veth pair as
        # flt-<hex>, so that is what removal must delete. It previously
        # looked for flt-<hex>-o, which never exists, making removal a
        # silent no-op.
        recorder = self.patch_commands()
        self.patch_netns_exists(False)

        with mock.patch(
                'shakenfist.daemons.privexec.util.check_for_interface',
                return_value=True) as mock_check:
            reply = self.job._remove_floating_ip(self._request())

        mock_check.assert_called_once_with(FLOATING_INTERFACE)
        self.assertEqual(
            [('ip', 'link', 'del', FLOATING_INTERFACE)],
            recorder.find('link del'))
        self.assertEqual(privexec_pb2.RemoveFloatingIPReply.OK,
                         reply.remove_floating_ip_reply.error)

    def test_remove_deletes_stale_dnat_rules(self):
        listing = '\n'.join([
            '-P PREROUTING ACCEPT',
            ('-A PREROUTING -d 192.168.10.44/32 -j DNAT '
             '--to-destination 10.0.0.2'),
            ('-A PREROUTING -d 192.168.10.77/32 -j DNAT '
             '--to-destination 10.0.0.3'),
            ('-A PREROUTING -d 192.168.10.44/32 -j DNAT '
             '--to-destination 10.0.0.56'),
        ])
        recorder = self.patch_commands(
            results={'-S PREROUTING': (listing, '', 0)})
        self.patch_netns_exists(True)

        with mock.patch(
                'shakenfist.daemons.privexec.util.check_for_interface',
                return_value=False):
            reply = self.job._remove_floating_ip(self._request())

        deletes = recorder.find(' -D ')
        self.assertEqual([
            ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
             '-t', 'nat', '-D', 'PREROUTING', '-d', '192.168.10.44/32',
             '-j', 'DNAT', '--to-destination', '10.0.0.2'),
            ('ip', 'netns', 'exec', NETWORK_UUID, 'iptables', '-w', '10',
             '-t', 'nat', '-D', 'PREROUTING', '-d', '192.168.10.44/32',
             '-j', 'DNAT', '--to-destination', '10.0.0.56'),
        ], deletes)
        self.assertEqual(privexec_pb2.RemoveFloatingIPReply.OK,
                         reply.remove_floating_ip_reply.error)

    def test_remove_skips_dnat_when_namespace_missing(self):
        recorder = self.patch_commands()
        self.patch_netns_exists(False)

        with mock.patch(
                'shakenfist.daemons.privexec.util.check_for_interface',
                return_value=False):
            reply = self.job._remove_floating_ip(self._request())

        self.assertEqual([], recorder.calls)
        self.assertEqual(privexec_pb2.RemoveFloatingIPReply.OK,
                         reply.remove_floating_ip_reply.error)

    def test_remove_reports_interface_delete_failure(self):
        self.patch_commands(
            results={'link del': ('', 'boom', 1)})
        self.patch_netns_exists(False)

        with mock.patch(
                'shakenfist.daemons.privexec.util.check_for_interface',
                return_value=True):
            reply = self.job._remove_floating_ip(self._request())

        self.assertEqual(privexec_pb2.RemoveFloatingIPReply.FAILED,
                         reply.remove_floating_ip_reply.error)
        self.assertIn('boom', reply.remove_floating_ip_reply.error_text)

    def test_remove_is_best_effort_after_dnat_failure(self):
        # A DNAT delete failing must not abort the interface deletion --
        # otherwise a single stuck rule leaks the veth pair, which is
        # exactly the leak class this code exists to stop. The failure is
        # still reported, but the interface is deleted regardless.
        listing = '\n'.join([
            '-P PREROUTING ACCEPT',
            ('-A PREROUTING -d 192.168.10.44/32 -j DNAT '
             '--to-destination 10.0.0.56'),
        ])
        recorder = self.patch_commands(results={
            '-S PREROUTING': (listing, '', 0),
            ' -D ': ('', 'dnat boom', 1)})
        self.patch_netns_exists(True)

        with mock.patch(
                'shakenfist.daemons.privexec.util.check_for_interface',
                return_value=True):
            reply = self.job._remove_floating_ip(self._request())

        # The interface deletion still ran despite the earlier DNAT failure.
        self.assertEqual(
            [('ip', 'link', 'del', FLOATING_INTERFACE)],
            recorder.find('link del'))
        self.assertEqual(privexec_pb2.RemoveFloatingIPReply.FAILED,
                         reply.remove_floating_ip_reply.error)
        self.assertIn('dnat boom', reply.remove_floating_ip_reply.error_text)


class ConcurrencyFloatingIPTestCase(base.ShakenFistTestCase):
    """The client side must surface the error detail from the reply.

    A bare AddFloatingIPFailed with no message made this class of failure
    very hard to diagnose from centralised logging.
    """

    def test_add_floating_ip_raises_with_detail(self):
        reply = privexec_pb2.PrivExecReply(
            add_floating_ip_reply=privexec_pb2.AddFloatingIPReply(
                error=privexec_pb2.AddFloatingIPReply.ADD_ADDRESS_FAILED,
                error_text='failed to add 192.168.10.44/32'))
        with mock.patch(
                'shakenfist.util.concurrency._marshal_privexec_request',
                return_value=reply):
            exc = self.assertRaises(
                exceptions.AddFloatingIPFailed,
                util_concurrency.add_floating_ip,
                NETWORK_UUID, '192.168.10.44', '10.0.0.56')
        self.assertIn('ADD_ADDRESS_FAILED', str(exc))
        self.assertIn('failed to add 192.168.10.44/32', str(exc))

    def test_remove_floating_ip_raises_with_detail(self):
        reply = privexec_pb2.PrivExecReply(
            remove_floating_ip_reply=privexec_pb2.RemoveFloatingIPReply(
                error=privexec_pb2.RemoveFloatingIPReply.FAILED,
                error_text='failed to delete interface'))
        with mock.patch(
                'shakenfist.util.concurrency._marshal_privexec_request',
                return_value=reply):
            exc = self.assertRaises(
                exceptions.RemoveFloatingIPFailed,
                util_concurrency.remove_floating_ip,
                NETWORK_UUID, '192.168.10.44')
        self.assertIn('FAILED', str(exc))
        self.assertIn('failed to delete interface', str(exc))
