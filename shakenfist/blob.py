# Copyright 2021 Michael Still

import magic
import os
import time

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist import constants
from shakenfist import db
from shakenfist.exceptions import BlobRefCountDecrementBelowZero, BlobDeleted
from shakenfist import instance
from shakenfist import logutil
from shakenfist.util import general as util_general
from shakenfist.util import image as util_image


LOG, _ = logutil.setup(__name__)


class Blob(dbo):
    object_type = 'blob'
    current_version = 2
    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, static_values):
        super(Blob, self).__init__(static_values.get('uuid'),
                                   static_values.get('version'))

        self.__size = static_values['size']
        self.__modified = static_values['modified']
        self.__fetched_at = static_values['fetched_at']

    @classmethod
    def new(cls, blob_uuid, size, modified, fetched_at):
        Blob._db_create(
            blob_uuid,
            {
                'uuid': blob_uuid,
                'size': size,
                'modified': modified,
                'fetched_at': fetched_at,

                'version': cls.current_version
            }
        )

        b = Blob.from_db(blob_uuid)
        b.state = Blob.STATE_CREATED
        return b

    @staticmethod
    def from_db(blob_uuid):
        if not blob_uuid:
            return None

        static_values = Blob._db_get(blob_uuid)
        if not static_values:
            return None

        return Blob(static_values)

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        out = {
            'uuid': self.uuid,
            'size': self.size,
            'modified': self.modified,
            'fetched_at': self.fetched_at
        }

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

    # Values routed to attributes
    @property
    def locations(self):
        locs = self._db_get_attribute('locations')
        if not locs:
            return []
        return locs.get('locations', [])

    @locations.setter
    def locations(self, new_locations):
        self._db_set_attribute('locations', {'locations': new_locations})

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
                if d.get('blob_uuid') == self.uuid:
                    instance_uuids.append(inst.uuid)
        return instance_uuids

    # Operations
    def add_node_location(self):
        with self.get_lock_attr('locations', 'Add node to location'):
            locs = self.locations
            if config.NODE_NAME not in locs:
                locs.append(config.NODE_NAME)
            self.locations = locs

    def drop_node_location(self):
        with self.get_lock_attr('locations', 'Remove node from location'):
            locs = self.locations
            try:
                locs.remove(config.NODE_NAME)
            except ValueError:
                pass
            else:
                self.locations = locs
        return locs

    def observe(self):
        self.add_node_location()

        with self.get_lock_attr('locations', 'Set blob info'):
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
        with self.get_lock_attr('ref_count', 'Increase ref count'):
            if self.state.value == self.STATE_DELETED:
                raise BlobDeleted
            new_count = self.ref_count + 1
            self._db_set_attribute('ref_count', {'ref_count': new_count})
            return new_count

    def ref_count_dec(self):
        with self.get_lock_attr('ref_count', 'Increase ref count'):
            new_count = self.ref_count - 1
            if new_count < 0:
                raise BlobRefCountDecrementBelowZero

            self._db_set_attribute('ref_count', {'ref_count': new_count})

            # If no references then the blob cannot be used, therefore delete.
            if new_count == 0:
                self.state = self.STATE_DELETED

            return new_count

    @staticmethod
    def filepath(blob_uuid):
        return os.path.join(config.STORAGE_PATH, 'blobs', blob_uuid)


def _ensure_blob_path():
    blobs_path = os.path.join(config.STORAGE_PATH, 'blobs')
    os.makedirs(blobs_path, exist_ok=True)


def snapshot_disk(disk, blob_uuid, related_object=None):
    if not os.path.exists(disk['path']):
        return
    _ensure_blob_path()
    dest_path = Blob.filepath(blob_uuid)

    # Actually make the snapshot
    with util_general.RecordedOperation('snapshot %s' % disk['device'], related_object):
        util_image.snapshot(None, disk['path'], dest_path)
        st = os.stat(dest_path)

    # And make the associated blob
    b = Blob.new(blob_uuid, st.st_size, time.time(), time.time())
    b.observe()
    return b


def http_fetch(resp, blob_uuid, locks, logs):
    _ensure_blob_path()

    fetched = 0
    total_size = int(resp.headers.get('Content-Length'))
    previous_percentage = 0.0
    last_refresh = 0
    dest_path = Blob.filepath(blob_uuid)

    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            fetched += len(chunk)
            f.write(chunk)

            percentage = fetched / total_size * 100.0
            if (percentage - previous_percentage) > 10.0:
                logs.with_field('bytes_fetched', fetched).info(
                    'Fetch %.02f percent complete' % percentage)
                previous_percentage = percentage

            if time.time() - last_refresh > constants.LOCK_REFRESH_SECONDS:
                db.refresh_locks(locks)
                last_refresh = time.time()

    logs.with_field('bytes_fetched', fetched).info('Fetch complete')

    # We really should verify the checksum here before we commit the blob to the
    # database.

    # And make the associated blob
    b = Blob.new(blob_uuid,
                 resp.headers.get('Content-Length'),
                 resp.headers.get('Last-Modified'),
                 time.time())
    b.observe()
    return b
