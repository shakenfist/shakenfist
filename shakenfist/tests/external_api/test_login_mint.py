"""Minting the credential set a person is entitled to.

POST /auth/login is the cluster's privilege boundary for human access:
an unauthenticated endpoint which hands out namespace keys in bulk,
one per mapping rule the caller's identity satisfies. Its failure mode
is silent -- a login which mints one key too many, or from a rule an
administrator deleted, looks exactly like a login which did the right
thing -- so these tests are overwhelmingly about what must *not* be
minted, and about the order in which the refusals happen.

Two things are deliberately asserted as pairs against one issuer
configuration rather than separately. The audience: a token stamped
with the issuer's audience column must be refused and one stamped with
its client identifier must be accepted, because asserting only one of
those passes against an endpoint which checks neither. And the system
namespace: a matching rule owned by system must mint, and a cluster
with no such rule must never produce a key there.
"""

import json
import logging
import sys
import time
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.mapping_rule import MappingRule
from shakenfist.namespace import Namespace
from shakenfist.namespace_key import NamespaceKey
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests import fake_jwks
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.trusted_issuer import TrustedIssuer
from shakenfist.util import credentials
from shakenfist import exceptions
from shakenfist import federation
from shakenfist.config import config


IDP = 'https://idp.example.com/application/o/sf/'
IDP_JWKS = IDP + 'jwks/'
IDP_AUTHORIZE = IDP + 'authorize/'

# The two audiences an operator configures, which are stamped by
# different parties and are never the same value in a correctly
# configured cluster. AUDIENCE is what this cluster tells a workload to
# ask its provider for; CLIENT_ID is this cluster's registration at the
# provider, which is what OpenID Connect puts in a person's token.
AUDIENCE = 'https://sf.example.com'
CLIENT_ID = 'shakenfist-cli'

# The keys of one entry of the response, exactly. Asserted as a set
# rather than by checking that the secret is present: a client caching
# the set needs the expiry and the scopes, and an entry which quietly
# grew a field would be an unreviewed disclosure of whatever the key
# object happens to carry next.
ENTRY_KEYS = {'namespace', 'key_name', 'key', 'expiry', 'scopes'}


class LoginMintTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False
        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()
        for namespace in ('system', 'ci', 'ops', 'dev'):
            self.mock_mariadb.create_namespace(namespace, 'key1', 'bar')

        self.key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048)
        self.keys = {'key-1': self.key}
        self.fetches = []
        self.jti_counter = 0

        # The rotation cooldown would suppress a forced JWKS refetch for
        # thirty seconds, which is longer than a test runs, so the fetch
        # counting below would hold whether or not the code did its job.
        cooldown = mock.patch.object(
            federation.config,
            'FEDERATION_JWKS_ROTATION_COOLDOWN_SECONDS', 0)
        cooldown.start()
        self.addCleanup(cooldown.stop)

        federation.JWKS_CACHE = federation.JWKSCache()
        self.issuer = TrustedIssuer.new(
            'corporate', IDP, IDP_JWKS, AUDIENCE,
            authorization_endpoint=IDP_AUTHORIZE, client_id=CLIENT_ID,
            oidc_scopes='openid profile groups')

        # Three namespaces this identity is entitled to, and one rule
        # beside them which it is not. The names are chosen so that
        # sorting by namespace then rule name is not the order they were
        # created in.
        self.rules = {
            'ops': MappingRule.new(
                'ops', 'staff', 'corporate', {'groups': 'sf-staff'},
                ['instance.*'], 3600, 'corporate'),
            'ci': MappingRule.new(
                'ci', 'staff', 'corporate', {'groups': 'sf-staff'},
                ['blob.read', 'artifact.*'], 1800, 'corporate'),
            'dev': MappingRule.new(
                'dev', 'staff', 'corporate', {'groups': 'sf-staff'},
                ['instance.read'], 900, 'corporate')
        }
        self.unmatched = MappingRule.new(
            'ci', 'admins', 'corporate', {'groups': 'sf-admins'},
            ['node.read'], 3600, 'corporate-admin')

        fake_jwks.patch_transport(self, self._respond)

        self.client = external_api.app.test_client()

    def _respond(self, url):
        if not url.startswith(IDP):
            return None

        self.fetches.append(url)
        return json.dumps(
            fake_jwks.jwks_document(self.keys)).encode('utf-8')

    def _token(self, claims=None, audience=CLIENT_ID, issuer=IDP,
               exp_delta=300, key=None, kid='key-1', jti=None):
        self.jti_counter += 1
        now = int(time.time())
        body = {
            'iss': issuer, 'aud': audience, 'iat': now,
            'exp': now + exp_delta,
            'sub': 'f9e8d7c6-1234-4321-9876-abcdefabcdef',
            'preferred_username': 'alice',
            'groups': 'sf-staff'
        }
        if jti is not False:
            body['jti'] = jti or 'jti-%d-%d' % (now, self.jti_counter)
        if claims:
            body.update(claims)
        return jwt.encode(body, key or self.key, algorithm='RS256',
                          headers={'kid': kid})

    def _login(self, token=None):
        body = {}
        if token is not False:
            body['token'] = token if token is not None else self._token()
        return self.client.post('/auth/login', data=json.dumps(body))

    def _key_counts(self):
        """How many namespace keys each namespace holds right now."""
        counts = {}
        for data in self.mock_mariadb.namespace_key_objects.values():
            counts[data.namespace] = counts.get(data.namespace, 0) + 1
        return counts

    def _rate_limit(self, limit):
        patcher = mock.patch.object(
            config, 'FEDERATION_RATE_LIMIT_PER_MINUTE', limit)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cap(self, cap):
        patcher = mock.patch.object(
            config, 'FEDERATION_MAX_LOGIN_CREDENTIALS', cap)
        patcher.start()
        self.addCleanup(patcher.stop)


