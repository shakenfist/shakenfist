# Copyright 2021 Michael Still

import magic
import os
import time

from shakenfist.baseobject import (DatabaseBackedObject as dbo)
from shakenfist.config import config
from shakenfist import db
from shakenfist import images
from shakenfist import logutil
from shakenfist import util


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
        b.add_event('db record creation', None)
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

    @property
    def info(self):
        return self._db_get_attribute('info')

    def observe(self):
        with self.get_lock_attr('locations', 'Observe blob'):
            locs = self.locations
            if config.NODE_NAME not in locs:
                locs.append(config.NODE_NAME)
            self._db_set_attribute('locations', {'locations': locs})

            if not self.info:
                blob_path = os.path.join(
                    config.STORAGE_PATH, 'blobs', self.uuid)

                info = images.identify(blob_path)
                info['mime-type'] = magic.Magic(mime=True).from_file(blob_path)
                self._db_set_attribute('info', info)


def _ensure_blob_path():
    blobs_path = os.path.join(config.STORAGE_PATH, 'blobs')
    os.makedirs(blobs_path, exist_ok=True)


def snapshot_disk(disk, blob_uuid, related_object=None):
    if not os.path.exists(disk['path']):
        return
    _ensure_blob_path()
    dest_path = os.path.join(config.STORAGE_PATH, 'blobs', blob_uuid)

    # Actually make the snapshot
    with util.RecordedOperation('snapshot %s' % disk['device'], related_object):
        images.snapshot(None, disk['path'], dest_path)
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
    dest_path = os.path.join(config.STORAGE_PATH, 'blobs', blob_uuid)

    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            fetched += len(chunk)
            f.write(chunk)

            percentage = fetched / total_size * 100.0
            if (percentage - previous_percentage) > 10.0:
                logs.with_field('bytes_fetched', fetched).info(
                    'Fetch %.02f percent complete' % percentage)
                previous_percentage = percentage

            if time.time() - last_refresh > 5:
                db.refresh_locks(locks)
                last_refresh = time.time()

    logs.with_field('bytes_fetched', fetched).info('Fetch complete')

    # And make the associated blob
    b = Blob.new(blob_uuid, fetched, time.time(), time.time())
    b.observe()
    return b
