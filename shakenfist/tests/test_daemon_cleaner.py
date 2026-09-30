import contextlib
import datetime
import os
import tempfile
import uuid
from unittest import mock

import schedule

from shakenfist import eventlog
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import node
from shakenfist.config import BaseSettings
from shakenfist.constants import AGENT_INSTANCE_OFF
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.schema.object_types import ObjectType
from shakenfist.daemons.cleaner import main as cleaner_main
from shakenfist.daemons.cleaner import scheduled_tasks as cleaner_st
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


# Module-level storage for test instance UUIDs that the fake libvirt uses
_test_instance_uuids = {}

# Further domains a test wants the fake libvirt to list, as FakeLibvirtDomain
# objects. Reset by CleanerBaseTestCase.setUp().
_test_extra_domains = []

# The names of the domains undefine() was called on. Reset by
# CleanerBaseTestCase.setUp().
_test_undefined_domains = []

# The libvirt uuid of the fake's one foreign (non-SF) domain.
FOREIGN_DOMAIN_UUID = '0e8b8d47-5b8c-4b0e-9d0a-6f6c1b6f3b1a'


class FakeLibvirtError(Exception):
    """A class of its own, as libvirt's is. Were it Exception, the cleaner's
    `except libvirtError` handlers would swallow every error, including the
    ones these tests exist to see."""


class FakeLibvirt:
    VIR_DOMAIN_BLOCKED = 1
    VIR_DOMAIN_CRASHED = 2
    VIR_DOMAIN_NOSTATE = 3
    VIR_DOMAIN_PAUSED = 4
    VIR_DOMAIN_RUNNING = 5
    VIR_DOMAIN_SHUTDOWN = 6
    VIR_DOMAIN_SHUTOFF = 7
    VIR_DOMAIN_PMSUSPENDED = 8

    VIR_CONNECT_LIST_DOMAINS_ACTIVE = 1
    VIR_CONNECT_LIST_DOMAINS_INACTIVE = 2

    VIR_DOMAIN_PAUSED_USER = 1
    VIR_DOMAIN_PAUSED_IOERROR = 5

    # libvirt's values.
    VIR_DOMAIN_SHUTOFF_UNKNOWN = 0
    VIR_DOMAIN_SHUTOFF_SHUTDOWN = 1
    VIR_DOMAIN_SHUTOFF_DESTROYED = 2
    VIR_DOMAIN_SHUTOFF_CRASHED = 3
    VIR_DOMAIN_SHUTOFF_MIGRATED = 4
    VIR_DOMAIN_SHUTOFF_SAVED = 5
    VIR_DOMAIN_SHUTOFF_FAILED = 6
    VIR_DOMAIN_SHUTOFF_FROM_SNAPSHOT = 7
    VIR_DOMAIN_SHUTOFF_DAEMON = 8

    VIR_DOMAIN_DISK_ERROR_NONE = 0
    VIR_DOMAIN_DISK_ERROR_UNSPEC = 1
    VIR_DOMAIN_DISK_ERROR_NO_SPACE = 2

    libvirtError = FakeLibvirtError

    def open(self, _ignored):
        return FakeLibvirtConnection()


class FakeLibvirtConnection:
    def listAllDomains(self, flags):
        # Mirrors how libvirt's listAllDomains() lists domains, not our
        # domain template: every defined domain, filtered by whether libvirt
        # considers it active. This is why the crashed domain comes back
        # active here even though our hypervisors never produce an active
        # crashed domain in production (on_crash is restart); see D4 in
        # docs/plans/PLAN-power-state-correctness-phase-01a-listing.md.

        # Map domain IDs to (name_key, state, pause_reason) where name_key is
        # used to look up the actual UUID from _test_instance_uuids
        domain_map = {
            'id1': ('running', FakeLibvirt.VIR_DOMAIN_RUNNING,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),
            'id2': ('apache2', FakeLibvirt.VIR_DOMAIN_RUNNING,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),  # non-SF domain
            'id3': ('shutoff', FakeLibvirt.VIR_DOMAIN_SHUTOFF,
                    FakeLibvirt.VIR_DOMAIN_SHUTOFF_SHUTDOWN),
            'id4': ('crashed', FakeLibvirt.VIR_DOMAIN_CRASHED,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),
            'id5': ('paused', FakeLibvirt.VIR_DOMAIN_PAUSED,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),
            'id6': ('suspended', FakeLibvirt.VIR_DOMAIN_PMSUSPENDED,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),
        }
        # The ioerror-paused domain only exists for tests which create an
        # instance for it, so that the other tests don't see an unknown
        # domain (which the cleaner would try to virsh destroy).
        if 'ioerror' in _test_instance_uuids:
            domain_map['id7'] = (
                'ioerror', FakeLibvirt.VIR_DOMAIN_PAUSED,
                FakeLibvirt.VIR_DOMAIN_PAUSED_IOERROR)

        domains = []
        for name_key, state, reason in domain_map.values():
            if name_key == 'apache2':
                # Non-SF domain, return as-is
                domains.append(FakeLibvirtDomain(
                    'apache2', state, uuid=FOREIGN_DOMAIN_UUID))
                continue

            # SF domain - use the actual instance UUID. A domain exists only
            # for the instances a test created: a real domain's name is
            # always "sf:" and a uuid, never "sf:running".
            if name_key not in _test_instance_uuids:
                continue
            inst_uuid = _test_instance_uuids[name_key]
            disk_errors = {}
            if name_key == 'ioerror':
                disk_errors = {
                    'vda': FakeLibvirt.VIR_DOMAIN_DISK_ERROR_UNSPEC,
                    'vdb': FakeLibvirt.VIR_DOMAIN_DISK_ERROR_NONE,
                }
            domains.append(FakeLibvirtDomain(
                f'sf:{inst_uuid}', state, reason=reason,
                disk_errors=disk_errors))

        domains.extend(_test_extra_domains)

        # Flags of 0 means every defined domain, as it does for libvirt.
        if flags == 0:
            return domains

        wants_active = flags == FakeLibvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE
        return [d for d in domains if d.isActive() == wants_active]

    def lookupByName(self, name):
        # The domain listAllDomains() would list under that name, active or
        # not, and libvirt's error if there is none.
        for domain in self.listAllDomains(0):
            if domain.name() == name:
                return domain
        raise FakeLibvirtError(f'Domain not found: no domain with name {name}')

    def close(self):
        pass


class FakeLibvirtDomain:
    def __init__(self, name, state, reason=1, disk_errors=None, uuid=None):
        self._name = name
        self._state = state
        self._reason = reason
        self._disk_errors = disk_errors or {}

        # libvirt.tmpl sets an SF domain's libvirt uuid to the instance uuid,
        # so by default the name's suffix is the uuid. A test can pass a
        # different one.
        if uuid is None and name.startswith('sf:'):
            uuid = name[len('sf:'):]
        self._uuid = uuid

    def name(self):
        return self._name

    def state(self):
        return [self._state, self._reason]

    def diskErrors(self):
        return self._disk_errors

    def UUIDString(self):
        return self._uuid

    def isActive(self):
        # As libvirt, and as FakeLibvirtConnection.listAllDomains() decides.
        return self._state != FakeLibvirt.VIR_DOMAIN_SHUTOFF

    def undefine(self):
        _test_undefined_domains.append(self._name)


class FakeInstanceLocks:
    """Stands in for Instance.get_lock(), which unit tests have no nodelock
    socket for. Records each lock taken and released, and can be told to
    time out the way a bounded NodeLock does."""

    def __init__(self):
        self.get_lock_calls = []
        self.events = []
        self.timeout = False

        # Called with the instance uuid once a lock is held, to stand in for
        # whatever the previous holder did.
        self.on_lock = None

    def get_lock(self, inst, **kwargs):
        self.get_lock_calls.append((str(inst.uuid), kwargs))
        return self._lock(str(inst.uuid))

    @contextlib.contextmanager
    def _lock(self, inst_uuid):
        if self.timeout:
            raise exceptions.NodeLockTimeout(
                f'Timed out waiting for lock instance-{inst_uuid}')
        self.events.append(('lock', inst_uuid))
        try:
            if self.on_lock:
                self.on_lock(inst_uuid)
            yield
        finally:
            self.events.append(('unlock', inst_uuid))

    def locked_uuids(self):
        return [u for u, _ in self.get_lock_calls]


def fake_exists(path):
    if path == '/srv/shakenfist/instances/nofiles':
        return False
    return True


class FakeConfig(BaseSettings):
    NODE_NAME: str = 'abigcomputer'
    STORAGE_PATH: str = '/srv/shakenfist'
    LOGLEVEL_CLEANER: str = 'debug'
    CLEANER_DELAY: int = 3600


fake_config = FakeConfig()


class CleanerBaseTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()

        global _test_instance_uuids
        global _test_extra_domains
        global _test_undefined_domains
        _test_instance_uuids = {}
        _test_extra_domains = []
        _test_undefined_domains = []

        self.libvirt = mock.patch(
            'shakenfist.util.libvirt.get_libvirt',
            return_value=FakeLibvirt())
        self.mock_libvirt = self.libvirt.start()
        self.addCleanup(self.libvirt.stop)

        self.config = mock.patch('shakenfist.daemons.cleaner.main.config',
                                 fake_config)
        self.mock_config = self.config.start()
        self.addCleanup(self.config.stop)

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

        self.locks = FakeInstanceLocks()
        self.get_lock = mock.patch.object(
            instance.Instance, 'get_lock', autospec=True,
            side_effect=self.locks.get_lock)
        self.get_lock.start()
        self.addCleanup(self.get_lock.stop)

    def _create_instances(self, names):
        global _test_instance_uuids

        instance_uuids = {}
        for name in names:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids
        return instance_uuids

    def _power_state(self, inst_uuid):
        attrs = self.mock_mariadb.get_mariadb_instance_attributes(inst_uuid)
        if not attrs or not attrs.power_state:
            return None
        return attrs.power_state.get('power_state')


class CleanerTestCase(CleanerBaseTestCase):
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
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)

        # Populate the module-level dict so FakeLibvirtConnection can find
        # the instance UUIDs
        _test_instance_uuids = instance_uuids

        cleaner_st.update_power_states()

        for name, state in [('running', 'on'),
                            ('crashed', 'crashed'),
                            ('paused', 'paused'),
                            ('suspended', 'paused')]:
            inst_uuid = instance_uuids[name]
            # power_state is now written to MariaDB only (no etcd dual-write)
            inst_attrs = self.mock_mariadb.get_mariadb_instance_attributes(
                inst_uuid)
            self.assertIsNotNone(
                inst_attrs,
                f'No MariaDB attributes for instance "{name}"')
            self.assertEqual(
                state, inst_attrs.power_state['power_state'],
                f'State for instance "{name}" does not match "{state}"')

    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_update_power_states_detects_shutoff(
            self, mock_unlink, mock_listdir, mock_time, mock_exists):
        """A powered off domain's instance is recorded as off (F1).

        get_active_sf_domains() only lists domains libvirt considers
        active, so the cleaner's second loop lists the inactive ones.
        Before it did, a shutoff domain was seen by neither loop and its
        instance kept whatever power_state it had before.
        """
        global _test_instance_uuids

        instance_uuids = {}
        for name in ['running', 'shutoff', 'crashed', 'paused', 'suspended']:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids

        cleaner_st.update_power_states()

        inst_uuid = instance_uuids['shutoff']
        inst_attrs = self.mock_mariadb.get_mariadb_instance_attributes(
            inst_uuid)
        self.assertIsNotNone(
            inst_attrs, 'No MariaDB attributes for instance "shutoff"')
        self.assertEqual('off', inst_attrs.power_state['power_state'])

    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_update_power_states_does_not_enforce_capacity(
            self, mock_unlink, mock_listdir, mock_time, mock_exists):
        """The cleaner's placement writes are ground truth (P5).

        It records where a libvirt domain already is, so the capacity
        guard must not be able to refuse it -- refusing would leave the
        ledger disagreeing with reality, which is strictly worse than a
        node briefly over its limit.
        """
        global _test_instance_uuids

        instance_uuids = {}
        for name in ['running', 'shutoff']:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids

        with mock.patch.object(
                instance.Instance, 'place_instance') as place:
            cleaner_st.update_power_states()

        self.assertTrue(place.called)
        for call in place.call_args_list:
            self.assertFalse(call.kwargs['enforce'])

    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_update_power_states_pets_watchdog(
            self, mock_unlink, mock_listdir, mock_time, mock_exists):
        """The per-domain loops must pet the systemd watchdog.

        The cleaner runs this outside its idle() loop, so without an
        explicit pet a busy pass over many domains overruns the 60s
        systemd watchdog and systemd SIGABRTs the cleaner mid-operation,
        stranding the placement lock it holds.
        """
        global _test_instance_uuids

        instance_uuids = {}
        for name in ['running', 'shutoff', 'crashed', 'paused', 'suspended']:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids

        pet = mock.Mock()
        cleaner_st.update_power_states(pet)

        self.assertTrue(
            pet.called,
            'update_power_states must pet the watchdog while iterating domains')


class CleanerCrashedInstanceTestCase(CleanerTestCase):
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks.util_concurrency.execute')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_crashed_delete_wait_instance_is_marked_deleted(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_execute):
        """A crashed domain whose instance is in delete-wait must really
        transition to deleted. The old code assigned to the returned
        State object's value attribute (inst.state.value = ...), which
        persisted nothing, so the reaped instance stayed in delete-wait
        forever.
        """
        global _test_instance_uuids

        instance_uuids = {}
        for name in ['running', 'shutoff', 'crashed', 'paused', 'suspended']:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids

        crashed = instance.Instance.from_db(instance_uuids['crashed'])
        crashed.state = instance.Instance.STATE_DELETE_WAIT

        cleaner_st.update_power_states()

        # The stray domain was undefined...
        undefines = [c for c in mock_execute.call_args_list
                     if 'virsh undefine' in c[0][0]]
        self.assertEqual(1, len(undefines))
        self.assertIn(instance_uuids['crashed'], undefines[0][0][0])

        # ... and the state change was persisted.
        db_state = self.mock_mariadb.get_mariadb_state(
            ObjectType.INSTANCE, instance_uuids['crashed'])
        self.assertEqual(instance.Instance.STATE_DELETED, db_state['value'])


