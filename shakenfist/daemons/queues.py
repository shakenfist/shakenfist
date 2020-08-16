import copy
import logging
import multiprocessing
import os
import time

from shakenfist import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import net
from shakenfist import util
from shakenfist import virt

LOG = logging.getLogger(__name__)


def handle(workitem):
    LOG.info('Worker for item %s has pid %d' % (workitem, os.getpid()))
    for task in workitem.get('tasks', []):
        if task.get('type').startswith('instance_') and not workitem.get('instance_uuid'):
            LOG.error('Instance task lacks instance uuid: %s' % workitem)

        if task.get('type') == 'instance_delete':
            instance_delete(workitem.get('instance_uuid'))
            db.update_instance_state(
                workitem.get('instance_uuid'),
                task.get('next_state', 'unknown'))

    LOG.info('Worker for item %s has pid %d, complete'
             % (workitem, os.getpid()))


def instance_delete(instance_uuid):
    with db.get_lock('/sf/instance/%s' % instance_uuid) as _:
        db.add_event('instance', instance_uuid,
                     'api', 'delete', None, None)

        # Create list of networks used by instance
        instance_networks = []
        for iface in list(db.get_instance_interfaces(instance_uuid)):
            if not iface['network_uuid'] in instance_networks:
                instance_networks.append(iface['network_uuid'])

        # Create list of networks used by all other instances
        host_networks = []
        for inst in list(
                db.get_instances(only_node=config.parsed.get('NODE_NAME'))):
            if not inst['uuid'] == instance_uuid:
                for iface in db.get_instance_interfaces(inst['uuid']):
                    if not iface['network_uuid'] in host_networks:
                        host_networks.append(iface['network_uuid'])

        instance_from_db_virt = virt.from_db(instance_uuid)
        if instance_from_db_virt:
            instance_from_db_virt.delete()

        # Check each network used by the deleted instance
        for network in instance_networks:
            n = net.from_db(network)
            if n:
                # If network used by another instance, only update
                if network in host_networks:
                    with util.RecordedOperation('deallocate ip address',
                                                instance_from_db_virt) as _:
                        n.update_dhcp()
                else:
                    # Network not used by any other instance therefore delete
                    with util.RecordedOperation('remove network', n) as _:
                        n.delete()


class Monitor(daemon.Daemon):
    def run(self):
        workers = []
        LOG.info('Starting')

        while True:
            try:
                for w in copy.copy(workers):
                    if not w.is_alive():
                        w.join(1)
                        workers.remove(w)

                workitem = db.dequeue(config.parsed.get('NODE_NAME'))
                if not workitem:
                    time.sleep(0.2)
                    continue

                p = multiprocessing.Process(
                    target=handle, args=(workitem,),
                    name='%s-worker' % daemon.process_name('queues'))
                p.start()
                workers.append(p)

            except Exception as e:
                util.ignore_exception(daemon.process_name('queues'), e)
