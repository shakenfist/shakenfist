import flask
from flask_jwt_extended import jwt_required, get_jwt_identity
import json
import os
import requests
import shutil
import time
import uuid

from shakenfist.artifact import Artifact, Artifacts, UPLOAD_URL
from shakenfist.blob import Blob
from shakenfist import baseobject
from shakenfist import constants
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.config import config
from shakenfist import db
from shakenfist import etcd
from shakenfist import logutil
from shakenfist.tasks import FetchImageTask
from shakenfist.upload import Upload
from shakenfist.util import general as util_general


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


def arg_is_artifact_uuid(func):
    def wrapper(*args, **kwargs):
        if 'artifact_uuid' in kwargs:
            kwargs['artifact_from_db'] = Artifact.from_db(
                kwargs['artifact_uuid'])
        if not kwargs.get('artifact_from_db'):
            LOG.with_field('artifact', kwargs['artifact_uuid']).info(
                'Artifact not found')
            return api_base.error(404, 'artifact not found')

        return func(*args, **kwargs)
    return wrapper


class ArtifactEndpoint(api_base.Resource):
    @jwt_required
    @arg_is_artifact_uuid
    def get(self, artifact_uuid=None, artifact_from_db=None):
        with etcd.ThreadLocalReadOnlyCache():
            return artifact_from_db.external_view()

    @jwt_required
    @arg_is_artifact_uuid
    def delete(self, artifact_uuid=None, artifact_from_db=None):
        """Delete an artifact from the cluster

        Artifacts can only be deleted from the system if they are not in use.
        The actual deletion of the on-disk files is left to the cleaner daemon.

        It is acknowledged that there is a potential race condition between the
        check that an artifact is not in use and the marking of the artifact as
        deleted. This is only caused by a user simultaneously deleting an
        artifact and attempting to start a VM using it. It is recommended that
        the user does not do that.
        """
        # TODO(andy): Enforce namespace permissions when snapshots have namespaces
        # TODO(mikal): this should all be refactored to be in the object

        if artifact_from_db.state.value == Artifact.STATE_DELETED:
            # Already deleted, nothing to do.
            return

        # Check for instances using a blob referenced by the artifact.
        blobs = []
        sole_ref_in_use = []
        for blob_index in artifact_from_db.get_all_indexes():
            b = Blob.from_db(blob_index['blob_uuid'])
            if b:
                blobs.append(b)
                if b.ref_count == 1:
                    sole_ref_in_use += b.instances
        if sole_ref_in_use:
            return api_base.error(
                400, 'Cannot delete last reference to blob in use by instance (%s)' % (
                    ', '.join(sole_ref_in_use), ))

        artifact_from_db.delete()
        for b in blobs:
            b.ref_count_dec()


class ArtifactsEndpoint(api_base.Resource):
    @jwt_required
    def get(self, node=None):
        retval = []

        with etcd.ThreadLocalReadOnlyCache():
            for a in Artifacts(filters=[baseobject.active_states_filter]):
                if node:
                    idx = a.most_recent_index
                    if 'blob_uuid' in idx:
                        b = Blob.from_db(idx['blob_uuid'])
                        if b and node in b.locations:
                            retval.append(a.external_view())
                else:
                    retval.append(a.external_view())

        return retval

    @jwt_required
    def post(self, url=None):
        # The only artifact type you can force the cluster to fetch is an
        # image, so TYPE_IMAGE is assumed here. We ensure that the image exists
        # in the database in an initial state here so that it will show up in
        # image list requests. The image is fetched by the queued job later.
        a = Artifact.from_url(Artifact.TYPE_IMAGE, url)

        etcd.enqueue(config.NODE_NAME, {
            'tasks': [FetchImageTask(url)],
        })
        return a.external_view()