class CleanerGuardsTestCase(CleanerBaseTestCase):
    """The guards phase 1b adds before the cleaner acts on a domain.

    See D2, D3, D4 and D8 in
    docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md.
    Not a CleanerTestCase subclass, so these run once rather than once per
    subclass.
    """

    def _unknown_running_domain(self):
        """A running SF domain with no instance in the database."""
        global _test_instance_uuids

        unknown_uuid = str(uuid.uuid4())
        _test_instance_uuids = {'running': unknown_uuid}
        return unknown_uuid

    @staticmethod
    def _virsh_commands(mock_execute, inst_uuid):
        return [c[0][0] for c in mock_execute.call_args_list
                if inst_uuid in c[0][0]]

    def test_sf_instance_uuid(self):
        u = str(uuid.uuid4())
        self.assertEqual(u, cleaner_st._sf_instance_uuid(
            FakeLibvirtDomain(f'sf:{u}', FakeLibvirt.VIR_DOMAIN_RUNNING)))

        # The name disagrees with the domain's uuid.
        self.assertIsNone(cleaner_st._sf_instance_uuid(
            FakeLibvirtDomain(f'sf:{u}', FakeLibvirt.VIR_DOMAIN_RUNNING,
                              uuid=str(uuid.uuid4()))))

        # An empty suffix, even one the domain's uuid agrees with.
        self.assertIsNone(cleaner_st._sf_instance_uuid(
            FakeLibvirtDomain('sf:', FakeLibvirt.VIR_DOMAIN_RUNNING, uuid='')))

        # Not one of ours.
        self.assertIsNone(cleaner_st._sf_instance_uuid(
            FakeLibvirtDomain('apache2', FakeLibvirt.VIR_DOMAIN_RUNNING,
                              uuid=FOREIGN_DOMAIN_UUID)))

    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks._delete_instance_files')
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks.util_concurrency.execute')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_mismatched_uuid_domain_is_ignored(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_execute, mock_delete_files):
        """A domain whose name disagrees with its uuid is neither looked up
        nor deleted (D4). Neither uuid has an instance, so without the
        guard the unknown domain branch would destroy it."""
        name_uuid = str(uuid.uuid4())
        domain_uuid = str(uuid.uuid4())
        _test_extra_domains.append(FakeLibvirtDomain(
            f'sf:{name_uuid}', FakeLibvirt.VIR_DOMAIN_RUNNING,
            uuid=domain_uuid))

        with mock.patch.object(
                instance.Instance, 'from_db',
                wraps=instance.Instance.from_db) as mock_from_db:
            cleaner_st.update_power_states()

        looked_up = [str(c[0][0]) for c in mock_from_db.call_args_list]
        self.assertNotIn(name_uuid, looked_up)
        self.assertNotIn(domain_uuid, looked_up)
        self.assertEqual([], mock_execute.call_args_list)
        self.assertEqual([], mock_delete_files.call_args_list)

    @mock.patch('shakenfist.daemons.cleaner.scheduled_tasks.shutil.rmtree')
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks._delete_instance_files')
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks.util_concurrency.execute')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_empty_name_domain_is_ignored(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_execute, mock_delete_files, mock_rmtree):
        """A domain named "sf:" would have the cleaner remove
        STORAGE_PATH/instances/ itself (S6). The fake's uuid for it is the
        empty suffix, so only the empty name check stops it."""
        _test_extra_domains.append(FakeLibvirtDomain(
            'sf:', FakeLibvirt.VIR_DOMAIN_RUNNING))

        cleaner_st.update_power_states()

        self.assertEqual([], mock_execute.call_args_list)
        self.assertEqual([], mock_delete_files.call_args_list)
        self.assertEqual([], mock_rmtree.call_args_list)

    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks._delete_with_kill')
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks.util_concurrency.execute')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_unknown_domain_not_destroyed_when_database_errors(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_execute, mock_kill):
        """A non-retryable database error reads as a miss to from_db()
        (#3373). The strict lookup raises instead, and the running domain
        and its disks survive (D3)."""
        unknown_uuid = self._unknown_running_domain()
        mock_get_instance = self.mock_mariadb._mariadb_get_instance

        def get_instance(inst_uuid, *, strict=False):
            if strict:
                raise exceptions.DatabaseUnavailable('UNKNOWN from sf-database')
            return mock_get_instance(inst_uuid)

        with mock.patch('shakenfist.mariadb.get_instance',
                        side_effect=get_instance) as mock_lookup:
            cleaner_st.update_power_states()

        # The strict lookup was asked, and said no.
        self.assertIn(
            mock.call(uuid.UUID(unknown_uuid), strict=True),
            mock_lookup.call_args_list)
        self.assertEqual(
            [], self._virsh_commands(mock_execute, unknown_uuid))
        self.assertFalse(mock_kill.called)

    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks._delete_with_kill')
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks.util_concurrency.execute')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_unknown_domain_not_destroyed_when_strict_lookup_finds_it(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_execute, mock_kill):
        """If the strict lookup finds the instance the first lookup missed,
        the first lookup was the error, and nothing is deleted (D3)."""
        inst_uuid = self._create_instances(['running'])['running']

        with mock.patch.object(instance.Instance, 'from_db',
                               return_value=None):
            cleaner_st.update_power_states()

        self.assertEqual([], self._virsh_commands(mock_execute, inst_uuid))
        self.assertFalse(mock_kill.called)

    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks._delete_with_kill')
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks._delete_instance_files')
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks.util_concurrency.execute')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_unknown_domain_destroyed_on_strict_miss(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_execute, mock_delete_files, mock_kill):
        """A running SF domain the database really has never heard of is
        still destroyed, as it is today."""
        unknown_uuid = self._unknown_running_domain()

        def execute(cmd, *args, **kwargs):
            # As libvirt would, forget the domain once it is undefined, so
            # the second loop does not find it again.
            global _test_instance_uuids
            if cmd.startswith('virsh undefine'):
                _test_instance_uuids = {}
            return '', ''

        mock_execute.side_effect = execute
        cleaner_st.update_power_states()

        self.assertEqual(
            [f'virsh destroy "sf:{unknown_uuid}"',
             f'virsh undefine --nvram "sf:{unknown_uuid}"'],
            self._virsh_commands(mock_execute, unknown_uuid))
        mock_delete_files.assert_called_once_with(unknown_uuid)

    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_power_state_written_under_lock(
            self, mock_unlink, mock_listdir, mock_time, mock_exists):
        """A changed power state is written holding the instance's node
        lock, with a bounded wait (D2)."""
        inst_uuid = self._create_instances(['running'])['running']

        written = []

        def update_power_state(inst, state):
            written.append((state, list(self.locks.events)))
            return True

        with mock.patch.object(instance.Instance, 'update_power_state',
                               autospec=True,
                               side_effect=update_power_state):
            cleaner_st.update_power_states()

        self.assertEqual(
            [(inst_uuid, {'op': 'Cleaner power state update',
                          'global_scope': False, 'node_timeout': 2})],
            self.locks.get_lock_calls)
        self.assertEqual([('on', [('lock', inst_uuid)])], written)
        self.assertEqual(
            [('lock', inst_uuid), ('unlock', inst_uuid)], self.locks.events)

    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_lock_timeout_leaves_power_state_unwritten(
            self, mock_unlink, mock_listdir, mock_time, mock_exists):
        """F13: a busy instance is skipped for this pass, not written
        without its lock."""
        inst_uuids = self._create_instances(['running', 'paused'])
        before = {u: self._power_state(u) for u in inst_uuids.values()}
        self.locks.timeout = True

        cleaner_st.update_power_states()

        self.assertEqual(
            sorted(inst_uuids.values()), sorted(self.locks.locked_uuids()))
        for inst_uuid in inst_uuids.values():
            self.assertNotIn(self._power_state(inst_uuid), ['on', 'paused'])
            self.assertEqual(before[inst_uuid], self._power_state(inst_uuid))

    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_stale_reading_not_written(
            self, mock_unlink, mock_listdir, mock_time, mock_exists):
        """F13: a domain listed as paused, but powered off by the time the
        cleaner holds the lock, is not recorded as paused. The power off
        held the lock, so the reading from the listing is stale."""
        inst_uuid = self._create_instances(['paused'])['paused']
        inst = instance.Instance.from_db(inst_uuid)
        inst.update_power_state('on')

        powered_off = FakeLibvirtDomain(
            f'sf:{inst_uuid}', FakeLibvirt.VIR_DOMAIN_SHUTOFF)
        with mock.patch.object(FakeLibvirtConnection, 'lookupByName',
                               return_value=powered_off) as mock_lookup:
            cleaner_st.update_power_states()

        mock_lookup.assert_called_once_with(f'sf:{inst_uuid}')
        self.assertEqual([inst_uuid], self.locks.locked_uuids())
        self.assertEqual('on', self._power_state(inst_uuid))

    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_agreeing_power_state_takes_no_lock(
            self, mock_unlink, mock_listdir, mock_time, mock_exists):
        """When the database already agrees with libvirt there is nothing
        to write, so no lock is taken. S14's database load claim rests on
        this."""
        inst_uuids = self._create_instances(['running', 'paused', 'ioerror'])
        for name, state in [('running', 'on'), ('paused', 'paused'),
                            ('ioerror', 'paused')]:
            instance.Instance.from_db(inst_uuids[name]).update_power_state(
                state)
        # An I/O error paused instance already marked errored has nothing
        # left to write either.
        instance.Instance.from_db(inst_uuids['ioerror']).state = \
            instance.Instance.STATE_CREATED_ERROR

        with mock.patch.object(
                instance.Instance, 'update_power_state') as mock_update:
            cleaner_st.update_power_states()

        self.assertEqual([], self.locks.get_lock_calls)
        self.assertFalse(mock_update.called)

    def _run_sweep(self, profiles, instance_dirs=()):
        """Run a pass over apparmor profiles old enough to be swept, and
        return the paths it removed."""
        profile_dir = '/etc/apparmor.d/libvirt'
        existing = {profile_dir}
        for u in instance_dirs:
            existing.add(os.path.join(
                cleaner_st.config.STORAGE_PATH, 'instances', u))

        with mock.patch('os.path.exists', side_effect=existing.__contains__), \
                mock.patch('os.listdir', return_value=profiles), \
                mock.patch('os.stat', return_value=mock.Mock(st_mtime=0)), \
                mock.patch('os.path.isdir', return_value=False), \
                mock.patch('time.time', return_value=10 ** 9), \
                mock.patch('os.unlink') as mock_unlink, \
                mock.patch('shutil.rmtree') as mock_rmtree:
            cleaner_st.update_power_states()

        self.assertFalse(mock_rmtree.called)
        return [c[0][0] for c in mock_unlink.call_args_list]

    def test_sweep_keeps_foreign_domain_profile(self):
        """The apparmor sweep lists every defined domain, including ones
        which are not ours, and keeps their profiles (D8)."""
        undefined_uuid = str(uuid.uuid4())
        removed = self._run_sweep([
            f'libvirt-{FOREIGN_DOMAIN_UUID}',
            f'libvirt-{FOREIGN_DOMAIN_UUID}.files',
            f'libvirt-{undefined_uuid}'])

        # The profile of a domain which is not defined at all is still
        # swept, which shows the sweep ran.
        self.assertEqual(
            [f'/etc/apparmor.d/libvirt/libvirt-{undefined_uuid}'], removed)

    def test_sweep_skipped_when_listing_fails(self):
        """If listing every domain fails, the sweep deletes nothing, rather
        than treating every old profile as an undefined domain's (S7)."""
        undefined_uuid = str(uuid.uuid4())
        with mock.patch(
                'shakenfist.util.libvirt.LibvirtConnection.get_all_domain_uuids',
                side_effect=FakeLibvirtError('listing failed')):
            removed = self._run_sweep([
                f'libvirt-{FOREIGN_DOMAIN_UUID}',
                f'libvirt-{undefined_uuid}'])

        self.assertEqual([], removed)


