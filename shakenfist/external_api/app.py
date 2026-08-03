#################################################################################
# DEAR FUTURE ME... The order of decorators on these API methods deeply deeply  #
# matters. We need to verify auth before anything, and we need to fetch things  #
# from the database before we make decisions based on those things. So remember #
# the outer decorator is executed first!                                        #
#                                                                               #
# Additionally, you should use suppress_traceback=True in calls to error()      #
# which exist inside an expected exception block, otherwise we'll log a stray   #
# traceback.                                                                    #
#################################################################################
# To run a local test API, use this command line:
#     SHAKENFIST_MARIADB_HOST=localhost flask \
#         --app shakenfist.external_api.app:app --debug run
import base64
import json
import sys

import flasgger
import flask
import flask_restful
from flask import got_request_exception
from flask_jwt_extended import JWTManager
from flask_request_id import RequestID
from shakenfist_utilities import logs  # noreorder

from shakenfist import constants
from shakenfist import eventlog
from shakenfist.schema.object_types import ObjectType
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.node import Node
from shakenfist.external_api import admin as api_admin
from shakenfist.external_api import agentoperation as api_agentoperation
from shakenfist.external_api import artifact as api_artifact
from shakenfist.external_api import auth as api_auth
from shakenfist.external_api import blob as api_blob
from shakenfist.external_api import clusteroperation as api_clusteroperation
from shakenfist.external_api import health
from shakenfist.external_api import instance as api_instance
from shakenfist.external_api import interface as api_interface
from shakenfist.external_api import label as api_label
from shakenfist.external_api import network as api_network
from shakenfist.external_api import node as api_node
from shakenfist.external_api import snapshot as api_snapshot
from shakenfist.external_api import base as api_base
from shakenfist.external_api import upload as api_upload
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')
# The api runs under gunicorn rather than via write_pid_file(), so
# the package-wide level (which quiets the modules the per-module
# set_log_level calls cannot reach, like util.concurrency) is
# applied here at worker import time instead.
daemon.apply_log_level('api')


app = flask.Flask(__name__)
RequestID(app)
api = flask_restful.Api(app, catch_all_404s=False)

app.config['JWT_SECRET_KEY'] = config.AUTH_SECRET_SEED
jwt = JWTManager(app)

swagger = flasgger.Swagger(app, template={
    'swagger': '2.0',
    'info': {
        'title': 'Shaken Fist REST API',
        'description': 'Shaken Fist cluster control via REST API',
        'version': util_general.get_version(),
    },
    'host': config.API_ADVERTISED_HOST,
    'basePath': config.API_ADVERTISED_BASE_PATH,
    'schemes': config.API_ADVERTISED_HTTP_SCHEMES
})

# Use our handler to get SF log format (instead of gunicorn's handlers)
app.logger.handlers = [HANDLER]


# Record exceptions that occur during request handling. The method decorators
# in base.Resource catch exceptions raised during method execution, but
# exceptions during Flask-RESTful's JSON response serialization happen after
# the method returns. Flask-RESTful has its own error handling that bypasses
# Flask's @app.errorhandler decorator, so we use the got_request_exception
# signal instead, which fires for all unhandled exceptions.
#
# We only record non-HTTP exceptions (actual errors), not expected HTTP
# responses like 404 Not Found or 401 Unauthorized.
#
# This deliberately does not pass already_logged=True, unlike the
# record_exception decorator in base.py. Flask logs this path itself,
# via app.log_exception() after the signal fires, and we have no way to
# attach the exception_hash to a line we do not emit -- so suppressing
# our WARNING here would take the only correlation fields with it
# (issue 3590). The duplicate is accepted because this signal only sees
# response-serialisation failures, which are rare, rather than the
# per-task volume that motivated the change.
def _record_exception(sender, exception, **extra):
    from werkzeug.exceptions import HTTPException
    if isinstance(exception, HTTPException):
        return
    util_exceptions.record_exception(*sys.exc_info())


got_request_exception.connect(_record_exception, app)


