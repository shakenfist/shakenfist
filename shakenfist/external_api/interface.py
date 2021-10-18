
from flask_jwt_extended import jwt_required

from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist.external_api import (
    base as api_base,
    util as api_util)
from shakenfist import logutil
from shakenfist import net
from shakenfist.tasks import (
    DefloatNetworkInterfaceTask,
    FloatNetworkInterfaceTask)


LOG, HANDLER = logutil.setup(__name__)
daemon.set_log_level(LOG, 'api')


class InterfaceEndpoint(api_base.Resource):
    @jwt_required
    @api_base.redirect_to_network_node
    def get(self, interface_uuid=None):
        ni, _, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err
        return ni.external_view()


class InterfaceFloatEndpoint(api_base.Resource):
    @jwt_required
    def post(self, interface_uuid=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err

        err = api_util.assign_floating_ip(ni)
        if err:
            return err

        etcd.enqueue('networknode',
                     FloatNetworkInterfaceTask(n.uuid, interface_uuid))


class InterfaceDefloatEndpoint(api_base.Resource):
    @jwt_required
    def post(self, interface_uuid=None):
        ni, n, err = api_util.safe_get_network_interface(interface_uuid)
        if err:
            return err

        float_net = net.Network.from_db('floating')
        if not float_net:
            return api_base.error(404, 'floating network not found')

        # Address is freed as part of the job, so code is "unbalanced" compared
        # to above for reasons.
        etcd.enqueue('networknode',
                     DefloatNetworkInterfaceTask(n.uuid, interface_uuid))
