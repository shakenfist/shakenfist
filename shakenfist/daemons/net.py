import ipaddress
import time

from oslo_concurrency import processutils

from shakenfist import baseobject
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import net
from shakenfist.tasks import (DeployNetworkTask,
                              NetworkTask,
                              RemoveDHCPNetworkTask,
                              UpdateDHCPNetworkTask)
from shakenfist import util
from shakenfist import virt


LOG, _ = logutil.setup(__name__)


class Monitor(daemon.Daemon):
    def _maintain_networks(self):
        LOG.info('Maintaining networks')

        # Discover what networks are present
        _, _, vxid_to_mac = util.discover_interfaces()

        # Determine what networks we should be on
        host_networks = []
        seen_vxids = []

        if not util.is_network_node():
            # For normal nodes, just the ones we have instances for
            for inst in virt.Instances([virt.this_node_filter, virt.active_states_filter]):
                for iface in db.get_instance_interfaces(inst.uuid):
                    if not iface['network_uuid'] in host_networks:
                        host_networks.append(iface['network_uuid'])
        else:
            # For network nodes, its all networks
            for n in net.Networks([baseobject.active_states_filter]):
                bad = False
                try:
                    netblock = ipaddress.ip_network(n.netblock)
                    if netblock.num_addresses < 8:
                        bad = True
                except ValueError:
                    bad = True

                if bad:
                    LOG.with_network(n.uuid).error(
                        'Network netblock is invalid, deleting network.')
                    netobj = net.Network.from_db(n.uuid)
                    netobj.delete()
                    continue

                host_networks.append(n.uuid)

                # Network nodes also look for interfaces for absent instances
                # and delete them
                for ni in db.get_network_interfaces(n.uuid):
                    stray = False
                    inst = virt.Instance.from_db(ni['instance_uuid'])
                    if not inst:
                        stray = True
                    else:
                        if inst.state.value in ['deleted', 'error', 'unknown']:
                            stray = True

                    if stray:
                        db.hard_delete_network_interface(ni['uuid'])
                        LOG.with_instance(
                            ni['instance_uuid']).with_networkinterface(
                            ni['uuid']).info('Hard deleted stray network interface')

        # Ensure we are on every network we have a host for
        for network in host_networks:
            try:
                n = net.Network.from_db(network)
                if not n:
                    continue

                seen_vxids.append(n.vxid)

                if time.time() - n.state.update_time < 60:
                    # Network state changed in the last minute, punt for now
                    continue

                if not n.is_okay():
                    LOG.with_network(n).info('Recreating not okay network')
                    if util.is_network_node():
                        n.create_on_network_node()
                    else:
                        n.create_on_hypervisor()

                n.update_dhcp()
                n.ensure_mesh()

            except exceptions.LockException as e:
                LOG.warning(
                    'Failed to acquire lock while maintaining networks: %s' % e)
            except exceptions.DeadNetwork as e:
                LOG.with_field('exception', e).info(
                    'maintain_network attempted on dead network')
            except processutils.ProcessExecutionError as e:
                LOG.error('Network maintenance failure: %s', e)

        # Determine if there are any extra vxids
        extra_vxids = set(vxid_to_mac.keys()) - set(seen_vxids)

        # Delete "deleted" SF networks and log unknown vxlans
        if extra_vxids:
            LOG.with_field('vxids', extra_vxids).warning(
                'Extra vxlans present!')

            # Determine the network uuids for those vxids
            # vxid_to_uuid = {}
            # for n in db.get_networks():
            #     vxid_to_uuid[n['vxid']] = n.uuid

            # for extra in extra_vxids:
            #     if extra in vxid_to_uuid:
            #         with db.get_lock('network', None, vxid_to_uuid[extra],
            #                          ttl=120, op='Network reap VXLAN'):
            #             n = net.Network.from_db(vxid_to_uuid[extra])
            #             n.delete()
            #             LOG.info('Extra vxlan %s (network %s) removed.'
            #                      % (extra, vxid_to_uuid[extra]))
            #     else:
            #         LOG.error('Extra vxlan %s does not map to any network.'
            #                   % extra)

        # And record vxids in the database
        db.persist_node_vxid_mapping(config.NODE_NAME, vxid_to_mac)

    def _process_network_node_workitems(self):
        jobname, workitem = db.dequeue('networknode')
        try:
            if not workitem:
                time.sleep(0.2)
                return

            log_ctx = LOG.with_field('workitem', workitem)
            if not NetworkTask.__subclasscheck__(type(workitem)):
                raise exceptions.UnknownTaskException(
                    'Network workitem was not decoded: %s' % workitem)

            log_ctx = log_ctx.with_network(workitem.network_uuid())
            n = net.Network.from_db(workitem.network_uuid())
            if not n:
                log_ctx.warning('Received work item for non-existent network')
                return

            # NOTE(mikal): there's really nothing stopping us from processing a bunch
            # of these jobs in parallel with a pool of workers, but I am not sure its
            # worth the complexity right now. Are we really going to be changing
            # networks that much?

            # Tasks valid for a network in any state
            if isinstance(workitem, RemoveDHCPNetworkTask):
                n.remove_dhcp()
                db.add_event('network', workitem.network_uuid(),
                             'network node', 'remove dhcp', None, None)
                return

            # Tasks that should not operate on a dead network
            if n.is_dead():
                log_ctx.with_fields({'state': n.state,
                                     'workitem': workitem}).info(
                    'Received work item for a dead network')
                return

            if isinstance(workitem, DeployNetworkTask):
                try:
                    n.create_on_network_node()
                    n.ensure_mesh()
                    n.state = 'created'
                    db.add_event('network', workitem.network_uuid(),
                                 'network node', 'deploy', None, None)
                except exceptions.DeadNetwork as e:
                    log_ctx.with_field('exception', e).warning(
                        'DeployNetworkTask on dead network')

            elif isinstance(workitem, UpdateDHCPNetworkTask):
                try:
                    n.create_on_network_node()
                    n.ensure_mesh()
                    db.add_event('network', workitem.network_uuid(),
                                 'network node', 'update dhcp', None, None)
                except exceptions.DeadNetwork as e:
                    log_ctx.with_field('exception', e).warning(
                        'UpdateDHCPNetworkTask on dead network')

        finally:
            if jobname:
                db.resolve('networknode', jobname)

    def run(self):
        LOG.info('Starting')
        last_management = 0

        while True:
            if util.is_network_node():
                self._process_network_node_workitems()
            else:
                management_age = time.time() - last_management
                time.sleep(max(0, 30 - management_age))

            if time.time() - last_management > 30:
                self._maintain_networks()
                last_management = time.time()
