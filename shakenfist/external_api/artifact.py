# Documentation state:
#   - Has metadata calls: yes
#   - OpenAPI complete: yes
#   - Covered in user or operator docs: both
#   - API reference docs exist: yes
#        - and link to OpenAPI docs: yes
#        - and include examples: yes
#   - Has complete CI coverage:
import json
import os
import shutil
import time
import uuid
from functools import partial

import flask
import requests
from flasgger import swag_from
from shakenfist_utilities import api as sf_api  # noreorder
from shakenfist_utilities import logs  # noreorder
from webargs import fields
from webargs.flaskparser import use_kwargs

from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.artifact_fetch_op \
    import create_and_enqueue as afo_create_and_enqueue
from shakenfist.schema.operations.artifact_fetch_op \
    import model_tasks as afo_tasks
from shakenfist import exceptions
from shakenfist.artifact import Artifact
from shakenfist.artifact import Artifacts
from shakenfist.artifact import namespace_or_shared_filter
from shakenfist.artifact import UPLOAD_URL
from shakenfist.artifact import validated_max_versions
from shakenfist.blob import Blob
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.daemons import daemon
from shakenfist.external_api import base as api_base
from shakenfist.instance import instance_blob_usage
from shakenfist.namespace import get_api_token
from shakenfist.namespace import namespace_is_trusted
from shakenfist.upload import Upload
from shakenfist.util.access_tokens import request_namespace
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


def _resolve_artifact_ref(func, widen):
    # Shared body of the two ref decorators below. `widen` says
    # whether a *name* may resolve outside the caller's own namespace;
    # see those two for which routes get which, and why.
    def wrapper(*args, **kwargs):
        body_namespace = kwargs.pop('namespace', None)

        # Resolve and authorise the namespace once, regardless of
        # whether the caller named the artifact by UUID or by ref.
        # The UUID branch does not use `lookup_namespace` because
        # `Artifact.from_db` takes only a UUID, but the early-reject
        # for tenants passing a foreign namespace must still apply
        # so the two paths share an identical authz posture.
        lookup_namespace, err = api_base.resolve_lookup_namespace(
            body_namespace, 'artifact')
        if err:
            return err

        # Older style call: some internal flows pass artifact_uuid
        # directly. Routes only mount `<artifact_ref>` today, but the
        # body-key population in generic_wrapper means any body with
        # `artifact_uuid` would reach this branch.
        if 'artifact_uuid' in kwargs:
            kwargs['artifact_from_db'] = Artifact.from_db(
                kwargs['artifact_uuid'])
        else:
            try:
                # Naming a namespace turns the widening off whatever
                # the route asked for: that caller asked about one
                # namespace and must be answered from it or not at all.
                if widen and not body_namespace:
                    kwargs['artifact_from_db'] = \
                        Artifact.from_db_by_ref_visible_to(
                            kwargs.get('artifact_ref'), lookup_namespace)
                else:
                    kwargs['artifact_from_db'] = Artifact.from_db_by_ref(
                        kwargs.get('artifact_ref'), lookup_namespace)
            except exceptions.MultipleObjects as e:
                return sf_api.error(400, str(e), suppress_traceback=True)

        if not kwargs.get('artifact_from_db'):
            return sf_api.error(404, 'artifact not found')

        # UUID lookups bypass from_db_by_ref's namespace filter; if the
        # caller explicitly named a namespace, the resolved artifact
        # must live there or we reject it. This intentionally hides
        # shared artifacts owned by other namespaces — callers who want
        # shared visibility must omit `namespace`.
        a = kwargs['artifact_from_db']
        if body_namespace and a.namespace != body_namespace:
            LOG.with_fields({
                'artifact': a,
                'requested_namespace': body_namespace,
            }).info('Artifact not in requested namespace')
            return sf_api.error(404, 'artifact not found')

        return func(*args, **kwargs)
    return wrapper


def arg_is_artifact_ref(func):
    """Resolve a ref, with names scoped to the caller's namespace.

    Pair this with `requires_artifact_ownership`, which is to say use
    it on everything that changes an artifact.

    A name here means "mine". `requires_artifact_ownership` is the
    gate that actually refuses somebody else's artifact, and it does so
    whichever way the artifact was named -- trust no longer reaches
    past reading, so a uuid gets the same refusal a name does. This
    decorator is defence in depth on top of that: *resolving* a name
    into another namespace and then destroying what it landed on is a
    different proposition from being handed a uuid. `sf-client artifact
    delete build-cache` run in a namespace that has no `build-cache` of
    its own should say so, not quietly find the one belonging to a
    namespace which happens to trust it and then refuse with a 404 that
    looks like the name was wrong. Destructive actions get the narrow,
    boring reading of an ambiguous name.
    """
    return _resolve_artifact_ref(func, widen=False)


