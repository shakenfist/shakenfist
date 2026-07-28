"""Scope derivation and enforcement.

Scopes are derived from the resource class and the HTTP method rather
than hand-tagged, so that coverage is automatic and adding an endpoint
cannot silently leave it ungoverned. These tests pin the derivation
rule, the override mechanism, and the enforcement behaviour -- in
particular that everything which existed before scopes did keeps
working, which is the property that makes this safe to ship.
"""

import json
import logging
import sys

import flask_restful

from shakenfist import mariadb
from shakenfist.external_api import app as external_api
from shakenfist.external_api import scopes
from shakenfist.namespace import Namespace
from shakenfist.namespace_key import NamespaceKey
from shakenfist.schema.namespace_key_attributes import (
    NamespaceKeyAttributesData)
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class DerivationTestCase(base.ShakenFistTestCase):
    def test_verbs_map_from_http_methods(self):
        self.assertEqual('read', scopes.verb_for_method('GET'))
        self.assertEqual('read', scopes.verb_for_method('head'))
        self.assertEqual('write', scopes.verb_for_method('POST'))
        self.assertEqual('write', scopes.verb_for_method('PUT'))
        self.assertEqual('write', scopes.verb_for_method('PATCH'))
        self.assertEqual('delete', scopes.verb_for_method('DELETE'))

    def test_unknown_method_derives_nothing(self):
        self.assertIsNone(scopes.verb_for_method('OPTIONS'))

    def test_family_comes_from_the_leading_word(self):
        class BlobEndpoint:
            pass

        class BlobMetadatasEndpoint:
            pass

        class InstancePowerOffEndpoint:
            pass

        self.assertEqual('blob', scopes.family_for_resource(BlobEndpoint))
        self.assertEqual(
            'blob', scopes.family_for_resource(BlobMetadatasEndpoint))
        self.assertEqual(
            'instance', scopes.family_for_resource(InstancePowerOffEndpoint))

    def test_plural_class_names_share_the_singular_family(self):
        class BlobEndpoint:
            pass

        class BlobsEndpoint:
            pass

        self.assertEqual(scopes.family_for_resource(BlobEndpoint),
                         scopes.family_for_resource(BlobsEndpoint))

    def test_scope_family_attribute_overrides_derivation(self):
        class ClusterOperationEndpoint:
            scope_family = 'clusteroperation'

        self.assertEqual(
            'clusteroperation',
            scopes.family_for_resource(ClusterOperationEndpoint))

    def test_required_scope_combines_family_and_verb(self):
        class BlobEndpoint:
            pass

        self.assertEqual(
            'blob.read', scopes.required_scope(BlobEndpoint, 'GET'))
        self.assertEqual(
            'blob.delete', scopes.required_scope(BlobEndpoint, 'DELETE'))

    def test_decorator_override_wins(self):
        class InstanceEndpoint:
            pass

        self.assertEqual(
            'instance.power',
            scopes.required_scope(InstanceEndpoint, 'POST',
                                  {'verb': 'power'}))
        self.assertEqual(
            'artifact.write',
            scopes.required_scope(InstanceEndpoint, 'POST',
                                  {'family': 'artifact'}))
        self.assertEqual(
            'something.odd',
            scopes.required_scope(InstanceEndpoint, 'POST',
                                  {'scope': 'something.odd'}))

    def test_underivable_scope_is_none(self):
        class Weird:
            pass

        # An unrecognised HTTP method derives no verb.
        self.assertIsNone(scopes.required_scope(Weird, 'OPTIONS'))


class SatisfiesTestCase(base.ShakenFistTestCase):
    def test_wildcard_satisfies_everything(self):
        self.assertTrue(scopes.satisfies(['*'], 'blob.read'))
        self.assertTrue(scopes.satisfies(['*'], 'admin'))

    def test_absent_claim_is_treated_as_wildcard(self):
        # Tokens minted before the scopes claim existed carry no
        # claim at all. They came from unscoped keys, and refusing
        # them would invalidate every token in flight across an
        # upgrade.
        self.assertTrue(scopes.satisfies(None, 'blob.read'))

    def test_exact_match_satisfies(self):
        self.assertTrue(scopes.satisfies(['blob.read'], 'blob.read'))

    def test_other_scopes_do_not_satisfy(self):
        self.assertFalse(scopes.satisfies(['blob.read'], 'blob.write'))
        self.assertFalse(scopes.satisfies(['blob.read'], 'instance.read'))

    def test_scoped_token_is_denied_when_derivation_failed(self):
        # Default deny: a scope system that allows what it cannot
        # classify is not one. The wildcard still passes, so this
        # only ever bites deliberately scoped credentials.
        self.assertFalse(scopes.satisfies(['blob.read'], None))
        self.assertTrue(scopes.satisfies(['*'], None))


