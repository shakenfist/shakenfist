import mock
import testtools

from shakenfist import config
from shakenfist import exceptions


class ConfigTestCase(testtools.TestCase):
    @mock.patch('socket.gethostbyname', return_value='1.1.1.1')
    def test_hostname(self, mock_hostname):
        conf = config.SFConfig()
        mock_hostname.assert_called()
        self.assertEqual('1.1.1.1', str(conf.get('NODE_IP')))
        self.assertEqual('1.1.1.1', str(conf.NODE_IP))

    @mock.patch.dict('os.environ', {'SHAKENFIST_STORAGE_PATH': 'foo'})
    def test_string_override(self):
        conf = config.SFConfig()
        self.assertTrue(isinstance(conf.get('STORAGE_PATH'), str))
        self.assertEqual('foo', conf.get('STORAGE_PATH'))

    @mock.patch.dict('os.environ', {'SHAKENFIST_CPU_OVERCOMMIT_RATIO': '1'})
    def test_int_override(self):
        conf = config.SFConfig()
        self.assertTrue(isinstance(conf.CPU_OVERCOMMIT_RATIO, float))
        self.assertEqual(1, conf.CPU_OVERCOMMIT_RATIO)

    @mock.patch.dict('os.environ', {'SHAKENFIST_RAM_SYSTEM_RESERVATION': '4.0'})
    def test_float_override(self):
        conf = config.SFConfig()
        self.assertTrue(isinstance(conf.RAM_SYSTEM_RESERVATION, float))
        self.assertEqual(4.0, conf.RAM_SYSTEM_RESERVATION)

    @mock.patch.dict('os.environ', {'SHAKENFIST_RAM_SYSTEM_RESERVATION': 'banana'})
    def test_bogus_override(self):
        self.assertRaises(ValueError, config.SFConfig)

    @mock.patch.dict('shakenfist.config.CONFIG_DEFAULTS', {'FOO': [1, 2, 3]})
    @mock.patch.dict('os.environ', {'SHAKENFIST_FOO': '[1, 4, 6]'})
    def test_bogus_default(self):
        self.assertRaises(exceptions.FlagException, config.SFConfig)