def arg_is_visible_artifact_ref(func):
    """Resolve a ref, with names spanning everything the caller sees.

    Pair this with `requires_artifact_access`, which is to say use it
    on read only routes.

    Reading is where the narrow reading is the surprising one: a
    tenant sees a shared image in `GET /artifacts`, asks for it by the
    name the listing just showed them, and gets a 404. Widening costs
    nothing here because the worst case is that the caller reads
    something they were already entitled to read, and
    `requires_artifact_access` still has to agree.
    """
    return _resolve_artifact_ref(func, widen=True)


def requires_artifact_ownership(func):
    # Requires that @arg_is_artifact_ref has already run -- that one
    # specifically, not the widening variant, so that a name never
    # resolves into another namespace on a route which then changes
    # what it found.
    #
    # The stricter of the two tests, for anything which changes the
    # artifact: the caller's own namespace, or system.
    #
    # Deliberately does *not* consult the shared flag -- sharing an
    # artifact publishes it for reading, it does not hand the world a
    # delete button -- and deliberately does not consult trust either.
    # Trust is a visibility mechanism. The operator guide introduces it
    # as a way to get the system namespace's cross-namespace *sight* on
    # a smaller scale, and letting somebody delete your artifacts is
    # not a smaller scale version of being able to see them. It is also
    # what the rest of the codebase already does:
    # requires_instance_ownership and requires_network_ownership both
    # test `request_namespace() not in [obj.namespace, 'system']`, and
    # artifacts were the only object type where trust reached past
    # reading. Now they match.
    #
    # Creating an artifact *in* a namespace which trusts you is a
    # different question and still allowed -- see the namespace checks
    # on the cache and upload routes. That is the "gifting" pattern the
    # operator guide's ci-images example is built on, and it is
    # additive: the receiving namespace opted in by trusting you, and
    # nothing it already had is lost.
    def wrapper(*args, **kwargs):
        if not kwargs.get('artifact_from_db'):
            return sf_api.error(404, 'artifact not found')

        a = kwargs['artifact_from_db']
        if request_namespace() not in [a.namespace, 'system']:
            LOG.with_fields({'artifact': a}).info(
                'Artifact not found, ownership test in decorator')
            return sf_api.error(404, 'artifact not found')

        return func(*args, **kwargs)
    return wrapper


def requires_artifact_access(func):
    # Requires that @arg_is_visible_artifact_ref has already run.
    #
    # The wider test, for read only routes: ownership as above, plus
    # any artifact explicitly marked shared, plus anything owned by a
    # namespace which trusts the caller. That last one is the whole of
    # what trust now buys -- sight, on the read routes, and nothing on
    # the routes which change things. This is exactly the
    # predicate the artifact listing filters on, and it reuses that
    # function rather than restating it -- "appears in the list" and
    # "is readable by uuid" have to be one rule, because two copies of
    # a visibility rule is two chances to get it wrong.
    #
    # It was previously two copies, and they did disagree. The test
    # here read `if a.shared and requestor not in [a.namespace,
    # 'system']`, which is inverted in both directions: it hid shared
    # artifacts from the namespaces they were shared with, and, far
    # worse, let any caller who knew a uuid read an *unshared*
    # artifact belonging to any namespace. `arg_is_artifact_ref`
    # short-circuits a uuid straight to `Artifact.from_db`, applying no
    # namespace filter of its own, so this decorator was the only thing
    # standing between a guessed or leaked uuid and another tenant's
    # artifact metadata.
    #
    # The refusal also logged through LOG.with_object, which
    # shakenfist_utilities no longer provides, so the one case the old
    # test did refuse raised AttributeError and answered 500 rather
    # than 404. That nobody noticed is itself the evidence for how
    # rarely the branch ran.
    def wrapper(*args, **kwargs):
        if not kwargs.get('artifact_from_db'):
            return sf_api.error(404, 'artifact not found')

        a = kwargs['artifact_from_db']
        if not namespace_or_shared_filter(request_namespace(), a):
            LOG.with_fields({'artifact': a}).info(
                'Artifact not found, access test in decorator')
            return sf_api.error(404, 'artifact not found')

        return func(*args, **kwargs)
    return wrapper


