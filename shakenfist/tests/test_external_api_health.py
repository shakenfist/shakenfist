# Copyright 2019 Michael Still and contributors
"""Unit tests for the per-worker sf-api readiness module.

The HealthStub is always mocked -- these tests never touch the network.
"""
from unittest import mock

from grpc_health.v1 import health_pb2

from shakenfist.external_api import health
from shakenfist.tests import base


def _stub_returning(status):
    """Build a mock HealthStub whose Check returns the given status."""
    stub = mock.MagicMock()
    resp = mock.MagicMock()
    resp.status = status
    stub.Check.return_value = resp
    return stub


def _serving_stub():
    return _stub_returning(health_pb2.HealthCheckResponse.SERVING)


def _not_serving_stub():
    return _stub_returning(health_pb2.HealthCheckResponse.NOT_SERVING)


class ReadinessStateTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        health._reset_for_test()
        self.addCleanup(health._reset_for_test)

    def test_three_consecutive_failures_flip_ready_false(self):
        # Prime to ready so we can observe the flip.
        health._poll_once(_serving_stub())
        self.assertTrue(health.ready)

        stub = _not_serving_stub()

        # First failure: still ready (below threshold).
        health._poll_once(stub)
        self.assertEqual(1, health.consecutive_failures)
        self.assertTrue(health.ready)

        # Second failure: still ready.
        health._poll_once(stub)
        self.assertEqual(2, health.consecutive_failures)
        self.assertTrue(health.ready)

        # Third failure: now not ready.
        health._poll_once(stub)
        self.assertEqual(3, health.consecutive_failures)
        self.assertFalse(health.ready)

    def test_exception_counts_as_failure(self):
        health._poll_once(_serving_stub())
        self.assertTrue(health.ready)

        stub = mock.MagicMock()
        stub.Check.side_effect = RuntimeError('boom')

        for _ in range(health.READINESS_FAIL_THRESHOLD - 1):
            health._poll_once(stub)
            self.assertTrue(health.ready)
        health._poll_once(stub)
        self.assertFalse(health.ready)

    def test_none_stub_counts_as_failure(self):
        health._poll_once(_serving_stub())
        self.assertTrue(health.ready)
        for _ in range(health.READINESS_FAIL_THRESHOLD):
            health._poll_once(None)
        self.assertFalse(health.ready)

    def test_serving_poll_sets_ready_and_resets_counter(self):
        # Two failures so the counter is non-zero but ready not yet flipped.
        stub_bad = _not_serving_stub()
        health._poll_once(stub_bad)
        health._poll_once(stub_bad)
        self.assertEqual(2, health.consecutive_failures)

        health._poll_once(_serving_stub())
        self.assertTrue(health.ready)
        self.assertEqual(0, health.consecutive_failures)

    def test_poll_always_updates_last_update(self):
        before = health.last_update
        health._poll_once(_not_serving_stub())
        self.assertGreater(health.last_update, before)

    def test_is_ready_false_when_stale(self):
        health._poll_once(_serving_stub())
        self.assertTrue(health.is_ready())

        # Pretend the last update was longer ago than READINESS_STALE.
        health.last_update = health.last_update - (health.READINESS_STALE + 1)
        self.assertTrue(health.ready)
        self.assertFalse(health.is_ready())

    def test_is_ready_false_when_draining(self):
        health._poll_once(_serving_stub())
        self.assertTrue(health.is_ready())

        health.begin_drain()
        self.assertTrue(health.is_draining())
        self.assertTrue(health.ready)
        self.assertFalse(health.is_ready())

    def test_begin_drain_is_one_way(self):
        self.assertFalse(health.is_draining())
        health.begin_drain()
        self.assertTrue(health.is_draining())
        # Calling again stays draining.
        health.begin_drain()
        self.assertTrue(health.is_draining())


class StartCheckerTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        health._reset_for_test()
        self.addCleanup(health._reset_for_test)

    def test_start_checker_spawns_single_thread(self):
        created = []

        class _FakeThread:
            def __init__(self, *args, **kwargs):
                created.append(self)

            def start(self):
                pass

        with mock.patch('shakenfist.external_api.health.threading.Thread',
                        _FakeThread):
            health.start_checker()
            health.start_checker()

        self.assertEqual(1, len(created))
        self.assertIsNotNone(health._checker_thread)
