import flask
from flask_jwt_extended import jwt_required, get_jwt_identity
from functools import partial
import json
import os
import requests
import shutil
import time
import uuid

from shakenfist.artifact import (
    Artifact, Artifacts, UPLOAD_URL, namespace_exact_filter,
    namespace_or_shared_filter)
from shakenfist.blob import Blob
from shakenfist import baseobject
from shakenfist import constants
from shakenfist.daemons import daemon
from shakenfist import eventlog
from shakenfist.external_api import base as api_base
from shakenfist.config import config
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


def requires_artifact_ownership(func):
    # Requires that @arg_is_artifact_uuid has already run
    def wrapper(*args, **kwargs):
        if not kwargs.get('artifact_from_db'):
            LOG.with_field('artifact', kwargs['artifact_uuid']).info(
                'Artifact not found, kwarg missing')
            return api_base.error(404, 'artifact not found')

        a = kwargs['artifact_from_db']
        if get_jwt_identity()[0] not in [a.namespace, 'system']:
            LOG.with_object(a).info(
                'Artifact not found, ownership test in decorator')
            return api_base.error(404, 'artifact not found')

        return func(*args, **kwargs)
    return wrapper


def requires_artifact_access(func):
    # Requires that @arg_is_artifact_uuid has already run
    def wrapper(*args, **kwargs):
        if not kwargs.get('artifact_from_db'):
            LOG.with_field('artifact', kwargs['artifact_uuid']).info(
                'Artifact not found, kwarg missing')
            return api_base.error(404, 'artifact not found')

        a = kwargs['artifact_from_db']
        if (a.shared and get_jwt_identity()[0] not in [a.namespace, 'system']):
            LOG.with_object(a).info(
                'Artifact not found, ownership test in decorator')
            return api_base.error(404, 'artifact not found')

        return func(*args, **kwargs)
    return wrapper


class ArtifactEndpoint(api_base.Resource):
    @jwt_required()
    @arg_is_artifact_uuid
    @requires_artifact_access
    def get(self, artifact_uuid=None, artifact_from_db=None):
        with etcd.ThreadLocalReadOnlyCache():
            return artifact_from_db.external_view()

    @jwt_required()
    @arg_is_artifact_uuid
    @requires_artifact_ownership
    def delete(self, artifact_uuid=None, artifact_from_db=None):
        if artifact_from_db.state.value == Artifact.STATE_DELETED:
            return
        artifact_from_db.delete()
        return artifact_from_db.external_view()


class ArtifactsEndpoint(api_base.Resource):
    @jwt_required()
    def get(self, node=None):
        retval = []
        with etcd.ThreadLocalReadOnlyCache():
            for a in Artifacts(filters=[
                    baseobject.active_states_filter,
                    partial(namespace_or_shared_filter, get_jwt_identity()[0])]):
                if node:
                    idx = a.most_recent_index
                    if 'blob_uuid' in idx:
                        b = Blob.from_db(idx['blob_uuid'])
                        if b and node in b.locations:
                            retval.append(a.external_view())
                else:
                    retval.append(a.external_view())
        return retval

    @jwt_required()
    @api_base.requires_namespace_exist
    def post(self, url=None, shared=False, namespace=None):
        # The only artifact type you can force the cluster to fetch is an
        # image, so TYPE_IMAGE is assumed here. We ensure that the image exists
        # in the database in an initial state here so that it will show up in
        # image list requests. The image is fetched by the queued job later.
        if not namespace:
            namespace = get_jwt_identity()[0]

        # If accessing a foreign namespace, we need to be an admin
        if get_jwt_identity()[0] not in [namespace, 'system']:
            return api_base.error(404, 'namespace not found')

        a = Artifact.from_url(Artifact.TYPE_IMAGE, url, namespace=namespace,
                              create_if_new=True)

        # Only admin can create shared artifacts
        if shared:
            if get_jwt_identity()[0] != 'system':
                return api_base.error(
                    403, 'only the system namespace can create shared artifacts')
            a.shared = True

        etcd.enqueue(config.NODE_NAME, {
            'tasks': [FetchImageTask(url, namespace=namespace)],
        })
        return a.external_view()

    @jwt_required()
    @api_base.requires_namespace_exist
    def delete(self, confirm=False, namespace=None):
        """Delete all artifacts in the namespace."""

        if confirm is not True:
            return api_base.error(400, 'parameter confirm is not set true')

        if get_jwt_identity()[0] == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all artifacts in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return api_base.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != get_jwt_identity()[0]:
                return api_base.error(401, 'you cannot delete other namespaces')
            namespace = get_jwt_identity()[0]

        deleted = []
        for a in Artifacts([partial(namespace_exact_filter, namespace)]):
            a.delete()
            deleted.append(a.uuid)

        return deleted