class SuccessfulLoginTestCase(LoginMintTestCase):
    def test_one_token_mints_one_key_per_matching_rule(self):
        resp = self._login()

        self.assertEqual(200, resp.status_code, resp.get_data(as_text=True))
        body = resp.get_json()
        self.assertEqual(3, len(body))
        self.assertEqual(
            ['ci', 'dev', 'ops'], [entry['namespace'] for entry in body])

    def test_each_entry_carries_exactly_the_published_fields(self):
        for entry in self._login().get_json():
            self.assertEqual(ENTRY_KEYS, set(entry.keys()))

    def test_each_entry_describes_the_credential_it_carries(self):
        by_namespace = {entry['namespace']: entry
                        for entry in self._login().get_json()}

        self.assertEqual(['blob.read', 'artifact.*'],
                         by_namespace['ci']['scopes'])
        self.assertEqual(['instance.*'], by_namespace['ops']['scopes'])

        # 1800 from the ci rule, give or take the time the test took.
        self.assertGreater(by_namespace['ci']['expiry'], time.time() + 1700)
        self.assertLess(by_namespace['ci']['expiry'], time.time() + 1900)

    def test_every_minted_key_authenticates_in_its_own_namespace(self):
        for entry in self._login().get_json():
            self.assertTrue(credentials.looks_valid(entry['key']))
            resp = self.client.post('/auth', data=json.dumps(
                {'namespace': entry['namespace'], 'key': entry['key']}))
            self.assertEqual(200, resp.status_code, entry['namespace'])

    def test_a_key_carries_its_rules_scopes_and_expiry(self):
        by_namespace = {entry['namespace']: entry
                        for entry in self._login().get_json()}

        key = NamespaceKey.from_db_by_name(
            'ci', by_namespace['ci']['key_name'])
        self.assertEqual(['blob.read', 'artifact.*'], key.scopes)
        self.assertEqual(by_namespace['ci']['expiry'], key.expiry)

    def test_provenance_records_the_grant_as_it_was_made(self):
        entry = self._login().get_json()[0]
        key = NamespaceKey.from_db_by_name('ci', entry['key_name'])

        provenance = key.provenance
        # 'login' rather than 'federated', so an operator can tell a
        # person's credential from a workload's without reading on.
        self.assertEqual('login', provenance['source'])
        self.assertEqual(str(self.rules['ci'].uuid), provenance['rule'])
        self.assertEqual('staff', provenance['rule_name'])
        self.assertEqual('corporate', provenance['issuer'])
        self.assertEqual({'groups': 'sf-staff'}, provenance['claims'])
        self.assertEqual(
            'f9e8d7c6-1234-4321-9876-abcdefabcdef', provenance['sub'])

    def test_the_key_name_does_not_identify_the_person(self):
        # The name is visible to everyone who can list the namespace's
        # keys. Who logged in belongs in provenance, which takes an
        # administrator to read.
        for entry in self._login().get_json():
            self.assertTrue(entry['key_name'].startswith('corporate-'))
            self.assertNotIn('alice', entry['key_name'])
            self.assertNotIn('f9e8d7c6', entry['key_name'])

    def test_a_second_login_is_additive_rather_than_a_rotation(self):
        # NamespaceKey.new() rotates an existing key of the same name,
        # so a deterministic name would mean logging in on a laptop
        # invalidated the credential a workstation was still using.
        first = self._login().get_json()
        second = self._login().get_json()

        self.assertNotEqual(
            {entry['key_name'] for entry in first},
            {entry['key_name'] for entry in second})

        for entry in first + second:
            resp = self.client.post('/auth', data=json.dumps(
                {'namespace': entry['namespace'], 'key': entry['key']}))
            self.assertEqual(200, resp.status_code)

    def test_a_rule_the_identity_does_not_satisfy_mints_nothing(self):
        # The admins rule sits in ci beside a rule this identity does
        # satisfy, so this also pins that one non-match does not take
        # the matching rule beside it down with it.
        namespaces = [entry['namespace'] for entry in self._login().get_json()]
        self.assertEqual(1, namespaces.count('ci'))

        for key in self.mock_mariadb.namespace_key_objects.values():
            self.assertFalse(key.name.startswith('corporate-admin'))

    def test_a_successful_login_is_audited_against_the_key(self):
        with mock.patch.object(NamespaceKey, 'add_event') as add_event:
            self._login()

        messages = [c.args[1] for c in add_event.call_args_list
                    if len(c.args) > 1 and 'human login' in c.args[1]]
        self.assertEqual(['key minted by human login'] * 3, messages)

    def test_the_route_is_redacted_from_request_and_response_logging(self):
        # The response body carries every minted secret, and app.py logs
        # response bodies to the audit event stream.
        with external_api.app.test_request_context('/auth/login'):
            self.assertTrue(external_api._handles_credentials())

    def test_the_identity_token_is_not_written_to_the_api_log(self):
        # log_request logs the parsed body as the decorated method's
        # kwargs, and that copy is what ships to the log aggregator. The
        # identity token is a bearer credential until it expires.
        token = self._token()

        with mock.patch.object(api_base, 'LOG') as log:
            self._login(token=token)

        logged = json.dumps(
            [[str(c.args), str(c.kwargs)]
             for c in log.with_fields.call_args_list])
        self.assertNotIn(token, logged)
        self.assertIn('/auth/login', logged)

    def test_no_minted_secret_appears_in_an_event(self):
        recorded = []

        def _capture(self, eventtype, message, duration=None, extra=None,
                     **kwargs):
            recorded.append((message, extra))

        with mock.patch.object(NamespaceKey, 'add_event', _capture):
            minted = self._login().get_json()

        for message, extra in recorded:
            for entry in minted:
                self.assertNotIn(entry['key'], json.dumps(extra or {}))
                self.assertNotIn(entry['key'], message)


