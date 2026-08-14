# Copyright 2019 Michael Still and contributors
#
# Unit tests for the NamespaceKey DatabaseBackedObject added by phase 2
# of the auth federation plan. The behaviour-preservation tests for the
# old JSON column implementation live in test_namespace_keys.py and are
# deliberately left alone.
import base64
import json
from unittest import mock

import bcrypt
from pydantic import SecretStr

from shakenfist import exceptions
from shakenfist.constants import get_object_class
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.namespace_key import NamespaceKey
from shakenfist.namespace_key import NamespaceKeys
from shakenfist.schema.object_types import ObjectType
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


class NamespaceKeyTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.mock_mariadb = MockMariaDB(self, node_count=1)
        self.mock_mariadb.setup()
        self.mock_mariadb.create_namespace('banana', 'key1', 'bacon')

    def _attributes(self, key):
        return self.mock_mariadb.namespace_key_attributes[str(key.uuid)]

    def _keys_named(self, name, namespace=None):
        """The stored key objects with a given name.

        Every namespace created by the fixture owns a 'key1', so a
        global count of namespace_key_objects would conflate the
        fixture's key with the one under test.
        """
        return [d for d in self.mock_mariadb.namespace_key_objects.values()
                if d.name == name
                and (namespace is None or d.namespace == namespace)]

    def _assert_no_secret_material(self, mock_add_event, attrs):
        """Assert no event carries the stored hash or the nonce.

        The needles are unwrapped with get_secret_value() deliberately,
        and this helper exists so the reason is written down once rather
        than re-derived at each call site.

        Both of the obvious spellings are broken now that these fields
        are SecretStr, and neither breaks loudly.
        ``assertNotIn(attrs.key, haystack)`` cannot fail: SecretStr
        implements no __contains__, so the containment raises
        TypeError, and testtools' Contains matcher catches TypeError
        and reports "does not contain". That is not hypothetical --
        these two tests silently stopped testing anything the moment
        the field type changed, and only a deliberate check caught it.
        ``assertNotIn(str(attrs.key), haystack)`` is worse, because it
        asserts the literal '**********' is absent, which is true of an
        event which leaked the real secret.

        ShakenFistTestCase now refuses a SecretStr operand outright, so
        the first spelling raises rather than passing. This helper
        stays because unwrapping in one place beats unwrapping at every
        call site.

        See docs/plans/PLAN-auth-federation-phase-06-secret-types.md,
        Decision 7. test_the_secret_guard_detects_a_real_leak() proves
        this helper still fails when a secret genuinely escapes.
        """
        for call in mock_add_event.call_args_list:
            self.assertNotIn(attrs.key.get_secret_value(), str(call))
            self.assertNotIn(attrs.nonce.get_secret_value(), str(call))