class ArtifactUploadEndpoint(api_base.Resource):
    @jwt_required()
    @api_base.requires_namespace_exist
    def post(self, artifact_name=None, upload_uuid=None, source_url=None,
             shared=False, namespace=None):
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

        if not namespace:
            namespace = get_jwt_identity()[0]

        # If accessing a foreign namespace, we need to be an admin
        if get_jwt_identity()[0] not in [namespace, 'system']:
            return api_base.error(404, 'namespace not found')

        a = Artifact.from_url(Artifact.TYPE_IMAGE, source_url,
                              namespace=namespace, create_if_new=True)

        # Only admin can create shared artifacts
        if shared:
            if get_jwt_identity()[0] != 'system':
                return api_base.error(
                    403, 'only the system namespace can create shared artifacts')
            a.shared = True

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

            a.add_event2('upload complete')
            a.add_index(b.uuid)
            a.state = Artifact.STATE_CREATED

            u.hard_delete()
            return a.external_view()


class ArtifactEventsEndpoint(api_base.Resource):
    @jwt_required()
    @arg_is_artifact_uuid
    @requires_artifact_access
    @api_base.redirect_to_eventlog_node
    def get(self, artifact_uuid=None, artifact_from_db=None):
        with eventlog.EventLog('artifact', artifact_uuid) as eventdb:
            return list(eventdb.read_events())


class ArtifactVersionsEndpoint(api_base.Resource):
    @jwt_required()
    @arg_is_artifact_uuid
    @requires_artifact_access
    def get(self, artifact_uuid=None, artifact_from_db=None):
        retval = []
        for idx in artifact_from_db.get_all_indexes():
            b = Blob.from_db(idx['blob_uuid'])
            bout = b.external_view()
            bout['index'] = idx['index']
            retval.append(bout)
        return retval

    @jwt_required()
    @arg_is_artifact_uuid
    @requires_artifact_ownership
    def post(self, artifact_uuid=None, artifact_from_db=None,
             max_versions=config.ARTIFACT_MAX_VERSIONS_DEFAULT):
        try:
            mv = int(max_versions)
        except ValueError:
            return api_base.error(400, 'max version is not an integer')
        artifact_from_db.max_versions = mv


class ArtifactVersionEndpoint(api_base.Resource):
    @jwt_required()
    @arg_is_artifact_uuid
    @requires_artifact_ownership
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
                return artifact_from_db.external_view()

        return api_base.error(404, 'artifact index not found')


class ArtifactShareEndpoint(api_base.Resource):
    @jwt_required()
    @arg_is_artifact_uuid
    @requires_artifact_ownership
    def post(self, artifact_uuid=None, artifact_from_db=None):
        if artifact_from_db.namespace != 'system':
            return api_base.error(
                403, 'only artifacts in the system namespace can be shared')
        artifact_from_db.shared = True
        return artifact_from_db.external_view()


class ArtifactUnshareEndpoint(api_base.Resource):
    @jwt_required()
    @arg_is_artifact_uuid
    @requires_artifact_ownership
    def post(self, artifact_uuid=None, artifact_from_db=None):
        if not artifact_from_db.shared:
            return api_base.error(403, 'artifact not shared')
        artifact_from_db.shared = False
        return artifact_from_db.external_view()
