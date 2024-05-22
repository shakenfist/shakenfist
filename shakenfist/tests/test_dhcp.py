import jinja2
from unittest import mock
import os
from pydantic import AnyHttpUrl, IPvAnyAddress
import signal
import six
import tempfile
import testtools
import time
import uuid

from shakenfist.config import BaseSettings
from shakenfist import dhcp
from shakenfist import network
from shakenfist.tests.mock_etcd import MockEtcd

TEST_DIR = os.path.dirname(os.path.abspath(__file__))


class DHCPTestCase(testtools.TestCase):
    def setUp(self):
        super().setUp()

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
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid)
        n = network.Network.from_db(network_uuid)

        d = dhcp.DHCP(n, 'eth0')
        self.assertEqual('/a/b/c/dhcp/%s' % network_uuid, d.subst['config_dir'])

    def test_str(self):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid)
        n = network.Network.from_db(network_uuid)

        d = dhcp.DHCP(n, 'eth0')
        s = str(d)
        self.assertEqual('dhcp(%s)' % network_uuid, s)

    @mock.patch('os.makedirs')
    def test_make_config(self, mock_makedir):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dhcp.DHCP(n, 'eth0')

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
                'pid-file=/a/b/c/dhcp/%(network_uuid)s/pid',
                'dhcp-leasefile=/a/b/c/dhcp/%(network_uuid)s/leases',
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
                'dhcp-hostsfile=/a/b/c/dhcp/%(network_uuid)s/hosts',
            ]) % {'network_uuid': network_uuid}
        )

    @mock.patch('os.makedirs')
    def test_make_hosts(self, mock_makedir):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        instance_uuid_one = str(uuid.uuid4())
        instance_uuid_two = str(uuid.uuid4())
        network_uuid = str(uuid.uuid4())
        iface_uuid_one = str(uuid.uuid4())
        iface_uuid_two = str(uuid.uuid4())

        self.mock_etcd.create_network('testing', network_uuid, netblock='127.0.0.0/8')
        self.mock_etcd.create_network_interface(
            iface_uuid_one,
            {
                'network_uuid': network_uuid,
                'address': '127.0.0.5',
                'model': None,
                'macaddress': '1a:91:64:d2:15:39',
            },
            instance_uuid=instance_uuid_one, order=0)
        self.mock_etcd.create_network_interface(
            iface_uuid_two,
            {
                'network_uuid': network_uuid,
                'address': '127.0.0.6',
                'model': None,
                'macaddress': '1a:91:64:d2:15:40',
            },
            instance_uuid=instance_uuid_two, order=0)
        self.mock_etcd.create_instance('inst1', instance_uuid_one)
        self.mock_etcd.create_instance('inst2', instance_uuid_two)

        n = network.Network.from_db(network_uuid)
        d = dhcp.DHCP(n, 'eth0')

        mock_open = mock.mock_open()
        with mock.patch.object(six.moves.builtins, 'open', new=mock_open):
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
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dhcp.DHCP(n, 'eth0')

        d._remove_config()
        mock_exists.assert_called_with('/a/b/c/dhcp/%s' % network_uuid)
        mock_rmtree.assert_called_with('/a/b/c/dhcp/%s' % network_uuid)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('psutil.pid_exists', return_value=True)
    @mock.patch('os.kill')
    @mock.patch('os.waitpid')
    def test_send_signal(self, mock_waitpid, mock_kill, mock_pid_exists,
                         mock_path_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dhcp.DHCP(n, 'eth0')

        mock_open = mock.mock_open(read_data='424242')
        with mock.patch.object(six.moves.builtins, 'open',
                               new=mock_open):
            self.assertEqual(True, d._send_signal(signal.SIGKILL))

        mock_pid_exists.assert_called()
        mock_path_exists.assert_called_with('/a/b/c/dhcp/%s/pid' % network_uuid)
        mock_kill.assert_called_with(424242, signal.SIGKILL)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('psutil.pid_exists', return_value=False)
    @mock.patch('os.kill')
    def test_send_signal_no_process(self, mock_kill, mock_pid_exists,
                                    mock_path_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dhcp.DHCP(n, 'eth0')

        mock_open = mock.mock_open(read_data='424242')
        with mock.patch.object(six.moves.builtins, 'open',
                               new=mock_open):
            self.assertEqual(False, d._send_signal(signal.SIGKILL))

        mock_pid_exists.assert_called()
        mock_path_exists.assert_called_with('/a/b/c/dhcp/%s/pid' % network_uuid)
        mock_kill.assert_not_called()

    @mock.patch('shakenfist.dhcp.DHCP._send_signal')
    @mock.patch('shakenfist.dhcp.DHCP._remove_config')
    def test_remove_dhcpd(self, mock_remove_config, mock_signal):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dhcp.DHCP(n, 'eth0')

        d.remove_dhcpd()
        mock_remove_config.assert_called()
        mock_signal.assert_called_with(signal.SIGKILL)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.dhcp.DHCP._send_signal', return_value=False)
    @mock.patch('shakenfist.dhcp.DHCP._make_config')
    @mock.patch('shakenfist.dhcp.DHCP._make_hosts')
    @mock.patch('shakenfist.util.process.execute')
    def test_restart_dhcpd(self, mock_execute, mock_hosts, mock_config,
                           mock_signal, mock_makedirs, mock_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)

        with tempfile.TemporaryDirectory() as dir:
            with open(os.path.join(dir, 'leases'), 'w') as f:
                f.write('')

            d = dhcp.DHCP(n, 'eth0')
            d.subst['config_dir'] = dir
            d.restart_dhcpd()
            mock_signal.assert_called_with(signal.SIGHUP)
            mock_execute.assert_called_with(
                None, 'dnsmasq --conf-file=%s/config' % dir,
                namespace=network_uuid)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.dhcp.DHCP._send_signal', return_value=False)
    @mock.patch('shakenfist.dhcp.DHCP._make_config')
    @mock.patch('shakenfist.dhcp.DHCP._make_hosts',
                return_value={
                    '02:00:00:55:04:a2': '172.10.0.8',
                    '1a:91:64:d2:15:39': '127.0.0.5'
                })
    @mock.patch('shakenfist.util.process.execute')
    def test_remove_leases(self, mock_execute, mock_hosts, mock_config,
                           mock_signal, mock_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)

        with tempfile.TemporaryDirectory() as dir:
            with open(os.path.join(dir, 'leases'), 'w') as f:
                f.write('%d 02:00:00:55:04:a2 172.10.0.8 client *\n'
                        % (time.time() - 3600))
                f.write('%d 02:00:00:55:04:a3 172.10.0.9 client2 *\n'
                        % (time.time() + 3600))
                f.write('%d 1a:91:64:d2:15:39 127.0.0.5 client3 *'
                        % (time.time() + 3600))

            d = dhcp.DHCP(n, 'eth0')
            d.subst['config_dir'] = dir

            d.restart_dhcpd()

            mock_signal.assert_called_with(signal.SIGKILL)
            mock_execute.assert_called_with(
                None, 'dnsmasq --conf-file=%s/config' % dir,
                namespace=network_uuid)

            with open(os.path.join(dir, 'leases')) as f:
                leases = f.read()

            # Expired lease stays
            self.assertTrue('02:00:00:55:04:a2' in leases)

            # Invalid lease removed
            self.assertFalse('02:00:00:55:04:a3' in leases)

            # Valid lease stays
            self.assertTrue('1a:91:64:d2:15:39' in leases)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.dhcp.DHCP._send_signal', return_value=True)
    @mock.patch('shakenfist.dhcp.DHCP._make_config')
    @mock.patch('shakenfist.dhcp.DHCP._make_hosts',
                return_value={
                    '02:00:00:55:04:a2': '172.10.0.8',
                    '1a:91:64:d2:15:39': '127.0.0.5'
                })
    @mock.patch('shakenfist.util.process.execute')
    def test_remove_no_leases(self, mock_execute, mock_hosts, mock_config,
                              mock_signal, mock_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)

        with tempfile.TemporaryDirectory() as dir:
            with open(os.path.join(dir, 'leases'), 'w') as f:
                f.write('%d 1a:91:64:d2:15:39 127.0.0.5 client3 *'
                        % (time.time() + 3600))

            d = dhcp.DHCP(n, 'eth0')
            d.subst['config_dir'] = dir

            d.restart_dhcpd()

            mock_signal.assert_called_with(signal.SIGHUP)

            with open(os.path.join(dir, 'leases')) as f:
                leases = f.read()
            self.assertTrue('1a:91:64:d2:15:39' in leases)