class NamespaceKeyCreationTestCase(NamespaceKeyTestCase):
    def test_new_writes_both_rows_and_is_created(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')

        self.assertEqual('banana', k.namespace)
        self.assertEqual('deploy', k.name)
        self.assertEqual(NamespaceKey.STATE_CREATED, k.state.value)

        # Both the static and the attributes row exist.
        self.assertIn(str(k.uuid), self.mock_mariadb.namespace_key_objects)
        self.assertIn(str(k.uuid), self.mock_mariadb.namespace_key_attributes)

    def test_new_hashes_exactly_as_add_key_did(self):
        # The stored value must be a base64 encoded bcrypt hash which
        # validates against the plaintext, because tokens minted before
        # this phase were checked against hashes built this way.
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')

        stored = self._attributes(k).key.get_secret_value()
        self.assertTrue(bcrypt.checkpw(
            'sekrit'.encode('utf-8'), base64.b64decode(stored)))
        self.assertFalse(bcrypt.checkpw(
            'wrong'.encode('utf-8'), base64.b64decode(stored)))

    def test_new_generates_a_nonce(self):
        with mock.patch('shakenfist.namespace_key.sfrandom.random_id',
                        return_value='noncenonce') as mock_random_id:
            k = NamespaceKey.new('banana', 'deploy', 'sekrit')

        mock_random_id.assert_called_once_with()
        self.assertEqual('noncenonce', k.nonce.get_secret_value())

    def test_new_stores_expiry_scopes_and_provenance(self):
        k = NamespaceKey.new(
            'banana', 'deploy', 'sekrit', expiry=2000.0,
            scopes=['read'], provenance={'rule': 'r1'})

        self.assertEqual(2000.0, k.expiry)
        self.assertEqual(['read'], k.scopes)
        self.assertEqual({'rule': 'r1'}, k.provenance)

    def test_new_defaults_to_no_expiry_scopes_or_provenance(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        self.assertIsNone(k.expiry)
        self.assertIsNone(k.scopes)
        self.assertIsNone(k.provenance)
        self.assertFalse(k.expired())

    def test_new_with_an_existing_name_rotates_in_place(self):
        # Namespace.add_key() has always overwritten a key of the same
        # name rather than erroring, and that must keep working.
        first = NamespaceKey.new('banana', 'deploy', 'sekrit', expiry=2000.0)
        original_hash = self._attributes(first).key.get_secret_value()
        original_nonce = self._attributes(first).nonce.get_secret_value()

        second = NamespaceKey.new('banana', 'deploy', 'different')

        # Same object, new secret material...
        self.assertEqual(first.uuid, second.uuid)
        self.assertEqual(1, len(self._keys_named('deploy', 'banana')))
        self.assertNotEqual(
            original_hash, self._attributes(second).key.get_secret_value())
        self.assertNotEqual(
            original_nonce,
            self._attributes(second).nonce.get_secret_value())
        self.assertTrue(bcrypt.checkpw(
            'different'.encode('utf-8'),
            base64.b64decode(
                self._attributes(second).key.get_secret_value())))

        # ...and the whole attribute set is replaced, so the expiry the
        # first call set is gone, exactly as add_key() behaved.
        self.assertIsNone(second.expiry)

    def test_losing_the_unique_index_race_becomes_a_rotation(self):
        # Two writers can insert the same (namespace, name) at once now
        # that there is no whole-blob lock; the unique index is the
        # arbiter. The loser must not leave an orphaned attributes row
        # behind, and must still end up having rotated the key.
        winner = NamespaceKey.new('banana', 'deploy', 'sekrit')
        attributes_rows = len(self.mock_mariadb.namespace_key_attributes)

        with mock.patch.object(NamespaceKey, 'from_db_by_name',
                               side_effect=[None, winner]):
            loser = NamespaceKey.new('banana', 'deploy', 'different')

        self.assertEqual(winner.uuid, loser.uuid)
        self.assertEqual(1, len(self._keys_named('deploy', 'banana')))
        self.assertEqual(
            attributes_rows, len(self.mock_mariadb.namespace_key_attributes))
        self.assertTrue(bcrypt.checkpw(
            'different'.encode('utf-8'),
            base64.b64decode(
                self._attributes(loser).key.get_secret_value())))

    def test_the_same_name_in_another_namespace_is_a_different_key(self):
        self.mock_mariadb.create_namespace('apple', 'key1', 'bacon')
        one = NamespaceKey.new('banana', 'deploy', 'sekrit')
        two = NamespaceKey.new('apple', 'deploy', 'sekrit')

        self.assertNotEqual(one.uuid, two.uuid)
        self.assertEqual(2, len(self._keys_named('deploy')))
        self.assertEqual(1, len(self._keys_named('deploy', 'banana')))
        self.assertEqual(1, len(self._keys_named('deploy', 'apple')))

    def test_creation_events_carry_no_secret_material(self):
        with mock.patch('shakenfist.eventlog.add_event') as mock_add_event:
            k = NamespaceKey.new('banana', 'deploy', 'sekrit')

        attrs = self._attributes(k)
        self.assertNotEqual(0, len(mock_add_event.call_args_list))
        self._assert_no_secret_material(mock_add_event, attrs)

    def test_the_secret_guard_detects_a_real_leak(self):
        """Prove the guard above is capable of failing.

        A test which cannot fail is not a test, and the two guards in
        this file were exactly that for the length of one edit -- see
        _assert_no_secret_material(). This deliberately leaks the hash
        and then the nonce into an event and asserts the helper objects
        to each, so that any future repair which makes the guards
        vacuous fails here instead of passing quietly.

        The expected exception is self.failureException (testtools'
        MismatchError) rather than a bare Exception, because Exception
        is satisfied by the guard *breaking* as well as by the guard
        firing: an AttributeError from a renamed field, or a typo in the
        helper, would otherwise read as proof that the guard still
        works. A test which proves another test can fail should not
        itself be able to pass for the wrong reason.
        """
        with mock.patch('shakenfist.eventlog.add_event'):
            k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        attrs = self._attributes(k)

        for leaked in [attrs.key.get_secret_value(),
                       attrs.nonce.get_secret_value()]:
            leaky = mock.MagicMock()
            leaky.call_args_list = [
                mock.call('objtype', 'uuid', 'audit', 'leaked',
                          extra={'oops': leaked})]
            self.assertRaises(
                self.failureException, self._assert_no_secret_material,
                leaky, attrs)

    def test_a_masked_secret_is_not_mistaken_for_absence(self):
        """The other half of Decision 7.

        An event carrying the rendered form of a SecretStr contains
        '**********' and not the secret, so the guard must pass on it.
        This pins that the guard tests the real value rather than the
        mask -- if it were rewritten to compare str(attrs.key), this
        event would look like a leak and the test would fail.
        """
        with mock.patch('shakenfist.eventlog.add_event'):
            k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        attrs = self._attributes(k)

        masked = mock.MagicMock()
        masked.call_args_list = [
            mock.call('objtype', 'uuid', 'audit', 'masked',
                      extra={'key': str(attrs.key),
                             'nonce': str(attrs.nonce)})]
        self._assert_no_secret_material(masked, attrs)


class NamespaceKeyLookupTestCase(NamespaceKeyTestCase):
    def test_from_db_by_name(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        found = NamespaceKey.from_db_by_name('banana', 'deploy')

        self.assertIsNotNone(found)
        self.assertEqual(k.uuid, found.uuid)
        self.assertEqual('deploy', found.name)

    def test_from_db_by_name_is_namespace_scoped(self):
        NamespaceKey.new('banana', 'deploy', 'sekrit')
        self.assertIsNone(NamespaceKey.from_db_by_name('apple', 'deploy'))
        self.assertIsNone(NamespaceKey.from_db_by_name('banana', 'nosuch'))

    def test_from_db_by_name_uses_a_single_point_read(self):
        # Token validation does this once per request, so it must not
        # degrade into a listing plus a filter.
        NamespaceKey.new('banana', 'deploy', 'sekrit')

        with mock.patch(
                'shakenfist.mariadb.get_namespace_key_by_name',
                side_effect=self.mock_mariadb._mariadb_get_namespace_key_by_name
        ) as mock_get:
            with mock.patch('shakenfist.mariadb.find_namespace_keys') as find:
                NamespaceKey.from_db_by_name('banana', 'deploy')

        mock_get.assert_called_once_with('banana', 'deploy')
        find.assert_not_called()


class NamespaceKeyRotationTestCase(NamespaceKeyTestCase):
    def test_rotate_changes_both_hash_and_nonce(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        original_hash = self._attributes(k).key.get_secret_value()
        original_nonce = self._attributes(k).nonce.get_secret_value()

        returned_nonce = k.rotate('newsekrit')

        self.assertNotEqual(
            original_hash, self._attributes(k).key.get_secret_value())
        self.assertNotEqual(
            original_nonce, self._attributes(k).nonce.get_secret_value())
        # rotate() returns a SecretStr, matching what it stored. Compared
        # unwrapped so a mismatch reports the values rather than two
        # identical rows of asterisks.
        self.assertIsInstance(returned_nonce, SecretStr)
        self.assertEqual(
            self._attributes(k).nonce.get_secret_value(),
            returned_nonce.get_secret_value())
        self.assertTrue(bcrypt.checkpw(
            'newsekrit'.encode('utf-8'),
            base64.b64decode(
                self._attributes(k).key.get_secret_value())))

    def test_rotate_keeps_the_object_identity_and_state(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        original_uuid = k.uuid
        k.rotate('newsekrit')

        self.assertEqual(original_uuid, k.uuid)
        self.assertEqual(NamespaceKey.STATE_CREATED, k.state.value)

    def test_rotate_events_carry_no_secret_material(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')

        with mock.patch('shakenfist.eventlog.add_event') as mock_add_event:
            k.rotate('newsekrit')

        attrs = self._attributes(k)
        self.assertNotEqual(0, len(mock_add_event.call_args_list))
        self._assert_no_secret_material(mock_add_event, attrs)


class NamespaceKeyExpiryTestCase(NamespaceKeyTestCase):
    def test_expired_is_check_at_use(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit', expiry=2000.0)

        self.assertFalse(k.expired(now=1000.0))
        self.assertTrue(k.expired(now=3000.0))

        # The key is still there -- nothing removed it at expiry time.
        self.assertIn(str(k.uuid), self.mock_mariadb.namespace_key_objects)

    def test_a_key_without_an_expiry_never_expires(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        self.assertFalse(k.expired(now=1e12))


class NamespaceKeyExternalViewTestCase(NamespaceKeyTestCase):
    def test_external_view_never_exposes_secrets(self):
        k = NamespaceKey.new(
            'banana', 'deploy', 'sekrit', expiry=2000.0, scopes=['read'])
        attrs = self._attributes(k)

        view = k.external_view()

        self.assertNotIn('key', view)
        self.assertNotIn('nonce', view)
        serialised = json.dumps(view, default=str)
        # Unwrapped needles, for the reasons on
        # _assert_no_secret_material(): a SecretStr needle would make
        # both of these pass whatever the view contained.
        self.assertNotIn(attrs.key.get_secret_value(), serialised)
        self.assertNotIn(attrs.nonce.get_secret_value(), serialised)

    def test_external_view_exposes_the_operator_visible_fields(self):
        k = NamespaceKey.new(
            'banana', 'deploy', 'sekrit', expiry=2000.0, scopes=['read'],
            provenance={'rule': 'r1'})

        view = k.external_view()

        self.assertEqual('banana', view['namespace'])
        self.assertEqual('deploy', view['name'])
        self.assertEqual(2000.0, view['expiry'])
        self.assertEqual(['read'], view['scopes'])
        self.assertEqual({'rule': 'r1'}, view['provenance'])
        self.assertEqual(str(k.uuid), str(view['uuid']))
        self.assertEqual(NamespaceKey.STATE_CREATED, view['state'])


class NamespaceKeyStateTestCase(NamespaceKeyTestCase):
    def test_delete_is_a_soft_delete(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        k.delete()

        self.assertEqual(NamespaceKey.STATE_DELETED, k.state.value)

        # Soft delete leaves both rows behind for the reaper.
        self.assertIn(str(k.uuid), self.mock_mariadb.namespace_key_objects)
        self.assertIn(str(k.uuid), self.mock_mariadb.namespace_key_attributes)

    def test_deleted_keys_do_not_undelete(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        k.delete()

        self.assertRaises(
            exceptions.InvalidStateException,
            setattr, k, 'state', NamespaceKey.STATE_CREATED)

    def test_illegal_transition_is_rejected(self):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')

        # created -> initial is not in state_targets.
        self.assertRaises(
            exceptions.InvalidStateException,
            setattr, k, 'state', NamespaceKey.STATE_INITIAL)

        # ... and neither is an error state; key operations are atomic.
        self.assertRaises(
            exceptions.InvalidStateException,
            setattr, k, 'state', NamespaceKey.STATE_ERROR)

    @mock.patch('shakenfist.mariadb.delete_object_events', return_value=None)
    def test_hard_delete_removes_both_rows(self, mock_delete_events):
        k = NamespaceKey.new('banana', 'deploy', 'sekrit')
        k.delete()
        k.hard_delete()

        self.assertNotIn(str(k.uuid), self.mock_mariadb.namespace_key_objects)
        self.assertNotIn(str(k.uuid), self.mock_mariadb.namespace_key_attributes)
        self.assertIsNone(NamespaceKey.from_db_by_name('banana', 'deploy'))

        # And the state row went with them, so the reaper does not keep
        # rediscovering the key.
        self.assertIsNone(
            self.mock_mariadb.mariadb_states.get(
                f'{ObjectType.NAMESPACE_KEY}/{k.uuid}'))


class NamespaceKeysIteratorTestCase(NamespaceKeyTestCase):
    def test_find_pushes_the_namespace_down_to_sql(self):
        NamespaceKey.new('banana', 'deploy', 'sekrit')

        with mock.patch(
                'shakenfist.mariadb.find_namespace_keys',
                side_effect=self.mock_mariadb._mariadb_find_namespace_keys
        ) as mock_find:
            with mock.patch(
                    'shakenfist.mariadb.get_objects_by_state') as by_state:
                names = sorted(
                    k.name for k in NamespaceKeys(namespace='banana'))

        # 'key1' is the fixture's key; the point is that no other
        # namespace's keys appear.
        self.assertEqual(['deploy', 'key1'], names)
        mock_find.assert_called_once_with('banana', include_expired=True)
        by_state.assert_not_called()

    def test_the_system_namespace_is_not_a_wildcard(self):
        # Most iterators treat namespace='system' as "everything", but
        # the system namespace owns keys of its own.
        self.mock_mariadb.create_namespace('system', 'key1', 'bacon')
        NamespaceKey.new('banana', 'deploy', 'sekrit')
        NamespaceKey.new('system', '_service_key_abcde', 'sekrit')

        names = sorted(k.name for k in NamespaceKeys(namespace='system'))

        # Only system's own keys -- banana's 'deploy' must not appear,
        # which it would if 'system' were treated as a wildcard.
        self.assertEqual(['_service_key_abcde', 'key1'], names)

    def test_expired_keys_can_be_filtered_in_sql(self):
        # An expiry of 1000.0 is comfortably in the past, so the key is
        # expired without having to fake the clock in two places.
        NamespaceKey.new('banana', 'forever', 'sekrit')
        NamespaceKey.new('banana', 'expired', 'sekrit', expiry=1000.0)

        everything = sorted(
            k.name for k in NamespaceKeys(namespace='banana'))
        current = sorted(
            k.name for k in NamespaceKeys(
                namespace='banana', include_expired=False))

        # 'key1' is the fixture's key and never expires, so it survives
        # both listings; 'expired' only survives the unfiltered one.
        self.assertEqual(['expired', 'forever', 'key1'], everything)
        self.assertEqual(['forever', 'key1'], current)

    def test_deleted_keys_are_filtered_by_state(self):
        NamespaceKey.new('banana', 'live', 'sekrit')
        doomed = NamespaceKey.new('banana', 'doomed', 'sekrit')
        doomed.delete()

        active = sorted(k.name for k in NamespaceKeys(namespace='banana'))
        self.assertEqual(['key1', 'live'], active)

        deleted = sorted(k.name for k in NamespaceKeys(
            namespace='banana', prefilter='deleted'))
        self.assertEqual(['doomed'], deleted)


class NamespaceKeyRegistrationTestCase(base.ShakenFistTestCase):
    def test_registered_for_the_standard_reaper(self):
        # per_deleted_object_checks() walks OBJECT_NAMES_TO_CLASSES to
        # find soft deleted objects to hard delete.
        self.assertIn(ObjectType.NAMESPACE_KEY, OBJECT_NAMES_TO_CLASSES)
        self.assertEqual(
            NamespaceKey, get_object_class(ObjectType.NAMESPACE_KEY))

    def test_object_type_proto_id_is_stable(self):
        self.assertEqual(29, ObjectType.NAMESPACE_KEY.proto_id)
        self.assertEqual(
            ObjectType.NAMESPACE_KEY, ObjectType.from_proto_id(29))
