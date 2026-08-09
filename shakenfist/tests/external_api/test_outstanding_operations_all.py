# Copyright 2019 Michael Still and contributors
"""The ``all`` parameter on the outstanding-operations endpoints.

Issue 3629: the shipped client serialises every request -- including
GETs -- to a JSON body and never builds a query string, but these
endpoints bound their webargs schema to the query location only. webargs
finishes with ``kwargs.update(parsed_args)``, so the schema's
``load_default=False`` overwrote the body-supplied ``all=True`` that
``log_request`` had already merged into kwargs. The fix binds the schema
to ``json_or_query``, a location loader registered in
``external_api/base.py`` which accepts the parameter from either place
with the JSON body authoritative (decision D6 of
docs/plans/PLAN-api-input-validation.md).

These tests drive the real Flask app through its test client so the
whole decorator chain -- ``log_request``, webargs, the ref lookups --
runs as it does in production.
"""
import json
import logging
import sys
from unittest import mock

from shakenfist.artifact import Artifact
from shakenfist.config import SFConfig
from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class OutstandingOperationsAllTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        fake_config = SFConfig(
            NODE_NAME='seriously',
            NODE_EGRESS_IP='127.0.0.1',
            NETWORK_NODE_IP='127.0.0.1',
            NODE_EGRESS_NIC='eth0',
            NODE_MESH_NIC='eth1',
            NODE_IS_NETWORK_NODE=True,
        )
        self.config_patch = mock.patch(
            'shakenfist.external_api.base.config', fake_config)
        self.mock_config = self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.client = external_api.app.test_client()

        self.mock_mariadb.create_namespace('foo', 'key1', 'bar')

        self.instance = self.mock_mariadb.create_instance(
            'test-instance', namespace='foo')
        self.network = self.mock_mariadb.create_network(
            'test-network', namespace='foo')
        self.artifact = Artifact.new(
            Artifact.TYPE_OTHER, 'http://example.com/thing.tgz',
            name='thing', namespace='foo')
        self.artifact.state = Artifact.STATE_CREATED

        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': 'foo', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.token = 'Bearer %s' % resp.get_json()['access_token']

        # The endpoints only pass outstanding_only through to the object,
        # so capture that rather than constructing real cluster
        # operations.
        fake_op = mock.MagicMock()
        fake_op.external_view.return_value = {'uuid': 'fake-op'}
        self.get_ops = mock.patch(
            'shakenfist.baseobject.DatabaseBackedObjectWithOperations'
            '.get_cluster_operations',
            return_value=[fake_op])
        self.mock_get_ops = self.get_ops.start()
        self.addCleanup(self.get_ops.stop)

    def _paths(self):
        return [
            '/instances/%s/clusteroperations' % self.instance.uuid,
            '/artifacts/%s/clusteroperations' % self.artifact.uuid,
            '/networks/%s/clusteroperations' % self.network.uuid,
        ]

    def _get(self, path, body=None, query=''):
        kwargs = {'headers': {'Authorization': self.token}}
        if body is not None:
            kwargs['data'] = json.dumps(body)
            kwargs['content_type'] = 'application/json'
        return self.client.get(path + query, **kwargs)

    def _assert_outstanding_only(self, resp, expected):
        self.assertEqual(200, resp.status_code)
        self.assertEqual([{'uuid': 'fake-op'}], resp.get_json())
        self.mock_get_ops.assert_called_once_with(outstanding_only=expected)
        self.mock_get_ops.reset_mock()

    def test_all_in_body(self):
        # The only form the shipped client can produce, and the one
        # issue 3629 is about: body-supplied all=True was silently
        # dropped.
        for path in self._paths():
            self._assert_outstanding_only(
                self._get(path, body={'all': True}), False)

    def test_all_in_query(self):
        for path in self._paths():
            self._assert_outstanding_only(
                self._get(path, query='?all=true'), False)

    def test_all_defaults_false(self):
        for path in self._paths():
            self._assert_outstanding_only(self._get(path), True)

    def test_all_false_in_body(self):
        for path in self._paths():
            self._assert_outstanding_only(
                self._get(path, body={'all': False}), True)

    def test_body_is_authoritative_over_query(self):
        # Decision D6: when a key arrives in both places, the JSON body
        # wins.
        self._assert_outstanding_only(
            self._get(self._paths()[0], body={'all': True},
                      query='?all=false'),
            False)

    def test_unknown_query_keys_are_ignored(self):
        # webargs applies unknown=EXCLUDE to the query location but not
        # to a custom one, so the loader must drop keys the schema does
        # not name itself or a stray query key would fail validation.
        # (A stray *body* key still errors, but from log_request merging
        # it into the handler's kwargs -- unchanged by this fix.)
        self._assert_outstanding_only(
            self._get(self._paths()[0], body={'all': True},
                      query='?banana=1'),
            False)

    def test_invalid_all_is_rejected(self):
        # A value marshmallow cannot load as a boolean is a validation
        # failure whichever way it arrives, not a silently ignored or
        # truthy value. Only rejection is asserted, not the status:
        # today generic_wrapper wraps webargs' 422 abort into a 500,
        # which predates this fix (querystring validation failures on
        # main behave identically), and pinning that wart here would
        # break the test the day it is fixed.
        for kwargs in [{'query': '?all=banana'}, {'body': {'all': 'banana'}}]:
            resp = self._get(self._paths()[0], **kwargs)
            self.assertNotEqual(200, resp.status_code)
            self.mock_get_ops.assert_not_called()
