# Copyright 2026 Michael Still and contributors
"""Tests for the privexec execute handler.

ionice and ip netns exec run an executable, not a shell command line. The
handler used to glue them onto the front of the request's command string,
so for `a | b` only `a` was wrapped, and valid shell such as `FOO=bar cmd`
failed outright because ionice tried to execute `FOO=bar`. The wrappers
are now an argv list around a quoted inner `/bin/sh -c`, matching the
in-guest agent's executor (shakenfist/agent-python#144).
"""

import shutil
from unittest import mock

from shakenfist.daemons.privexec import main as privexec_main
from shakenfist.protos import common_pb2
from shakenfist.tests import base


class PrivExecExecuteTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.locate_command = mock.patch(
            'shakenfist.daemons.privexec.util.locate_command',
            side_effect=lambda c: c)
        self.locate_command.start()
        self.addCleanup(self.locate_command.stop)

        self.job = privexec_main.PrivExecJob(mock.MagicMock())

    def _mocked_execute(self, ionice, network_namespace, io_priority):
        # Run an execute request with Popen mocked out and return the
        # command line Popen was given.
        with mock.patch('psutil.Process') as mock_process, \
                mock.patch('subprocess.Popen') as mock_popen:
            mock_process.return_value.ionice.return_value = ionice
            mock_popen.return_value.communicate.return_value = (b'', b'')
            mock_popen.return_value.returncode = 0

            reply = self.job._execute(common_pb2.ExecuteRequest(
                command="FOO='a b' cmd | other",
                network_namespace=network_namespace,
                io_priority=io_priority))

            self.assertTrue(reply.HasField('execute_reply'))
            self.assertTrue(mock_popen.call_args.kwargs['shell'])
            return mock_popen.call_args.args[0]

    def test_wrapped_command(self):
        # Wrappers must apply to a quoted inner shell, not to the first
        # word of the command line.
        command = self._mocked_execute(
            (0, 0), 'ns1', common_pb2.ExecuteRequest.HIGH)
        self.assertEqual(
            "ip netns exec ns1 ionice -c 2 -n 0 /bin/sh -c "
            "'FOO='\"'\"'a b'\"'\"' cmd | other'",
            command)

    def test_network_namespace_only(self):
        command = self._mocked_execute(
            (2, 4), 'ns1', common_pb2.ExecuteRequest.NORMAL)
        self.assertEqual(
            "ip netns exec ns1 /bin/sh -c 'FOO='\"'\"'a b'\"'\"' cmd | other'",
            command)

    def test_ionice_only(self):
        command = self._mocked_execute(
            (0, 0), '', common_pb2.ExecuteRequest.LOW)
        self.assertEqual(
            "ionice -c 2 -n 7 /bin/sh -c 'FOO='\"'\"'a b'\"'\"' cmd | other'",
            command)

    def test_network_namespace_is_quoted(self):
        command = self._mocked_execute(
            (2, 4), 'ns1; touch /tmp/x', common_pb2.ExecuteRequest.NORMAL)
        self.assertTrue(
            command.startswith(
                "ip netns exec 'ns1; touch /tmp/x' /bin/sh -c "),
            command)

    def test_unwrapped_command_is_verbatim(self):
        # No wrapper applies, so the shell command line is passed through
        # untouched.
        command = self._mocked_execute(
            (2, 4), '', common_pb2.ExecuteRequest.NORMAL)
        self.assertEqual("FOO='a b' cmd | other", command)

    def _real_execute(self, command):
        # LOW forces the ionice wrapper unless the test process itself
        # already runs at that priority, which nothing in CI does.
        reply = self.job._execute(common_pb2.ExecuteRequest(
            command=command,
            io_priority=common_pb2.ExecuteRequest.LOW))
        return reply.execute_reply

    def test_execute_environment_prefix(self):
        if not shutil.which('ionice'):
            self.skipTest('ionice is not installed')
        reply = self._real_execute(
            'SF_TEST_VALUE=banana printenv SF_TEST_VALUE')
        self.assertEqual(0, reply.exit_code, reply.stderr)
        self.assertEqual('banana\n', reply.stdout)

    def test_execute_pipeline(self):
        if not shutil.which('ionice'):
            self.skipTest('ionice is not installed')
        reply = self._real_execute('echo banana | tr a-z A-Z')
        self.assertEqual(0, reply.exit_code, reply.stderr)
        self.assertEqual('BANANA\n', reply.stdout)