class BoundedWorkTestCase(LoginMintTestCase):
    """One login, a fixed number of reads, whatever the cluster holds.

    A rule's policy lives in its attributes row and its deletion is a
    soft one, so the obvious implementation reads two rows per rule.
    That is work an anonymous caller multiplies by however many rules
    the cluster happens to have, and the measured version of the same
    mistake elsewhere was fifteen thousand round trips a pass.
    """

    def setUp(self):
        super().setUp()
        # More rules than the three which match, so a per-rule read
        # would be visible as a count greater than one.
        for i in range(5):
            MappingRule.new(
                'dev', 'other-%d' % i, 'corporate',
                {'groups': 'sf-other-%d' % i}, ['instance.read'], 900,
                'other')

    def test_the_rule_set_is_read_in_two_queries(self):
        with mock.patch(
                'shakenfist.mariadb.find_mapping_rules',
                side_effect=self.mock_mariadb._mariadb_find_mapping_rules
                ) as find, \
                mock.patch(
                    'shakenfist.mariadb.get_objects_by_state',
                    side_effect=self.mock_mariadb._mariadb_get_objects_by_state
                ) as by_state:
            resp = self._login()

        self.assertEqual(200, resp.status_code)
        self.assertEqual(1, find.call_count)

        rule_states = [c for c in by_state.call_args_list
                       if c.args and c.args[0] == ObjectType.MAPPING_RULE]
        self.assertEqual(1, len(rule_states))

    def test_no_policy_is_read_one_rule_at_a_time(self):
        # The joined accessor hands back each rule's policy, so the
        # point read which would otherwise be issued per rule must not
        # happen at all.
        with mock.patch(
                'shakenfist.mariadb.get_mapping_rule_attributes',
                side_effect=self.mock_mariadb._mariadb_get_mapping_rule_attributes  # noqa: E501
                ) as point_read:
            resp = self._login()

        self.assertEqual(200, resp.status_code)
        self.assertEqual([], point_read.call_args_list)


