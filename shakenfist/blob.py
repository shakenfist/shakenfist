# Copyright 2021 Michael Still

from functools import partial
import hashlib
import magic
import numbers
import os
import random
from shakenfist_utilities import logs, random as sf_random
import socket
import time

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist.config import config
from shakenfist.constants import LOCK_REFRESH_SECONDS, GiB
from shakenfist import db
from shakenfist import etcd
from shakenfist.exceptions import (BlobMissing, BlobDeleted, BlobFetchFailed,
                                   BlobDependencyMissing, BlobsMustHaveContent,
                                   BlobAlreadyBeingTransferred, BadCheckSum,
                                   BlobTransferSetupFailed, UnknownChecksumType)
from shakenfist import instance
from shakenfist.metrics import get_minimum_object_version as gmov
from shakenfist.node import (Node, Nodes, nodes_by_free_disk_descending,
                             inactive_states_filter as node_inactive_states_filter)
from shakenfist.tasks import FetchBlobTask
from shakenfist.util import general as util_general
from shakenfist.util import process as util_process
from shakenfist.util import image as util_image


LOG, _ = logs.setup(__name__)


class Blob(dbo):
    object_type = 'blob'
    current_version = 5
    upgrade_supported = True

    state_targets = {
        None: (dbo.STATE_INITIAL),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_ERROR, dbo.STATE_DELETED),
        dbo.STATE_CREATED: (dbo.STATE_ERROR, dbo.STATE_DELETED),
        dbo.STATE_ERROR: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, static_values):
        if static_values['version'] != self.current_version:
            upgraded, static_values = self.upgrade(static_values)

            if upgraded and gmov('blob') == self.current_version:
                etcd.put(self.object_type, None,
                         static_values.get('uuid'), static_values)
                LOG.with_fields({
                    self.object_type: static_values['uuid']}).info(
                        'Online upgrade committed')

        super(Blob, self).__init__(static_values.get('uuid'),
                                   static_values.get('version'))

        self.__size = static_values['size']
        self.__modified = static_values['modified']
        self.__fetched_at = static_values['fetched_at']
        self.__depends_on = static_values.get('depends_on')

    @classmethod
    def upgrade(cls, static_values):
        changed = False
        starting_version = static_values.get('version')

        if static_values.get('version') == 3:
            static_values['modified'] = cls.normalize_timestamp(
                static_values['modified'])
            static_values['version'] = 4
            changed = True

        if static_values.get('version') == 4:
            cls._upgrade_metadata_to_attribute(static_values['uuid'])
            static_values['version'] = 5
            changed = True

        if changed:
            LOG.with_fields({
                cls.object_type: static_values['uuid'],
                'start_version': starting_version,
                'final_version': static_values.get('version')
            }).info('Object online upgraded')
        return changed, static_values

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
            'sha512': self.checksums.get('sha512')
        })

        # The order of these two calls matters, as instances updates last_used
        # if there are instances using the blob
        out['instances'] = self.instances
        out['last_used'] = self.last_used

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

    # Derived values
    def _instance_usage(self, node=None):
        filters = [instance.healthy_states_filter]
        if node:
            filters.append(partial(placement_filter, node))

        instance_uuids = []
        for inst in instance.Instances(filters):
            # inst.block_devices isn't populated until the instance is created,
            # so it may not be ready yet. This means we will miss instances
            # which have been requested but not yet started.
            for d in inst.block_devices.get('devices', []):
                if 'blob_uuid' not in d:
                    continue

                # This blob is in direct use
                if d['blob_uuid'] == self.uuid:
                    instance_uuids.append(inst.uuid)
                    continue

                # The blob is deleted
                disk_blob = Blob.from_db(d['blob_uuid'])
                if not disk_blob:
                    continue

                # Recurse for dependencies
                while disk_blob.depends_on:
                    disk_blob = Blob.from_db(disk_blob.depends_on)
                    if disk_blob and disk_blob.uuid == self.uuid:
                        instance_uuids.append(inst.uuid)
                        break

        return instance_uuids

    @property
    def instances(self):
        return self._instance_usage()

    @property
    def instances_on_this_node(self):
        return self._instance_usage(node=config.NODE_NAME)

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
                blob_path = os.path.join(
                    config.STORAGE_PATH, 'blobs', self.uuid)

                # We put a bunch of information from "qemu-img info" into the
                # blob because its helpful. However, there are some values we
                # don't want to persist.
                info = util_image.identify(blob_path)
                for key in ['corrupt', 'image', 'lazy refcounts', 'refcount bits']:
                    if key in info:
                        del info[key]

                info['mime-type'] = magic.Magic(mime=True).from_file(blob_path)
                self._db_set_attribute('info', info)

    def ref_count_inc(self):
        with self.get_lock_attr('ref_count', 'Increase reference count'):
            if self.state.value == self.STATE_DELETED:
                raise BlobDeleted
            new_count = self.ref_count + 1
            self._db_set_attribute('ref_count', {'ref_count': new_count})
            return new_count

    def ref_count_dec(self):
        with self.get_lock_attr('ref_count', 'Decrease reference count'):
            new_count = self.ref_count - 1
            if new_count < 0:
                new_count = 0
                self.log.warning('Reference count decremented below zero')

            self._db_set_attribute('ref_count', {'ref_count': new_count})

            # If no references then the blob cannot be used, therefore delete.
            if new_count == 0:
                self.state = self.STATE_DELETED

                for transcoded_blob_uuid in self.transcoded.values():
                    transcoded_blob = Blob.from_db(transcoded_blob_uuid)
                    if transcoded_blob:
                        transcoded_blob.ref_count_dec()

                depends_on = self.depends_on
                if depends_on:
                    dep_blob = Blob.from_db(depends_on)
                    if dep_blob:
                        dep_blob.ref_count_dec()

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

        # Actually replicate this blob
        blob_dir = os.path.join(config.STORAGE_PATH, 'blobs')
        blob_path = os.path.join(blob_dir, self.uuid)
        os.makedirs(blob_dir, exist_ok=True)

        # If the blob exists already, we're done
        if os.path.exists(blob_path):
            self.observe()
            return

        partial_path = blob_path + '.partial'
        while os.path.exists(partial_path):
            st = os.stat(partial_path)
            if time.time() - st.st_mtime > 300:
                self.log.with_fields({
                    'partial file age': time.time() - st.st_mtime}).warning(
                    'No activity on previous partial download in more than '
                    'five minutes. Removing and re-attempting.')
                os.unlink(partial_path)
            else:
                if not wait_for_other_transfers:
                    raise BlobAlreadyBeingTransferred()

                self.log.with_fields({
                    'partial file age': time.time() - st.st_mtime}).info(
                    'Waiting for existing download to complete')
                time.sleep(10)

        # If the blob exists after waiting for another partial transfer,
        # we're done
        if os.path.exists(blob_path):
            self.observe()
            return

        with open(partial_path, 'wb') as f:
            total_bytes_received = 0
            last_refresh = 0
            previous_percentage = 0

            # NOTE(mikal): this port allocation scheme isn't great as it doesn't
            # handle a port being in use already on the remote node. We should
            # probably have a retry thing here.
            locations = self.locations
            for n in Nodes([node_inactive_states_filter]):
                if n.uuid in locations:
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
                    'transfer %s failed to setup' % name)

            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect((locations[0], data['port']))
            client.send(token.encode('utf-8'))

            sha512_hash = hashlib.sha512()
            with open(partial_path, 'wb') as f:
                d = client.recv(8000)
                while d:
                    f.write(d)
                    sha512_hash.update(d)
                    total_bytes_received += len(d)

                    if time.time() - last_refresh > LOCK_REFRESH_SECONDS:
                        db.refresh_locks(locks)
                        last_refresh = time.time()

                    percentage = (total_bytes_received /
                                  int(self.size) * 100.0)
                    if (percentage - previous_percentage) > 10.0:
                        if instance_object:
                            instance_object.add_event(
                                'Fetching required blob %s, %d%% complete'
                                % (self.uuid, percentage))
                        self.log.with_fields({
                            'bytes_fetched': total_bytes_received,
                            'size': int(self.size)
                        }).debug('Fetch %.02f percent complete' % percentage)
                        previous_percentage = percentage

                    d = client.recv(8000)

            if total_bytes_received != int(self.size):
                if os.path.exists(partial_path):
                    os.unlink(partial_path)
                raise BlobFetchFailed('Did not fetch enough data')

            self.verify_checksum(sha512_hash.hexdigest())
            os.rename(partial_path, blob_path)
            if instance_object:
                instance_object.add_event(
                    'Fetching required blob %s, complete' % self.uuid)
            self.log.with_fields({
                'bytes_fetched': total_bytes_received,
                'size': int(self.size)
            }).info('Fetch complete')
            self.observe()
            return total_bytes_received

    def request_replication(self, allow_excess=0):
        absent_nodes = []
        for n in Nodes([node_inactive_states_filter]):
            LOG.with_fields({
                'node': n.fqdn}).info('Node is absent for blob replication')
            absent_nodes.append(n.fqdn)
        LOG.info('Found %d inactive nodes' % len(absent_nodes))

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
            targets = (config.BLOB_REPLICATION_FACTOR + current_transfers +
                       allow_excess - replica_count)
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

                self.log.with_fields({'nodes': nodes}).debug(
                    'Considered for blob replication')

                for n in nodes[:targets]:
                    etcd.enqueue(n, {
                        'tasks': [FetchBlobTask(self.uuid)]
                    })
                    self.log.with_fields({'node': n}).info(
                        'Instructed to replicate blob')

    @staticmethod
    def filepath(blob_uuid):
        return os.path.join(config.STORAGE_PATH, 'blobs', blob_uuid)

    @property
    def checksums(self):
        return self._db_get_attribute('checksums')

    def _remove_if_idle(self, msg):
        if len(self.instances_on_this_node) == 0:
            blob_path = Blob.filepath(self.uuid)
            if os.path.exists(blob_path):
                os.unlink(blob_path)
            if os.path.exists(blob_path + '.partial'):
                os.unlink(blob_path + '.partial')

            self.remove_location(config.NODE_NAME)
            self.log.error('Removed idle blob %s' % msg)
        else:
            self.log.error('Not removing in-use blob %s' % msg)

    def verify_size(self):
        st = os.stat(Blob.filepath(self.uuid))
        if self.size != st.st_size:
            self.add_event('blob failed checksum validation',
                           extra={
                               'stored_size': self.size,
                               'node_size': st.st_size,
                               'node': config.NODE_NAME
                           })
            self._remove_if_idle('with incorrect size')
            return False
        return True

    def verify_checksum(self, hash=None):
        if not hash:
            hash_out, _ = util_process.execute(
                None,
                'sha512sum %s' % Blob.filepath(self.uuid),
                iopriority=util_process.PRIORITY_LOW)
            hash = hash_out.split(' ')[0]
        self.log.with_fields({'sha512': hash}).info('Local checksum')

        with self.get_lock_attr('checksums', op='update checksums'):
            c = self.checksums
            if 'sha512' not in c:
                c['sha512'] = hash
            else:
                if c['sha512'] != hash:
                    self.add_event('blob failed checksum validation',
                                   extra={
                                       'stored_hash': c['sha512'],
                                       'node_hash': hash,
                                       'node': config.NODE_NAME
                                   })
                    self._remove_if_idle('with incorrect checksum')

            if 'nodes' not in c:
                c['nodes'] = {config.NODE_NAME: time.time()}
            else:
                c['nodes'][config.NODE_NAME] = time.time()

            self._db_set_attribute('checksums', c)


