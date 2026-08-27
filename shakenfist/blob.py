# Copyright 2021 Michael Still
# Please note: blobs are a "foundational" baseobject type, which means they
# should not rely on any other baseobjects for their implementation. This is
# done to help minimize circular import problems.
import copy
import hashlib
import numbers
import os
import pathlib
import random
import socket
import time
import uuid
from typing import Any
from typing import Optional
from typing import Union

import magic
from shakenfist_utilities import logs  # noreorder
from shakenfist_utilities import random as sf_random  # noreorder

from shakenfist import mariadb
from shakenfist.schema.blob_attributes import BlobAttributesData
from shakenfist.schema.blob_data import BlobData
from shakenfist.schema.blob_hash import BlobHash
from shakenfist.schema.blob_transfer import BlobTransfer
from shakenfist.schema.operations.baseclusteroperation \
    import PRIORITY
from shakenfist.schema.operations.node_blob_op \
    import create_and_enqueue as nbo_create_and_enqueue
from shakenfist.schema.operations.node_blob_op \
    import model_tasks as nbo_tasks
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import BLOB_HASH_ALGORITHMS
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import GiB
from shakenfist.schema.object_reference import ObjectReference
from shakenfist.schema.object_reference import references_to_grouped_dict
from shakenfist.schema.object_types import ObjectType
from shakenfist.schema.relationship_types import RelationshipType
from shakenfist.eventlog import add_event
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import BlobAlreadyBeingTransferred
from shakenfist.exceptions import BlobDependencyMissing
from shakenfist.exceptions import BlobFetchFailed
from shakenfist.exceptions import BlobMissing
from shakenfist.exceptions import BlobsMustHaveContent
from shakenfist.exceptions import BlobSizeCannotChange
from shakenfist.exceptions import BlobTransferSetupFailed
from shakenfist.exceptions import HashFailed
from shakenfist.node import Node
from shakenfist.util.access_tokens import request_namespace
from shakenfist.util import callstack as util_callstack
from shakenfist.node import Nodes
from shakenfist.node import nodes_by_free_disk_descending
from shakenfist.util import general as util_general
from shakenfist.util import image as util_image
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


def _local_node_uuid() -> Optional[str]:
    """Resolve the UUID of the current node.

    config.NODE_UUID may be None when the environment variable was not
    set.  Fall back to the persisted UUID file written by sentinel_first,
    and finally to a database lookup by FQDN.
    """
    node_uuid = config.NODE_UUID
    if node_uuid:
        return node_uuid

    node_uuid = Node._load_persisted_uuid()
    if node_uuid:
        return node_uuid

    n = Node.from_db(config.NODE_NAME)
    if n:
        return str(n.uuid)

    return None


