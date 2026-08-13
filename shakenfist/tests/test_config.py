from unittest import mock

from shakenfist.config import SFConfig
from shakenfist.tests import base


class ConfigTestCase(base.ShakenFistTestCase):
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
                     {'SHAKENFIST_NODE_RAM_RESERVATION_GB': '4.0'})
    def test_float_override(self):
        conf = SFConfig()
        self.assertTrue(isinstance(conf.NODE_RAM_RESERVATION_GB, float))
        self.assertEqual(4.0, conf.NODE_RAM_RESERVATION_GB)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_NODE_RAM_RESERVATION_GB': 'banana'})
    def test_bogus_override(self):
        self.assertRaises(ValueError, SFConfig)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_API_VALIDATION_MODE': 'enforced'})
    def test_bogus_validation_mode_fails_at_load(self):
        # The setting exists to be flipped to 'enforce' in phase 4 of
        # PLAN-api-input-validation. A typo silently meaning warn is no
        # validation and no signal anything is wrong, so anything other
        # than the two literals must refuse to load.
        self.assertRaises(ValueError, SFConfig)

    @mock.patch.dict('os.environ',
                     {'SHAKENFIST_API_VALIDATION_MODE': 'enforce'})
    def test_valid_validation_mode_loads(self):
        conf = SFConfig()
        self.assertEqual('enforce', conf.API_VALIDATION_MODE)
