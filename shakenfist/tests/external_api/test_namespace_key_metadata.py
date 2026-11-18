"""Reading a namespace key's metadata.

Two reads serve it: the keys listing with detail set, and GET on a
single key's route. What they must never do is the point of the tests:
publish who a human login minted a key for to anyone but the system
namespace, publish the hash or the nonce at all, or change what the bare
listing has always answered.

The disclosure rule is asserted as a pair against one key rather than
separately, because asserting only that an owner does not see the field
passes against an endpoint which never serves it to anybody, and
asserting only that system does passes against one which serves it to
everybody.
"""

import json
import logging
import sys
import time
from unittest import mock

from shakenfist import namespace_key
from shakenfist.external_api import app as external_api
from shakenfist.namespace import Namespace
from shakenfist.namespace_key import NamespaceKey
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


PROVENANCE = {
    'source': 'login',
    'sub': 'f9e8d7c6-1234-4321-9876-abcdefabcdef',
    'jti': 'jti-for-alice',
    'rule_name': 'staff',
    'issuer': 'corporate',
    'claims': {'email': 'alice@example.com'}
}

# The fields every entry of a detailed listing carries, and which a
# system caller sees one more than.
OWNER_FIELDS = {'namespace', 'key_name', 'uuid', 'expiry', 'scopes'}


class KeyMetadataTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        external_api.TESTING = True
        external_api.app.testing = True
        external_api.app.debug = False
        external_api.app.logger.addHandler(logging.StreamHandler(sys.stdout))
        external_api.app.logger.setLevel(logging.DEBUG)
        logging.root.setLevel(logging.DEBUG)

        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        for namespace in ('system', 'target', 'trustee', 'stranger'):
            self.mock_mariadb.create_namespace(namespace, 'login', 'secret-' + namespace)

        # The target grants full trust to trustee, which
        # requires_namespace_ownership admits to the target's keys.
        # Stranger is trusted by nobody.
        Namespace.from_db('target').add_trust('trustee')

        self.now = time.time()
        self.future = self.now + 86400
        self.past = self.now - 86400

        target = Namespace.from_db('target')
        # The 'login' key made by the fixture never expires. Two more
        # around it: one live with an expiry, one already lapsed.
        NamespaceKey.new(
            'target', 'person', 'person-secret', expiry=self.future,
            scopes=['instance.read'], provenance=PROVENANCE)
        NamespaceKey.new(
            'target', 'lapsed', 'lapsed-secret', expiry=self.past,
            scopes=['blob.read'], provenance=PROVENANCE)
        self.assertIsNotNone(target)

        # A key of the same name as one of the target's, elsewhere.
        NamespaceKey.new('stranger', 'elsewhere', 'elsewhere-secret')
        NamespaceKey.new('stranger', 'person', 'other-person-secret')

        self.client = external_api.app.test_client()
        self.tokens = {}

    def _token(self, namespace):
        if namespace not in self.tokens:
            resp = self.client.post('/auth', data=json.dumps(
                {'namespace': namespace, 'key': 'secret-' + namespace}))
            self.assertEqual(200, resp.status_code, resp.get_data(as_text=True))
            self.tokens[namespace] = 'Bearer %s' % resp.get_json()['access_token']
        return self.tokens[namespace]

    def _get(self, caller, path='/auth/namespaces/target/keys', **kwargs):
        return self.client.get(
            path, headers={'Authorization': self._token(caller)}, **kwargs)

    def _detail(self, caller):
        resp = self._get(caller, query_string={'detail': 'true'})
        self.assertEqual(200, resp.status_code, resp.get_data(as_text=True))
        return {entry['key_name']: entry for entry in resp.get_json()}

    def _secrets(self):
        """The hash and the nonce of every key the fixture holds."""
        values = []
        for attrs in self.mock_mariadb.namespace_key_attributes.values():
            values.append(attrs.key.get_secret_value())
            values.append(attrs.nonce.get_secret_value())
        return values


class BareListingTestCase(KeyMetadataTestCase):
    def test_the_bare_listing_is_unchanged_and_omits_the_expired_key(self):
        resp = self._get('target')

        self.assertEqual(200, resp.status_code)
        # The whole response: the exact names, in the order the
        # accessor yields them, with the lapsed key absent.
        self.assertEqual(['login', 'person'], resp.get_json())

    def test_detail_false_is_the_bare_listing(self):
        resp = self._get('target', query_string={'detail': 'false'})

        self.assertEqual(['login', 'person'], resp.get_json())


