# Copyright 2021 Michael Still
import uuid as uuid_mod
from functools import partial
from typing import Any
from typing import Optional
from typing import Union
from uuid import uuid4

from shakenfist_utilities import logs  # noreorder

from shakenfist import baseobject
from shakenfist import blob
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectWithOperations as dbowo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.eventlog import add_event
from shakenfist.namespace import namespace_is_trusted
from shakenfist.schema.artifact_attributes import ArtifactAttributesData
from shakenfist.schema.artifact_data import ArtifactData
from shakenfist.schema.object_filter import ObjectFilterCriteria
from shakenfist.schema.object_reference import references_to_grouped_dict
from shakenfist.schema.object_types import ObjectType
from shakenfist.util import callstack as util_callstack
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


ARTIFACT_URL = 'sf://artifact/'
BLOB_URL = 'sf://blob/'
INSTANCE_URL = 'sf://instance/'
LABEL_URL = 'sf://label/'
SNAPSHOT_URL = 'sf://snapshot/'
UPLOAD_URL = 'sf://upload/'


class Artifact(dbowo):
    object_type = ObjectType.ARTIFACT
    initial_version = 8
    current_version = 9

    # docs/developer_guide/state_machine.md has a description of these states.
    state_targets = {
        None: (dbo.STATE_INITIAL),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: (),
    }

    ACTIVE_STATES = {dbo.STATE_INITIAL, dbo.STATE_CREATED, dbo.STATE_ERROR}

    TYPE_SNAPSHOT = 'snapshot'
    TYPE_LABEL = 'label'
    TYPE_IMAGE = 'image'
    TYPE_OTHER = 'other'

    def __init__(self, static_values):
        # Accept either a Pydantic ArtifactData model (from MariaDB) or a
        # dict (for in-memory-only artifacts and legacy etcd path).
        if isinstance(static_values, ArtifactData):
            data = self.upgrade_pydantic_data(static_values, ArtifactData)
            super().__init__(data.uuid, data.version)
            self.__artifact_type = data.artifact_type
            self.__source_url = data.source_url
            self.__name = data.name
            self.__namespace = data.namespace
        else:
            # Dict path: used for in-memory-only artifacts
            self.upgrade(static_values)
            super().__init__(static_values.get('uuid'),
                             static_values.get('version'),
                             static_values.get('in_memory_only', False))
            self.__artifact_type = static_values['artifact_type']
            self.__source_url = static_values['source_url']
            self.__name = static_values['name']
            self.__namespace = static_values.get('namespace')

        # Lazy-load attributes from MariaDB
        self.__attributes: Optional[ArtifactAttributesData] = None
        self.__attributes_loaded: bool = False

    def _load_attributes(self) -> Optional[ArtifactAttributesData]:
        """Load attributes from MariaDB."""
        if not self.__attributes_loaded:
            # In-memory only artifacts must never touch the database; an
            # attributes row written for one is orphaned forever because
            # hard_delete() early-returns for in-memory objects (issue
            # 3532).
            if self.in_memory_only:
                self.__attributes = ArtifactAttributesData(uuid=self.uuid)
            else:
                self.__attributes = mariadb.get_artifact_attributes(self.uuid)
            self.__attributes_loaded = True
        return self.__attributes

    def _ensure_attributes(self) -> ArtifactAttributesData:
        """Ensure attributes record exists, creating with defaults if needed."""
        attrs = self._load_attributes()
        if attrs is None:
            attrs = ArtifactAttributesData(uuid=self.uuid)
            mariadb.create_artifact_attributes(attrs)
            self.__attributes = attrs
            self.__attributes_loaded = True
        return attrs

    def _update_attributes(self, **kwargs) -> None:
        """Update attributes in MariaDB.

        Only the columns named in kwargs are written. This object
        caches its attributes in memory, so an unmasked write would
        push an arbitrarily stale snapshot of the other columns over
        any concurrent writer's committed changes (the cross-attribute
        lost update fixed for instance attributes).
        """
        attrs = self._ensure_attributes()
        updated = ArtifactAttributesData(
            uuid=attrs.uuid,
            max_versions=kwargs.get('max_versions', attrs.max_versions),
            shared=kwargs.get('shared', attrs.shared),
            highest_index=kwargs.get('highest_index', attrs.highest_index)
        )
        if not self.in_memory_only:
            mariadb.update_artifact_attributes(updated, fields=list(kwargs))
        self.__attributes = updated

    @classmethod
    def _upgrade_step_8_to_9(cls, static_values):
        ...

    @classmethod
    def _persist_pydantic_upgrade(cls, data: ArtifactData) -> None:
        """Persist an upgraded ArtifactData to MariaDB."""
        mariadb.update_artifact(data)

    @classmethod
    def _db_create(cls, object_uuid: str, metadata: dict[str, Any]) -> None:
        """Create an artifact record in MariaDB."""
        if not mariadb.create_artifact(
            uuid_mod.UUID(object_uuid),
            metadata['artifact_type'],
            metadata['source_url'],
            metadata['name'],
            metadata['namespace'],
            metadata['version']
        ):
            raise RuntimeError(f'Failed to create artifact {object_uuid} in MariaDB')
        # Create default attributes record
        if not mariadb.create_artifact_attributes(
            ArtifactAttributesData(uuid=uuid_mod.UUID(object_uuid))
        ):
            raise RuntimeError(f'Failed to create artifact attributes {object_uuid} in MariaDB')
        super()._db_create(object_uuid, metadata)

    @classmethod
    def _db_get(cls, object_uuid: Union[str, uuid_mod.UUID]) -> Optional[ArtifactData]:
        """Get artifact static values from MariaDB instead of etcd."""
        if isinstance(object_uuid, uuid_mod.UUID):
            db_uuid = object_uuid
        else:
            db_uuid = uuid_mod.UUID(object_uuid)
        data = mariadb.get_artifact(db_uuid)
        if not data:
            return None

        if data.version != cls.current_version:
            if not cls.upgrade_supported:
                raise exceptions.BadObjectVersion(
                    f'Unsupported object version - {cls.object_type}: {data}')
        return data

    @classmethod
    def filter(cls, filters):
        """Override base class to use MariaDB instead of etcd.

        Documented fallback: ``Artifact.from_db_by_ref`` is the
        live name-lookup path and pushes its predicates to SQL via
        ``find_artifacts``. ``filter()`` exists so the predicate
        API on ``DatabaseBackedObject.from_db_by_ref`` keeps a
        usable implementation, even though no in-tree caller
        currently reaches it. See commit 2d8d393b.
        """
        for data in mariadb.get_all_artifacts():  # nopushdown: fallback (see docstring)
            obj = cls(data)
            if all(f(obj) for f in filters):
                yield obj

    @classmethod
    def from_db(cls, object_uuid: Union[str, uuid_mod.UUID],
                suppress_failure_audit: bool = False) -> 'Artifact | None':
        """Load an Artifact from the database.

        Override the base class from_db because _db_get returns a Pydantic
        ArtifactData model, not a dictionary.
        """
        if not object_uuid:
            return None

        data = cls._db_get(object_uuid)
        if not data:
            if not suppress_failure_audit:
                add_event(
                    EVENT_TYPE_AUDIT, cls.object_type, object_uuid,
                    'attempt to lookup non-existent object',
                    extra={'caller': util_callstack.get_caller(offset=-3)},
                    log_as_error=True)
            return None

        return cls(data)

    @classmethod
    def from_db_by_ref(
            cls, object_ref: Union[str, uuid_mod.UUID],
            namespace: Optional[str] = None) -> 'Artifact | None':
        """Look up an artifact by UUID or by name within a namespace.

        UUID lookups short-circuit to from_db. Name lookups push
        state + namespace + name down to a single indexed SQL
        query via mariadb.find_artifacts.
        """
        if object_ref and util_general.valid_uuid4(object_ref):
            return cls.from_db(object_ref)

        # namespace='system' or namespace=None means "look across
        # all namespaces" — preserve that by omitting the namespace
        # filter. Matches baseobject.namespace_filter semantics.
        criteria_namespace = (
            namespace if namespace and namespace != 'system' else None)

        criteria = ObjectFilterCriteria(
            states=list(cls.ACTIVE_STATES),
            namespace=criteria_namespace,
            name=object_ref,
        )
        matches = mariadb.find_artifacts(criteria)

        if not matches:
            return None
        if len(matches) > 1:
            raise exceptions.MultipleObjects(
                f'multiple artifacts have the name "{object_ref}"'
                f' in namespace "{namespace}"')
        return cls(matches[0])

    @classmethod
    def new(cls, artifact_type, source_url, name=None, max_versions=0,
            namespace=None):
        if namespace is None:
            raise exceptions.ArtifactHasNoNamespace()

        if not name:
            name = source_url.split('/')[-1]

        artifact_uuid = str(uuid4())
        if not max_versions:
            max_versions = config.ARTIFACT_MAX_VERSIONS_DEFAULT

        static_values = {
            'uuid': artifact_uuid,
            'artifact_type': artifact_type,
            'source_url': source_url,
            'name': name,
            'namespace': namespace,

            'version': cls.current_version
        }

        # Artifacts of type IMAGE which are references to blobs are not
        # persisted to the database, as they are an ephemeral convenience
        # abstraction. We track blobs elsewhere.
        if artifact_type == cls.TYPE_IMAGE and source_url.startswith(BLOB_URL):
            static_values['in_memory_only'] = True
            a = Artifact(static_values)
            a.log.with_fields(static_values).info('Artifact is in-memory only')
        else:
            Artifact._db_create(artifact_uuid, static_values)
            a = Artifact.from_db(artifact_uuid)

        a.state = Artifact.STATE_INITIAL
        a.max_versions = max_versions
        return a

    @staticmethod
    def from_url(artifact_type, url, name=None, max_versions=0, namespace=None,
                 create_if_new=False):
        artifacts = list(Artifacts([
            partial(url_filter, url),
            partial(type_filter, artifact_type),
            not_dead_states_filter,
            partial(namespace_or_shared_filter, namespace)]))

        if len(artifacts) == 0:
            if create_if_new:
                if not name:
                    name = url.split('/')[-1]
                return Artifact.new(artifact_type, url, name=name,
                                    max_versions=max_versions,
                                    namespace=namespace)
            return None
        if len(artifacts) == 1:
            return artifacts[0]

        # We have more than one match. If only one of those is in our
        # namespace, then use it. Otherwise give up as being ambiguous.
        local_artifacts = []
        for a in artifacts:
            if a.namespace == namespace:
                local_artifacts.append(a)

        if len(local_artifacts) == 1:
            return local_artifacts[0]

        raise exceptions.TooManyMatches()

    # Static values
    @property
    def artifact_type(self):
        return self.__artifact_type

    @property
    def source_url(self):
        return self.__source_url

    @property
    def name(self):
        return self.__name

    @property
    def namespace(self):
        return self.__namespace

    @property
    def max_versions(self):
        if self.in_memory_only:
            return config.ARTIFACT_MAX_VERSIONS_DEFAULT
        attrs = self._load_attributes()
        if not attrs or attrs.max_versions <= 0:
            # Zero has always meant "use the configured default",
            # and a negative must mean it too: delete_old_versions()
            # computes sorted(indexes)[:-max], so -1 would silently
            # delete the oldest version on every index add. The API
            # rejects negatives now, but rows written before it did
            # still have to be harmless.
            return config.ARTIFACT_MAX_VERSIONS_DEFAULT
        return attrs.max_versions

    @max_versions.setter
    def max_versions(self, value):
        if self.in_memory_only:
            return
        self._update_attributes(max_versions=value)
        self.delete_old_versions()

    @property
    def most_recent_index(self):
        if self.in_memory_only:
            return {'index': 0}
        indexes = list(mariadb.get_all_artifact_indexes(self.uuid))
        if not indexes:
            return {'index': 0}
        highest = max(indexes, key=lambda x: x.index_number)
        return {
            'index': highest.index_number,
            'blob_uuid': str(highest.blob_uuid)
        }

    @property
    def shared(self):
        if self.in_memory_only:
            return False
        attrs = self._load_attributes()
        if not attrs:
            return False
        return attrs.shared

    @shared.setter
    def shared(self, value):
        if self.in_memory_only:
            return
        self._update_attributes(shared=value)

    def external_view_without_index(self):
        out = self._external_view()
        out.update({
            'artifact_type': self.artifact_type,
            'source_url': self.source_url,
            'max_versions': self.max_versions,
            'namespace': self.namespace,
            'shared': self.shared,
            'last_cluster_operation': self.last_cluster_operation
        })
        return out

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        a = self.external_view_without_index()
        a.update(self.most_recent_index)

        # Insert blob information
        blobs = {}
        for blob_index in self.get_all_indexes():
            blob_uuid = blob_index['blob_uuid']
            b = blob.Blob.from_db(blob_uuid)
            if b:
                # Blobs might have a UUID listed but not yet be instantiated.
                # TODO(andy): Artifacts should not reference non-existent blobs
                blobs[blob_index['index']] = {
                    'uuid': blob_uuid,
                    'size': b.size,
                    'reference_count': b.ref_count,
                    'depends_on': b.depends_on
                }
        a['blobs'] = blobs

        # Add object references (what references this artifact and what this
        # artifact references). Skip for in_memory_only artifacts as they don't
        # persist references.
        if not self.in_memory_only:
            refs_to = mariadb.get_references_to(ObjectType.ARTIFACT, self.uuid)
            refs_from = mariadb.get_references_from(
                ObjectType.ARTIFACT, self.uuid)
            a['references_to'] = references_to_grouped_dict(refs_to)
            a['references_from'] = references_to_grouped_dict(refs_from)
        else:
            a['references_to'] = {}
            a['references_from'] = {}

        return a

    def get_all_indexes(self):
        if self.in_memory_only:
            return
        indexes = mariadb.get_all_artifact_indexes(self.uuid)
        for idx in sorted(indexes, key=lambda x: x.index_number):
            yield {
                'index': idx.index_number,
                'blob_uuid': str(idx.blob_uuid)
            }

    def update_billing(self):
        total_used_storage = 0
        for blob_index in self.get_all_indexes():
            blob_uuid = blob_index['blob_uuid']
            b = blob.Blob.from_db(blob_uuid)
            if b:
                # NOTE(mikal): I've decided not to include blob replication
                # cost in this number, as that is a decision the cluster
                # deployer machines (its a config option), not a decision
                # the owner of the blob makes.
                total_used_storage += int(b.size)

        self.add_event(EVENT_TYPE_USAGE, 'usage', extra={'bytes': total_used_storage},
                       suppress_event_logging=True)

    def add_index(self, blob_uuid, force=False):
        with self.get_lock_attr('index', 'Artifact index creation'):
            mri = self.most_recent_index
            old_sha512 = None
            old_blob_uuid = None
            if 'blob_uuid' in mri:
                old_blob = blob.Blob.from_db(mri['blob_uuid'])
                if not old_blob:
                    raise exceptions.BlobMissing(
                        'Failed to retrieve previous artifact version: '
                        f'{mri["blob_uuid"]}')
                old_blob_uuid = old_blob.uuid
                old_sha512 = mariadb.get_valid_hash(str(old_blob.uuid), 'sha512')

            if not force and old_blob_uuid and old_blob_uuid == blob_uuid:
                # Skip using the same blob UUID as two consecutive indexes
                return mri

            new_blob = blob.Blob.from_db(blob_uuid)
            if not new_blob:
                raise exceptions.BlobMissing(
                    f'Failed to retrieve new artifact version: {blob_uuid}')
            new_sha512 = mariadb.get_valid_hash(str(new_blob.uuid), 'sha512')

            if not force and old_sha512 and new_sha512:
                if old_sha512 == new_sha512:
                    # Skipping the update, the blobs have the same content...
                    return mri

            # Get current highest index from attributes
            attrs = self._ensure_attributes()
            index = attrs.highest_index + 1
            self._update_attributes(highest_index=index)

            # Create the index record in MariaDB
            mariadb.create_artifact_index(
                self.uuid, index, uuid_mod.UUID(str(blob_uuid)))

            entry = {
                'index': index,
                'blob_uuid': str(blob_uuid)
            }

            if not self.in_memory_only:
                b = blob.Blob.from_db(blob_uuid)
                if b:
                    b.add_artifact_index_reference(self.uuid, index)

            # There is an implied billing update in delete_old_versions, so we
            # don't need one of our own here.
            self.delete_old_versions()
            return entry

    def delete_old_versions(self):
        """Count versions and if necessary remove oldest versions."""
        indexes = [i['index'] for i in self.get_all_indexes()]
        max = self.max_versions
        if len(indexes) > max:
            for i in sorted(indexes)[:-max]:
                self.del_index(i, update_billing=False)
            self.update_billing()

    def del_index(self, index, update_billing=True):
        index_data = mariadb.get_artifact_index(self.uuid, index)
        if not index_data:
            self.log.with_fields({'index': index}).warn('Cannot find index in DB')
            return

        mariadb.delete_artifact_index(self.uuid, index)
        if not self.in_memory_only:
            b = blob.Blob.from_db(str(index_data.blob_uuid))
            if b:
                b.remove_artifact_index_reference(self.uuid, index)

        if update_billing:
            self.update_billing()

    def delete(self):
        self.state = self.STATE_DELETED

        # Remove all artifact index references from this artifact
        if not self.in_memory_only:
            mariadb.remove_all_references_from(ObjectType.ARTIFACT, self.uuid)

    def hard_delete(self):
        mariadb.delete_all_artifact_indexes(self.uuid)
        mariadb.delete_artifact_attributes(self.uuid)
        mariadb.delete_artifact(self.uuid)
        super().hard_delete()

    def resolve_to_blob(self):
        mri = self.most_recent_index

        blob_uuid = mri.get('blob_uuid')
        if not blob_uuid:
            self.log.with_fields({'most_recent_index': mri}).error(
                'Failed to resolve blob: no uuid')
            return

        b = blob.Blob.from_db(blob_uuid)
        if not b:
            self.log.with_fields({'most_recent_index': mri}).error(
                'Failed to resolve blob: blob missing')
            return

        if b.state == blob.Blob.STATE_DELETED:
            self.log.with_fields({'most_recent_index': mri}).error(
                'Failed to resolve blob: blob deleted')
            return

        return blob_uuid


