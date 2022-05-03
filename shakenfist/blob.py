# Copyright 2021 Michael Still

import http
import magic
import numbers
import os
import random
import requests
import time
import urllib3

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist.config import config
from shakenfist.constants import LOCK_REFRESH_SECONDS, GiB
from shakenfist import db
from shakenfist import etcd
from shakenfist.exceptions import (BlobDeleted, BlobFetchFailed,
                                   BlobDependencyMissing, BlobsAlreadyConfigured)
from shakenfist import instance
from shakenfist import logutil
from shakenfist.metrics import get_minimum_object_version as gmov
from shakenfist.node import Node, nodes_by_free_disk_descending
from shakenfist.tasks import FetchBlobTask
from shakenfist.util import general as util_general
from shakenfist.util import image as util_image


LOG, _ = logutil.setup(__name__)


# Caution! The blob object stores some otherwise immutable values as attributes
# because we don't know them at the time we create the blob. That is, they're
# empty briefly, and then populated later. Thus, don't assume that is ok to
# change the size, modification or fetch time, or dependency chain of a blob post
# creation. The lack of write properties for these values is deliberate!

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
                LOG.with_field(
                    self.object_type, static_values['uuid']).info('Online upgrade committed')

        super(Blob, self).__init__(static_values.get('uuid'),
                                   static_values.get('version'))

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
            immutable_attributes = {
                'size': static_values['size'],
                'modified': static_values['modified'],
                'fetched_at': static_values['fetched_at'],
                'depends_on': static_values['depends_on']
            }
            etcd.put('attribute/%s' % cls.object_type, static_values['uuid'],
                     'immutable_attributes', immutable_attributes)

            del static_values['size']
            del static_values['modified']
            del static_values['fetched_at']
            del static_values['depends_on']
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

        # Or a HTTP last-modified timestamp like "Sun, 09 Jan 2022 23:05:25 GMT"
        # to be converted to epoch seconds.
        t = time.strptime(timestamp, '%a, %d %b %Y %H:%M:%S %Z')
        return time.mktime(t)

    @classmethod
    def new(cls, blob_uuid):
        Blob._db_create(
            blob_uuid,
            {
                'uuid': blob_uuid,
                'version': cls.current_version
            }
        )

        b = Blob.from_db(blob_uuid)
        b.state = Blob.STATE_INITIAL
        return b

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        out = {
            'uuid': self.uuid,
            'state': self.state.value,
            'size': self.size,
            'modified': self.modified,
            'fetched_at': self.fetched_at,
            'depends_on': self.depends_on,
            'transcodes': self.transcoded,
            'locations': self.locations,
            'reference_count': self.ref_count
        }

        # The order of these two calls matters, as instances updates last_used
        # if there are instances using the blob
        out['instances'] = self.instances
        out['last_used'] = self.last_used

        out.update(self.info)
        return out

    # Static values, but stored in the "immutable attributes" attribute because
    # we don't always know them at creation time
    @property
    def size(self):
        return self._db_get_attribute('immutable_attributes').get('size')

    @property
    def modified(self):
        return self._db_get_attribute('immutable_attributes').get('modified')

    @property
    def fetched_at(self):
        return self._db_get_attribute('immutable_attributes').get('fetched_at')

    @property
    def depends_on(self):
        return self._db_get_attribute('immutable_attributes').get('depends_on')

    def set_immutable_attributes(self, size, modified, fetched_at, depends_on=None):
        if self._db_get_attribute('immutable_attributes') != {}:
            raise BlobsAlreadyConfigured(
                'immutable attributes already set for blob')

        immutable_attributes = {
            'size': size,
            'modified': modified,
            'fetched_at': fetched_at,
            'depends_on': depends_on
        }
        self._db_set_attribute('immutable_attributes', immutable_attributes)

    # Values routed to attributes
    @property
    def locations(self):
        return self._db_get_attribute('locations').get('locations', [])

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
        count = self._db_get_attribute('ref_count')
        if not count:
            return 0
        return int(count.get('ref_count', 0))

    @property
    def transcoded(self):
        transcoded = self._db_get_attribute('transcoded')
        if not transcoded:
            return {}
        return transcoded

    def add_transcode(self, style, blob_uuid):
        self.record_usage()
        with self.get_lock(op='Update trancoded versions'):
            transcoded = self.transcoded
            transcoded[style] = blob_uuid
            self._db_set_attribute('transcoded', transcoded)

    @property
    def last_used(self):
        last_used = self._db_get_attribute('last_used')
        if not last_used:
            return None
        return last_used['last_used']

    def record_usage(self):
        self._db_set_attribute('last_used', {'last_used': time.time()})

    # Derived values
    @property
    def instances(self):
        """Build a list of instances that are using the blob as a block device.

        Returns a list of instance UUIDs.
        """
        instance_uuids = []
        for inst in instance.Instances([instance.healthy_states_filter]):
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
                blob_path = self.filepath()

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

    def ensure_local(self, locks, instance_object=None):
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
        with self.get_lock(config.NODE_NAME) as blob_lock:
            ensure_blob_path()
            blob_path = self.filepath()
            if os.path.exists(blob_path):
                self.observe()
                return

            with open(blob_path + '.partial', 'wb') as f:
                done = False
                last_refresh = 0
                refreshable_locks = locks.copy()
                refreshable_locks.append(blob_lock)

                total_bytes_received = 0
                previous_percentage = 0
                connection_failures = 0

                while not done:
                    locations = self.locations
                    random.shuffle(locations)
                    blob_source = locations[0]
                    bytes_in_attempt = 0

                    try:
                        admin_token = util_general.get_api_token(
                            'http://%s:%d' % (blob_source, config.API_PORT))
                        url = ('http://%s:%d/blobs/%s/data?offset=%d'
                               % (blob_source, config.API_PORT, self.uuid,
                                  total_bytes_received))
                        r = requests.request(
                            'GET', url, stream=True,
                            headers={'Authorization': admin_token,
                                     'User-Agent': util_general.get_user_agent()})
                        connection_failures = 0

                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                            total_bytes_received += len(chunk)
                            bytes_in_attempt += len(chunk)

                            if time.time() - last_refresh > LOCK_REFRESH_SECONDS:
                                db.refresh_locks(refreshable_locks)
                                last_refresh = time.time()

                            percentage = (total_bytes_received /
                                          int(self.size) * 100.0)
                            if (percentage - previous_percentage) > 10.0:
                                if instance_object:
                                    instance_object.add_event2(
                                        'Fetching required blob %s, %d%% complete'
                                        % (self.uuid, percentage))
                                self.log.with_fields({
                                    'bytes_fetched': total_bytes_received,
                                    'size': int(self.size)
                                }).debug('Fetch %.02f percent complete' % percentage)
                                previous_percentage = percentage

                        done = True
                        self.log.with_fields({
                            'bytes_fetched': total_bytes_received,
                            'size': int(self.size),
                            'done': done
                        }).info('HTTP request ran out of chunks')

                    except urllib3.exceptions.NewConnectionError as e:
                        connection_failures += 1
                        if connection_failures > 2:
                            if instance_object:
                                instance_object.add_event2(
                                    'Transfer of blob %s failed' % self.uuid)
                            self.log.error(
                                'HTTP connection repeatedly failed: %s' % e)
                            raise e

                    except (http.client.IncompleteRead,
                            urllib3.exceptions.ProtocolError,
                            requests.exceptions.ChunkedEncodingError) as e:
                        # An API error (or timeout) occurred. Retry unless we got nothing.
                        if bytes_in_attempt > 0:
                            self.log.info('HTTP connection dropped, retrying')
                        else:
                            self.log.error('HTTP connection dropped without '
                                           'transferring data: %s' % e)
                            raise e

            if total_bytes_received != int(self.size):
                if os.path.exists(blob_path + '.partial'):
                    os.unlink(blob_path + '.partial')
                raise BlobFetchFailed('Did not fetch enough data')

            os.rename(blob_path + '.partial', blob_path)
            if instance_object:
                instance_object.add_event2(
                    'Fetching required blob %s, complete' % self.uuid)
            self.log.with_fields({
                'bytes_fetched': total_bytes_received,
                'size': int(self.size)
            }).info('Fetch complete')
            self.observe()
            return total_bytes_received

    def request_replication(self, allow_excess=0):
        with self.get_lock_attr('locations', 'Request replication'):
            locations = self.locations

            # Filter out absent locations
            for node_name in self.locations:
                n = Node.from_db(node_name)
                if n.state.value != Node.STATE_CREATED:
                    locations.remove(node_name)

            replica_count = len(locations)
            targets = config.BLOB_REPLICATION_FACTOR + allow_excess - replica_count
            self.log.info('Desired replica count is %d, we have %d, excess of %d requested'
                          % (config.BLOB_REPLICATION_FACTOR, replica_count, allow_excess))
            if targets > 0:
                blob_size_gb = int(int(self.size) / GiB)
                nodes = nodes_by_free_disk_descending(
                    minimum=blob_size_gb + config.MINIMUM_FREE_DISK,
                    intention='blobs')

                # Don't copy to locations which already have the blob
                for n in self.locations:
                    if n in nodes:
                        nodes.remove(n)

                self.log.with_field('nodes', nodes).debug(
                    'Considered for blob replication')

                for n in nodes[:targets]:
                    etcd.enqueue(n, {
                        'tasks': [FetchBlobTask(self.uuid)]
                    })
                    self.log.with_field('node', n).info(
                        'Instructed to replicate blob')

    def filepath(self):
        return os.path.join(config.STORAGE_PATH, 'blobs', self.uuid)


