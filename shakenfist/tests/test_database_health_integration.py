# Copyright 2019 Michael Still and contributors

"""Integration-flavoured test: _update_health() -> servicer.set() -> Check().

This test proves that the dependency-aware path is end-to-end observable
at the gRPC protocol level. It stands up a real in-process gRPC server (the
same approach as test_database_health.py), injects a Monitor with
`mariadb.check_reachable` patched, calls _update_health(), and then asserts
that a real grpc.health.v1 Check over the in-process channel returns the
expected status. This is the path that grpc-health-probe and sf-api's
readiness checker use in production.
"""

import concurrent.futures
from unittest import mock

import grpc
from grpc_health.v1 import health
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

from shakenfist.daemons.database import main as database_main
from shakenfist.tests import base


def _make_monitor(health_servicer: health.HealthServicer) -> database_main.Monitor:
    """Build a Monitor without running its heavy __init__.

    The real __init__ registers Prometheus metrics globally and starts an
    HTTP metrics server, neither of which is relevant to the health-update
    logic under test. We construct via __new__ and set only the attributes
    _update_health() touches, mirroring the pattern in
    test_database_health_loop.py.
    """
    m = database_main.Monitor.__new__(database_main.Monitor)
    m.health_servicer = health_servicer
    m._last_health_status = None
    return m


class DatabaseHealthIntegrationTestCase(base.ShakenFistTestCase):
    """End-to-end test: _update_health() drives a real gRPC health channel.

    Each test method spins up an in-process gRPC server, calls
    _update_health() with check_reachable patched, then issues a real
    grpc.health.v1/Check RPC over the in-process channel and asserts on
    the protocol-level response. This confirms that the servicer.set()
    inside _update_health() is observable via the same path that
    grpc-health-probe and sf-api's readiness checker use.
    """

    def setUp(self) -> None:
        super().setUp()
        self.server = grpc.server(
            concurrent.futures.ThreadPoolExecutor(max_workers=1))
        self.servicer = health.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(self.servicer, self.server)
        port = self.server.add_insecure_port('127.0.0.1:0')
        self.server.start()
        # Disable http_proxy so the loopback connection is not routed through
        # any ambient proxy (CI runners typically set http_proxy).
        self.channel = grpc.insecure_channel(
            f'127.0.0.1:{port}',
            options=[('grpc.enable_http_proxy', 0)])
        self.stub = health_pb2_grpc.HealthStub(self.channel)
        self.monitor = _make_monitor(self.servicer)

    def tearDown(self) -> None:
        self.server.stop(grace=0).wait()
        super().tearDown()

    @mock.patch('shakenfist.mariadb.check_reachable', return_value=False)
    def test_unreachable_check_returns_not_serving(self, _mock_reachable):
        """_update_health() with unreachable MariaDB -> Check returns NOT_SERVING."""
        self.monitor._update_health()

        resp = self.stub.Check(health_pb2.HealthCheckRequest(service=''))
        self.assertEqual(
            resp.status,
            health_pb2.HealthCheckResponse.NOT_SERVING,
            'Expected NOT_SERVING after unreachable poll',
        )

    @mock.patch('shakenfist.mariadb.check_reachable', return_value=True)
    def test_reachable_check_returns_serving(self, _mock_reachable):
        """_update_health() with reachable MariaDB -> Check returns SERVING."""
        self.monitor._update_health()

        resp = self.stub.Check(health_pb2.HealthCheckRequest(service=''))
        self.assertEqual(
            resp.status,
            health_pb2.HealthCheckResponse.SERVING,
            'Expected SERVING after reachable poll',
        )

    def test_unreachable_then_reachable_transitions_check_status(self):
        """Transition from NOT_SERVING to SERVING is visible via Check."""
        with mock.patch('shakenfist.mariadb.check_reachable', return_value=False):
            self.monitor._update_health()

        resp = self.stub.Check(health_pb2.HealthCheckRequest(service=''))
        self.assertEqual(
            resp.status,
            health_pb2.HealthCheckResponse.NOT_SERVING,
            'Expected NOT_SERVING after first (unreachable) poll',
        )

        with mock.patch('shakenfist.mariadb.check_reachable', return_value=True):
            self.monitor._update_health()

        resp = self.stub.Check(health_pb2.HealthCheckRequest(service=''))
        self.assertEqual(
            resp.status,
            health_pb2.HealthCheckResponse.SERVING,
            'Expected SERVING after second (reachable) poll',
        )
