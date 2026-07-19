import threading
import time

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