@app.before_request
def resolve_node_uuid():
    """Resolve config.NODE_UUID if not already set.

    Gunicorn workers don't go through Daemon.run(), so NODE_UUID won't be
    populated by _resolve_node_uuid(). We resolve it lazily on the first
    request instead.
    """
    # Health probes must never touch the database. On a configured node
    # NODE_UUID is already set so this is a no-op anyway, but short-circuit
    # explicitly so the "a /readyz burst makes zero DB calls" guarantee holds
    # unconditionally -- even on a worker whose NODE_UUID is not yet resolved.
    if _is_health_probe():
        return
    if config.NODE_UUID:
        return
    node_uuid = Node._load_persisted_uuid()
    if not node_uuid:
        n = Node.from_db(config.NODE_NAME)
        if n:
            node_uuid = str(n.uuid)
    if node_uuid:
        config.NODE_UUID = node_uuid
        LOG.with_fields({'node_uuid': node_uuid}).info(
            'Resolved node UUID in API worker')
    else:
        LOG.warning('Failed to resolve node UUID in API worker')


# The Root endpoint at '/' is hit on every health probe from
# sf-queues (see daemons/daemon.py:health_check_api). Each probe
# would otherwise produce two audit events per worker per cluster
# every few seconds, which under load saturates the eventlog
# server and channels every event through the event_dlq MariaDB
# path -- see GH actions run 26612233454 for the full cascade.
def _is_health_probe():
    return flask.request.path in api_base.HEALTH_PROBE_PATHS


# Request and response bodies are logged verbatim below, which is
# useful for debugging and unacceptable for the routes which carry
# credentials: POST /auth is sent a plaintext namespace key and
# answers with a JWT, and the key management routes are sent key
# secrets. Every such route lives under /auth, so bodies there are
# not logged at all. Redacting by field name instead was rejected
# because "key" means a metadata key name on most endpoints and a
# secret on only a few, so the check would have to know which route
# it was on anyway -- and would silently start leaking the day
# somebody adds a route it had not heard of.
#
# The URL is still logged, so an audit reader keeps the namespace and
# the key name. Only the credential itself is lost.
def _handles_credentials():
    path = flask.request.path
    return path == '/auth' or path.startswith('/auth/')


REDACTED_BODY = '...body not logged as this route handles credentials...'


@app.before_request
def log_request_info():
    if _is_health_probe():
        return

    if _handles_credentials():
        body = REDACTED_BODY
    elif not flask.request.content_length:
        body = ''
    elif flask.request.content_length > 1024:
        body = '...body not logged as greater than 1kb...'
    else:
        body = flask.request.get_json(silent=True)
        if not body:
            body = 'base64:%s' % base64.b64encode(flask.request.get_data())

    eventlog.add_event(
        constants.EVENT_TYPE_AUDIT, ObjectType.API_REQUESTS,
        flask.request.environ.get('FLASK_REQUEST_ID', 'none'),
        '%s api request received' % flask.request.method, extra={
            'url': str(flask.request.url),
            'body': str(body)
        })


@app.after_request
def log_response_info(response):
    if _is_health_probe():
        return response

    if _handles_credentials():
        body = REDACTED_BODY
    elif response.is_streamed:
        body = '...body not logged as it is streamed...'
    elif response.direct_passthrough:
        body = '...body not logged as response is streaming...'
    elif response.content_length > 1024:
        body = '...body not logged as greater than 1kb...'
    else:
        body = response.get_data()

        try:
            body = json.loads(body)
        except ValueError:
            ...

        try:
            body = str(body)
        except TypeError:
            body = 'base64:%s' % base64.b64encode(body)

    eventlog.add_event(
        constants.EVENT_TYPE_AUDIT, ObjectType.API_REQUESTS,
        flask.request.environ.get('FLASK_REQUEST_ID', 'none'),
        '%s api response sent' % flask.request.method, extra={
            'status': response.status_code,
            'body': body
        })
    return response


