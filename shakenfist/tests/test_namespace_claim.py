"""The NamespaceClaim object.

A claim is a promise the cluster has made, so the properties worth
pinning are the ones that stop the promise and the accounting drifting
apart: the object's existence state and the claim's coverage state stay
two separate facts, a deleted claim gives its capacity back, and a
namespace cannot take its claims to the grave with it.
"""

from unittest import mock

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist import namespace_claim
from shakenfist.constants import get_object_class
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.namespace import Namespace
from shakenfist.namespace_claim import claims_in_namespace
from shakenfist.namespace_claim import ClaimRefused
from shakenfist.namespace_claim import NamespaceClaim
from shakenfist.namespace_claim import NamespaceClaims
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NamespaceClaimTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        Namespace.new('ci')

    def _new(self, namespace='ci', limit_cpus=4, limit_memory_mb=8192,
             limit_disk_gb=100, expires_in_seconds=3600):
        return NamespaceClaim.new(
            namespace, limit_cpus, limit_memory_mb, limit_disk_gb,
            expires_in_seconds)

    def _row(self, claim):
        """The stored claim row, reached around the object deliberately.

        Tests which want to move a counter or expire a claim are
        standing in for the reconciler and the admission path, neither
        of which goes through the object.
        """
        return self.mock_mariadb.namespace_claims[str(claim.uuid)]

    def _state_row(self, claim):
        return self.mock_mariadb.mariadb_states.get(
            f'{ObjectType.NAMESPACE_CLAIM}/{claim.uuid}')


class NamespaceClaimCreationTestCase(NamespaceClaimTestCase):
    def test_new_writes_the_row_and_the_state(self):
        c = self._new()

        self.assertEqual('ci', c.namespace)
        self.assertEqual(NamespaceClaim.STATE_CREATED, c.state.value)

        # The claim row exists, with the limits asked for.
        row = self._row(c)
        self.assertEqual(4, row['limit_cpus'])
        self.assertEqual(8192, row['limit_memory_mb'])
        self.assertEqual(100, row['limit_disk_gb'])

        # ... and so does the object state row, so the claim is visible
        # to every state driven iterator rather than being a zombie.
        self.assertIsNotNone(self._state_row(c))

    def test_from_db_reads_the_claim_back(self):
        c = self._new()

        found = NamespaceClaim.from_db(str(c.uuid))
        self.assertIsNotNone(found)
        self.assertEqual(c.uuid, found.uuid)
        self.assertEqual('ci', found.namespace)
        self.assertEqual(
            {'cpus': 4, 'memory_mb': 8192, 'disk_gb': 100}, found.limits)
        self.assertEqual(
            {'cpus': 0, 'memory_mb': 0, 'disk_gb': 0}, found.used)
        self.assertEqual(NamespaceClaim.STATE_CREATED, found.state.value)

    def test_from_db_of_an_unknown_claim_is_none(self):
        self.assertIsNone(NamespaceClaim.from_db(
            '11111111-2222-4333-8444-555555555555'))

    def test_the_version_is_the_reading_builds(self):
        # The claims table has no version column, so a claim reports the
        # version of the build reading it. If that ever stops being true
        # this test is the place the assumption is written down.
        c = self._new()
        self.assertEqual(NamespaceClaim.current_version, c.version)


class NamespaceClaimTwoStatesTestCase(NamespaceClaimTestCase):
    """D2: existence and coverage are two facts, and stay two facts."""

    def test_external_view_publishes_both_states_distinctly(self):
        c = self._new()

        view = c.external_view()
        self.assertEqual(NamespaceClaim.STATE_CREATED, view['state'])
        self.assertEqual(namespace_claim.COVERAGE_ACTIVE,
                         view['coverage_state'])
        self.assertEqual('ci', view['namespace'])
        self.assertEqual(4, view['limit_cpus'])
        self.assertEqual(0, view['used_cpus'])
        self.assertIsNotNone(view['expires_at'])

    def test_an_expired_claim_is_still_a_created_object(self):
        # The expiry sweep moves coverage, and nothing else. An expired
        # claim which had also become a `deleted` object would be
        # invisible to every listing and would still be holding cluster
        # capacity.
        c = self._new()
        self._row(c)['state'] = namespace_claim.COVERAGE_EXPIRED

        view = c.external_view()
        self.assertEqual(namespace_claim.COVERAGE_EXPIRED,
                         view['coverage_state'])
        self.assertEqual(
            NamespaceClaim.STATE_CREATED, view['state'],
            'expiring coverage must not touch the object state')
        self.assertEqual(namespace_claim.COVERAGE_EXPIRED, c.coverage_state)
        self.assertEqual(
            NamespaceClaim.STATE_CREATED,
            NamespaceClaim.from_db(str(c.uuid)).state.value)

    def test_the_object_state_is_not_written_into_the_claim_row(self):
        # The other direction of the same rule: nothing about the
        # object's lifecycle may appear in the coverage column.
        c = self._new()
        self.assertEqual(namespace_claim.COVERAGE_ACTIVE, self._row(c)['state'])
        self.assertNotIn(
            self._row(c)['state'],
            (NamespaceClaim.STATE_INITIAL, NamespaceClaim.STATE_CREATED,
             NamespaceClaim.STATE_DELETED))