class AudienceTestCase(LoginMintTestCase):
    """Two doors, two audiences, one issuer configuration.

    An OpenID Connect identity token's aud is the relying party's
    client identifier; a workload's is whatever the operator told it to
    ask for. One column cannot hold both, so the login path verifies
    against client_id and the exchange against audience. Both halves
    are asserted here against the same issuer, because a test for
    either one alone passes against an endpoint which checks neither.
    """

    def test_a_token_stamped_with_the_client_id_is_accepted(self):
        resp = self._login(token=self._token(audience=CLIENT_ID))
        self.assertEqual(200, resp.status_code)

    def test_a_token_stamped_with_the_issuers_audience_is_refused(self):
        resp = self._login(token=self._token(audience=AUDIENCE))
        self.assertEqual(401, resp.status_code)
        self.assertEqual({}, self._key_counts_minted())

    def test_a_token_for_a_third_audience_is_refused(self):
        resp = self._login(token=self._token(audience='https://elsewhere'))
        self.assertEqual(401, resp.status_code)

    def test_the_exchange_still_refuses_a_human_token(self):
        # The other door, against this same issuer: a token stamped for
        # the client registration must not buy the one credential a
        # targeted exchange names either.
        resp = self.client.post('/auth/federated', data=json.dumps({
            'token': self._token(audience=CLIENT_ID), 'namespace': 'ci',
            'rule': 'staff'}))
        self.assertEqual(401, resp.status_code)

    def test_the_exchange_still_accepts_a_workload_token(self):
        resp = self.client.post('/auth/federated', data=json.dumps({
            'token': self._token(audience=AUDIENCE), 'namespace': 'ci',
            'rule': 'staff'}))
        self.assertEqual(200, resp.status_code)

    def _key_counts_minted(self):
        """Keys beyond the one each namespace was created with."""
        return {ns: count - 1 for ns, count in self._key_counts().items()
                if count > 1}


class LoginCapableIssuerTestCase(LoginMintTestCase):
    """Narrower than trusted, and the same predicate the listing uses.

    issuer_claiming_url() resolves any issuer which is not deleted,
    because that is the right answer for the workload exchange. Without
    a narrower test here, a token from an issuer GET /auth/login never
    advertised would be logged in through anyway.
    """

    def _configuration(self, **overrides):
        # A copy, because the mock database hands back the row it
        # stores: mutating it in place would outlive the patch and the
        # next read would see the damage.
        attrs = self.issuer.configuration().model_copy(deep=True)
        for field, value in overrides.items():
            setattr(attrs, field, value)
        return mock.patch.object(
            TrustedIssuer, 'configuration', return_value=attrs)

    def test_an_issuer_with_no_client_id_is_refused(self):
        # It still resolves -- the token's iss names it and it is not
        # deleted -- so this is the check doing the work rather than
        # the issuer having disappeared.
        with self._configuration(client_id=None):
            resp = self._login(token=self._token(audience=AUDIENCE))
        self.assertEqual(401, resp.status_code)

        self.assertIsNotNone(
            federation.issuer_for_token(self._token()))

    def test_an_issuer_with_an_empty_client_id_is_refused(self):
        # The column has no minimum length, so an empty string is
        # storable, and it must not fall back to the audience column.
        with self._configuration(client_id=''):
            resp = self._login(token=self._token(audience=AUDIENCE))
        self.assertEqual(401, resp.status_code)

    def test_an_issuer_with_no_flow_endpoint_is_refused(self):
        with self._configuration(authorization_endpoint=None,
                                 device_authorization_endpoint=None):
            resp = self._login()
        self.assertEqual(401, resp.status_code)

    def test_a_device_flow_only_issuer_is_accepted(self):
        with self._configuration(
                authorization_endpoint=None,
                device_authorization_endpoint=IDP + 'device/'):
            resp = self._login()
        self.assertEqual(200, resp.status_code)

    def test_an_issuer_with_no_state_row_is_refused(self):
        # issuer_claiming_url() accepts anything which is not deleted,
        # and a static row whose state write failed reads as a state of
        # None -- which is not deleted, so it resolves. An issuer in
        # that condition was never finished being configured and must
        # not be logged in through. There is no error state to test
        # instead: configuring an issuer is atomic, so its state
        # machine has only initial, created and deleted.
        self.mock_mariadb.mariadb_states.pop(
            f'{ObjectType.TRUSTED_ISSUER}/{self.issuer.uuid}')

        self.assertEqual(401, self._login().status_code)
        self.assertIsNotNone(federation.issuer_for_token(self._token()))

    def test_the_listing_and_the_mint_agree(self):
        # The point of sharing the predicate: an issuer which is not
        # offered must not be loggable in through, in both directions.
        with self._configuration(client_id=None):
            listed = self.client.get('/auth/login').get_json()
            refused = self._login(token=self._token(audience=AUDIENCE))

        self.assertEqual([], listed)
        self.assertEqual(401, refused.status_code)

        self.assertEqual(
            ['corporate'],
            [entry['name'] for entry in
             self.client.get('/auth/login').get_json()])
        self.assertEqual(200, self._login().status_code)