class Root(api_base.Resource):
    @api_base.public
    def get(self):
        resp = flask.Response(
            ('<html><head><title>Shaken Fist REST API service</title></head>'
             '<body><h1>Shaken Fist REST API service</h1>'
             '<p>You might be interested in the <a href="/apidocs">apidocs</a>.</p>'
             '<p>Machine searchable API capabilities:</p><ul>'

             '<li>admin: cluster-cacert, cluster-resources, '
             'vdi-token-pubkey</li>'
             '<li>agent-operations: agentoperations-crud, instance-agentoperations, '
             'instance-agentoperations-all, agentoperations-put-with-mode</li>'
             '<li>artifacts: artifact-metadata, artifact-upload-types, '
             'artifact-clusteroperations</li>'
             # scope-enforcement lets a client tell whether a 403 means
             # "your token is not scoped for this" on this cluster, or
             # something else entirely.
             '<li>auth: trusted-issuers, generated-key-secrets, '
             'scope-enforcement</li>'
             '<li>blobs: blob-metadata, blob-search-by-hash, blob-data-limit, '
             'blob-hash-sha1, blob-hash-sha256, blob-hash-xxh128, blob-events, '
             'blob-checksums, blob-single-checksum</li>'
             '<li>cluster-operations: get-cluster-operations, '
             'cluster-operation-chain, cluster-operations-by-target</li>'
             '<li>events: events-by-type</li>'
             '<li>instances: pure-affinity, spice-vdi-console, vdi-console-helper, '
             'vdi-console-proxy, '
             'instance-put-blob, instance-execute, instance-get, instance-screenshot, '
             'get-instance-namespace, hot-plug-interface, '
             'include-queued-agent-operations, instance-clusteroperations</li>'
             '<li>networks: list-addresses, route-addresses, get-network-namespace, '
             'provide-dns, extra-dns-entries, network-clusteroperations, '
             'network-delete-async</li>'
             '<li>networkinterfaces: interface-metadata</li>'
             '<li>nodes: node-get, node-metadata, node-process-metrics</li>'
             '</ul></p></body></html>'),
            mimetype='text/html')
        resp.status_code = 200
        return resp


class Livez(api_base.Resource):
    # Unauthenticated: liveness means "this worker process is serving HTTP".
    # It deliberately does not check any downstream dependency, so a healthy
    # but not-yet-ready worker is not killed by an orchestrator liveness probe.
    @api_base.public
    def get(self):
        resp = flask.Response('ok', mimetype='text/plain')
        resp.status_code = 200
        return resp


class Readyz(api_base.Resource):
    # Unauthenticated: readiness reflects whether this worker should receive
    # traffic. health.is_ready() already returns False when the worker is
    # draining or its cached readiness state is stale.
    @api_base.public
    def get(self):
        if health.is_ready():
            return flask.Response('ready', mimetype='text/plain', status=200)
        return flask.Response('not ready', mimetype='text/plain', status=503)


# TODO(mikal): we are inconsistent in this interface. Elsewhere the object type
# is always singular, here its a mix. We should move all of these to the
# singular form for consistency.
api.add_resource(Root, '/')
api.add_resource(Livez, '/livez')
api.add_resource(Readyz, '/readyz')
# Same Resource on a second path; flask-restful derives the endpoint name from
# the class, so an explicit endpoint avoids a duplicate-endpoint collision.
api.add_resource(Readyz, '/healthz', endpoint='healthz')

api.add_resource(api_admin.AdminLocksEndpoint, '/admin/locks')
api.add_resource(api_admin.AdminClusterCaCertificateEndpoint, '/admin/cacert')
api.add_resource(api_admin.AdminVDITokenPublicKeyEndpoint,
                 '/admin/vditokenpubkey')
api.add_resource(api_admin.AdminResourcesEndpoint, '/admin/resources')

api.add_resource(api_artifact.ArtifactEndpoint, '/artifacts/<artifact_ref>')
api.add_resource(api_artifact.ArtifactsEndpoint, '/artifacts')
api.add_resource(api_artifact.ArtifactUploadEndpoint,
                 '/artifacts/upload/<artifact_name>')
api.add_resource(api_artifact.ArtifactEventsEndpoint,
                 '/artifacts/<artifact_ref>/events')
api.add_resource(api_artifact.ArtifactMetadatasEndpoint,
                 '/artifacts/<artifact_ref>/metadata')
api.add_resource(api_artifact.ArtifactMetadataEndpoint,
                 '/artifacts/<artifact_ref>/metadata/<key>')
api.add_resource(api_artifact.ArtifactShareEndpoint,
                 '/artifacts/<artifact_ref>/share')
api.add_resource(api_artifact.ArtifactUnshareEndpoint,
                 '/artifacts/<artifact_ref>/unshare')
api.add_resource(api_artifact.ArtifactVersionsEndpoint,
                 '/artifacts/<artifact_ref>/versions')
