import mock
import testtools

from shakenfist.baseobject import DatabaseBackedObject, State
from shakenfist import exceptions
from shakenfist.tests import base


class DatabaseBackedObjectTestCase(base.ShakenFistTestCase):
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                side_effect=[
                    {'value': None, 'update_time': 2},
                    {'value': DatabaseBackedObject.STATE_INITIAL, 'update_time': 4},
                    {'value': DatabaseBackedObject.STATE_CREATED, 'update_time': 10},
                ])
    def test_state(self, mock_get_attribute):
        d = DatabaseBackedObject('uuid')
        self.assertEqual(d.state, State(None, 2))
        self.assertEqual(d.state, State(DatabaseBackedObject.STATE_INITIAL, 4))
        self.assertEqual(d.state, State(
            DatabaseBackedObject.STATE_CREATED, 10))

    def test_property_state_object_full(self):
        s = State('state1', 3)

        self.assertEqual(s.value, 'state1')
        self.assertEqual(s.update_time, 3)

        self.assertEqual(s.obj_dict(), {
            'value': 'state1',
            'update_time': 3,
        })

        self.assertEqual(s, State('state1', 3))
        self.assertEqual(str(s),
                         "State({'value': 'state1', 'update_time': 3})")

    @mock.patch('shakenfist.eventlog.add_event')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                side_effect=[
                    None,
                    {'message': 'bad error'},
                    {'value': DatabaseBackedObject.STATE_INITIAL, 'update_time': 4},
                    {'value': DatabaseBackedObject.STATE_ERROR, 'update_time': 4},
                    {'message': 'real bad'},
                ])
    def test_property_error_msg(self, mock_get_attribute, mock_set_attribute,
                                mock_add_event):
        d = DatabaseBackedObject('uuid')
        self.assertEqual(d.error, None)
        self.assertEqual(d.error, 'bad error')

        with testtools.ExpectedException(exceptions.InvalidStateException):
            d.error = 'real bad'

        d.error = 'real bad'
