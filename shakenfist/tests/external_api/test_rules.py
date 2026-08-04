"""The identity mapping rule CRUD API.

A rule decides that an external identity may act as a namespace, so
these tests care as much about who is refused as about what works: a
rule is owned by its namespace, and only that namespace's owner (or
somebody it is trusted by) may write one.
"""

import json
import logging
import sys

from shakenfist import mariadb
from shakenfist.external_api import app as external_api
from shakenfist.namespace import Namespace
from shakenfist.namespace_key import NamespaceKey
from shakenfist.schema.namespace_key_attributes import (
    NamespaceKeyAttributesData)
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.trusted_issuer import TrustedIssuer


GITHUB = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS = GITHUB + '/.well-known/jwks'


class RuleEndpointTestCase(base.ShakenFistTestCase):
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
        self.mock_mariadb.create_namespace('ci', 'key1', 'bacon')
        self.mock_mariadb.create_namespace('other', 'key1', 'sausage')

        TrustedIssuer.new(
            'github', GITHUB, GITHUB_JWKS, 'https://sf.example.com')

        self.client = external_api.app.test_client()
        self.admin = self._token('system', 'bar')
        self.owner = self._token('ci', 'bacon')
        self.stranger = self._token('other', 'sausage')

    def _token(self, namespace, key):
        resp = self.client.post(
            '/auth', data=json.dumps({'namespace': namespace, 'key': key}))
        self.assertEqual(200, resp.status_code)
        return 'Bearer %s' % resp.get_json()['access_token']

    def _create(self, token=None, namespace='ci', **overrides):
        body = {
            'name': 'ryll',
            'issuer': 'github',
            'bound_claims': {'repository': 'shakenfist/ryll'},
            'scopes': ['blob.read'],
            'key_ttl': 3600,
            'key_name_prefix': 'ryll-ci'
        }
        body.update(overrides)
        return self.client.post(
            '/auth/namespaces/%s/rules' % namespace,
            headers={'Authorization': token or self.owner},
            data=json.dumps(body))

    def test_the_namespace_owner_can_create_a_rule(self):
        resp = self._create()

        self.assertEqual(200, resp.status_code)
        self.assertEqual('ryll', resp.get_json()['name'])
        self.assertEqual(['blob.read'], resp.get_json()['scopes'])

    def test_a_stranger_cannot_create_a_rule_in_another_namespace(self):
        # The whole point of the ownership gate. A 404 rather than a
        # 403, matching every other namespace-scoped endpoint, so the
        # existence of a namespace is not disclosed.
        resp = self._create(token=self.stranger)
        self.assertEqual(404, resp.status_code)

    def test_a_stranger_cannot_list_another_namespaces_rules(self):
        self._create()
        resp = self.client.get(
            '/auth/namespaces/ci/rules',
            headers={'Authorization': self.stranger})
        self.assertEqual(404, resp.status_code)

    def test_an_unauthenticated_caller_is_refused(self):
        resp = self.client.get('/auth/namespaces/ci/rules')
        self.assertEqual(401, resp.status_code)

    def test_admin_reaches_any_namespace(self):
        # system is trusted by every namespace, which is how every
        # other namespace-scoped endpoint already behaves.
        self.assertEqual(200, self._create(token=self.admin).status_code)

    def test_rules_are_listed_and_fetched(self):
        self._create()

        listed = self.client.get(
            '/auth/namespaces/ci/rules',
            headers={'Authorization': self.owner})
        self.assertEqual(200, listed.status_code)
        self.assertEqual(['ryll'], [r['name'] for r in listed.get_json()])

        fetched = self.client.get(
            '/auth/namespaces/ci/rules/ryll',
            headers={'Authorization': self.owner})
        self.assertEqual(200, fetched.status_code)
        self.assertEqual('github', fetched.get_json()['issuer'])

    def test_a_duplicate_name_is_a_conflict(self):
        self.assertEqual(200, self._create().status_code)
        self.assertEqual(409, self._create().status_code)

    def test_an_unbound_rule_is_refused(self):
        # The single most important rejection: a rule with no bound
        # claims accepts every identity the issuer will ever vouch for.
        resp = self._create(bound_claims={})
        self.assertEqual(400, resp.status_code)
        self.assertIn('at least one claim', resp.get_json()['error'])

    def test_a_rule_granting_nothing_is_refused(self):
        resp = self._create(scopes=[])
        self.assertEqual(400, resp.status_code)
        self.assertIn('at least one scope', resp.get_json()['error'])

    def test_a_pattern_matcher_is_refused(self):
        # Patterns are deliberately absent from v1. This stores fine as
        # an exact string, and is refused only when it is not a string
        # at all -- so the test that matters is that the stored matcher
        # is compared exactly, which the object tests cover. Here we
        # check the shape rejection an operator is most likely to hit.
        resp = self._create(bound_claims={'ref': {'prefix': 'refs/heads/'}})
        self.assertEqual(400, resp.status_code)

    def test_an_unknown_issuer_is_refused(self):
        resp = self._create(issuer='nosuch')
        self.assertEqual(400, resp.status_code)
        self.assertIn('no trusted issuer', resp.get_json()['error'])

    def test_a_missing_field_is_refused(self):
        resp = self._create(issuer=None)
        self.assertEqual(400, resp.status_code)
        self.assertIn('issuer', resp.get_json()['error'])

    def test_a_rule_can_be_updated(self):
        self._create()
        resp = self.client.put(
            '/auth/namespaces/ci/rules/ryll',
            headers={'Authorization': self.owner},
            data=json.dumps({
                'issuer': 'github',
                'bound_claims': {'ref': ['refs/heads/main']},
                'scopes': ['blob.read', 'artifact.*'],
                'key_ttl': 900,
                'key_name_prefix': 'ryll-main'
            }))

        self.assertEqual(200, resp.status_code)
        self.assertEqual(['blob.read', 'artifact.*'],
                         resp.get_json()['scopes'])
        self.assertEqual(900, resp.get_json()['key_ttl'])

    def test_an_update_cannot_reach_a_state_a_create_would_refuse(self):
        self._create()
        resp = self.client.put(
            '/auth/namespaces/ci/rules/ryll',
            headers={'Authorization': self.owner},
            data=json.dumps({
                'issuer': 'github',
                'bound_claims': {},
                'scopes': ['blob.read'],
                'key_ttl': 900,
                'key_name_prefix': 'ryll-main'
            }))
        self.assertEqual(400, resp.status_code)

    def test_a_rule_can_be_deleted_and_stops_resolving(self):
        self._create()

        resp = self.client.delete(
            '/auth/namespaces/ci/rules/ryll',
            headers={'Authorization': self.owner})
        self.assertEqual(200, resp.status_code)

        gone = self.client.get(
            '/auth/namespaces/ci/rules/ryll',
            headers={'Authorization': self.owner})
        self.assertEqual(404, gone.status_code)

    def test_an_unknown_rule_is_a_404(self):
        resp = self.client.get(
            '/auth/namespaces/ci/rules/nope',
            headers={'Authorization': self.owner})
        self.assertEqual(404, resp.status_code)


