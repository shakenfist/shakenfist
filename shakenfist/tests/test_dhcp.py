import jinja2
import mock
import os
import signal
import six
import testtools
from pydantic import AnyHttpUrl, IPvAnyAddress

from shakenfist.config import BaseSettings
from shakenfist.ipmanager import IPManager
from shakenfist import dhcp
from shakenfist.instance import Instance
from shakenfist.networkinterface import NetworkInterface

TEST_DIR = os.path.dirname(os.path.abspath(__file__))


class FakeNetwork(object):
    def __init__(self):
        self.uuid = 'notauuid'
        self.ipmanager = IPManager('uuid', '10.0.0.0/8')
        self.router = self.ipmanager.get_address_at_index(1)
        self.dhcp_start = '10.0.0.2'
        self.netmask = '255.0.0.0'
        self.broadcast = '10.255.255.255'
        self.provide_nat = True
        self.networkinterfaces = ['notauuid1', 'notauuid2']


class FakeNetworkInterface(object):
    object_type = 'networkinterface'

    def __init__(self, values):
        self.uuid = values['uuid']
        self.instance_uuid = values['instance_uuid']
        self.network_uuid = values['network_uuid']
        self.macaddr = values['macaddr']
        self.ipv4 = values['ipv4']
        self.order = values['order']


