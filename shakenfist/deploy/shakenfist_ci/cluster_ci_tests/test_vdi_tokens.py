import json
import time

import jwt
from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class TestVDIConsoleTokens(base.BaseNamespacedTestCase):
    """Mint a Kerbside VDI console token and verify it offline.

    Exercises the mint path end to end without a kerbside in the loop: a
    namespaced client mints a token via /instances/<uuid>/vdiconsoleproxy
    and this test verifies the returned JWT's signature and claims against
    the cluster's published signing key (/admin/vditokenpubkey), exactly as
    kerbside would offline. A second namespace must not be able to mint for
    the first namespace's instance.

    Both endpoints are called with _request_url() directly rather than a
    client SDK method: the client methods for them live on an unmerged
    branch, so calling the REST API directly keeps this test green at
    develop HEAD regardless of the client PR's merge order.

    The feature is off unless the cluster has KERBSIDE_URL set and a signing
    key ensured (sf-ctl ensure-kerbside-signing-key). When it is off the
    mint endpoint 404s (or 500s with no key); the test skips cleanly in that
    case rather than failing a cluster that legitimately runs the feature
    off.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'vditokens'
        super().__init__(*args, **kwargs)

    def _create_spice_instance(self, client, namespace):
        # A minimal diskless instance: no base image is downloaded so nothing
        # boots to an OS, but the VM still reaches the created state, which is
        # all the mint endpoint requires. video is left unset so the server
        # applies its default SPICE console.
        minimal_disk = [{'size': 1, 'type': 'disk'}]
        inst = client.create_instance(
            'vditoken-%s' % self._uniquifier(), 1, 128, None, minimal_disk,
            None, None, namespace=namespace)
        self.addDetail(
            'instance',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self._await_instance_create(inst['uuid'])
        return inst['uuid']

    def test_capability_advertisement_matches_configuration(self):
        """The root page advertises vdi-console-proxy iff the endpoint works.

        check_capability() is the client's only feature-detection channel,
        and a client that sees the token takes the proxy path with no 404
        fallback -- so a cluster that advertises it while /vdiconsoleproxy
        404s breaks vdiconsole for every client (issue 4003). That is the
        Kerbside-less combination no other test covers: this test never
        skips, asserting absence on a feature-off cluster and presence on
        a Kerbside-enabled one.
        """
        advertised = self.test_client.check_capability('vdi-console-proxy')

        instance_uuid = self._create_spice_instance(
            self.test_client, self.namespace)
        try:
            self.test_client._request_url(
                'GET', '/instances/%s/vdiconsoleproxy' % instance_uuid)
            feature_on = True
        except apiclient.ResourceNotFoundException:
            feature_on = False
        except apiclient.InternalServerError:
            # KERBSIDE_URL is set but no signing key is provisioned yet.
            # The feature is on (if misconfigured), so advertising it is
            # correct.
            feature_on = True

        self.assertEqual(
            feature_on, advertised,
            'the vdi-console-proxy capability advertisement does not match '
            'the vdiconsoleproxy endpoint behaviour')

    def test_mint_and_verify_console_token(self):
        instance_uuid = self._create_spice_instance(
            self.test_client, self.namespace)

        # Happy path: the owning namespace mints a token. A 404 means the
        # feature is not configured on this cluster (no KERBSIDE_URL), and a
        # 500 means no signing key is configured -- skip rather than fail, so
        # this test never breaks a cluster that runs the feature off.
        try:
            resp = self.test_client._request_url(
                'GET', '/instances/%s/vdiconsoleproxy' % instance_uuid).json()
        except apiclient.ResourceNotFoundException:
            self.skipTest(
                'Kerbside integration is not configured on this cluster '
                '(KERBSIDE_URL unset); skipping VDI console token test')
        except apiclient.InternalServerError:
            self.skipTest(
                'No Kerbside VDI token signing key configured on this cluster '
                '(run sf-ctl ensure-kerbside-signing-key); skipping')

        self.addDetail(
            'mint_response',
            content.text_content(json.dumps(resp, indent=4, sort_keys=True)))

        # The URL is <KERBSIDE_URL>/sf-console.vv?token=<jwt>. Split it back
        # into its base (which must equal the token audience) and the token,
        # so nothing about the deployment's KERBSIDE_URL is hard-coded here.
        marker = '/sf-console.vv?token='
        self.assertIn(marker, resp['url'])
        base_url, token = resp['url'].split(marker, 1)
        self.assertTrue(token, 'the exchange URL carries no token')
        self.assertGreater(
            resp['expires_at'], int(time.time()),
            'the token expiry is not in the future')

        # Fetch the cluster's published signing public keys and pick the one
        # named by the token's kid header.
        material = self.test_client._request_url(
            'GET', '/admin/vditokenpubkey').json()
        self.addDetail(
            'signing_material',
            content.text_content(
                json.dumps(material, indent=4, sort_keys=True)))

        header = jwt.get_unverified_header(token)
        self.assertEqual('EdDSA', header.get('alg'))
        kid = header.get('kid')
        public_pem = None
        for key in material['keys']:
            if key['kid'] == kid:
                public_pem = key['public_pem']
                break
        self.assertIsNotNone(
            public_pem, 'no published key matches the token kid %s' % kid)

        # The audience the token was minted for, read from the token itself.
        unverified = jwt.decode(token, options={'verify_signature': False})
        audience = unverified['aud']

        # The real verification: signature against the published key, plus the
        # audience check -- exactly what kerbside does offline.
        payload = jwt.decode(
            token, public_pem, algorithms=['EdDSA'], audience=audience)
        self.assertEqual(instance_uuid, payload['sub'])
        self.assertEqual(self.namespace, payload['sf:namespace'])
        self.assertTrue(payload.get('iss'), 'the token carries no issuer')
        self.assertTrue(payload.get('jti'), 'the token carries no jti')

        # The exchange URL's origin must be the audience the token pins, so a
        # token minted for this cluster cannot be replayed against a different
        # kerbside.
        self.assertEqual(
            base_url, audience,
            'the exchange URL base does not match the token audience')

        # Ownership: a second namespace must not be able to mint a token for
        # this namespace's instance. Only asserted once the happy path has
        # confirmed the feature is on, so this is never a vacuous pass on a
        # cluster where every mint 404s because the feature is off.
        other_ns = self.namespace + '-other'
        other_key = self._uniquifier()
        other_client = self._make_namespace(other_ns, other_key)
        try:
            self.assertRaises(
                apiclient.ResourceNotFoundException,
                other_client._request_url,
                'GET', '/instances/%s/vdiconsoleproxy' % instance_uuid)
        finally:
            self._remove_namespace(other_ns)