# NOTE(mikal): blobs are immutable objects, that is their content cannot change
# once set. However, we don't always know the size or content of the blob when
# we reserve its UUID, so we do allow the size of the blob to be set after
# creation.
class Blob(dbo):
    object_type = ObjectType.BLOB
    initial_version = 8
    current_version = 11

    # STORAGE_PATH-relative subdirectories this object type depends on to be
    # healthy on a node that hosts it (PLAN-node-resource-health). Blob
    # replicas live in the blobs store.
    health_dependencies = ['blobs']

    # docs/developer_guide/state_machine.md has a description of these states.
    state_targets = {
        None: (dbo.STATE_INITIAL),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_ERROR, dbo.STATE_DELETED),
        dbo.STATE_CREATED: (dbo.STATE_ERROR, dbo.STATE_DELETED),
        dbo.STATE_ERROR: (dbo.STATE_DELETED),
        dbo.STATE_DELETED: (),
    }

    def __init__(self, data: BlobData) -> None:
        # Apply lazy upgrades to the immutable Pydantic model if needed
        data = self.upgrade_pydantic_data(data, BlobData)

        super().__init__(data.uuid, data.version)

        self.__modified: float = data.modified
        self.__fetched_at: float = data.fetched_at

        # Lazy-load attributes from MariaDB
        self.__attributes: Optional[BlobAttributesData] = None
        self.__attributes_loaded: bool = False

    def _load_attributes(self) -> Optional[BlobAttributesData]:
        """Load attributes from MariaDB."""
        if not self.__attributes_loaded:
            self.__attributes = mariadb.get_blob_attributes(self.uuid)
            self.__attributes_loaded = True
        return self.__attributes

    def _ensure_attributes(self) -> BlobAttributesData:
        """Ensure attributes record exists, creating with defaults if needed."""
        attrs = self._load_attributes()
        if attrs is None:
            attrs = BlobAttributesData(uuid=self.uuid)
            mariadb.create_blob_attributes(attrs)
            self.__attributes = attrs
            self.__attributes_loaded = True
        return attrs

    @classmethod
    def _upgrade_step_8_to_9(cls, static_values: dict[str, Any]) -> None:
        ...

    @classmethod
    def _upgrade_step_9_to_10(cls, static_values: dict[str, Any]) -> None:
        ...

    @classmethod
    def _upgrade_step_10_to_11(cls, static_values: dict[str, Any]) -> None:
        ...

    @classmethod
    def _persist_pydantic_upgrade(  # type: ignore[override]
            cls, data: BlobData) -> None:
        """Persist an upgraded BlobData to MariaDB."""
        mariadb.update_blob(data)

    @classmethod
    def _db_create(cls, object_uuid: str, metadata: dict[str, Any]) -> None:
        """Create a blob record in MariaDB."""
        if not mariadb.create_blob(
            uuid.UUID(object_uuid),
            metadata['modified'],
            metadata['fetched_at'],
            metadata['version']
        ):
            raise RuntimeError(f'Failed to create blob {object_uuid} in MariaDB')
        super()._db_create(object_uuid, metadata)

    @classmethod
    def _db_get(cls, object_uuid: Union[str, uuid.UUID]) -> BlobData | None:
        """Get blob static values from MariaDB instead of etcd."""
        if isinstance(object_uuid, uuid.UUID):
            db_uuid = object_uuid
        else:
            try:
                db_uuid = uuid.UUID(object_uuid)
            except ValueError:
                # A name that is not UUID shaped cannot be in the database,
                # so it is a miss under from_db()'s not-found contract --
                # callers such as storage scanners pass filenames here.
                return None
        data = mariadb.get_blob(db_uuid)
        if not data:
            return None

        if data.version != cls.current_version:
            if not cls.upgrade_supported:
                from shakenfist import exceptions
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - {cls.object_type}: {data}')
        return data

    @classmethod
    def from_db(cls, object_uuid: Union[str, uuid.UUID],
                suppress_failure_audit: bool = False) -> 'Blob | None':
        """Load a Blob from the database.

        Override the base class from_db because _db_get returns a Pydantic
        BlobData model, not a dictionary. The base class from_db uses
        dict methods (get, in) that don't work with Pydantic models.
        """
        if not object_uuid:
            return None

        data = cls._db_get(object_uuid)
        if not data:
            if not suppress_failure_audit:
                add_event(
                    EVENT_TYPE_AUDIT, cls.object_type, object_uuid,
                    'attempt to lookup non-existent object',
                    extra={'caller': util_callstack.get_caller(offset=-3)})
            return None

        return cls(data)

    @classmethod
    def normalize_timestamp(
            cls, timestamp: Union[float, int, str, None]
    ) -> float:
        # The timestamp is either a number (int or float, assumed to be epoch
        # seconds)...
        if isinstance(timestamp, numbers.Number):
            return float(timestamp)

        # Or the timestamp could be empty, at which point we just default to now.
        if timestamp is None:
            return time.time()

        # Or a HTTP last-modified timestamp like "Sun, 09 Jan 2022 23:05:25 GMT"
        # to be converted to epoch seconds. At this point, timestamp must be a str.
        assert isinstance(timestamp, str)
        t = time.strptime(timestamp, '%a, %d %b %Y %H:%M:%S %Z')
        return time.mktime(t)

    @classmethod
    def new(
            cls,
            blob_uuid: str,
            modified: Union[float, int, str, None],
            fetched_at: float,
            depends_on: Optional[str] = None
    ) -> 'Blob':
        normalized_modified = cls.normalize_timestamp(modified)
        metadata: dict[str, Any] = {
            'uuid': blob_uuid,
            'modified': normalized_modified,
            'fetched_at': fetched_at,
            'depends_on': depends_on,
            'version': cls.current_version
        }
        Blob._db_create(blob_uuid, metadata)

        # Create BlobData for the new object
        data = BlobData(
            uuid=blob_uuid,
            modified=normalized_modified,
            fetched_at=fetched_at,
            version=cls.current_version
        )
        b = Blob(data)
        b.state = Blob.STATE_INITIAL

        # Record the depends_on relationship in the object_references table
        if depends_on:
            b.add_depends_on_reference(depends_on)

        return b

    def external_view(self) -> dict[str, Any]:
        # If this is an external view, then mix back in attributes that users
        # expect
        out = self._external_view()

        # Get checksums from MariaDB (excludes internal fields like node and
        # verification timestamps)
        checksums = mariadb.get_valid_checksums(str(self.uuid))

        # The unfiltered references_from read already contains the DEPENDS_ON
        # and TRANSCODE rows, so derive those fields from it rather than
        # issuing the properties' filtered reads as well (issue 3876).
        refs_from = mariadb.get_references_from(ObjectType.BLOB, self.uuid)

        out.update({
            'size': self.size,
            'modified': self.modified,
            'fetched_at': self.fetched_at,
            'depends_on': self._depends_on_from_references(refs_from),
            'transcodes': self._transcodes_from_references(refs_from),
            'reference_count': self.ref_count,
            'sha512': checksums.get('sha512'),
            'last_used': self.last_used,
            'checksums': checksums
        })

        if request_namespace() == 'system':
            out['locations'] = self.locations
            for loc in self.incomplete_locations:
                out['locations'].append(
                    f'{loc["node"]} ({loc["percentage"]:.1f}%)')

        # Include information about the blob
        out.update(self.info)

        # Add object references (what references this blob and what this blob
        # references)
        refs_to = mariadb.get_references_to(ObjectType.BLOB, self.uuid)
        out['references_to'] = references_to_grouped_dict(refs_to)
        out['references_from'] = references_to_grouped_dict(refs_from)

        return out

    # Static values
    @property
    def modified(self) -> float:
        return self.__modified

    @property
    def fetched_at(self) -> float:
        return self.__fetched_at

    @staticmethod
    def _depends_on_from_references(
            refs: list[ObjectReference]) -> Optional[str]:
        for ref in refs:
            if ref.relationship == RelationshipType.DEPENDS_ON:
                return str(ref.target_uuid)
        return None

    @property
    def depends_on(self) -> Optional[str]:
        """Return the UUID of the blob this blob depends on, if any.

        This queries the object_references table for a DEPENDS_ON relationship
        from this blob to another blob.
        """
        refs = mariadb.get_references_from(
            ObjectType.BLOB, self.uuid, RelationshipType.DEPENDS_ON)
        return self._depends_on_from_references(refs)

    # Values routed to attributes (stored in blob_attributes table)
    @property
    def size(self) -> int:
        attrs = self._load_attributes()
        return attrs.size if attrs else 0

    @size.setter
    def size(self, new_size: int) -> None:
        if new_size < 1:
            raise BlobsMustHaveContent()

        attrs = self._ensure_attributes()
        if attrs.size > 0:
            raise BlobSizeCannotChange()

        # Update in MariaDB
        new_attrs = BlobAttributesData(
            uuid=attrs.uuid,
            size=new_size,
            info=attrs.info,
            last_used=attrs.last_used,
            expires_at=attrs.expires_at
        )
        mariadb.update_blob_attributes(new_attrs, fields=['size'])
        self.__attributes = new_attrs

    @property
    def locations(self) -> list[str]:
        """Return list of node names where this blob is fully present.

        This queries the object_references table for BLOB_LOCATION relationships
        where nodes reference this blob.
        """
        refs = mariadb.get_references_to(
            ObjectType.BLOB, self.uuid, RelationshipType.BLOB_LOCATION)
        return [str(ref.source_uuid) for ref in refs]

    def add_location(self, location: str) -> None:
        """Record that this blob is present on the given node."""
        self.record_usage()
        mariadb.record_relationship(
            ObjectType.NODE, location,
            RelationshipType.BLOB_LOCATION, None,
            ObjectType.BLOB, self.uuid)

    def remove_location(self, location: str) -> None:
        """Remove the record that this blob is present on the given node."""
        mariadb.remove_relationship(
            ObjectType.NODE, location,
            RelationshipType.BLOB_LOCATION, None,
            ObjectType.BLOB, self.uuid)

    @property
    def incomplete_locations(self) -> list[dict[str, Any]]:
        """Return list of in-progress transfers for this blob.

        Each transfer is represented as a dict with:
            - node: The node name receiving the blob
            - percentage: Transfer progress (0.0-100.0)
        """
        transfers = mariadb.get_blob_transfers_for_blob(str(self.uuid))
        return [{'node': t.requesting_node, 'percentage': t.percentage}
                for t in transfers]

    @property
    def incomplete_healthy_locations(self) -> list[dict[str, Any]]:
        """Return incomplete_locations filtered to only active nodes."""
        absent_nodes = Nodes([], prefilter='inactive')
        out: list[dict[str, Any]] = []
        for loc in self.incomplete_locations:
            if loc['node'] not in absent_nodes:
                out.append(loc)
        return out

    @property
    def info(self) -> dict[str, Any]:
        attrs = self._load_attributes()
        return attrs.info if attrs else {}

    @property
    def ref_count(self) -> int:
        """Return the number of references to this blob from object_references.

        Node location relationships are excluded from the count because they
        represent where the blob is stored, not what uses it. Including
        locations would prevent blobs from ever being considered unused and
        reaped.
        """
        return mariadb.count_references_to(
            ObjectType.BLOB, self.uuid,
            exclude_relationships=[RelationshipType.BLOB_LOCATION])

    @staticmethod
    def _transcodes_from_references(
            refs: list[ObjectReference]) -> dict[str, str]:
        result: dict[str, str] = {}
        for ref in refs:
            if (ref.relationship == RelationshipType.TRANSCODE and
                    ref.relationship_value is not None):
                result[ref.relationship_value] = str(ref.target_uuid)
        return result

    @property
    def transcoded(self) -> dict[str, str]:
        """Return a dict of {style: blob_uuid} for all transcodes of this blob."""
        refs = mariadb.get_references_from(
            ObjectType.BLOB, self.uuid, RelationshipType.TRANSCODE)
        return self._transcodes_from_references(refs)

    def add_transcode(self, style: str, blob_uuid: str) -> bool:
        """Record a transcode relationship from this blob to a transcoded blob.

        Returns True if the transcode was recorded, False if it already exists.
        """
        self.record_usage()
        # Check if this transcode already exists
        transcoded = self.transcoded
        if style in transcoded:
            # This is a duplicate transcode
            return False

        # Record the relationship in MariaDB
        mariadb.record_relationship(
            ObjectType.BLOB, self.uuid,
            RelationshipType.TRANSCODE, style,
            ObjectType.BLOB, blob_uuid)
        return True

    def remove_transcodes(self) -> None:
        """Remove all transcode relationships from this blob."""
        mariadb.remove_all_references_from(
            ObjectType.BLOB, self.uuid, RelationshipType.TRANSCODE)

    # =========================================================================
    # Reference management methods
    # These methods are the preferred way to record relationships to this blob.
    # They ensure record_usage() is called to prevent premature cleanup.
    #
    # Each method is decorated with @restrict_caller to enforce that only the
    # appropriate modules call them. This provides soft enforcement (warnings)
    # to catch architectural violations during development.
    # =========================================================================

    @util_callstack.restrict_caller('shakenfist.instance', 'shakenfist.tests')
    def add_disk_reference(self, instance_uuid: str, disk_idx: int) -> None:
        """Record that an instance uses this blob as a disk."""
        self.record_usage()
        mariadb.record_relationship(
            ObjectType.INSTANCE, instance_uuid,
            RelationshipType.DISK, str(disk_idx),
            ObjectType.BLOB, self.uuid)

    @util_callstack.restrict_caller('shakenfist.instance', 'shakenfist.tests')
    def remove_disk_reference(self, instance_uuid: str, disk_idx: int) -> None:
        """Remove the record that an instance uses this blob as a disk."""
        mariadb.remove_relationship(
            ObjectType.INSTANCE, instance_uuid,
            RelationshipType.DISK, str(disk_idx),
            ObjectType.BLOB, self.uuid)

    @util_callstack.restrict_caller('shakenfist.instance', 'shakenfist.tests')
    def add_nvram_template_reference(self, instance_uuid: str) -> None:
        """Record that an instance uses this blob as an NVRAM template."""
        self.record_usage()
        mariadb.record_relationship(
            ObjectType.INSTANCE, instance_uuid,
            RelationshipType.NVRAM_TEMPLATE, None,
            ObjectType.BLOB, self.uuid)

    @util_callstack.restrict_caller('shakenfist.instance', 'shakenfist.tests')
    def remove_nvram_template_reference(self, instance_uuid: str) -> None:
        """Remove the record that an instance uses this blob as NVRAM template."""
        mariadb.remove_relationship(
            ObjectType.INSTANCE, instance_uuid,
            RelationshipType.NVRAM_TEMPLATE, None,
            ObjectType.BLOB, self.uuid)

    @util_callstack.restrict_caller('shakenfist.artifact', 'shakenfist.tests')
    def add_artifact_index_reference(
            self, artifact_uuid: str, index: int) -> None:
        """Record that an artifact references this blob at the given index."""
        self.record_usage()
        mariadb.record_relationship(
            ObjectType.ARTIFACT, artifact_uuid,
            RelationshipType.ARTIFACT_INDEX, str(index).zfill(12),
            ObjectType.BLOB, self.uuid)

    @util_callstack.restrict_caller('shakenfist.artifact', 'shakenfist.tests')
    def remove_artifact_index_reference(
            self, artifact_uuid: str, index: int) -> None:
        """Remove the record that an artifact references this blob."""
        mariadb.remove_relationship(
            ObjectType.ARTIFACT, artifact_uuid,
            RelationshipType.ARTIFACT_INDEX, str(index).zfill(12),
            ObjectType.BLOB, self.uuid)

    @util_callstack.restrict_caller('shakenfist.blob', 'shakenfist.tests')
    def add_depends_on_reference(self, parent_blob_uuid: str) -> None:
        """Record that this blob depends on another blob (e.g., snapshot)."""
        self.record_usage()
        mariadb.record_relationship(
            ObjectType.BLOB, self.uuid,
            RelationshipType.DEPENDS_ON, None,
            ObjectType.BLOB, parent_blob_uuid)

    @util_callstack.restrict_caller(
        'shakenfist.daemons.sidechannel', 'shakenfist.tests')
    def add_agent_output_reference(
            self, agentop_uuid: str, output_type: str) -> None:
        """Record that an agent operation produced this blob as output."""
        self.record_usage()
        mariadb.record_relationship(
            ObjectType.AGENTOPERATION, agentop_uuid,
            RelationshipType.AGENT_OUTPUT, output_type,
            ObjectType.BLOB, self.uuid)

    @util_callstack.restrict_caller(
        'shakenfist.operations.agentoperation', 'shakenfist.tests')
    def remove_agent_output_reference(
            self, agentop_uuid: str, output_type: str) -> None:
        """Remove the record that an agent operation produced this blob."""
        mariadb.remove_relationship(
            ObjectType.AGENTOPERATION, agentop_uuid,
            RelationshipType.AGENT_OUTPUT, output_type,
            ObjectType.BLOB, self.uuid)

    @property
    def last_used(self) -> Optional[float]:
        attrs = self._load_attributes()
        return attrs.last_used if attrs else None

    def record_usage(self) -> None:
        now = time.time()
        self._ensure_attributes()
        # Use optimized single-column update
        mariadb.update_blob_last_used(self.uuid, now)
        # Update local cache
        if self.__attributes:
            self.__attributes.last_used = now

    @property
    def expires_at(self) -> float:
        attrs = self._load_attributes()
        return attrs.expires_at if attrs else 0.0

    def set_lifetime(self, seconds_from_now: float) -> None:
        expires = time.time() + seconds_from_now
        attrs = self._ensure_attributes()
        new_attrs = BlobAttributesData(
            uuid=attrs.uuid,
            size=attrs.size,
            info=attrs.info,
            last_used=attrs.last_used,
            expires_at=expires
        )
        mariadb.update_blob_attributes(new_attrs, fields=['expires_at'])
        self.__attributes = new_attrs

    # Operations
    def add_node_location(self) -> None:
        self.add_location(config.NODE_NAME)

    def drop_node_location(self, node: str = config.NODE_NAME) -> None:
        self.remove_location(node)

    def observe(self) -> None:
        self.add_node_location()

        # Observing a blob can move it from initial to created, but it should not
        # move it from deleted to created.
        if self.state.value == self.STATE_INITIAL:
            self.state = self.STATE_CREATED

        if not self.info:
            blob_path = Blob.filepath(self.uuid)

            # We put a bunch of information from "qemu-img info" into the
            # blob because its helpful. However, there are some values we
            # don't want to persist.
            info = util_image.identify(blob_path)
            for key in ['corrupt', 'image', 'lazy refcounts', 'refcount bits']:
                if key in info:
                    del info[key]

            info['mime-type'] = magic.Magic(mime=True).from_file(blob_path)

            # Store info in MariaDB blob_attributes table
            attrs = self._ensure_attributes()
            new_attrs = BlobAttributesData(
                uuid=attrs.uuid,
                size=attrs.size,
                info=info,
                last_used=attrs.last_used,
                expires_at=attrs.expires_at
            )
            mariadb.update_blob_attributes(new_attrs, fields=['info'])
            self.__attributes = new_attrs

    def cascading_delete(self) -> None:
        self.state = self.STATE_DELETED

        # Remove all transcode references from this blob
        self.remove_transcodes()

        # Remove depends_on reference from this blob to its dependency
        mariadb.remove_all_references_from(
            ObjectType.BLOB, self.uuid, RelationshipType.DEPENDS_ON)

    def ensure_local(
            self, instance_object: Any = None, wait_for_other_transfers: bool = True
    ) -> None:
        affected_objects = [self]
        if instance_object:
            affected_objects.append(instance_object)

        if self.state.value != self.STATE_CREATED:
            add_event_multi(
                EVENT_TYPE_STATUS, affected_objects,
                'blob not in created state, replication to this node cancelled')
            return

        # Replicate any blob this blob depends on
        if self.depends_on:
            dep_blob = Blob.from_db(self.depends_on)
            if not dep_blob:
                raise BlobDependencyMissing(self.depends_on)
            dep_blob.ensure_local(instance_object=instance_object)

        # If the blob exists already, we're done
        blob_path = Blob.filepath(self.uuid)
        if os.path.exists(blob_path):
            self.observe()
            return

        add_event_multi(
            EVENT_TYPE_STATUS, affected_objects, 'replicating blob to this node')
        partial_path = blob_path + '.partial'
        while os.path.exists(partial_path):
            st = os.stat(partial_path)
            if time.time() - st.st_mtime > 300:
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    ('no activity on previous partial download in more than '
                     'five minutes. Removing and re-attempting.'),
                    extra={
                        'partial file age': round(time.time() - st.st_mtime, 2)
                    })
                os.unlink(partial_path)
            else:
                if not wait_for_other_transfers:
                    raise BlobAlreadyBeingTransferred()

                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    'waiting for existing download to complete',
                    extra={
                        'partial file age': round(time.time() - st.st_mtime, 2)
                    }
                )
                time.sleep(10)

        # If the blob exists after waiting for another partial transfer,
        # we're done
        if os.path.exists(blob_path):
            add_event_multi(
                EVENT_TYPE_AUDIT, affected_objects, 'blob now exists on this node')
            self.observe()
            return

        # Fetch with a few retries
        attempts = 0
        while True:
            try:
                with util_concurrency.NodeLock(f'blob-{self.uuid}-transfer'):
                    # Check the blob didn't show up without us
                    if os.path.exists(blob_path):
                        self.observe()
                        return

                    # Attempt a transfer
                    self._attempt_transfer(
                        affected_objects, partial_path, blob_path)
                    return
            except (ConnectionRefusedError, BlobTransferSetupFailed,
                    BlobFetchFailed) as e:
                attempts += 1
                time.sleep(10)
                if attempts > 3:
                    raise BlobFetchFailed(
                        'Repeated attempts to fetch blob failed: %s' % e)

    # This method assumes the caller is holding the 'blob-{self.uuid}-transfer'
    # external lock. Luckily the only caller right now is the one directly
    # above here.
    def _attempt_transfer(
            self, affected_objects: list[Any], partial_path: str, blob_path: str
    ) -> None:
        add_event_multi(
            EVENT_TYPE_AUDIT, affected_objects, 'attempting transfer')
        with open(partial_path, 'wb') as f:
            locations = self.locations
            for n in Nodes([], prefilter='inactive'):
                if n.fqdn in locations:
                    LOG.with_fields({
                        'node': n,
                        'state': n.state.value}).debug(
                        'Node is inactive, ignoring '
                        'blob location')
                    locations.remove(n.fqdn)
            if len(locations) == 0:
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    'there are no online sources for this blob')
                raise BlobMissing(
                    f'There are no online sources for blob {self.uuid}')

            random.shuffle(locations)
            transfer_name = sf_random.random_id()
            token = sf_random.random_id()
            now = time.time()

            # Create transfer request in MariaDB
            transfer = BlobTransfer(
                source_node=locations[0],
                transfer_name=transfer_name,
                requesting_node=config.NODE_MESH_IP,
                blob_uuid=str(self.uuid),
                token=token,
                server_state=dbo.STATE_INITIAL,
                port=None,
                percentage=0.0,
                created_at=now,
                updated_at=now
            )

            direction_info = f'({locations[0]} -> {config.NODE_NAME})'
            affected_objects = copy.deepcopy(affected_objects)
            affected_objects.append(('node', config.NODE_NAME))
            affected_objects.append(('node', locations[0]))

            mariadb.create_blob_transfer(transfer)
            add_event_multi(
                EVENT_TYPE_AUDIT, affected_objects,
                f'created transfer request {direction_info}',
                extra=transfer.external_view())

            # Poll for server to set up the transfer
            waiting_time = time.time()
            while time.time() - waiting_time < 30:
                transfer = mariadb.get_blob_transfer(locations[0], transfer_name)
                if transfer and transfer.server_state == dbo.STATE_CREATED:
                    break
                time.sleep(1)

            if not transfer or transfer.server_state != dbo.STATE_CREATED:
                state = transfer.server_state if transfer else 'missing'
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    f'transfer setup failed {direction_info}',
                    extra={'server_state': state})
                mariadb.delete_blob_transfer(locations[0], transfer_name)
                raise BlobTransferSetupFailed(
                    f'transfer {transfer_name} failed to setup, state is {state}')

            add_event_multi(
                EVENT_TYPE_AUDIT, affected_objects,
                f'transfer setup succeeded {direction_info}',
                extra=transfer.external_view())

            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect((locations[0], transfer.port))
            client.send(token.encode('utf-8'))

            total_bytes_received = 0
            next_percentage = 10

            last_event = time.time()
            sha512_hash = hashlib.sha512()
            with open(partial_path, 'wb') as f:
                while d := client.recv(8000):
                    if len(d) == 0:
                        break

                    f.write(d)
                    sha512_hash.update(d)
                    total_bytes_received += len(d)

                    percentage = total_bytes_received / int(self.size) * 100.0
                    if ((next_percentage - percentage) < 0 or
                            time.time() - last_event > 30):
                        add_event_multi(
                            EVENT_TYPE_STATUS, affected_objects,
                            f'fetching required blob {direction_info}',
                            extra={
                                'percentage': int(percentage)
                            }
                        )
                        if (next_percentage - percentage) < 0:
                            next_percentage += 10
                        last_event = time.time()

            if total_bytes_received != int(self.size):
                add_event_multi(
                    EVENT_TYPE_STATUS, affected_objects,
                    f'did not fetch entire blob, cleaning up {direction_info}',
                    extra={
                        'received': total_bytes_received,
                        'expected': self.size
                    }
                )
                if os.path.exists(partial_path):
                    os.unlink(partial_path)
                raise BlobFetchFailed(
                    'The amount of fetched data does not match the stored size. We '
                    f'fetched {total_bytes_received} bytes, but expected {self.size}.')

            if not self.verify_size(partial=True):
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    f'fetching required blob failed, incorrect size {direction_info}')
                raise BlobFetchFailed(
                    f'Fetching required blob {self.uuid} failed. We fetched '
                    f'{total_bytes_received} bytes, but expected {self.size}.')

            if not self.verify_checksum(hash=sha512_hash.hexdigest()):
                add_event_multi(
                    EVENT_TYPE_AUDIT, affected_objects,
                    f'fetching required blob failed, incorrect checksum {direction_info}')
                raise BlobFetchFailed(
                    f'Fetching required blob {self.uuid} failed. Incorrect checksum.')

            os.rename(partial_path, blob_path)
            add_event_multi(
                EVENT_TYPE_AUDIT, affected_objects,
                f'fetching required blob complete {direction_info}')
            self.observe()

    def request_replication(self, allow_excess: int = 0) -> None:
        present_nodes = list(Nodes([], prefilter='active'))
        present_nodes_len = len(present_nodes)
        absent_nodes = list(Nodes([], prefilter='inactive'))

        with self.get_lock_attr('locations', 'Request replication'):
            # We take current transfers into account when replicating, to avoid
            # over replicating very large blobs
            current_transfers = 0
            for loc in self.incomplete_locations:
                if loc['node'] not in absent_nodes:
                    current_transfers += 1

            locations = self.locations

            # Filter out absent locations
            for node_name in self.locations:
                n = Node.from_db(node_name)
                if not n:
                    locations.remove(node_name)
                elif n.state.value != Node.STATE_CREATED:
                    locations.remove(node_name)

            replica_count = len(locations)
            if replica_count == 0:
                self.log.debug('No available replicas, giving up')
                return

            targets = (config.BLOB_REPLICATION_FACTOR + current_transfers +
                       allow_excess - replica_count)

            if (replica_count + current_transfers) == present_nodes_len:
                self.log.debug('Run out of nodes to replicate to, giving up')
                return

            self.log.info('Desired replica count is %d, we have %d, and %d inflight, '
                          'excess of %d requested, target is therefore %d new copies'
                          % (config.BLOB_REPLICATION_FACTOR, replica_count,
                             current_transfers, allow_excess, targets))
            if targets > 0:
                blob_size_gb = int(int(self.size) / GiB)
                # The helper compares against each node's own reservation-aware
                # headroom, so we only require room for the blob itself here.
                nodes = nodes_by_free_disk_descending(
                    minimum=blob_size_gb,
                    intention='blobs')

                # Don't copy to locations which already have the blob
                for n in self.locations:
                    if n in nodes:
                        nodes.remove(n)

                for node_fqdn in nodes[:targets]:
                    node_obj = Node.from_db(node_fqdn)
                    if not node_obj:
                        continue
                    nbo_create_and_enqueue(
                        str(node_obj.uuid), self.uuid, [nbo_tasks.ensure_local], PRIORITY.background_high_io)
                    self.log.with_fields({'node': node_fqdn}).info('Instructed to replicate blob')

    def register(self, request_checksums: bool = True) -> None:
        # We don't remove the partial file until we've finished registering the
        # blob to avoid deletion races. Note that this _must_ be a hard link,
        # which is why we don't use util_general.link().
        dest_path = self.filepath(self.uuid)
        os.link(dest_path + '.partial', dest_path)

        if self.size == 0:
            st = os.stat(dest_path + '.partial')
            self.size = st.st_size

        self.state = self.STATE_CREATED
        self.observe()

        # Request checksums be calculated
        if request_checksums:
            node_uuid = _local_node_uuid()
            if node_uuid:
                nbo_create_and_enqueue(
                    node_uuid,
                    self.uuid,
                    [nbo_tasks.verify_size_and_checksum],
                    PRIORITY.background_high_io)
            else:
                self.log.warning(
                    'Cannot enqueue checksum operation, '
                    'local node UUID is unknown')

        self.request_replication()
        os.unlink(dest_path + '.partial')

    @staticmethod
    def filedir(blob_uuid: Union[str, uuid.UUID]) -> str:
        blob_uuid = str(blob_uuid)
        path = os.path.join(config.STORAGE_PATH, 'blobs', blob_uuid[0:2])
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def filepath(blob_uuid: Union[str, uuid.UUID]) -> str:
        blob_uuid = str(blob_uuid)
        return os.path.join(Blob.filedir(blob_uuid), blob_uuid)

    def _remove_corrupt_blob(self) -> None:
        blob_path = Blob.filepath(self.uuid)
        if os.path.exists(blob_path):
            os.unlink(blob_path)
        if os.path.exists(blob_path + '.partial'):
            os.unlink(blob_path + '.partial')
        self.drop_node_location(config.NODE_NAME)

    def verify_size(self, partial: bool = False) -> bool:
        blob_path = Blob.filepath(self.uuid)
        if partial:
            blob_path += '.partial'

        st = os.stat(blob_path)
        if self.size != st.st_size:
            self.add_event(EVENT_TYPE_AUDIT,
                           'blob failed size validation',
                           extra={
                               'stored_size': self.size,
                               'node_size': st.st_size,
                               'node': config.NODE_NAME
                           })
            self._remove_corrupt_blob()
            return False
        return True

    def hard_delete(self) -> None:
        mariadb.delete_blob_hashes(str(self.uuid))
        mariadb.delete_blob_transfers_for_blob(str(self.uuid))
        mariadb.delete_blob_attributes(self.uuid)
        mariadb.delete_blob(self.uuid)
        super().hard_delete()

    def verify_checksum(self, hash: Optional[str] = None, urgent: bool = True) -> bool:
        # This method is focussed on sha512 hashes at the moment, but I also
        # want it to be able to do other hash types later -- for example OVA
        # support needs sha1 or sha256, and xxhash is a lot faster. So for now
        # we always make sure there is a sha512, but if we're not in a hurry
        # we'll calculate a few others just once as well.
        file_path = self.filepath(self.uuid)
        blob_uuid = str(self.uuid)
        now = time.time()

        try:
            if hash:
                sha512_hash = hash
            else:
                sha512_hash = util_concurrency.hash_file(file_path, 'sha512')

            # Get existing hashes for this blob on this node from MariaDB
            existing_hashes = mariadb.get_blob_hashes(blob_uuid, config.NODE_NAME)
            existing_by_alg = {h.algorithm: h for h in existing_hashes}

            # Check for hash algorithms we don't have yet
            needs_rehashing = False
            extra_hashes = {}
            for alg in BLOB_HASH_ALGORITHMS:
                if alg not in existing_by_alg:
                    if not urgent:
                        extra_hashes[alg] = \
                            util_concurrency.hash_file(file_path, alg)
                    else:
                        needs_rehashing = True

        except HashFailed as e:
            # Being unable to compute a hash at all is a different failure
            # to a checksum mismatch, and "file not found" is a different
            # failure to "disk is dying". Record why, with the blob uuid
            # attached, before deciding what to do (issue 3744).
            failure_fields = {
                'error': e.error,
                'error_text': e.error_text,
                'algorithm': e.algorithm,
                'node': config.NODE_NAME
            }
            self.log.with_fields(failure_fields).error(
                'Unable to verify blob checksum')
            self.add_event(EVENT_TYPE_AUDIT,
                           'blob checksum verification error',
                           extra=failure_fields)

            if e.error == 'FILE_NOT_FOUND':
                # This node claims to hold a replica which is not on disk,
                # so the location record is wrong. Drop it and let the
                # replicator recover the replica count elsewhere.
                self._remove_corrupt_blob()
                return False

            # Anything else (hasher missing, I/O error) might be transient,
            # so keep the replica. last_verified_at is not updated, which
            # means the periodic checksum sweep will retry this node.
            raise

        # If we're in a hurry but extra hashes are missing, enqueue those as
        # background tasks
        if needs_rehashing:
            node_uuid = _local_node_uuid()
            if node_uuid:
                nbo_create_and_enqueue(
                    node_uuid,
                    self.uuid,
                    [nbo_tasks.verify_size_and_checksum],
                    PRIORITY.background_high_io)
            else:
                self.log.warning(
                    'Cannot enqueue rehash operation, '
                    'local node UUID is unknown')

        # Validate sha512 hash against stored value
        is_new_hash = 'sha512' not in existing_by_alg
        if 'sha512' in existing_by_alg:
            stored_hash = existing_by_alg['sha512'].hash_value
            if stored_hash != sha512_hash:
                self.add_event(EVENT_TYPE_AUDIT,
                               'blob failed checksum validation',
                               extra={
                                   'stored_hash': stored_hash,
                                   'node_hash': sha512_hash,
                                   'node': config.NODE_NAME
                               })
                self._remove_corrupt_blob()
                return False
            else:
                self.add_event(EVENT_TYPE_AUDIT,
                               'blob checksum verified',
                               extra={
                                   'algorithm': 'sha512',
                                   'node': config.NODE_NAME
                               })

        # Upsert sha512 hash (and any extra hashes we calculated)
        all_hashes = {'sha512': sha512_hash}
        all_hashes.update(extra_hashes)

        for alg, hash_value in all_hashes.items():
            # Note: computed_at is only used on first insert; the upsert
            # preserves the original value on subsequent updates.
            blob_hash = BlobHash(
                blob_uuid=blob_uuid,
                node=config.NODE_NAME,
                algorithm=alg,
                hash_value=hash_value,
                file_size=self.size,
                computed_at=now,
                last_verified_at=now,
                verification_status='valid',
                error_message=None
            )
            mariadb.upsert_blob_hash(blob_hash)

        # Log event for hash recording
        if is_new_hash or extra_hashes:
            new_algorithms = list(extra_hashes.keys())
            if is_new_hash:
                new_algorithms.insert(0, 'sha512')
            self.add_event(EVENT_TYPE_AUDIT,
                           'blob hash recorded',
                           extra={
                               'algorithms': new_algorithms,
                               'node': config.NODE_NAME
                           })

        return True


