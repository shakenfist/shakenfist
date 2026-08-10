# Copyright 2026 Michael Still and contributors
#
# GET /blobs/<uuid>/data publishes a minimum of 0 on both offset and
# limit, and the server backs that rather than waiting for phase 4 to
# compile the bound, because unbacked both failed worse than
# meaninglessly. A negative offset reached f.seek() inside
# stream_with_context, so the OSError arrived after the 200 had begun
# and the caller saw a truncated body rather than an error. A negative
# limit made `remaining` negative, so f.read(min(CHUNK_SIZE, -1)) read
# to EOF and quietly defeated the cap it was asked for.
#
# The bound is checked in the handler rather than declared as a
# marshmallow validate=Range(min=0) on the webargs schema, because
# webargs raises UnprocessableEntity and the app's error handler
# renders that as a 500 -- confirmed by trying it. That is the same
# serialisation hazard base.py's json_or_query loader documents, and a
# 500 for a caller's mistake would have been no better than the
# truncated 200 it replaced.

import json
from unittest import mock

from shakenfist.external_api import app as external_api
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class BlobDataBoundsTestCase(base.ShakenFistTestCase):
    BLOB_UUID = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'

    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')

        self.client = external_api.app.test_client()
        resp = self.client.post('/auth', data=json.dumps(
            {'namespace': 'system', 'key': 'bar'}))
        self.assertEqual(200, resp.status_code)
        self.auth = {
            'Authorization': 'Bearer %s' % resp.get_json()['access_token']}

    def _get(self, query):
        # A blob which resolves but has no file on this node, so a
        # request which gets past the bounds check falls through to the
        # proxy path and looks for a node holding it. That is enough:
        # the question here is only whether the check fires, and a
        # status which is not 400 answers it.
        with mock.patch('shakenfist.external_api.blob.Blob.from_db',
                        return_value=mock.MagicMock()):
            return self.client.get(
                '/blobs/%s/data?%s' % (self.BLOB_UUID, query),
                headers=self.auth)

    def test_a_negative_offset_is_refused(self):
        resp = self._get('offset=-1')
        self.assertEqual(400, resp.status_code, resp.get_data())
        self.assertIn('offset', resp.get_json()['error'])

    def test_a_negative_limit_is_refused(self):
        resp = self._get('limit=-1')
        self.assertEqual(400, resp.status_code, resp.get_data())
        self.assertIn('limit', resp.get_json()['error'])

    def test_the_documented_values_are_still_accepted(self):
        # The control. Without it both refusals above could be an
        # endpoint which had stopped accepting any offset at all.
        for query in ('offset=0', 'limit=0', 'offset=10&limit=20'):
            with self.subTest(query=query):
                self.assertNotEqual(400, self._get(query).status_code)