api.add_resource(api_artifact.ArtifactVersionEndpoint,
                 '/artifacts/<artifact_ref>/versions/<version_id>')
api.add_resource(api_artifact.ArtifactOutstandingOperationsEndpoint,
                 '/artifacts/<artifact_ref>/clusteroperations')

api.add_resource(api_agentoperation.AgentOperationEndpoint,
                 '/agentoperations/<operation_uuid>')

api.add_resource(api_auth.AuthEndpoint, '/auth')
api.add_resource(api_auth.AuthNamespacesEndpoint, '/auth/namespaces')
api.add_resource(api_auth.AuthNamespaceEndpoint,
                 '/auth/namespaces/<namespace>')
api.add_resource(api_auth.AuthIssuersEndpoint, '/auth/issuers')
api.add_resource(api_auth.AuthIssuerEndpoint,
                 '/auth/issuers/<issuer_name>')
api.add_resource(api_auth.AuthNamespaceKeysEndpoint,
                 '/auth/namespaces/<namespace>/keys')
api.add_resource(api_auth.AuthNamespaceKeyEndpoint,
                 '/auth/namespaces/<namespace>/keys/<key_name>')
api.add_resource(api_auth.AuthMetadatasEndpoint,
                 '/auth/namespaces/<namespace>/metadata')
api.add_resource(api_auth.AuthMetadataEndpoint,
                 '/auth/namespaces/<namespace>/metadata/<key>')
api.add_resource(api_auth.AuthNamespaceTrustsEndpoint,
                 '/auth/namespaces/<namespace>/trust')
api.add_resource(api_auth.AuthNamespaceTrustEndpoint,
                 '/auth/namespaces/<namespace>/trust/<external_namespace>')

api.add_resource(api_blob.BlobsEndpoint, '/blobs')
api.add_resource(api_blob.BlobEndpoint, '/blobs/<blob_uuid>')
api.add_resource(api_blob.BlobDataEndpoint, '/blobs/<blob_uuid>/data')
api.add_resource(api_blob.BlobEventsEndpoint, '/blobs/<blob_uuid>/events')
api.add_resource(api_blob.BlobChecksumEndpoint,
                 '/blobs/<blob_uuid>/checksum/<algorithm>')
api.add_resource(api_blob.BlobChecksumsEndpoint, '/blob_checksums/<algorithm>/<hash>')
api.add_resource(api_blob.BlobMetadatasEndpoint, '/blobs/<blob_uuid>/metadata')
api.add_resource(api_blob.BlobMetadataEndpoint, '/blobs/<blob_uuid>/metadata/<key>')

api.add_resource(api_clusteroperation.ClusterOperationEndpoint,
                 '/clusteroperations/<operation_type>/<operation_uuid>')
api.add_resource(api_clusteroperation.ClusterOperationChainEndpoint,
                 '/clusteroperations/<op_uuid>/chain')
api.add_resource(api_clusteroperation.ClusterOperationsEndpoint,
                 '/clusteroperations')

api.add_resource(api_instance.InstancesEndpoint, '/instances')
api.add_resource(api_instance.InstanceEndpoint, '/instances/<instance_ref>')
api.add_resource(api_instance.InstanceEventsEndpoint,
                 '/instances/<instance_ref>/events')
api.add_resource(api_instance.InstanceInterfacesEndpoint,
                 '/instances/<instance_ref>/interfaces')
api.add_resource(api_snapshot.InstanceSnapshotEndpoint,
                 '/instances/<instance_ref>/snapshot')
api.add_resource(api_agentoperation.InstanceAgentOperationsEndpoint,
                 '/instances/<instance_ref>/agentoperations')
api.add_resource(api_instance.InstanceRebootSoftEndpoint,
                 '/instances/<instance_ref>/rebootsoft')
api.add_resource(api_instance.InstanceRebootHardEndpoint,
                 '/instances/<instance_ref>/reboothard')
api.add_resource(api_instance.InstancePowerOffEndpoint,
                 '/instances/<instance_ref>/poweroff')
api.add_resource(api_instance.InstancePowerOnEndpoint,
                 '/instances/<instance_ref>/poweron')
api.add_resource(api_instance.InstancePauseEndpoint,
                 '/instances/<instance_ref>/pause')
api.add_resource(api_instance.InstanceUnpauseEndpoint,
                 '/instances/<instance_ref>/unpause')
