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

from shakenfist.daemons.privexec import main as privexec_main
from shakenfist.protos import privexec_pb2
from shakenfist.tests import base


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
