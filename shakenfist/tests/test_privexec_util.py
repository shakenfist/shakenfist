# Copyright 2026 Michael Still and contributors
"""Tests for the privexec util helpers.

create_network_namespace() must leave the namespace's loopback
interface up. A namespace from "ip netns add" has it down, and a down
loopback drops a packet to an address which is local to the namespace
rather than refusing to send it, so the DHCP readiness probe read its
own undelivered request as a wedged dnsmasq (issue 730).

create_interface() must report why an interface could not be created,
not just that it could not be. The captured stderr carries the kernel's
errno text (for example "No such file or directory" when the target
network namespace was torn down underneath the operation), and dropping
it made the issue 3608 CREATE_INTERFACE_FAILED occurrences
undiagnosable from centralised logging.
"""

from unittest import mock

from shakenfist.daemons.privexec import util as privexec_util
from shakenfist.tests import base


NETWORK_UUID = 'ccc652aa-f7b6-4f99-b76d-443ae4d91412'


class CreateInterfaceTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        for target, kwargs in [
                ('shakenfist.daemons.privexec.util.locate_command',
                 {'side_effect': lambda c: c}),
                ('shakenfist.daemons.privexec.util.check_for_interface',
                 {'return_value': False}),
                ('shakenfist.daemons.privexec.util.time.sleep',
                 {'return_value': None})]:
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def patch_command_helper(self, side_effect):
        patcher = mock.patch(
            'shakenfist.daemons.privexec.util.command_helper',
            side_effect=side_effect)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def test_success_returns_no_error(self):
        self.patch_command_helper(lambda *c, **kw: ('', '', 0))

        success, error = privexec_util.create_interface(
            'flt-c0a80a2c', 'veth', ['peer', 'name', 'flt-c0a80a2c-i'],
            inner_namespace=NETWORK_UUID)

        self.assertTrue(success)
        self.assertEqual('', error)

    def test_link_add_failure_surfaces_stderr(self):
        self.patch_command_helper(
            lambda *c, **kw: ('', 'RTNETLINK answers: File exists\n', 2))

        success, error = privexec_util.create_interface(
            'flt-c0a80a2c', 'veth', ['peer', 'name', 'flt-c0a80a2c-i'],
            inner_namespace=NETWORK_UUID)

        self.assertFalse(success)
        self.assertIn('ip link add exited 2', error)
        self.assertIn('RTNETLINK answers: File exists', error)

    def test_netns_move_failure_surfaces_stderr(self):
        # The veth pair is created, but the move of the inner end fails
        # because the namespace no longer exists -- the "network torn
        # down underneath the operation" candidate from issue 3608.
        def responder(*command, **kwargs):
            if 'netns' in command:
                return ('',
                        f'Cannot open network namespace "{NETWORK_UUID}": '
                        f'No such file or directory\n', 1)
            return ('', '', 0)

        self.patch_command_helper(responder)

        success, error = privexec_util.create_interface(
            'flt-c0a80a2c', 'veth', ['peer', 'name', 'flt-c0a80a2c-i'],
            inner_namespace=NETWORK_UUID)

        self.assertFalse(success)
        self.assertIn(f'ip link set netns {NETWORK_UUID} exited 1', error)
        self.assertIn('No such file or directory', error)


class CreateNetworkNamespaceTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        patcher = mock.patch(
            'shakenfist.daemons.privexec.util.locate_command',
            side_effect=lambda c: c)
        patcher.start()
        self.addCleanup(patcher.stop)

    def patch_command_helper(self, side_effect, exists=False):
        for target, kwargs in [
                ('shakenfist.daemons.privexec.util.os.path.exists',
                 {'return_value': exists}),
                ('shakenfist.daemons.privexec.util.command_helper',
                 {'side_effect': side_effect})]:
            patcher = mock.patch(target, **kwargs)
            mocked = patcher.start()
            self.addCleanup(patcher.stop)
        return mocked

    def _commands(self, mocked):
        return [' '.join(c.args) for c in mocked.call_args_list]

    def test_a_new_namespace_has_its_loopback_brought_up(self):
        mocked = self.patch_command_helper(lambda *c, **kw: ('', '', 0))

        self.assertTrue(privexec_util.create_network_namespace(NETWORK_UUID))
        self.assertEqual(
            [f'ip netns add {NETWORK_UUID}',
             f'ip netns exec {NETWORK_UUID} ip link set lo up'],
            self._commands(mocked))

    def test_an_existing_namespace_has_its_loopback_brought_up(self):
        # The heal: a namespace created before this fix is not added
        # again, so bringing lo up cannot be part of the add.
        mocked = self.patch_command_helper(
            lambda *c, **kw: ('', '', 0), exists=True)

        self.assertTrue(privexec_util.create_network_namespace(NETWORK_UUID))
        self.assertEqual(
            [f'ip netns exec {NETWORK_UUID} ip link set lo up'],
            self._commands(mocked))

    def test_a_loopback_which_cannot_be_brought_up_fails_the_create(self):
        # Reporting success here would leave a namespace in which every
        # DHCP probe times out, and so a network which is rebuilt on
        # every maintenance pass.
        def responder(*command, **kwargs):
            if 'link' in command:
                return ('', 'Cannot open network namespace: No such file\n', 1)
            return ('', '', 0)

        self.patch_command_helper(responder)

        self.assertFalse(privexec_util.create_network_namespace(NETWORK_UUID))
