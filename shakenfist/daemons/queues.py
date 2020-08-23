import copy
import logging
import multiprocessing
import os
import setproctitle
import time

from shakenfist import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import etcd
from shakenfist import images
from shakenfist import net
from shakenfist import util
from shakenfist import virt

LOG = logging.getLogger(__name__)


def handle(jobname, workitem):
    LOG.info('Worker for workitem %s has pid %d' % (jobname, os.getpid()))
    setproctitle.setproctitle(
        '%s-%s' % (daemon.process_name('triggers'), jobname))

    try:
        for task in workitem.get('tasks', []):
            if task.get('type').startswith('instance_') and not workitem.get('instance_uuid'):
                LOG.error('Instance task lacks instance uuid: %s' % workitem)

            if task.get('type') == 'image_fetch':
                image_fetch(task.get('url'))

            if task.get('type') == 'instance_preflight':
                instance_preflight(workitem.get('instance_uuid'))

            if task.get('type') == 'instance_start':
                instance_start(workitem.get('instance_uuid'),
                               workitem.get('network'))
                db.update_instance_state(
                    workitem.get('instance_uuid'), 'created')

            if task.get('type') == 'instance_delete':
                instance_delete(workitem.get('instance_uuid'))
                db.update_instance_state(
                    workitem.get('instance_uuid'),
                    task.get('next_state', 'unknown'))

        LOG.info('Worker for workitem %s has pid %d, complete'
                 % (jobname, os.getpid()))

    finally:
        db.resolve(config.parsed.get('NODE_NAME'), jobname)


def image_fetch(url):
    try:
        images.get_image(url, [], url, timeout=images.IMAGE_FETCH_LOCK_TIMEOUT)
    except etcd.LockException:
        pass


def instance_preflight(instance_uuid):
    # TODO(mikal): preflight with retries etc
    pass


def instance_start(instance_uuid, network):
    with db.get_lock('instance', None, instance_uuid, ttl=900) as lock:
        db.add_event('instance', instance_uuid,
                     'queued', 'create', None, None)
        instance = virt.from_db(instance_uuid)

        # Collect the networks
        nets = {}
        for netdesc in network:
            if netdesc['network_uuid'] not in nets:
                n = net.from_db(netdesc['network_uuid'])
                if not n:
                    db.enqueue_delete(
                        config.parsed.get('NODE_NAME'), instance_uuid, 'error')
                    return

                nets[netdesc['network_uuid']] = n

        # Create the networks
        with util.RecordedOperation('ensure networks exist', instance) as _:
            for network_uuid in nets:
                n = nets[network_uuid]
                n.create()
                n.ensure_mesh()
                n.update_dhcp()

        # Now we can start the isntance
        libvirt = util.get_libvirt()
        try:
            with util.RecordedOperation('instance creation',
                                        instance) as _:
                instance.create(lock=lock)

        except libvirt.libvirtError as e:
            code = e.get_error_code()
            if code in (libvirt.VIR_ERR_CONFIG_UNSUPPORTED,
                        libvirt.VIR_ERR_XML_ERROR):
                db.enqueue_delete(
                    config.parsed.get('NODE_NAME'), instance_uuid, 'error')
                return

        for iface in db.get_instance_interfaces(instance.db_entry['uuid']):
            db.update_network_interface_state(iface['uuid'], 'created')


def instance_delete(instance_uuid):
    with db.get_lock('instance', None, instance_uuid) as _:
        db.add_event('instance', instance_uuid,
                     'queued', 'delete', None, None)

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

                jobname, workitem = db.dequeue(config.parsed.get('NODE_NAME'))
                if not workitem:
                    time.sleep(0.2)
                    continue

                p = multiprocessing.Process(
                    target=handle, args=(jobname, workitem,),
                    name='%s-worker' % daemon.process_name('queues'))
                p.start()
                workers.append(p)

            except Exception as e:
                util.ignore_exception(daemon.process_name('queues'), e)
