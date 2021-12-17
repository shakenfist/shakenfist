# Copyright 2021 Michael Still

import time

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import logutil


LOG, _ = logutil.setup(__name__)


class Upload(dbo):
    object_type = 'upload'
    current_version = 2
    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, static_values):
        super(Upload, self).__init__(static_values.get('uuid'),
                                     static_values.get('version'))
        self.__node = static_values['node']
        self.__created_at = static_values['created_at']

    @classmethod
    def new(cls, upload_uuid, node):
        static_values = {
            'uuid': upload_uuid,
            'node': node,
            'created_at': time.time(),

            'version': cls.current_version
        }
        Upload._db_create(upload_uuid, static_values)
        u = Upload(static_values)
        u.state = Upload.STATE_CREATED
        return u

    @staticmethod
    def from_db(upload_uuid):
        if not upload_uuid:
            return None

        static_values = Upload._db_get(upload_uuid)
        if not static_values:
            return None

        return Upload(static_values)

    # Static values
    @property
    def node(self):
        return self.__node

    @property
    def created_at(self):
        return self.__created_at

    def external_view(self):
        return {
            'uuid': self.uuid,
            'node': self.node,
            'created_at': self.created_at
        }