def snapshot_disk(
        disk: dict[str, Any],
        blob_uuid: str,
        related_object: Any = None,
        thin: bool = False
) -> Optional[Blob]:
    if not os.path.exists(disk['path']):
        return None
    dest_path = Blob.filepath(blob_uuid)

    # Actually make the snapshot
    depends_on = None
    with util_general.RecordedOperation('snapshot %s' % disk['device'], related_object):
        depends_on = util_image.snapshot(
            disk['path'], dest_path + '.partial', thin=thin)

    # Check that the dependency (if any) actually exists. This test can fail when
    # the blob used to start an instance has been deleted already.
    if depends_on:
        dep_blob = Blob.from_db(depends_on)
        if not dep_blob or dep_blob.state.value != Blob.STATE_CREATED:
            raise BlobDependencyMissing(
                'Snapshot depends on blob UUID %s, which is missing' % depends_on)

    # And make the associated blob. Note that we deliberately don't calculate the
    # snapshot checksum here, as this makes large snapshots even slower for users.
    # The checksum will "catch up" when the scheduled verification occurs.
    b = Blob.new(blob_uuid, time.time(), time.time(), depends_on=depends_on)
    b.register()
    return b


def http_fetch(
        url: str, resp: Any, b: Blob, affected_objects: list[Any]
) -> Blob:
    fetched = 0

    if resp.headers.get('Content-Length'):
        total_size = int(resp.headers.get('Content-Length'))
    else:
        total_size = None

    dest_path = Blob.filepath(b.uuid)

    md5_hash = hashlib.md5()
    sha512_hash = hashlib.sha512()

    percentage: float = 0
    next_percentage: float = 10
    last_event = time.time()
    with open(dest_path + '.partial', 'wb') as f:
        for chunk in resp.iter_content(chunk_size=8192):
            fetched += len(chunk)
            f.write(chunk)
            md5_hash.update(chunk)
            sha512_hash.update(chunk)

            if total_size:
                percentage = fetched / total_size * 100.0

            if ((next_percentage - percentage) < 0 or
                    time.time() - last_event > 30):
                add_event_multi(
                    EVENT_TYPE_STATUS, affected_objects,
                    'fetching required HTTP resource',
                    extra={
                        'url': url,
                        'percentage': int(percentage),
                        'bytes_fetched': fetched
                    })
                if (next_percentage - percentage) < 0:
                    next_percentage += 10
                last_event = time.time()

    add_event_multi(
        EVENT_TYPE_USAGE, affected_objects,
        'fetching required HTTP resource complete',
        extra={
            'url': url,
            'bytes_fetched': fetched
        })

    # Import the newly fetched blob
    b.size = fetched
    b.verify_checksum(hash=sha512_hash.hexdigest())
    b.register(request_checksums=False)
    return b


