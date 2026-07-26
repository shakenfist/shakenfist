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

        stored = self._attributes(k).key
        self.assertTrue(bcrypt.checkpw(
            'sekrit'.encode('utf-8'), base64.b64decode(stored)))
        self.assertFalse(bcrypt.checkpw(
            'wrong'.encode('utf-8'), base64.b64decode(stored)))

    def test_new_generates_a_nonce(self):
        with mock.patch('shakenfist.namespace_key.sfrandom.random_id',
                        return_value='noncenonce') as mock_random_id:
            k = NamespaceKey.new('banana', 'deploy', 'sekrit')

        mock_random_id.assert_called_once_with()
        self.assertEqual('noncenonce', k.nonce)

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
        original_hash = self._attributes(first).key
        original_nonce = self._attributes(first).nonce

        second = NamespaceKey.new('banana', 'deploy', 'different')

        # Same object, new secret material...
        self.assertEqual(first.uuid, second.uuid)
        self.assertEqual(1, len(self.mock_mariadb.namespace_key_objects))
        self.assertNotEqual(original_hash, self._attributes(second).key)
        self.assertNotEqual(original_nonce, self._attributes(second).nonce)
        self.assertTrue(bcrypt.checkpw(
            'different'.encode('utf-8'),
            base64.b64decode(self._attributes(second).key)))

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
        self.assertEqual(1, len(self.mock_mariadb.namespace_key_objects))
        self.assertEqual(
            attributes_rows, len(self.mock_mariadb.namespace_key_attributes))
        self.assertTrue(bcrypt.checkpw(
            'different'.encode('utf-8'),
            base64.b64decode(self._attributes(loser).key)))

    def test_the_same_name_in_another_namespace_is_a_different_key(self):
        self.mock_mariadb.create_namespace('apple', 'key1', 'bacon')
        one = NamespaceKey.new('banana', 'deploy', 'sekrit')
        two = NamespaceKey.new('apple', 'deploy', 'sekrit')

        self.assertNotEqual(one.uuid, two.uuid)
        self.assertEqual(2, len(self.mock_mariadb.namespace_key_objects))

    def test_creation_events_carry_no_secret_material(self):
        with mock.patch('shakenfist.eventlog.add_event') as mock_add_event:
            k = NamespaceKey.new('banana', 'deploy', 'sekrit')

        attrs = self._attributes(k)
        self.assertNotEqual(0, len(mock_add_event.call_args_list))
        for call in mock_add_event.call_args_list:
            self.assertNotIn(attrs.key, str(call))
            self.assertNotIn(attrs.nonce, str(call))


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
        original_hash = self._attributes(k).key
        original_nonce = self._attributes(k).nonce

        returned_nonce = k.rotate('newsekrit')

        self.assertNotEqual(original_hash, self._attributes(k).key)
        self.assertNotEqual(original_nonce, self._attributes(k).nonce)
        self.assertEqual(self._attributes(k).nonce, returned_nonce)
        self.assertTrue(bcrypt.checkpw(
            'newsekrit'.encode('utf-8'),
            base64.b64decode(self._attributes(k).key)))

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
        for call in mock_add_event.call_args_list:
            self.assertNotIn(attrs.key, str(call))
            self.assertNotIn(attrs.nonce, str(call))


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
        self.assertNotIn(attrs.key, serialised)
        self.assertNotIn(attrs.nonce, serialised)

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
                names = [k.name for k in NamespaceKeys(namespace='banana')]

        self.assertEqual(['deploy'], names)
        mock_find.assert_called_once_with('banana', include_expired=True)
        by_state.assert_not_called()

    def test_the_system_namespace_is_not_a_wildcard(self):
        # Most iterators treat namespace='system' as "everything", but
        # the system namespace owns keys of its own.
        self.mock_mariadb.create_namespace('system', 'key1', 'bacon')
        NamespaceKey.new('banana', 'deploy', 'sekrit')
        NamespaceKey.new('system', '_service_key_abcde', 'sekrit')

        names = [k.name for k in NamespaceKeys(namespace='system')]
        self.assertEqual(['_service_key_abcde'], names)

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

        self.assertEqual(['expired', 'forever'], everything)
        self.assertEqual(['forever'], current)

    def test_deleted_keys_are_filtered_by_state(self):
        NamespaceKey.new('banana', 'live', 'sekrit')
        doomed = NamespaceKey.new('banana', 'doomed', 'sekrit')
        doomed.delete()

        active = [k.name for k in NamespaceKeys(namespace='banana')]
        self.assertEqual(['live'], active)

        deleted = [k.name for k in NamespaceKeys(
            namespace='banana', prefilter='deleted')]
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