class DetailedListingTestCase(KeyMetadataTestCase):
    def test_the_detailed_listing_includes_the_expired_key(self):
        entries = self._detail('target')

        self.assertEqual({'login', 'person', 'lapsed'}, set(entries))

    def test_each_entry_carries_its_expiry_and_scopes(self):
        entries = self._detail('target')

        self.assertEqual(self.past, entries['lapsed']['expiry'])
        self.assertEqual(self.future, entries['person']['expiry'])
        self.assertIsNone(entries['login']['expiry'])
        self.assertEqual(['blob.read'], entries['lapsed']['scopes'])
        self.assertEqual(['instance.read'], entries['person']['scopes'])

    def test_the_name_is_key_name_and_never_name(self):
        for entry in self._detail('target').values():
            self.assertIn('key_name', entry)
            self.assertNotIn('name', entry)
            self.assertEqual('target', entry['namespace'])

    def test_an_owner_entry_has_exactly_the_published_fields(self):
        for entry in self._detail('target').values():
            self.assertEqual(OWNER_FIELDS, set(entry))

    def test_the_uuid_is_the_keys_own(self):
        entries = self._detail('target')

        stored = NamespaceKey.from_db_by_name('target', 'person')
        self.assertEqual(str(stored.uuid), entries['person']['uuid'])


class ProvenanceDisclosureTestCase(KeyMetadataTestCase):
    def test_system_sees_provenance_and_the_owner_does_not_on_one_key(self):
        as_system = self._detail('system')['person']
        as_owner = self._detail('target')['person']

        self.assertEqual(PROVENANCE, as_system['provenance'])
        self.assertNotIn('provenance', as_owner)
        # Same key, so the pair cannot be explained by different data.
        self.assertEqual(as_system['uuid'], as_owner['uuid'])

    def test_a_trusted_namespace_does_not_see_provenance(self):
        # The trustee is admitted by requires_namespace_ownership, so
        # this is a 200 with the field withheld rather than a refusal.
        resp = self._get('trustee', query_string={'detail': 'true'})

        self.assertEqual(200, resp.status_code, resp.get_data(as_text=True))
        entries = {e['key_name']: e for e in resp.get_json()}
        self.assertEqual({'login', 'person', 'lapsed'}, set(entries))
        for entry in entries.values():
            self.assertNotIn('provenance', entry)
        self.assertNotIn('alice@example.com', resp.get_data(as_text=True))

    def test_the_single_key_read_discloses_by_the_same_rule(self):
        path = '/auth/namespaces/target/keys/person'

        as_system = self._get('system', path)
        as_owner = self._get('target', path)
        as_trustee = self._get('trustee', path)

        self.assertEqual(200, as_system.status_code)
        self.assertEqual(PROVENANCE, as_system.get_json()['provenance'])
        self.assertEqual(200, as_owner.status_code)
        self.assertNotIn('provenance', as_owner.get_json())
        self.assertEqual(200, as_trustee.status_code)
        self.assertNotIn('provenance', as_trustee.get_json())

    def test_a_namespace_nobody_trusts_cannot_read_the_listing(self):
        resp = self._get('stranger', query_string={'detail': 'true'})

        self.assertEqual(404, resp.status_code)


class SingleKeyTestCase(KeyMetadataTestCase):
    def test_a_present_key_is_described(self):
        resp = self._get('target', '/auth/namespaces/target/keys/person')

        self.assertEqual(200, resp.status_code)
        body = resp.get_json()
        self.assertEqual(OWNER_FIELDS, set(body))
        self.assertEqual('person', body['key_name'])
        self.assertEqual(self.future, body['expiry'])

    def test_an_expired_key_can_still_be_read(self):
        resp = self._get('target', '/auth/namespaces/target/keys/lapsed')

        self.assertEqual(200, resp.status_code)
        self.assertEqual(self.past, resp.get_json()['expiry'])

    def test_an_absent_name_is_a_404(self):
        resp = self._get('target', '/auth/namespaces/target/keys/nonesuch')

        self.assertEqual(404, resp.status_code)
        self.assertEqual('key name not found in namespace',
                         resp.get_json()['error'])

    def test_a_name_which_exists_only_in_another_namespace_is_a_404(self):
        resp = self._get('target', '/auth/namespaces/target/keys/elsewhere')

        self.assertEqual(404, resp.status_code)
        self.assertEqual('key name not found in namespace',
                         resp.get_json()['error'])

    def test_a_name_in_both_namespaces_answers_with_the_requested_one(self):
        resp = self._get('system', '/auth/namespaces/target/keys/person')

        self.assertEqual('target', resp.get_json()['namespace'])
        self.assertEqual(self.future, resp.get_json()['expiry'])


