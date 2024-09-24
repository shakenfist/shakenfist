# Copyright 2021 Michael Still
import time

from shakenfist_utilities import logs

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter


LOG, _ = logs.setup(__name__)


class Upload(dbo):
    object_type = 'upload'
    initial_version = 2
    current_version = 3

    # docs/developer_guide/state_machine.md has a description of these states.
    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: (),
    }

    ACTIVE_STATES = {dbo.STATE_CREATED}

    def __init__(self, static_values):
        self.upgrade(static_values)

        super().__init__(static_values.get('uuid'), static_values.get('version'))
        self.__node = static_values['node']
        self.__created_at = static_values['created_at']

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values):
        cls._upgrade_metadata_to_attribute(static_values['uuid'])

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

    # Static values
    @property
    def node(self):
        return self.__node

    @property
    def created_at(self):
        return self.__created_at

    def external_view(self):
        retval = self._external_view()
        retval.update({
            'node': self.node,
            'created_at': self.created_at
        })
        return retval


class Uploads(dbo_iter):
    base_object = Upload

    def __iter__(self):
        for _, u in self.get_iterator():
            u = Upload(u)
            if not u:
                continue

            out = self.apply_filters(u)
            if out:
                yield out