class RefusedLoginTestCase(LoginMintTestCase):
    def test_an_untrusted_issuer_is_refused_without_a_fetch(self):
        resp = self._login(token=self._token(issuer='https://evil.example'))
        self.assertEqual(401, resp.status_code)
        self.assertEqual([], self.fetches)

    def test_a_token_signed_by_the_wrong_key_is_refused(self):
        other = rsa.generate_private_key(
            public_exponent=65537, key_size=2048)
        self.assertEqual(401, self._login(
            token=self._token(key=other)).status_code)

    def test_an_expired_token_is_refused(self):
        self.assertEqual(401, self._login(
            token=self._token(exp_delta=-1)).status_code)

    def test_an_identity_which_matches_no_rule_is_refused(self):
        # Not a 200 carrying an empty list: a client cannot tell "you
        # are entitled to nothing" from "this cluster is half
        # configured", and would have to treat a successful login with
        # no credentials as a state of its own.
        before = self._key_counts()
        resp = self._login(token=self._token(claims={'groups': 'nobody'}))

        self.assertEqual(401, resp.status_code)
        self.assertEqual(before, self._key_counts())

    def test_a_refusal_says_nothing_about_the_rules(self):
        resp = self._login(token=self._token(claims={'groups': 'nobody'}))
        body = resp.get_data(as_text=True)

        self.assertNotIn('sf-staff', body)
        self.assertNotIn('groups', body)
        self.assertNotIn('staff', body)
        self.assertNotIn('corporate', body)

    def test_no_match_is_indistinguishable_from_a_bad_token(self):
        # Otherwise the endpoint is an oracle for whether a given
        # identity matches any rule on the cluster, which an anonymous
        # caller holding one valid token could walk.
        no_match = self._login(token=self._token(claims={'groups': 'nobody'}))
        bad_token = self._login(token=self._token(exp_delta=-1))

        self.assertEqual(no_match.status_code, bad_token.status_code)
        self.assertEqual(no_match.get_json(), bad_token.get_json())

    def test_no_globbing_in_claim_matching(self):
        resp = self._login(token=self._token(claims={'groups': 'sf-staff-x'}))
        self.assertEqual(401, resp.status_code)

    def test_a_missing_token_is_refused(self):
        self.assertEqual(400, self._login(token=False).status_code)

    def test_garbage_is_refused_rather_than_raising(self):
        for junk in ['', 'not-a-token', 'a.b.c', '.' * 100]:
            resp = self._login(token=junk)
            self.assertIn(resp.status_code, (400, 401),
                          f'{junk!r} produced {resp.status_code}')

    def test_a_cluster_with_no_issuers_refuses_and_mints_nothing(self):
        self.issuer.delete()
        before = self._key_counts()

        resp = self._login()
        self.assertEqual(401, resp.status_code)
        self.assertEqual(before, self._key_counts())

    def test_an_oversized_body_is_refused(self):
        resp = self.client.post('/auth/login', data=json.dumps(
            {'token': 'a' * (config.FEDERATION_MAX_TOKEN_BYTES + 1)}))
        self.assertEqual(413, resp.status_code)

    def test_an_oversized_body_is_refused_before_it_is_parsed(self):
        # The endpoint's own size check runs after log_request has
        # already called get_json(force=True), so the refusal has to
        # come from the request hook. This route was not covered by it
        # until human login existed.
        body = json.dumps(
            {'token': 'a' * (config.FEDERATION_MAX_TOKEN_BYTES + 1)})

        with mock.patch('flask.Request.get_json') as get_json:
            resp = self.client.post('/auth/login', data=body)

        self.assertEqual(413, resp.status_code)
        get_json.assert_not_called()

    def test_a_body_with_no_declared_length_is_refused(self):
        resp = self.client.post(
            '/auth/login',
            data=json.dumps({'token': self._token()}),
            headers={'Transfer-Encoding': 'chunked'})
        self.assertEqual(411, resp.status_code)

    def test_the_metadata_read_still_takes_no_body(self):
        # The hook now covers both methods of this path. A GET with no
        # content-length is the normal case and must not become a 411.
        self.assertEqual(200, self.client.get('/auth/login').status_code)


