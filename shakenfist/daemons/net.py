import time

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
            for inst in list(db.get_instances(only_node=config.NODE_NAME)):
                for iface in db.get_instance_interfaces(inst['uuid']):
                    if not iface['network_uuid'] in host_networks:
                        host_networks.append(iface['network_uuid'])
        else:
            # For network nodes, its all networks
            for n in db.get_networks():
                host_networks.append(n['uuid'])

                # Network nodes also look for interfaces for absent instances
                # and delete them
                for ni in db.get_network_interfaces(n['uuid']):
                    inst = db.get_instance(ni['instance_uuid'])
                    if (not inst
                            or inst.get('state', 'unknown') in ['deleted', 'error', 'unknown']):
                        db.hard_delete_network_interface(ni['uuid'])
                        LOG.withInstance(
                            ni['instance_uuid']).withNetworkInterface(
                            ni['uuid']).info('Hard deleted stray network interface')

        # Ensure we are on every network we have a host for
        for network in host_networks:
            try:
                n = net.from_db(network)
                if not n:
                    continue

                seen_vxids.append(n.db_entry['vxid'])

                if time.time() - n.db_entry['state_updated'] < 60:
                    # Network state changed in the last minute, punt for now
                    continue

                if not n.is_okay():
                    LOG.withObj(n).info('Recreating not okay network')
                    n.create()

                n.ensure_mesh()

            except exceptions.LockException as e:
                LOG.warning(
                    'Failed to acquire lock while maintaining networks: %s' % e)
            except exceptions.DeadNetwork as e:
                LOG.withField('exception', e).info(
                    'maintain_network attempted on dead network')

        # Determine if there are any extra vxids
        extra_vxids = set(vxid_to_mac.keys()) - set(seen_vxids)

        # Delete "deleted" SF networks and log unknown vxlans
        if extra_vxids:
            LOG.withField('vxids', extra_vxids).warning(
                'Extra vxlans present!')

            # Determine the network uuids for those vxids
            # vxid_to_uuid = {}
            # for n in db.get_networks():
            #     vxid_to_uuid[n['vxid']] = n['uuid']

            # for extra in extra_vxids:
            #     if extra in vxid_to_uuid:
            #         with db.get_lock('network', None, vxid_to_uuid[extra],
            #                          ttl=120, op='Network reap VXLAN'):
            #             n = net.from_db(vxid_to_uuid[extra])
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

            log_ctx = LOG.withField('workitem', workitem)
            if not NetworkTask.__subclasscheck__(type(workitem)):
                raise exceptions.UnknownTaskException(
                    'Network workitem was not decoded: %s' % workitem)

            n = net.from_db(workitem.network_uuid())
            if not n:
                log_ctx.withNetwork(workitem.network_uuid()).warning(
                    'Received work item for non-existent network')
                return

            # NOTE(mikal): there's really nothing stopping us from processing a bunch
            # of these jobs in parallel with a pool of workers, but I am not sure its
            # worth the complexity right now. Are we really going to be changing
            # networks that much?
            if isinstance(workitem, DeployNetworkTask):
                try:
                    n.create()
                    n.ensure_mesh()
                    db.add_event('network', workitem.network_uuid(),
                                 'network node', 'deploy', None, None)
                except exceptions.DeadNetwork as e:
                    log_ctx.withField('exception', e).warning(
                        'DeployNetworkTask on dead network')

            elif isinstance(workitem, UpdateDHCPNetworkTask):
                try:
                    n.create()
                    n.ensure_mesh()
                    n.update_dhcp()
                    db.add_event('network', workitem.network_uuid(),
                                 'network node', 'update dhcp', None, None)
                except exceptions.DeadNetwork as e:
                    log_ctx.withField('exception', e).warning(
                        'UpdateDHCPNetworkTask on dead network')

            elif isinstance(workitem, RemoveDHCPNetworkTask):
                n.remove_dhcp()
                db.add_event('network', workitem.network_uuid(),
                             'network node', 'remove dhcp', None, None)

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
