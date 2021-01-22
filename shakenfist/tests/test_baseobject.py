import mock
import testtools

from shakenfist.baseobject import DatabaseBackedObject, State
from shakenfist import exceptions
from shakenfist.tests import test_shakenfist


class DatabaseBackedObjectTestCase(test_shakenfist.ShakenFistTestCase):
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                side_effect=[
                    {'value': None, 'update_time': 2},
                    {'value': 'initial', 'update_time': 4},
                    {'value': 'created', 'update_time': 10},
                ])
    def test_state(self, mock_get_attribute):
        d = DatabaseBackedObject('uuid')
        self.assertEqual(d.state, State(None, 2))
        self.assertEqual(d.state, State('initial', 4))
        self.assertEqual(d.state, State('created', 10))

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

    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_set_attribute')
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                side_effect=[
                    None,
                    {'message': 'bad error'},
                    {'value': 'initial', 'update_time': 4},
                    {'value': 'error', 'update_time': 4},
                    {'message': 'real bad'},
                ])
    def test_property_error_msg(self, mock_get_attribute, mock_set_attribute):
        d = DatabaseBackedObject('uuid')
        self.assertEqual(d.error, None)
        self.assertEqual(d.error, 'bad error')

        with testtools.ExpectedException(exceptions.InvalidStateException):
            d.error = 'real bad'

        d.error = 'real bad'