artifact_get_example = """{
    "artifact_type": "image",
    "blob_uuid": "25adc99e-369b-4959-a387-2ae046ee6ad4",
    "blobs": {
        "99": {
            "depends_on": null,
            "instances": [],
            "reference_count": 1,
            "size": 307552768,
            "uuid": "6c72c98e-e579-48c0-afd5-e1d02a834b99"
        },
        "100": {
            "depends_on": null,
            "instances": [],
            "reference_count": 1,
            "size": 307489280,
            "uuid": "af85e6cd-4a93-4fb9-becf-999e3a2c7526"
        },
        "101": {
            "depends_on": null,
            "instances": [],
            "reference_count": 1,
            "size": 308406784,
            "uuid": "25adc99e-369b-4959-a387-2ae046ee6ad4"
        }
    },
    "index": 101,
    "last_cluster_operation": {
        "op_type": "artifact_fetch_op",
        "op_uuid": "f3a52d98-f06b-4e2c-968d-21262e4b104b"
    },
    "max_versions": 3,
    "namespace": "system",
    "shared": true,
    "source_url": "debian:11",
    "state": "created",
    "uuid": "69ff59a7-f6ac-4f64-a575-bb54a7ee8961",
    "references_to": {},
    "references_from": {
        "artifact_index": [
            {
                "source_object_type": "artifact",
                "source_uuid": "69ff59a7-f6ac-4f64-a575-bb54a7ee8961",
                "relationship": "artifact_index",
                "relationship_value": "000000000101",
                "target_object_type": "blob",
                "target_uuid": "25adc99e-369b-4959-a387-2ae046ee6ad4",
                "created": 1683995934.357137,
                "last_active": 1684054381.217045
            }
        ]
    }
}"""


artifact_delete_example = """{
    "artifact_type": "image",
    "blob_uuid": "25adc99e-369b-4959-a387-2ae046ee6ad4",
    "blobs": {
        "99": {
            "depends_on": null,
            "instances": [],
            "reference_count": 1,
            "size": 307552768,
            "uuid": "6c72c98e-e579-48c0-afd5-e1d02a834b99"
        },
        "100": {
            "depends_on": null,
            "instances": [],
            "reference_count": 1,
            "size": 307489280,
            "uuid": "af85e6cd-4a93-4fb9-becf-999e3a2c7526"
        },
        "101": {
            "depends_on": null,
            "instances": [],
            "reference_count": 1,
            "size": 308406784,
            "uuid": "25adc99e-369b-4959-a387-2ae046ee6ad4"
        }
    },
    "index": 101,
    "max_versions": 3,
    "namespace": "system",
    "shared": true,
    "source_url": "debian:11",
    "state": "deleted",
    "uuid": "69ff59a7-f6ac-4f64-a575-bb54a7ee8961",
    "references_to": {},
    "references_from": {}
}"""


class ArtifactEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts', 'Get artifact information.',
        [('artifact_ref', 'path', 'uuidorname',
          'The UUID or name of the artifact.', True)],
        [(200, 'Information about a single artifact.', artifact_get_example),
         (404, 'Artifact not found.', None)]))
    @arg_is_visible_artifact_ref
    @requires_artifact_access
    @api_base.log_token_use
    def get(self, artifact_ref=None, artifact_from_db=None):
        ev = artifact_from_db.external_view()

        # One instance walk for the whole response, not one per version
        # (issue 3876). instance_usage_for_blob_uuid() would repeat the
        # walk, and every disk's dependency chain reads, per blob.
        blob_usage = instance_blob_usage()
        for idx in ev['blobs']:
            ev['blobs'][idx]['instances'] = blob_usage.get(
                str(ev['blobs'][idx]['uuid']), [])
        return ev

    @swag_from(api_base.swagger_helper(
        'artifacts', 'Delete an artifact.',
        [('artifact_ref', 'path', 'uuidorname',
          'The UUID or name of the artifact.', True)],
        [(200, ('The artifact has been deleted. The final state of the '
                'artifact is returned.'), artifact_delete_example),
         (404, 'Artifact not found.', None)]))
    @arg_is_artifact_ref
    @requires_artifact_ownership
    @api_base.log_token_use
    def delete(self, artifact_ref=None, artifact_from_db=None):
        if artifact_from_db.state.value == Artifact.STATE_DELETED:
            return
        artifact_from_db.add_event(
            EVENT_TYPE_AUDIT, 'deletion request from REST API')
        artifact_from_db.delete()
        return artifact_from_db.external_view()


artifacts_get_example = """[
    {
        "artifact_type": "label",
        "blob_uuid": "21f69064-679e-40c3-a23e-a7ff79cbb596",
        ...
        "state": "created",
        "uuid": "3420f4ac-529a-4b34-b8d8-c05a838b9e0c",
        "version": 4
    },
    {
        "artifact_type": "label",
        "blob_uuid": "a50f0af1-f8f0-4b10-88bb-bf1279575932",
        ...
        "state": "created",
        "uuid": "6c8b0b52-ab1b-4351-b50f-d8a32999fd29",
        "version": 4
    },
        {
        "artifact_type": "label",
        "blob_uuid": "99c4eeca-088f-48ee-918a-f7aa7907f83b",
        ...
        "state": "created",
        "uuid": "e01c71eb-33d4-431a-b70f-df764fa7ed99",
        "version": 4
    },
]"""


