# Copyright 2026 Michael Still and contributors
"""Tests for the privexec util helpers.

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