def ensure_blob_path():
    blobs_path = os.path.join(config.STORAGE_PATH, 'blobs')
    os.makedirs(blobs_path, exist_ok=True)


def snapshot_disk(disk, blob_uuid, related_object=None, thin=False):
    if not os.path.exists(disk['path']):
        return
    ensure_blob_path()
    dest_path = Blob.filepath(blob_uuid)

    # Actually make the snapshot
    depends_on = None
    with util_general.RecordedOperation('snapshot %s' % disk['device'], related_object):
        depends_on = util_image.snapshot(
            None, disk['path'], dest_path + '.partial', thin=thin)
        st = os.stat(dest_path + '.partial')

    # Check that the dependency (if any) actually exists. This test can fail when
    # the blob used to start an instance has been deleted already.
    if depends_on:
        dep_blob = Blob.from_db(depends_on)
        if not dep_blob or dep_blob.state.value != Blob.STATE_CREATED:
            raise BlobDependencyMissing(
                'Snapshot depends on blob UUID %s, which is missing'
                % depends_on)
        dep_blob.ref_count_inc()

    # And make the associated blob
    os.rename(dest_path + '.partial', dest_path)
    b = Blob.new(blob_uuid, st.st_size, time.time(), time.time(),
                 depends_on=depends_on)
    b.state = Blob.STATE_CREATED
    b.observe()
    b.verify_checksum()
    b.request_replication()
    return b