artifact_uuid_list_example = """[
    0411861d-c323-4ea7-85b5-2b4fcbe4493c,
    050e4397-d1ee-4e8f-ac76-7371977d7530
]"""


class ArtifactsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts', ('Get all artifacts visible to the currently '
                      'authenticated namespace.'),
        [('node', 'body', 'node',
          'Limit results to a specific hypervisor node.', False)],
        [(200, ('A list of artifact dictionaries, each containing the same '
                'output as a GET for a single artifact would show.'),
          artifacts_get_example)]))
    @api_base.log_token_use
    def get(self, node=None):
        # One instance walk for the whole listing, not one per
        # artifact (issue 3876).
        blob_usage = instance_blob_usage()

        retval = []
        for a in Artifacts(filters=[
                partial(namespace_or_shared_filter, request_namespace())],
                prefilter='active'):
            idx = a.most_recent_index
            ev = a.external_view()

            if 'blob_uuid' in idx:
                b = Blob.from_db(idx['blob_uuid'])
                if b and (not node or node in b.locations):
                    ev['instances'] = blob_usage.get(str(b.uuid), [])

            retval.append(ev)

        return retval

    @swag_from(api_base.swagger_helper(
        'artifacts', ('Fetch an image artifact into the cluster.'),
        [
            ('url', 'body', 'url', 'The URL to fetch.', True),
            ('shared', 'body', 'boolean',
             ('Should this artifact be shared? You must be authenticated against '
              'the system namespace to set this option to True.'), True),
            ('namespace', 'body', 'namespace',
             ('Which namespace to store the artifact in. You must be authenticated '
              'against the system namespace to set this option.'), False)
        ],
        [(200, 'Information about a single artifact.', artifact_get_example),
         (404, 'Artifact not found.', None)]))
    @api_base.log_token_use
    @api_base.requires_namespace_exist_if_specified
    def post(self, url=None, shared=False, namespace=None):
        # The only artifact type you can force the cluster to fetch is an
        # image, so TYPE_IMAGE is assumed here. We ensure that the image exists
        # in the database in an initial state here so that it will show up in
        # image list requests. The image is fetched by the queued job later.
        if not namespace:
            namespace = request_namespace()

        if not namespace_is_trusted(namespace, request_namespace()):
            return sf_api.error(404, 'namespace not found')

        # As on the upload route, resolve by ownership. The trust check
        # above only establishes that we may act on the named namespace;
        # from_url would additionally have matched an artifact belonging
        # to a third namespace which shares with, or is trusted by, that
        # one, and the image_fetch queued below adds a version to
        # whatever it lands on.
        #
        # Spelled out rather than deferred to owned_from_url_or_new,
        # because the two cases below are authorised differently and
        # that is not an accident of style. The trust check above is
        # enough to gift this namespace an artifact it did not have;
        # replacing what one it already owns resolves to takes the
        # owning namespace or system, which is the stricter test below
        # rather than a restatement of the trust.
        a = Artifact.owned_from_url(Artifact.TYPE_IMAGE, url,
                                    namespace=namespace)
        if a:
            if request_namespace() not in [a.namespace, 'system']:
                return sf_api.error(404, 'namespace not found')
        else:
            a = Artifact.new(Artifact.TYPE_IMAGE, url, namespace=namespace)

        a.add_event(EVENT_TYPE_AUDIT, 'creation request from REST API')

        # Only admin can create shared artifacts
        if shared:
            if request_namespace() != 'system':
                return sf_api.error(
                    403, 'only the system namespace can create shared artifacts')
            a.shared = True

        afo_create_and_enqueue(
            namespace,
            url,
            None,
            [afo_tasks.image_fetch],
            PRIORITY.user_facing,
            artifact_uuid=a.uuid,
            request_id=util_general.get_request_id(),
            runs_after=[a.last_cluster_operation])

        return a.external_view()

    @swag_from(api_base.swagger_helper(
        'artifacts', ('Delete all artifacts in a namespace.'),
        [
            ('confirm', 'body', 'boolean', 'Yes I really mean it.', True),
            ('namespace', 'body', 'namespace',
             ('Which namespace to remove artifacts from. You must be authenticated '
              'against the system namespace to set this option.'), False)
        ],
        [(200, 'A list of artifact uuids that were deleted.',
          artifact_uuid_list_example),
         (400, ('Confirm parameter not set, or a system user must specify a '
                'namespace.'), None),
         (401, 'You cannot delete other namespaces.', None)]))
    @api_base.log_token_use
    @api_base.requires_namespace_exist_if_specified
    def delete(self, confirm=False, namespace=None):
        if confirm is not True:
            return sf_api.error(400, 'parameter confirm is not set true')

        if request_namespace() == 'system':
            if not isinstance(namespace, str):
                # A client using a system key must specify the namespace. This
                # ensures that deleting all artifacts in the cluster (by
                # specifying namespace='system') is a deliberate act.
                return sf_api.error(400, 'system user must specify parameter namespace')

        else:
            if namespace and namespace != request_namespace():
                return sf_api.error(401, 'you cannot delete other namespaces')
            namespace = request_namespace()

        deleted = []
        for a in Artifacts(namespace=namespace):
            a.add_event(EVENT_TYPE_AUDIT, 'deletion request from REST API')
            a.delete()
            deleted.append(str(a.uuid))

        return deleted


class ArtifactUploadEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts', 'Convert an upload into an artifact.',
        [
            ('artifact_name', 'path', 'string',
             'The name of the artifact. This is used to construct a source url if '
             'you do not specify one with source_url.', True),
            ('upload_uuid', 'body', 'uuid',
             'The UUID of an upload to convert to an artifact. You must either '
             'specify this or blob_uuid.', False),
            ('blob_uuid', 'body', 'uuid',
             'The UUID of a blob to convert to an artifact. This is used by the '
             'command line client if an upload would have created a duplicate '
             'blob to one already in existence. You must specify either this '
             'or upload_uuid.', False),
            ('source_url', 'body', 'url',
             'The URL the artifact should claim to be downloaded from.', False),
            ('shared', 'body', 'boolean',
                'Is this artifact shared? Defaults to False.', False),
            ('namespace', 'body', 'namespace',
                ('Which namespace to remove artifacts from. You must be authenticated '
                 'against the system namespace to set this option.'), False),
            ('artifact_type', 'body', 'string',
             ('The type of the artifact. Should be one of "image" or "other". '
              'Defaults to "image" if not specified.'), False)
        ],
        [(200, 'Information about a single artifact.', artifact_get_example),
         (403, 'Invalid artifact type specified.', None),
         (404, 'Upload, namespace, or blob not found.', None)]))
    @api_base.log_token_use
    @api_base.requires_namespace_exist_if_specified
    def post(self, artifact_name=None, upload_uuid=None, blob_uuid=None,
             source_url=None, shared=False, namespace=None, artifact_type='image'):
        if upload_uuid and blob_uuid:
            return sf_api.error(400, 'only specify one of upload_uuid and blob_uuid')

        u = None
        if upload_uuid:
            # Proxy to the correct node and continue there.
            u = Upload.from_db(upload_uuid)
            if not u:
                return sf_api.error(404, 'upload not found')

            if u.node != config.NODE_NAME:
                path = flask.request.environ['PATH_INFO']
                url = f'http://{u.node}:13000{path}'
                api_token = get_api_token(
                    f'http://{u.node}:13000', namespace=request_namespace())
                r = requests.request(
                    flask.request.environ['REQUEST_METHOD'], url,
                    data=json.dumps(sf_api.flask_get_post_body()),
                    headers={
                        'Authorization': api_token,
                        'User-Agent': util_general.get_user_agent(),
                        'X-Request-ID': flask.request.headers.get('X-Request-ID')
                    })

                LOG.info('Proxied %s %s returns: %d, %s' % (
                        flask.request.environ['REQUEST_METHOD'], url,
                        r.status_code, r.text))
                resp = flask.Response(r.text,  mimetype='application/json')
                resp.status_code = r.status_code
                return resp

        if not source_url:
            source_url = ('%s%s/%s'
                          % (UPLOAD_URL, request_namespace(), artifact_name))

        if not namespace:
            namespace = request_namespace()

        if artifact_type == 'image':
            artifact_type_value = Artifact.TYPE_IMAGE
        elif artifact_type == 'other':
            artifact_type_value = Artifact.TYPE_OTHER
        else:
            return sf_api.error(403, 'invalid artifact type specified')

        # Resolve by ownership rather than visibility, then authorise the
        # two cases apart. from_url would have matched an artifact owned
        # by anyone who merely shares with us or trusts us, and the write
        # below is add_index, which ends in delete_old_versions -- so a
        # trusted namespace could replace what somebody else's artifact
        # resolves to and destroy the versions underneath it.
        #
        # Creating is additive and a trust is enough for it, which is what
        # the operator guide promises. Pushing a version into an artifact
        # a namespace already owns is not additive, so that takes the
        # owning namespace or system, exactly as requires_artifact_
        # ownership demands everywhere else.
        a = Artifact.owned_from_url(artifact_type_value, source_url,
                                    namespace=namespace)
        if a:
            if request_namespace() not in [a.namespace, 'system']:
                return sf_api.error(404, 'namespace not found')
        else:
            if not namespace_is_trusted(namespace, request_namespace()):
                return sf_api.error(404, 'namespace not found')
            a = Artifact.new(artifact_type_value, source_url,
                             name=artifact_name, namespace=namespace)

        # The audit event is written after the authorisation check, not
        # before it, so a refused caller cannot append to the event log of
        # a namespace it has just been told does not exist.
        a.add_event(EVENT_TYPE_AUDIT, 'convert upload to artifact from REST API')

        # Only admin can create shared artifacts
        if shared:
            if request_namespace() != 'system':
                return sf_api.error(
                    403, 'only the system namespace can create shared artifacts')
            a.shared = True

        with a.get_lock(timeout=config.MAX_IMAGE_TRANSFER_SECONDS):
            if not blob_uuid:
                # Convert upload to a blob
                blob_uuid = str(uuid.uuid4())
                blob_path = Blob.filepath(blob_uuid)

                upload_dir = os.path.join(config.STORAGE_PATH, 'uploads')
                upload_path = os.path.join(upload_dir, str(u.uuid))

                # NOTE(mikal): we can't use os.rename() here because these paths
                # might be on different filesystems.
                shutil.move(upload_path, blob_path)
                st = os.stat(blob_path)
                b = Blob.new(
                    blob_uuid,
                    time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime()),
                    time.time())
                b.size = st.st_size
                b.state = Blob.STATE_CREATED
                b.observe()
                b.verify_checksum()
                b.request_replication()

            else:
                b = Blob.from_db(blob_uuid)
                if not b:
                    return sf_api.error(404, 'blob not found')

            a.add_event(EVENT_TYPE_AUDIT, 'upload complete')
            a.add_index(b.uuid)
            a.state = Artifact.STATE_CREATED

            if upload_uuid:
                u.hard_delete()

            return a.external_view()


