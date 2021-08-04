import mock

from shakenfist.config import SFConfig
from shakenfist.tests import test_shakenfist


class ConfigTestCase(test_shakenfist.ShakenFistTestCase):
    @mock.patch('socket.getfqdn', return_value='a.b.com')
    def test_hostname(self, mock_fqdn):
        conf = SFConfig()
        mock_fqdn.assert_called()
        self.assertEqual('a.b.com', str(conf.NODE_NAME))

    @mock.patch.dict('os.environ', {'SHAKENFIST_STORAGE_PATH': 'foo'})
    def test_string_override(self):
        conf = SFConfig()
        self.assertTrue(isinstance(conf.STORAGE_PATH, str))
        self.assertEqual('foo', conf.STORAGE_PATH)

    @mock.patch.dict('os.environ', {'SHAKENFIST_CPU_OVERCOMMIT_RATIO': '1'})
    def test_int_override(self):
        conf = SFConfig()
        self.assertTrue(isinstance(conf.CPU_OVERCOMMIT_RATIO, float))
        self.assertEqual(1, conf.CPU_OVERCOMMIT_RATIO)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_RAM_SYSTEM_RESERVATION': '4.0'})
    def test_float_override(self):
        conf = SFConfig()
        self.assertTrue(isinstance(conf.RAM_SYSTEM_RESERVATION, float))
        self.assertEqual(4.0, conf.RAM_SYSTEM_RESERVATION)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_RAM_SYSTEM_RESERVATION': 'banana'})
    def test_bogus_override(self):
        self.assertRaises(ValueError, SFConfig)