def http_fetch(url, resp, blob_uuid, locks, logs, checksum, checksum_type,
               instance_object=None):
    ensure_blob_path()

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
                            'Fetching required HTTP resource %s into blob %s, '
                            '%d%% complete'
                            % (url, blob_uuid, percentage))

                    logs.with_fields({'bytes_fetched': fetched}).debug(
                        'Fetch %.02f percent complete' % percentage)
                    previous_percentage = percentage

            if time.time() - last_refresh > LOCK_REFRESH_SECONDS:
                db.refresh_locks(locks)
                last_refresh = time.time()

    if instance_object:
        instance_object.add_event(
            'Fetching required HTTP resource %s into blob %s, complete'
            % (url, blob_uuid))
    logs.with_fields({'bytes_fetched': fetched}).info('Fetch complete')

    # Verify the downloaded checksum, if we were given one
    if checksum:
        if checksum_type == 'md5':
            calc = md5_hash.hexdigest()
            correct = calc == checksum
            logs.with_fields({
                'calculated': calc,
                'correct': correct}).info('Image checksum verification')
            if not correct:
                raise BadCheckSum('url=%s' % url)
        else:
            raise UnknownChecksumType(checksum_type)

    # Make the associated blob
    os.rename(dest_path + '.partial', dest_path)
    b = Blob.new(blob_uuid, fetched, resp.headers.get('Last-Modified'),
                 time.time())
    b.state = Blob.STATE_CREATED
    b.observe()
    b.verify_checksum(sha512_hash.hexdigest())
    b.request_replication()
    return b


class Blobs(dbo_iter):
    def __iter__(self):
        for _, b in etcd.get_all('blob', None):
            b = Blob.from_db(b['uuid'])
            if not b:
                continue

            out = self.apply_filters(b)
            if out:
                yield out


def placement_filter(node, b):
    return node in b.locations