class DHCPTestCase(testtools.TestCase):
    def setUp(self):
        super(DHCPTestCase, self).setUp()

        class FakeConfig(BaseSettings):
            DNS_SERVER: str = '8.8.8.8'
            MAX_HYPERVISOR_MTU: int = 8000
            NODE_NAME: str = 'foo'
            STORAGE_PATH: str = '/a/b/c'
            ZONE: str = 'shakenfist'
            ETCD_USER: str = 'sf'
            ETCD_PASSWORD: str = 'foo'
            ETCD_SERVER: str = 'localhost'
            NODE_EGRESS_IP: IPvAnyAddress = '127.0.0.1'
            DOWNLOAD_URL_CIRROS: AnyHttpUrl = ('http://download.cirros-cloud.net/%(vernum)s/'
                                               'cirros-%(vernum)s-x86_64-disk.img')
            DOWNLOAD_URL_UBUNTU: AnyHttpUrl = ('https://cloud-images.ubuntu.com/%(vername)s/current/'
                                               '%(vername)s-server-cloudimg-amd64.img')
            ETCD_HOST: str = '127.0.0.1'

        fake_config = FakeConfig()

        self.config = mock.patch('shakenfist.dhcp.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        with open('%s/files/dhcp.tmpl' % TEST_DIR) as f:
            dhcp_tmpl = f.read()
        with open('%s/files/dhcphosts.tmpl' % TEST_DIR) as f:
            dhcphosts_tmpl = f.read()

        def fake_read_template(self, filename):
            if filename == 'dhcp.tmpl':
                return jinja2.Template(dhcp_tmpl)
            if filename == 'dhcphosts.tmpl':
                return jinja2.Template(dhcphosts_tmpl)
            raise Exception('Unknown template')

        self.template = mock.patch('shakenfist.dhcp.DHCP._read_template',
                                   fake_read_template)
        self.mock_template = self.template.start()
        self.addCleanup(self.template.stop)

    def test_init(self):
        d = dhcp.DHCP(FakeNetwork(), 'eth0')
        self.assertEqual('/a/b/c/dhcp/notauuid', d.subst['config_dir'])

    def test_str(self):
        d = dhcp.DHCP(FakeNetwork(), 'eth0')
        s = str(d)
        self.assertEqual('dhcp(notauuid)', s)

    @mock.patch('os.path.exists', return_value=True)
    def test_make_config(self, mock_exists):
        d = dhcp.DHCP(FakeNetwork(), 'eth0')

        mock_open = mock.mock_open()
        with mock.patch.object(six.moves.builtins, 'open',
                               new=mock_open):
            d._make_config()

        handle = mock_open()
        handle.write.assert_called_with(
            '\n'.join([
                'domain-needed    # Do not forward DNS lookups for unqualified names',
                'bogus-priv       # Do not forward DNS lookups for RFC1918 blocks',
                'no-hosts         # Do not use /etc/hosts',
                'no-resolv        # Do not use /etc/resolv.conf',
                'filterwin2k      # Filter weird windows 2000 queries',
                '',
                '# Disable DNS',
                'port=0',
                '',
                'pid-file=/a/b/c/dhcp/notauuid/pid',
                'dhcp-leasefile=/a/b/c/dhcp/notauuid/leases',
                '',
                'interface=eth0',
                'listen-address=10.0.0.1',
                '',
                'domain=shakenfist',
                'local=/shakenfist/',
                '',
                'dhcp-range=eth0,10.0.0.2,static,255.0.0.0,10.255.255.255,1h',
                'dhcp-option=eth0,1,255.0.0.0',
                'dhcp-option=eth0,3,10.0.0.1',
                'dhcp-option=eth0,6,8.8.8.8',
                'dhcp-option=eth0,15,shakenfist',
                'dhcp-hostsfile=/a/b/c/dhcp/notauuid/hosts',
            ])
        )

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.networkinterface.NetworkInterface._db_get',
                side_effect=[
                    {
                        'uuid': 'notauuid1',
                        'instance_uuid': 'instuuid1',
                        'network_uuid': 'netuuid',
                        'macaddr': '1a:91:64:d2:15:39',
                        'model': 'virtio',
                        'ipv4': '127.0.0.5',
                        'order': 0,
                        'version': NetworkInterface.current_version
                    },
                    {
                        'uuid': 'notauuid2',
                        'instance_uuid': 'instuuid2',
                        'network_uuid': 'netuuid',
                        'macaddr': '1a:91:64:d2:15:40',
                        'model': 'virtio',
                        'ipv4': '127.0.0.6',
                        'order': 0,
                        'version': NetworkInterface.current_version
                    }
                ])
    @mock.patch('shakenfist.instance.Instance._db_get',
                side_effect=[
                    {
                        'uuid': 'instuuid1',
                        'name': 'inst1',
                        'disk_spec': [{'size': 4, 'base': 'foo'}],
                        'version': Instance.current_version
                    },
                    {
                        'uuid': 'instuuid2',
                        'name': 'inst2',
                        'disk_spec': [{'size': 4, 'base': 'foo'}],
                        'version': Instance.current_version
                    }])
    def test_make_hosts(self, mock_instances, mock_interfaces, mock_exists):
        d = dhcp.DHCP(FakeNetwork(), 'eth0')

        mock_open = mock.mock_open()
        with mock.patch.object(six.moves.builtins, 'open',
                               new=mock_open):
            d._make_hosts()

        handle = mock_open()
        handle.write.assert_called_with(
            '\n'.join([
                '',
                '1a:91:64:d2:15:39,inst1,127.0.0.5',
                '1a:91:64:d2:15:40,inst2,127.0.0.6',
            ])
        )

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shutil.rmtree')
    def test_remove_config(self, mock_rmtree, mock_exists):
        d = dhcp.DHCP(FakeNetwork(), 'eth0')
        d._remove_config()
        mock_exists.assert_called_with('/a/b/c/dhcp/notauuid')
        mock_rmtree.assert_called_with('/a/b/c/dhcp/notauuid')

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('psutil.pid_exists', return_value=True)
    @mock.patch('os.kill')
    @mock.patch('os.waitpid')
    def test_send_signal(self, mock_waitpid, mock_kill, mock_pid_exists,
                         mock_path_exists):
        d = dhcp.DHCP(FakeNetwork(), 'eth0')

        mock_open = mock.mock_open(read_data='424242')
        with mock.patch.object(six.moves.builtins, 'open',
                               new=mock_open):
            self.assertEqual(True, d._send_signal(signal.SIGKILL))

        mock_pid_exists.assert_called()
        mock_path_exists.assert_called_with('/a/b/c/dhcp/notauuid/pid')
        mock_kill.assert_called_with(424242, signal.SIGKILL)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('psutil.pid_exists', return_value=False)
    @mock.patch('os.kill')
    def test_send_signal_no_process(self, mock_kill, mock_pid_exists,
                                    mock_path_exists):
        d = dhcp.DHCP(FakeNetwork(), 'eth0')

        mock_open = mock.mock_open(read_data='424242')
        with mock.patch.object(six.moves.builtins, 'open',
                               new=mock_open):
            self.assertEqual(False, d._send_signal(signal.SIGKILL))

        mock_pid_exists.assert_called()
        mock_path_exists.assert_called_with('/a/b/c/dhcp/notauuid/pid')
        mock_kill.assert_not_called()

    @mock.patch('shakenfist.dhcp.DHCP._send_signal')
    @mock.patch('shakenfist.dhcp.DHCP._remove_config')
    def test_remove_dhcpd(self, mock_remove_config, mock_signal):
        d = dhcp.DHCP(FakeNetwork(), 'eth0')
        d.remove_dhcpd()
        mock_remove_config.assert_called()
        mock_signal.assert_called_with(signal.SIGKILL)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.dhcp.DHCP._send_signal', return_value=False)
    @mock.patch('shakenfist.dhcp.DHCP._make_config')
    @mock.patch('shakenfist.dhcp.DHCP._make_hosts')
    @mock.patch('shakenfist.util.process.execute')
    def test_restart_dhcpd(self, mock_execute, mock_hosts, mock_config,
                           mock_signal, mock_exists):
        d = dhcp.DHCP(FakeNetwork(), 'eth0')
        d.restart_dhcpd()
        mock_signal.assert_called_with(signal.SIGHUP)
        mock_execute.assert_called_with(
            None, 'dnsmasq --conf-file=/a/b/c/dhcp/notauuid/config',
            namespace='notauuid')
