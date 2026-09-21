# Copyright 2019 Michael Still and contributors

import re
from unittest import mock

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.tests import base


class BodySizeLimitTestCase(base.ShakenFistTestCase):
    """The general request body cap of issue 4249.

    The validation layer bounds everything it emits and bounded nothing
    it consumed: a 2MB body of malformed nested elements cost about two
    seconds inside validation.check() per gunicorn worker. The fix is
    the limit_request_body_size hook, in the same shape as
    limit_federated_body_size: refuse an oversized or unmeasured body
    before any reader, on every route rather than one.
    """

    def setUp(self):
        super().setUp()

        external_api.app.testing = True

        # The resolve_node_uuid hook hits the database if NODE_UUID is
        # not already set. Nothing here needs a database, so pin it.
        self.saved_node_uuid = config.NODE_UUID
        config.NODE_UUID = 'test-node-uuid'
        self.addCleanup(self._restore_node_uuid)

        self.client = external_api.app.test_client()

    def _restore_node_uuid(self):
        config.NODE_UUID = self.saved_node_uuid

    def test_an_oversized_body_is_refused(self):
        resp = self.client.post(
            '/instances',
            data='x' * (config.API_MAX_REQUEST_BODY_BYTES + 1))
        self.assertEqual(413, resp.status_code)

    def test_an_oversized_body_is_refused_before_it_is_parsed(self):
        # The refusal must land ahead of every reader: log_request calls
        # get_json(force=True) before authentication, and the validation
        # pass walks whatever it parsed. A cap that runs after either
        # has already paid the cost it exists to bound.
        with mock.patch('flask.Request.get_json') as get_json:
            resp = self.client.post(
                '/instances',
                data='x' * (config.API_MAX_REQUEST_BODY_BYTES + 1))

        self.assertEqual(413, resp.status_code)
        get_json.assert_not_called()

    def test_a_body_at_the_limit_is_not_refused(self):
        # The cap is exclusive: a body of exactly the limit passes the
        # hook and the unauthenticated request is answered by the JWT
        # layer instead. This is the control which proves the 413 above
        # comes from the size check rather than from anything later.
        resp = self.client.post(
            '/instances', data='x' * config.API_MAX_REQUEST_BODY_BYTES)
        self.assertEqual(401, resp.status_code)

    def test_a_chunked_body_is_refused(self):
        # content_length is None for chunked transfer encoding. Treating
        # unknown as small enough would let anyone opt out of the limit
        # by choosing a header, so a request that claims a body it does
        # not measure is refused instead.
        resp = self.client.post(
            '/instances',
            data='x' * (config.API_MAX_REQUEST_BODY_BYTES + 1),
            headers={'Transfer-Encoding': 'chunked'})
        self.assertEqual(411, resp.status_code)

    def test_a_bodyless_request_is_not_refused(self):
        # A request with no body carries neither a Content-Length nor a
        # Transfer-Encoding header. Unlike the federated hook, which
        # serves one POST-only route, the general cap must let these
        # through -- refusing them would break every client that sends
        # a bodyless request.
        resp = self.client.get('/instances')
        self.assertEqual(401, resp.status_code)

    def test_the_upload_data_route_is_exempt(self):
        # An upload arrives as a sequence of large binary chunks
        # streamed to disk, never parsed, so the route declaring the
        # raw request body is exempt from the cap: the oversized body
        # reaches the JWT layer instead of being refused on size.
        resp = self.client.post(
            '/upload/9433c936-4a58-4e2b-8ecd-1de9dbea1c6c',
            data='x' * (config.API_MAX_REQUEST_BODY_BYTES + 1))
        self.assertEqual(401, resp.status_code)

    def test_an_unmatched_route_is_still_capped(self):
        # flask.request.url_rule is None when no route matched. The
        # exemption must not treat that as exempt.
        resp = self.client.post(
            '/no/such/route',
            data='x' * (config.API_MAX_REQUEST_BODY_BYTES + 1))
        self.assertEqual(413, resp.status_code)

    def test_the_exemptions_match_the_published_declarations(self):
        # RAW_BODY_URL_RULES is maintained by hand, and the published
        # specification knows which operations declare the raw request
        # body (swagger_helper renders api_base.RAW_BODY_PARAMETER with
        # type 'binary' as a body schema of format 'Binary data'). The
        # two must agree exactly: a raw-body endpoint missing from the
        # set has its uploads refused on size, and a stale entry exempts
        # a JSON route from the cap.
        resp = self.client.get('/apispec_1.json')
        self.assertEqual(200, resp.status_code)
        spec = resp.get_json()

        spec_raw_paths = set()
        for path, methods in spec['paths'].items():
            for operation in methods.values():
                if not isinstance(operation, dict):
                    continue
                for param in operation.get('parameters', []):
                    if param.get('in') != 'body':
                        continue
                    if param.get('schema', {}).get('format') == 'Binary data':
                        spec_raw_paths.add(path)

        # A flask rule like /upload/<upload_uuid> publishes as
        # /upload/{upload_uuid}; converters like <int:offset> drop
        # their converter name.
        exempt_as_spec_paths = {
            re.sub(r'<(?:[^<>:]+:)?([^<>]+)>', r'{\1}', rule)
            for rule in external_api.RAW_BODY_URL_RULES}

        self.assertEqual(spec_raw_paths, exempt_as_spec_paths)
