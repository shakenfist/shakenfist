import copy
import multiprocessing
import requests
import setproctitle
import time

from shakenfist.artifact import Artifact
from shakenfist import blob
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import db
from shakenfist import exceptions
from shakenfist.images import Image
from shakenfist import instance
from shakenfist import logutil
from shakenfist.tasks import (QueueTask,
                              DeleteInstanceTask,
                              FetchImageTask,
                              InstanceTask,
                              PreflightInstanceTask,
                              StartInstanceTask,
                              FloatNetworkInterfaceTask,
                              SnapshotTask)
from shakenfist import net
from shakenfist import networkinterface
from shakenfist import scheduler
from shakenfist import util


LOG, _ = logutil.setup(__name__)


def handle(jobname, workitem):
    log = LOG.with_field('workitem', jobname)
    log.info('Processing workitem')

    setproctitle.setproctitle(
        '%s-%s' % (daemon.process_name('queues'), jobname))

    inst = None
    task = None
    try:
        for task in workitem.get('tasks', []):
            if not QueueTask.__subclasscheck__(type(task)):
                raise exceptions.UnknownTaskException(
                    'Task was not decoded: %s' % task)

            if InstanceTask.__subclasscheck__(type(task)):
                inst = instance.Instance.from_db(task.instance_uuid())
                if not inst:
                    raise exceptions.InstanceNotInDBException(
                        task.instance_uuid())

            if isinstance(task, FetchImageTask):
                inst = instance.Instance.from_db(task.instance_uuid())

            if isinstance(task, SnapshotTask):
                inst = instance.Instance.from_db(task.instance_uuid())

            if inst:
                log_i = log.with_instance(inst)
            else:
                log_i = log

            log_i.with_field('task_name', task.name()).info('Starting task')

            # TODO(andy) Should network events also come through here eventually?
            # Then this can be generalised to record events on networks/instances

            # TODO(andy) This event should be recorded when it is recorded as
            # dequeued in the DB. Currently it's reporting action on the item
            # and calling it 'dequeue'.

            if inst:
                # TODO(andy) move to QueueTask
                db.add_event('instance', inst.uuid, task.pretty_task_name(),
                             'dequeued', None, 'Work item %s' % jobname)

            if isinstance(task, FetchImageTask):
                image_fetch(task.url(), inst)

            elif isinstance(task, PreflightInstanceTask):
                if (inst.state.value == dbo.STATE_DELETED or
                        inst.state.value.endswith('-error')):
                    log_i.warning(
                        'You cannot preflight an instance in state %s, skipping task'
                        % inst.state.value)
                    continue

                redirect_to = instance_preflight(inst, task.network())
                if redirect_to:
                    log_i.info('Redirecting instance start to %s'
                               % redirect_to)
                    db.enqueue(redirect_to, workitem)
                    return

            elif isinstance(task, StartInstanceTask):
                if (inst.state.value == dbo.STATE_DELETED or
                        inst.state.value.endswith('-error')):
                    log_i.warning(
                        'You cannot start an instance in state %s, skipping task'
                        % inst.state.value)
                    continue

                instance_start(inst, task.network())
                db.enqueue('%s-metrics' % config.NODE_NAME, {})

            elif isinstance(task, DeleteInstanceTask):
                try:
                    instance_delete(inst)
                    db.enqueue('%s-metrics' % config.NODE_NAME, {})
                except Exception as e:
                    util.ignore_exception(daemon.process_name('queues'), e)

            elif isinstance(task, FloatNetworkInterfaceTask):
                # Just punt it to the network node now that the interface is ready
                db.enqueue('networknode', task)

            elif isinstance(task, SnapshotTask):
                snapshot(inst, task.disk(),
                         task.artifact_uuid(), task.blob_uuid())

            else:
                log_i.with_field('task', task).error(
                    'Unhandled task - dropped')

            log_i.info('Task complete')

    except exceptions.ImageFetchTaskFailedException as e:
        # Usually caused by external issue and not an application error
        log.info('Fetch Image Error: %s', e)
        if inst:
            inst.enqueue_delete_due_error('Failed queue task: %s' % e)

    except Exception as e:
        # Logging ignored exception - this should be investigated
        util.ignore_exception(daemon.process_name('queues'), e)
        if inst:
            inst.enqueue_delete_due_error('Failed queue task: %s' % e)

    finally:
        db.resolve(config.NODE_NAME, jobname)
        if inst:
            inst.add_event('tasks complete', 'dequeued',
                           msg='Work item %s' % jobname)
        log.info('Completed workitem')


