import os
import tempfile
import threading
import time
from unittest import mock

import testtools

from shakenfist import resource_health
from shakenfist.tests import base


class HealthResultTestCase(base.ShakenFistTestCase):
    def test_healthy_only_for_ok(self):
        ok = resource_health.HealthResult('x', resource_health.HealthStatus.OK)
        self.assertTrue(ok.healthy)
        for status in [resource_health.HealthStatus.MISSING,
                       resource_health.HealthStatus.READONLY,
                       resource_health.HealthStatus.UNWRITABLE,
                       resource_health.HealthStatus.TIMEOUT]:
            self.assertFalse(
                resource_health.HealthResult('x', status).healthy,
                f'{status} must not be healthy')


class DeadlineProbeTestCase(base.ShakenFistTestCase):
    def test_completes_promptly(self):
        probe = resource_health.DeadlineProbe()
        completed, result = probe.run(lambda: 'value', 1.0)
        self.assertTrue(completed)
        self.assertEqual('value', result)

    def test_exception_propagates(self):
        probe = resource_health.DeadlineProbe()

        def boom():
            raise ValueError('nope')

        with testtools.ExpectedException(ValueError):
            probe.run(boom, 1.0)

    def test_completed_probe_with_lingering_thread_is_not_outstanding(self):
        # The worker sets its completion event before its thread terminates,
        # so on a loaded machine a completed probe's thread can still report
        # is_alive() when the next run() arrives. That must not be mistaken
        # for an outstanding probe. Reproduce the window deterministically by
        # standing in a still-alive thread while the completion event is set.
        probe = resource_health.DeadlineProbe()
        completed, result = probe.run(lambda: 'first', 1.0)
        self.assertTrue(completed)
        self.assertEqual('first', result)

        release = threading.Event()
        stand_in = threading.Thread(
            target=release.wait, args=(5,), daemon=True)
        stand_in.start()
        self.addCleanup(release.set)
        probe._thread = stand_in
        self.assertTrue(probe._completed.is_set())

        completed, result = probe.run(lambda: 'second', 1.0)
        self.assertTrue(completed)
        self.assertEqual('second', result)

    def test_timeout_leaves_probe_outstanding_and_is_not_relaunched(self):
        probe = resource_health.DeadlineProbe()
        release = threading.Event()
        starts = []

        def blocker():
            starts.append(1)
            release.wait(5)
            return 'late'

        # First run blocks past the deadline and times out.
        completed, result = probe.run(blocker, 0.05)
        self.assertFalse(completed)
        self.assertIsNone(result)

        # A second run while the first is still blocked must NOT start a
        # new probe -- the blocker body has run exactly once.
        completed, result = probe.run(blocker, 0.05)
        self.assertFalse(completed)
        self.assertEqual(1, len(starts))

        # Once the blocked probe is released, its daemon thread ends and a
        # fresh run succeeds.
        release.set()
        completed = False
        result = None
        for _ in range(200):
            completed, result = probe.run(lambda: 'fresh', 1.0)
            if completed:
                break
            time.sleep(0.01)
        self.assertTrue(completed)
        self.assertEqual('fresh', result)
        # The blocker still only ever started once.
        self.assertEqual(1, len(starts))


