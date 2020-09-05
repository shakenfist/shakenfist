import mock
import testtools
import time


from shakenfist import db


class DBTestCase(testtools.TestCase):
    def setUp(self):
        super(DBTestCase, self).setUp()

    @mock.patch('shakenfist.etcd.put')
    @mock.patch('shakenfist.db.allocate_console_port', side_effect=[1, 2])
    def test_create_instance(self, mock_console_allocate, mock_put):
        db.create_instance('uuid42', 'barry', 1, 2048, 'disks',
                           'sshkey', 'userdata', 'namespace',
                           {'memory': 16384, 'model': 'cirrus'}, None)

        etcd_write = mock_put.mock_calls[0][1]
        del etcd_write[3]['node']
        del etcd_write[3]['state_updated']

        self.assertEqual(
            ('instance', None, 'uuid42',
             {
                 'uuid': 'uuid42',
                 'name': 'barry',
                 'cpus': 1,
                 'memory': 2048,
                 'disk_spec': 'disks',
                 'ssh_key': 'sshkey',
                 'console_port': 1,
                 'vdi_port': 2,
                 'user_data': 'userdata',
                 'block_devices': None,
                 'state': 'initial',
                 'namespace': 'namespace',
                 'power_state': 'initial',
                 'video': {'memory': 16384, 'model': 'cirrus'},
                 'node_history': [],
                 'error_message': None,
                 'requested_placement': None,
                 'placement_attempts': 0,
             }),
            etcd_write)

    @mock.patch('shakenfist.etcd.get',
                return_value={'uuid': 'uuid42', 'state': 'initial'})
    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_state(self, mock_put, mock_get):
        db.update_instance_state('uuid42', 'created')
        mock_get.assert_called()

        etcd_write = mock_put.mock_calls[0][1]
        self.assertEqual(('instance', None, 'uuid42'), etcd_write[0:3])
        self.assertTrue(time.time() - etcd_write[3]['state_updated'] < 3)
        del etcd_write[3]['state_updated']
        self.assertEqual(
            {
                'state': 'created',
                'uuid': 'uuid42',
                'video': {'memory': 16384, 'model': 'cirrus'},
                'error_message': None,
            },
            etcd_write[3])

    @mock.patch('shakenfist.etcd.get',
                return_value={'uuid': 'uuid42', 'state': 'created'})
    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_state_duplicate(self, mock_put, mock_get):
        db.update_instance_state('uuid42', 'created')
        mock_get.assert_called()
        mock_put.assert_not_called()

    @mock.patch('shakenfist.etcd.get',
                return_value={'uuid': 'uuid42', 'power_state': 'on'})
    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_power_state(self, mock_put, mock_get):
        db.update_instance_power_state('uuid42', 'off')
        mock_get.assert_called()

        etcd_write = mock_put.mock_calls[0][1]
        self.assertEqual(('instance', None, 'uuid42'), etcd_write[0:3])
        self.assertTrue(time.time() - etcd_write[3]['power_state_updated'] < 3)
        del etcd_write[3]['power_state_updated']
        self.assertEqual(
            {
                'power_state': 'off',
                'power_state_previous': 'on',
                'uuid': 'uuid42',
                'video': {'memory': 16384, 'model': 'cirrus'},
                'error_message': None,
            },
            etcd_write[3])

    @mock.patch('shakenfist.etcd.get',
                return_value={'uuid': 'uuid42', 'power_state': 'on'})
    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_power_state_duplicate(self, mock_put, mock_get):
        db.update_instance_power_state('uuid42', 'on')
        mock_get.assert_called()
        mock_put.assert_not_called()

    @mock.patch('shakenfist.etcd.get',
                return_value={
                    'uuid': 'uuid42',
                    'power_state_previous': 'on',
                    'power_state': 'transition-to-off',
                    'power_state_updated': time.time()
                })
    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_power_state_transition_new(self, mock_put, mock_get):
        db.update_instance_power_state('uuid42', 'on')
        mock_get.assert_called()
        mock_put.assert_not_called()

    @mock.patch('shakenfist.etcd.get',
                return_value={
                    'uuid': 'uuid42',
                    'power_state_previous': 'on',
                    'power_state': 'transition-to-off',
                    'power_state_updated': time.time() - 71
                })
    @mock.patch('shakenfist.etcd.put')
    def test_update_instance_power_state_transition_old(self, mock_put, mock_get):
        db.update_instance_power_state('uuid42', 'on')
        mock_get.assert_called()

        etcd_write = mock_put.mock_calls[0][1]
        self.assertEqual(('instance', None, 'uuid42'), etcd_write[0:3])
        self.assertTrue(time.time() - etcd_write[3]['power_state_updated'] < 3)
        del etcd_write[3]['power_state_updated']
        self.assertEqual(
            {
                'power_state': 'on',
                'power_state_previous': 'transition-to-off',
                'uuid': 'uuid42',
                'video': {'memory': 16384, 'model': 'cirrus'},
                'error_message': None,
            },
            etcd_write[3])
