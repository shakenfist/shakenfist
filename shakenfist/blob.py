# Copyright 2021 Michael Still

# Please note: blobs are a "foundational" baseobject type, which means they
# should not rely on any other baseobjects for their implementation. This is
# done to help minimize circular import problems.

import hashlib
import magic
import numbers
import os
import psutil
import random
from shakenfist_utilities import logs, random as sf_random
import socket
import time
import uuid

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT, EVENT_TYPE_STATUS, EVENT_TYPE_MUTATE
from shakenfist.constants import LOCK_REFRESH_SECONDS, GiB
from shakenfist import etcd
from shakenfist.exceptions import (BlobMissing, BlobDeleted, BlobFetchFailed,
                                   BlobDependencyMissing, BlobsMustHaveContent,
                                   BlobAlreadyBeingTransferred, BlobTransferSetupFailed)
from shakenfist.node import Node, Nodes, nodes_by_free_disk_descending
from shakenfist.tasks import FetchBlobTask
from shakenfist.util import callstack as util_callstack
from shakenfist.util import general as util_general
from shakenfist.util import process as util_process
from shakenfist.util import image as util_image


LOG, _ = logs.setup(__name__)


class Blob(dbo):
    object_type = 'blob'
    initial_version = 2
    current_version = 6

    # docs/developer_guide/state_machine.md has a description of these states.
    state_targets = {
        None: (dbo.STATE_INITIAL),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_ERROR, dbo.STATE_DELETED),
        dbo.STATE_CREATED: (dbo.STATE_ERROR, dbo.STATE_DELETED),
        dbo.STATE_ERROR: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values.get('uuid'), static_values.get('version'))

        self.__size = static_values['size']
        self.__modified = static_values['modified']
        self.__fetched_at = static_values['fetched_at']
        self.__depends_on = static_values.get('depends_on')

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values):
        static_values['depends_on'] = None

    @classmethod
    def _upgrade_step_3_to_4(cls, static_values):
        static_values['modified'] = cls.normalize_timestamp(
                static_values.get('modified'))

    @classmethod
    def _upgrade_step_4_to_5(cls, static_values):
        cls._upgrade_metadata_to_attribute(static_values['uuid'])

    @classmethod
    def _upgrade_step_5_to_6(cls, static_values):
        etcd.put('attribute/blob', static_values['uuid'], 'retention',
                 {'expires_at': 0})

    @classmethod
    def normalize_timestamp(cls, timestamp):
        # The timestamp is either a number (int or float, assumed to be epoch
        # seconds)...
        if isinstance(timestamp, numbers.Number):
            return timestamp

        # Or the timestamp could be empty, at which point we just default to now.
        if timestamp is None:
            return time.time()

        # Or a HTTP last-modified timestamp like "Sun, 09 Jan 2022 23:05:25 GMT"
        # to be converted to epoch seconds.
        t = time.strptime(timestamp, '%a, %d %b %Y %H:%M:%S %Z')
        return time.mktime(t)

    @classmethod
    def new(cls, blob_uuid, size, modified, fetched_at, depends_on=None):
        if not size:
            raise BlobsMustHaveContent('A blob cannot be of zero size')

        Blob._db_create(
            blob_uuid,
            {
                'uuid': blob_uuid,
                'size': size,
                'modified': cls.normalize_timestamp(modified),
                'fetched_at': fetched_at,
                'depends_on': depends_on,

                'version': cls.current_version
            }
        )

        b = Blob.from_db(blob_uuid)
        b.state = Blob.STATE_INITIAL
        return b

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        out = self._external_view()
        out.update({
            'size': self.size,
            'modified': self.modified,
            'fetched_at': self.fetched_at,
            'depends_on': self.depends_on,
            'transcodes': self.transcoded,
            'locations': self.locations,
            'reference_count': self.ref_count,
            'sha512': self.checksums.get('sha512'),
            'last_used': self.last_used
        })

        out.update(self.info)
        return out

    # Static values
    @property
    def size(self):
        return self.__size

    @property
    def modified(self):
        return self.__modified

    @property
    def fetched_at(self):
        return self.__fetched_at

    @property
    def depends_on(self):
        return self.__depends_on

    # Values routed to attributes
    @property
    def locations(self):
        locs = self._db_get_attribute('locations', {'locations': []})
        return locs['locations']

    def add_location(self, location):
        self._add_item_in_attribute_list('locations', location)

    def remove_location(self, location):
        self._remove_item_in_attribute_list('locations', location)

    @property
    def info(self):
        return self._db_get_attribute('info')

    @property
    def ref_count(self):
        """Counts artifact references to the blob"""
        count = self._db_get_attribute('ref_count', {'ref_count': 0})
        return int(count['ref_count'])

    @property
    def transcoded(self):
        return self._db_get_attribute('transcoded')

    def add_transcode(self, style, blob_uuid):
        self.record_usage()
        with self.get_lock(op='Update transcoded versions'):
            transcoded = self.transcoded
            transcoded[style] = blob_uuid
            self._db_set_attribute('transcoded', transcoded)

    def remove_transcodes(self):
        with self.get_lock(op='Remove transcoded versions'):
            self._db_set_attribute('transcoded', {})

    @property
    def last_used(self):
        last_used = self._db_get_attribute('last_used', {'last_used': None})
        return last_used['last_used']

    def record_usage(self):
        self._db_set_attribute('last_used', {'last_used': time.time()})

    @property
    def expires_at(self):
        retention = self._db_get_attribute('retention', {'expires_at': 0})
        return retention['expires_at']

    def set_lifetime(self, seconds_from_now):
        self._db_set_attribute('retention', {'expires_at': time.time() + seconds_from_now})

    # Operations
    def add_node_location(self):
        self.add_location(config.NODE_NAME)

        n = Node.from_db(config.NODE_NAME)
        n.add_blob(self.uuid)

    def drop_node_location(self, node=config.NODE_NAME):
        self.remove_location(node)

        # Remove from cached node blob list
        n = Node.from_db(node)
        n.remove_blob(self.uuid)

    def observe(self):
        self.add_node_location()

        # Observing a blob can move it from initial to created, but it should not
        # move it from deleted to created.
        if self.state.value == self.STATE_INITIAL:
            self.state = self.STATE_CREATED

        with self.get_lock_attr('info', 'Set blob info'):
            if not self.info:
                blob_path = Blob.filepath(self.uuid)

                # We put a bunch of information from "qemu-img info" into the
                # blob because its helpful. However, there are some values we
                # don't want to persist.
                info = util_image.identify(blob_path)
                for key in ['corrupt', 'image', 'lazy refcounts', 'refcount bits']:
                    if key in info:
                        del info[key]

                info['mime-type'] = magic.Magic(mime=True).from_file(blob_path)
                self._db_set_attribute('info', info)

    def ref_count_inc(self, baseobject, count=1):
        with self.get_lock_attr('ref_count', 'Increase reference count'):
            if self.state.value == self.STATE_DELETED:
                raise BlobDeleted(self.uuid)
            new_count = self.ref_count + count
            self._db_set_attribute('ref_count', {'ref_count': new_count})
            self.add_event(
                EVENT_TYPE_MUTATE, 'incremented reference count',
                extra={
                    baseobject.object_type: baseobject.uuid,
                    'increment': count,
                    'reference_count': new_count,
                    'caller': util_callstack.get_caller(offset=-3)
                    })
            return new_count

    def _delete_unused(self, new_count):
        # If no references then the blob cannot be used, therefore delete.
        if new_count == 0:
            self.state = self.STATE_DELETED

            for transcoded_blob_uuid in self.transcoded.values():
                transcoded_blob = Blob.from_db(transcoded_blob_uuid)
                if transcoded_blob:
                    transcoded_blob.ref_count_dec(self)

            depends_on = self.depends_on
            if depends_on:
                dep_blob = Blob.from_db(depends_on)
                if dep_blob:
                    dep_blob.ref_count_dec(self)

    def ref_count_dec(self, baseobject, count=1):
        with self.get_lock_attr('ref_count', 'Decrease reference count'):
            new_count = self.ref_count - 1
            if new_count < 0:
                new_count = 0
                self.add_event(
                    EVENT_TYPE_MUTATE, 'decremented reference count below zero',
                    extra={
                        baseobject.object_type: baseobject.uuid,
                        'decrement': count,
                        'reference_count': new_count
                        })
            else:
                self.add_event(
                    EVENT_TYPE_MUTATE, 'decremented reference count',
                    extra={
                        baseobject.object_type: baseobject.uuid,
                        'decrement': count,
                        'reference_count': new_count
                        })

            self._db_set_attribute('ref_count', {'ref_count': new_count})
            self._delete_unused(new_count)
            return new_count

    def ensure_local(self, locks, instance_object=None,
                     wait_for_other_transfers=True):
        if self.state.value != self.STATE_CREATED:
            self.log.warning(
                'Blob not in created state, replication cancelled')
            return

        # Replicate any blob this blob depends on
        if self.depends_on:
            dep_blob = Blob.from_db(self.depends_on)
            if not dep_blob:
                raise BlobDependencyMissing(self.depends_on)
            dep_blob.ensure_local(locks, instance_object=instance_object)

        # If the blob exists already, we're done
        blob_path = Blob.filepath(self.uuid)
        if os.path.exists(blob_path):
            self.observe()
            return

        partial_path = blob_path + '.partial'
        while os.path.exists(partial_path):
            st = os.stat(partial_path)
            if time.time() - st.st_mtime > 300:
                self.log.with_fields({
                    'partial file age': time.time() - st.st_mtime}).info(
                    'No activity on previous partial download in more than '
                    'five minutes. Removing and re-attempting.')
                os.unlink(partial_path)
            else:
                if not wait_for_other_transfers:
                    raise BlobAlreadyBeingTransferred()

                self.log.with_fields({
                    'partial file age': time.time() - st.st_mtime}).debug(
                    'Waiting for existing download to complete')
                time.sleep(10)

        # If the blob exists after waiting for another partial transfer,
        # we're done
        if os.path.exists(blob_path):
            self.observe()
            return

        # Fetch with a few retries
        attempts = 0
        while True:
            try:
                return self._attempt_transfer(
                    locks, instance_object, partial_path, blob_path)
            except (ConnectionRefusedError, BlobTransferSetupFailed,
                    BlobFetchFailed) as e:
                attempts += 1
                if attempts > 3:
                    raise BlobFetchFailed(
                        'Repeated attempts to fetch blob failed: %s' % e)

    def _attempt_transfer(self, locks, instance_object, partial_path,
                          blob_path):
        with open(partial_path, 'wb') as f:
            total_bytes_received = 0
            last_refresh = 0
            previous_percentage = 0

            locations = self.locations
            for n in Nodes([], prefilter='inactive'):
                if n.uuid in locations:
                    LOG.with_fields({
                        'node': n,
                        'state': n.state.value}).debug(
                        'Node is inactive, ignoring blob location')
                    locations.remove(n.uuid)
            if len(locations) == 0:
                raise BlobMissing('There are no online sources for this blob')

            random.shuffle(locations)
            name = sf_random.random_id()
            token = sf_random.random_id()
            data = {
                'server_state': dbo.STATE_INITIAL,
                'requestor': config.NODE_MESH_IP,
                'blob_uuid': self.uuid,
                'token': token
            }

            etcd.put('transfer', locations[0], name, data)
            self.log.with_fields(data).info('Created transfer request')

            waiting_time = time.time()
            while time.time() - waiting_time < 30:
                data = etcd.get('transfer', locations[0], name)
                if data['server_state'] == dbo.STATE_CREATED:
                    break
                time.sleep(1)

            if data['server_state'] != dbo.STATE_CREATED:
                raise BlobTransferSetupFailed(
                    'transfer %s failed to setup, state is %s'
                    % (name, data['server_state']))

            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect((locations[0], data['port']))
            client.send(token.encode('utf-8'))

            sha512_hash = hashlib.sha512()
            with open(partial_path, 'wb') as f:
                while d := client.recv(8000):
                    f.write(d)
                    sha512_hash.update(d)
                    total_bytes_received += len(d)

                    if time.time() - last_refresh > LOCK_REFRESH_SECONDS:
                        etcd.refresh_locks(locks)
                        last_refresh = time.time()

                    percentage = total_bytes_received / int(self.size) * 100.0
                    if (percentage - previous_percentage) > 10.0:
                        if instance_object:
                            instance_object.add_event(
                                EVENT_TYPE_STATUS, 'fetching required blob',
                                extra={
                                    'blob_uuid': self.uuid,
                                    'percentage': percentage
                                })
                        self.log.with_fields({
                            'bytes_fetched': total_bytes_received,
                            'size': int(self.size)
                        }).debug('Fetch %.02f percent complete' % percentage)
                        previous_percentage = percentage

            if total_bytes_received != int(self.size):
                if os.path.exists(partial_path):
                    os.unlink(partial_path)
                raise BlobFetchFailed(
                    'The amount of fetched data does not match the stored size. We '
                    'fetched %d bytes, but expected %d.'
                    % (total_bytes_received, self.size))

            if not self.verify_size(partial=True):
                if instance_object:
                    instance_object.add_event(
                        EVENT_TYPE_AUDIT, 'fetching required blob failed',
                        extra={
                            'blob_uuid': self.uuid,
                            'error': 'incorrect size'
                            })
                raise BlobFetchFailed(
                    'Fetching required blob %s failed. We fetched %d bytes, but expected %d.'
                    % (self.uuid, total_bytes_received, self.size))

            if not self.verify_checksum(sha512_hash.hexdigest()):
                if instance_object:
                    instance_object.add_event(
                        EVENT_TYPE_AUDIT, 'fetching required blob failed',
                        extra={
                            'blob_uuid': self.uuid,
                            'error': 'incorrect checksum'
                            })
                raise BlobFetchFailed(
                    'Fetching required blob %s failed. Incorrect checksum.' % self.uuid)

            os.rename(partial_path, blob_path)
            if instance_object:
                instance_object.add_event(
                    EVENT_TYPE_STATUS, 'fetching required blob complete',
                    extra={'blob_uuid': self.uuid})
            self.log.with_fields({
                'bytes_fetched': total_bytes_received,
                'size': int(self.size)
            }).info('Fetch complete')
            self.observe()
            return total_bytes_received

    def request_replication(self, allow_excess=0):
        absent_nodes = list(Nodes([], prefilter='inactive'))

        present_nodes = list(Nodes([], prefilter='active'))
        present_nodes_len = len(present_nodes)

        # We take current transfers into account when replicating, to avoid
        # over replicating very large blobs
        current_transfers = etcd.get_current_blob_transfers(
            absent_nodes=absent_nodes).get(self.uuid, 0)

        with self.get_lock_attr('locations', 'Request replication'):
            locations = self.locations

            # Filter out absent locations
            for node_name in self.locations:
                n = Node.from_db(node_name)
                if not n:
                    locations.remove(node_name)
                elif n.state.value != Node.STATE_CREATED:
                    locations.remove(node_name)

            replica_count = len(locations)
            if replica_count == 0:
                self.log.debug('No available replicas, giving up')
                return

            targets = (config.BLOB_REPLICATION_FACTOR + current_transfers +
                       allow_excess - replica_count)

            if (replica_count + current_transfers) == present_nodes_len:
                self.log.debug('Run out of nodes to replicate to, giving up')
                return

            self.log.info('Desired replica count is %d, we have %d, and %d inflight, '
                          'excess of %d requested, target is therefore %d new copies'
                          % (config.BLOB_REPLICATION_FACTOR, replica_count,
                             current_transfers, allow_excess, targets))
            if targets > 0:
                blob_size_gb = int(int(self.size) / GiB)
                nodes = nodes_by_free_disk_descending(
                    minimum=blob_size_gb + config.MINIMUM_FREE_DISK,
                    intention='blobs')

                # Don't copy to locations which already have the blob
                for n in self.locations:
                    if n in nodes:
                        nodes.remove(n)

                for n in nodes[:targets]:
                    etcd.enqueue(n, {
                        'tasks': [FetchBlobTask(self.uuid)]
                    })
                    self.log.with_fields({'node': n}).info(
                        'Instructed to replicate blob')

    @staticmethod
    def filedir(blob_uuid):
        path = os.path.join(config.STORAGE_PATH, 'blobs', blob_uuid[0:2])
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def filepath(blob_uuid):
        return os.path.join(Blob.filedir(blob_uuid), blob_uuid)

    @property
    def checksums(self):
        return self._db_get_attribute('checksums')

    def _remove_if_idle(self, msg):
        # NOTE(mikal): we specifically don't lookup instance records in etcd here
        # for two reasons -- we don't want to depend on a "higher level object",
        # but also because it wouldn't cover instances that had somehow escaped
        # tracking. Instead, we build our own `lsof` instead!
        blob_path = Blob.filepath(self.uuid)
        users = 0
        for pid in psutil.pids():
            try:
                p = psutil.Process(pid)
                for f in p.open_files():
                    if f.path.startswith(blob_path):
                        users += 1
                        self.log.warning('Process %d is using blob' % pid)
            except FileNotFoundError:
                # This is a race. The PID ended between us listing the PIDs and
                # psutil trying to open its entry in /proc.
                pass

        if users == 0:
            blob_path = Blob.filepath(self.uuid)
            if os.path.exists(blob_path):
                os.unlink(blob_path)
            if os.path.exists(blob_path + '.partial'):
                os.unlink(blob_path + '.partial')
            self.drop_node_location(config.NODE_NAME)
        else:
            self.log.error('Not removing in-use blob replica %s' % msg)

    def verify_size(self, partial=False):
        blob_path = Blob.filepath(self.uuid)
        if partial:
            blob_path += '.partial'

        st = os.stat(blob_path)
        if self.size != st.st_size:
            self.add_event(EVENT_TYPE_AUDIT,
                           'blob failed size validation',
                           extra={
                               'stored_size': self.size,
                               'node_size': st.st_size,
                               'node': config.NODE_NAME
                           })
            self._remove_if_idle('with incorrect size')
            return False
        return True

    def verify_checksum(self, hash=None, locks=None):
        if not hash:
            hash_out, _ = util_process.execute(
                locks,
                'sha512sum %s' % Blob.filepath(self.uuid),
                iopriority=util_process.PRIORITY_LOW)
            hash = hash_out.split(' ')[0]

        with self.get_lock_attr('checksums', op='update checksums'):
            c = self.checksums
            if 'sha512' not in c:
                c['sha512'] = hash
            else:
                if c['sha512'] != hash:
                    self.add_event(EVENT_TYPE_AUDIT,
                                   'blob failed checksum validation',
                                   extra={
                                       'stored_hash': c['sha512'],
                                       'node_hash': hash,
                                       'node': config.NODE_NAME
                                   })
                    self._remove_if_idle('with incorrect checksum')
                    return False

            if 'nodes' not in c:
                c['nodes'] = {config.NODE_NAME: time.time()}
            else:
                c['nodes'][config.NODE_NAME] = time.time()

            self._db_set_attribute('checksums', c)

        return True


