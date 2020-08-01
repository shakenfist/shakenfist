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

    def run(self):
        while True:
            time.sleep(30)

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

            # Ensure we are on every network we have a host for
            for network in host_networks:
                n = net.from_db(network)
                n.create()
                n.ensure_mesh()
                seen_vxids.append(n.vxlan_id)

            # Determine if there are any extra vxids
            extra_vxids = list(vxid_to_mac.keys())
            for seen in seen_vxids:
                if seen in extra_vxids:
                    extra_vxids.remove(seen)

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
