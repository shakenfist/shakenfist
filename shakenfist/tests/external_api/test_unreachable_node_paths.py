# Copyright 2026 Michael Still and contributors
"""The paths an unreachable node still escaped through as a 500.

Issue 3743 taught `proxy_request_to_node` to answer 503 when a peer
node cannot be reached, but it guarded one call site. Issue 4161 found
two more places proxying to a peer without it, and a third mechanism
which is not a peer at all -- the node's own privileged nodelock
daemon, on a local Unix socket.

All three used to reach `suppress_exceptions_to_client` and answer an
unqualified 500. Since the generic 500 body became a bare `server
error` with no exception detail, the status code is the only signal
left which tells a caller "what you asked for is fine, something here
is temporarily down" apart from "the handler is broken".
"""

import json
import logging
from unittest import mock

import flask
import requests

from shakenfist import exceptions
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.external_api import blob as api_blob
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.util import concurrency as util_concurrency


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capture(testcase, logger_name):
    capture = _CaptureHandler()
    logging.getLogger(logger_name).addHandler(capture)
    testcase.addCleanup(
        logging.getLogger(logger_name).removeHandler, capture)
    return capture


class BlobDataPeerUnreachableTestCase(base.ShakenFistTestCase):
    """GET /blobs/<uuid>/data proxied to the node holding the blob.

    The hard one of the two proxy sites. The read ran inside
    `flask.stream_with_context` from a generator body, which does not
    execute until its first iteration -- by which time the 200 has
    already gone to the client. A peer which was simply down therefore
    produced a truncated 200 rather than any error at all.
    """

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

        self.capture = _capture(self, 'shakenfist.external_api.blob')

    def _blob_held_on(self, node):
        b = mock.MagicMock()
        b.locations = [node]
        return b

    def _proxied(self, side_effect):
        """Drive GET /blobs/<uuid>/data down the proxy path.

        filepath() points at a path which does not exist, so the local
        fast path is skipped and the handler looks for a node holding
        the blob -- which is the path under test.
        """
        with mock.patch('shakenfist.external_api.blob.Blob.from_db',
                        return_value=self._blob_held_on('sf-5')):
            with mock.patch(
                    'shakenfist.external_api.blob.Blob.filepath',
                    return_value='/nonexistent/blob'):
                with mock.patch(
                        'shakenfist.external_api.blob.get_api_token',
                        return_value='Bearer token'):
                    with mock.patch(
                            'shakenfist.external_api.blob.requests.request',
                            side_effect=side_effect):
                        return self.client.get(
                            '/blobs/%s/data' % self.BLOB_UUID,
                            headers=self.auth)

    def test_an_unreachable_holder_is_a_503_naming_it(self):
        resp = self._proxied(requests.exceptions.ConnectionError(
            '[Errno 111] Connection refused'))

        self.assertEqual(503, resp.status_code, resp.get_data())
        self.assertIn('sf-5', resp.get_json()['error'])
        self.assertIn('retry', resp.get_json()['error'])

    def test_a_holder_which_stalls_is_a_503_naming_it(self):
        # A hung node accepts the connection and never answers. Only a
        # bounded read makes that an exception at all; this pins what
        # the handler does with it.
        resp = self._proxied(requests.exceptions.ReadTimeout('stalled'))

        self.assertEqual(503, resp.status_code, resp.get_data())
        self.assertIn('sf-5', resp.get_json()['error'])

    def test_the_proxied_read_is_bounded(self):
        # ...and this pins that the read is bounded, which the test
        # above cannot: a mocked request raises whenever it is told to.
        with mock.patch('shakenfist.external_api.blob.get_api_token',
                        return_value='Bearer token'):
            with mock.patch(
                    'shakenfist.external_api.blob.request_namespace',
                    return_value='system'):
                with mock.patch('shakenfist.external_api.blob.flask'):
                    with mock.patch(
                            'shakenfist.external_api.blob.requests.request'
                    ) as mock_request:
                        api_blob._open_remote('sf-5', self.BLOB_UUID)
        self.assertEqual(
            api_base.PEER_REQUEST_TIMEOUT,
            mock_request.call_args.kwargs['timeout'])

    def test_the_connection_opens_before_the_status_is_committed(self):
        # The regression guard. If the request moves back inside the
        # generator, the response is a 200 whose body is empty and the
        # failure is invisible to the caller.
        opened = []

        def _record(*args, **kwargs):
            opened.append(args)
            raise requests.exceptions.ConnectionError('refused')

        resp = self._proxied(_record)

        self.assertEqual(1, len(opened))
        self.assertNotEqual(200, resp.status_code)

    def test_a_failure_mid_transfer_is_logged_not_raised(self):
        # Once the status line has gone there is no status code left to
        # send, so the most this can do is record the truncation rather
        # than let the exception escape into the WSGI server unlogged.
        response = mock.MagicMock()
        response.iter_content.side_effect = (
            requests.exceptions.ChunkedEncodingError('Connection broken'))

        chunks = list(
            api_blob._stream_remote(response, 'sf-5', self.BLOB_UUID))

        self.assertEqual([], chunks)
        records = [r for r in self.capture.records
                   if 'truncated body' in r.getMessage()]
        self.assertEqual(1, len(records))
        self.assertEqual(logging.WARNING, records[0].levelno)
        self.assertEqual('sf-5', records[0].extra_fields['host'])

    def test_a_finished_transfer_releases_the_peer_connection(self):
        response = mock.MagicMock()
        response.iter_content.return_value = iter([b'blob', b'data'])

        list(api_blob._stream_remote(response, 'sf-5', self.BLOB_UUID))

        response.close.assert_called_once_with()

    def test_a_caller_which_disconnects_releases_the_peer_connection(self):
        # The WSGI server closes the generator when the client goes.
        # Without a close the peer socket waits for garbage collection,
        # or for the whole read timeout if the peer has stalled.
        response = mock.MagicMock()
        response.iter_content.return_value = iter([b'blob', b'data'])

        stream = api_blob._stream_remote(response, 'sf-5', self.BLOB_UUID)
        next(stream)
        response.close.assert_not_called()
        stream.close()

        response.close.assert_called_once_with()

    def test_a_body_which_is_never_started_releases_the_peer_connection(
            self):
        # werkzeug sends no body for a HEAD request, so the generator
        # never starts and its finally never runs. Without a close on
        # the response the peer socket waits for garbage collection.
        remote = mock.MagicMock()
        remote.status_code = 200
        remote.iter_content.return_value = iter([b'blob', b'data'])

        with mock.patch('shakenfist.external_api.blob.Blob.from_db',
                        return_value=self._blob_held_on('sf-5')):
            with mock.patch(
                    'shakenfist.external_api.blob.Blob.filepath',
                    return_value='/nonexistent/blob'):
                with mock.patch(
                        'shakenfist.external_api.blob.get_api_token',
                        return_value='Bearer token'):
                    with mock.patch(
                            'shakenfist.external_api.blob.requests.request',
                            return_value=remote):
                        resp = self.client.head(
                            '/blobs/%s/data' % self.BLOB_UUID,
                            headers=self.auth)
                        self.assertEqual(200, resp.status_code)
                        self.assertEqual(b'', resp.get_data())
                        resp.close()

        remote.iter_content.assert_not_called()
        remote.close.assert_called_with()

    def test_a_reachable_holder_still_streams(self):
        response = mock.MagicMock()
        response.status_code = 200
        response.iter_content.return_value = iter([b'blob', b'data'])

        chunks = list(
            api_blob._stream_remote(response, 'sf-5', self.BLOB_UUID))

        self.assertEqual([b'blob', b'data'], chunks)
        self.assertEqual(
            [], [r for r in self.capture.records
                 if 'truncated body' in r.getMessage()])