class RuleScopeCeilingTestCase(RuleEndpointTestCase):
    """A rule may not grant scopes its author does not hold.

    A rule is a standing instruction to mint a key, so it is subject to
    the same ceiling `_namespace_keys_putpost` applies when minting one
    directly. Without it there is a two hop privilege escalation: write
    a rule granting `*`, satisfy its bound claims with an identity
    token, exchange that for a wildcard key. In the system namespace
    the wildcard reaches cluster-admin.
    """

    def _scoped_token(self, namespace, secret, scopes_granted):
        """A token whose key carries exactly `scopes_granted`."""
        name = 'scoped-%s' % secret
        ns = Namespace.from_db(namespace)
        ns.add_key(name, secret)

        key = ns.lookup_key(name)
        obj = NamespaceKey.from_db_by_name(namespace, name)
        mariadb.update_namespace_key_attributes(
            NamespaceKeyAttributesData(
                uuid=obj.uuid, key=key.key, nonce=key.nonce,
                expiry=key.expiry, scopes=scopes_granted, provenance=None))

        return self._token(namespace, secret)

    def test_a_rule_writer_cannot_grant_the_wildcard(self):
        # The escalation, refused. `rule.write` is not `*`, so it
        # cannot author a rule which hands out `*`.
        token = self._scoped_token('ci', 'narrow1', ['rule.write'])

        resp = self._create(token=token, scopes=['*'])
        self.assertEqual(403, resp.status_code)
        self.assertIn('do not hold', resp.get_data(as_text=True))

    def test_a_rule_writer_cannot_grant_cluster_admin(self):
        token = self._scoped_token('ci', 'narrow2', ['rule.write'])

        self.assertEqual(
            403, self._create(token=token, scopes=['cluster-admin']
                              ).status_code)

    def test_a_rule_writer_cannot_grant_an_unrelated_family(self):
        token = self._scoped_token(
            'ci', 'narrow3', ['rule.write', 'blob.read'])

        resp = self._create(token=token, scopes=['instance.delete'])
        self.assertEqual(403, resp.status_code)

    def test_a_rule_writer_cannot_widen_a_family_it_holds_one_verb_of(self):
        # Holding `blob.read` must not let you grant `blob.*`.
        token = self._scoped_token(
            'ci', 'narrow4', ['rule.write', 'blob.read'])

        self.assertEqual(
            403, self._create(token=token, scopes=['blob.*']).status_code)

    def test_a_rule_writer_can_grant_what_it_holds(self):
        # The control. Without this the 403s above could be the scope
        # gate on the endpoint itself rather than the ceiling.
        token = self._scoped_token(
            'ci', 'narrow5', ['rule.write', 'blob.read'])

        resp = self._create(token=token, scopes=['blob.read'])
        self.assertEqual(200, resp.status_code)
        self.assertEqual(['blob.read'], resp.get_json()['scopes'])

    def test_a_family_wildcard_holder_can_grant_a_verb_in_it(self):
        token = self._scoped_token(
            'ci', 'narrow6', ['rule.write', 'blob.*'])

        resp = self._create(token=token, scopes=['blob.read'])
        self.assertEqual(200, resp.status_code)

    def test_an_unscoped_caller_is_unrestricted(self):
        # Every operator holding a legacy key is in this state, and
        # their rules have to keep working exactly as before.
        resp = self._create(token=self.owner, scopes=['*'])
        self.assertEqual(200, resp.status_code)

    def test_the_ceiling_applies_to_update_as_well_as_create(self):
        # Otherwise the escalation is just one request longer: create a
        # modest rule, then widen it.
        self.assertEqual(200, self._create().status_code)
        token = self._scoped_token('ci', 'narrow7', ['rule.write'])

        resp = self.client.put(
            '/auth/namespaces/ci/rules/ryll',
            headers={'Authorization': token},
            data=json.dumps({
                'issuer': 'github',
                'bound_claims': {'repository': 'shakenfist/ryll'},
                'scopes': ['*'],
                'key_ttl': 3600,
                'key_name_prefix': 'ryll-ci'}))
        self.assertEqual(403, resp.status_code)

        # And the stored rule is untouched.
        rule = self.client.get(
            '/auth/namespaces/ci/rules/ryll',
            headers={'Authorization': self.owner}).get_json()
        self.assertEqual(['blob.read'], rule['scopes'])
