from unittest import mock

from shakenfist.tests import base
from shakenfist.util import general as util_general


class UtilUserAgent(base.ShakenFistTestCase):
    @mock.patch('cpuinfo.get_cpu_info', return_value={
        'arch_string_raw': 'x86_64',
        'vendor_id_raw': 'GenuineIntel'
    })
    @mock.patch('distro.name', return_value='Debian GNU/Linux 10 (buster)')
    @mock.patch('shakenfist.util.general.get_version', return_value='1.2.3')
    def test_user_agent(self, mock_version, mock_distro, mock_cpuinfo):
        ua = util_general.get_user_agent()
        self.assertEqual('Mozilla/5.0 (Debian GNU/Linux 10 (buster); '
                         'GenuineIntel x86_64) Shaken Fist/1.2.3',
                         ua)

    @mock.patch('cpuinfo.get_cpu_info', return_value={})
    @mock.patch('distro.name', return_value='Debian GNU/Linux 10 (buster)')
    @mock.patch('shakenfist.util.general.get_version', return_value='1.2.3')
    def test_user_agent_partial_cpu_info(self, mock_version, mock_distro, mock_cpuinfo):
        # py-cpuinfo returns whatever its probes collected, so both keys can be
        # missing (issue 3523). This must not raise a KeyError.
        ua = util_general.get_user_agent()
        self.assertEqual('Mozilla/5.0 (Debian GNU/Linux 10 (buster); '
                         'unknown unknown) Shaken Fist/1.2.3',
                         ua)

    @mock.patch('cpuinfo.get_cpu_info', return_value={'arch_string_raw': 'x86_64'})
    @mock.patch('distro.name', return_value='Debian GNU/Linux 10 (buster)')
    @mock.patch('shakenfist.util.general.get_version', return_value='1.2.3')
    def test_user_agent_missing_vendor_only(self, mock_version, mock_distro, mock_cpuinfo):
        # A probe can also succeed for one key and not the other; pin that the two
        # lookups are guarded independently rather than as a single fallback.
        ua = util_general.get_user_agent()
        self.assertEqual('Mozilla/5.0 (Debian GNU/Linux 10 (buster); '
                         'unknown x86_64) Shaken Fist/1.2.3',
                         ua)
