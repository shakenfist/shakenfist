# Copyright 2021 Michael Still

from functools import partial
from uuid import uuid4

from shakenfist import baseobject
from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist import blob
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import logutil
from shakenfist.config import config


LOG, _ = logutil.setup(__name__)


BLOB_URL = 'sf://blob/'
INSTANCE_URL = 'sf://instance/'
LABEL_URL = 'sf://label/'
SNAPSHOT_URL = 'sf://snapshot/'
UPLOAD_URL = 'sf://upload/'


class Artifact(dbo):
    object_type = 'artifact'
    current_version = 2
    state_targets = {
        None: (dbo.STATE_INITIAL),
        dbo.STATE_INITIAL: (dbo.STATE_CREATED, dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_CREATED: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_ERROR: (dbo.STATE_DELETED, dbo.STATE_ERROR),
        dbo.STATE_DELETED: (),
    }

    TYPE_SNAPSHOT = 'snapshot'
    TYPE_LABEL = 'label'
    TYPE_IMAGE = 'image'

    def __init__(self, static_values):
        super(Artifact, self).__init__(static_values.get('uuid'),
                                       static_values.get('version'))

        self.__artifact_type = static_values['artifact_type']
        self.__source_url = static_values['source_url']
        self.__max_versions = static_values.get(
            'max_versions', config.ARTIFACT_MAX_VERSIONS_DEFAULT)

    @classmethod
    def new(cls, artifact_type, source_url, max_versions=0):
        artifact_uuid = str(uuid4())
        if not max_versions:
            max_versions = config.ARTIFACT_MAX_VERSIONS_DEFAULT

        Artifact._db_create(
            artifact_uuid,
            {
                'uuid': artifact_uuid,
                'artifact_type': artifact_type,
                'source_url': source_url,

                'version': cls.current_version
            }
        )

        LOG.with_fields({
            'uuid': artifact_uuid,
            'artifact_type': artifact_type,
            'source_url': source_url
        }).debug('Artifact created')

        a = Artifact.from_db(artifact_uuid)
        a.state = Artifact.STATE_INITIAL
        a.max_versions = max_versions

        return a

    @staticmethod
    def from_db(artifact_uuid):
        if not artifact_uuid:
            return None

        static_values = Artifact._db_get(artifact_uuid)
        if not static_values:
            return None

        return Artifact(static_values)

    @staticmethod
    def from_url(artifact_type, url, max_versions=0):
        artifacts = list(Artifacts([partial(url_filter, url),
                                    partial(type_filter, artifact_type),
                                    not_dead_states_filter]))
        if len(artifacts) == 0:
            return Artifact.new(artifact_type, url, max_versions)
        if len(artifacts) == 1:
            return artifacts[0]
        raise exceptions.TooManyMatches()

    @property
    def max_versions(self):
        db_data = self._db_get_attribute('max_versions')
        if not db_data:
            return config.ARTIFACT_MAX_VERSIONS_DEFAULT
        return db_data.get('max_versions', config.ARTIFACT_MAX_VERSIONS_DEFAULT)

    @max_versions.setter
    def max_versions(self, value):
        self._db_set_attribute('max_versions', {'max_versions': value})
        self._delete_old_versions()

    @property
    def most_recent_index(self):
        indices = {}
        for key, data in self._db_get_attributes('index_'):
            if data:
                indices[int(key.split('_')[1])] = data
        if not indices:
            return {'index': 0}
        return indices[sorted(indices)[-1]]

    def get_all_indexes(self):
        indices = {}
        for key, data in self._db_get_attributes('index_'):
            indices[key] = data

        for key in sorted(indices):
            self.log.info('Yielding index %s' % indices[key])
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
            self.log.with_fields(entry).info('Added index to artifact')
            self._delete_old_versions()
            return entry

    def _delete_old_versions(self):
        """Count versions and if necessary remove oldest versions."""
        indexes = [i['index'] for i in self.get_all_indexes()]
        max = self.max_versions
        if len(indexes) > max:
            for i in sorted(indexes)[:-max]:
                self.log.with_field(
                    'index', i).info('Deleting artifact version')
                self.del_index(i)

    def del_index(self, index):
        index_data = self._db_get_attribute('index_%012d' % index)
        if not index_data:
            self.log.withField('index', index).warn('Cannot find index in DB')
            return
        self._db_delete_attribute('index_%012d' % index)
        b = blob.Blob.from_db(index_data['blob_uuid'])
        b.ref_count_dec()

    def external_view_without_index(self):
        return {
            'uuid': self.uuid,
            'artifact_type': self.artifact_type,
            'state': self.state.value,
            'source_url': self.source_url,
            'version': self.version,
            'max_versions': self.max_versions,
        }

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        a = self.external_view_without_index()
        a.update(self.most_recent_index)

        # Build list of instances for each blob
        blob_usage = {}
        for inst in instance.Instances([instance.healthy_states_filter]):
            # inst.block_devices isn't populated until the instance is created,
            # so it may not be ready yet. This means we will miss instances
            # which have been requested but not yet started.
            for d in inst.block_devices.get('devices', []):
                blob_usage.setdefault(d.get('blob_uuid'), []).append(inst.uuid)

        # Insert blob information
        blobs = {}
        for blob_index in self.get_all_indexes():
            blob_uuid = blob_index['blob_uuid']
            b = blob.Blob.from_db(blob_uuid)
            blobs[blob_index['index']] = {
                'uuid': blob_uuid,
                'instances': blob_usage.get(blob_uuid, []),
                'size': b.size,
                'reference_count': b.ref_count,
            }
        a['blobs'] = blobs
        return a

    # Static values
    @property
    def artifact_type(self):
        return self.__artifact_type

    @property
    def source_url(self):
        return self.__source_url


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
