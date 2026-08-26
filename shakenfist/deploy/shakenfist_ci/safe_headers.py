"""Header sanitisation for HTTP servers the CI suite stands up.

This module must import nothing beyond the standard library: the unit
test suite loads it from source (shakenfist/tests/test_safe_headers.py),
and the server package's dependencies are not installed where the
functional suite runs.
"""


class SafeHeaderMixin:
    """Strip CR and LF from header values before they reach the wire.

    A header value containing a line break splits the HTTP response
    (CWE-113), letting whatever produced the value inject headers or
    body content of its own. The handlers in this suite only send
    static or length-derived values today, but the sanitisation is the
    audited property, not the call sites.

    Inherit this *before* the handler base class -- listed after it,
    the handler's own send_header() wins the MRO and nothing here ever
    runs.
    """

    def send_header(self, keyword, value):
        value = str(value).replace('\r', '').replace('\n', '')
        super().send_header(keyword, value)