def from_memory(content: bytes) -> Blob:
    blob_uuid = str(uuid.uuid4())
    with open(Blob.filepath(blob_uuid), 'wb') as f:
        f.write(content)

    b = Blob.new(blob_uuid, time.time(), time.time())
    b.size = len(content)
    b.state = Blob.STATE_CREATED
    b.observe()
    b.request_replication()
    return b


def observe_local_blobs() -> int:
    """Observe all blob files on this node to update BLOB_LOCATION references.

    This function scans the local blob storage directory and calls observe()
    on each valid blob. This updates the BLOB_LOCATION reference in MariaDB
    (with last_active timestamp).

    This replaces the cluster daemon's periodic cache rebuild, making each
    node authoritative for its own blob locations.

    Returns:
        int: The number of blobs observed.
    """

    blob_path = os.path.join(config.STORAGE_PATH, 'blobs')
    if not os.path.exists(blob_path):
        return 0

    observed_count = 0
    try:
        p = pathlib.Path(blob_path)
        for path_entry in p.glob('**/*'):
            entpath = str(path_entry)

            if not os.path.isfile(entpath):
                continue

            # Blob files are named for their UUID. Anything else in the
            # store (_version markers, .partial transfers, the resource
            # health _heartbeat sentinel) is not a blob.
            blob_uuid = entpath.split('/')[-1]
            if not util_general.valid_uuid4(blob_uuid):
                continue

            b = Blob.from_db(blob_uuid, suppress_failure_audit=True)
            if b and b.state.value == Blob.STATE_CREATED:
                # Calling observe() updates the BLOB_LOCATION reference
                # in MariaDB (with last_active timestamp)
                b.observe()
                observed_count += 1

    except FileNotFoundError:
        ...

    return observed_count