class NamespaceClaimDeletionTestCase(NamespaceClaimTestCase):
    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    def test_hard_delete_removes_the_row_and_returns_the_capacity(
            self, _mock_delete_events):
        c = self._new()
        row = self._row(c)
        row['used_cpus'] = 3
        row['used_memory_mb'] = 4096
        row['used_disk_gb'] = 50
        claim_uuid = str(c.uuid)

        mariadb.delete_namespace_claim.reset_mock()
        with mock.patch('shakenfist.namespace_claim.eventlog.add_event') as ae:
            c.hard_delete()

        self.assertNotIn(claim_uuid, self.mock_mariadb.namespace_claims)
        self.assertIsNone(NamespaceClaim.from_db(
            claim_uuid, suppress_failure_audit=True))
        self.assertIsNone(self._state_row(c))

        # Removal must go through the capacity returning primitive
        # rather than deleting the row, or the cluster's claimed_*
        # counters keep the capacity forever.
        mariadb.delete_namespace_claim.assert_called_once_with(claim_uuid)

        extras = [call.kwargs.get('extra') or {} for call in ae.call_args_list]
        returned = [e for e in extras if 'returned_cpus' in e]
        self.assertEqual(
            1, len(returned),
            'deleting a claim must record the capacity it returned')
        self.assertEqual(3, returned[0]['returned_cpus'])
        self.assertEqual(4096, returned[0]['returned_memory_mb'])
        self.assertEqual(50, returned[0]['returned_disk_gb'])

    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    def test_hard_delete_twice_is_harmless(self, _mock_delete_events):
        c = self._new()
        c.hard_delete()
        c.hard_delete()

        self.assertEqual({}, self.mock_mariadb.namespace_claims)

    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    def test_a_failed_delete_leaves_the_claim_whole(self, _mock_delete_events):
        # A delete that failed is not a delete that found nothing. The
        # row survives holding claimed_*, so tearing down the state row
        # on top of it would strand the capacity somewhere no sweep
        # looks: the row would be there, and the object that explains it
        # would not.
        c = self._new()
        claim_uuid = str(c.uuid)

        with mock.patch(
                'shakenfist.mariadb.delete_namespace_claim',
                return_value={
                    'success': False,
                    'error': 'the claim row kept changing under '
                             'concurrent writers',
                    'deleted': False, 'returned_cpus': 0,
                    'returned_memory_mb': 0, 'returned_disk_gb': 0,
                    'clamped': False}):
            self.assertRaises(exceptions.WriteException, c.hard_delete)

        self.assertIn(claim_uuid, self.mock_mariadb.namespace_claims)
        self.assertIsNotNone(
            self._state_row(c),
            'the state row went while the claim row and its capacity '
            'stayed, which is the pairing nothing repairs')
        self.assertIsNotNone(NamespaceClaim.from_db(claim_uuid))