class CleanerInactiveDomainTestCase(CleanerBaseTestCase):
    """The cleaner's second loop, over powered off domains.

    See brief 5, D2 and D5 to D7 in
    docs/plans/PLAN-power-state-correctness-phase-01b-inactive-domains.md.
    Not a CleanerTestCase subclass, so these run once rather than once per
    subclass.
    """

    def setUp(self):
        super().setUp()

        # Instance directories which do not exist. Everything else does.
        self.missing_paths = set()

        for target, kwargs in [
                ('os.path.exists', {'side_effect': self._exists}),
                ('time.time', {'return_value': 7}),
                ('os.listdir', {'return_value': []}),
                ('os.unlink', {}),
                ('shakenfist.daemons.cleaner.scheduled_tasks.'
                 'util_concurrency.execute', {}),
                ('shakenfist.daemons.cleaner.scheduled_tasks.'
                 '_delete_instance_files', {})]:
            patcher = mock.patch(target, **kwargs)
            started = patcher.start()
            self.addCleanup(patcher.stop)
            if target == 'time.time':
                self.mock_time = started
            elif target.endswith('execute'):
                self.mock_execute = started
            elif target.endswith('_delete_instance_files'):
                self.mock_delete_files = started

    def _exists(self, path):
        return path not in self.missing_paths

    def _inactive_instance(self, state=instance.Instance.STATE_CREATED,
                           power_state='on',
                           reason=FakeLibvirt.VIR_DOMAIN_SHUTOFF_SHUTDOWN):
        """An instance whose domain is defined and powered off."""
        inst = self.mock_mariadb.create_instance(
            'inactive', set_state=state)
        if power_state:
            inst.update_power_state(power_state)
        _test_extra_domains.append(FakeLibvirtDomain(
            f'sf:{inst.uuid}', FakeLibvirt.VIR_DOMAIN_SHUTOFF, reason=reason))
        return str(inst.uuid)

    def _files_missing(self, inst_uuid):
        self.missing_paths.add(
            instance.Instance.from_db(inst_uuid).instance_path)

    def _state(self, inst_uuid):
        return self.mock_mariadb.get_mariadb_state(
            ObjectType.INSTANCE, inst_uuid)['value']

    def _agent_state(self, inst_uuid):
        return instance.Instance.from_db(inst_uuid).agent_state.value

    def _after_grace(self):
        """Move time past the five minute grace the deleting branches give
        a delete in flight."""
        self.mock_time.return_value = 7 + 301

    @staticmethod
    def _events(mock_add_event, message):
        return [c for c in mock_add_event.call_args_list
                if c[0][1] == message]

    @staticmethod
    def _audit_events(mock_add_event):
        """The audit events, leaving out the mutate events which a test's
        own writes add."""
        return [c for c in mock_add_event.call_args_list
                if c[0][0] == EVENT_TYPE_AUDIT]

    def _run(self):
        """Run one pass, recording the events added to instances."""
        with mock.patch.object(instance.Instance, 'add_event') as add_event:
            cleaner_st.update_power_states()
        return add_event

    @staticmethod
    def _active_domain(inst_uuid):
        return FakeLibvirtDomain(
            f'sf:{inst_uuid}', FakeLibvirt.VIR_DOMAIN_RUNNING)

    def test_detected_poweroff(self):
        """A powered off domain's instance is recorded as off, with the
        agent state set and an event carrying libvirt's shutoff reason
        and the power state it replaced (D7)."""
        inst_uuid = self._inactive_instance()

        add_event = self._run()

        self.assertEqual('off', self._power_state(inst_uuid))
        self.assertEqual(
            AGENT_INSTANCE_OFF, self._agent_state(inst_uuid))
        self.assertEqual(
            [mock.call(EVENT_TYPE_AUDIT, 'detected poweroff',
                       extra={'reason': 'shutdown',
                              'previous_power_state': 'on'})],
            self._events(add_event, 'detected poweroff'))
        # The instance's state does not change.
        self.assertEqual(instance.Instance.STATE_CREATED, self._state(inst_uuid))
        self.assertEqual([inst_uuid], self.locks.locked_uuids())

    def test_detected_poweroff_reason_is_the_domains(self):
        """The reason is read from the domain, and is display only: a
        crashed qemu is still off (D7)."""
        inst_uuid = self._inactive_instance(
            power_state='paused', reason=FakeLibvirt.VIR_DOMAIN_SHUTOFF_CRASHED)

        add_event = self._run()

        self.assertEqual('off', self._power_state(inst_uuid))
        self.assertEqual(
            [mock.call(EVENT_TYPE_AUDIT, 'detected poweroff',
                       extra={'reason': 'crashed',
                              'previous_power_state': 'paused'})],
            self._events(add_event, 'detected poweroff'))
        self.assertEqual(instance.Instance.STATE_CREATED, self._state(inst_uuid))

    def test_detected_poweroff_write_order(self):
        """The power state is written before the agent state, and both
        before the event: the functional tests wait for the event, then
        read the power state once (D7)."""
        self._inactive_instance()

        calls = mock.Mock()
        with mock.patch.object(instance.Instance, 'update_power_state',
                               calls.update_power_state), \
                mock.patch.object(instance.Instance, 'agent_state',
                                  new_callable=mock.PropertyMock) as agent, \
                mock.patch.object(instance.Instance, 'add_event',
                                  calls.add_event):
            calls.attach_mock(agent, 'agent_state')
            cleaner_st.update_power_states()

        self.assertEqual(
            [mock.call.update_power_state('off'),
             mock.call.agent_state(AGENT_INSTANCE_OFF),
             mock.call.add_event(
                 EVENT_TYPE_AUDIT, 'detected poweroff',
                 extra={'reason': 'shutdown', 'previous_power_state': 'on'})],
            calls.mock_calls)

    def test_already_off_takes_no_lock(self):
        """When the database already says off there is nothing to write,
        so no lock is taken and no event added (S14)."""
        self._inactive_instance(power_state='off')

        with mock.patch.object(
                instance.Instance, 'update_power_state') as mock_update, \
                mock.patch.object(instance.Instance, 'add_event') as add_event:
            cleaner_st.update_power_states()

        self.assertEqual([], self.locks.get_lock_calls)
        self.assertFalse(mock_update.called)
        self.assertEqual([], add_event.call_args_list)

    def test_instance_being_created_is_skipped(self):
        """A domain whose instance is still being created is left alone
        (S2)."""
        inst_uuids = [
            self._inactive_instance(state=state)
            for state in [instance.Instance.STATE_INITIAL,
                          instance.Instance.STATE_PREFLIGHT,
                          instance.Instance.STATE_CREATING]]
        # Even with its files missing.
        self._files_missing(inst_uuids[0])

        with mock.patch.object(
                instance.Instance, 'place_instance') as mock_place:
            add_event = self._run()

        self.assertEqual([], self.locks.get_lock_calls)
        self.assertFalse(mock_place.called)
        self.assertEqual([], add_event.call_args_list)
        for inst_uuid in inst_uuids:
            self.assertEqual('on', self._power_state(inst_uuid))

    def test_lock_timeout_is_skipped(self):
        """A busy instance is skipped this pass, in every branch which
        writes (D2)."""
        off = self._inactive_instance()
        files_missing = self._inactive_instance()
        self._files_missing(files_missing)
        deleted = self._inactive_instance(state=instance.Instance.STATE_DELETED)
        delete_wait = self._inactive_instance(
            state=instance.Instance.STATE_DELETE_WAIT)
        self._after_grace()
        self.locks.timeout = True

        with mock.patch.object(
                instance.Instance, 'enqueue_delete') as mock_enqueue:
            add_event = self._run()

        self.assertEqual(
            sorted([off, files_missing, deleted, delete_wait]),
            sorted(self.locks.locked_uuids()))
        self.assertEqual('on', self._power_state(off))
        self.assertEqual(instance.Instance.STATE_CREATED,
                         self._state(files_missing))
        self.assertFalse(self.mock_delete_files.called)
        self.assertEqual([], _test_undefined_domains)
        self.assertFalse(mock_enqueue.called)
        self.assertEqual([], add_event.call_args_list)

    def test_domain_active_inside_lock_is_skipped(self):
        """A domain listed as powered off but running by the time the
        cleaner holds the lock is not recorded as off: a power on held the
        lock, so the listing is stale (D2)."""
        inst_uuid = self._inactive_instance()

        with mock.patch.object(
                FakeLibvirtConnection, 'lookupByName',
                return_value=self._active_domain(inst_uuid)) as mock_lookup:
            add_event = self._run()

        mock_lookup.assert_called_once_with(f'sf:{inst_uuid}')
        self.assertEqual([inst_uuid], self.locks.locked_uuids())
        self.assertEqual('on', self._power_state(inst_uuid))
        self.assertEqual([], add_event.call_args_list)

    def test_files_missing_domain_active_inside_lock_is_skipped(self):
        inst_uuid = self._inactive_instance()
        self._files_missing(inst_uuid)

        with mock.patch.object(
                FakeLibvirtConnection, 'lookupByName',
                return_value=self._active_domain(inst_uuid)):
            add_event = self._run()

        self.assertEqual([inst_uuid], self.locks.locked_uuids())
        self.assertEqual(instance.Instance.STATE_CREATED, self._state(inst_uuid))
        self.assertEqual([], add_event.call_args_list)

    def test_deleted_domain_gone_inside_lock_is_skipped(self):
        """If the domain was undefined while the cleaner waited for the
        lock, there is nothing left to tear down."""
        inst_uuid = self._inactive_instance(
            state=instance.Instance.STATE_DELETED)
        self._after_grace()

        with mock.patch.object(
                FakeLibvirtConnection, 'lookupByName',
                side_effect=FakeLibvirtError('Domain not found')):
            add_event = self._run()

        self.assertEqual([inst_uuid], self.locks.locked_uuids())
        self.assertFalse(self.mock_delete_files.called)
        self.assertEqual([], _test_undefined_domains)
        self.assertEqual([], add_event.call_args_list)

    def test_delete_wait_domain_gone_inside_lock_is_skipped(self):
        inst_uuid = self._inactive_instance(
            state=instance.Instance.STATE_DELETE_WAIT)
        self._after_grace()

        with mock.patch.object(
                FakeLibvirtConnection, 'lookupByName',
                side_effect=FakeLibvirtError('Domain not found')), \
                mock.patch.object(
                    instance.Instance, 'enqueue_delete') as mock_enqueue:
            self._run()

        self.assertEqual([inst_uuid], self.locks.locked_uuids())
        self.assertFalse(mock_enqueue.called)
        self.assertEqual(
            {}, instance.Instance.from_db(inst_uuid).enforced_deletes or {})

    def test_state_changed_inside_lock_is_skipped(self):
        """Each branch acts on the state it read inside the lock. The
        previous holder may have finished the delete, or started one."""
        delete_wait = self._inactive_instance(
            state=instance.Instance.STATE_DELETE_WAIT)
        files_missing = self._inactive_instance()
        self._files_missing(files_missing)
        self._after_grace()

        def on_lock(inst_uuid):
            inst = instance.Instance.from_db(inst_uuid)
            if inst_uuid == delete_wait:
                inst.state = instance.Instance.STATE_DELETED
            else:
                inst.state = instance.Instance.STATE_DELETE_WAIT

        self.locks.on_lock = on_lock
        with mock.patch.object(
                instance.Instance, 'enqueue_delete') as mock_enqueue:
            add_event = self._run()

        self.assertEqual(
            sorted([delete_wait, files_missing]),
            sorted(self.locks.locked_uuids()))
        self.assertFalse(mock_enqueue.called)
        self.assertFalse(self.mock_delete_files.called)
        self.assertEqual([], _test_undefined_domains)
        self.assertEqual(
            instance.Instance.STATE_DELETE_WAIT, self._state(files_missing))
        self.assertEqual([], self._audit_events(add_event))

    def test_files_restored_inside_lock_is_skipped(self):
        """Files missing is re-read inside the lock too."""
        inst_uuid = self._inactive_instance()
        self._files_missing(inst_uuid)
        self.locks.on_lock = lambda _: self.missing_paths.clear()

        add_event = self._run()

        self.assertEqual([inst_uuid], self.locks.locked_uuids())
        self.assertEqual(instance.Instance.STATE_CREATED, self._state(inst_uuid))
        self.assertEqual([], self._audit_events(add_event))

    def test_domain_seen_active_this_pass_waits(self):
        """A domain the first loop recorded as running, and which powered
        off before the second loop listed it, waits for the next pass."""
        inst_uuid = self._create_instances(['running'])['running']
        running = self._active_domain(inst_uuid)
        stopped = FakeLibvirtDomain(
            f'sf:{inst_uuid}', FakeLibvirt.VIR_DOMAIN_SHUTOFF)

        def list_all_domains(conn, flags):
            if flags == FakeLibvirt.VIR_CONNECT_LIST_DOMAINS_ACTIVE:
                return [running]
            return [stopped]

        with mock.patch.object(FakeLibvirtConnection, 'listAllDomains',
                               autospec=True, side_effect=list_all_domains), \
                mock.patch.object(FakeLibvirtConnection, 'lookupByName',
                                  side_effect=[running, stopped]):
            add_event = self._run()

        self.assertEqual('on', self._power_state(inst_uuid))
        self.assertEqual([], self._events(add_event, 'detected poweroff'))

    def test_power_off_recorded_inside_lock_is_not_detected(self):
        """A power off through the API holds the lock and records itself.
        The cleaner which waited on it does not add a detected poweroff."""
        inst_uuid = self._inactive_instance()

        def on_lock(inst_uuid):
            instance.Instance.from_db(inst_uuid).update_power_state('off')

        self.locks.on_lock = on_lock
        with mock.patch.object(
                instance.Instance, 'update_power_state',
                autospec=True,
                side_effect=instance.Instance.update_power_state) as update:
            add_event = self._run()

        self.assertEqual([inst_uuid], self.locks.locked_uuids())
        # Only the previous holder's write.
        self.assertEqual(1, update.call_count)
        self.assertEqual([], self._audit_events(add_event))
        self.assertIsNone(self._agent_state(inst_uuid))

    def test_delete_wait_enqueue_schedule(self):
        """A stray delete-wait domain has its delete enqueued on the first,
        sixth and eleventh passes, is given up on at the sixteenth, and is
        never deleted in place, which would leak its interfaces (S3, D5)."""
        inst_uuid = self._inactive_instance(
            state=instance.Instance.STATE_DELETE_WAIT)
        self._after_grace()

        enqueued = []
        abandoned = []
        for attempt in range(1, 21):
            with mock.patch.object(
                    instance.Instance, 'enqueue_delete') as mock_enqueue, \
                    mock.patch.object(
                        instance.Instance, 'delete') as mock_delete:
                add_event = self._run()

            self.assertFalse(mock_delete.called)
            if mock_enqueue.called:
                self.assertEqual(1, mock_enqueue.call_count)
                self.assertEqual(
                    [mock.call(EVENT_TYPE_AUDIT,
                               'stray powered off instance delete enqueued',
                               extra={'attempt': attempt})],
                    self._events(
                        add_event,
                        'stray powered off instance delete enqueued'))
                enqueued.append(attempt)
            abandon = self._events(
                add_event, 'stray powered off instance delete abandoned')
            if abandon:
                self.assertEqual(
                    [mock.call(EVENT_TYPE_AUDIT,
                               'stray powered off instance delete abandoned',
                               extra={'attempt': attempt}, log_as_error=True)],
                    abandon)
                abandoned.append(attempt)

        self.assertEqual([1, 6, 11], enqueued)
        self.assertEqual([16], abandoned)
        self.assertEqual(
            instance.Instance.STATE_DELETE_WAIT, self._state(inst_uuid))
        self.assertFalse(self.mock_delete_files.called)
        self.assertEqual([], _test_undefined_domains)

    def test_delete_wait_within_grace_is_left_alone(self):
        self._inactive_instance(state=instance.Instance.STATE_DELETE_WAIT)

        with mock.patch.object(
                instance.Instance, 'enqueue_delete') as mock_enqueue:
            self._run()

        self.assertEqual([], self.locks.get_lock_calls)
        self.assertFalse(mock_enqueue.called)

    def test_deleted_is_torn_down_without_state_write(self):
        """A deleted instance's lingering domain and files are removed
        locally, holding its lock, and its state is not written (S4)."""
        inst_uuid = self._inactive_instance(
            state=instance.Instance.STATE_DELETED)
        self._after_grace()

        with mock.patch.object(
                instance.Instance, '_state_update', autospec=True,
                side_effect=cleaner_st.dbo._state_update) as mock_state:
            add_event = self._run()

        self.assertEqual([inst_uuid], self.locks.locked_uuids())
        self.mock_delete_files.assert_called_once_with(inst_uuid)
        self.assertEqual([f'sf:{inst_uuid}'], _test_undefined_domains)
        self.assertEqual(
            [mock.call(EVENT_TYPE_AUDIT, 'deleted stray instance')],
            self._events(add_event, 'deleted stray instance'))
        self.assertEqual(
            [], [c for c in mock_state.call_args_list
                 if str(c[0][0].uuid) == inst_uuid])
        self.assertEqual(
            instance.Instance.STATE_DELETED, self._state(inst_uuid))

    def test_files_missing_marks_created_errored(self):
        """An instance whose domain is powered off and whose files are
        gone moves to its error state, with a message (D6)."""
        inst_uuid = self._inactive_instance()
        self._files_missing(inst_uuid)

        add_event = self._run()

        self.assertEqual(
            instance.Instance.STATE_CREATED_ERROR, self._state(inst_uuid))
        self.assertEqual(
            'instance files missing',
            instance.Instance.from_db(inst_uuid).error)
        self.assertEqual(
            1, len(self._events(add_event, 'instance files missing')))
        self.assertEqual([inst_uuid], self.locks.locked_uuids())

    def test_files_missing_from_error_states_does_nothing(self):
        """An errored instance is not errored again: 'error-error' and
        'created-error-error' are not valid transitions, and would raise
        on every pass (S8, D6)."""
        inst_uuids = {
            state: self._inactive_instance(state=state)
            for state in [instance.Instance.STATE_ERROR,
                          instance.Instance.STATE_CREATED_ERROR]}
        for inst_uuid in inst_uuids.values():
            self._files_missing(inst_uuid)

        add_event = self._run()

        for state, inst_uuid in inst_uuids.items():
            self.assertEqual(state, self._state(inst_uuid))
        self.assertEqual([], self.locks.get_lock_calls)
        self.assertEqual([], add_event.call_args_list)

    def test_unknown_domain_not_removed_when_database_errors(self):
        """A non-retryable database error reads as a miss to from_db()
        (#3373). The strict lookup raises instead, and the powered off
        domain and its disks survive (D3)."""
        unknown_uuid = str(uuid.uuid4())
        _test_extra_domains.append(FakeLibvirtDomain(
            f'sf:{unknown_uuid}', FakeLibvirt.VIR_DOMAIN_SHUTOFF))
        mock_get_instance = self.mock_mariadb._mariadb_get_instance

        def get_instance(inst_uuid, *, strict=False):
            if strict:
                raise exceptions.DatabaseUnavailable('UNKNOWN from sf-database')
            return mock_get_instance(inst_uuid)

        with mock.patch('shakenfist.mariadb.get_instance',
                        side_effect=get_instance) as mock_lookup:
            cleaner_st.update_power_states()

        self.assertIn(
            mock.call(uuid.UUID(unknown_uuid), strict=True),
            mock_lookup.call_args_list)
        self.assertFalse(self.mock_delete_files.called)
        self.assertEqual([], _test_undefined_domains)
        self.assertEqual([], self.mock_execute.call_args_list)

    def test_unknown_domain_removed_on_strict_miss(self):
        """A powered off SF domain the database really has never heard of
        is removed, as the second loop always meant to."""
        unknown_uuid = str(uuid.uuid4())
        _test_extra_domains.append(FakeLibvirtDomain(
            f'sf:{unknown_uuid}', FakeLibvirt.VIR_DOMAIN_SHUTOFF))

        cleaner_st.update_power_states()

        self.mock_delete_files.assert_called_once_with(unknown_uuid)
        self.assertEqual([f'sf:{unknown_uuid}'], _test_undefined_domains)

    def test_delete_with_virsh_without_instance_succeeds(self):
        """A successful virsh delete of an unknown domain reports success,
        so its caller does not go on to SIGKILL."""
        inst_uuid = str(uuid.uuid4())
        self.assertTrue(cleaner_st._delete_with_virsh(inst_uuid, None))
        self.assertEqual(
            [mock.call(f'virsh destroy "sf:{inst_uuid}"'),
             mock.call(f'virsh undefine --nvram "sf:{inst_uuid}"')],
            self.mock_execute.call_args_list)
        self.mock_delete_files.assert_called_once_with(inst_uuid)