def ensure_blob_path():
    blobs_path = os.path.join(config.STORAGE_PATH, 'blobs')
    os.makedirs(blobs_path, exist_ok=True)


def snapshot_disk(disk, blob_uuid, instance_object, thin=False):
    b = Blob.from_db(blob_uuid)
    if not os.path.exists(disk['path']):
        b.state = Blob.STATE_ERROR
        b.error = 'Disk missing: %s' % disk['path']
        return
    ensure_blob_path()

    instance_object.add_event2('creating snapshot with blob uuid %s' % b.uuid)
    b.add_event2('blob is a snapshot of instance %s' % instance_object.uuid)
    dest_path = b.filepath()

    # Actually make the snapshot
    depends_on = util_image.snapshot(None, disk['path'], dest_path, thin=thin)
    st = os.stat(dest_path)

    # Check that the dependency (if any) actually exists. This test can fail when
    # the blob used to start an instance has been deleted already.
    if depends_on:
        dep_blob = Blob.from_db(depends_on)
        if not dep_blob or dep_blob.state.value != Blob.STATE_CREATED:
            b.state = Blob.STATE_ERROR
            b.error = 'Snapshot depends on blob %s which is missing' % depends_on
            return
        dep_blob.ref_count_inc()

    # And make the associated blob
    b.set_immutable_attributes(st.st_size, time.time(), time.time(),
                               depends_on=depends_on)
    b.state = Blob.STATE_CREATED
    instance_object.add_event2('created snapshot with blob uuid %s' % b.uuid)
    b.observe()
    b.request_replication()
    return b


