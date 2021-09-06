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

import flask
from flask_jwt_extended import JWTManager
import flask_restful

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.external_api import (
    admin as api_admin,
    auth as api_auth,
    base as api_base,
    blob as api_blob,
    image as api_image,
    interface as api_interface,
    instance as api_instance,
    label as api_label,
    network as api_network,
    node as api_node,
    snapshot as api_snapshot)
from shakenfist import logutil


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


app = flask.Flask(__name__)
api = flask_restful.Api(app, catch_all_404s=False)
app.config['JWT_SECRET_KEY'] = config.AUTH_SECRET_SEED.get_secret_value()
jwt = JWTManager(app)

# Use our handler to get SF log format (instead of gunicorn's handlers)
app.logger.handlers = [HANDLER]


@app.before_request
def log_request_info():
    LOG.debug(
        'API request headers:\n' +
        ''.join(['    %s: %s\n' % (h, v) for h, v in flask.request.headers]) +
        'API request body: %s' % flask.request.get_data())


class Root(api_base.Resource):
    def get(self):
        resp = flask.Response(
            'Shaken Fist REST API service',
            mimetype='text/plain')
        resp.status_code = 200
        return resp


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

api.add_resource(api_blob.BlobEndpoint, '/blob/<blob_uuid>')

api.add_resource(api_instance.InstancesEndpoint, '/instances')
api.add_resource(api_instance.InstanceEndpoint, '/instances/<instance_uuid>')
api.add_resource(api_instance.InstanceEventsEndpoint,
                 '/instances/<instance_uuid>/events')
api.add_resource(api_instance.InstanceInterfacesEndpoint,
                 '/instances/<instance_uuid>/interfaces')
api.add_resource(api_snapshot.InstanceSnapshotEndpoint,
                 '/instances/<instance_uuid>/snapshot')
api.add_resource(api_instance.InstanceRebootSoftEndpoint,
                 '/instances/<instance_uuid>/rebootsoft')
api.add_resource(api_instance.InstanceRebootHardEndpoint,
                 '/instances/<instance_uuid>/reboothard')
api.add_resource(api_instance.InstancePowerOffEndpoint,
                 '/instances/<instance_uuid>/poweroff')
api.add_resource(api_instance.InstancePowerOnEndpoint,
                 '/instances/<instance_uuid>/poweron')
api.add_resource(api_instance.InstancePauseEndpoint,
                 '/instances/<instance_uuid>/pause')
api.add_resource(api_instance.InstanceUnpauseEndpoint,
                 '/instances/<instance_uuid>/unpause')
api.add_resource(api_instance.InstanceMetadatasEndpoint,
                 '/instances/<instance_uuid>/metadata')
api.add_resource(api_instance.InstanceMetadataEndpoint,
                 '/instances/<instance_uuid>/metadata/<key>')
api.add_resource(api_instance.InstanceConsoleDataEndpoint,
                 '/instances/<instance_uuid>/consoledata',
                 defaults={'length': 10240})

api.add_resource(api_interface.InterfaceEndpoint,
                 '/interfaces/<interface_uuid>')
api.add_resource(api_interface.InterfaceFloatEndpoint,
                 '/interfaces/<interface_uuid>/float')
api.add_resource(api_interface.InterfaceDefloatEndpoint,
                 '/interfaces/<interface_uuid>/defloat')

api.add_resource(api_image.ImagesEndpoint, '/images')
api.add_resource(api_image.ImageEventsEndpoint, '/images/events')

api.add_resource(api_label.LabelEndpoint, '/label/<label_name>')

api.add_resource(api_network.NetworksEndpoint, '/networks')
api.add_resource(api_network.NetworkEndpoint, '/networks/<network_uuid>')
api.add_resource(api_network.NetworkEventsEndpoint,
                 '/networks/<network_uuid>/events')
api.add_resource(api_network.NetworkInterfacesEndpoint,
                 '/networks/<network_uuid>/interfaces')
api.add_resource(api_network.NetworkMetadatasEndpoint,
                 '/networks/<network_uuid>/metadata')
api.add_resource(api_network.NetworkMetadataEndpoint,
                 '/networks/<network_uuid>/metadata/<key>')
api.add_resource(api_network.NetworkPingEndpoint,
                 '/networks/<network_uuid>/ping/<address>')

api.add_resource(api_node.NodesEndpoint, '/nodes')
