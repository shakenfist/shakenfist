# Copyright 2019 Michael Still and contributors
import json
import logging
from unittest import mock

import flask
import requests

from shakenfist.external_api import base as api_base
from shakenfist.tests import base


class _CaptureHandler(logging.Handler):
    """Collect emitted log records for assertion."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class ProxyPeerUnreachableTestCase(base.ShakenFistTestCase):
    """An unreachable peer on the node-to-node proxy path is a 503, not a 500.

    Issue 3743: a refused or reset connection while proxying a request to
    the node hosting the object escaped as a raw ConnectionError, which
    the endpoint wrappers logged as an unqualified 'Server error' and
    returned to the client as a content-free 500. The failure is an
    infrastructure condition (the node-to-node variant of issues 3373 and
    3522), so it must surface as a 503 naming the peer, with the peer and
    proxied URL attached as structured log fields.
    """

    def setUp(self):
        super().setUp()

        self.app = flask.Flask(__name__)

        self.capture = _CaptureHandler()
        logging.getLogger('shakenfist.external_api.base').addHandler(
            self.capture)
        self.addCleanup(
            logging.getLogger('shakenfist.external_api.base').removeHandler,
            self.capture)

    def _unreachable_records(self):
        return [r for r in self.capture.records
                if r.getMessage() ==
                'Peer node API unreachable while proxying request']

    def test_connection_refused_returns_503_naming_peer(self):
        url = 'http://192.168.21.55:13000/instances/abc/consoledata'
        with self.app.test_request_context(
                '/instances/abc/consoledata', method='GET'):
            with mock.patch(
                    'shakenfist.external_api.base.requests.request',
                    side_effect=requests.exceptions.ConnectionError(
                        '[Errno 111] Connection refused')):
                resp = api_base.proxy_request_to_node(
                    url, 'Bearer token', None, 'sf-5')

        self.assertEqual(503, resp.status_code)
        body = json.loads(resp.get_data(as_text=True))
        self.assertIn('sf-5', body['error'])
        self.assertIn('retry', body['error'])

        # The peer and proxied URL must be structured log fields, not
        # only recoverable by parsing a traceback.
        records = self._unreachable_records()
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual(logging.ERROR, record.levelno)
        fields = record.extra_fields
        self.assertEqual('sf-5', fields['peer'])
        self.assertEqual(url, fields['url'])
        self.assertEqual('GET', fields['method'])
        self.assertIn('Connection refused', fields['error'])

    def test_reset_mid_response_returns_503(self):
        # ECONNRESET partway through the response also arrives as a
        # requests exception and must take the same qualified path.
        with self.app.test_request_context(
                '/instances/abc/consoledata', method='GET'):
            with mock.patch(
                    'shakenfist.external_api.base.requests.request',
                    side_effect=requests.exceptions.ChunkedEncodingError(
                        'Connection broken')):
                resp = api_base.proxy_request_to_node(
                    'http://192.168.21.55:13000/instances/abc/consoledata',
                    'Bearer token', None, 'sf-5')

        self.assertEqual(503, resp.status_code)
        self.assertEqual(1, len(self._unreachable_records()))

    def test_success_relays_peer_response(self):
        peer_response = mock.MagicMock()
        peer_response.status_code = 200
        peer_response.content = b'console output'
        peer_response.headers = {'Content-Type': 'text/plain'}

        with self.app.test_request_context(
                '/instances/abc/consoledata', method='GET'):
            with mock.patch(
                    'shakenfist.external_api.base.requests.request',
                    return_value=peer_response):
                resp = api_base.proxy_request_to_node(
                    'http://192.168.21.55:13000/instances/abc/consoledata',
                    'Bearer token', None, 'sf-5')

        self.assertEqual(200, resp.status_code)
        self.assertEqual(b'console output', resp.get_data())
        self.assertEqual('text/plain', resp.mimetype)
        self.assertEqual([], self._unreachable_records())

    def test_redirect_instance_request_peer_unreachable(self):
        # End to end through the decorator: the handler is never called
        # and the client sees a 503, not an escaped ConnectionError.
        handler = mock.MagicMock(name='handler')
        wrapped = api_base.redirect_instance_request(handler)

        instance = mock.MagicMock()
        instance.placement = {'node': 'peer-node-uuid'}

        peer_node = mock.MagicMock()
        peer_node.ip = '192.168.21.55'
        peer_node.fqdn = 'sf-5'

        with self.app.test_request_context(
                '/instances/abc/consoledata', method='GET'):
            with mock.patch.object(
                    api_base.config, 'NODE_UUID', 'local-node-uuid'):
                with mock.patch.object(
                        api_base.Node, 'from_db', return_value=peer_node):
                    with mock.patch(
                            'shakenfist.external_api.base.get_api_token',
                            return_value='Bearer token'):
                        with mock.patch(
                                'shakenfist.external_api.base.'
                                'request_namespace',
                                return_value='system'):
                            with mock.patch(
                                    'shakenfist.external_api.base.'
                                    'requests.request',
                                    side_effect=requests.exceptions.
                                    ConnectionError(
                                        '[Errno 111] Connection refused')):
                                resp = wrapped(instance_from_db=instance)

        self.assertEqual(503, resp.status_code)
        body = json.loads(resp.get_data(as_text=True))
        self.assertIn('sf-5', body['error'])
        handler.assert_not_called()
        self.assertEqual(1, len(self._unreachable_records()))
