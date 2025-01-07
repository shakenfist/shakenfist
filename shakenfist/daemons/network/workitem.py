import flask
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import network
from shakenfist.networkinterface import NetworkInterface
from shakenfist.tasks import DefloatNetworkInterfaceTask
from shakenfist.tasks import DeployNetworkTask
from shakenfist.tasks import DestroyNetworkTask
from shakenfist.tasks import FloatNetworkInterfaceTask
from shakenfist.tasks import NetworkInterfaceTask
from shakenfist.tasks import NetworkTask
from shakenfist.tasks import RemoveDHCPLeaseNetworkTask
from shakenfist.tasks import RemoveDnsMasqNetworkTask
from shakenfist.tasks import RemoveNATNetworkTask
from shakenfist.tasks import RouteAddressTask
from shakenfist.tasks import UnrouteAddressTask
from shakenfist.tasks import UpdateDnsMasqNetworkTask
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


class Job(util_concurrency.Job):
    def __init__(self, name):
        super().__init__()
        self.name = name

        self.abort_path = f'/run/sf/net-{name}.abort'
        daemon.clear_abort_path(self.abort_path)

    def execute(self):
        LOG.info('Starting network worker')
        was_previously_idle = False

        while daemon.check_abort_path(self.abort_path):
            jobname_workitem = etcd.dequeue('networknode')
            if not jobname_workitem:
                if not was_previously_idle:
                    util_concurrency.set_thread_name('idle')
                    LOG.debug('This network thread is now idle')
                    was_previously_idle = True
                time.sleep(0.2)

            else:
                jobname, workitem = jobname_workitem
                util_concurrency.set_thread_name(jobname)
                LOG.debug(
                    f'This network thread is now processing job {jobname}')

                # Tasks should log with the request id of the API request that
                # caused them, if there was in fact one.
                request_id = workitem.request_id()
                try:
                    if request_id:
                        flask.request.environ['REQUEST_ID'] = request_id
                    else:
                        if 'REQUEST_ID' in flask.request.environ:
                            del flask.request.environ['REQUEST_ID']
                except RuntimeError:
                    ...

                try:
                    log_ctx = LOG.with_fields({'workitem': workitem})
                    log_ctx.info('Starting work item')

                    if NetworkTask.__subclasscheck__(type(workitem)):
                        self._process_network_workitem(log_ctx, workitem)
                    elif NetworkInterfaceTask.__subclasscheck__(type(workitem)):
                        self._process_networkinterface_workitem(
                            log_ctx, workitem)
                    else:
                        raise exceptions.UnknownTaskException(
                            'Network workitem was not decoded: %s' % workitem)

                finally:
                    etcd.resolve('networknode', jobname)

    def _process_network_workitem(self, log_ctx, workitem):
        log_ctx = log_ctx.with_fields({'network': workitem.network_uuid()})
        n = network.Network.from_db(workitem.network_uuid())
        if not n:
            log_ctx.warning('Received work item for non-existent network')
            return

        # NOTE(mikal): there's really nothing stopping us from processing a bunch
        # of these jobs in parallel with a pool of workers, but I am not sure its
        # worth the complexity right now. Are we really going to be changing
        # networks that much?

        #
        # Tasks valid for a network in ANY STATE
        #
        if isinstance(workitem, RemoveDnsMasqNetworkTask):
            n.remove_dnsmasq()
            return

        if isinstance(workitem, RemoveNATNetworkTask):
            n.remove_nat()
            return

        if isinstance(workitem, UnrouteAddressTask):
            n.unroute_address(workitem.ipv4())

        #
        # Tasks that should NOT operate on a DEAD network
        #
        if n.is_dead() and n.state.value != network.Network.STATE_DELETE_WAIT:
            log_ctx.with_fields({'state': n.state,
                                 'workitem': workitem}).info(
                'Received work item for a dead network and not delete_wait')
            return

        if isinstance(workitem, DestroyNetworkTask):
            if n.networkinterfaces:
                log_ctx.with_fields(
                    {'networkinterfaces': n.networkinterfaces}).info(
                    'DestroyNetworkTask for network with interfaces, deferring.')
                etcd.enqueue('networknode', workitem, delay=60)
                return

            try:
                n.delete_on_network_node()
            except exceptions.DeadNetwork as e:
                log_ctx.with_fields({'exception': e}).warning(
                    'DestroyNetworkTask on dead network')
            return

        #
        # Tasks that should NOT operate on a DEAD or DELETE_WAIT network
        #
        if n.is_dead():
            log_ctx.with_fields({'state': n.state,
                                 'workitem': workitem}).info(
                'Received work item for a dead network')
            return

        try:
            if isinstance(workitem, DeployNetworkTask):
                n.create_on_network_node()
                n.ensure_mesh()

            elif isinstance(workitem, UpdateDnsMasqNetworkTask):
                n.create_on_network_node()
                n.ensure_mesh()

            elif isinstance(workitem, RemoveDHCPLeaseNetworkTask):
                n.remove_dhcp_lease(workitem.ipv4(), workitem.macaddr())

            elif isinstance(workitem, RouteAddressTask):
                n.route_address(workitem.ipv4())

        except exceptions.DeadNetwork as e:
            log_ctx.with_fields({'exception': e}).warning(
                'Network task on dead network')

    def _process_networkinterface_workitem(self, log_ctx, workitem):
        log_ctx = log_ctx.with_fields({
            'networkinterface': workitem.interface_uuid()})
        n = network.Network.from_db(workitem.network_uuid())
        if not n:
            log_ctx.warning('Received work item for non-existent network')
            return

        ni = NetworkInterface.from_db(workitem.interface_uuid())
        if not ni:
            log_ctx.warning(
                'Received work item for non-existent network interface')
            return

        # Tasks that should not operate on a dead or delete waiting network
        if n.is_dead() and n.state.value != network.Network.STATE_DELETE_WAIT:
            log_ctx.with_fields({'state': n.state,
                                 'workitem': workitem}).info(
                'Received work item for a completely dead network')
            return

        if isinstance(workitem, DefloatNetworkInterfaceTask):
            n.remove_floating_ip(
                workitem.floating(), ni.ipv4,
                [ni, ('instance', ni.instance_uuid)])
            return

        # Tasks that should not operate on a dead network
        if n.is_dead():
            log_ctx.with_fields({'state': n.state,
                                 'workitem': workitem}).info(
                'Received work item for a dead network')
            return

        if isinstance(workitem, FloatNetworkInterfaceTask):
            floating = ni.floating.get('floating_address')
            if not floating:
                log_ctx.warning(
                    'Not floating an interface with no floating address')
            else:
                n.add_floating_ip(
                    floating, ni.ipv4, [ni, ('instance', ni.instance_uuid)])
            return
