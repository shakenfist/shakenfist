"""The MappingRule object.

A rule is the thing that decides an external identity may act as a
namespace, so the properties worth pinning are the ones that stop it
granting more than its author meant: claim matchers cannot be shaped
so that they match everything, a rule always says what it grants, and
the (namespace, name) pair is unambiguous.
"""

from functools import partial
from unittest import mock

from shakenfist import mapping_rule
from shakenfist.baseobject import state_filter
from shakenfist.mapping_rule import MappingRule
from shakenfist.mapping_rule import MappingRules
from shakenfist.mapping_rule import RuleValidationError
from shakenfist.mapping_rule import rules_in_namespace
from shakenfist.mapping_rule import validate_bound_claims
from shakenfist.mapping_rule import validate_key_ttl
from shakenfist.mapping_rule import validate_scopes
from shakenfist.namespace import Namespace
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.trusted_issuer import TrustedIssuer


GITHUB = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS = GITHUB + '/.well-known/jwks'

CLAIMS = {'repository': 'shakenfist/ryll'}
SCOPES = ['blob.read']


class MappingRuleTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        TrustedIssuer.new(
            'github', GITHUB, GITHUB_JWKS, 'https://sf.example.com')

    def _new(self, namespace='ci', name='ryll', issuer='github',
             bound_claims=None, scopes=None, key_ttl=3600,
             key_name_prefix='ryll-ci'):
        return MappingRule.new(
            namespace, name, issuer,
            dict(CLAIMS) if bound_claims is None else bound_claims,
            list(SCOPES) if scopes is None else scopes,
            key_ttl, key_name_prefix)

    def test_new_rule_is_created_and_readable(self):
        rule = self._new()

        self.assertIsNotNone(rule)
        self.assertEqual('ci', rule.namespace)
        self.assertEqual('ryll', rule.name)
        self.assertEqual('github', rule.issuer)
        self.assertEqual(CLAIMS, rule.bound_claims)
        self.assertEqual(SCOPES, rule.scopes)
        self.assertEqual(3600, rule.key_ttl)
        self.assertEqual('ryll-ci', rule.key_name_prefix)
        self.assertEqual('created', rule.state.value)

    def test_lookup_by_namespace_and_name(self):
        created = self._new()
        found = MappingRule.from_db_by_name('ci', 'ryll')

        self.assertIsNotNone(found)
        self.assertEqual(created.uuid, found.uuid)

    def test_lookup_of_an_unknown_rule_is_a_miss_not_an_error(self):
        self.assertIsNone(MappingRule.from_db_by_name('ci', 'nope'))

    def test_names_are_unique_within_a_namespace(self):
        self.assertIsNotNone(self._new())
        self.assertIsNone(self._new())

    def test_the_same_name_in_another_namespace_is_a_different_rule(self):
        # Rules are namespaced, so two teams naming their rule "ryll"
        # is ordinary rather than a collision.
        self.assertIsNotNone(self._new(namespace='ci'))
        self.assertIsNotNone(self._new(namespace='staging'))
        self.assertEqual(2, len(list(MappingRules([]))))

    def test_several_rules_may_bind_the_same_issuer(self):
        # Two repositories legitimately feed one namespace, and
        # forbidding this would push operators towards one over-broad
        # rule -- the opposite of what the design wants.
        self.assertIsNotNone(self._new(name='ryll'))
        self.assertIsNotNone(self._new(
            name='instar', bound_claims={'repository': 'shakenfist/instar'}))

    def test_listing_is_scoped_to_one_namespace(self):
        self._new(namespace='ci')
        self._new(namespace='staging')

        found = list(MappingRules([], namespace='ci'))
        self.assertEqual(1, len(found))
        self.assertEqual('ci', found[0].namespace)


