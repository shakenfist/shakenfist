"""Scope derivation and enforcement.

Scopes are derived from the resource class and the HTTP method rather
than hand-tagged, so that coverage is automatic and adding an endpoint
cannot silently leave it ungoverned. These tests pin the derivation
rule, the override mechanism, and the enforcement behaviour -- in
particular that everything which existed before scopes did keeps
working, which is the property that makes this safe to ship.
"""

import ast
import inspect
import json
import logging
import os
import sys

import flask_restful
from flask_jwt_extended import decode_token

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
        self.assertTrue(scopes.satisfies(['*'], scopes.ADMIN))

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


class FamilyWildcardTestCase(base.ShakenFistTestCase):
    """"<family>.*" grants every verb in one family and no more."""

    def test_family_wildcard_grants_every_verb(self):
        for verb in ('read', 'write', 'delete'):
            self.assertTrue(
                scopes.satisfies(['blob.*'], f'blob.{verb}'),
                f'blob.* should grant blob.{verb}')

    def test_family_wildcard_does_not_cross_families(self):
        self.assertFalse(scopes.satisfies(['blob.*'], 'instance.read'))

    def test_family_wildcard_is_not_a_string_prefix(self):
        # The match is on the family, not on characters, so a family
        # whose name merely starts with another's is unaffected. Get
        # this wrong and node.* silently reaches nodegroup.delete.
        self.assertFalse(scopes.satisfies(['node.*'], 'nodegroup.read'))
        self.assertFalse(scopes.satisfies(['node.*'], 'nodegroup.delete'))

    def test_family_wildcard_does_not_grant_administration(self):
        # ADMIN is dotless so that no family wildcard can produce it.
        # If it ever gains a dot this test fails, which is the point.
        self.assertFalse(scopes.satisfies(['cluster-admin.*'], scopes.ADMIN))
        self.assertFalse(scopes.satisfies(['admin.*'], scopes.ADMIN))
        self.assertNotIn('.', scopes.ADMIN)

    def test_family_wildcard_does_not_satisfy_an_underivable_scope(self):
        # Default deny survives the new matcher.
        self.assertFalse(scopes.satisfies(['blob.*'], None))

    def test_admin_family_wildcard_still_needs_the_admin_scope(self):
        # admin.* covers the derived scope for an admin endpoint but
        # says nothing about whether the caller may administer at all.
        self.assertTrue(scopes.satisfies(['admin.*'], 'admin.read'))
        self.assertFalse(scopes.satisfies(['admin.*'], scopes.ADMIN))