class NoSecretMaterialTestCase(KeyMetadataTestCase):
    def _bodies(self):
        bodies = []
        for caller in ('system', 'target', 'trustee'):
            bodies.append(self._get(caller).get_data(as_text=True))
            bodies.append(self._get(
                caller, query_string={'detail': 'true'}).get_data(as_text=True))
            for name in ('login', 'person', 'lapsed'):
                bodies.append(self._get(
                    caller, '/auth/namespaces/target/keys/' + name
                ).get_data(as_text=True))
        return bodies

    def test_no_response_contains_a_hash_or_a_nonce(self):
        secrets = self._secrets()
        # Guard against a vacuous pass: there are secrets to look for,
        # and they are long enough that a substring match means
        # something.
        self.assertGreaterEqual(len(secrets), 8)
        for secret in secrets:
            self.assertGreater(len(secret), 8)

        bodies = self._bodies()
        self.assertEqual(3 * 5, len(bodies))
        for body in bodies:
            self.assertTrue(any(n in body for n in ('login', 'person', 'lapsed')))
            for secret in secrets:
                self.assertNotIn(secret, body)

    def test_no_response_contains_a_plaintext_secret(self):
        for body in self._bodies():
            for plaintext in ('person-secret', 'lapsed-secret',
                              'secret-target'):
                self.assertNotIn(plaintext, body)


class CostTestCase(KeyMetadataTestCase):
    """The reads cost one query however many keys there are.

    The request decorators read the *namespace's* state once to refuse
    a deleted namespace, so get_state is called -- but for the one
    namespace, never for a key. A per-key state read is the cost these
    tests exist to catch, and it would show as a namespace key lookup.
    """

    def setUp(self):
        super().setUp()
        for i in range(6):
            NamespaceKey.new('target', 'extra-%d' % i, 'extra-secret-%d' % i,
                             expiry=self.future)
        # Warm the tokens so authentication is not counted.
        self._token('target')
        self._token('system')

    def _count(self, caller, path, **kwargs):
        real_accessor = namespace_key.keys_with_attributes
        real_state = self.mock_mariadb._mariadb_get_state
        with mock.patch('shakenfist.namespace_key.keys_with_attributes',
                        side_effect=real_accessor) as accessor:
            with mock.patch('shakenfist.mariadb.get_state',
                            side_effect=real_state) as state:
                resp = self._get(caller, path, **kwargs)
            self.assertEqual(200, resp.status_code,
                             resp.get_data(as_text=True))
        return resp, accessor, state

    def _assert_no_key_state_read(self, state):
        key_reads = [c for c in state.call_args_list
                     if c.args[0] == ObjectType.NAMESPACE_KEY]
        self.assertEqual([], key_reads)
        # Whatever was read, it was not read once per key.
        self.assertLess(state.call_count, 4)

    def test_the_detailed_listing_reads_the_keys_once_and_no_key_state(self):
        resp, accessor, state = self._count(
            'target', '/auth/namespaces/target/keys',
            query_string={'detail': 'true'})

        self.assertEqual(9, len(resp.get_json()))
        self.assertEqual(1, accessor.call_count)
        self._assert_no_key_state_read(state)

    def test_the_bare_listing_reads_the_keys_once_and_no_key_state(self):
        resp, accessor, state = self._count(
            'target', '/auth/namespaces/target/keys')

        self.assertEqual(8, len(resp.get_json()))
        self.assertEqual(1, accessor.call_count)
        self._assert_no_key_state_read(state)

    def test_the_single_key_read_reads_the_keys_once_and_no_key_state(self):
        resp, accessor, state = self._count(
            'system', '/auth/namespaces/target/keys/extra-3')

        self.assertEqual('extra-3', resp.get_json()['key_name'])
        self.assertEqual(1, accessor.call_count)
        self._assert_no_key_state_read(state)