class DeletedRuleTestCase(LoginMintTestCase):
    """A mapping rule's only delete is a soft one.

    The row survives until the hard delete reaper reaches it, so an
    accessor which does not filter object state still returns it. A
    login which mints from it is an authorisation bypass rather than a
    stale listing, which is why the state filter and the failure of the
    state query are tested separately.
    """

    def test_a_soft_deleted_rule_mints_nothing(self):
        self.assertEqual(3, len(self._login().get_json()))

        self.rules['ci'].delete()

        body = self._login().get_json()
        self.assertEqual(['dev', 'ops'],
                         [entry['namespace'] for entry in body])

    def test_deleting_every_rule_refuses_the_login(self):
        for rule in self.rules.values():
            rule.delete()

        before = self._key_counts()
        self.assertEqual(401, self._login().status_code)
        self.assertEqual(before, self._key_counts())

    def test_an_unreadable_state_query_refuses_rather_than_mints(self):
        # None is a failed query and [] is "nothing is actionable".
        # Reading the failure as an empty answer mints nothing, which
        # is safe; reading it as "do not filter" mints from rules an
        # administrator deleted, which is not. Neither guess is
        # allowed, so the login is refused outright.
        before = self._key_counts()

        with mock.patch('shakenfist.mariadb.get_objects_by_state',
                        return_value=None):
            resp = self._login()

        self.assertEqual(503, resp.status_code)
        self.assertEqual(before, self._key_counts())

    def test_the_state_query_asks_for_the_actionable_states(self):
        with mock.patch(
                'shakenfist.mariadb.get_objects_by_state',
                side_effect=self.mock_mariadb._mariadb_get_objects_by_state
                ) as by_state:
            self._login()

        rule_states = [c for c in by_state.call_args_list
                       if c.args and c.args[0] == ObjectType.MAPPING_RULE]
        self.assertEqual(1, len(rule_states))
        self.assertEqual(
            sorted(MappingRule.ACTIVE_STATES), rule_states[0].args[1])


class ReplayRefusalTestCase(LoginMintTestCase):
    """One token, one login, however many namespaces it reached.

    The replay table's grain is per (token, thing spent on), and a
    login spends one token on an issuer rather than on each rule it
    matched. Claiming the rules one at a time would make a retry after
    a partial failure answer with whichever credentials the first
    attempt had not taken, and nothing in the answer would say the set
    was short.
    """

    def test_the_same_token_twice_is_refused(self):
        token = self._token()

        self.assertEqual(200, self._login(token=token).status_code)
        self.assertEqual(401, self._login(token=token).status_code)

    def test_a_replayed_token_mints_nothing_anywhere(self):
        token = self._token()
        self._login(token=token)

        before = self._key_counts()
        self._login(token=token)
        self.assertEqual(before, self._key_counts())

    def test_a_fresh_token_after_a_replay_still_works(self):
        token = self._token()
        self._login(token=token)
        self.assertEqual(401, self._login(token=token).status_code)

        self.assertEqual(200, self._login().status_code)

    def test_the_claim_is_keyed_on_the_issuer_not_on_a_rule(self):
        # One row for a login which reached three namespaces, and it is
        # the issuer's uuid in it. Per-rule claims would leave three.
        self._login()

        [(_, spent_on)] = list(self.mock_mariadb.federation_replay)
        self.assertEqual(str(self.issuer.uuid), spent_on)

    def test_a_token_with_no_jti_is_still_protected(self):
        token = self._token(jti=False)

        self.assertEqual(200, self._login(token=token).status_code)
        self.assertEqual(401, self._login(token=token).status_code)

    def test_the_claim_is_taken_before_anything_is_minted(self):
        # The claim is the last gate before minting, so a token which
        # the table refuses must cost nothing. If the claim moved after
        # the first mint, the replay would arrive with one key already
        # made.
        token = self._token()
        self._login(token=token)

        before = self._key_counts()
        with mock.patch.object(NamespaceKey, 'new') as new:
            self.assertEqual(401, self._login(token=token).status_code)

        new.assert_not_called()
        self.assertEqual(before, self._key_counts())

    def test_an_unreadable_replay_table_refuses_rather_than_mints(self):
        before = self._key_counts()
        with mock.patch('shakenfist.mariadb.record_federated_exchange',
                        side_effect=exceptions.DatabaseUnavailable('down')):
            resp = self._login()

        self.assertEqual(503, resp.status_code)
        self.assertEqual(before, self._key_counts())

    def test_a_login_does_not_burn_a_workload_exchange(self):
        # The two claims are different pairs, so an identity used for
        # both is not refused at the second door. This is the grain
        # doing its job rather than an accident of ordering.
        token = self._token(audience=AUDIENCE)
        resp = self.client.post('/auth/federated', data=json.dumps({
            'token': token, 'namespace': 'ci', 'rule': 'staff'}))
        self.assertEqual(200, resp.status_code)

        self.assertEqual(200, self._login().status_code)


