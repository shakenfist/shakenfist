# Copyright 2026 Michael Still and contributors
#
# End to end tests for what POST /instances/<ref>/poweron and
# POST /instances/<ref>/poweroff answer (InstancePowerOnEndpoint and
# InstancePowerOffEndpoint).

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


class InstancePowerEndpointTestCase(base.ShakenFistTestCase):
    """An authenticated client and an instance placed on this node, shared by
    the power on and power off endpoint tests.
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


class InstancePowerOnEndpointTestCase(InstancePowerEndpointTestCase):
    """A power on which failed answers 500, not 200 with a null body.
    The hypervisor's last error goes in an audit event, not the response,
    because it can name host paths.
    """

    def _power_on(self, **power_on_kwargs):
        with mock.patch.object(Instance, 'power_on', **power_on_kwargs):
            return self.client.post(
                '/instances/%s/poweron' % self.instance_uuid,
                headers=self.auth, data=json.dumps({}))

    def test_success_answers_200_with_null_body(self):
        resp = self._power_on(return_value=None)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertIsNone(resp.get_json())

    def test_failed_power_on_answers_500_and_records_the_error(self):
        error = ('unhandled instance start error: Cannot access storage file '
                 '/srv/shakenfist/instances/abc/vda')
        resp = self._power_on(side_effect=exceptions.InstancePowerOnFailed(
            error))

        self.assertEqual(500, resp.status_code)
        self.assertEqual(
            'instance failed to power on, see the instance events for '
            'details', resp.get_json()['error'])
        self.assertNotIn('/srv/shakenfist', resp.get_data(as_text=True))
        eventlog.add_event_multi.assert_any_call(
            EVENT_TYPE_AUDIT, [('instance', UUID(self.instance_uuid))],
            'power on failed', duration=None,
            extra={'error': error},
            suppress_event_logging=False, log_as_error=False)

    def test_paused_instance_answers_409(self):
        resp = self._power_on(side_effect=exceptions.InvalidLifecycleState(
            'you cannot power on a paused instance; unpause it instead'))
        self.assertEqual(409, resp.status_code)
        self.assertIn('unpause it instead', resp.get_json()['error'])


class InstancePowerOffEndpointTestCase(InstancePowerEndpointTestCase):
    """A power off which left the instance running answers 500, not 200
    with the instance recorded as off. destroy()'s error goes in an
    audit event, not the response, because it can name host paths.
    """

    def _power_off(self, **power_off_kwargs):
        with mock.patch.object(Instance, 'power_off', **power_off_kwargs):
            return self.client.post(
                '/instances/%s/poweroff' % self.instance_uuid,
                headers=self.auth, data=json.dumps({}))

    def test_power_off_success_answers_200_with_null_body(self):
        resp = self._power_off(return_value=None)
        self.assertEqual(200, resp.status_code, resp.get_json())
        self.assertIsNone(resp.get_json())

    def test_failed_power_off_answers_500_and_records_the_error(self):
        error = ('internal error: process /usr/bin/qemu-system-x86_64 '
                 'did not exit')
        resp = self._power_off(side_effect=exceptions.InstancePowerOffFailed(
            error))

        self.assertEqual(500, resp.status_code)
        self.assertEqual(
            'instance failed to power off, see the instance events for '
            'details', resp.get_json()['error'])
        self.assertNotIn('qemu-system', resp.get_data(as_text=True))
        eventlog.add_event_multi.assert_any_call(
            EVENT_TYPE_AUDIT, [('instance', UUID(self.instance_uuid))],
            'power off failed', duration=None,
            extra={'error': error},
            suppress_event_logging=False, log_as_error=False)


class InstancePauseUnpauseEndpointTestCase(InstancePowerEndpointTestCase):
    """Pausing or unpausing a powered off instance answers 409, not the
    generic 500 a leaked libvirtError would give.
    """

    def test_pause_of_powered_off_instance_answers_409(self):
        with mock.patch.object(
                Instance, 'pause',
                side_effect=exceptions.InvalidLifecycleState(
                    'you cannot pause a powered off instance')):
            resp = self.client.post(
                '/instances/%s/pause' % self.instance_uuid,
                headers=self.auth, data=json.dumps({}))

        self.assertEqual(409, resp.status_code)
        self.assertIn(
            'you cannot pause a powered off instance', resp.get_json()['error'])

    def test_unpause_of_powered_off_instance_answers_409(self):
        with mock.patch.object(
                Instance, 'unpause',
                side_effect=exceptions.InvalidLifecycleState(
                    'you cannot unpause a powered off instance')):
            resp = self.client.post(
                '/instances/%s/unpause' % self.instance_uuid,
                headers=self.auth, data=json.dumps({}))

        self.assertEqual(409, resp.status_code)
        self.assertIn(
            'you cannot unpause a powered off instance', resp.get_json()['error'])
