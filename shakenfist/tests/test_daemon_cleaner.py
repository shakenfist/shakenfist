import json
from unittest import mock

from shakenfist import instance
from shakenfist.config import BaseSettings
from shakenfist.daemons import cleaner
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


class FakeLibvirt:
    VIR_DOMAIN_BLOCKED = 1
    VIR_DOMAIN_CRASHED = 2
    VIR_DOMAIN_NOSTATE = 3
    VIR_DOMAIN_PAUSED = 4
    VIR_DOMAIN_RUNNING = 5
    VIR_DOMAIN_SHUTDOWN = 6
    VIR_DOMAIN_SHUTOFF = 7
    VIR_DOMAIN_PMSUSPENDED = 8

    libvirtError = Exception

    def open(self, _ignored):
        return FakeLibvirtConnection()


class FakeLibvirtConnection:
    def listDomainsID(self):
        return ['id1', 'id2', 'id3', 'id4', 'id5', 'id6']

    def lookupByID(self, id):
        args = {
            'id1': ('sf:running', FakeLibvirt.VIR_DOMAIN_RUNNING),
            'id2': ('apache2', FakeLibvirt.VIR_DOMAIN_RUNNING),
            'id3': ('sf:shutoff', FakeLibvirt.VIR_DOMAIN_SHUTOFF),
            'id4': ('sf:crashed', FakeLibvirt.VIR_DOMAIN_CRASHED),
            'id5': ('sf:paused', FakeLibvirt.VIR_DOMAIN_PAUSED),
            'id6': ('sf:suspended', FakeLibvirt.VIR_DOMAIN_PMSUSPENDED),
        }

        return FakeLibvirtDomain(*args.get(id))

    def lookupByName(self, name):
        return FakeLibvirtDomain(name, FakeLibvirt.VIR_DOMAIN_RUNNING)

    def close(self):
        pass


class FakeLibvirtDomain:
    def __init__(self, name, state):
        self._name = name
        self._state = state

    def name(self):
        return self._name

    def state(self):
        return [self._state, 1]

    def UUIDString(self):
        return 'fake_uuid'


def fake_exists(path):
    if path == '/srv/shakenfist/instances/nofiles':
        return False
    return True


class FakeConfig(BaseSettings):
    NODE_NAME: str = 'abigcomputer'
    STORAGE_PATH: str = '/srv/shakenfist'
    LOGLEVEL_CLEANER: str = 'debug'
    ETCD_HOST: str = '127.0.0.1'


fake_config = FakeConfig()


class CleanerTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.libvirt = mock.patch(
            'shakenfist.util.libvirt.get_libvirt',
            return_value=FakeLibvirt())
        self.mock_libvirt = self.libvirt.start()
        self.addCleanup(self.libvirt.stop)

        self.proctitle = mock.patch('setproctitle.setproctitle')
        self.mock_proctitle = self.proctitle.start()
        self.addCleanup(self.proctitle.stop)
        self.config = mock.patch('shakenfist.daemons.cleaner.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    def test_update_power_states(self, mock_listdir, mock_time, mock_exists):
        for id in ['running', 'shutoff', 'crashed', 'paused', 'suspended']:
            self.mock_etcd.create_instance(
                id, id, set_state=instance.Instance.STATE_CREATED)

        m = cleaner.Monitor('cleaner')
        m._update_power_states()

        for id, state in [('running', 'on'),
                          ('shutoff', 'off'),
                          ('crashed', 'crashed'),
                          ('paused', 'paused'),
                          ('suspended', 'paused')]:
            read_state = json.loads(self.mock_etcd.get(
                f'/sf/attribute/instance/{id}/power_state')[0])
            self.assertEqual(
                state, read_state['power_state'],
                f'State for instance "{id}" does not match "{state}"')