def http_fetch(url, resp, blob_uuid, locks, logs, instance_object=None):
    ensure_blob_path()
    b = Blob.new(blob_uuid)

    fetched = 0
    if resp.headers.get('Content-Length'):
        total_size = int(resp.headers.get('Content-Length'))
    else:
        total_size = None

    previous_percentage = 0.0
    last_refresh = 0
    dest_path = b.filepath()

    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            fetched += len(chunk)
            f.write(chunk)

            if total_size:
                percentage = fetched / total_size * 100.0
                if (percentage - previous_percentage) > 10.0:
                    if instance_object:
                        instance_object.add_event2(
                            'Fetching required HTTP resource %s into blob %s, %d%% complete'
                            % (url, b.uuid, percentage))

                    logs.with_field('bytes_fetched', fetched).debug(
                        'Fetch %.02f percent complete' % percentage)
                    previous_percentage = percentage

            if time.time() - last_refresh > LOCK_REFRESH_SECONDS:
                db.refresh_locks(locks)
                last_refresh = time.time()

    if instance_object:
        instance_object.add_event2(
            'Fetching required HTTP resource %s into blob %s, complete'
            % (url, b.uuid))
    logs.with_field('bytes_fetched', fetched).info('Fetch complete')

    # We really should verify the checksum here before we commit the blob to the
    # database.

    # And make the associated blob
    if not total_size:
        total_size = fetched

    b.set_immutable_attributes(total_size, resp.headers.get('Last-Modified'),
                               time.time())
    b.state = Blob.STATE_CREATED
    b.observe()
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
