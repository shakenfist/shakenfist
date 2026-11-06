# Copyright 2026 Michael Still and contributors
"""A refused console token exchange must leave the evidence behind.

Kerbside answers a key set which does not hold the minting key, a wrong
key, and a clock it does not share with the minting host with one 401
"invalid token signature" (issue 4706, and #4598, where the three could
not be told apart after the fact). exchange_diagnostics() is what the
functional exchange test records before it fails, so these tests pin
what it says about each of those three, that it says something rather
than raising when a value is missing or malformed, and that it never
carries the token itself -- the token is a live credential and the logs
it lands in are public.

``vdi.py`` is loaded through ``test_ci_vdi_redact``, which imports it
from the tree behind stubs for the packages the unit environment does
not install.
"""

import json

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from shakenfist.tests import base
from shakenfist.tests import test_ci_vdi_redact as vdi_redact


vdi = vdi_redact.vdi

# The exchange URL's shape, as instance.py's vdiconsoleproxy builds it.
KERBSIDE_URL = 'https://kerbside.example.com:13002'
EXCHANGE_URL = KERBSIDE_URL + '/sf-console.vv?token=%s'

# Noon UTC, as an epoch and as an HTTP date, so a Date header and the
# claims can be set relative to one instant.
NOON = 1793980800
NOON_HTTP = 'Fri, 06 Nov 2026 16:00:00 GMT'

ACTIVE_KID = '3f2a9c1e'
PREVIOUS_KID = 'a1b2c3d4'


def signed_token(kid=ACTIVE_KID, iat=NOON, exp=None, claims=None):
    """A token shaped like mint_console_token()'s, signed with a fresh key.

    The signature is never checked here -- the diagnostics read the token
    unverified, which is the point of them -- but signing it for real
    keeps the header and the claim set honest.
    """
    private_pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode('utf-8')
    payload = {
        'iss': 'https://sf.example.com',
        'aud': KERBSIDE_URL,
        'sub': 'cafef00d-0000-0000-0000-00000000000a',
        'sf:namespace': 'vdiexchange',
        'iat': iat,
        'exp': exp if exp is not None else iat + 60,
        'jti': 'd3adb33fd3adb33fd3adb33fd3adb33f',
    }
    payload.update(claims or {})
    return jwt.encode(
        payload, private_pem, algorithm='EdDSA', headers={'kid': kid})


class FakeResponse:
    """Only the three members the diagnostics read of a response."""

    def __init__(self, status_code=401, headers=None,
                 text='invalid token signature'):
        self.status_code = status_code
        self.headers = headers if headers is not None else {'Date': NOON_HTTP}
        self.text = text


def published(kids=(ACTIVE_KID,), active_kid=ACTIVE_KID):
    """The /admin/vditokenpubkey document, public members only."""
    return {
        'active_kid': active_kid,
        'keys': [{'kid': kid, 'alg': 'EdDSA', 'public_pem': '-----BEGIN...',
                  'created': NOON - 3600} for kid in kids],
    }


class ExchangeTokenTestCase(base.ShakenFistTestCase):
    def test_token_is_read_from_the_query(self):
        token = signed_token()
        self.assertEqual(
            token, vdi.exchange_token(EXCHANGE_URL % token))

    def test_no_token_parameter(self):
        for url in (KERBSIDE_URL + '/sf-console.vv',
                    KERBSIDE_URL + '/sf-console.vv?token=',
                    KERBSIDE_URL + '/sf-console.vv?other=1'):
            self.assertIsNone(vdi.exchange_token(url))


