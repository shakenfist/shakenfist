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
from shakenfist.exceptions import NatOnlyNetworksShouldNotHaveDnsMasq
from shakenfist.managed_executables import dnsmasq
from shakenfist import network
from shakenfist.tests.mock_etcd import MockEtcd

TEST_DIR = os.path.dirname(os.path.abspath(__file__))


class DnsMasqTestCase(testtools.TestCase):
    def setUp(self):
        super().setUp()

        class FakeConfig(BaseSettings):
            DNS_SERVER: str = '8.8.8.8'
            MAX_HYPERVISOR_MTU: int = 8000
            NODE_NAME: str = 'foo'
            STORAGE_PATH: str = f'{TEST_DIR}/files/'
            ZONE: str = 'shakenfist'
            ETCD_USER: str = 'sf'
            ETCD_PASSWORD: str = 'foo'
            ETCD_SERVER: str = 'localhost'
            NODE_EGRESS_IP: IPvAnyAddress = '127.0.0.1'
            DOWNLOAD_URL_CIRROS: AnyHttpUrl = (
                'http://download.cirros-cloud.net/%(vernum)s/'
                'cirros-%(vernum)s-x86_64-disk.img')
            DOWNLOAD_URL_UBUNTU: AnyHttpUrl = (
                'https://cloud-images.ubuntu.com/%(vername)s/current/'
                '%(vername)s-server-cloudimg-amd64.img')
            ETCD_HOST: str = '127.0.0.1'

        fake_config = FakeConfig()
        self.config = mock.patch(
            'shakenfist.managed_executables.managedexecutable.config',
            fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

    def test_init(self):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid)
        n = network.Network.from_db(network_uuid)

        d = dnsmasq.DnsMasq.new(n)
        self.assertEqual(f'{TEST_DIR}/files/dhcp/{network_uuid}',
                         d.config_directory)

    def test_str(self):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network('bobnet', network_uuid)
        n = network.Network.from_db(network_uuid)

        d = dnsmasq.DnsMasq.new(n)
        s = str(d)
        self.assertEqual(
            f'dhcp({network_uuid}, as owned by network({network_uuid}))', s)

    def test_override_config_dir(self):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)

        with tempfile.TemporaryDirectory() as dir:
            d = dnsmasq.DnsMasq.new(n)
            d.config_directory = dir
            self.assertEqual(dir, d.config_directory)

    @mock.patch('os.makedirs')
    def test_make_config_just_dhcp(self, mock_makedir):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dnsmasq.DnsMasq.new(n, provide_dhcp=True, provide_nat=False,
                                provide_dns=False)

        mock_open = mock.mock_open()
        with mock.patch.object(six.moves.builtins, 'open', new=mock_open):
            d._make_config(just_this_path='config')

        handle = mock_open()
        handle.write.assert_called_with(
            '\n'.join([
                'domain-needed    # Do not forward DNS lookups for unqualified names',
                'bogus-priv       # Do not forward DNS lookups for RFC1918 blocks',
                'no-hosts         # Do not use /etc/hosts',
                'no-resolv        # Do not use /etc/resolv.conf',
                'filterwin2k      # Filter weird windows 2000 queries',
                'port=0           # Disable DNS',
                '',
                f'pid-file={TEST_DIR}/files/dhcp/{network_uuid}/pid',
                '',
                f'interface={d.interface}',
                'listen-address=10.0.0.1',
                '',
                'domain=shakenfist',
                'local=/shakenfist/',
                f'dhcp-leasefile={TEST_DIR}/files/dhcp/{network_uuid}/leases',
                f'dhcp-range={d.interface},10.0.0.2,static,255.0.0.0,10.255.255.255,1h',
                f'dhcp-option={d.interface},1,255.0.0.0',
                f'dhcp-option={d.interface},15,shakenfist',
                f'dhcp-option={d.interface},26,7950',
                f'dhcp-hostsfile={TEST_DIR}/files/dhcp/{network_uuid}/hosts'
            ])
        )

    @mock.patch('os.makedirs')
    def test_make_config_just_dns(self, mock_makedir):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dnsmasq.DnsMasq.new(n, provide_dhcp=False, provide_nat=False,
                                provide_dns=True)

        mock_open = mock.mock_open()
        with mock.patch.object(six.moves.builtins, 'open', new=mock_open):
            d._make_config(just_this_path='config')

        handle = mock_open()
        handle.write.assert_called_with(
            '\n'.join([
                'domain-needed    # Do not forward DNS lookups for unqualified names',
                'bogus-priv       # Do not forward DNS lookups for RFC1918 blocks',
                'no-hosts         # Do not use /etc/hosts',
                'no-resolv        # Do not use /etc/resolv.conf',
                'filterwin2k      # Filter weird windows 2000 queries',
                'port=53          # Enable DNS',
                'server=8.8.8.8',
                f'addn-hosts={TEST_DIR}/files/dhcp/{network_uuid}/dnshosts',
                'expand-hosts',
                '',
                f'pid-file={TEST_DIR}/files/dhcp/{network_uuid}/pid',
                '',
                f'interface={d.interface}',
                'listen-address=10.0.0.1',
                '',
                'domain=shakenfist',
                'local=/shakenfist/'
            ])
        )

    @mock.patch('os.makedirs')
    def test_make_config_just_nat(self, mock_makedir):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        self.assertRaises(
            NatOnlyNetworksShouldNotHaveDnsMasq, dnsmasq.DnsMasq.new, n,
            provide_dhcp=False, provide_nat=True, provide_dns=False)

    @mock.patch('os.makedirs')
    def test_make_config_nothing(self, mock_makedir):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        self.assertRaises(
            NatOnlyNetworksShouldNotHaveDnsMasq, dnsmasq.DnsMasq.new, n,
            provide_dhcp=False, provide_nat=False, provide_dns=False)

    @mock.patch('os.makedirs')
    def test_make_config_everything(self, mock_makedir):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dnsmasq.DnsMasq.new(n, provide_dhcp=True, provide_nat=True,
                                provide_dns=True)

        mock_open = mock.mock_open()
        with mock.patch.object(six.moves.builtins, 'open', new=mock_open):
            d._make_config(just_this_path='config')

        handle = mock_open()
        handle.write.assert_called_with(
            '\n'.join([
                'domain-needed    # Do not forward DNS lookups for unqualified names',
                'bogus-priv       # Do not forward DNS lookups for RFC1918 blocks',
                'no-hosts         # Do not use /etc/hosts',
                'no-resolv        # Do not use /etc/resolv.conf',
                'filterwin2k      # Filter weird windows 2000 queries',
                'port=53          # Enable DNS',
                'server=8.8.8.8',
                f'addn-hosts={TEST_DIR}/files/dhcp/{network_uuid}/dnshosts',
                'expand-hosts',
                f'dhcp-option={d.interface},6,10.0.0.1',
                '',
                f'pid-file={TEST_DIR}/files/dhcp/{network_uuid}/pid',
                '',
                f'interface={d.interface}',
                'listen-address=10.0.0.1',
                '',
                'domain=shakenfist',
                'local=/shakenfist/',
                f'dhcp-leasefile={TEST_DIR}/files/dhcp/{network_uuid}/leases',
                f'dhcp-range={d.interface},10.0.0.2,static,255.0.0.0,10.255.255.255,1h',
                f'dhcp-option={d.interface},1,255.0.0.0',
                f'dhcp-option={d.interface},15,shakenfist',
                f'dhcp-option={d.interface},26,7950',
                f'dhcp-hostsfile={TEST_DIR}/files/dhcp/{network_uuid}/hosts'
            ])
        )

    @mock.patch('os.makedirs')
    def test_make_dhcp_hosts(self, mock_makedir):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        instance_uuid_one = str(uuid.uuid4())
        instance_uuid_two = str(uuid.uuid4())
        network_uuid = str(uuid.uuid4())
        iface_uuid_one = str(uuid.uuid4())
        iface_uuid_two = str(uuid.uuid4())

        self.mock_etcd.create_network(
            'testing', network_uuid, netblock='127.0.0.0/8')
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
        d = dnsmasq.DnsMasq.new(n)

        mock_open = mock.mock_open()
        with mock.patch.object(six.moves.builtins, 'open', new=mock_open):
            d._make_config(just_this_path='hosts')

        handle = mock_open()
        handle.write.assert_called_with(
            '\n'.join([
                '',
                '1a:91:64:d2:15:39,inst1,127.0.0.5',
                '1a:91:64:d2:15:40,inst2,127.0.0.6',
            ])
        )

    @mock.patch('os.makedirs')
    def test_make_dns_hosts(self, mock_makedir):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        instance_uuid_one = str(uuid.uuid4())
        instance_uuid_two = str(uuid.uuid4())
        network_uuid = str(uuid.uuid4())
        iface_uuid_one = str(uuid.uuid4())
        iface_uuid_two = str(uuid.uuid4())

        self.mock_etcd.create_network(
            'testing', network_uuid, netblock='127.0.0.0/8')
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
        d = dnsmasq.DnsMasq.new(n, provide_dns=True)

        mock_open = mock.mock_open()
        with mock.patch.object(six.moves.builtins, 'open', new=mock_open):
            d._make_config(just_this_path='dnshosts')

        handle = mock_open()
        handle.write.assert_called_with(
            '\n'.join([
                '',
                '127.0.0.5 inst1',
                '127.0.0.6 inst2',
            ])
        )

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shutil.rmtree')
    def test_remove_config(self, mock_rmtree, mock_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dnsmasq.DnsMasq.new(n)

        d._remove_config()
        mock_exists.assert_called_with(f'{TEST_DIR}/files/dhcp/{network_uuid}')
        mock_rmtree.assert_called_with(f'{TEST_DIR}/files/dhcp/{network_uuid}')

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('psutil.pid_exists', return_value=True)
    @mock.patch('os.kill')
    @mock.patch('os.waitpid')
    def test_send_signal(self, mock_waitpid, mock_kill, mock_pid_exists,
                         mock_path_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dnsmasq.DnsMasq.new(n)

        mock_open = mock.mock_open(read_data='424242')
        with mock.patch.object(six.moves.builtins, 'open', new=mock_open):
            self.assertEqual(True, d._send_signal(signal.SIGKILL))

        mock_pid_exists.assert_called()
        mock_path_exists.assert_called_with(
            f'{TEST_DIR}/files/dhcp/{network_uuid}/pid')
        mock_kill.assert_called_with(424242, signal.SIGKILL)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('psutil.pid_exists', return_value=False)
    @mock.patch('os.kill')
    def test_send_signal_no_process(self, mock_kill, mock_pid_exists,
                                    mock_path_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dnsmasq.DnsMasq.new(n)

        mock_open = mock.mock_open(read_data='424242')
        with mock.patch.object(six.moves.builtins, 'open', new=mock_open):
            self.assertEqual(False, d._send_signal(signal.SIGKILL))

        mock_pid_exists.assert_called()
        mock_path_exists.assert_called_with(
            f'{TEST_DIR}/files/dhcp/{network_uuid}/pid')
        mock_kill.assert_not_called()

    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._send_signal')
    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._remove_config')
    def test_remove_dhcpd(self, mock_remove_config, mock_signal):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)
        d = dnsmasq.DnsMasq.new(n)

        d.terminate()
        mock_remove_config.assert_called()
        mock_signal.assert_called_with(signal.SIGKILL)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('os.makedirs')
    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._send_signal',
                return_value=False)
    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._make_config')
    @mock.patch('shakenfist.util.process.execute')
    def test_restart(self, mock_execute, mock_config, mock_signal,
                     mock_makedirs, mock_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)

        with tempfile.TemporaryDirectory() as dir:
            with open(os.path.join(dir, 'leases'), 'w') as f:
                f.write('')

            d = dnsmasq.DnsMasq.new(n)
            d.config_directory = dir
            d.restart()
            mock_signal.assert_called_with(signal.SIGHUP)
            mock_execute.assert_called_with(
                None, 'dnsmasq --conf-file=%s/config' % dir,
                namespace=network_uuid)

    @mock.patch('os.path.exists', return_value=True)
    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._send_signal',
                return_value=False)
    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._make_config')
    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._enumerate_leases',
                return_value=(
                    None, {
                        '02:00:00:55:04:a2': '172.10.0.8',
                        '1a:91:64:d2:15:39': '127.0.0.5'
                    })
                )
    @mock.patch('shakenfist.util.process.execute')
    def test_remove_leases(self, mock_execute, mock_hosts, mock_config,
                           mock_signal, mock_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)

        with tempfile.TemporaryDirectory() as dir:
            with open(os.path.join(dir, 'leases'), 'w') as f:
                f.write('%d 02:00:00:55:04:a2 172.10.0.8 client *\n'
                        % (time.time() - 3600))
                f.write('%d 02:00:00:55:04:a3 172.10.0.9 client2 *\n'
                        % (time.time() + 3600))
                f.write('%d 1a:91:64:d2:15:39 127.0.0.5 client3 *'
                        % (time.time() + 3600))

            d = dnsmasq.DnsMasq.new(n)
            d.config_directory = dir
            d.restart()

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
    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._send_signal',
                return_value=True)
    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._make_config')
    @mock.patch('shakenfist.managed_executables.dnsmasq.DnsMasq._enumerate_leases',
                return_value=(
                    None, {
                        '02:00:00:55:04:a2': '172.10.0.8',
                        '1a:91:64:d2:15:39': '127.0.0.5'
                    })
                )
    @mock.patch('shakenfist.util.process.execute')
    def test_remove_no_leases(self, mock_execute, mock_hosts, mock_config,
                              mock_signal, mock_exists):
        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

        network_uuid = str(uuid.uuid4())
        self.mock_etcd.create_network(
            'bobnet', network_uuid, netblock='10.0.0.0/8')
        n = network.Network.from_db(network_uuid)

        with tempfile.TemporaryDirectory() as dir:
            with open(os.path.join(dir, 'leases'), 'w') as f:
                f.write('%d 1a:91:64:d2:15:39 127.0.0.5 client3 *'
                        % (time.time() + 3600))

            d = dnsmasq.DnsMasq.new(n)
            d.config_directory = dir

            d.restart()

            mock_signal.assert_called_with(signal.SIGHUP)

            with open(os.path.join(dir, 'leases')) as f:
                leases = f.read()
            self.assertTrue('1a:91:64:d2:15:39' in leases)