class MaintainBlobsSentinelTestCase(base.ShakenFistTestCase):
    """_maintain_blobs must neither delete nor crash on the resource
    health _heartbeat sentinel, even one that has gone stale because its
    store stopped being writable (github issue 3490)."""

    @mock.patch('shakenfist.daemons.cleaner.main.mariadb')
    @mock.patch('shakenfist.daemons.cleaner.main.node')
    def test_stale_heartbeat_sentinels_survive(self, mock_node, mock_mariadb):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)

        blob_dir = os.path.join(tempdir.name, 'blobs')
        cache_dir = os.path.join(tempdir.name, 'image_cache')
        os.makedirs(blob_dir)
        os.makedirs(cache_dir)

        blob_heartbeat = os.path.join(blob_dir, '_heartbeat')
        cache_heartbeat = os.path.join(cache_dir, '_heartbeat')
        for path in [blob_heartbeat, cache_heartbeat]:
            with open(path, 'w') as f:
                f.write('1\n')
            # Much older than 2 * CLEANER_DELAY: a stale sentinel on an
            # unhealthy store.
            os.utime(path, (0, 0))

        mock_mariadb.get_active_blob_uuids.return_value = []
        mock_mariadb.get_node_blob_uuids.return_value = []
        mock_node.Node.from_db.return_value = mock.MagicMock()

        m = cleaner_main.Monitor.__new__(cleaner_main.Monitor)
        m.pet_watchdog = mock.MagicMock()

        with mock.patch('shakenfist.daemons.cleaner.main.config',
                        FakeConfig(STORAGE_PATH=tempdir.name)):
            m._maintain_blobs()

        self.assertTrue(os.path.exists(blob_heartbeat))
        self.assertTrue(os.path.exists(cache_heartbeat))

    @mock.patch('shakenfist.daemons.cleaner.main.Blob')
    @mock.patch('shakenfist.daemons.cleaner.main.mariadb')
    @mock.patch('shakenfist.daemons.cleaner.main.node')
    def test_stale_uuid_named_files_still_deleted(
            self, mock_node, mock_mariadb, mock_blob):
        """The UUID-shape filter must not stop legitimate garbage
        collection: stale orphans, partial transfers and dangling
        UUID-named symlinks are still removed; only non-object names
        are exempt."""
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)

        blob_dir = os.path.join(tempdir.name, 'blobs')
        cache_dir = os.path.join(tempdir.name, 'image_cache')
        shard = os.path.join(blob_dir, 'ab')
        os.makedirs(shard)
        os.makedirs(cache_dir)

        orphan = os.path.join(shard, '12345678-1234-4321-8234-123456789012')
        partial = os.path.join(
            shard, '87654321-4321-1234-8234-210987654321.partial')
        cache_orphan = os.path.join(
            cache_dir, 'abcdefab-1234-4321-8234-123456789012.qcow2')
        for path in [orphan, partial, cache_orphan]:
            with open(path, 'w') as f:
                f.write('...')
            os.utime(path, (0, 0))

        # A dangling image cache symlink with a non-object name must
        # survive; a UUID-named one is still cleaned up.
        dangling_kept = os.path.join(cache_dir, '_stale_probe')
        dangling_removed = os.path.join(
            cache_dir, '99999999-9999-4999-8999-999999999999.qcow2')
        os.symlink(os.path.join(tempdir.name, 'nonexistent'), dangling_kept)
        os.symlink(os.path.join(tempdir.name, 'nonexistent'), dangling_removed)

        mock_mariadb.get_active_blob_uuids.return_value = []
        mock_mariadb.get_node_blob_uuids.return_value = []
        mock_node.Node.from_db.return_value = mock.MagicMock()
        mock_blob.from_db.return_value = None

        m = cleaner_main.Monitor.__new__(cleaner_main.Monitor)
        m.pet_watchdog = mock.MagicMock()

        with mock.patch('shakenfist.daemons.cleaner.main.config',
                        FakeConfig(STORAGE_PATH=tempdir.name)):
            m._maintain_blobs()

        self.assertFalse(os.path.exists(orphan))
        self.assertFalse(os.path.exists(partial))
        self.assertFalse(os.path.exists(cache_orphan))
        self.assertFalse(os.path.lexists(dangling_removed))
        self.assertTrue(os.path.lexists(dangling_kept))

    @mock.patch('shakenfist.daemons.cleaner.main.Blob')
    @mock.patch('shakenfist.daemons.cleaner.main.mariadb')
    @mock.patch('shakenfist.daemons.cleaner.main.node')
    def test_unreadable_active_list_deletes_nothing(
            self, mock_node, mock_mariadb, mock_blob):
        """An unreadable active-blob list must not empty the blob store.

        _maintain_blobs uses the active list as a complement set: every
        blob file whose uuid is absent from it is unlinked. While
        get_active_blob_uuids() flattened a failed read to [] (#3638), a
        single oversized RESOURCE_EXHAUSTED reply therefore read as "no
        blobs are active" and instructed this pass to delete every blob
        on the node. The pass must be skipped instead.
        """
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)

        blob_dir = os.path.join(tempdir.name, 'blobs')
        cache_dir = os.path.join(tempdir.name, 'image_cache')
        shard = os.path.join(blob_dir, 'ab')
        os.makedirs(shard)
        os.makedirs(cache_dir)

        # A healthy blob, old enough to be collected had it genuinely
        # been absent from the active list.
        live_blob_uuid = '12345678-1234-4321-8234-123456789012'
        live_blob = os.path.join(shard, live_blob_uuid)
        with open(live_blob, 'w') as f:
            f.write('...')
        os.utime(live_blob, (0, 0))

        mock_mariadb.get_active_blob_uuids.side_effect = (
            exceptions.DatabaseUnavailable(
                'could not read the list of active blobs'))

        m = cleaner_main.Monitor.__new__(cleaner_main.Monitor)
        m.pet_watchdog = mock.MagicMock()

        with mock.patch('shakenfist.daemons.cleaner.main.config',
                        FakeConfig(STORAGE_PATH=tempdir.name)):
            m._maintain_blobs()

        self.assertTrue(os.path.exists(live_blob))
        # The pass is abandoned at the failed read, before any other
        # database work happens. Asserting that is what distinguishes
        # "skipped the pass" from "walked the store and happened to
        # delete nothing"; the complementary case, where a genuinely
        # empty list does delete the file, is covered by
        # test_stale_uuid_named_files_still_deleted.
        mock_node.Node.from_db.assert_not_called()
        mock_blob.from_db.assert_not_called()

    @mock.patch('shakenfist.daemons.cleaner.main.Blob')
    @mock.patch('shakenfist.daemons.cleaner.main.mariadb')
    @mock.patch('shakenfist.daemons.cleaner.main.node')
    def test_unreadable_node_blob_list_deletes_nothing(
            self, mock_node, mock_mariadb, mock_blob):
        """The *other* operand of the deletion decision, hardened too.

        The test is an OR over two lists, so hardening only the active
        list left the store fully deletable through the node's own blob
        locations. That read is the likelier one to fail in practice: it
        goes to MariaDB directly, so a lock wait timeout or a dropped
        connection breaks it while sf-database itself stays healthy and
        answers every other request normally.

        The active list deliberately succeeds here, and returns a list
        that does *not* contain the blob, so the first operand alone
        would still unlink it. Only the second skip can save it.
        """
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)

        blob_dir = os.path.join(tempdir.name, 'blobs')
        cache_dir = os.path.join(tempdir.name, 'image_cache')
        shard = os.path.join(blob_dir, 'ab')
        os.makedirs(shard)
        os.makedirs(cache_dir)

        live_blob_uuid = '12345678-1234-4321-8234-123456789012'
        live_blob = os.path.join(shard, live_blob_uuid)
        with open(live_blob, 'w') as f:
            f.write('...')
        os.utime(live_blob, (0, 0))

        mock_mariadb.get_active_blob_uuids.return_value = []
        mock_mariadb.get_node_blob_uuids.side_effect = (
            exceptions.DatabaseUnavailable(
                'could not read the blob locations for node sf-1'))
        mock_node.Node.from_db.return_value = mock.MagicMock()

        m = cleaner_main.Monitor.__new__(cleaner_main.Monitor)
        m.pet_watchdog = mock.MagicMock()

        with mock.patch('shakenfist.daemons.cleaner.main.config',
                        FakeConfig(STORAGE_PATH=tempdir.name)):
            m._maintain_blobs()

        self.assertTrue(os.path.exists(live_blob))
        # As above, proving the pass was abandoned at the failed read
        # rather than having walked the store and deleted nothing.
        mock_blob.from_db.assert_not_called()


