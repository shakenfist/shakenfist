# Copyright 2021 Michael Still

from collections import defaultdict
from functools import partial
from uuid import uuid4

from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist import blob
from shakenfist.config import config
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import logutil
from shakenfist.metrics import get_minimum_object_version as gmov


LOG, _ = logutil.setup(__name__)


BLOB_URL = 'sf://blob/'
INSTANCE_URL = 'sf://instance/'
LABEL_URL = 'sf://label/'
SNAPSHOT_URL = 'sf://snapshot/'
UPLOAD_URL = 'sf://upload/'


class Artifact(dbo):
    object_type = 'artifact'
    current_version = 4
    upgrade_supported = True

    state_targets = {
        None: (dbo.STATE_INITIAL),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: (),
    }

    ACTIVE_STATES = set([dbo.STATE_INITIAL,
                         dbo.STATE_CREATED,
                         dbo.STATE_ERROR,
                         ])

    TYPE_SNAPSHOT = 'snapshot'
    TYPE_LABEL = 'label'
    TYPE_IMAGE = 'image'

    def __init__(self, static_values):
        if static_values['version'] != self.current_version:
            upgraded, static_values = self.upgrade(static_values)

            if upgraded and gmov('artifact') == self.current_version:
                etcd.put(self.object_type, None,
                         static_values.get('uuid'), static_values)
                LOG.with_field(
                    self.object_type, static_values['uuid']).info('Online upgrade committed')

        super(Artifact, self).__init__(static_values.get('uuid'),
                                       static_values.get('version'),
                                       static_values.get('in_memory_only', False))

        self.__artifact_type = static_values['artifact_type']
        self.__source_url = static_values['source_url']
        self.__namespace = static_values.get('namespace')

    @classmethod
    def upgrade(cls, static_values):
        changed = False
        starting_version = static_values.get('version')

        if static_values.get('version') == 2:
            static_values['namespace'] = 'sharedwithall'
            static_values['version'] = 3
            changed = True

        if static_values.get('version') == 3:
            if static_values['namespace'] == 'sharedwithall':
                static_values['namespace'] = 'system'
                etcd.put('attribute/artifact',
                         static_values['uuid'], 'shared', {'shared': True})

            static_values['version'] = 4
            changed = True

        if changed:
            LOG.with_fields({
                cls.object_type: static_values['uuid'],
                'start_version': starting_version,
                'final_version': static_values.get('version')
            }).info('Object online upgraded')
        return changed, static_values

    @classmethod
    def new(cls, artifact_type, source_url, max_versions=0, namespace=None):
        if namespace is None:
            raise exceptions.ArtifactHasNoNamespace()

        artifact_uuid = str(uuid4())
        if not max_versions:
            max_versions = config.ARTIFACT_MAX_VERSIONS_DEFAULT

        static_values = {
            'uuid': artifact_uuid,
            'artifact_type': artifact_type,
            'source_url': source_url,
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
    def from_url(artifact_type, url, max_versions=0, namespace=None, create_if_new=False):
        with etcd.get_lock('artifact_from_url', None, url):
            artifacts = list(Artifacts([
                partial(url_filter, url),
                partial(type_filter, artifact_type),
                not_dead_states_filter,
                partial(namespace_or_shared_filter, namespace)]))
            if len(artifacts) == 0:
                if create_if_new:
                    return Artifact.new(artifact_type, url, max_versions=max_versions,
                                        namespace=namespace)
                return None
            if len(artifacts) == 1:
                return artifacts[0]
            raise exceptions.TooManyMatches()

    # Static values
    @property
    def artifact_type(self):
        return self.__artifact_type

    @property
    def source_url(self):
        return self.__source_url

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
        db_data = self._db_get_attribute('shared')
        if not db_data:
            return False
        return db_data.get('shared', False)

    @shared.setter
    def shared(self, value):
        self._db_set_attribute('shared', {'shared': value})

    def external_view_without_index(self):
        return {
            'uuid': self.uuid,
            'artifact_type': self.artifact_type,
            'state': self.state.value,
            'source_url': self.source_url,
            'version': self.version,
            'max_versions': self.max_versions,
            'namespace': self.namespace,
            'shared': self.shared
        }

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        a = self.external_view_without_index()
        a.update(self.most_recent_index)

        # Build list of instances for each blob
        blob_usage = defaultdict(list)
        for inst in instance.Instances([instance.healthy_states_filter]):
            # inst.block_devices isn't populated until the instance is created,
            # so it may not be ready yet. This means we will miss instances
            # which have been requested but not yet started.
            for d in inst.block_devices.get('devices', []):
                blob_usage[d.get('blob_uuid')].append(inst.uuid)

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
                    'instances': blob_usage.get(blob_uuid, []),
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

    def add_index(self, blob_uuid):
        with self.get_lock_attr('index', 'Artifact index creation'):
            highest_index = self._db_get_attribute('highest_index')
            index = highest_index.get('index', 0) + 1
            self._db_set_attribute('highest_index', {'index': index})

            entry = {
                'index': index,
                'blob_uuid': blob_uuid
            }
            self._db_set_attribute('index_%012d' % index, entry)
            self.add_event2('Added index %d to artifact' % index)
            self.delete_old_versions()
            return entry

    def delete_old_versions(self):
        """Count versions and if necessary remove oldest versions."""
        indexes = [i['index'] for i in self.get_all_indexes()]
        max = self.max_versions
        if len(indexes) > max:
            for i in sorted(indexes)[:-max]:
                self.del_index(i)
                self.add_event2('Deleted index %d from artifact' % i)

    def del_index(self, index):
        index_data = self._db_get_attribute('index_%012d' % index)
        if not index_data:
            self.log.withField('index', index).warn('Cannot find index in DB')
            return

        self.add_event2('Deleted index %d from artifact' % index)
        self._db_delete_attribute('index_%012d' % index)
        b = blob.Blob.from_db(index_data['blob_uuid'])
        if b:
            b.ref_count_dec()

    def delete(self):
        self.state = self.STATE_DELETED

        for blob_index in self.get_all_indexes():
            b = blob.Blob.from_db(blob_index['blob_uuid'])
            if b:
                b.ref_count_dec()

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
    def __iter__(self):
        for _, a in etcd.get_all('artifact', None):
            a = Artifact.from_db(a['uuid'])
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
    return a.source_url.startswith('%s%s' % (INSTANCE_URL, instance_uuid))


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
    return o.namespace == namespace
