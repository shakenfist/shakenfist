# Copyright 2021 Michael Still

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo)
from shakenfist.config import config
from shakenfist import logutil


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
        return {
            'uuid': self.uuid,
            'size': self.size,
            'modified': self.modified,
            'fetched_at': self.fetched_at
        }

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

    def observe(self):
        with self.get_lock_attr('locations', 'Observe blob'):
            locs = self.locations
            if config.NODE_NAME not in locs:
                locs.append(config.NODE_NAME)
            self._db_set_attribute('locations', {'locations': locs})
