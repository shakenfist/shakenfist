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
from shakenfist.schema.object_types import ObjectType
from shakenfist.daemons.cleaner import main as cleaner_main
from shakenfist.daemons.cleaner import scheduled_tasks as cleaner_st
from shakenfist.tests import base
from shakenfist.tests.mock_mariadb import MockMariaDB


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

    VIR_DOMAIN_PAUSED_USER = 1
    VIR_DOMAIN_PAUSED_IOERROR = 5

    VIR_DOMAIN_DISK_ERROR_NONE = 0
    VIR_DOMAIN_DISK_ERROR_UNSPEC = 1
    VIR_DOMAIN_DISK_ERROR_NO_SPACE = 2

    libvirtError = Exception

    def open(self, _ignored):
        return FakeLibvirtConnection()


class FakeLibvirtConnection:
    def listDomainsID(self):
        ids = ['id1', 'id2', 'id3', 'id4', 'id5', 'id6']
        # The ioerror-paused domain only exists for tests which create an
        # instance for it, so that the other tests don't see an unknown
        # domain (which the cleaner would try to virsh destroy).
        if 'ioerror' in _test_instance_uuids:
            ids.append('id7')
        return ids

    def lookupByID(self, id):
        # Map domain IDs to (name_key, state, pause_reason) where name_key is
        # used to look up the actual UUID from _test_instance_uuids
        domain_map = {
            'id1': ('running', FakeLibvirt.VIR_DOMAIN_RUNNING,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),
            'id2': ('apache2', FakeLibvirt.VIR_DOMAIN_RUNNING,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),  # non-SF domain
            'id3': ('shutoff', FakeLibvirt.VIR_DOMAIN_SHUTOFF,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),
            'id4': ('crashed', FakeLibvirt.VIR_DOMAIN_CRASHED,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),
            'id5': ('paused', FakeLibvirt.VIR_DOMAIN_PAUSED,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),
            'id6': ('suspended', FakeLibvirt.VIR_DOMAIN_PMSUSPENDED,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_USER),
            'id7': ('ioerror', FakeLibvirt.VIR_DOMAIN_PAUSED,
                    FakeLibvirt.VIR_DOMAIN_PAUSED_IOERROR),
        }

        name_key, state, reason = domain_map.get(id)
        if name_key == 'apache2':
            # Non-SF domain, return as-is
            return FakeLibvirtDomain('apache2', state)
        # SF domain - use the actual instance UUID
        inst_uuid = _test_instance_uuids.get(name_key, name_key)
        disk_errors = {}
        if name_key == 'ioerror':
            disk_errors = {
                'vda': FakeLibvirt.VIR_DOMAIN_DISK_ERROR_UNSPEC,
                'vdb': FakeLibvirt.VIR_DOMAIN_DISK_ERROR_NONE,
            }
        return FakeLibvirtDomain(
            f'sf:{inst_uuid}', state, reason=reason, disk_errors=disk_errors)

    def lookupByName(self, name):
        return FakeLibvirtDomain(name, FakeLibvirt.VIR_DOMAIN_RUNNING)

    def close(self):
        pass


class FakeLibvirtDomain:
    def __init__(self, name, state, reason=1, disk_errors=None):
        self._name = name
        self._state = state
        self._reason = reason
        self._disk_errors = disk_errors or {}

    def name(self):
        return self._name

    def state(self):
        return [self._state, self._reason]

    def diskErrors(self):
        return self._disk_errors

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
    CLEANER_DELAY: int = 3600


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

        self.mock_mariadb = MockMariaDB(self, node_count=4)
        self.mock_mariadb.setup()

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
                            ('shutoff', 'off'),
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