class CredentialCapTestCase(LoginMintTestCase):
    """The cap refuses the whole login rather than truncating the set.

    A truncated set is one the person cannot tell is incomplete; they
    would discover it as a permission denied in whichever namespace
    fell off the end. The refusal is 507 rather than 409 because
    nothing about the request can be changed to make it succeed -- what
    has to change is the cluster's rules or its cap.
    """

    def test_exceeding_the_cap_refuses_and_mints_nothing(self):
        self._cap(2)
        before = self._key_counts()

        resp = self._login()
        self.assertEqual(507, resp.status_code)
        self.assertEqual(before, self._key_counts())

    def test_a_login_at_the_cap_still_mints(self):
        # The cap is the most a login may mint, not one less than it.
        self._cap(3)
        self.assertEqual(3, len(self._login().get_json()))

    def test_one_more_rule_than_the_cap_refuses(self):
        self._cap(3)
        MappingRule.new(
            'ops', 'staff-two', 'corporate', {'groups': 'sf-staff'},
            ['instance.read'], 900, 'corporate')

        self.assertEqual(507, self._login().status_code)

    def test_the_refusal_names_neither_the_counts_nor_the_setting(self):
        self._cap(2)
        body = self._login().get_data(as_text=True)

        self.assertNotIn('FEDERATION_MAX_LOGIN_CREDENTIALS', body)
        self.assertNotIn('2', body)
        self.assertNotIn('3', body)

    def test_the_cap_refusal_does_not_spend_the_token(self):
        # Nothing was minted, so the token must still be good once the
        # operator has raised the cap or the person's entitlements have
        # shrunk.
        self._cap(2)
        token = self._token()
        self.assertEqual(507, self._login(token=token).status_code)

        with mock.patch.object(config, 'FEDERATION_MAX_LOGIN_CREDENTIALS', 8):
            self.assertEqual(200, self._login(token=token).status_code)


class SystemNamespaceTestCase(LoginMintTestCase):
    """A mapping rule in system is an administrative grant.

    The authorisation decision belongs entirely to the mapping rules,
    so there is no allow list of namespaces here and no special case
    keyed on a name: a rule an administrator wrote in system mints like
    any other. What this does instead is make it legible, with its own
    audit wording, and prove that there is no other path to a key
    there.
    """

    def test_a_cluster_with_no_system_rule_never_mints_into_system(self):
        before = self._key_counts()

        body = self._login().get_json()
        self.assertEqual(['ci', 'dev', 'ops'],
                         [entry['namespace'] for entry in body])
        self.assertEqual(before['system'], self._key_counts()['system'])

    def test_a_matching_rule_in_system_mints(self):
        MappingRule.new(
            'system', 'staff', 'corporate', {'groups': 'sf-staff'},
            ['node.read'], 600, 'corporate-admin')

        body = self._login().get_json()
        self.assertEqual(['ci', 'dev', 'ops', 'system'],
                         [entry['namespace'] for entry in body])

    def test_a_system_mint_is_audited_as_an_administrative_one(self):
        # An administrative credential has to be legible as one in the
        # audit log, which a shared wording would not make it.
        MappingRule.new(
            'system', 'staff', 'corporate', {'groups': 'sf-staff'},
            ['node.read'], 600, 'corporate-admin')

        with mock.patch.object(NamespaceKey, 'add_event') as key_event:
            self._login()

        messages = [c.args[1] for c in key_event.call_args_list
                    if len(c.args) > 1 and 'human login' in c.args[1]]
        self.assertEqual(
            ['key minted by human login'] * 3 +
            ['administrative key minted by human login'], messages)

    def test_a_system_rule_which_does_not_match_mints_nothing_there(self):
        MappingRule.new(
            'system', 'admins', 'corporate', {'groups': 'sf-admins'},
            ['node.read'], 600, 'corporate-admin')
        before = self._key_counts()

        self._login()
        self.assertEqual(before['system'], self._key_counts()['system'])


