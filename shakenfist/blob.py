# Copyright 2021 Michael Still
# Please note: blobs are a "foundational" baseobject type, which means they
# should not rely on any other baseobjects for their implementation. This is
# done to help minimize circular import problems.
import copy
import hashlib
import numbers
import os
import random
import socket
import time
import uuid

import magic
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as sf_random  # noreorder

from shakenfist import cache
from shakenfist import etcd
from shakenfist.etcd_schema.operations.baseclusteroperation \
    import PRIORITY
from shakenfist.etcd_schema.operations.node_blob_op \
    import create_and_enqueue as nbo_create_and_enqueue
from shakenfist.etcd_schema.operations.node_blob_op \
    import model_tasks as nbo_tasks
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import BLOB_HASH_ALGORITHMS
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_MUTATE
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import GiB
from shakenfist.constants import LOCK_REFRESH_SECONDS
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import BlobAlreadyBeingTransferred
from shakenfist.exceptions import BlobDeleted
from shakenfist.exceptions import BlobDependencyMissing
from shakenfist.exceptions import BlobFetchFailed
from shakenfist.exceptions import BlobMissing
from shakenfist.exceptions import BlobsMustHaveContent
from shakenfist.exceptions import BlobSizeCannotChange
from shakenfist.exceptions import BlobTransferSetupFailed
from shakenfist.exceptions import LocklessUpdateFailed
from shakenfist.node import Node
from shakenfist.node import Nodes
from shakenfist.node import nodes_by_free_disk_descending
from shakenfist.util import callstack as util_callstack
from shakenfist.util import general as util_general
from shakenfist.util import image as util_image
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


