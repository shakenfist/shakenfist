# Copyright 2019 Michael Still and contributors

"""A fake JWKS endpoint for tests, patched in at the transport.

Three test modules need PyJWKClient to answer with locally generated
keys instead of reaching a real identity provider, and they used to
each patch jwt.jwks_client.urllib.request.urlopen. PyJWT 2.14 stopped
calling urlopen -- it builds an opener so it can refuse redirects --
and every one of those patches silently stopped intercepting. The
tests did not fail loudly on a missing mock; they made real HTTPS
requests to token.actions.githubusercontent.com, got GitHub's genuine
key set, and failed as though the exchange endpoint were refusing
valid tokens.

So the patch goes on urllib.request.OpenerDirector.open, which is what
both spellings end up calling, and it lives in one place because the
next time PyJWT rearranges its fetch there should be one edit rather
than three.

It is deliberately still a transport level fake. Patching
PyJWKClient.fetch_data would be simpler and would be wrong: fetch_data
is what writes PyJWKClient's key set cache, so replacing it disables
the caching that several of these tests exist to check, and they would
pass without it.
"""

import io
import json
import urllib.request
from unittest import mock

import jwt


def jwks_document(keys):
    """A JWKS document for a {kid: private key} mapping."""
    return {
        'keys': [
            json.loads(
                jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())
            ) | {'kid': kid, 'use': 'sig', 'alg': 'RS256'}
            for kid, key in keys.items()
        ]
    }


def patch_transport(test_case, respond):
    """Answer every urllib fetch for the duration of test_case.

    respond is called with the requested URL and returns the response
    body as bytes. It is also the test's chance to record the fetch, or
    to sleep, since the point of several of these tests is how many
    fetches happen and whether they overlap.
    """
    def _open(self, fullurl, data=None, timeout=None):
        body = respond(getattr(fullurl, 'full_url', fullurl))
        response = mock.MagicMock()
        response.read.return_value = body
        response.__enter__.return_value = io.BytesIO(body)
        response.__exit__.return_value = False
        return response

    patcher = mock.patch.object(urllib.request.OpenerDirector, 'open', _open)
    patcher.start()
    test_case.addCleanup(patcher.stop)