class RateLimitTestCase(LoginMintTestCase):
    def setUp(self):
        super().setUp()

        # Pin the clock the rate limiter sees to the middle of a
        # window, so every attempt in a test lands in one window by
        # construction rather than by luck.
        self.frozen_time = 1700000010.0
        self.mock_time = mock.Mock(
            time=mock.Mock(return_value=self.frozen_time))
        patcher = mock.patch.object(federation, 'time', self.mock_time)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_limit_trips_and_then_recovers(self):
        self._rate_limit(3)

        for _ in range(3):
            self.assertEqual(200, self._login().status_code)
        self.assertEqual(429, self._login().status_code)

        self.mock_time.time.return_value = (
            self.frozen_time + federation.RATE_LIMIT_WINDOW_SECONDS)
        self.assertEqual(200, self._login().status_code)

    def test_the_limiter_is_consulted_before_the_issuer_is_resolved(self):
        # Resolving an issuer scans every configured issuer and reads
        # state and attributes per row, so an unmetered caller could
        # drive that scan as fast as they could send.
        self._rate_limit(1)
        self._login()

        with mock.patch.object(federation, 'issuer_for_token') as resolve:
            resp = self._login()

        self.assertEqual(429, resp.status_code)
        resolve.assert_not_called()

    def test_the_budget_is_this_endpoints_own(self):
        # A login costs a JWKS fetch and a bcrypt hash per credential,
        # where the metadata read costs one scan. A shared counter would
        # mean a burst of one throttling the other.
        self._rate_limit(10)
        self._login()

        sources = [source for source, _ in
                   self.mock_mariadb.federation_rate_limits]
        self.assertEqual(['login-mint:127.0.0.1'], sources)

    def test_the_metadata_read_does_not_spend_the_mint_budget(self):
        self._rate_limit(10)
        self.client.get('/auth/login')
        self._login()

        self.assertEqual(
            {('login:127.0.0.1', 1699999980): 1,
             ('login-mint:127.0.0.1', 1699999980): 1},
            self.mock_mariadb.federation_rate_limits)

    def test_a_rate_limited_caller_is_told_nothing_useful(self):
        self._rate_limit(1)
        self._login()
        resp = self._login()

        body = resp.get_data(as_text=True)
        self.assertEqual(429, resp.status_code)
        self.assertNotIn('corporate', body)
        self.assertNotIn('staff', body)

    def test_an_unwritable_counter_refuses_rather_than_mints(self):
        self._rate_limit(10)
        before = self._key_counts()

        with mock.patch('shakenfist.mariadb.count_federated_attempt',
                        side_effect=exceptions.DatabaseUnavailable('down')):
            resp = self._login()

        self.assertEqual(503, resp.status_code)
        self.assertEqual(before, self._key_counts())

    def test_rate_limiting_precedes_the_jwks_fetch(self):
        # A limited caller must not still be able to make us dial out on
        # every attempt. The second token carries an unknown key id,
        # which is what makes this say anything: one reusing key-1 would
        # hit the cache and fetch nothing regardless.
        self._rate_limit(1)
        self._login()

        before = len(self.fetches)
        self.assertEqual(429, self._login(
            token=self._token(kid='key-2')).status_code)
        self.assertEqual(before, len(self.fetches))


class DamagedRuleTestCase(LoginMintTestCase):
    """One unusable rule must not cost a person their other credentials.

    Which is the opposite of the targeted exchange, where the damaged
    rule is the one the caller named and a refusal is the only honest
    answer. Here it is one of many, and refusing would let a single bad
    row deny every login on the cluster.
    """

    def test_a_rule_with_no_scopes_is_skipped_and_the_rest_still_mint(self):
        # Damaged in storage rather than through update(), which
        # refuses to write a rule that can grant nothing.
        self.mock_mariadb.mapping_rule_attributes[
            str(self.rules['ci'].uuid)].scopes = []

        body = self._login().get_json()
        self.assertEqual(['dev', 'ops'],
                         [entry['namespace'] for entry in body])

    def test_a_rule_with_no_attributes_row_does_not_appear(self):
        # The joined read drops it, because a rule nobody can read
        # grants nothing.
        self.mock_mariadb.mapping_rule_attributes.pop(
            str(self.rules['ci'].uuid))

        body = self._login().get_json()
        self.assertEqual(['dev', 'ops'],
                         [entry['namespace'] for entry in body])

    def test_a_rule_whose_namespace_is_gone_is_skipped(self):
        # Namespace hard delete is supposed to prevent this, so it is a
        # cannot-happen rather than a configuration an operator reaches.
        # Skipped for the same reason an unusable rule is: a broken rule
        # must not cost the person the credentials the cluster's other
        # rules would have granted.
        real_from_db = Namespace.from_db

        def _absent(name, *args, **kwargs):
            if name == 'ci':
                return None
            return real_from_db(name, *args, **kwargs)

        with mock.patch.object(Namespace, 'from_db', side_effect=_absent):
            body = self._login().get_json()

        self.assertEqual(['dev', 'ops'],
                         [entry['namespace'] for entry in body])
