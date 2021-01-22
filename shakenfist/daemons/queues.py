import copy
import multiprocessing
import re
import requests
import setproctitle
import time

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import exceptions
from shakenfist.images import Image
from shakenfist.ipmanager import IPManager
from shakenfist import logutil
from shakenfist import net
from shakenfist import scheduler
from shakenfist import util
from shakenfist import virt
from shakenfist.tasks import (QueueTask,
                              DeleteInstanceTask,
                              FetchImageTask,
                              InstanceTask,
                              PreflightInstanceTask,
                              StartInstanceTask,
                              )


LOG, _ = logutil.setup(__name__)


def handle(jobname, workitem):
    log = LOG.with_field('workitem', jobname)
    log.info('Processing workitem')

    setproctitle.setproctitle(
        '%s-%s' % (daemon.process_name('queues'), jobname))

    instance = None
    task = None
    try:
        for task in workitem.get('tasks', []):
            if not QueueTask.__subclasscheck__(type(task)):
                raise exceptions.UnknownTaskException(
                    'Task was not decoded: %s' % task)

            if InstanceTask.__subclasscheck__(type(task)):
                instance = virt.Instance.from_db(task.instance_uuid())
                if not instance:
                    raise exceptions.InstanceNotInDBException(
                        task.instance_uuid())

            if isinstance(task, FetchImageTask):
                instance = virt.Instance.from_db(task.instance_uuid())

            if instance:
                log_i = log.with_instance(instance)
            else:
                log_i = log

            log_i.with_field('task_name', task.name()).info('Starting task')

            # TODO(andy) Should network events also come through here eventually?
            # Then this can be generalised to record events on networks/instances

            # TODO(andy) This event should be recorded when it is recorded as
            # dequeued in the DB. Currently it's reporting action on the item
            # and calling it 'dequeue'.

            if instance:
                # TODO(andy) move to QueueTask
                db.add_event('instance', instance.uuid, task.pretty_task_name(),
                             'dequeued', None, 'Work item %s' % jobname)

            if isinstance(task, FetchImageTask):
                image_fetch(task.url(), instance)

            elif isinstance(task, PreflightInstanceTask):
                redirect_to = instance_preflight(instance, task.network())
                if redirect_to:
                    log_i.info('Redirecting instance start to %s'
                               % redirect_to)
                    db.enqueue(redirect_to, workitem)
                    return

            elif isinstance(task, StartInstanceTask):
                instance_start(instance, task.network())
                db.enqueue('%s-metrics' % config.NODE_NAME, {})

            elif isinstance(task, DeleteInstanceTask):
                try:
                    instance_delete(instance)
                    db.enqueue('%s-metrics' % config.NODE_NAME, {})
                except Exception as e:
                    util.ignore_exception(daemon.process_name('queues'), e)

            else:
                log_i.with_field('task', task).error(
                    'Unhandled task - dropped')

            log_i.info('Task complete')

    except exceptions.ImageFetchTaskFailedException as e:
        # Usually caused by external issue and not an application error
        log.info('Fetch Image Error: %s', e)
        if instance:
            instance.enqueue_delete_due_error('Failed queue task: %s' % e)

    except Exception as e:
        # Logging ignored exception - this should be investigated
        util.ignore_exception(daemon.process_name('queues'), e)
        if instance:
            instance.enqueue_delete_due_error('Failed queue task: %s' % e)

    finally:
        db.resolve(config.NODE_NAME, jobname)
        if instance:
            instance.add_event('tasks complete', 'dequeued',
                               msg='Work item %s' % jobname)
        log.info('Completed workitem')


def image_fetch(url, instance):
    try:
        # TODO(andy): Wait up to 15 mins for another queue process to download
        # the required image. This will be changed to queue on a
        # "waiting_image_fetch" queue but this works now.
        with db.get_lock('image', config.NODE_NAME, Image.calc_unique_ref(url),
                         timeout=15*60, op='Image fetch') as lock:
            # Note that the image might already exist in the database as the API
            # creates a records so that the image is included in listings before
            # it is fetched. However, the new() call here handles that case and
            # will just return the previous entry if one exists.
            img = Image.new(url)
            img.get([lock], instance)
            db.add_event('image', url, 'fetch', None, None, 'success')

    except (exceptions.HTTPError, requests.exceptions.RequestException) as e:
        LOG.with_field('image', url).info('Failed to fetch image')

        # Clean common problems to store in events
        msg = str(e)
        re_conn_err = re.compile(r'.*NewConnectionError\(\'\<.*\>: (.*)\'')
        m = re_conn_err.match(msg)
        if m:
            msg = m.group(1)
        db.add_event('image', url, 'fetch', None, None, 'Error: '+msg)

        raise exceptions.ImageFetchTaskFailedException(
            'Failed to fetch image: %s Exception: %s' % (url, e))