class MappingRuleValidationTestCase(base.ShakenFistTestCase):
    """A rule that exists is a rule that was safe to write.

    Every case here is one where a rule would have granted more than
    its author meant, or would silently never have fired.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        TrustedIssuer.new(
            'github', GITHUB, GITHUB_JWKS, 'https://sf.example.com')

    def test_a_rule_must_bind_at_least_one_claim(self):
        # The whole rule. An unbound rule accepts every identity the
        # issuer will ever vouch for, which is not a federation.
        self.assertRaises(RuleValidationError, validate_bound_claims, {})

    def test_bound_claims_must_be_an_object(self):
        self.assertRaises(
            RuleValidationError, validate_bound_claims, ['repository'])

    def test_an_exact_matcher_is_accepted(self):
        self.assertEqual(
            {'repository': 'shakenfist/ryll'},
            validate_bound_claims({'repository': 'shakenfist/ryll'}))

    def test_an_enumerated_matcher_is_accepted(self):
        refs = ['refs/heads/develop', 'refs/heads/main']
        self.assertEqual(
            {'ref': refs}, validate_bound_claims({'ref': refs}))

    def test_an_empty_string_matcher_is_refused(self):
        # No claim value equals the empty string, so this rule could
        # never fire -- which means it is a mistake, not a policy.
        self.assertRaises(
            RuleValidationError, validate_bound_claims, {'ref': ''})

    def test_an_empty_list_matcher_is_refused(self):
        self.assertRaises(
            RuleValidationError, validate_bound_claims, {'ref': []})

    def test_a_list_matcher_of_empty_strings_is_refused(self):
        self.assertRaises(
            RuleValidationError, validate_bound_claims, {'ref': ['a', '']})

    def test_a_boolean_matcher_is_refused(self):
        # A client sending true rather than "true" would otherwise
        # store a matcher that never equals a JSON string claim, and
        # the rule would silently never fire.
        self.assertRaises(
            RuleValidationError, validate_bound_claims, {'verified': True})

    def test_a_numeric_matcher_is_refused(self):
        self.assertRaises(
            RuleValidationError, validate_bound_claims, {'run': 12})

    def test_a_null_matcher_is_refused(self):
        self.assertRaises(
            RuleValidationError, validate_bound_claims, {'ref': None})

    def test_a_nested_matcher_is_refused(self):
        self.assertRaises(
            RuleValidationError, validate_bound_claims,
            {'ref': {'startswith': 'refs/heads/'}})

    def test_scopes_must_be_present_and_non_empty(self):
        # Unlike a NamespaceKey, where a missing scope list means
        # unscoped and therefore wildcard, an empty list here must not
        # become the loosest possible grant.
        self.assertRaises(RuleValidationError, validate_scopes, [])

    def test_scopes_must_be_a_list(self):
        self.assertRaises(RuleValidationError, validate_scopes, 'blob.read')

    def test_scopes_must_be_non_empty_strings(self):
        self.assertRaises(RuleValidationError, validate_scopes, ['blob.read',
                                                                 ''])

    def test_key_ttl_must_be_positive(self):
        self.assertRaises(RuleValidationError, validate_key_ttl, 0)
        self.assertRaises(RuleValidationError, validate_key_ttl, -1)

    def test_key_ttl_must_be_an_integer(self):
        self.assertRaises(RuleValidationError, validate_key_ttl, 'an hour')
        # bool is a subclass of int, and "key_ttl": true is not a time.
        self.assertRaises(RuleValidationError, validate_key_ttl, True)

    def test_key_ttl_has_an_upper_bound(self):
        # A federated key stands in for an identity token valid for
        # minutes. Without a ceiling a rule can mint a credential that
        # outlives the thing which justified it by a year.
        self.assertEqual(
            mapping_rule.MAX_KEY_TTL_SECONDS,
            validate_key_ttl(mapping_rule.MAX_KEY_TTL_SECONDS))
        self.assertRaises(
            RuleValidationError, validate_key_ttl,
            mapping_rule.MAX_KEY_TTL_SECONDS + 1)

    def test_a_reserved_key_name_prefix_is_refused(self):
        # The key endpoints refuse these names, so a rule minting keys
        # with them would be a way around that check.
        for prefix in ('service_key', '_service_key', '_service_key-ci'):
            self.assertRaises(
                RuleValidationError,
                mapping_rule.validate_key_name_prefix, prefix)

    def test_a_reserved_prefix_cannot_be_smuggled_in_through_a_rule(self):
        self.assertRaises(
            RuleValidationError, MappingRule.new, 'ci', 'ryll', 'github',
            dict(CLAIMS), list(SCOPES), 3600, '_service_key')
        self.assertIsNone(MappingRule.from_db_by_name('ci', 'ryll'))

    def test_oversized_fields_are_refused_rather_than_stored(self):
        # Every one of these is a field with no natural bound, so
        # without a check the refusal happens at the database and the
        # operator gets a 500 instead of a message they can act on.
        too_long = 'x' * 10000
        cases = [
            ('key_name_prefix',
             lambda: mapping_rule.validate_key_name_prefix(too_long)),
            ('claim name',
             lambda: validate_bound_claims({too_long: 'a'})),
            ('claim value',
             lambda: validate_bound_claims({'repository': too_long})),
            ('claim alternative',
             lambda: validate_bound_claims({'repository': [too_long]})),
            ('claim count',
             lambda: validate_bound_claims(
                 {str(i): 'a' for i in range(
                     mapping_rule.MAX_BOUND_CLAIMS + 1)})),
            ('alternative count',
             lambda: validate_bound_claims(
                 {'ref': ['a'] * (mapping_rule.MAX_CLAIM_ALTERNATIVES + 1)})),
            ('scope length', lambda: validate_scopes([too_long])),
            ('scope count',
             lambda: validate_scopes(
                 ['blob.read'] * (mapping_rule.MAX_SCOPES + 1))),
        ]
        for name, call in cases:
            self.assertRaises(RuleValidationError, call)

    def test_the_issuer_must_exist(self):
        self.assertRaises(
            RuleValidationError, MappingRule.new, 'ci', 'ryll', 'nosuch',
            dict(CLAIMS), list(SCOPES), 3600, 'ryll-ci')

    def test_a_deleted_issuer_does_not_count_as_existing(self):
        # from_db_by_name filters deleted issuers, so a rule cannot be
        # written against an issuer the cluster has stopped trusting.
        TrustedIssuer.from_db_by_name('github').delete()
        self.assertRaises(
            RuleValidationError, MappingRule.new, 'ci', 'ryll', 'github',
            dict(CLAIMS), list(SCOPES), 3600, 'ryll-ci')

    def test_creation_is_refused_whole_when_validation_fails(self):
        # A rejected create must leave nothing behind, or the next
        # attempt collides with a rule nobody can see.
        self.assertRaises(
            RuleValidationError, MappingRule.new, 'ci', 'ryll', 'github',
            {}, list(SCOPES), 3600, 'ryll-ci')
        self.assertIsNone(MappingRule.from_db_by_name('ci', 'ryll'))

    def test_update_is_validated_exactly_as_creation_is(self):
        # An edit must not be able to reach a state a create would
        # have refused.
        rule = MappingRule.new(
            'ci', 'ryll', 'github', dict(CLAIMS), list(SCOPES), 3600,
            'ryll-ci')
        self.assertRaises(
            RuleValidationError, rule.update, 'github', {}, list(SCOPES),
            3600, 'ryll-ci')
        self.assertRaises(
            RuleValidationError, rule.update, 'github', dict(CLAIMS), [],
            3600, 'ryll-ci')

        # ... and the rule is unchanged by the refusals.
        self.assertEqual(CLAIMS, rule.bound_claims)
        self.assertEqual(SCOPES, rule.scopes)


class MappingRuleLifecycleTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        TrustedIssuer.new(
            'github', GITHUB, GITHUB_JWKS, 'https://sf.example.com')

    def _new(self, namespace='ci', name='ryll'):
        return MappingRule.new(
            namespace, name, 'github', dict(CLAIMS), list(SCOPES), 3600,
            'ryll-ci')

    def test_deleting_a_rule_hides_it_immediately(self):
        # Deleting must stop the rule minting keys now, rather than
        # when the reaper eventually collects the row.
        rule = self._new()
        rule.delete()

        self.assertIsNone(MappingRule.from_db_by_name('ci', 'ryll'))
        self.assertIsNotNone(
            MappingRule.from_db_by_name('ci', 'ryll', include_deleted=True))

    def test_a_deleted_rule_is_not_listed(self):
        self._new()
        self._new(name='other').delete()

        listed = list(MappingRules(
            [partial(state_filter, MappingRule.ACTIVE_STATES)],
            namespace='ci'))
        self.assertEqual(['ryll'], [r.name for r in listed])

    def test_the_name_of_a_deleted_rule_can_be_reused(self):
        first = self._new()
        first.delete()

        second = self._new()
        self.assertIsNotNone(second)
        self.assertNotEqual(first.uuid, second.uuid)

    def test_updating_a_rule_replaces_its_policy(self):
        rule = self._new()
        rule.update('github', {'ref': ['refs/heads/main']},
                    ['blob.read', 'artifact.*'], 900, 'ryll-main')

        self.assertEqual({'ref': ['refs/heads/main']}, rule.bound_claims)
        self.assertEqual(['blob.read', 'artifact.*'], rule.scopes)
        self.assertEqual(900, rule.key_ttl)
        self.assertEqual('ryll-main', rule.key_name_prefix)

    def test_hard_delete_removes_the_attributes_too(self):
        rule = self._new()
        rule_uuid = rule.uuid
        rule.hard_delete()

        self.assertIsNone(MappingRule.from_db_by_name(
            'ci', 'ryll', include_deleted=True))
        self.assertNotIn(str(rule_uuid),
                         self.mock_mariadb.mapping_rule_attributes)

    def test_external_view_carries_the_policy(self):
        view = self._new().external_view()

        self.assertEqual('ci', view['namespace'])
        self.assertEqual('ryll', view['name'])
        self.assertEqual('github', view['issuer'])
        self.assertEqual(CLAIMS, view['bound_claims'])
        self.assertEqual(SCOPES, view['scopes'])
        self.assertEqual(3600, view['key_ttl'])
        self.assertEqual('ryll-ci', view['key_name_prefix'])

    def test_rules_in_namespace_ignores_state(self):
        # Namespace.hard_delete() is the last chance to remove rules,
        # so this accessor must see deleted ones too.
        self._new()
        self._new(name='gone').delete()

        self.assertEqual(
            {'ryll', 'gone'},
            {r.name for r in rules_in_namespace('ci')})


class SystemNamespaceRuleTestCase(base.ShakenFistTestCase):
    """A rule targeting system is allowed, but never quiet.

    system is the one namespace where a minted key sits next to a
    cluster-admin grant, so the phase plan asks for a warning at rule
    creation rather than a refusal -- forbidding it would push
    operators towards a long-lived static key, which is worse.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        TrustedIssuer.new(
            'github', GITHUB, GITHUB_JWKS, 'https://sf.example.com')

    def _new(self, namespace):
        return MappingRule.new(
            namespace, 'ryll', 'github', dict(CLAIMS), list(SCOPES), 3600,
            'ryll-ci')

    def test_a_system_rule_is_allowed_but_audited(self):
        with mock.patch.object(MappingRule, 'add_event') as add_event:
            rule = self._new('system')

        self.assertIsNotNone(rule)
        messages = [c.args[1] for c in add_event.call_args_list
                    if len(c.args) > 1]
        self.assertIn('mapping rule targets the system namespace', messages)

    def test_an_ordinary_rule_is_not_audited_that_way(self):
        with mock.patch.object(MappingRule, 'add_event') as add_event:
            self._new('ci')

        messages = [c.args[1] for c in add_event.call_args_list
                    if len(c.args) > 1]
        self.assertNotIn(
            'mapping rule targets the system namespace', messages)