def snapshot_disk(disk, blob_uuid, related_object=None, thin=False):
    if not os.path.exists(disk['path']):
        return
    dest_path = Blob.filepath(blob_uuid)

    # Actually make the snapshot
    depends_on = None
    with util_general.RecordedOperation('snapshot %s' % disk['device'], related_object):
        depends_on = util_image.snapshot(
            None, disk['path'], dest_path + '.partial', thin=thin)
        st = os.stat(dest_path + '.partial')

    # Check that the dependency (if any) actually exists. This test can fail when
    # the blob used to start an instance has been deleted already.
    dep_blob = None
    if depends_on:
        dep_blob = Blob.from_db(depends_on)
        if not dep_blob or dep_blob.state.value != Blob.STATE_CREATED:
            raise BlobDependencyMissing(
                'Snapshot depends on blob UUID %s, which is missing' % depends_on)

    # And make the associated blob. Note that we deliberately don't calculate the
    # snapshot checksum here, as this makes large snapshots even slower for users.
    # The checksum will "catch up" when the scheduled verification occurs.
    # We don't remove the partial file until we've finished registering the blob
    # to avoid deletion races. Note that this _must_ be a hard link, which is why
    # we don't use util_general.link().
    os.link(dest_path + '.partial', dest_path)
    b = Blob.new(blob_uuid, st.st_size, time.time(), time.time(), depends_on=depends_on)
    b.state = Blob.STATE_CREATED
    if dep_blob:
        dep_blob.ref_count_inc(b)
    b.observe()
    b.request_replication()
    os.unlink(dest_path + '.partial')
    return b


