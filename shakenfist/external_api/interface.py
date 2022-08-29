
from flask_jwt_extended import jwt_required
from shakenfist_utilities import api as sf_api, logs

from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist.tasks import (
    DefloatNetworkInterfaceTask,
    FloatNetworkInterfaceTask)


LOG, HANDLER = logs.setup(__name__)
daemon.set_log_level(LOG, 'api')


class InterfaceEndpoint(sf_api.Resource):
    @jwt_required()
    @api_base.redirect_to_network_node
    def get(self, interface_uuid=None):
        ni, _, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err
        return ni.external_view()


class InterfaceFloatEndpoint(sf_api.Resource):
    @jwt_required()
    def post(self, interface_uuid=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err

        try:
            api_util.assign_floating_ip(ni)
        except exceptions.CongestedNetwork as e:
            return sf_api.error(507, str(e))

        etcd.enqueue('networknode',
                     FloatNetworkInterfaceTask(n.uuid, interface_uuid))


class InterfaceDefloatEndpoint(sf_api.Resource):
    @jwt_required()
    def post(self, interface_uuid=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err

        # Address is freed as part of the job, so code is "unbalanced" compared
        # to above for reasons.
        etcd.enqueue('networknode',
                     DefloatNetworkInterfaceTask(n.uuid, interface_uuid))