def instance_preflight(instance, network):
    instance.state = 'preflight'

    # Try to place on this node
    s = scheduler.Scheduler()
    try:
        s.place_instance(instance, network, candidates=[config.NODE_NAME])
        return None

    except exceptions.LowResourceException as e:
        instance.add_event('schedule', 'retry', None,
                           'insufficient resources: ' + str(e))

    # Unsuccessful placement, check if reached placement attempt limit
    db_placement = instance.placement
    if db_placement['placement_attempts'] > 3:
        raise exceptions.AbortInstanceStartException(
            'Too many start attempts')

    # Try placing on another node
    try:
        if instance.requested_placement:
            # TODO(andy): Ask Mikal why this is not the current node?
            candidates = [instance.requested_placement]
        else:
            candidates = []
            for node in s.metrics.keys():
                if node != config.NODE_NAME:
                    candidates.append(node)

        candidates = s.place_instance(instance, network,
                                      candidates=candidates)
        instance.place_instance(candidates[0])
        return candidates[0]

    except exceptions.LowResourceException as e:
        instance.add_event('schedule', 'failed', None,
                           'insufficient resources: ' + str(e))
        # This raise implies delete above
        raise exceptions.AbortInstanceStartException(
            'Unable to find suitable node')


def instance_start(instance, network):
    with instance.get_lock(ttl=900, op='Instance start') as lock:
        # Ensure networks are connected to this node
        nets = {}
        for netdesc in network:
            if netdesc['network_uuid'] not in nets:
                n = net.Network.from_db(netdesc['network_uuid'])
                if not n:
                    instance.enqueue_delete_due_error(
                        'missing network: %s' % netdesc['network_uuid'])
                    return

                if n.state.value != 'created':
                    instance.enqueue_delete_due_error(
                        'network is not active: %s' % n.uuid)
                    return

                n.create_on_hypervisor()
                n.ensure_mesh()
                n.update_dhcp()

        # Allocate console and VDI ports
        instance.allocate_instance_ports()

        # Now we can start the instance
        libvirt = util.get_libvirt()
        try:
            with util.RecordedOperation('instance creation',
                                        instance):
                instance.create(lock=lock)

        except libvirt.libvirtError as e:
            code = e.get_error_code()
            if code in (libvirt.VIR_ERR_CONFIG_UNSUPPORTED,
                        libvirt.VIR_ERR_XML_ERROR):
                instance.enqueue_delete_due_error(
                    'instance failed to start: %s' % e)
                return

        for iface in db.get_instance_interfaces(instance.uuid):
            db.update_network_interface_state(iface['uuid'], 'created')


def instance_delete(instance):
    with instance.get_lock(op='Instance delete'):
        db.add_event('instance', instance.uuid, 'queued', 'delete', None, None)

        # Create list of networks used by instance
        instance_networks = []
        for iface in list(db.get_instance_interfaces(instance.uuid)):
            if not iface['network_uuid'] in instance_networks:
                instance_networks.append(iface['network_uuid'])

        # Create list of networks used by all other instances
        host_networks = []
        for inst in virt.Instances([virt.this_node_filter,
                                    virt.active_states_filter]):
            if not inst.uuid == instance.uuid:
                for iface in db.get_instance_interfaces(inst.uuid):
                    if not iface['network_uuid'] in host_networks:
                        host_networks.append(iface['network_uuid'])

        instance.delete()

        # Delete the instance's interfaces
        with util.RecordedOperation('release network addresses', instance):
            for ni in db.get_instance_interfaces(instance.uuid):
                db.update_network_interface_state(ni['uuid'], 'deleted')
                with db.get_lock('ipmanager', None, ni['network_uuid'],
                                 ttl=120, op='Instance delete'):
                    ipm = IPManager.from_db(ni['network_uuid'])
                    ipm.release(ni['ipv4'])
                    ipm.persist()

        # Check each network used by the deleted instance
        for network in instance_networks:
            n = net.Network.from_db(network)
            if n:
                # If network used by another instance, only update
                if network in host_networks:
                    with util.RecordedOperation('deallocate ip address',
                                                instance):
                        n.update_dhcp()
                else:
                    # Network not used by any other instance therefore delete
                    with util.RecordedOperation('remove network from node', n):
                        n.delete_on_node_with_lock()
        return instance


class Monitor(daemon.Daemon):
    def run(self):
        workers = []
        LOG.info('Starting Queues')

        libvirt = util.get_libvirt()
        conn = libvirt.open('qemu:///system')
        present_cpus, _, _ = conn.getCPUMap()

        while True:
            try:
                for w in copy.copy(workers):
                    if not w.is_alive():
                        w.join(1)
                        workers.remove(w)

                if len(workers) < present_cpus / 2:
                    jobname, workitem = db.dequeue(config.NODE_NAME)
                else:
                    workitem = None

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