class ExchangeDiagnosticsTestCase(base.ShakenFistTestCase):
    def diagnose(self, token=None, response=None, material=None, now=NOON):
        if token is None:
            token = signed_token()
        if material is None:
            material = published()
        return vdi.exchange_diagnostics(
            EXCHANGE_URL % token, response or FakeResponse(), material, now)

    def test_the_result_is_json_serialisable(self):
        # The functional test attaches it as JSON, so a value which does
        # not serialise would replace the test's failure with its own.
        d = self.diagnose()
        self.assertEqual(d, json.loads(json.dumps(d, sort_keys=True)))

    def test_the_token_itself_is_never_recorded(self):
        token = signed_token()
        rendered = json.dumps(self.diagnose(token=token))
        self.assertNotIn(token, rendered)
        # Nor its signature alone, which is the part that is secret
        # because it is what makes the token spendable.
        self.assertNotIn(token.rsplit('.', 1)[1], rendered)

    def test_the_unverified_header_and_claims_are_recorded(self):
        d = self.diagnose(token=signed_token(kid=PREVIOUS_KID))
        self.assertEqual('EdDSA', d['token_header']['alg'])
        self.assertEqual(PREVIOUS_KID, d['token_header']['kid'])
        self.assertEqual(KERBSIDE_URL, d['token_claims']['aud'])
        self.assertEqual(NOON, d['token_claims']['iat'])
        self.assertEqual(
            'cafef00d-0000-0000-0000-00000000000a', d['token_claims']['sub'])
        self.assertEqual(401, d['status'])
        self.assertEqual('kerbside.example.com', d['exchange_url_host'])

    def test_a_kid_the_cluster_does_not_publish(self):
        # A token signed with a key which is not in the published set at
        # all: a rotation which dropped it, or the wrong cluster.
        d = self.diagnose(token=signed_token(kid='deadbeef'))
        self.assertEqual([ACTIVE_KID], d['published_kids'])
        self.assertEqual(ACTIVE_KID, d['published_active_kid'])
        self.assertFalse(d['token_kid_published'])

    def test_a_published_but_not_active_kid(self):
        # Kerbside's cached set can hold a key Shaken Fist no longer
        # signs with, which verifies, and one it has never seen, which
        # does not. Both facts are on the record.
        d = self.diagnose(
            token=signed_token(kid=PREVIOUS_KID),
            material=published(kids=(ACTIVE_KID, PREVIOUS_KID)))
        self.assertEqual([ACTIVE_KID, PREVIOUS_KID], d['published_kids'])
        self.assertEqual(ACTIVE_KID, d['published_active_kid'])
        self.assertTrue(d['token_kid_published'])

    def test_unfetchable_published_keys_are_recorded_not_raised(self):
        d = self.diagnose(material='unreadable: APIException: 503')
        self.assertEqual('unreadable: APIException: 503', d['published_kids'])
        self.assertNotIn('token_kid_published', d)
        # The rest of the evidence survives the missing half.
        self.assertEqual(ACTIVE_KID, d['token_header']['kid'])

    def test_an_absent_key_document(self):
        # No document at all, rather than a reason for its absence.
        d = vdi.exchange_diagnostics(
            EXCHANGE_URL % signed_token(), FakeResponse(), None, NOON)
        self.assertEqual('not fetched', d['published_kids'])

    def test_kerbside_clock_against_the_minting_hosts(self):
        # Kerbside is 90 seconds ahead of the host which minted the
        # token, so a token minted at NOON is 90 seconds old there and
        # its 60 second expiry has already passed: expiry, not signature.
        response = FakeResponse(
            headers={'Date': 'Fri, 06 Nov 2026 16:01:30 GMT'})
        d = self.diagnose(response=response)
        self.assertEqual(NOON + 90, d['kerbside_clock'])
        self.assertEqual(90, d['kerbside_clock_minus_iat_seconds'])
        self.assertEqual(30, d['kerbside_clock_minus_exp_seconds'])
        self.assertEqual(90, d['kerbside_clock_minus_caller_seconds'])
        self.assertEqual(NOON, d['caller_clock'])

    def test_a_kerbside_clock_behind_the_minting_hosts(self):
        # The other direction is the one no single-host lane can produce:
        # Kerbside judges the token not yet valid, and says "invalid
        # token signature" about it.
        response = FakeResponse(
            headers={'Date': 'Fri, 06 Nov 2026 15:58:00 GMT'})
        d = self.diagnose(response=response)
        self.assertEqual(-120, d['kerbside_clock_minus_iat_seconds'])
        self.assertEqual(-180, d['kerbside_clock_minus_exp_seconds'])

    def test_a_date_header_in_another_timezone(self):
        response = FakeResponse(
            headers={'Date': 'Fri, 06 Nov 2026 17:00:00 +0100'})
        d = self.diagnose(response=response)
        self.assertEqual(0, d['kerbside_clock_minus_iat_seconds'])

    def test_a_missing_or_unreadable_date_header(self):
        for headers in ({}, {'Date': ''}, {'Date': 'tomorrow lunchtime'}):
            d = self.diagnose(response=FakeResponse(headers=headers))
            self.assertEqual('no readable Date header', d['kerbside_clock'])
            self.assertNotIn('kerbside_clock_minus_iat_seconds', d)
            self.assertEqual(headers.get('Date'), d['kerbside_date'])
            # The claims are still there to read by hand.
            self.assertEqual(NOON, d['token_claims']['iat'])

    def test_a_malformed_token_does_not_raise(self):
        d = self.diagnose(token='not.a.token')
        self.assertIn('unreadable', d['token_header'])
        self.assertIn('unreadable', d['token_claims'])
        # Without claims there is nothing to compare, but the clock and
        # the published kids are still recorded.
        self.assertEqual(NOON, d['kerbside_clock'])
        self.assertNotIn('kerbside_clock_minus_iat_seconds', d)
        self.assertEqual([ACTIVE_KID], d['published_kids'])

    def test_a_token_with_no_times(self):
        token = jwt.encode({'sub': 'x'}, 'secret', algorithm='HS256')
        d = vdi.exchange_diagnostics(
            EXCHANGE_URL % token, FakeResponse(), published(), NOON)
        self.assertEqual({'sub': 'x'}, d['token_claims'])
        self.assertNotIn('kerbside_clock_minus_iat_seconds', d)
        self.assertNotIn('kerbside_clock_minus_exp_seconds', d)

    def test_an_exchange_url_with_no_token(self):
        d = vdi.exchange_diagnostics(
            KERBSIDE_URL + '/sf-console.vv', FakeResponse(), published(),
            NOON)
        self.assertIn('no token parameter', d['token'])
        self.assertNotIn('token_header', d)
        self.assertFalse(d['token_kid_published'])
        self.assertEqual(NOON, d['kerbside_clock'])
