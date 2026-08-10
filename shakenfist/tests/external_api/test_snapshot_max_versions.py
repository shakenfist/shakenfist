# Copyright 2026 Michael Still and contributors
#
# POST /instances/<ref>/snapshot is the third of three routes which
# write an artifact's max_versions, and the second of the two which
# never checked it. The value travels through Instance.snapshot() into
# Artifact.owned_from_url_or_new() and lands in the max_versions
# setter, where a negative persists and has every later add_index()
# delete the oldest surviving version.

import json
from unittest import mock
from uuid import uuid4

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.instance import Instance
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class SnapshotMaxVersionsTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        # Placed on this node, so redirect_instance_request() hands the
        # request to the handler rather than proxying it elsewhere --
        # or, with no placement at all, returning None and serving an
        # empty 200 which every assertion below would have to be
        # written not to believe.
        self.saved_node_uuid = config.NODE_UUID
        config.NODE_UUID = self.mock_mariadb.node_uuids['node1_net']
        self.addCleanup(self._restore_node_uuid)

        self.instance_uuid = str(uuid4())
        self.mock_mariadb.create_instance(
            'snapme', uuid=self.instance_uuid, namespace='foo',
            set_state=dbo.STATE_CREATED, place_on_node=config.NODE_UUID)

        self.client = external_api.app.test_client()
        resp = self.client.post('/auth', data=json.dumps(
            {'namespace': 'foo', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth = {
            'Authorization': 'Bearer %s' % resp.get_json()['access_token']}

    def _restore_node_uuid(self):
        config.NODE_UUID = self.saved_node_uuid

    def _post(self, max_versions):
        # Instance.snapshot() needs a running hypervisor, which this
        # harness does not have. Stubbing it is the assertion as much
        # as it is a convenience: whether the request reached the
        # snapshot at all, and with what, is the question here.
        with mock.patch.object(Instance, 'snapshot',
                               autospec=True) as snapshot:
            snapshot.return_value = {}
            resp = self.client.post(
                '/instances/%s/snapshot' % self.instance_uuid,
                headers=self.auth,
                data=json.dumps({'max_versions': max_versions}))
        return resp, snapshot

    def test_a_negative_max_versions_is_refused(self):
        resp, snapshot = self._post(-1)
        self.assertEqual(400, resp.status_code, resp.get_json())
        snapshot.assert_not_called()

    def test_an_unparsable_max_versions_is_a_400_not_a_500(self):
        for value in (['two'], {'two': 2}, 'two'):
            with self.subTest(value=value):
                resp, snapshot = self._post(value)
                self.assertEqual(400, resp.status_code, resp.get_json())
                snapshot.assert_not_called()

    def test_a_valid_max_versions_still_snapshots(self):
        # The control: without it the refusals above could be a route
        # which now refuses every snapshot.
        resp, snapshot = self._post(3)
        self.assertEqual(200, resp.status_code, resp.get_json())
        snapshot.assert_called_once()
        self.assertEqual(3, snapshot.call_args.kwargs['max_versions'])