class ResilientJobTestCase(base.ShakenFistTestCase):
    """A raising scheduled task must not starve the cleaner's scheduler.

    schedule.Job.run() only reschedules after job_func returns, so an
    unwrapped raising job stays permanently overdue, sorts first in
    run_pending(), and aborts every tick before any other job runs
    (github issue 3490).
    """

    def test_failing_job_does_not_starve_others(self):
        ran = []

        def failing():
            ran.append('failing')
            raise ValueError('badly formed hexadecimal UUID string')

        def healthy():
            ran.append('healthy')

        sched = schedule.Scheduler()
        sched.every(5).minutes.do(cleaner_main._resilient_job(failing))
        sched.every(1).minutes.do(cleaner_main._resilient_job(healthy))

        # Force both jobs due, with the failing job sorting first --
        # exactly the wedged state from the issue.
        past = datetime.datetime.now() - datetime.timedelta(hours=1)
        sched.jobs[0].next_run = past
        sched.jobs[1].next_run = past + datetime.timedelta(minutes=1)

        sched.run_pending()

        # Both jobs ran despite the first raising...
        self.assertEqual(['failing', 'healthy'], ran)

        # ... and both were rescheduled into the future, so neither is
        # permanently overdue.
        now = datetime.datetime.now()
        for job in sched.jobs:
            self.assertGreater(job.next_run, now)

    def test_resilient_job_passes_arguments(self):
        recorded = []
        cleaner_main._resilient_job(recorded.append, 'petted')()
        self.assertEqual(['petted'], recorded)


