import mock

from shakenfist.configuration import config
from shakenfist import exceptions
from shakenfist.tests import test_shakenfist


class ConfigTestCase(test_shakenfist.ShakenFistTestCase):
    @mock.patch('socket.getfqdn', return_value='a.b.com')
    @mock.patch('socket.gethostbyname', return_value='1.1.1.1')
    def test_hostname(self, mock_hostname, mock_fqdn):
        config.parse()

        mock_fqdn.assert_called()
        mock_hostname.assert_called()

        self.assertEqual('a.b.com', config.get('NODE_NAME'))
        self.assertEqual('1.1.1.1', config.get('NODE_IP'))

    @mock.patch.dict('os.environ', {'SHAKENFIST_STORAGE_PATH': 'foo'})
    def test_string_override(self):
        config.parse()
        self.assertTrue(isinstance(config.get('STORAGE_PATH'), str))
        self.assertEqual('foo', config.get('STORAGE_PATH'))

    @mock.patch.dict('os.environ', {'SHAKENFIST_CPU_OVERCOMMIT_RATIO': '1'})
    def test_int_override(self):
        config.parse()
        self.assertTrue(isinstance(config.get('CPU_OVERCOMMIT_RATIO'), int))
        self.assertEqual(1, config.get('CPU_OVERCOMMIT_RATIO'))

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_RAM_SYSTEM_RESERVATION': '4.0'})
    def test_float_override(self):
        config.parse()
        self.assertTrue(
            isinstance(config.get('RAM_SYSTEM_RESERVATION'), float))
        self.assertEqual(4.0, config.get('RAM_SYSTEM_RESERVATION'))

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_RAM_SYSTEM_RESERVATION': 'banana'})
    def test_bogus_override(self):
        self.assertRaises(ValueError, config.parse)

    @mock.patch.dict('shakenfist.configuration.CONFIG_DEFAULTS',
                     {'FOO': [1, 2, 3]})
    @mock.patch.dict('os.environ', {'SHAKENFIST_FOO': '[1, 4, 6]'})
    def test_bogus_default(self):
        self.assertRaises(exceptions.FlagException, config.parse)
