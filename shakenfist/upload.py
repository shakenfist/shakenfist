# Copyright 2021 Michael Still
import os
import time
import uuid

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import eventlog
from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType


LOG, _ = logs.setup(__name__)


class Upload(dbo):
    object_type = ObjectType.UPLOAD
    initial_version = 2
    current_version = 5

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
    def _upgrade_step_3_to_4(cls, static_values):
        # State migration to MariaDB is now handled by sf-ctl migrate-state-to-mariadb
        ...

    @classmethod
    def _upgrade_step_4_to_5(cls, static_values):
        # Static values migration to MariaDB is handled by
        # sf-ctl migrate-uploads-to-mariadb
        ...

    @classmethod
    def _db_create(cls, object_uuid, metadata):
        """Create an upload record in MariaDB instead of etcd."""
        mariadb.create_upload(
            uuid.UUID(object_uuid),
            metadata['node'],
            metadata['created_at'],
            metadata['version']
        )
        eventlog.add_event(EVENT_TYPE_AUDIT, cls.object_type, object_uuid,
                           'db record created', extra=metadata)

    @classmethod
    def _db_get(cls, object_uuid):
        """Get upload static values from MariaDB instead of etcd."""
        data = mariadb.get_upload(uuid.UUID(object_uuid))
        if not data:
            return None

        if data.get('version', 0) != cls.current_version:
            if not cls.upgrade_supported:
                from shakenfist import exceptions
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - {cls.object_type}: {data}')
        return data

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


def remove_stale_uploads_for_this_node():
    """Remove upload files on disk that no longer exist in the database.

    This function compares the upload files on disk for this node against
    the uploads recorded in the database. Any files that don't have a
    corresponding database record are deleted.
    """
    # Get all upload UUIDs that should be on this node from the database
    uploads_on_this_node = {
        u['uuid'] for u in mariadb.get_uploads(node=config.NODE_NAME)
    }

    upload_path = os.path.join(config.STORAGE_PATH, 'uploads')
    os.makedirs(upload_path, exist_ok=True)

    for upload_uuid in os.listdir(upload_path):
        if upload_uuid not in uploads_on_this_node:
            LOG.with_fields({
                'upload': upload_uuid
            }).info('Removing stale upload file')
            os.unlink(os.path.join(upload_path, upload_uuid))


def remove_abandoned_uploads():
    """Cleanup old uploads which were never completed.

    Uploads older than 7 days are considered abandoned and are deleted
    from both the database and disk.
    """
    cutoff = time.time() - 7 * 24 * 3600

    for upload_data in mariadb.get_uploads(created_before=cutoff):
        upload = Upload(upload_data)
        upload.add_event('cleaning up abandoned upload')
        upload.hard_delete()