class NamespaceCascadeTestCase(NamespaceClaimTestCase):
    """A claim cannot outlive the namespace it sizes."""

    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    def test_hard_deleting_a_namespace_removes_its_claims(
            self, _mock_delete_events):
        c = self._new()
        self.assertEqual(1, len(claims_in_namespace('ci')))

        Namespace.from_db('ci').hard_delete()

        self.assertEqual(
            [], claims_in_namespace('ci'),
            'a claim left behind holds cluster capacity nothing can release')
        self.assertIsNone(NamespaceClaim.from_db(
            str(c.uuid), suppress_failure_audit=True))

    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    def test_every_claim_of_a_namespace_goes_and_returns_its_capacity(
            self, _mock_delete_events):
        # A namespace can hold more than one claim: the one-active-claim
        # rule is a probe outside the transaction, so two concurrent
        # creates can both commit. A cascade which stopped at the first
        # would leak the rest.
        first = self._new(limit_cpus=4)
        second_row = self.mock_mariadb.set_namespace_claim(
            'ci', limit_cpus=2, limit_memory_mb=1024, limit_disk_gb=10,
            used_cpus=2, used_memory_mb=1024, used_disk_gb=10)
        self._row(first)['used_cpus'] = 1
        self.assertEqual(2, len(claims_in_namespace('ci')))

        mariadb.delete_namespace_claim.reset_mock()
        Namespace.from_db('ci').hard_delete()

        self.assertEqual(
            [], claims_in_namespace('ci'),
            'the cascade stopped early and leaked a claim')
        self.assertEqual(
            {str(first.uuid), second_row['uuid']},
            {call.args[0]
             for call in mariadb.delete_namespace_claim.call_args_list},
            'every claim must be deleted through the capacity returning '
            'primitive, or its claimed_* capacity leaks forever')

    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    def test_an_expired_claim_is_collected_too(self, _mock_delete_events):
        # hard_delete is the last chance, so neither coverage nor object
        # state may exempt a claim from it.
        c = self._new()
        self._row(c)['state'] = namespace_claim.COVERAGE_EXPIRED

        Namespace.from_db('ci').hard_delete()

        self.assertEqual([], claims_in_namespace('ci'))

    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    def test_an_unreadable_claim_listing_aborts_the_cascade(
            self, _mock_delete_events):
        # The cascade asks which claims to collect and then removes the
        # namespace regardless of the answer. If an unreadable database
        # answered "none", the namespace would go and the claim would
        # stay, holding cluster capacity that nothing can ever release:
        # its own state row is healthy, so orphan reconciliation never
        # looks at it, and the namespace's state row is gone, so the
        # reaper never comes back. The read has to fail loudly.
        c = self._new()

        with mock.patch('shakenfist.mariadb.get_namespace_claims',
                        side_effect=exceptions.DatabaseUnavailable('down')):
            self.assertRaises(exceptions.DatabaseUnavailable,
                              Namespace.from_db('ci').hard_delete)

        self.assertIn(str(c.uuid), self.mock_mariadb.namespace_claims)
        self.assertIsNotNone(
            Namespace.from_db('ci'),
            'the namespace must survive so the reaper retries the whole '
            'delete rather than leaving an unreleasable claim')

    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    def test_another_namespaces_claims_are_untouched(
            self, _mock_delete_events):
        Namespace.new('staging')
        self._new()
        other = self._new(namespace='staging')

        Namespace.from_db('ci').hard_delete()

        self.assertEqual([], claims_in_namespace('ci'))
        self.assertEqual([other.uuid],
                         [c.uuid for c in claims_in_namespace('staging')])


class NamespaceClaimIteratorTestCase(NamespaceClaimTestCase):
    def test_listing_a_namespace_pushes_the_filter_into_sql(self):
        Namespace.new('staging')
        mine = self._new()
        self._new(namespace='staging')

        mariadb.get_namespace_claims.reset_mock()
        listed = list(NamespaceClaims([], namespace='ci'))

        self.assertEqual([mine.uuid], [c.uuid for c in listed])

        # The restriction is the database's work, not a Python filter
        # over every claim in the cluster.
        mariadb.get_namespace_claims.assert_called_once_with('ci')

    def test_listing_without_a_namespace_returns_every_claim(self):
        Namespace.new('staging')
        mine = self._new()
        theirs = self._new(namespace='staging')

        listed = list(NamespaceClaims([]))
        self.assertEqual({mine.uuid, theirs.uuid},
                         {c.uuid for c in listed})

    def test_a_deleted_claim_is_still_listed(self):
        # The listing and the by-uuid lookup disagree here on purpose,
        # so the disagreement is pinned rather than left to look like an
        # oversight. A claim has no soft delete, so a deleted object
        # state does not mean "already accounted for" -- it means a row
        # that still holds claimed_* with a state row zombie repair
        # wrote, waiting for the reaper. The listing is the operator's
        # only view of held capacity, so it shows it; arg_is_claim_ref
        # 404s it because there is nothing useful to do to it.
        c = self._new()
        c.state = NamespaceClaim.STATE_DELETED

        self.assertEqual(
            [c.uuid], [listed.uuid for listed in NamespaceClaims(
                [], namespace='ci')],
            'a claim still holding cluster capacity vanished from the '
            'only listing an operator has')