class NamespaceCascadeTestCase(base.ShakenFistTestCase):
    """Rules die with the namespace that owns them.

    A rule names its namespace by name rather than by uuid, so a rule
    outliving its namespace is not merely litter: if the name were ever
    recreated, the new owner would inherit a federation trust they
    never asked for.
    """

    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        TrustedIssuer.new(
            'github', GITHUB, GITHUB_JWKS, 'https://sf.example.com')
        Namespace.new('ci')

    def test_hard_deleting_a_namespace_removes_its_rules(self):
        MappingRule.new('ci', 'ryll', 'github', dict(CLAIMS), list(SCOPES),
                        3600, 'ryll-ci')
        self.assertEqual(1, len(rules_in_namespace('ci')))

        Namespace.from_db('ci').hard_delete()

        self.assertEqual([], rules_in_namespace('ci'))
        self.assertEqual({}, self.mock_mariadb.mapping_rule_attributes)

    def test_a_soft_deleted_rule_is_collected_too(self):
        # hard_delete is the last chance, so state must not exempt a
        # rule from it.
        MappingRule.new('ci', 'gone', 'github', dict(CLAIMS), list(SCOPES),
                        3600, 'ryll-ci').delete()

        Namespace.from_db('ci').hard_delete()

        self.assertEqual([], rules_in_namespace('ci'))

    def test_another_namespaces_rules_are_untouched(self):
        Namespace.new('staging')
        MappingRule.new('ci', 'ryll', 'github', dict(CLAIMS), list(SCOPES),
                        3600, 'ryll-ci')
        MappingRule.new('staging', 'ryll', 'github', dict(CLAIMS),
                        list(SCOPES), 3600, 'ryll-ci')

        Namespace.from_db('ci').hard_delete()

        self.assertEqual([], rules_in_namespace('ci'))
        self.assertEqual(1, len(rules_in_namespace('staging')))