api.add_resource(api_instance.InstanceMetadatasEndpoint,
                 '/instances/<instance_ref>/metadata')
api.add_resource(api_instance.InstanceMetadataEndpoint,
                 '/instances/<instance_ref>/metadata/<key>')
api.add_resource(api_instance.InstanceConsoleDataEndpoint,
                 '/instances/<instance_ref>/consoledata')
api.add_resource(api_instance.InstanceVDIConsoleHelperEndpoint,
                 '/instances/<instance_ref>/vdiconsolehelper')
api.add_resource(api_instance.InstanceVDIProxyConsoleHelperEndpoint,
                 '/instances/<instance_ref>/vdiconsoleproxy')
api.add_resource(api_instance.InstanceAgentPutEndpoint,
                 '/instances/<instance_ref>/agent/put')
api.add_resource(api_instance.InstanceAgentGetEndpoint,
                 '/instances/<instance_ref>/agent/get')
api.add_resource(api_instance.InstanceAgentExecuteEndpoint,
                 '/instances/<instance_ref>/agent/execute')
api.add_resource(api_instance.InstanceScreenshotEndpoint,
                 '/instances/<instance_ref>/screenshot')
api.add_resource(api_instance.InstanceOutstandingOperationsEndpoint,
                 '/instances/<instance_ref>/clusteroperations')

api.add_resource(api_interface.InterfaceEndpoint,
                 '/interfaces/<interface_uuid>')
api.add_resource(api_interface.InterfaceFloatEndpoint,
                 '/interfaces/<interface_uuid>/float')
api.add_resource(api_interface.InterfaceDefloatEndpoint,
                 '/interfaces/<interface_uuid>/defloat')
api.add_resource(api_interface.InterfaceMetadatasEndpoint,
                 '/interfaces/<interface_uuid>/metadata')
api.add_resource(api_interface.InterfaceMetadataEndpoint,
                 '/interfaces/<interface_uuid>/metadata/<key>')

api.add_resource(api_label.LabelEndpoint, '/label/<path:label_name>')

api.add_resource(api_network.NetworksEndpoint, '/networks')
api.add_resource(api_network.NetworkEndpoint, '/networks/<network_ref>')
api.add_resource(api_network.NetworkAddressesEndpoint,
                 '/networks/<network_ref>/addresses')
api.add_resource(api_network.NetworkEventsEndpoint,
                 '/networks/<network_ref>/events')
api.add_resource(api_network.NetworkInterfacesEndpoint,
                 '/networks/<network_ref>/interfaces')
api.add_resource(api_network.NetworkMetadatasEndpoint,
                 '/networks/<network_ref>/metadata')
api.add_resource(api_network.NetworkMetadataEndpoint,
                 '/networks/<network_ref>/metadata/<key>')
api.add_resource(api_network.NetworkPingEndpoint,
                 '/networks/<network_ref>/ping/<address>')
api.add_resource(api_network.NetworkRouteAddressEndpoint,
                 '/networks/<network_ref>/route')
api.add_resource(api_network.NetworkUnrouteAddressEndpoint,
                 '/networks/<network_ref>/route/<address>')
api.add_resource(api_network.NetworkDNSAddressEndpoint,
                 '/networks/<network_ref>/dns')
api.add_resource(api_network.NetworkOutstandingOperationsEndpoint,
                 '/networks/<network_ref>/clusteroperations')

api.add_resource(api_node.NodesEndpoint, '/nodes')
api.add_resource(api_node.NodeEndpoint, '/nodes/<node>')
api.add_resource(api_node.NodeEventsEndpoint, '/nodes/<node>/events')
api.add_resource(api_node.NodeProcessMetricsEndpoint, '/nodes/<node>/processmetrics')
api.add_resource(api_node.NodeMetadatasEndpoint, '/nodes/<node>/metadata')
api.add_resource(api_node.NodeMetadataEndpoint, '/nodes/<node>/metadata/<key>')

api.add_resource(api_upload.UploadCreateEndpoint, '/upload')
api.add_resource(api_upload.UploadDataEndpoint, '/upload/<upload_uuid>')
api.add_resource(api_upload.UploadTruncateEndpoint,
                 '/upload/<upload_uuid>/truncate/<offset>')
