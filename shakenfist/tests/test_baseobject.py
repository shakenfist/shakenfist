import mock

from shakenfist.baseobject import DatabaseBackedObject, State
from shakenfist.tests import test_shakenfist


class DatabaseBackedObjectTestCase(test_shakenfist.ShakenFistTestCase):
    @mock.patch('shakenfist.baseobject.DatabaseBackedObject._db_get_attribute',
                side_effect=[
                    {'state': None, 'update_time': 2},
                    {'state': 'initial', 'update_time': 4},
                    {'state': 'created', 'update_time': 10},
                ])
    def test_state_property(self, mock_get_attribute):
        d = DatabaseBackedObject('uuid')
        self.assertEqual(d.state, State(None, 2))
        self.assertEqual(d.state, State('initial', 4))
        self.assertEqual(d.state, State('created', 10))


class StateTestCase(test_shakenfist.ShakenFistTestCase):
    def test_state_object_full(self):
        s = State('state1', 3, 'error msg')

        self.assertEqual(s.state, 'state1')
        self.assertEqual(s.update_time, 3)
        self.assertEqual(s.error_msg, 'error msg')

        self.assertEqual(s.json_dump(), {
            'state': 'state1',
            'update_time': 3,
            'error_msg': 'error msg',
        })

        self.assertEqual(s, State('state1', 3, 'error msg'))

        self.assertEqual(str(s),
                         "State({'state': 'state1', 'update_time': 3, "
                         "'error_msg': 'error msg'})")

    def _test_state_object_def(self):
        s = State('state1', 4)

        self.assertEqual(s.state, 'state1')
        self.assertEqual(s.update_time, 4)
        self.assertEqual(s.error_msg, None)