class CleanerIOErrorPausedInstanceTestCase(CleanerTestCase):
    """A domain paused by qemu because a disk operation failed
    (error_policy='stop' in the domain XML, or qemu's default ENOSPC
    write handling) must surface as an instance error rather than
    sitting indistinguishable from an operator pause. The sf-6 blob
    NVMe failure of 2026-07-19 ran for six hours with guests taking
    EIO while the power state poller saw only 'running'/'paused'.
    """

    @mock.patch('shakenfist.instance.Instance.enqueue_delete')
    @mock.patch('shakenfist.instance.Instance.add_event')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_ioerror_paused_instance_is_errored(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_add_event, mock_enqueue_delete):
        global _test_instance_uuids

        instance_uuids = {}
        for name in ['running', 'shutoff', 'crashed', 'paused', 'suspended',
                     'ioerror']:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids

        cleaner_st.update_power_states()

        # The I/O error paused instance was marked errored (terminal) with the
        # per-disk detail recorded, but NOT auto-deleted -- an errored instance
        # can still be snapshotted, so we leave the teardown to the operator.
        db_state = self.mock_mariadb.get_mariadb_state(
            ObjectType.INSTANCE, instance_uuids['ioerror'])
        self.assertEqual(
            instance.Instance.STATE_CREATED_ERROR, db_state['value'])
        inst = instance.Instance.from_db(instance_uuids['ioerror'])
        self.assertIn('vda: unspecified error', inst.error)
        self.assertNotIn('vdb', inst.error)
        self.assertFalse(mock_enqueue_delete.called)

        io_events = [c for c in mock_add_event.call_args_list
                     if 'paused by disk I/O error' in c[0][1]]
        self.assertEqual(1, len(io_events))

        # The operator-paused instance was left alone.
        db_state = self.mock_mariadb.get_mariadb_state(
            ObjectType.INSTANCE, instance_uuids['paused'])
        self.assertEqual(instance.Instance.STATE_CREATED, db_state['value'])

    @mock.patch('shakenfist.instance.Instance.enqueue_delete')
    @mock.patch('shakenfist.instance.Instance.add_event')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_ioerror_paused_errored_instance_not_errored_again(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_add_event, mock_enqueue_delete):
        """The paused domain lingers until the operator deletes it, so the
        poller sees it again every pass; it must not stack another -error
        suffix (an invalid transition that would raise) or re-emit the event.
        """
        global _test_instance_uuids

        instance_uuids = {}
        for name in ['running', 'shutoff', 'crashed', 'paused', 'suspended',
                     'ioerror']:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids

        ioerror = instance.Instance.from_db(instance_uuids['ioerror'])
        ioerror.state = instance.Instance.STATE_CREATED_ERROR

        cleaner_st.update_power_states()

        db_state = self.mock_mariadb.get_mariadb_state(
            ObjectType.INSTANCE, instance_uuids['ioerror'])
        self.assertEqual(
            instance.Instance.STATE_CREATED_ERROR, db_state['value'])
        self.assertFalse(mock_enqueue_delete.called)
        io_events = [c for c in mock_add_event.call_args_list
                     if 'paused by disk I/O error' in c[0][1]]
        self.assertEqual(0, len(io_events))

    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks.util_concurrency.execute')
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks.shutil.rmtree')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_ioerror_paused_delete_wait_instance_is_destroyed(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_rmtree, mock_execute):
        """Unlike a crashed domain, an I/O error paused domain still has a
        qemu process, so the delete-wait path must destroy it, not just
        undefine it."""
        global _test_instance_uuids

        instance_uuids = {}
        for name in ['running', 'shutoff', 'crashed', 'paused', 'suspended',
                     'ioerror']:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids

        ioerror = instance.Instance.from_db(instance_uuids['ioerror'])
        ioerror.state = instance.Instance.STATE_DELETE_WAIT

        cleaner_st.update_power_states()

        destroys = [c for c in mock_execute.call_args_list
                    if 'virsh destroy' in c[0][0] and
                    instance_uuids['ioerror'] in c[0][0]]
        self.assertEqual(1, len(destroys))

        db_state = self.mock_mariadb.get_mariadb_state(
            ObjectType.INSTANCE, instance_uuids['ioerror'])
        self.assertEqual(instance.Instance.STATE_DELETED, db_state['value'])

    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks._delete_with_kill')
    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks._delete_with_virsh',
        return_value=False)
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_ioerror_paused_delete_wait_falls_back_to_kill(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_virsh, mock_kill):
        """If virsh cannot destroy the I/O error paused domain (its qemu
        may be wedged in the hung storage), the delete-wait path must
        fall back to the SIGKILL method and still mark the instance
        deleted."""
        global _test_instance_uuids

        instance_uuids = {}
        for name in ['running', 'shutoff', 'crashed', 'paused', 'suspended',
                     'ioerror']:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids

        ioerror = instance.Instance.from_db(instance_uuids['ioerror'])
        ioerror.state = instance.Instance.STATE_DELETE_WAIT

        cleaner_st.update_power_states()

        kills = [c for c in mock_kill.call_args_list
                 if c[0][0] == instance_uuids['ioerror']]
        self.assertEqual(1, len(kills))

        db_state = self.mock_mariadb.get_mariadb_state(
            ObjectType.INSTANCE, instance_uuids['ioerror'])
        self.assertEqual(instance.Instance.STATE_DELETED, db_state['value'])

    @mock.patch(
        'shakenfist.daemons.cleaner.scheduled_tasks.util_concurrency.execute')
    @mock.patch('os.path.exists', side_effect=fake_exists)
    @mock.patch('time.time', return_value=7)
    @mock.patch('os.listdir', return_value=[])
    @mock.patch('os.unlink')
    def test_operator_paused_delete_wait_instance_not_destroyed(
            self, mock_unlink, mock_listdir, mock_time, mock_exists,
            mock_execute):
        """The destroy-on-delete-wait branch is specific to I/O error
        pauses: an operator paused instance in delete-wait must be left
        to the normal queued delete flow, not destroyed by the poller."""
        global _test_instance_uuids

        instance_uuids = {}
        for name in ['running', 'shutoff', 'crashed', 'paused', 'suspended']:
            inst = self.mock_mariadb.create_instance(
                name, set_state=instance.Instance.STATE_CREATED)
            instance_uuids[name] = str(inst.uuid)
        _test_instance_uuids = instance_uuids

        paused = instance.Instance.from_db(instance_uuids['paused'])
        paused.state = instance.Instance.STATE_DELETE_WAIT

        cleaner_st.update_power_states()

        destroys = [c for c in mock_execute.call_args_list
                    if 'virsh destroy' in c[0][0] and
                    instance_uuids['paused'] in c[0][0]]
        self.assertEqual(0, len(destroys))

        db_state = self.mock_mariadb.get_mariadb_state(
            ObjectType.INSTANCE, instance_uuids['paused'])
        self.assertEqual(
            instance.Instance.STATE_DELETE_WAIT, db_state['value'])


