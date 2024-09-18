# Copyright 2021 Michael Still
from functools import partial
from uuid import uuid4

from shakenfist_utilities import logs

from shakenfist import baseobject
from shakenfist import blob
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.baseobject import DatabaseBackedObjectIterator as dbo_iter
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.namespace import namespace_is_trusted


LOG, _ = logs.setup(__name__)


BLOB_URL = 'sf://blob/'
INSTANCE_URL = 'sf://instance/'
LABEL_URL = 'sf://label/'
SNAPSHOT_URL = 'sf://snapshot/'
UPLOAD_URL = 'sf://upload/'


class Artifact(dbo):
    object_type = 'artifact'
    current_version = 6

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
        self.upgrade(static_values)

        super().__init__(static_values.get('uuid'),
                         static_values.get('version'),
                         static_values.get('in_memory_only', False))

        self.__artifact_type = static_values['artifact_type']
        self.__source_url = static_values['source_url']
        self.__name = static_values['name']
        self.__namespace = static_values.get('namespace')

    @classmethod
    def _upgrade_step_2_to_3(cls, static_values):
        static_values['namespace'] = 'sharedwithall'

    @classmethod
    def _upgrade_step_3_to_4(cls, static_values):
        if static_values['namespace'] == 'sharedwithall':
            static_values['namespace'] = 'system'
            etcd.put(
                'attribute/artifact', static_values['uuid'], 'shared',
                {'shared': True})

    @classmethod
    def _upgrade_step_4_to_5(cls, static_values):
        cls._upgrade_metadata_to_attribute(static_values['uuid'])

    @classmethod
    def _upgrade_step_5_to_6(cls, static_values):
        static_values['name'] = static_values['source_url'].split('/')[-1]

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
        db_data = self._db_get_attribute('max_versions')
        if not db_data:
            return config.ARTIFACT_MAX_VERSIONS_DEFAULT
        return db_data.get('max_versions', config.ARTIFACT_MAX_VERSIONS_DEFAULT)

    @max_versions.setter
    def max_versions(self, value):
        self._db_set_attribute('max_versions', {'max_versions': value})
        self.delete_old_versions()

    @property
    def most_recent_index(self):
        indices = {}
        for key, data in self._db_get_attributes('index_'):
            if data:
                indices[int(key.split('_')[1])] = data
        if not indices:
            return {'index': 0}
        return indices[sorted(indices)[-1]]

    @property
    def shared(self):
        db_data = self._db_get_attribute('shared', {'shared': False})
        return db_data.get('shared', False)

    @shared.setter
    def shared(self, value):
        self._db_set_attribute('shared', {'shared': value})

    def external_view_without_index(self):
        out = self._external_view()
        out.update({
            'artifact_type': self.artifact_type,
            'source_url': self.source_url,
            'max_versions': self.max_versions,
            'namespace': self.namespace,
            'shared': self.shared
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
        return a

    def get_all_indexes(self):
        indices = {}
        for key, data in self._db_get_attributes('index_'):
            indices[key] = data

        for key in sorted(indices):
            yield indices[key]

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

    def add_index(self, blob_uuid):
        with self.get_lock_attr('index', 'Artifact index creation'):
            mri = self.most_recent_index
            if 'blob_uuid' in mri:
                old_blob = blob.Blob.from_db(mri['blob_uuid'])
                if not old_blob:
                    raise exceptions.BlobMissing()
                old_checksums = old_blob.checksums
                old_blob_uuid = old_blob.uuid
            else:
                old_checksums = {}
                old_blob_uuid = None

            if old_blob_uuid and old_blob_uuid == blob_uuid:
                # Skip using the same blob UUID as two consecutive indexes
                return mri

            new_blob = blob.Blob.from_db(blob_uuid)
            if not new_blob:
                raise exceptions.BlobMissing()
            new_checksums = new_blob.checksums

            if old_checksums.get('sha512') and new_checksums.get('sha512'):
                if old_checksums.get('sha512') == new_checksums.get('sha512'):
                    # Skipping the update, the blobs have the same content...
                    return mri

            highest_index = self._db_get_attribute(
                'highest_index', {'index': 0})
            index = highest_index['index'] + 1
            self._db_set_attribute('highest_index', {'index': index})

            entry = {
                'index': index,
                'blob_uuid': blob_uuid
            }
            self._db_set_attribute('index_%012d' % index, entry)
            if not self.in_memory_only:
                new_blob.ref_count_inc(self)
            self.add_event(EVENT_TYPE_AUDIT, 'added index to artifact',
                           extra={'index': index})

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
        index_data = self._db_get_attribute('index_%012d' % index)
        if not index_data:
            self.log.with_fields({'index': index}).warn('Cannot find index in DB')
            return

        self._db_delete_attribute('index_%012d' % index)
        b = blob.Blob.from_db(index_data['blob_uuid'])
        if b and not self.in_memory_only:
            b.ref_count_dec(self)

        self.add_event(EVENT_TYPE_AUDIT, 'deleted index from artifact',
                       extra={'index': index})
        if update_billing:
            self.update_billing()

    def delete(self):
        self.state = self.STATE_DELETED

        for blob_index in self.get_all_indexes():
            b = blob.Blob.from_db(blob_index['blob_uuid'])
            if b and not self.in_memory_only:
                b.ref_count_dec(self)

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

    def __iter__(self):
        for _, a in self.get_iterator():
            a = Artifact(a)
            if not a:
                continue

            out = self.apply_filters(a)
            if out:
                yield out


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
    return Artifacts([partial(baseobject.namespace_filter, namespace)],
                     prefilter='active')