# NOTE(mikal): blobs are immutable objects, that is their content cannot change
# once set. However, we don't always know the size or content of the blob when
# we reserve its UUID, so we do allow the size of the blob to be set after
# creation.
class Blob(dbo):
    object_type = 'blob'
    initial_version = 2
    current_version = 8

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
        try:
            cls._upgrade_metadata_to_attribute(static_values['uuid'])
        except KeyError as e:
            # I am currently unsure why you'd end up here, but am seeing it in
            # CI. Let's gather some more information so we can chase it down.
            LOG.with_fields(static_values).error(
                'KeyError while upgrading metadata (v4 to v5): %s' % e)

    @classmethod
    def _upgrade_step_5_to_6(cls, static_values):
        try:
            etcd.put('attribute/blob', static_values['uuid'], 'retention',
                     {'expires_at': 0})
        except KeyError as e:
            # I am currently unsure why you'd end up here, but am seeing it in
            # CI. Let's gather some more information so we can chase it down.
            LOG.with_fields(static_values).error(
                'KeyError while upgrading retention (v5 to v6): %s' % e)

    @classmethod
    def _upgrade_step_6_to_7(cls, static_values):
        try:
            etcd.put('attribute/blob', static_values['uuid'], 'size',
                     {'size': static_values['size']})
        except KeyError as e:
            # I am currently unsure why you'd end up here, but am seeing it in
            # CI. Let's gather some more information so we can chase it down.
            LOG.with_fields(static_values).error(
                'KeyError while upgrading retention (v6 to v7): %s' % e)

    @classmethod
    def _upgrade_step_7_to_8(cls, static_values):
        # We added the concept of "incomplete locations".
        ...

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
    def new(cls, blob_uuid, modified, fetched_at, depends_on=None):
        Blob._db_create(
            blob_uuid,
            {
                'uuid': blob_uuid,
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

        checksums = self.checksums
        if 'nodes' in checksums:
            del checksums['nodes']

        out.update({
            'size': self.size,
            'modified': self.modified,
            'fetched_at': self.fetched_at,
            'depends_on': self.depends_on,
            'transcodes': self.transcoded,
            'reference_count': self.ref_count,
            'sha512': checksums.get('sha512'),
            'last_used': self.last_used,
            'checksums': checksums
        })

        # Locations and their incomplete counterparts
        out['locations'] = self.locations
        out['locations'].extend(self.incomplete_locations)

        # Include information about the blob
        out.update(self.info)
        return out

    # Static values
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
    def size(self):
        size = self._db_get_attribute('size', {'size': 0})
        return size['size']

    @size.setter
    def size(self, new_size):
        if new_size < 1:
            raise BlobsMustHaveContent()

        size = self._db_get_attribute('size', None)
        if size:
            raise BlobSizeCannotChange()
        self._db_set_attribute('size', {'size': new_size})

    @property
    def locations(self):
        locs = self._db_get_attribute('locations', {'locations': []})
        return locs['locations']

    def add_location(self, location):
        self._add_item_in_attribute_list('locations', location)

    def remove_location(self, location):
        self._remove_item_in_attribute_list('locations', location)

    @property
    def incomplete_locations(self):
        out = []
        locs = self._db_get_attribute(
            'incomplete_locations', {'locations': {}})
        for loc in locs['locations']:
            out.append(f'{loc} ({locs["locations"][loc]:.2f}%)')
        return out

    @property
    def incomplete_healthy_locations(self):
        absent_nodes = Nodes([], prefilter='inactive')
        out = []
        for loc in self.incomplete_locations:
            if loc not in absent_nodes:
                out.append(loc)
        return out

    def _update_incomplete_location_inner(self, percentage, node=None):
        if not node:
            node = config.NODE_NAME

        original = etcd.get(f'attribute/{self.object_type}', self.uuid,
                            'incomplete_locations')
        if not original:
            updated = {'locations': {}}
        else:
            updated = copy.deepcopy(original)
        changed = False

        if node not in updated['locations']:
            changed = True
            updated['locations'][node] = percentage
        elif updated['locations'][node] != percentage:
            changed = True
            updated['locations'][node] = percentage

        if changed:
            return etcd.replace(f'attribute/{self.object_type}', self.uuid,
                                'incomplete_locations', original, updated)

        return True

    def update_incomplete_location(self, percentage, node=None):
        percentage = round(percentage, 1)
        attempts = 0
        while attempts < 3:
            if self._update_incomplete_location_inner(percentage, node=node):
                return
            attempts += 1
            time.sleep(0.01)

        raise LocklessUpdateFailed(
            f'Lockless update of incomplete locations for blob {self.uuid} '
            'failed.')

    def _remove_incomplete_location_inner(self):
        original = etcd.get(f'attribute/{self.object_type}', self.uuid,
                            'incomplete_locations')
        if not original:
            return True

        if config.NODE_NAME in original['locations']:
            updated = copy.deepcopy(original)
            del updated['locations'][config.NODE_NAME]
            return etcd.replace(f'attribute/{self.object_type}', self.uuid,
                                'incomplete_locations', original, updated)

        return True

    def remove_incomplete_location(self):
        attempts = 0
        while attempts < 3:
            if self._remove_incomplete_location_inner():
                return
            attempts += 1
            time.sleep(0.01)

        raise LocklessUpdateFailed(
            f'Lockless removal of incomplete locations for blob {self.uuid} '
            'failed.')

    @property
    def info(self):
        return self._db_get_attribute('info')

    @property
    def ref_count(self):
        return int(self.ref_count_with_age['ref_count'])

    @property
    def ref_count_with_age(self):
        count = self._db_get_attribute('ref_count', {
            'ref_count': 0
        })
        if 'update_time' not in count:
            count['update_time'] = time.time()
        return count

    @property
    def transcoded(self):
        return self._db_get_attribute('transcoded')

    def add_transcode(self, style, blob_uuid):
        self.record_usage()
        with self.get_lock(op='Update transcoded versions'):
            transcoded = self.transcoded
            if style in transcoded:
                # This is a duplicate transcode
                return False

            transcoded[style] = blob_uuid
            self._db_set_attribute('transcoded', transcoded)
            return True

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
                add_event_multi(
                    EVENT_TYPE_AUDIT, [baseobject, self],
                    'attempt to use a deleted blob',
                    extra={
                        'blob_uuid': self.uuid,
                        f'{baseobject.object_type}_uuid': baseobject.uuid
                    })
                raise BlobDeleted(self.uuid)

            old_count = self.ref_count
            new_count = old_count + count
            self._db_set_attribute('ref_count', {
                'ref_count': new_count,
                'update_time': time.time()
            })
            self.add_event(
                EVENT_TYPE_MUTATE, 'incremented reference count',
                extra={
                    baseobject.object_type: baseobject.uuid,
                    'increment': count,
                    'reference_count': new_count,
                    'caller': util_callstack.get_caller(offset=-3)
                    })
            return new_count

    def ref_count_dec(self, baseobject, count=1):
        with self.get_lock_attr('ref_count', 'Decrease reference count'):
            old_count = self.ref_count
            new_count = old_count - count

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

            self._db_set_attribute('ref_count', {
                'ref_count': new_count,
                'update_time': time.time()
            })
            return new_count

    def cascading_delete(self):
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

    def ensure_local(self, locks, instance_object=None,
                     wait_for_other_transfers=True):
        affected_objects = [self]
        if instance_object:
            affected_objects.append(instance_object)

        if self.state.value != self.STATE_CREATED:
            add_event_multi(
                EVENT_TYPE_STATUS, affected_objects,
                'blob not in created state, replication to this node cancelled')
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

        add_event_multi(
            EVENT_TYPE_STATUS, affected_objects, 'replicating blob to this node')
        partial_path = blob_path + '.partial'
        while os.path.exists(partial_path):
            st = os.stat(partial_path)
            if time.time() - st.st_mtime > 300:
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    ('no activity on previous partial download in more than '
                     'five minutes. Removing and re-attempting.'),
                    extra={
                        'partial file age': round(time.time() - st.st_mtime, 2)
                    })
                os.unlink(partial_path)
            else:
                if not wait_for_other_transfers:
                    raise BlobAlreadyBeingTransferred()

                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    'waiting for existing download to complete',
                    extra={
                        'partial file age': round(time.time() - st.st_mtime, 2)
                    }
                )
                time.sleep(10)

        # If the blob exists after waiting for another partial transfer,
        # we're done
        if os.path.exists(blob_path):
            add_event_multi(
                EVENT_TYPE_AUDIT, affected_objects, 'blob now exists on this node')
            self.observe()
            return

        # Fetch with a few retries
        attempts = 0
        while True:
            try:
                with util_concurrency.NodeLock(f'blob-{self.uuid}-transfer'):
                    # Check the blob didn't show up without us
                    if os.path.exists(blob_path):
                        self.observe()
                        return

                    # Attempt a transfer
                    self._attempt_transfer(
                        locks, affected_objects, partial_path, blob_path)
                    return
            except (ConnectionRefusedError, BlobTransferSetupFailed,
                    BlobFetchFailed) as e:
                attempts += 1
                time.sleep(10)
                if attempts > 3:
                    raise BlobFetchFailed(
                        'Repeated attempts to fetch blob failed: %s' % e)

    # This method assumes the caller is holding the 'blob-{self.uuid}-transfer'
    # external lock. Luckily the only caller right now is the one directly
    # above here.
    def _attempt_transfer(self, locks, affected_objects, partial_path,
                          blob_path):
        add_event_multi(
            EVENT_TYPE_AUDIT, affected_objects, 'attempting transfer')
        with open(partial_path, 'wb') as f:
            locations = self.locations
            for n in Nodes([], prefilter='inactive'):
                if n.uuid in locations:
                    LOG.with_fields({
                        'node': n,
                        'state': n.state.value}).debug(
                        'Node is inactive, ignoring blob location')
                    locations.remove(n.uuid)
            if len(locations) == 0:
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    'there are no online sources for this blob')
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

            direction_info = f'({locations[0]} -> {config.NODE_NAME})'
            affected_objects = copy.deepcopy(affected_objects)
            affected_objects.append(('node', config.NODE_NAME))
            affected_objects.append(('node', locations[0]))

            etcd.put('transfer', locations[0], name, data)
            add_event_multi(
                EVENT_TYPE_AUDIT, affected_objects,
                f'created transfer request {direction_info}', extra=data)

            waiting_time = time.time()
            while time.time() - waiting_time < 30:
                data = etcd.get('transfer', locations[0], name)
                if data['server_state'] == dbo.STATE_CREATED:
                    break
                time.sleep(1)

            if data['server_state'] != dbo.STATE_CREATED:
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    f'transfer setup failed {direction_info}', extra=data)
                raise BlobTransferSetupFailed(
                    f'transfer {name} failed to setup, state is {data["server_state"]}')

            add_event_multi(
                EVENT_TYPE_AUDIT, affected_objects,
                f'transfer setup succeeded  {direction_info}', extra=data)

            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect((locations[0], data['port']))
            client.send(token.encode('utf-8'))

            total_bytes_received = 0
            last_refresh = 0
            next_percentage = 10

            last_event = time.time()
            sha512_hash = hashlib.sha512()
            with open(partial_path, 'wb') as f:
                while d := client.recv(8000):
                    if len(d) == 0:
                        break

                    f.write(d)
                    sha512_hash.update(d)
                    total_bytes_received += len(d)

                    if time.time() - last_refresh > LOCK_REFRESH_SECONDS:
                        etcd.refresh_locks(locks)
                        last_refresh = time.time()

                    percentage = total_bytes_received / int(self.size) * 100.0
                    if ((next_percentage - percentage) < 0 or
                            time.time() - last_event > 30):
                        add_event_multi(
                            EVENT_TYPE_STATUS, affected_objects,
                            f'fetching required blob {direction_info}',
                            extra={
                                'percentage': int(percentage)
                            }
                        )
                        self.update_incomplete_location(percentage)
                        if (next_percentage - percentage) < 0:
                            next_percentage += 10
                        last_event = time.time()

            self.remove_incomplete_location()

            if total_bytes_received != int(self.size):
                add_event_multi(
                    EVENT_TYPE_STATUS, affected_objects,
                    f'did not fetch entire blob, cleaning up {direction_info}',
                    extra={
                        'received': total_bytes_received,
                        'expected': self.size
                    }
                )
                if os.path.exists(partial_path):
                    os.unlink(partial_path)
                raise BlobFetchFailed(
                    'The amount of fetched data does not match the stored size. We '
                    f'fetched {total_bytes_received} bytes, but expected {self.size}.')

            if not self.verify_size(partial=True):
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    f'fetching required blob failed, incorrect size {direction_info}')
                raise BlobFetchFailed(
                    f'Fetching required blob {self.uuid} failed. We fetched '
                    f'{total_bytes_received} bytes, but expected {self.size}.')

            if not self.verify_checksum(hash=sha512_hash.hexdigest()):
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    f'fetching required blob failed, incorrect checksum {direction_info}')
                raise BlobFetchFailed(
                    f'Fetching required blob {self.uuid} failed. Incorrect checksum.')

            os.rename(partial_path, blob_path)
            add_event_multi(
                EVENT_TYPE_AUDIT, affected_objects,
                f'fetching required blob complete {direction_info}')
            self.observe()

    def request_replication(self, allow_excess=0):
        present_nodes = list(Nodes([], prefilter='active'))
        present_nodes_len = len(present_nodes)
        absent_nodes = list(Nodes([], prefilter='inactive'))

        with self.get_lock_attr('locations', 'Request replication'):
            # We take current transfers into account when replicating, to avoid
            # over replicating very large blobs
            current_transfers = 0
            for node in self.incomplete_locations:
                if node not in absent_nodes:
                    current_transfers += 1

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
                    nbo_create_and_enqueue(
                        n,
                        self.uuid,
                        [nbo_tasks.ensure_local],
                        PRIORITY.background_high_io)
                    self.update_incomplete_location(0, node=n)
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

    def _remove_corrupt_blob(self):
        blob_path = Blob.filepath(self.uuid)
        if os.path.exists(blob_path):
            os.unlink(blob_path)
        if os.path.exists(blob_path + '.partial'):
            os.unlink(blob_path + '.partial')
        self.drop_node_location(config.NODE_NAME)

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
            self._remove_corrupt_blob()
            return False
        return True

    def _get_hash(self, hashtype='sha512', locks=None):
        hash_out, _ = util_concurrency.execute(
            locks,
            f'{hashtype}sum {Blob.filepath(self.uuid)}',
            iopriority=util_concurrency.PRIORITY_LOW)
        return hash_out.split(' ')[0]

    def verify_checksum(self, hash=None, locks=None, urgent=True):
        # This method is focussed on sha512 hashes at the moment, but I also
        # want it to be able to do other hash types later -- for example OVA
        # support needs sha1 or sha256, and xxhash is a lot faster. So for now
        # we always make sure there is a sha512, but if we're not in a hurry
        # we'll calculate a few others just once as well.
        if hash:
            sha512_hash = hash
        if not hash:
            sha512_hash = self._get_hash(hashtype='sha512', locks=locks)

        # If we're not in a hurry, calculate missing extra hashes
        extra_hashes = {}
        needs_rehashing = False
        c = self.checksums
        for alg in BLOB_HASH_ALGORITHMS:
            if alg not in c:
                if not urgent:
                    extra_hashes[alg] = self._get_hash(
                        hashtype=alg, locks=locks)
                else:
                    needs_rehashing = True

        # If we're in a hurry but extra hashes are missing, enqueue those as
        # background tasks
        if needs_rehashing:
            nbo_create_and_enqueue(
                config.NODE_NAME,
                self.uuid,
                [nbo_tasks.verify_size_and_checksum],
                PRIORITY.background_high_io)

        # Validate / update our stored checksums
        with self.get_lock_attr('checksums', op='update checksums'):
            c = self.checksums
            if 'sha512' not in c:
                c['sha512'] = sha512_hash
            else:
                if c['sha512'] != sha512_hash:
                    self.add_event(EVENT_TYPE_AUDIT,
                                   'blob failed checksum validation',
                                   extra={
                                       'stored_hash': c['sha512'],
                                       'node_hash': sha512_hash,
                                       'node': config.NODE_NAME
                                   })
                    self._remove_corrupt_blob()
                    return False

            if 'nodes' not in c:
                c['nodes'] = {config.NODE_NAME: time.time()}
            else:
                c['nodes'][config.NODE_NAME] = time.time()

            for alg in extra_hashes:
                if alg not in c:
                    c[alg] = extra_hashes[alg]

            self._db_set_attribute('checksums', c)

        # Avoid holding the checksum lock while updating the blob hash cache
        hashes = {}
        for alg in BLOB_HASH_ALGORITHMS:
            if alg in c:
                hashes[alg] = c[alg]
        cache.update_blob_hash_cache(self.uuid, hashes)

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
    b = Blob.new(blob_uuid, time.time(), time.time(), depends_on=depends_on)
    b.size = st.st_size
    b.state = Blob.STATE_CREATED
    if dep_blob:
        dep_blob.ref_count_inc(b)
    b.observe()
    b.request_replication()
    os.unlink(dest_path + '.partial')
    return b