class NamespaceClaimRefusalTestCase(NamespaceClaimTestCase):
    """A refusal is a decision, and the caller is told which one."""

    def test_a_capacity_refusal_is_raised_with_its_reason(self):
        self.mock_mariadb.refuse_namespace_claims('capacity')

        exc = self.assertRaises(ClaimRefused, self._new)
        self.assertEqual('capacity', exc.reason)

    def test_a_refused_claim_leaves_nothing_behind(self):
        # Neither a claim row nor -- more importantly -- an object state
        # row, which would be a stateless zombie for a claim that was
        # never granted (issue 3588).
        self.mock_mariadb.refuse_namespace_claims('capacity')

        self.assertRaises(ClaimRefused, self._new)
        self.assertEqual({}, self.mock_mariadb.namespace_claims)
        self.assertEqual(
            [], [k for k in self.mock_mariadb.mariadb_states
                 if k.startswith(f'{ObjectType.NAMESPACE_CLAIM}/')])

    def test_a_second_claim_for_a_namespace_is_refused_as_exists(self):
        self._new()

        exc = self.assertRaises(ClaimRefused, self._new)
        self.assertEqual('exists', exc.reason)

    def test_a_shrink_below_usage_is_refused(self):
        c = self._new()
        self._row(c)['used_cpus'] = 3

        exc = self.assertRaises(
            ClaimRefused, c.update, limit_cpus=1, fields=['limit_cpus'])
        self.assertEqual('below_usage', exc.reason)
        self.assertEqual(4, self._row(c)['limit_cpus'])

    def test_an_update_without_a_field_mask_is_refused_locally(self):
        # An unmasked update would shrink every dimension the caller did
        # not mention to zero.
        c = self._new()
        self.assertRaises(ValueError, c.update, limit_cpus=8)

    def test_a_permitted_update_is_applied(self):
        c = self._new()
        c.update(limit_cpus=8, fields=['limit_cpus'])

        self.assertEqual(8, c.limits['cpus'])
        self.assertEqual(8192, c.limits['memory_mb'])


class NamespaceClaimRegistrationTestCase(base.ShakenFistTestCase):
    """The registries a new object type has to join.

    Asserted directly rather than left to be noticed, because
    NAMESPACE_KEY was missing from _STATIC_TABLE_GETTERS from the day it
    landed: the orphan reconciler could not repair its zombie rows, and
    4,151 stateless keys sat unrepairable while the expiry sweep
    re-evented every one of them every pass -- roughly 380,000 junk
    audit events a day (issue 3588).
    """

    def test_the_orphan_reconciler_knows_the_static_table(self):
        self.assertIn(
            ObjectType.NAMESPACE_CLAIM.value, mariadb._STATIC_TABLE_GETTERS,
            'namespace_claim is missing from _STATIC_TABLE_GETTERS, so the '
            'orphan reconciler can neither remove its phantom state rows '
            'nor repair its zombie claim rows (issue 3588)')

        getter, pk = mariadb._STATIC_TABLE_GETTERS[
            ObjectType.NAMESPACE_CLAIM.value]
        self.assertIs(mariadb._get_namespace_claims_table, getter)
        self.assertEqual('uuid', pk)

        self.assertIn(ObjectType.NAMESPACE_CLAIM.value,
                      mariadb.ORPHAN_RECONCILABLE_OBJECT_TYPES)

    def test_the_object_type_resolves_to_the_class(self):
        # OBJECT_NAMES_TO_CLASSES is what the deleted object reaper and
        # the zombie repair sweep use to hydrate an object from its type
        # string. Without it a claim marked deleted is never collected.
        self.assertIn('namespace_claim', OBJECT_NAMES_TO_CLASSES)
        self.assertIs(NamespaceClaim, get_object_class('namespace_claim'))

    def test_the_proto_id_is_stable_and_unique(self):
        self.assertEqual(32, ObjectType.NAMESPACE_CLAIM.proto_id)
        self.assertIs(ObjectType.NAMESPACE_CLAIM,
                      ObjectType.from_proto_id(32))
        self.assertEqual(
            len({o.proto_id for o in ObjectType}), len(list(ObjectType)))
