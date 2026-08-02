"""The TrustedIssuer object.

An issuer is who this cluster is willing to believe about identity, so
the properties worth pinning are the ones that stop it becoming
ambiguous or silently mutable: names are unique, configuration moves as
a set, and deletion follows the standard object lifecycle.
"""

from functools import partial

from shakenfist.baseobject import state_filter
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB
from shakenfist.trusted_issuer import TrustedIssuer
from shakenfist.trusted_issuer import TrustedIssuers


GITHUB = 'https://token.actions.githubusercontent.com'
GITHUB_JWKS = GITHUB + '/.well-known/jwks'


class TrustedIssuerTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()

    def _new(self, name='github', audience='https://sf.example.com'):
        return TrustedIssuer.new(name, GITHUB, GITHUB_JWKS, audience)

    def test_new_issuer_is_created_and_readable(self):
        issuer = self._new()

        self.assertIsNotNone(issuer)
        self.assertEqual('github', issuer.name)
        self.assertEqual(GITHUB, issuer.issuer_url)
        self.assertEqual(GITHUB_JWKS, issuer.jwks_uri)
        self.assertEqual('created', issuer.state.value)

    def test_lookup_by_name(self):
        created = self._new()
        found = TrustedIssuer.from_db_by_name('github')

        self.assertIsNotNone(found)
        self.assertEqual(created.uuid, found.uuid)

    def test_lookup_of_an_unknown_name_is_a_miss_not_an_error(self):
        self.assertIsNone(TrustedIssuer.from_db_by_name('nope'))

    def test_names_are_unique(self):
        # Rules reference an issuer by name, so two issuers sharing one
        # would make a rule ambiguous about who it trusts.
        self.assertIsNotNone(self._new())
        self.assertIsNone(self._new())

    def test_a_second_issuer_with_a_different_name_is_fine(self):
        self.assertIsNotNone(self._new(name='github'))
        self.assertIsNotNone(self._new(name='authentik'))
        self.assertEqual(2, len(list(TrustedIssuers([]))))

    def test_update_replaces_the_whole_configuration(self):
        issuer = self._new()
        issuer.update('https://other.example.com',
                      'https://other.example.com/jwks',
                      'https://sf2.example.com')

        reloaded = TrustedIssuer.from_db_by_name('github')
        self.assertEqual('https://other.example.com', reloaded.issuer_url)
        self.assertEqual('https://other.example.com/jwks',
                         reloaded.jwks_uri)
        self.assertEqual('https://sf2.example.com', reloaded.audience)

    def test_external_view_carries_the_configuration(self):
        issuer = self._new()
        view = issuer.external_view()

        self.assertEqual('github', view['name'])
        self.assertEqual(GITHUB, view['issuer_url'])
        self.assertEqual(GITHUB_JWKS, view['jwks_uri'])
        self.assertEqual('https://sf.example.com', view['audience'])

    def test_soft_delete_stops_the_issuer_resolving(self):
        issuer = self._new()
        issuer.delete()
        self.assertEqual('deleted', issuer.state.value)

        # Deleting an issuer has to revoke trust in it now, not when
        # the reaper eventually collects the row. The exchange resolves
        # issuers by name, so a soft-deleted one that still resolves is
        # one this cluster still believes.
        self.assertIsNone(TrustedIssuer.from_db_by_name('github'))

        # The row itself survives until the reaper runs, and an
        # operator inspecting history can still reach it.
        self.assertIsNotNone(
            TrustedIssuer.from_db_by_name('github', include_deleted=True))

        issuer.hard_delete()
        self.assertIsNone(
            TrustedIssuer.from_db_by_name('github', include_deleted=True))

    def test_soft_deleted_issuers_are_not_listed(self):
        self._new(name='github')
        self._new(name='authentik').delete()

        listed = {i.name for i in TrustedIssuers(
            [partial(state_filter, TrustedIssuer.ACTIVE_STATES)])}
        self.assertEqual({'github'}, listed)

    def test_name_is_reusable_after_a_soft_delete(self):
        first = self._new()
        first.delete()

        # The unique index still holds the old row, so reclaiming the
        # name means the superseded issuer has to actually go.
        second = self._new(audience='https://sf2.example.com')
        self.assertIsNotNone(second)
        self.assertNotEqual(first.uuid, second.uuid)
        self.assertEqual('https://sf2.example.com',
                         TrustedIssuer.from_db_by_name('github').audience)

    def test_hard_delete_removes_the_attributes_too(self):
        issuer = self._new()
        uuid = issuer.uuid
        issuer.hard_delete()

        self.assertNotIn(
            str(uuid), self.mock_mariadb.trusted_issuer_attributes)

    def test_name_is_reusable_after_a_hard_delete(self):
        self._new().hard_delete()
        self.assertIsNotNone(self._new())