class ArtifactUploadProxyTestCase(base.ShakenFistTestCase):
    """POST /artifacts proxied to the node an upload landed on.

    The easy one of the two: not inside a stream, so it can use the
    helper issue 3743 already built.
    """

    def setUp(self):
        super().setUp()
        self.app = flask.Flask(__name__)
        self.capture = _capture(self, 'shakenfist.external_api.base')

    def test_an_unreachable_upload_node_is_a_503_naming_it(self):
        with self.app.test_request_context('/artifacts', method='POST'):
            with mock.patch(
                    'shakenfist.external_api.base.requests.request',
                    side_effect=requests.exceptions.ConnectionError(
                        '[Errno 111] Connection refused')):
                resp = api_base.proxy_request_to_node(
                    'http://sf-5:13000/artifacts', 'Bearer token',
                    json.dumps({'upload_uuid': 'u'}), 'sf-5')

        self.assertEqual(503, resp.status_code)
        self.assertIn('sf-5', json.loads(resp.get_data(as_text=True))['error'])

    def test_a_peer_which_stalls_is_a_503_naming_it(self):
        with self.app.test_request_context('/artifacts', method='POST'):
            with mock.patch(
                    'shakenfist.external_api.base.requests.request',
                    side_effect=requests.exceptions.ReadTimeout(
                        'stalled')) as mock_request:
                resp = api_base.proxy_request_to_node(
                    'http://sf-5:13000/artifacts', 'Bearer token',
                    json.dumps({'upload_uuid': 'u'}), 'sf-5')

        self.assertEqual(503, resp.status_code)
        self.assertIn('sf-5', json.loads(resp.get_data(as_text=True))['error'])
        # A hung peer only raises at all because the request is bounded.
        self.assertEqual(
            api_base.PEER_REQUEST_TIMEOUT,
            mock_request.call_args.kwargs['timeout'])

    def test_artifact_py_has_no_proxy_of_its_own(self):
        # The point of the fix is that this site stopped carrying its
        # own copy of the proxy. A second copy is how the guard came to
        # be missing here in the first place, so assert the module makes
        # no raw node-to-node request at all rather than re-testing the
        # helper's behaviour through it.
        #
        # Read as a syntax tree rather than as text, so a comment or a
        # docstring which mentions requests neither trips this nor
        # satisfies it.
        import ast
        import inspect

        from shakenfist.external_api import artifact as api_artifact

        tree = ast.parse(inspect.getsource(api_artifact))

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split('.')[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        self.assertNotIn('requests', imported)

        called = {
            ast.unparse(node.func) for node in ast.walk(tree)
            if isinstance(node, ast.Call)}
        self.assertIn('api_base.proxy_request_to_node', called)


class NodeLockUnavailableTestCase(base.ShakenFistTestCase):
    """The node's own nodelock daemon not answering.

    Not a peer node: a refusal on the local Unix socket means this
    node's privileged lock daemon was not listening. Reusing the peer
    wording would send diagnosis to the wrong machine, so this answers
    in its own words.
    """

    def setUp(self):
        super().setUp()
        self.app = flask.Flask(__name__)
        self.capture = _capture(self, 'shakenfist.external_api.base')

    def test_acquire_retries_then_raises_a_named_exception(self):
        lock = util_concurrency.NodeLock('a-lock', timeout=None)

        with mock.patch(
                'shakenfist.util.concurrency._node_lock_request',
                side_effect=ConnectionRefusedError(
                    '[Errno 111] Connection refused')) as mock_request:
            with mock.patch('shakenfist.util.concurrency.time.sleep'):
                self.assertRaises(
                    exceptions.NodeLockUnavailable, lock.__enter__)

        # Retried rather than failing on the first refusal: a healthy
        # daemon with a full listen backlog refuses connections too.
        self.assertEqual(
            util_concurrency.NODELOCK_ACQUIRE_ATTEMPTS,
            mock_request.call_count)

    def test_acquire_does_not_wait_forever_without_a_timeout(self):
        # The retry is bounded by a count, not by self.timeout, which
        # may be None. An unbounded wait on a daemon which is not there
        # would park the caller forever -- worse than the 500 this
        # replaces.
        lock = util_concurrency.NodeLock('a-lock', timeout=None)

        with mock.patch(
                'shakenfist.util.concurrency._node_lock_request',
                side_effect=exceptions.MissingNodeLockSocket()):
            with mock.patch('shakenfist.util.concurrency.time.sleep'):
                self.assertRaises(
                    exceptions.NodeLockUnavailable, lock.__enter__)

    def test_a_transient_refusal_does_not_fail_the_acquire(self):
        lock = util_concurrency.NodeLock('a-lock', timeout=None)

        with mock.patch(
                'shakenfist.util.concurrency._node_lock_request',
                side_effect=[
                    ConnectionRefusedError('refused'),
                    ConnectionRefusedError('refused'),
                    True]):
            with mock.patch('shakenfist.util.concurrency.time.sleep'):
                self.assertIs(lock, lock.__enter__())

    def test_refusals_spread_across_a_long_wait_do_not_accumulate(self):
        # The count is of *consecutive* failures. A lock which is
        # legitimately held is polled for as long as the caller is
        # willing to wait, and over minutes of that an occasional
        # refused connection is not evidence the daemon has gone --
        # without the reset, three of them spread across an otherwise
        # healthy wait would fail an acquire that should have
        # succeeded.
        lock = util_concurrency.NodeLock('a-lock', timeout=None)

        with mock.patch(
                'shakenfist.util.concurrency._node_lock_request',
                side_effect=[
                    ConnectionRefusedError('refused'),
                    False,   # the daemon answered: the lock is held
                    ConnectionRefusedError('refused'),
                    False,
                    ConnectionRefusedError('refused'),
                    False,
                    True]):
            with mock.patch('shakenfist.util.concurrency.time.sleep'):
                self.assertIs(lock, lock.__enter__())

    def test_the_api_answers_503_in_its_own_words(self):
        @api_base.handle_nodelock_unavailable
        def handler():
            raise exceptions.NodeLockUnavailable(
                'the nodelock daemon on this node did not answer')

        with self.app.test_request_context(
                '/instances/abc/poweroff', method='POST'):
            resp = handler()

        self.assertEqual(503, resp.status_code)
        body = json.loads(resp.get_data(as_text=True))
        self.assertIn('lock service', body['error'])
        self.assertIn('retry', body['error'])
        # Not the peer-node sentence: no peer is involved, and naming
        # one would point diagnosis at the wrong machine.
        self.assertNotIn('peer', body['error'])

        records = [r for r in self.capture.records
                   if 'Nodelock daemon unavailable' in r.getMessage()]
        self.assertEqual(1, len(records))
        self.assertEqual(logging.WARNING, records[0].levelno)

    def test_the_handler_is_in_the_decorator_stack(self):
        # A handler nothing applies would pass every test above and
        # still leave every endpoint answering 500.
        self.assertIn(
            api_base.handle_nodelock_unavailable,
            api_base.Resource.method_decorators)
