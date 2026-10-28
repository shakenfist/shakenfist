# Copyright 2026 Michael Still and contributors
"""Tests for sf-privexec ending in-flight requests when it stops.

The old drain loop read job attributes which did not exist (task_info,
pid), so it raised AttributeError on its first line whenever a request was
still running, the daemon exited 1, and the caller got a truncated response
(#4563). Its kill would have come 30 seconds after the stop began anyway,
which is when systemd SIGKILLs the daemon. Nothing exercised the path.
These tests run real commands through a real job.
"""

import os
import re
import socket
import threading
import time
from unittest import mock

import jinja2

from shakenfist.daemons.privexec import main as privexec_main
from shakenfist.protos import common_pb2
from shakenfist.protos import privexec_pb2
from shakenfist.tests import base


UNIT_TEMPLATE = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'roles', 'node', 'templates', 'sf.service'))


class FakeThread:
    def __init__(self, alive):
        self.alive = alive
        self.ident = 1

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass


class PrivExecDrainTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        for p in [
                mock.patch.object(privexec_main, 'STOP_GRACE', 0.3),
                mock.patch.object(privexec_main, 'TERM_GRACE', 0.5),
                mock.patch.object(privexec_main, 'KILL_GRACE', 0.5),
                mock.patch.object(privexec_main, 'STOP_POLL', 0.05),
                mock.patch('shakenfist.daemons.privexec.util.locate_command', side_effect=lambda c: c)]:
            p.start()
            self.addCleanup(p.stop)
        self.readers = []

    def start_request(self, command, wait_for_start=True):
        """Run command through a real job on a thread, as a client would.

        Returns the worker, and a list which receives the reply. Like the real
        client (util.concurrency), ours sends one request, reads the reply and
        closes, which is what lets the job's thread finish.
        """
        ours, theirs = socket.socketpair()
        replies = []

        def client():
            ours.settimeout(30)
            try:
                data = ours.recv(102400)
                reply = privexec_pb2.PrivExecReply()
                reply.ParseFromString(data)
                replies.append(reply.execute_reply)
            finally:
                ours.close()

        job = privexec_main.PrivExecJob(theirs)
        thread = threading.Thread(target=job.run, daemon=True)
        thread.start()
        self.addCleanup(self.kill_leftover, job)

        request = privexec_pb2.PrivExecRequest(
            execute_request=common_pb2.ExecuteRequest(command=command))
        ours.sendall(request.SerializeToString())
        reader = threading.Thread(target=client, daemon=True)
        reader.start()
        self.readers.append(reader)
        if wait_for_start:
            deadline = time.monotonic() + 10
            while job.process is None and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(job.process, 'the command never started')
            self.assertEqual('execute_request', job.task_details['request'])
            # Wrapped in a quoted ionice command line when the priority
            # differs, so look for a fragment which survives the quoting.
            self.assertIn('sleep', job.task_details['command'])
        return {'object': job, 'thread': thread}, replies

    def kill_leftover(self, job):
        process = job.process
        if process:
            try:
                os.killpg(process.pid, 9)
            except ProcessLookupError:
                pass

    def read_reply(self, replies):
        for reader in self.readers:
            reader.join(10)
        self.assertEqual(1, len(replies), 'no reply reached the client')
        return replies[0]

    def test_a_command_which_honours_sigterm_is_ended_and_answered(self):
        worker, replies = self.start_request('sleep 60')
        start = time.monotonic()
        abandoned = privexec_main.drain_workers({'w': worker})
        elapsed = time.monotonic() - start

        self.assertEqual({}, abandoned)
        self.assertGreaterEqual(elapsed, 0.3)
        self.assertLess(elapsed, 0.3 + 0.5)
        self.assertEqual(-15, self.read_reply(replies).exit_code)

    def test_a_command_which_ignores_sigterm_is_killed_and_answered(self):
        worker, replies = self.start_request("trap '' TERM; exec sleep 60")
        start = time.monotonic()
        abandoned = privexec_main.drain_workers({'w': worker})
        elapsed = time.monotonic() - start

        self.assertEqual({}, abandoned)
        # SIGTERM ended the shell, but the sleep it started ignored it and held
        # the output open until the SIGKILL. The reply carries the exit code of
        # the command privexec started, the shell's.
        self.assertGreaterEqual(elapsed, 0.3 + 0.5)
        self.assertNotEqual(0, self.read_reply(replies).exit_code)

    def test_a_request_which_finishes_in_time_is_left_alone(self):
        worker, replies = self.start_request('sleep 0.1')
        with mock.patch.object(privexec_main.os, 'killpg', wraps=os.killpg) as killpg:
            abandoned = privexec_main.drain_workers({'w': worker})
        self.assertEqual({}, abandoned)
        killpg.assert_not_called()
        self.assertEqual(0, self.read_reply(replies).exit_code)

    def test_a_request_nothing_can_end_is_abandoned(self):
        job = privexec_main.PrivExecJob(mock.MagicMock())
        job.task_details = {'request': 'ensure_vxlan_mesh_request'}
        stuck = {'object': job, 'thread': FakeThread(alive=True)}

        start = time.monotonic()
        abandoned = privexec_main.drain_workers({'stuck': stuck})
        elapsed = time.monotonic() - start

        self.assertEqual(['stuck'], list(abandoned))
        self.assertGreaterEqual(elapsed, 0.3 + 0.5 + 0.5)
        self.assertLess(elapsed, 3)

    def test_finished_threads_are_reaped(self):
        done = {'object': privexec_main.PrivExecJob(mock.MagicMock()), 'thread': FakeThread(alive=False)}
        self.assertEqual({}, privexec_main.drain_workers({'done': done}))

    def test_a_job_records_nothing_between_requests(self):
        worker, replies = self.start_request('true', wait_for_start=False)
        self.read_reply(replies)
        deadline = time.monotonic() + 5
        while worker['object'].task_details is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNone(worker['object'].task_details)
        self.assertIsNone(worker['object'].process)


class PrivExecStopBudgetTestCase(base.ShakenFistTestCase):
    def test_the_drain_fits_inside_the_units_stop_timeout(self):
        # Leave a couple of seconds for the accept loop to notice and for exit.
        with open(UNIT_TEMPLATE) as f:
            timeouts = re.findall(r'^TimeoutStopSec=(\d+)s$', f.read(), re.MULTILINE)
        self.assertEqual(1, len(timeouts))
        total = privexec_main.STOP_GRACE + privexec_main.TERM_GRACE + privexec_main.KILL_GRACE
        self.assertLessEqual(total + 2, int(timeouts[0]))

    def test_only_privexec_itself_is_signalled_when_it_stops(self):
        # Its commands, and the dnsmasqs which daemonise from it, are in its
        # control group: the default KillMode SIGTERMed them all at the start
        # of the stop, before the drain could let them finish (#4508).
        with open(UNIT_TEMPLATE) as f:
            template = jinja2.Template(f.read())
        for item, expected in (('privexec', ['process']), ('net', []), ('queues', [])):
            with self.subTest(item):
                unit = template.render(item=item)
                self.assertEqual(expected, re.findall(r'^KillMode=(\w+)$', unit, re.MULTILINE))