def http_fetch(url, resp, b, locks, affected_objects):
    fetched = 0

    if resp.headers.get('Content-Length'):
        total_size = int(resp.headers.get('Content-Length'))
    else:
        total_size = None

    last_refresh = 0
    dest_path = Blob.filepath(b.uuid)

    md5_hash = hashlib.md5()
    sha512_hash = hashlib.sha512()

    percentage = 0
    next_percentage = 10
    last_event = time.time()
    with open(dest_path + '.partial', 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            fetched += len(chunk)
            f.write(chunk)
            md5_hash.update(chunk)
            sha512_hash.update(chunk)

            if total_size:
                percentage = fetched / total_size * 100.0

            if ((next_percentage - percentage) < 0 or
                    time.time() - last_event > 30):
                add_event_multi(
                    EVENT_TYPE_STATUS, affected_objects,
                    'fetching required HTTP resource',
                    extra={
                        'url': url,
                        'percentage': int(percentage),
                        'bytes_fetched': fetched
                    })
                b.update_incomplete_location(percentage)
                if (next_percentage - percentage) < 0:
                    next_percentage += 10
                last_event = time.time()

            if time.time() - last_refresh > LOCK_REFRESH_SECONDS:
                etcd.refresh_locks(locks)
                last_refresh = time.time()

    b.remove_incomplete_location()
    add_event_multi(
        EVENT_TYPE_USAGE, affected_objects,
        'fetching required HTTP resource complete',
        extra={
            'url': url,
            'bytes_fetched': fetched
        })

    # Import the newly fetched blob
    os.rename(dest_path + '.partial', dest_path)
    b.size = fetched
    b.state = Blob.STATE_CREATED
    b.verify_checksum(hash=sha512_hash.hexdigest())
    b.observe()
    b.request_replication()
    return b


def from_memory(content):
    blob_uuid = str(uuid.uuid4())
    with open(Blob.filepath(blob_uuid), 'wb') as f:
        f.write(content)

    b = Blob.new(blob_uuid, time.time(), time.time())
    b.size = len(content)
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