def http_fetch(url, resp, blob_uuid, locks, logs, instance_object=None):
    fetched = 0
    if resp.headers.get('Content-Length'):
        total_size = int(resp.headers.get('Content-Length'))
    else:
        total_size = None

    previous_percentage = 0.0
    last_refresh = 0
    dest_path = Blob.filepath(blob_uuid)

    md5_hash = hashlib.md5()
    sha512_hash = hashlib.sha512()

    with open(dest_path + '.partial', 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            fetched += len(chunk)
            f.write(chunk)
            md5_hash.update(chunk)
            sha512_hash.update(chunk)

            if total_size:
                percentage = fetched / total_size * 100.0
                if (percentage - previous_percentage) > 10.0:
                    if instance_object:
                        instance_object.add_event(
                            EVENT_TYPE_STATUS, 'fetching required HTTP resource',
                            extra={
                                'url': url,
                                'blob_uuid': blob_uuid,
                                'percentage': percentage
                            })

                    logs.with_fields({'bytes_fetched': fetched}).debug(
                        'Fetch %.02f percent complete' % percentage)
                    previous_percentage = percentage

            if time.time() - last_refresh > LOCK_REFRESH_SECONDS:
                etcd.refresh_locks(locks)
                last_refresh = time.time()

    if instance_object:
        instance_object.add_event(
            EVENT_TYPE_STATUS, 'fetching required HTTP resource complete',
            extra={
                'url': url,
                'blob_uuid': blob_uuid
            })
    logs.with_fields({'bytes_fetched': fetched}).info('Fetch complete')

    # Make the associated blob. Note that we deliberately don't calculate the
    # artifact checksum here, as this makes large snapshots even slower for users.
    # The checksum will "catch up" when the scheduled verification occurs.
    os.rename(dest_path + '.partial', dest_path)
    b = Blob.new(blob_uuid, fetched, resp.headers.get('Last-Modified'),
                 time.time())
    b.state = Blob.STATE_CREATED
    b.observe()
    b.request_replication()
    return b


def from_memory(content):
    blob_uuid = str(uuid.uuid4())
    with open(Blob.filepath(blob_uuid), 'wb') as f:
        f.write(content)

    b = Blob.new(blob_uuid, len(content), time.time(), time.time())
    b.state = Blob.STATE_CREATED
    b.observe()
    b.request_replication()
    return b


class Blobs(dbo_iter):
    base_object = Blob

    def __iter__(self):
        for _, b in self.get_iterator():
            b = Blob(b)
            if not b:
                continue

            out = self.apply_filters(b)
            if out:
                yield out


def placement_filter(node, b):
    return node in b.locations