class PathCheckTestCase(base.ShakenFistTestCase):
    def test_healthy_writes_heartbeat(self):
        with tempfile.TemporaryDirectory() as d:
            result = resource_health.PathCheck(d).check()
            self.assertEqual(resource_health.HealthStatus.OK, result.status)
            self.assertTrue(result.healthy)
            heartbeat = os.path.join(
                d, resource_health.HEARTBEAT_FILENAME)
            self.assertTrue(os.path.exists(heartbeat))
            with open(heartbeat) as f:
                float(f.read().strip())  # a bare timestamp, parses as a float

    def test_identity_is_abspath_and_dedups(self):
        a = resource_health.PathCheck('/srv/shakenfist/blobs')
        b = resource_health.PathCheck('/srv/shakenfist/../shakenfist/blobs')
        self.assertEqual('/srv/shakenfist/blobs', a.identity)
        self.assertEqual(a.identity, b.identity)

    def test_missing_on_statvfs_error(self):
        check = resource_health.PathCheck('/does/not/matter')
        with mock.patch('os.statvfs',
                        side_effect=OSError(5, 'Input/output error')):
            result = check.check()
        self.assertEqual(resource_health.HealthStatus.MISSING, result.status)
        self.assertIn('Input/output error', result.detail)

    def test_ensure_present_creates_absent_directory(self):
        # ensure_present() provisions a not-yet-created subdir at startup, so
        # the probe never sees a benign ENOENT.
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, 'uploads')
            self.assertFalse(os.path.exists(target))
            check = resource_health.PathCheck(target)
            check.ensure_present()
            self.assertTrue(os.path.isdir(target))
            # And it then probes healthy.
            self.assertEqual(
                resource_health.HealthStatus.OK, check.check().status)

    def test_missing_when_directory_absent_after_provisioning(self):
        # Once probing has begun, an ENOENT means the store vanished (not a
        # not-yet-provisioned subdir), so it is a MISSING fault -- the probe
        # does not silently recreate it.
        check = resource_health.PathCheck('/does/not/exist/at/all')
        with mock.patch('os.makedirs') as mock_makedirs:
            result = check.check()
        self.assertEqual(resource_health.HealthStatus.MISSING, result.status)
        mock_makedirs.assert_not_called()

    def test_readonly_when_st_rdonly_set(self):
        check = resource_health.PathCheck('/does/not/matter')
        fake = mock.Mock()
        fake.f_flag = os.ST_RDONLY
        with mock.patch('os.statvfs', return_value=fake):
            result = check.check()
        self.assertEqual(resource_health.HealthStatus.READONLY, result.status)

    def test_unwritable_when_write_fails(self):
        fake = mock.Mock()
        fake.f_flag = 0  # not read-only, so the write is attempted
        check = resource_health.PathCheck('/does/not/matter')
        with mock.patch('os.statvfs', return_value=fake), \
                mock.patch('os.open',
                           side_effect=OSError(30, 'Read-only file system')):
            result = check.check()
        self.assertEqual(
            resource_health.HealthStatus.UNWRITABLE, result.status)

    def test_write_interval_gates_the_heartbeat(self):
        # With a large interval, the first check writes and the second (soon
        # after) does not: only one heartbeat write, clock unadvanced.
        with tempfile.TemporaryDirectory() as d:
            check = resource_health.PathCheck(d, write_interval=3600)
            # A realistic epoch: with last_write=0 the first check is always
            # due to write (now >> interval); the second, 5s later, is not.
            #
            # Use a stateful clock rather than a positional side_effect list:
            # check() runs _probe_once (which also calls time.time()) in a
            # separate DeadlineProbe thread, so the number and interleaving of
            # time.time() calls across threads is not deterministic. Returning a
            # single consistent value per check makes the gate deterministic
            # regardless of how the calls are scheduled.
            clock = {'t': 100000.0}
            base_t = clock['t']
            real_open = os.open
            opened = []

            def counting_open(path, *a, **kw):
                # Only count heartbeat opens under this test's own tempdir:
                # os.open is patched process-wide, and a probe thread leaked
                # by another test in this worker could otherwise open its own
                # heartbeat inside our patch window and inflate the count.
                if (isinstance(path, str) and path.startswith(d) and
                        path.endswith(resource_health.HEARTBEAT_FILENAME)):
                    opened.append(path)
                return real_open(path, *a, **kw)

            with mock.patch('shakenfist.resource_health.time.time',
                            side_effect=lambda: clock['t']), \
                    mock.patch('os.open', side_effect=counting_open):
                first = check.check()
                clock['t'] = base_t + 5
                second = check.check()

            self.assertEqual(resource_health.HealthStatus.OK, first.status)
            self.assertEqual(resource_health.HealthStatus.OK, second.status)
            self.assertEqual(1, len(opened))

    def test_back_to_back_checks_survive_lingering_worker_thread(self):
        # The flake behind issue #3551: a check() whose probe completed can
        # leave its worker thread alive for a few more instructions, and a
        # back-to-back second check() must still report OK rather than
        # TIMEOUT. Simulate the scheduler latency with a stand-in thread
        # that is still alive after the first probe's event has fired.
        with tempfile.TemporaryDirectory() as d:
            check = resource_health.PathCheck(d)
            first = check.check()
            self.assertEqual(resource_health.HealthStatus.OK, first.status)

            release = threading.Event()
            stand_in = threading.Thread(
                target=release.wait, args=(5,), daemon=True)
            stand_in.start()
            self.addCleanup(release.set)
            check._probe._thread = stand_in

            second = check.check()
            self.assertEqual(resource_health.HealthStatus.OK, second.status)

    def test_timeout_when_probe_hangs(self):
        check = resource_health.PathCheck('/does/not/matter', timeout=0.05)
        release = threading.Event()

        def hang(_path):
            release.wait(5)
            return mock.Mock(f_flag=0)

        with mock.patch('os.statvfs', side_effect=hang):
            result = check.check()
            self.assertEqual(
                resource_health.HealthStatus.TIMEOUT, result.status)
            # A second call while the first probe is still blocked also
            # times out, without launching a new probe.
            result = check.check()
            self.assertEqual(
                resource_health.HealthStatus.TIMEOUT, result.status)
        release.set()
        # Join the probe's daemon thread before finishing. Once released it
        # resumes _probe_once with do_write=True and opens its heartbeat
        # file; unjoined, that open races into whichever test runs next in
        # this worker (seen as test_write_interval_gates_the_heartbeat
        # counting a stray os.open under its process-wide patch).
        check._probe._thread.join(5)
        self.assertFalse(check._probe._thread.is_alive())
