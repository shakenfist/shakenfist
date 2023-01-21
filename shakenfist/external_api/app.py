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
#     SHAKENFIST_ETCD_HOST=localhost flask --app shakenfist.external_api.app:app --debug run

import flask
from flask_jwt_extended import JWTManager
from flask_request_id import RequestID
import flask_restful
import flasgger
from pbr.version import VersionInfo
from shakenfist_utilities import api as sf_api, logs

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist.external_api import (
    admin as api_admin,
    auth as api_auth,
    blob as api_blob,
    artifact as api_artifact,
    interface as api_interface,
    instance as api_instance,
    label as api_label,
    network as api_network,
    node as api_node,
    snapshot as api_snapshot,
    upload as api_upload)


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


app = flask.Flask(__name__)
RequestID(app)
api = flask_restful.Api(app, catch_all_404s=False)

app.config['JWT_SECRET_KEY'] = config.AUTH_SECRET_SEED.get_secret_value()
jwt = JWTManager(app)

swagger = flasgger.Swagger(app, template={
    'swagger': '2.0',
    'info': {
        'title': 'Shaken Fist REST API',
        'description': 'Shaken Fist cluster control via REST API',
        'version': VersionInfo('shakenfist').version_string(),
    },
    'host': config.API_ADVERTISED_HOST,
    'basePath': config.API_ADVERTISED_BASE_PATH,
    'schemes': config.API_ADVERTISED_HTTP_SCHEMES
})

# Use our handler to get SF log format (instead of gunicorn's handlers)
app.logger.handlers = [HANDLER]


@app.before_request
def log_request_info():
    LOG.with_fields(
        {
            'request-id': flask.request.environ.get('FLASK_REQUEST_ID', 'none'),
            'headers': flask.request.headers,
            'body': flask.request.get_data()
        }).debug('API request received')


@app.after_request
def log_response_info(response):
    # Unfortunately the response body is too long to log here, but may be
    # obtained with flask.response.get_data() if we ever want to grow a more
    # complete tracing system.
    log = LOG.with_fields(
        {
            'request-id': flask.request.environ.get('FLASK_REQUEST_ID', 'none'),
            'headers': response.headers
        })
    if config.EXCESSIVE_ETCD_CACHE_LOGGING:
        log.with_fields(etcd.get_statistics()).info('API response sent')
    else:
        log.debug('API response sent')
    etcd.reset_statistics()
    return response


class Root(sf_api.Resource):
    def get(self):
        resp = flask.Response(
            ('<html><head><title>Shaken Fist REST API service</title></head>'
             '<body><h1>Shaken Fist REST API service</h1>'
             '<p>You might be interested in the <a href="/apidocs">apidocs</a>.</p>'
             '<p>Machine searchable API capabilities:</p><ul>'
             '<li>blob-search-by-hash</li>'
             '<li>spice-vdi-console</li>'
             '</ul></p></body></html>'),
            mimetype='text/html')
        resp.status_code = 200
        return resp


# TODO(mikal): we are inconsistent in this interface. Elsewhere the object type
# is always singular, here its a mix. We should move all of these to the
# singular form for consistency.
api.add_resource(Root, '/')

api.add_resource(api_admin.AdminLocksEndpoint, '/admin/locks')

api.add_resource(api_auth.AuthEndpoint, '/auth')
api.add_resource(api_auth.AuthNamespacesEndpoint, '/auth/namespaces')
api.add_resource(api_auth.AuthNamespaceEndpoint,
                 '/auth/namespaces/<namespace>')
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
api.add_resource(api_blob.BlobChecksumsEndpoint,
                 '/blob_checksums/sha512/<hash>')

api.add_resource(api_instance.InstancesEndpoint, '/instances')
api.add_resource(api_instance.InstanceEndpoint, '/instances/<instance_ref>')
api.add_resource(api_instance.InstanceEventsEndpoint,
                 '/instances/<instance_ref>/events')
api.add_resource(api_instance.InstanceInterfacesEndpoint,
                 '/instances/<instance_ref>/interfaces')
api.add_resource(api_snapshot.InstanceSnapshotEndpoint,
                 '/instances/<instance_ref>/snapshot')
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

api.add_resource(api_interface.InterfaceEndpoint,
                 '/interfaces/<interface_uuid>')
api.add_resource(api_interface.InterfaceFloatEndpoint,
                 '/interfaces/<interface_uuid>/float')
api.add_resource(api_interface.InterfaceDefloatEndpoint,
                 '/interfaces/<interface_uuid>/defloat')

api.add_resource(api_artifact.ArtifactEndpoint, '/artifacts/<artifact_ref>')
api.add_resource(api_artifact.ArtifactsEndpoint, '/artifacts')
api.add_resource(api_artifact.ArtifactUploadEndpoint,
                 '/artifacts/upload/<artifact_name>')
api.add_resource(api_artifact.ArtifactEventsEndpoint,
                 '/artifacts/<artifact_ref>/events')
api.add_resource(api_artifact.ArtifactVersionsEndpoint,
                 '/artifacts/<artifact_ref>/versions')
api.add_resource(api_artifact.ArtifactVersionEndpoint,
                 '/artifacts/<artifact_ref>/versions/<version_id>')
api.add_resource(api_artifact.ArtifactShareEndpoint,
                 '/artifacts/<artifact_ref>/share')
api.add_resource(api_artifact.ArtifactUnshareEndpoint,
                 '/artifacts/<artifact_ref>/unshare')

api.add_resource(api_label.LabelEndpoint, '/label/<path:label_name>')

api.add_resource(api_network.NetworksEndpoint, '/networks')
api.add_resource(api_network.NetworkEndpoint, '/networks/<network_ref>')
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

api.add_resource(api_node.NodesEndpoint, '/nodes')
api.add_resource(api_node.NodeEndpoint, '/nodes/<node>')
api.add_resource(api_node.NodeEventsEndpoint,
                 '/nodes/<node>/events')

api.add_resource(api_upload.UploadCreateEndpoint, '/upload')
api.add_resource(api_upload.UploadDataEndpoint, '/upload/<upload_uuid>')
api.add_resource(api_upload.UploadTruncateEndpoint,
                 '/upload/<upload_uuid>/truncate/<offset>')
