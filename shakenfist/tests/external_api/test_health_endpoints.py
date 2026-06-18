# Copyright 2019 Michael Still and contributors
import logging
import sys
import time
from unittest import mock

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.external_api import health
from shakenfist.tests import base


class HealthEndpointTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        # The before_request hook resolves config.NODE_UUID, hitting the
        # database if it is not already set. The health endpoints have no DB
        # dependency, so pin the UUID to keep these tests hermetic.
        self.saved_node_uuid = config.NODE_UUID
        config.NODE_UUID = 'test-node-uuid'
        self.addCleanup(self._restore_node_uuid)

        # Reset per-worker readiness state between tests.
        health._reset_for_test()
        self.addCleanup(health._reset_for_test)

        self.client = external_api.app.test_client()

    def _restore_node_uuid(self):
        config.NODE_UUID = self.saved_node_uuid

    def _set_ready(self):
        health.ready = True
        health.last_update = time.time()
        health.draining = False

    def test_health_probe_makes_no_db_call_when_node_uuid_unset(self):
        # The no-DB guarantee for health probes must hold even on a worker
        # whose NODE_UUID is not yet resolved: resolve_node_uuid short-circuits
        # for health-probe paths before its Node.from_db fallback.
        config.NODE_UUID = None
        self._set_ready()
        with mock.patch.object(external_api.Node, 'from_db') as mock_from_db:
            resp = self.client.get('/readyz')
            self.assertEqual(200, resp.status_code)
            mock_from_db.assert_not_called()

    def test_livez_always_ok(self):
        # Even with readiness state unset (not ready), liveness is 200.
        resp = self.client.get('/livez')
        self.assertEqual(200, resp.status_code)
        self.assertEqual(b'ok', resp.get_data())
        self.assertNotEqual(401, resp.status_code)

    def test_livez_no_auth_required(self):
        resp = self.client.get('/livez')
        self.assertNotEqual(401, resp.status_code)

    def test_readyz_ready(self):
        self._set_ready()
        resp = self.client.get('/readyz')
        self.assertEqual(200, resp.status_code)
        self.assertEqual(b'ready', resp.get_data())
        self.assertNotEqual(401, resp.status_code)

    def test_readyz_not_ready(self):
        # Default state after _reset_for_test() is not ready.
        resp = self.client.get('/readyz')
        self.assertEqual(503, resp.status_code)
        self.assertEqual(b'not ready', resp.get_data())
        self.assertNotEqual(401, resp.status_code)

    def test_readyz_not_ready_when_draining(self):
        self._set_ready()
        health.draining = True
        resp = self.client.get('/readyz')
        self.assertEqual(503, resp.status_code)
        self.assertEqual(b'not ready', resp.get_data())

    def test_readyz_with_mocked_is_ready(self):
        with mock.patch('shakenfist.external_api.health.is_ready',
                        return_value=True):
            resp = self.client.get('/readyz')
            self.assertEqual(200, resp.status_code)
            self.assertEqual(b'ready', resp.get_data())

        with mock.patch('shakenfist.external_api.health.is_ready',
                        return_value=False):
            resp = self.client.get('/readyz')
            self.assertEqual(503, resp.status_code)
            self.assertEqual(b'not ready', resp.get_data())

    def test_healthz_ready(self):
        self._set_ready()
        resp = self.client.get('/healthz')
        self.assertEqual(200, resp.status_code)
        self.assertEqual(b'ready', resp.get_data())
        self.assertNotEqual(401, resp.status_code)

    def test_healthz_not_ready(self):
        resp = self.client.get('/healthz')
        self.assertEqual(503, resp.status_code)
        self.assertEqual(b'not ready', resp.get_data())
        self.assertNotEqual(401, resp.status_code)
