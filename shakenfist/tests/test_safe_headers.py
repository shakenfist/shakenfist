# Copyright 2019 Michael Still and contributors
import http.server
import importlib.util
import io
import os

from shakenfist.tests import base


# The functional CI suite is a client of the cluster and is not
# importable from here, but safe_headers deliberately imports nothing
# beyond the standard library so its logic can be loaded from source
# and covered by the unit suite rather than only by whichever
# functional fixture happens to send a header.
MODULE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', 'deploy', 'shakenfist_ci',
    'safe_headers.py'))


def _load_safe_headers():
    spec = importlib.util.spec_from_file_location('safe_headers', MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RecordingBase:
    """Stands in for BaseHTTPRequestHandler at the end of the MRO."""

    def __init__(self):
        self.sent = []

    def send_header(self, keyword, value):
        self.sent.append((keyword, value))


class SafeHeaderMixinTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        module = _load_safe_headers()
        handler_class = type(
            'Handler', (module.SafeHeaderMixin, _RecordingBase), {})
        self.handler = handler_class()

    def test_cr_and_lf_are_stripped_from_values(self):
        self.handler.send_header(
            'Content-Type', 'text/plain\r\nX-Injected: gotcha')
        self.assertEqual(
            [('Content-Type', 'text/plainX-Injected: gotcha')],
            self.handler.sent)

    def test_bare_cr_and_bare_lf_are_also_stripped(self):
        # Response splitting does not need the full CRLF pair: some
        # parsers accept a lone LF as a line terminator, so both
        # characters have to go individually.
        self.handler.send_header('X-One', 'a\rb')
        self.handler.send_header('X-Two', 'a\nb')
        self.assertEqual(
            [('X-One', 'ab'), ('X-Two', 'ab')], self.handler.sent)

    def test_clean_values_pass_through_unchanged(self):
        self.handler.send_header('Content-Type', 'application/json')
        self.assertEqual(
            [('Content-Type', 'application/json')], self.handler.sent)

    def test_non_string_values_are_coerced_not_crashed(self):
        # The JWKS fixture sends str(len(body)), but a caller passing
        # the bare int must not turn sanitisation into an AttributeError.
        self.handler.send_header('Content-Length', 42)
        self.assertEqual([('Content-Length', '42')], self.handler.sent)

    def test_sanitised_header_reaches_a_real_handler_wire(self):
        """The mixin composes with the real handler base, not just a stub.

        Drives BaseHTTPRequestHandler's own send_header() underneath the
        mixin and asserts the bytes buffered for the wire contain exactly
        one line for the poisoned header.
        """
        module = _load_safe_headers()

        class Handler(module.SafeHeaderMixin,
                      http.server.BaseHTTPRequestHandler):
            def __init__(self):
                # Skip the socket-handling constructor; only the header
                # buffering matters here.
                self.request_version = 'HTTP/1.1'
                self.wfile = io.BytesIO()
                self._headers_buffer = []

        handler = Handler()
        handler.send_header('X-Poisoned', 'a\r\nX-Injected: gotcha')
        self.assertEqual(
            [b'X-Poisoned: aX-Injected: gotcha\r\n'],
            handler._headers_buffer)
