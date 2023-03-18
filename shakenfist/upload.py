# Copyright 2021 Michael Still

from shakenfist_utilities import logs
import time

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist import etcd
from shakenfist.metrics import get_minimum_object_version as gmov


LOG, _ = logs.setup(__name__)


class Upload(dbo):
    object_type = 'upload'
    initial_version = 2
    current_version = 3

    state_targets = {
        None: (dbo.STATE_CREATED),
        dbo.STATE_CREATED: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: (),
    }

    ACTIVE_STATES = set([dbo.STATE_CREATED])

    def __init__(self, static_values):
        if static_values.get('version', self.initial_version) != self.current_version:
            upgraded, static_values = self.upgrade(static_values)

            if upgraded and gmov(self.object_type) == self.current_version:
                etcd.put(
                    self.object_type, None, static_values.get('uuid'),
                    static_values)
                LOG.with_fields({
                    self.object_type: static_values['uuid']}).info(
                        'Online upgrade committed')

        super(Upload, self).__init__(static_values.get('uuid'),
                                     static_values.get('version'))
        self.__node = static_values['node']
        self.__created_at = static_values['created_at']

    @classmethod
    def upgrade(cls, static_values):
        changed = False
        starting_version = static_values.get('version', cls.initial_version)

        if static_values.get('version') == 2:
            cls._upgrade_metadata_to_attribute(static_values['uuid'])
            static_values['version'] = 3
            changed = True

        if changed:
            LOG.with_fields({
                cls.object_type: static_values['uuid'],
                'start_version': starting_version,
                'final_version': static_values.get('version')
            }).info('Object online upgraded')
        return changed, static_values

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
    def __iter__(self):
        for _, u in etcd.get_all('upload', None):
            u = Upload.from_db(u['uuid'])
            if not u:
                continue

            out = self.apply_filters(u)
            if out:
                yield out