class CleanerNodeSelfLookupTestCase(base.ShakenFistTestCase):
    """The cleaner's lookup of its own node record can miss.

    Between the daemon starting and sf-resources writing the node row,
    every cleaner pass looks up a node which does not exist yet (and the
    same happens if the node is removed from the cluster while the
    daemon runs). That is anticipated and handled, so the lookup must
    pass suppress_failure_audit -- otherwise baseobject audits it and
    every restart logs "attempt to lookup non-existent object"
    (github issue 3704).
    """

    def setUp(self):
        super().setUp()

        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

        self.config = mock.patch(
            'shakenfist.daemons.cleaner.main.config',
            FakeConfig(STORAGE_PATH=self.tempdir.name))
        self.config.start()
        self.addCleanup(self.config.stop)

        # None of the mocked nodes are named 'abigcomputer', so the
        # cleaner's self lookup genuinely misses.
        self.mock_mariadb = MockMariaDB(self, node_count=2)
        self.mock_mariadb.setup()

    def _lookup_failure_audits(self):
        return [c for c in eventlog.add_event_multi.call_args_list
                if 'attempt to lookup non-existent object' in c[0][2]]

    def _create_this_node(self):
        self.mock_mariadb._mariadb_create_node(
            uuid.uuid4(), fake_config.NODE_NAME, '10.0.0.42',
            node.Node.current_version)

    @mock.patch('shakenfist.mariadb.get_active_blob_uuids', return_value=[])
    def test_maintain_blobs_absent_node(self, mock_active_blobs):
        m = cleaner_main.Monitor.__new__(cleaner_main.Monitor)
        m.pet_watchdog = mock.MagicMock()

        m._maintain_blobs()

        self.assertEqual([], self._lookup_failure_audits())

    def test_find_missing_blobs_absent_node(self):
        m = cleaner_main.Monitor.__new__(cleaner_main.Monitor)
        m.pet_watchdog = mock.MagicMock()

        m._find_missing_blobs()

        self.assertEqual([], self._lookup_failure_audits())

    def test_run_inner_absent_node(self):
        """The startup lookup must not audit, and must be retried.

        _run_inner looks its node up once before entering the loop
        purely to attribute recorded operations. A startup miss used to
        both log an ERROR and leave the attribution permanently None.
        """
        m = cleaner_main.Monitor.__new__(cleaner_main.Monitor)
        m.abort_path = '/does/not/exist'
        m.pet_watchdog = mock.MagicMock()
        m.wait_for_nodelock = mock.MagicMock()
        m.cluster_stable = mock.MagicMock(return_value=True)
        m.idle = mock.MagicMock()
        m._maintain_blobs = mock.MagicMock()
        m._find_missing_blobs = mock.MagicMock()

        # Two passes: the node record appears between them, as it does
        # once sf-resources catches up.
        passes = [True, True, False]

        def fake_check_abort_path(_path):
            keep_going = passes.pop(0)
            if keep_going and len(passes) == 1:
                self._create_this_node()
            return keep_going

        with mock.patch('shakenfist.daemons.cleaner.main.schedule'), \
                mock.patch(
                    'shakenfist.daemons.cleaner.main.scheduled_tasks'), \
                mock.patch(
                    'shakenfist.daemons.cleaner.main.util_general.'
                    'RecordedOperation') as mock_recorded, \
                mock.patch(
                    'shakenfist.daemons.cleaner.main.daemon.check_abort_path',
                    side_effect=fake_check_abort_path):
            m._run_inner()

        self.assertEqual([], self._lookup_failure_audits())

        # The first pass had no node to attribute operations to, but the
        # second one did.
        attributions = [c[0][1] for c in mock_recorded.call_args_list
                        if c[0][0] == 'maintain blobs']
        self.assertEqual(2, len(attributions))
        self.assertIsNone(attributions[0])
        self.assertIsNotNone(attributions[1])
        self.assertEqual(fake_config.NODE_NAME, attributions[1].fqdn)


class CleanerWatchdogTestCase(base.ShakenFistTestCase):
    """``_maintain_blobs`` globs the on-disk blob directory and does
    per-blob work; on a large node it can run long before control returns
    to idle(60). It must pet the systemd watchdog per blob so it survives
    WatchdogSec once that is armed."""

    @mock.patch('shakenfist.daemons.cleaner.main.config', fake_config)
    @mock.patch('shakenfist.daemons.cleaner.main.os.makedirs')
    @mock.patch('shakenfist.daemons.cleaner.main.os.listdir', return_value=[])
    @mock.patch('shakenfist.daemons.cleaner.main.mariadb')
    @mock.patch('shakenfist.daemons.cleaner.main.node')
    def test_maintain_blobs_pets_per_blob(self, mock_node, mock_mariadb,
                                          mock_listdir, mock_makedirs):
        m = cleaner_main.Monitor.__new__(cleaner_main.Monitor)
        m.pet_watchdog = mock.MagicMock()

        mock_mariadb.get_active_blob_uuids.return_value = []
        fake_node = mock.MagicMock()
        fake_node.blobs = []
        mock_node.Node.from_db.return_value = fake_node

        # Two on-disk entries that are not regular files, so no destructive
        # work happens; we only need to confirm the pet fires per entry.
        # The code calls str(entpath), so plain strings are sufficient.
        entries = ['/srv/shakenfist/blobs/aa/blob-a',
                   '/srv/shakenfist/blobs/bb/blob-b']

        with mock.patch('shakenfist.daemons.cleaner.main.pathlib.Path') \
                as mock_path:
            mock_path.return_value.glob.return_value = entries
            with mock.patch('shakenfist.daemons.cleaner.main.os.path.isfile',
                            return_value=False):
                m._maintain_blobs()

        self.assertGreaterEqual(m.pet_watchdog.call_count, 2)
