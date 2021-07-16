# Copyright 2021 Michael Still

from functools import partial
from uuid import uuid4

from shakenfist.baseobject import (
    DatabaseBackedObject as dbo,
    DatabaseBackedObjectIterator as dbo_iter)
from shakenfist import db
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import logutil


LOG, _ = logutil.setup(__name__)


BLOB_URL = 'sf://blob/'
INSTANCE_URL = 'sf://instance/'
LABEL_URL = 'sf://label/'
SNAPSHOT_URL = 'sf://snapshot/'


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

    @classmethod
    def new(cls, artifact_type, source_url):
        artifact_uuid = str(uuid4())

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
        a.add_event('db record creation', None)

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
    def from_url(artifact_type, url):
        with db.get_lock('artifact', artifact_type, url):
            artifacts = list(Artifacts([partial(url_filter, url),
                                        partial(type_filter, artifact_type)]))
            if len(artifacts) == 0:
                return Artifact.new(artifact_type, url)
            if len(artifacts) == 1:
                return artifacts[0]
            raise exceptions.TooManyMatches()

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
            index = self.most_recent_index.get('index', 0) + 1
            entry = {
                'index': index,
                'blob_uuid': blob_uuid
            }
            self._db_set_attribute('index_%012d' % index, entry)
            return entry

    def external_view_without_index(self):
        return {
            'uuid': self.uuid,
            'artifact_type': self.artifact_type,
            'state': self.state.value,
            'source_url': self.source_url,
            'version': self.version
        }

    def external_view(self):
        # If this is an external view, then mix back in attributes that users
        # expect
        a = self.external_view_without_index()
        a.update(self.most_recent_index)
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
