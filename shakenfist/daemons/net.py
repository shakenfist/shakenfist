import logging
from logging import handlers as logging_handlers
import setproctitle
import time

from shakenfist import config
from shakenfist import db
from shakenfist import net
from shakenfist import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf net')

    def _maintain_networks(self):
        # Discover what networks are present
        _, _, vxid_to_mac = util.discover_interfaces()

        # Determine what networks we should be on
        host_networks = []
        seen_vxids = []

        if config.parsed.get('NODE_IP') != config.parsed.get('NETWORK_NODE_IP'):
            # For normal nodes, just the ones we have instances for
            for inst in list(db.get_instances(only_node=config.parsed.get('NODE_NAME'))):
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
                    if not inst:
                        db.hard_delete_network_interface(ni['uuid'])
                        LOG.info('Hard deleted stray network interface %s '
                                 'associated with absent instance %s'
                                 % (ni['uuid'], ni['instance_uuid']))
                    elif inst.get('state', 'unknown') in ['deleted', 'error', 'unknown']:
                        db.hard_delete_network_interface(ni['uuid'])
                        LOG.info('Hard deleted stray network interface %s '
                                 'associated with %s instance %s'
                                 % (ni['uuid'], inst.get('state', 'unknown'),
                                    ni['instance_uuid']))

        # Ensure we are on every network we have a host for
        for network in host_networks:
            with db.get_lock('sf/network/%s' % network, ttl=120) as _:
                n = net.from_db(network)
                if not n:
                    continue

                n.create()
                n.ensure_mesh()
                seen_vxids.append(n.vxlan_id)

        # Determine if there are any extra vxids
        extra_vxids = set(vxid_to_mac.keys()) - set(seen_vxids)

        # For now, just log extra vxids
        if extra_vxids:
            LOG.warn('Extra vxlans present! IDs are: %s'
                     % extra_vxids)

            # Determine the network uuids for those vxids
            vxid_to_uuid = {}
            for n in db.get_networks():
                vxid_to_uuid[n['vxid']] = n['uuid']

            for extra in extra_vxids:
                if extra in vxid_to_uuid:
                    with db.get_lock('sf/network/%s' % vxid_to_uuid[extra], ttl=120) as _:
                        n = net.from_db(vxid_to_uuid[extra])
                        n.delete()
                        LOG.info('Extra vxlan %s (network %s) removed.'
                                 % (extra, vxid_to_uuid[extra]))
                else:
                    LOG.error('Extra vxlan %s does not map to any network.'
                              % extra)

        # And record vxids in the database
        db.persist_node_vxid_mapping(
            config.parsed.get('NODE_NAME'), vxid_to_mac)

    def run(self):
        while True:
            time.sleep(30)

            try:
                self._maintain_networks()
            except Exception as e:
                util.ignore_exception('network monitor', e)