artifact_events_example = """[
    ...
    {
            "duration": null,
            "extra": {},
            "fqdn": "sf-3",
            "message": "artifact fetch complete",
            "timestamp": 1684718452.2673004,
            "type": "audit"
        },
    ...
]"""


class ArtifactEventsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts', 'Get artifact event information.',
        [
            ('artifact_ref', 'path', 'uuidorname',
             'The UUID or name of the artifact.', True),
            ('event_type', 'body', 'string', 'The type of event to return.', False),
            ('limit', 'body', 'integer',
             'The number of events to return, defaults to 100 and is '
             'capped at 1000.', False, {'minimum': 1, 'maximum': 1000})
        ],
        [(200, 'Event information about a single artifact.', artifact_events_example),
         (404, 'Artifact not found.', None)]))
    @arg_is_visible_artifact_ref
    @requires_artifact_access
    @api_base.log_token_use
    def get(self, artifact_ref=None, event_type=None, limit=100, artifact_from_db=None):
        return api_base.object_events_response(
            'artifact', artifact_from_db.uuid, limit, event_type)


artifact_versions_example = """[
    ...
    {
        "uuid": "cc6a6a96-8182-474a-ab31-45f1f9310b44",
        "state": "created",
        "size": 3093721088,
        "modified": 1669567073.027112,
        "fetched_at": 1669567073.027112,
        "depends_on": null,
        "transcodes": {
            "gunzip;qcow2;cluster_size": "84ae268a-a18d-49e7-8195-d151016561cf"
        },
        "locations": [
            "sf-3",
            "sf-2",
            "sf-4",
            "sf-1"
        ],
        "reference_count": 177,
        "instances": [
            "6bcb21a4-b2a5-4fba-81f5-5c8348e41b5f"
        ],
        "last_used": 1669787223.1966972,
        "cluster_size": 2097152.0,
        "compat": 1.1,
        "compression type": "zlib",
        "disk size": "2.87 GiB",
        "extended l2": "false",
        "file format": "qcow2",
        "mime-type": "application/octet-stream",
        "virtual size": 32212254720.0,
        "index": 6
    }
]"""


class ArtifactVersionsEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts', 'Get artifact version information.',
        [('artifact_ref', 'path', 'uuidorname',
          'The UUID or name of the artifact.', True)],
        [(200, 'A list of the blobs which form the artifact versions.',
          artifact_versions_example),
         (404, 'Artifact not found.', None)]))
    @arg_is_visible_artifact_ref
    @requires_artifact_access
    def get(self, artifact_ref=None, artifact_from_db=None):
        # One instance walk for the whole listing, not one per version
        # (issue 3876).
        blob_usage = instance_blob_usage()

        retval = []
        for idx in artifact_from_db.get_all_indexes():
            b = Blob.from_db(idx['blob_uuid'])
            # A version whose blob has vanished is skipped rather than
            # reported with the previous version's body: bout used to be
            # assigned inside this conditional and indexed outside it,
            # so a missing blob either duplicated the last version under
            # a new index or, on the first iteration, raised NameError
            # and answered 500.
            if not b:
                continue

            bout = b.external_view()
            bout['instances'] = blob_usage.get(str(b.uuid), [])
            bout['index'] = idx['index']
            retval.append(bout)
        return retval

    @swag_from(api_base.swagger_helper(
        'artifacts', 'Set the maximum number of versions for an artifact.',
        [
            ('artifact_ref', 'path', 'uuidorname',
             'The UUID or name of the artifact.', True),
            ('max_versions', 'body', 'unsignedinteger',
             'The maximum number of versions, or revert to the default it not set.',
             False)
        ],
        [(200, 'No return value', ''),
         (400, 'The max_versions must be an integer.', None),
         (404, 'Artifact not found.', None)]))
    @arg_is_artifact_ref
    @requires_artifact_ownership
    @api_base.log_token_use
    def post(self, artifact_ref=None, artifact_from_db=None,
             max_versions=config.ARTIFACT_MAX_VERSIONS_DEFAULT):
        # The coercion and the negativity check live in
        # artifact.validated_max_versions() so that all three routes
        # which write this attribute -- here, label create and
        # instance snapshot -- refuse the same values. The declaration
        # publishes minimum 0; this is the server backing it, rather
        # than waiting for phase 4 to compile the bound.
        try:
            mv = validated_max_versions(max_versions)
        except exceptions.InvalidMaxVersions as e:
            return sf_api.error(400, str(e))
        artifact_from_db.add_event(
            EVENT_TYPE_AUDIT, 'max versions set from REST API')
        artifact_from_db.max_versions = mv


class ArtifactVersionEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts',
        ('Delete the specified artifact version. Note that this will only '
         'remove the blob if its reference count reaches zero. If the artifact '
         'has no remaining versions, it will have its state set to deleted.'),
        [
            ('artifact_ref', 'path', 'uuidorname',
             'The UUID or name of the artifact.', True),
            ('version_id', 'path', 'unsignedinteger',
             'The version number to remove.', True)
        ],
        [(200, 'Information about a single artifact.', artifact_get_example),
         (404, 'Artifact index not found.', None)]))
    @arg_is_artifact_ref
    @requires_artifact_ownership
    @api_base.log_token_use
    def delete(self, artifact_ref=None, artifact_from_db=None, version_id=0):
        try:
            ver_index = int(version_id)
        except ValueError:
            return sf_api.error(400, 'version index is not an integer')

        indexes = list(artifact_from_db.get_all_indexes())
        for idx in indexes:
            if idx['index'] == ver_index:
                artifact_from_db.add_event(
                    EVENT_TYPE_AUDIT, 'index deletion request from REST API',
                    extra={'index': idx['index']})
                artifact_from_db.del_index(idx['index'])
                if len(indexes) == 1:
                    artifact_from_db.state = Artifact.STATE_DELETED
                return artifact_from_db.external_view()

        return sf_api.error(404, 'artifact index not found')


class ArtifactShareEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts', 'Share the specified artifact with all namespaces.',
        [('artifact_ref', 'path', 'uuidorname',
          'The UUID or name of the artifact.', True)],
        [(200, 'Information about a single artifact.', artifact_get_example),
         (403, 'Only artifacts in the system namespace may be shared.', None),
         (404, 'Artifact not found.', None)]))
    @arg_is_artifact_ref
    @requires_artifact_ownership
    @api_base.log_token_use
    def post(self, artifact_ref=None, artifact_from_db=None):
        if artifact_from_db.namespace != 'system':
            return sf_api.error(
                403, 'only artifacts in the system namespace can be shared')
        artifact_from_db.add_event(
            EVENT_TYPE_AUDIT, 'artifact share request from REST API')
        artifact_from_db.shared = True
        return artifact_from_db.external_view()


class ArtifactUnshareEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts', 'Unshare the specified artifact with all namespaces.',
        [('artifact_ref', 'path', 'uuidorname',
          'The UUID or name of the artifact.', True)],
        [(200, 'Information about a single artifact.', artifact_get_example),
         (403, 'Artifact not shared.', None),
         (404, 'Artifact not found.', None)]))
    @arg_is_artifact_ref
    @requires_artifact_ownership
    @api_base.log_token_use
    def post(self, artifact_ref=None, artifact_from_db=None):
        if not artifact_from_db.shared:
            return sf_api.error(403, 'artifact not shared')
        artifact_from_db.add_event(
            EVENT_TYPE_AUDIT, 'artifact unshare request from REST API')
        artifact_from_db.shared = False
        return artifact_from_db.external_view()


class ArtifactMetadatasEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts', 'Fetch metadata for an artifact.',
        [('artifact_ref', 'path', 'uuidorname',
          'The artifact to fetch metadata for.', True)],
        [(200, 'Artifact metadata, if any.', None),
         (404, 'Artifact not found.', None)],
        requires_admin=True))
    @arg_is_artifact_ref
    @requires_artifact_ownership
    @api_base.log_token_use
    def get(self, artifact_ref=None, artifact_from_db=None):
        return artifact_from_db.metadata

    @swag_from(api_base.swagger_helper(
        'artifacts', 'Add metadata for an artifact.',
        [
            ('artifact_ref', 'path', 'uuidorname', 'The artifact to add a key to.', True),
            ('key', 'body', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Artifact not found.', None)],
        requires_admin=True))
    @arg_is_artifact_ref
    @requires_artifact_ownership
    @api_base.log_token_use
    def post(self, artifact_ref=None, key=None, value=None, artifact_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        artifact_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'post'})
        artifact_from_db.add_metadata_key(key, value)


class ArtifactMetadataEndpoint(api_base.Resource):
    @swag_from(api_base.swagger_helper(
        'artifacts', 'Update a metadata key for an artifact.',
        [
            ('artifact_ref', 'path', 'uuidorname', 'The artifact to add a key to.', True),
            ('key', 'path', 'string', 'The metadata key to set', True),
            ('value', 'body', 'string', 'The value of the key.', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Artifact not found.', None)],
        requires_admin=True))
    @arg_is_artifact_ref
    @requires_artifact_ownership
    @api_base.log_token_use
    def put(self, artifact_ref=None, key=None, value=None, artifact_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        if not value:
            return sf_api.error(400, 'no value specified')
        artifact_from_db.add_event(
            EVENT_TYPE_AUDIT, 'set metadata key request from REST API',
            extra={'key': key, 'value': value, 'method': 'put'})
        artifact_from_db.add_metadata_key(key, value)

    @swag_from(api_base.swagger_helper(
        'artifacts', 'Delete a metadata key for an artifact.',
        [
            ('artifact_ref', 'path', 'uuidorname', 'The artifact to remove a key from.', True),
            ('key', 'path', 'string', 'The metadata key to set', True)
        ],
        [(200, 'Nothing.', None),
         (400, 'One of key or value are missing.', None),
         (404, 'Artifact not found.', None)],
        requires_admin=True))
    @arg_is_artifact_ref
    @requires_artifact_ownership
    @api_base.log_token_use
    def delete(self, artifact_ref=None, key=None, value=None, artifact_from_db=None):
        if not key:
            return sf_api.error(400, 'no key specified')
        artifact_from_db.add_event(
            EVENT_TYPE_AUDIT, 'delete metadata key request from REST API',
            extra={'key': key})
        artifact_from_db.remove_metadata_key(key)


artifact_outstanding_operations_example = """[
    [
        {
            "instance_uuid": "d657c162-65c2-4abd-b81f-5908632e6c3f",
            "namespace": "system",
            "operation_type": "artifact_fetch_op",
            "state": "complete",
            "tasks": [
                "image_fetch"
            ],
            "url": "debian:12",
            "uuid": "2f9cd517-0aab-443c-aaed-7fad23a0115d"
        }
    ]
]"""


class ArtifactOutstandingOperationsEndpoint(api_base.Resource):
    # NOTE(mikal): note that arguments from URL routes (object uuid for example),
    # are not included in the webargs schema because webargs doesn't appear to
    # know how to find them.
    get_args = {
        'all': fields.Boolean(load_default=False)
    }

    @swag_from(api_base.swagger_helper(
        'artifacts', 'Get the outstanding cluster operations for an artifact.',
        [('artifact_ref', 'path', 'uuidorname',
          'The UUID or name of the artifact.', True),
         ('all', 'query', 'boolean',
          'Include operations which have already completed, rather than '
          'only those still in flight.', False)],
        [(
            200,
            'A list of the cluster operations not yet executed for this artifact.',
            artifact_outstanding_operations_example),
         (404, 'Artifact not found.', None)]))
    @use_kwargs(get_args, location='json_or_query')
    @arg_is_visible_artifact_ref
    @requires_artifact_access
    def get(self, artifact_ref=None, all=False, artifact_from_db=None):
        retval = []
        for op in artifact_from_db.get_cluster_operations(
            outstanding_only=(not all)
        ):
            retval.append(op.external_view())
        return retval