class RealEndpointDerivationTestCase(base.ShakenFistTestCase):
    """The derived families over the actual routing table.

    The risk with mechanical derivation is that it quietly produces a
    family nobody expected, so the whole set is pinned here and
    reviewed as data rather than inferred from the class names.
    """

    EXPECTED_FAMILIES = {
        'admin', 'agentoperation', 'artifact', 'auth', 'blob',
        'clusteroperation', 'instance', 'interface', 'issuer', 'label',
        'network', 'node', 'upload',
        # Public endpoints. Their family is never consulted because
        # @public short-circuits before enforcement, but they are
        # listed so the assertion below is exhaustive.
        'livez', 'readyz', 'root',
    }

    def test_derived_families_are_as_expected(self):
        found = set()
        for rule in external_api.app.url_map.iter_rules():
            view = external_api.app.view_functions.get(rule.endpoint)
            resource = getattr(view, 'view_class', None)
            if resource is None or not issubclass(
                    resource, flask_restful.Resource):
                continue
            family = scopes.family_for_resource(resource)
            self.assertIsNotNone(
                family, f'{resource.__name__} derives no scope family')
            found.add(family)

        self.assertEqual(
            self.EXPECTED_FAMILIES, found,
            'The set of scope families changed. These are the words '
            'operators write in mapping rules, so a new one is a '
            'vocabulary decision and a renamed one silently changes '
            'what existing rules grant.')


class EnforcementTestCase(base.ShakenFistTestCase):
    """End to end: a scoped token may do only what it was granted."""

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
        self.mock_mariadb.create_namespace('system', 'key1', 'bar')
        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

        self.client = external_api.app.test_client()

    def _scoped_key(self, namespace, name, secret, scopes_granted):
        """Add a key carrying scopes, and mint a token from it."""
        ns = Namespace.from_db(namespace)
        ns.add_key(name, secret)
        key = ns.lookup_key(name)
        # add_key does not take scopes yet -- the federated exchange in
        # a later step is what sets them -- so write them directly.
        obj = NamespaceKey.from_db_by_name(namespace, name)
        mariadb.update_namespace_key_attributes(
            NamespaceKeyAttributesData(
                uuid=obj.uuid, key=key.key, nonce=key.nonce,
                expiry=key.expiry, scopes=scopes_granted,
                provenance=None))

        resp = self.client.post(
            '/auth',
            data=json.dumps({'namespace': namespace, 'key': secret}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _unscoped_token(self, namespace, secret):
        resp = self.client.post(
            '/auth',
            data=json.dumps({'namespace': namespace, 'key': secret}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def test_unscoped_key_mints_a_wildcard_token(self):
        token = self._unscoped_token('banana', 'bacon')
        resp = self.client.get('/instances',
                               headers={'Authorization': token})
        self.assertEqual(200, resp.status_code)

    def test_scoped_token_allowed_within_its_scope(self):
        token = self._scoped_key('banana', 'scoped', 'sekrit',
                                 ['instance.read'])
        resp = self.client.get('/instances',
                               headers={'Authorization': token})
        self.assertEqual(200, resp.status_code)

    def test_scoped_token_refused_outside_its_scope(self):
        token = self._scoped_key('banana', 'scoped', 'sekrit',
                                 ['blob.read'])
        resp = self.client.get('/instances',
                               headers={'Authorization': token})
        self.assertEqual(403, resp.status_code)
        self.assertIn('not scoped', resp.get_json()['error'])

    def test_scoped_token_refused_for_the_wrong_verb(self):
        # Read is granted; delete is a different scope entirely.
        token = self._scoped_key('banana', 'scoped', 'sekrit',
                                 ['instance.read'])
        resp = self.client.delete('/instances',
                                  headers={'Authorization': token})
        self.assertEqual(403, resp.status_code)

    def test_scoped_system_key_cannot_reach_admin_endpoints(self):
        # The escalation this closes: being in the system namespace
        # used to be sufficient for every administrative endpoint, so
        # a narrowly scoped key minted into system was a full cluster
        # administrator.
        token = self._scoped_key('system', 'scoped', 'sekrit',
                                 ['blob.read'])
        resp = self.client.get('/admin/locks',
                               headers={'Authorization': token})
        self.assertEqual(403, resp.status_code)

    def test_unscoped_system_key_still_reaches_admin_endpoints(self):
        # The compatibility half of the same change: existing admin
        # automation holds unscoped keys and must be unaffected.
        token = self._unscoped_token('system', 'bar')
        resp = self.client.get('/admin/locks',
                               headers={'Authorization': token})
        self.assertEqual(200, resp.status_code)

    def test_admin_endpoints_need_both_admin_and_the_derived_scope(self):
        # Decision 3 requires both: 'admin' says the token may act
        # administratively at all, and the derived scope says which
        # operation. Holding one without the other is not enough.
        admin_only = self._scoped_key('system', 'a', 'sekrit1', ['admin'])
        self.assertEqual(403, self.client.get(
            '/admin/locks',
            headers={'Authorization': admin_only}).status_code)

        derived_only = self._scoped_key(
            'system', 'b', 'sekrit2', ['admin.read'])
        self.assertEqual(403, self.client.get(
            '/admin/locks',
            headers={'Authorization': derived_only}).status_code)

        both = self._scoped_key(
            'system', 'c', 'sekrit3', ['admin', 'admin.read'])
        self.assertEqual(200, self.client.get(
            '/admin/locks',
            headers={'Authorization': both}).status_code)