def image_fetch(url, inst):
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
            img.get([lock], inst)
            db.add_event('image', url, 'fetch', None, None, 'success')

    except (exceptions.HTTPError, requests.exceptions.RequestException) as e:
        LOG.with_field('image', url).info('Failed to fetch image')

        # Clean common problems to store in events
        msg = str(e)
        if msg.find('Name or service not known'):
            msg = 'DNS error'
        if msg.find('No address associated with hostname'):
            msg = 'DNS error'
        db.add_event('image', url, 'fetch', None, None, msg)

        raise exceptions.ImageFetchTaskFailedException(
            'Failed to fetch image: %s Exception: %s' % (url, e))


def instance_preflight(inst, network):
    inst.state = 'preflight'

    # Try to place on this node
    s = scheduler.Scheduler()
    try:
        s.place_instance(inst, network, candidates=[config.NODE_NAME])
        return None

    except exceptions.LowResourceException as e:
        inst.add_event('schedule', 'retry', None,
                       'insufficient resources: ' + str(e))

    # Unsuccessful placement, check if reached placement attempt limit
    db_placement = inst.placement
    if db_placement['placement_attempts'] > 3:
        raise exceptions.AbortInstanceStartException(
            'Too many start attempts')

    # Try placing on another node
    try:
        if inst.requested_placement:
            # TODO(andy): Ask Mikal why this is not the current node?
            candidates = [inst.requested_placement]
        else:
            candidates = []
            for node in s.metrics.keys():
                if node != config.NODE_NAME:
                    candidates.append(node)

        candidates = s.place_instance(inst, network,
                                      candidates=candidates)
        inst.place_instance(candidates[0])
        return candidates[0]

    except exceptions.LowResourceException as e:
        inst.add_event('schedule', 'failed', None,
                       'insufficient resources: ' + str(e))
        # This raise implies delete above
        raise exceptions.AbortInstanceStartException(
            'Unable to find suitable node')


def instance_start(inst, network):
    with inst.get_lock(ttl=900, op='Instance start') as lock:
        # Ensure networks are connected to this node
        nets = {}
        for netdesc in network:
            if netdesc['network_uuid'] not in nets:
                n = net.Network.from_db(netdesc['network_uuid'])
                if not n:
                    inst.enqueue_delete_due_error(
                        'missing network: %s' % netdesc['network_uuid'])
                    return

                if n.state.value != dbo.STATE_CREATED:
                    inst.enqueue_delete_due_error(
                        'network is not active: %s' % n.uuid)
                    return

                n.create_on_hypervisor()
                n.ensure_mesh()
                n.update_dhcp()

        # Allocate console and VDI ports
        inst.allocate_instance_ports()

        # Now we can start the instance
        libvirt = util.get_libvirt()
        try:
            with util.RecordedOperation('instance creation', inst):
                inst.create(lock=lock)

        except libvirt.libvirtError as e:
            code = e.get_error_code()
            if code in (libvirt.VIR_ERR_CONFIG_UNSUPPORTED,
                        libvirt.VIR_ERR_XML_ERROR):
                inst.enqueue_delete_due_error(
                    'instance failed to start: %s' % e)
                return

        for ni in networkinterface.interfaces_for_instance(inst):
            ni.state = dbo.STATE_CREATED


def instance_delete(inst):
    with inst.get_lock(op='Instance delete'):
        db.add_event('instance', inst.uuid, 'queued', 'delete', None, None)

        # Create list of networks used by instance
        instance_networks = []
        for ni in networkinterface.interfaces_for_instance(inst):
            if ni.network_uuid not in instance_networks:
                instance_networks.append(ni.network_uuid)

        # Create list of networks used by all other instances
        host_networks = []
        for i in instance.Instances([instance.this_node_filter,
                                     instance.active_states_filter]):
            if not i.uuid == inst.uuid:
                for ni in networkinterface.interfaces_for_instance(i):
                    if ni.network_uuid not in host_networks:
                        host_networks.append(ni.network_uuid)

        inst.delete()

        # Delete the instance's interfaces
        with util.RecordedOperation('release network addresses', inst):
            for ni in networkinterface.interfaces_for_instance(inst):
                ni.delete()

        # Check each network used by the deleted instance
        for network in instance_networks:
            n = net.Network.from_db(network)
            if n:
                # If network used by another instance, only update
                if network in host_networks:
                    with util.RecordedOperation('deallocate ip address', inst):
                        n.update_dhcp()
                else:
                    # Network not used by any other instance therefore delete
                    with util.RecordedOperation('remove network from node', n):
                        n.delete_on_hypervisor()


def snapshot(inst, disk, artifact_uuid, blob_uuid):
    blob.snapshot_disk(disk, blob_uuid)
    a = Artifact.from_db(artifact_uuid)
    if a.state == dbo.STATE_INITIAL:
        a.state = dbo.STATE_CREATED


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
