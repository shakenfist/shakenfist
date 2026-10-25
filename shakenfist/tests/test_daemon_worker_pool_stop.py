# Copyright 2026 Michael Still and contributors
"""Tests that a worker pool daemon stops inside TimeoutStopSec.

WorkerPoolDaemon.run() used to wait for every worker to notice its abort path
with no deadline. A worker that never looks -- an image fetch, a long privexec
command -- held sf-queues until systemd SIGKILLed it, so the daemon never
recorded that it had stopped (#4550). The wait now gives up after
WORKER_STOP_GRACE.
"""

import os
import re
import threading
import time
from unittest import mock

from shakenfist.daemons import daemon
from shakenfist.tests import base


UNIT_TEMPLATE = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'collection', 'roles', 'node', 'templates', 'sf.service'))


class FakeWorker:
    def __init__(self, abort_path):
        self.abort_path = abort_path


class WorkerPoolStopTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.release = threading.Event()
        self.addCleanup(self.release.set)

        self.pool = daemon.WorkerPoolDaemon.__new__(daemon.WorkerPoolDaemon)
        self.pool.workers = {}
        self.pool._run_inner = mock.MagicMock()
        self.pool.record_start = mock.MagicMock()
        self.pool.record_exit = mock.MagicMock()
        self.pool._resolve_node_uuid = mock.MagicMock()

        self.aborted = set()
        for p in [
                mock.patch.object(daemon, 'WORKER_STOP_GRACE', 0.5),
                mock.patch.object(daemon, 'WORKER_STOP_POLL', 0.05),
                mock.patch.object(daemon, 'send_systemd_status'),
                mock.patch.object(daemon, 'set_abort_path',
                                  side_effect=lambda path, source: self.aborted.add(path))]:
            p.start()
            self.addCleanup(p.stop)

    def _add_worker(self, name, honours_abort):
        abort_path = f'/run/sf/test-{name}.abort'

        def body():
            while not self.release.is_set():
                if honours_abort and abort_path in self.aborted:
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=body, daemon=True, name=name)
        self.pool.workers[name] = {'object': FakeWorker(abort_path), 'thread': thread}
        thread.start()
        return abort_path

    def test_abandons_a_worker_which_ignores_its_abort_path(self):
        abort_path = self._add_worker('stuck', honours_abort=False)

        start = time.monotonic()
        with mock.patch.object(daemon, 'LOG') as log:
            self.pool.run()
        elapsed = time.monotonic() - start

        self.assertIn(abort_path, self.aborted)
        self.assertLess(elapsed, 3)
        self.assertGreaterEqual(elapsed, 0.5)
        self.pool.record_exit.assert_called_once()
        log.with_fields.assert_any_call({'workers': ['stuck']})

    def test_waits_for_workers_which_stop(self):
        self._add_worker('polite', honours_abort=True)

        self.pool.run()

        self.assertEqual({}, self.pool.workers)
        self.pool.record_exit.assert_called_once()


class WorkerStopGraceTestCase(base.ShakenFistTestCase):
    def test_grace_fits_inside_the_units_stop_timeout(self):
        # Leave the daemon time for record_exit() before systemd's SIGKILL.
        with open(UNIT_TEMPLATE) as f:
            timeouts = re.findall(r'^TimeoutStopSec=(\d+)s$', f.read(), re.MULTILINE)
        self.assertEqual(1, len(timeouts))
        self.assertLessEqual(daemon.WORKER_STOP_GRACE + 5, int(timeouts[0]))
