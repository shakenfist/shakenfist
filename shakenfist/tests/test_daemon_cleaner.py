from unittest import mock

from shakenfist import instance
from shakenfist.config import BaseSettings
from shakenfist.daemons.cleaner import scheduled_tasks as cleaner_st
from shakenfist.tests import base
from shakenfist.tests.mock_etcd import MockEtcd


# Module-level storage for test instance UUIDs that the fake libvirt uses
_test_instance_uuids = {}


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
        # Map domain IDs to (name_key, state) where name_key is used to look
        # up the actual UUID from _test_instance_uuids
        domain_map = {
            'id1': ('running', FakeLibvirt.VIR_DOMAIN_RUNNING),
            'id2': ('apache2', FakeLibvirt.VIR_DOMAIN_RUNNING),  # non-SF domain
            'id3': ('shutoff', FakeLibvirt.VIR_DOMAIN_SHUTOFF),
            'id4': ('crashed', FakeLibvirt.VIR_DOMAIN_CRASHED),
            'id5': ('paused', FakeLibvirt.VIR_DOMAIN_PAUSED),
            'id6': ('suspended', FakeLibvirt.VIR_DOMAIN_PMSUSPENDED),
        }

        name_key, state = domain_map.get(id)
        if name_key == 'apache2':
            # Non-SF domain, return as-is
            return FakeLibvirtDomain('apache2', state)
        # SF domain - use the actual instance UUID
        inst_uuid = _test_instance_uuids.get(name_key, name_key)
        return FakeLibvirtDomain(f'sf:{inst_uuid}', state)

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


fake_config = FakeConfig()


class CleanerTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        self.libvirt = mock.patch(
            'shakenfist.util.libvirt.get_libvirt',
            return_value=FakeLibvirt())
        self.mock_libvirt = self.libvirt.start()
        self.addCleanup(self.libvirt.stop)

        self.config = mock.patch('shakenfist.daemons.cleaner.main.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_etcd = MockEtcd(self, node_count=4)
        self.mock_etcd.setup()

    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_update_power_states(self, mock_unlink, mock_listdir, mock_time,
                                 mock_exists):
        global _test_instance_uuids

        # Create instances and store their UUIDs for later lookup
        instance_uuids = {}
        for name in ['running', 'shutoff', 'crashed', 'paused', 'suspended']:
            inst = self.mock_etcd.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)

        # Populate the module-level dict so FakeLibvirtConnection can find
        # the instance UUIDs
        _test_instance_uuids = instance_uuids

        cleaner_st.update_power_states()

        for name, state in [('running', 'on'),
                            ('shutoff', 'off'),
                            ('crashed', 'crashed'),
                            ('paused', 'paused'),
                            ('suspended', 'paused')]:
            inst_uuid = instance_uuids[name]
            # power_state is now written to MariaDB only (no etcd dual-write)
            inst_attrs = self.mock_etcd.get_mariadb_instance_attributes(
                inst_uuid)
            self.assertIsNotNone(
                inst_attrs,
                f'No MariaDB attributes for instance "{name}"')
            self.assertEqual(
                state, inst_attrs.power_state['power_state'],
                f'State for instance "{name}" does not match "{state}"')
