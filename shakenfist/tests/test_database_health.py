# Copyright 2019 Michael Still and contributors

import concurrent.futures

import grpc
from grpc_health.v1 import health
from grpc_health.v1 import health_pb2
from grpc_health.v1 import health_pb2_grpc

from shakenfist.tests import base


class HealthServicerEndToEndTestCase(base.ShakenFistTestCase):
    """Smoke test: HealthServicer wiring via a real in-process gRPC channel."""

    def test_health_servicer_reports_serving_then_not_serving(self):
        server = grpc.server(
            concurrent.futures.ThreadPoolExecutor(max_workers=1))
        servicer = health.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(servicer, server)
        servicer.set('', health_pb2.HealthCheckResponse.SERVING)
        port = server.add_insecure_port('127.0.0.1:0')
        server.start()
        try:
            channel = grpc.insecure_channel(f'127.0.0.1:{port}')
            stub = health_pb2_grpc.HealthStub(channel)

            resp = stub.Check(health_pb2.HealthCheckRequest(service=''))
            self.assertEqual(
                resp.status,
                health_pb2.HealthCheckResponse.SERVING,
                'Expected SERVING after initial set()',
            )

            servicer.set('', health_pb2.HealthCheckResponse.NOT_SERVING)
            resp = stub.Check(health_pb2.HealthCheckRequest(service=''))
            self.assertEqual(
                resp.status,
                health_pb2.HealthCheckResponse.NOT_SERVING,
                'Expected NOT_SERVING after flip',
            )
        finally:
            server.stop(grace=0).wait()
