import time
from unittest import mock

import testtools
from shakenfist import baseobject
from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.baseobject import State
from shakenfist.tests import base


class MaintainVersionCacheTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.mariadb.get_node')
    @mock.patch('shakenfist.mariadb.get_state',
                return_value=State(value=DatabaseBackedObject.STATE_CREATED,
                                   update_time=10))
    @mock.patch('shakenfist.mariadb.get_all_node_metrics')
    def test_refresh_does_not_fetch_node_objects(
            self, mock_all_metrics, mock_state, mock_get_node):
        # The version cache is fed from node metrics (which already carry
        # fqdn); it must not fan out a per-node get_node any more.
        mock_all_metrics.return_value = [{
            'node_uuid': '12345678-1234-4321-8234-123456789012',
            'fqdn': 'sf-1',
            'timestamp': time.time(),
            'metrics': {'object_version_node': 3},
        }]
        # Force a refresh rather than relying on the TTL.
        baseobject.VERSION_CACHE_MINIMUM = None
        baseobject.VERSION_CACHE_MAXIMUM = None
        baseobject.VERSION_CACHE_AGE = 0

        baseobject._maintain_version_cache(0)

        mock_get_node.assert_not_called()
        mock_all_metrics.assert_called_once()


class DatabaseBackedObjectTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.mariadb.get_state',
                side_effect=[
                    State(value=None, update_time=2),
                    State(value=DatabaseBackedObject.STATE_INITIAL, update_time=4),
                    State(value=DatabaseBackedObject.STATE_CREATED, update_time=10),
                ])
    def test_state(self, mock_mariadb_get_state):
        d = DatabaseBackedObject('12345678-1234-4321-8234-123456789012')
        self.assertEqual(d.state, State(value=None, update_time=2))
        self.assertEqual(d.state,
                         State(value=DatabaseBackedObject.STATE_INITIAL,
                               update_time=4))
        self.assertEqual(d.state,
                         State(value=DatabaseBackedObject.STATE_CREATED,
                               update_time=10))

    def test_property_state_object_full(self):
        s = State(value='state1', update_time=3.0)

        self.assertEqual(s.value, 'state1')
        self.assertEqual(s.update_time, 3.0)

        self.assertEqual(s.obj_dict(), {
            'value': 'state1',
            'update_time': 3.0,
        })

        self.assertEqual(s, State(value='state1', update_time=3.0))
        self.assertEqual(str(s),
                         "State({'value': 'state1', 'update_time': 3.0})")

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.mariadb.get_state',
                side_effect=[
                    State(value=DatabaseBackedObject.STATE_INITIAL, update_time=4),
                    State(value=DatabaseBackedObject.STATE_ERROR, update_time=4),
                ])
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                side_effect=[
                    {},
                    {'message': 'bad error'},
                    {'message': 'real bad'},
                ])
    def test_property_error_msg(self, mock_get_attribute, mock_mariadb_get_state,
                                mock_set_attribute, mock_add_event):
        d = DatabaseBackedObject('12345678-1234-4321-8234-123456789012')
        self.assertEqual(d.error, None)
        self.assertEqual(d.error, 'bad error')

        with testtools.ExpectedException(exceptions.InvalidStateException):
            d.error = 'real bad'

        d.error = 'real bad'


class InMemoryStateTestObject(DatabaseBackedObject):
    state_targets = {
        None: (DatabaseBackedObject.STATE_CREATED, ),
        DatabaseBackedObject.STATE_CREATED: (DatabaseBackedObject.STATE_DELETED, ),
        DatabaseBackedObject.STATE_DELETED: (),
    }


class InMemoryOnlyStateTestCase(base.ShakenFistTestCase):
    """In-memory only objects must never read or write primary state in
    MariaDB. A state row written by an in-memory object leaks forever:
    hard_delete() early-returns for in-memory objects and state-driven
    iterators skip objects with no static row, so nothing can remove it
    (issue 3532)."""

    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch('shakenfist.mariadb.get_state')
    def test_in_memory_state_never_touches_mariadb(
            self, mock_get_state, mock_set_state):
        d = InMemoryStateTestObject(
            '12345678-1234-4321-8234-123456789012', in_memory_only=True)
        self.assertIsNone(d.state.value)

        d.state = DatabaseBackedObject.STATE_CREATED
        self.assertEqual(DatabaseBackedObject.STATE_CREATED, d.state.value)

        d.state = DatabaseBackedObject.STATE_DELETED
        self.assertEqual(DatabaseBackedObject.STATE_DELETED, d.state.value)

        mock_get_state.assert_not_called()
        mock_set_state.assert_not_called()

    @mock.patch('shakenfist.mariadb.set_state')
    @mock.patch('shakenfist.mariadb.get_state')
    def test_in_memory_state_still_validates_transitions(
            self, mock_get_state, mock_set_state):
        d = InMemoryStateTestObject(
            '12345678-1234-4321-8234-123456789012', in_memory_only=True)

        with testtools.ExpectedException(exceptions.InvalidStateException):
            d.state = DatabaseBackedObject.STATE_DELETED

        mock_get_state.assert_not_called()
        mock_set_state.assert_not_called()
