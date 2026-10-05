# Copyright 2026 Michael Still and contributors
#
# End to end tests for what POST /instances/<ref>/poweron answers
# (InstancePowerOnEndpoint).

import json
from unittest import mock
from uuid import UUID
from uuid import uuid4

from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.external_api import app as external_api
from shakenfist.instance import Instance
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class InstancePowerOnEndpointTestCase(base.ShakenFistTestCase):
    """A power on which failed answers 500 with the hypervisor's last error,
    not 200 with a null body (F4).
    """

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        # Placed on this node so redirect_instance_request() runs the
        # handler here rather than proxying it.
        saved_node_uuid = config.NODE_UUID
        self.addCleanup(setattr, config, 'NODE_UUID', saved_node_uuid)
        config.NODE_UUID = self.mock_mariadb.node_uuids['node1_net']

        self.instance_uuid = str(uuid4())
        self.mock_mariadb.create_instance(
            'powerme', uuid=self.instance_uuid, namespace='foo',
            set_state=dbo.STATE_CREATED, place_on_node=config.NODE_UUID)

        # The endpoint takes the instance's node lock, whose daemon a unit
        # test does not run.
        p = mock.patch.object(Instance, 'get_lock')
        p.start()
        self.addCleanup(p.stop)

        self.client = external_api.app.test_client()
        resp = self.client.post('/auth', data=json.dumps(
            {'namespace': 'foo', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth = {
            'Authorization': 'Bearer %s' % resp.get_json()['access_token']}

    def _power_on(self, **power_on_kwargs):
        with mock.patch.object(Instance, 'power_on', **power_on_kwargs):
            return self.client.post(
                '/instances/%s/poweron' % self.instance_uuid,
                headers=self.auth, data=json.dumps({}))

    def test_success_answers_200_with_null_body(self):
        resp = self._power_on(return_value=None)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertIsNone(resp.get_json())

    def test_failed_power_on_answers_500_with_the_error(self):
        resp = self._power_on(side_effect=exceptions.InstancePowerOnFailed(
            'unhandled instance start error: no disk'))

        self.assertEqual(500, resp.status_code)
        self.assertEqual(
            'instance failed to power on: unhandled instance start error: '
            'no disk', resp.get_json()['error'])
        eventlog.add_event_multi.assert_any_call(
            EVENT_TYPE_AUDIT, [('instance', UUID(self.instance_uuid))],
            'power on failed', duration=None,
            extra={'error': 'unhandled instance start error: no disk'},
            suppress_event_logging=False, log_as_error=False)

    def test_paused_instance_answers_409(self):
        resp = self._power_on(side_effect=exceptions.InvalidLifecycleState(
            'you cannot power on a paused instance; unpause it instead'))
        self.assertEqual(409, resp.status_code)
        self.assertIn('unpause it instead', resp.get_json()['error'])
