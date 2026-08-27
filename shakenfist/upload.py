# Copyright 2021 Michael Still
from __future__ import annotations

import os
import time
import uuid
from typing import Any

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist import eventlog
from shakenfist import mariadb
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.upload import UploadData
from shakenfist.util import callstack as util_callstack
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Upload(dbo):
    object_type = ObjectType.UPLOAD
    initial_version = 2
    current_version = 5

    # STORAGE_PATH-relative subdirectories this object type depends on to be
    # healthy on a node that hosts it (PLAN-node-resource-health). Uploads in
    # flight are staged in the uploads directory.
    health_dependencies = ['uploads']

    # docs/developer_guide/state_machine.md has a description of these states.
    state_targets: dict[str | None, tuple[str, ...]] = {  # type: ignore[assignment]
        None: (dbo.STATE_CREATED,),
        dbo.STATE_CREATED: (dbo.STATE_DELETED,),
        dbo.STATE_DELETED: (),
    }

    ACTIVE_STATES = {dbo.STATE_CREATED}

    def __init__(self, data: UploadData) -> None:
        # Apply lazy upgrades to the immutable Pydantic model if needed
        data = self.upgrade_pydantic_data(data, UploadData)

        super().__init__(data.uuid, data.version)
        self.__node: str = data.node
        self.__created_at: float = data.created_at

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values: dict[str, Any]) -> None:
        # Previous etcd metadata-to-attribute upgrade is a no-op now that
        # etcd is gone.
        ...

    @classmethod
    def _upgrade_step_3_to_4(cls, static_values: dict[str, Any]) -> None:
        ...

    @classmethod
    def _upgrade_step_4_to_5(cls, static_values: dict[str, Any]) -> None:
        ...

    @classmethod
    def _persist_pydantic_upgrade(  # type: ignore[override]
            cls, data: UploadData) -> None:
        """Persist an upgraded UploadData to MariaDB."""
        mariadb.update_upload(data)

    @classmethod
    def _db_create(cls, object_uuid: str, metadata: dict[str, Any]) -> None:
        """Create an upload record in MariaDB."""
        if not mariadb.create_upload(
            uuid.UUID(object_uuid),
            metadata['node'],
            metadata['created_at'],
            metadata['version']
        ):
            raise RuntimeError(f'Failed to create upload {object_uuid} in MariaDB')
        super()._db_create(object_uuid, metadata)

    @classmethod
    def _db_get(cls, object_uuid: str) -> UploadData | None:
        """Get upload static values from MariaDB instead of etcd."""
        try:
            db_uuid = uuid.UUID(object_uuid)
        except ValueError:
            # A name that is not UUID shaped cannot be in the database,
            # so it is a miss under from_db()'s not-found contract.
            return None
        data = mariadb.get_upload(db_uuid)
        if not data:
            return None

        if data.version != cls.current_version:
            if not cls.upgrade_supported:
                from shakenfist import exceptions
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - {cls.object_type}: {data}')
        return data

    @classmethod
    def from_db(cls, object_uuid: str,
                suppress_failure_audit: bool = False) -> 'Upload | None':
        """Load an Upload from the database.

        Override the base class from_db because _db_get returns a Pydantic
        UploadData model, not a dictionary. The base class from_db uses
        dict methods (get, in) that don't work with Pydantic models.
        """
        if not object_uuid:
            return None

        data = cls._db_get(object_uuid)
        if not data:
            if not suppress_failure_audit:
                eventlog.add_event(
                    EVENT_TYPE_AUDIT, cls.object_type, object_uuid,
                    'attempt to lookup non-existent object',
                    extra={'caller': util_callstack.get_caller(offset=-3)})
            return None

        return cls(data)

    @classmethod
    def new(cls, upload_uuid: str, node: str) -> Upload:
        created_at = time.time()
        metadata: dict[str, Any] = {
            'uuid': upload_uuid,
            'node': node,
            'created_at': created_at,
            'version': cls.current_version
        }
        Upload._db_create(upload_uuid, metadata)

        # Create UploadData for the new object
        data = UploadData(
            uuid=upload_uuid,  # type: ignore[arg-type]
            node=node,
            created_at=created_at,
            version=cls.current_version
        )
        u = Upload(data)
        u.state = Upload.STATE_CREATED  # type: ignore[misc]
        return u

    def hard_delete(self) -> None:
        mariadb.delete_upload(self.uuid)
        super().hard_delete()

    # Static values
    @property
    def node(self) -> str:
        return self.__node

    @property
    def created_at(self) -> float:
        return self.__created_at

    def external_view(self) -> dict[str, Any]:
        retval: dict[str, Any] = self._external_view()
        retval.update({
            'node': self.node,
            'created_at': self.created_at
        })
        return retval


def remove_stale_uploads_for_this_node() -> None:
    """Remove upload files on disk that no longer exist in the database.

    This function compares the upload files on disk for this node against
    the uploads recorded in the database. Any files that don't have a
    corresponding database record are deleted.
    """
    # Get all upload UUIDs that should be on this node from the database
    uploads_on_this_node: set[str] = {
        str(u.uuid) for u in mariadb.get_uploads(node=config.NODE_NAME)
    }

    upload_path = os.path.join(config.STORAGE_PATH, 'uploads')
    os.makedirs(upload_path, exist_ok=True)

    for upload_uuid in os.listdir(upload_path):
        # Upload files are named for their UUID. Anything else (such as
        # the resource health _heartbeat sentinel) is not an upload and
        # must not be garbage collected.
        if not util_general.valid_uuid4(upload_uuid):
            continue

        if upload_uuid not in uploads_on_this_node:
            LOG.with_fields({
                'upload': upload_uuid
            }).info('Removing stale upload file')
            os.unlink(os.path.join(upload_path, upload_uuid))


def remove_abandoned_uploads() -> None:
    """Cleanup old uploads which were never completed.

    Uploads older than 7 days are considered abandoned and are deleted
    from both the database and disk.
    """
    cutoff: float = time.time() - 7 * 24 * 3600

    for upload_data in mariadb.get_uploads(created_before=cutoff):
        upload = Upload(upload_data)
        upload.add_event(EVENT_TYPE_AUDIT, 'cleaning up abandoned upload')
        upload.hard_delete()