class ArtifactUploadEndpoint(api_base.Resource):
    @jwt_required
    def post(self, artifact_name=None, upload_uuid=None, source_url=None):
        u = Upload.from_db(upload_uuid)
        if not u:
            return api_base.error(404, 'upload not found')

        if u.node != config.NODE_NAME:
            url = 'http://%s:%d%s' % (u.node, config.API_PORT,
                                      flask.request.environ['PATH_INFO'])
            api_token = util_general.get_api_token(
                'http://%s:%d' % (u.node, config.API_PORT),
                namespace=get_jwt_identity()[0])
            r = requests.request(
                flask.request.environ['REQUEST_METHOD'], url,
                data=json.dumps(api_base.flask_get_post_body()),
                headers={'Authorization': api_token,
                         'User-Agent': util_general.get_user_agent()})

            LOG.info('Proxied %s %s returns: %d, %s' % (
                     flask.request.environ['REQUEST_METHOD'], url,
                     r.status_code, r.text))
            resp = flask.Response(r.text,  mimetype='application/json')
            resp.status_code = r.status_code
            return resp

        if not source_url:
            source_url = ('%s%s/%s'
                          % (UPLOAD_URL, get_jwt_identity()[0], artifact_name))
        a = Artifact.from_url(Artifact.TYPE_IMAGE, source_url)

        with a.get_lock(ttl=(12 * constants.LOCK_REFRESH_SECONDS),
                        timeout=config.MAX_IMAGE_TRANSFER_SECONDS):
            blob_uuid = str(uuid.uuid4())
            blob_dir = os.path.join(config.STORAGE_PATH, 'blobs')
            blob_path = os.path.join(blob_dir, blob_uuid)

            upload_dir = os.path.join(config.STORAGE_PATH, 'uploads')
            upload_path = os.path.join(upload_dir, u.uuid)

            # NOTE(mikal): we can't use os.rename() here because these paths
            # might be on different filesystems.
            shutil.move(upload_path, blob_path)
            st = os.stat(blob_path)
            b = Blob.new(
                blob_uuid, st.st_size,
                time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime()),
                time.time())
            b.state = Blob.STATE_CREATED
            b.ref_count_inc()
            b.observe()
            b.request_replication()

            a.state = Artifact.STATE_CREATED
            a.add_event('upload', None, None, 'success')
            a.add_index(b.uuid)
            a.state = Artifact.STATE_CREATED
            return a.external_view()


class ArtifactEventsEndpoint(api_base.Resource):
    @jwt_required
    # TODO(andy): Should images be owned? Personalised images should be owned.
    def get(self, artifact_uuid):
        return list(db.get_events('artifact', artifact_uuid))


class ArtifactVersionsEndpoint(api_base.Resource):
    @jwt_required
    @arg_is_artifact_uuid
    def get(self, artifact_uuid=None, artifact_from_db=None):
        retval = []
        for idx in artifact_from_db.get_all_indexes():
            b = Blob.from_db(idx['blob_uuid'])
            bout = b.external_view()
            bout['index'] = idx['index']
            retval.append(bout)
        return retval

    @jwt_required
    @arg_is_artifact_uuid
    def post(self, artifact_uuid=None, artifact_from_db=None,
             max_versions=config.ARTIFACT_MAX_VERSIONS_DEFAULT):
        try:
            mv = int(max_versions)
        except ValueError:
            return api_base.error(400, 'max version is not an integer')
        artifact_from_db.max_versions = mv


class ArtifactVersionEndpoint(api_base.Resource):
    @jwt_required
    @arg_is_artifact_uuid
    def delete(self, artifact_uuid=None, artifact_from_db=None, version_id=0):
        try:
            ver_index = int(version_id)
        except ValueError:
            return api_base.error(400, 'version index is not an integer')

        indexes = list(artifact_from_db.get_all_indexes())
        for idx in indexes:
            if idx['index'] == ver_index:
                artifact_from_db.del_index(idx['index'])
                if len(indexes) == 1:
                    artifact_from_db.state = Artifact.STATE_DELETED
                return

        return api_base.error(404, 'artifact index not found')