class RealEndpointDerivationTestCase(base.ShakenFistTestCase):
    """The derived families over the actual routing table.

    The risk with mechanical derivation is that it quietly produces a
    family nobody expected, so the whole set is pinned here and
    reviewed as data rather than inferred from the class names.
    """

    EXPECTED_FAMILIES = {
        'admin', 'agentoperation', 'artifact', 'auth', 'blob',
        'clusteroperation', 'instance', 'interface', 'issuer', 'label',
        'network', 'node', 'rule', 'upload',
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

    # The three derived verbs, plus the ones only reachable through an
    # @api_base.scope override. Adding a verb is a vocabulary decision:
    # the test is whether an operator would sensibly grant it alone.
    EXPECTED_VERBS = {'read', 'write', 'delete', 'console', 'execute'}

    def test_derived_verbs_are_as_expected(self):
        found = set()
        for rule in external_api.app.url_map.iter_rules():
            view = external_api.app.view_functions.get(rule.endpoint)
            resource = getattr(view, 'view_class', None)
            if resource is None or not issubclass(
                    resource, flask_restful.Resource):
                continue
            for method in rule.methods:
                if method in ('OPTIONS', 'HEAD'):
                    continue
                handler = getattr(resource, method.lower(), None)
                if handler is None:
                    continue
                required = scopes.required_scope(
                    resource, method, getattr(handler, '_sf_scope', None))
                if required:
                    found.add(required.split('.', 1)[1])

        self.assertEqual(
            self.EXPECTED_VERBS, found,
            'The set of scope verbs changed. A new verb is only worth '
            'having if an operator would sensibly grant it on its own, '
            'so this is a deliberate decision rather than a detail.')


class ClassBodyStringLiteralTestCase(base.ShakenFistTestCase):
    """No class may have a string literal below its first statement.

    Adding scope_family above a class docstring silently destroys it:
    a string that is not the first statement in the body is a no-op
    expression, so __doc__ becomes None and the prose stops describing
    anything. This happened to two endpoints while scope families were
    being added, and nothing failed.
    """

    def test_no_orphaned_docstrings_in_external_api(self):
        api_dir = os.path.dirname(
            inspect.getfile(external_api)).replace('/tests', '')
        offenders = []

        for name in sorted(os.listdir(api_dir)):
            if not name.endswith('.py'):
                continue
            path = os.path.join(api_dir, name)
            with open(path) as f:
                tree = ast.parse(f.read(), filename=path)

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for statement in node.body[1:]:
                    if (isinstance(statement, ast.Expr)
                            and isinstance(statement.value, ast.Constant)
                            and isinstance(statement.value.value, str)):
                        offenders.append(
                            f'{name}:{statement.lineno} '
                            f'{node.name} has a string literal below its '
                            'first statement')

        self.assertEqual(
            [], offenders,
            'A class body string literal that is not the first statement '
            'is dead code, and is almost always a docstring that an '
            'inserted attribute pushed out of place:\n  '
            + '\n  '.join(offenders))


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

    def test_a_key_granted_nothing_can_do_nothing(self):
        # An empty scope list is "granted nothing", which is a
        # different thing from the None of a legacy key. Testing the
        # list for truthiness conflates them and turns a grant of
        # nothing into a grant of everything.
        token = self._scoped_key('banana', 'empty', 'sekrit0', [])
        self.assertEqual(403, self.client.get(
            '/instances', headers={'Authorization': token}).status_code)

        # Assert on the claim too, so this fails for the right reason
        # rather than for any incidental 403.
        with external_api.app.app_context():
            claims = decode_token(token.split(' ', 1)[1])
        self.assertEqual([], claims['scopes'])

    def test_scoped_token_allowed_within_its_scope(self):
        token = self._scoped_key('banana', 'scoped', 'sekrit',
                                 ['instance.read'])
        resp = self.client.get('/instances',
                               headers={'Authorization': token})
        self.assertEqual(200, resp.status_code)

    def test_a_scoped_token_cannot_mint_a_broader_key(self):
        # Key creation is gated by namespace ownership, and a namespace
        # always owns itself, so without scope inheritance a token
        # holding auth.write could create an unscoped key beside itself
        # and re-authenticate carrying the wildcard. That is a complete
        # bypass of scoping, reachable by any scoped credential.
        token = self._scoped_key('banana', 'writer', 'sekrit1',
                                 ['auth.write', 'auth.read'])

        resp = self.client.post(
            '/auth/namespaces/banana/keys',
            headers={'Authorization': token},
            data=json.dumps({'key_name': 'escalated'}))
        self.assertEqual(200, resp.status_code)
        minted = resp.get_json()['key']

        resp = self.client.post(
            '/auth',
            data=json.dumps({'namespace': 'banana', 'key': minted}))
        self.assertEqual(200, resp.status_code)
        escalated = 'Bearer %s' % resp.get_json()['access_token']

        # The derived key inherited its creator's scopes rather than
        # becoming a wildcard.
        with external_api.app.app_context():
            claims = decode_token(escalated.split(' ', 1)[1])
        self.assertEqual(['auth.write', 'auth.read'], claims['scopes'])

        self.assertEqual(403, self.client.get(
            '/instances', headers={'Authorization': escalated}).status_code)

    def test_an_unscoped_caller_still_creates_unscoped_keys(self):
        # The compatibility half: an operator holding a legacy key must
        # keep creating ordinary unrestricted keys.
        token = self._unscoped_token('banana', 'bacon')

        resp = self.client.post(
            '/auth/namespaces/banana/keys',
            headers={'Authorization': token},
            data=json.dumps({'key_name': 'ordinary'}))
        self.assertEqual(200, resp.status_code)

        resp = self.client.post(
            '/auth', data=json.dumps({
                'namespace': 'banana', 'key': resp.get_json()['key']}))
        self.assertEqual(200, resp.status_code)
        derived = 'Bearer %s' % resp.get_json()['access_token']

        self.assertEqual(200, self.client.get(
            '/instances', headers={'Authorization': derived}).status_code)

    def test_instance_read_does_not_grant_console_control(self):
        # A console helper hands out SPICE credentials, which is
        # interactive keyboard and mouse control of the guest. The
        # design's worked example is a credential that can watch but
        # not destroy; a "read" scope that can drive the machine makes
        # that promise meaningless.
        token = self._scoped_key('banana', 'watcher', 'sekrit',
                                 ['instance.read'])

        for path in ('/instances/whatever/vdiconsolehelper',
                     '/instances/whatever/vdiconsoleproxy'):
            resp = self.client.get(path, headers={'Authorization': token})
            self.assertEqual(403, resp.status_code, path)
            self.assertIn('not scoped', resp.get_json()['error'])

    def test_instance_write_does_not_grant_in_guest_execution(self):
        token = self._scoped_key('banana', 'builder', 'sekrit',
                                 ['instance.write'])
        resp = self.client.post(
            '/instances/whatever/agent/execute',
            headers={'Authorization': token},
            data=json.dumps({'command_line': 'id'}))
        self.assertEqual(403, resp.status_code)

    def test_the_scope_override_reaches_enforcement(self):
        # The @api_base.scope override is only useful if the attribute
        # survives the decorator stack and is read off the bound method
        # at dispatch. Granting exactly the overridden scope proves the
        # whole path, not just the derivation helper.
        token = self._scoped_key('banana', 'operator', 'sekrit',
                                 ['instance.console'])
        resp = self.client.get('/instances/whatever/vdiconsolehelper',
                               headers={'Authorization': token})
        # 404 because the instance does not exist -- but the scope
        # check passed, which is what this is pinning.
        self.assertEqual(404, resp.status_code)

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
        # Decision 3 requires both: 'cluster-admin' says the token may
        # act administratively at all, and the derived scope says which
        # operation. Holding one without the other is not enough.
        admin_only = self._scoped_key(
            'system', 'a', 'sekrit1', [scopes.ADMIN])
        self.assertEqual(403, self.client.get(
            '/admin/locks',
            headers={'Authorization': admin_only}).status_code)

        derived_only = self._scoped_key(
            'system', 'b', 'sekrit2', ['admin.read'])
        self.assertEqual(403, self.client.get(
            '/admin/locks',
            headers={'Authorization': derived_only}).status_code)

        both = self._scoped_key(
            'system', 'c', 'sekrit3', [scopes.ADMIN, 'admin.read'])
        self.assertEqual(200, self.client.get(
            '/admin/locks',
            headers={'Authorization': both}).status_code)

    def test_least_privilege_admin_token(self):
        # The capability the two axis design exists to provide: a
        # monitoring credential with cluster wide visibility that
        # cannot destroy anything. Collapse administration into one
        # flag and this becomes inexpressible.
        readonly = self._scoped_key(
            'system', 'd', 'sekrit4', [scopes.ADMIN, 'node.read'])
        self.assertEqual(200, self.client.get(
            '/nodes', headers={'Authorization': readonly}).status_code)
        self.assertEqual(403, self.client.delete(
            '/nodes/nosuchnode',
            headers={'Authorization': readonly}).status_code)
