from unittest import mock

import testtools
from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject
from shakenfist.baseobject import State
from shakenfist.tests import base


class DatabaseBackedObjectTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.mariadb.get_state',
                side_effect=[
                    State(value=None, update_time=2),
                    State(value=DatabaseBackedObject.STATE_INITIAL, update_time=4),
                    State(value=DatabaseBackedObject.STATE_CREATED, update_time=10),
                ])
    def test_state(self, mock_mariadb_get_state):
        d = DatabaseBackedObject('uuid')
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
        d = DatabaseBackedObject('uuid')
        self.assertEqual(d.error, None)
        self.assertEqual(d.error, 'bad error')

        with testtools.ExpectedException(exceptions.InvalidStateException):
            d.error = 'real bad'

        d.error = 'real bad'