class Artifacts(dbo_iter):
    base_object = Artifact

    def _resolve_prefilter_to_states(self):
        # Preserve the pre-phase-4 Artifacts override behaviour: when
        # no prefilter is set, do not filter on state (return every
        # artifact and let predicate filters scope). The base class
        # default of ACTIVE_STATES is kept for other inheritors.
        if self.prefilter is None:
            return set()
        return super()._resolve_prefilter_to_states()

    def _find(self, criteria):
        return mariadb.find_artifacts(criteria)

    def __iter__(self):
        for _, data in self.get_iterator():
            obj = Artifact(data)
            if not obj:
                continue
            filtered = self.apply_filters(obj)
            if filtered:
                yield filtered


def url_filter(url, a):
    return url == a.source_url


def type_filter(artifact_type, a):
    return artifact_type == a.artifact_type


def instance_snapshot_filter(instance_uuid, a):
    if a.artifact_type != Artifact.TYPE_SNAPSHOT:
        return False
    return a.source_url.startswith(f'{INSTANCE_URL}{instance_uuid}')


not_dead_states_filter = partial(
    baseobject.state_filter, [
        Artifact.STATE_INITIAL,
        Artifact.STATE_CREATING,
        Artifact.STATE_CREATED,
    ])


def namespace_exact_filter(namespace, o):
    return o.namespace == namespace


def namespace_or_shared_filter(namespace, o):
    if namespace == 'system':
        return True
    if o.shared:
        return True
    if namespace_is_trusted(o.namespace, namespace):
        return True
    return o.namespace == namespace


def artifacts_in_namespace(namespace):
    return Artifacts(namespace=namespace, prefilter='active')
