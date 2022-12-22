import requests
import setproctitle

from shakenfist.artifact import Artifact
from shakenfist import blob
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import images
from shakenfist import instance
from shakenfist import logutil
from shakenfist.tasks import (QueueTask,
                              DeleteInstanceTask,
                              FetchImageTask,
                              HypervisorDestroyNetworkTask,
                              InstanceTask,
                              PreflightInstanceTask,
                              StartInstanceTask,
                              DestroyNetworkTask,
                              DeleteNetworkWhenClean,
                              FloatNetworkInterfaceTask,
                              SnapshotTask,
                              FetchBlobTask)
from shakenfist import network
from shakenfist import networkinterface
from shakenfist import scheduler
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt


LOG, _ = logutil.setup(__name__)


def handle(jobname, workitem):
    libvirt = util_libvirt.get_libvirt()

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

            if isinstance(task, FetchImageTask):
                n = task.namespace()
                if not n:
                    n = 'system'
                image_fetch(task.url(), n, inst)

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
                    etcd.enqueue(redirect_to, workitem)
                    return

            elif isinstance(task, StartInstanceTask):
                if (inst.state.value == dbo.STATE_DELETED or
                        inst.state.value.endswith('-error')):
                    log_i.warning(
                        'You cannot start an instance in state %s, skipping task'
                        % inst.state.value)
                    continue

                instance_start(inst, task.network())

            elif isinstance(task, DeleteInstanceTask):
                try:
                    instance_delete(inst)
                except Exception as e:
                    util_general.ignore_exception(
                        'instance %s delete task' % inst, e)

            elif isinstance(task, FloatNetworkInterfaceTask):
                # Just punt it to the network node now that the interface is ready
                etcd.enqueue('networknode', task)

            elif isinstance(task, SnapshotTask):
                snapshot(inst, task.disk(), task.artifact_uuid(), task.blob_uuid(),
                         task.thin())

            elif isinstance(task, DeleteNetworkWhenClean):
                # This is a historical concept, it turns out the network node
                # now just defers the delete task until there are no interfaces,
                # so we don't need this at all.
                etcd.enqueue(
                    'networknode', DestroyNetworkTask(task.network_uuid()))

            elif isinstance(task, HypervisorDestroyNetworkTask):
                n = network.Network.from_db(task.network_uuid())
                n.delete_on_hypervisor()

            elif isinstance(task, FetchBlobTask):
                metrics = etcd.get('metrics', config.NODE_NAME, None)
                if metrics:
                    metrics = metrics.get('metrics', {})
                else:
                    metrics = {}

                b = blob.Blob.from_db(task.blob_uuid())
                if not b:
                    log.with_fields({
                        'blob': task.blob_uuid()
                    }).info('Cannot replicate blob, not found')

                elif (int(metrics.get('disk_free_blobs', 0)) - int(b.size) <
                      config.MINIMUM_FREE_DISK):
                    log.with_fields({
                        'blob': task.blob_uuid()
                    }).info('Cannot replicate blob, insufficient space')

                else:
                    log.with_object(b).info('Replicating blob')
                    size = b.ensure_local([])
                    log.with_object(b).with_fields({
                        'transferred': size,
                        'expected': b.size
                    }).info('Replicating blob complete')

            else:
                log_i.with_field('task', task).error(
                    'Unhandled task - dropped')

            log_i.info('Task complete')

    except exceptions.ImageFetchTaskFailedException as e:
        # Usually caused by external issue and not an application error
        log.info('Fetch Image Error: %s', e)
        if inst:
            inst.enqueue_delete_due_error('Image fetch failed: %s' % e)

    except exceptions.ImagesCannotShrinkException as e:
        log.info('Fetch Resize Error: %s', e)
        if inst:
            inst.enqueue_delete_due_error('Image resize failed: %s' % e)

    except libvirt.libvirtError as e:
        log.info('Libvirt Error: %s', e)
        if inst:
            inst.enqueue_delete_due_error('Instance task failed: %s' % e)

    except exceptions.InstanceException as e:
        log.info('Instance Error: %s', e)
        if inst:
            inst.enqueue_delete_due_error('Instance task failed: %s' % e)

    except Exception as e:
        # Logging ignored exception - this should be investigated
        util_general.ignore_exception('queue worker', e)
        if inst:
            inst.enqueue_delete_due_error('Failed queue task: %s' % e)

    finally:
        etcd.resolve(config.NODE_NAME, jobname)


def image_fetch(url, namespace, inst):
    a = Artifact.from_url(Artifact.TYPE_IMAGE, url, namespace=namespace,
                          create_if_new=True)
    try:
        # TODO(andy): Wait up to 15 mins for another queue process to download
        # the required image. This will be changed to queue on a
        # "waiting_image_fetch" queue but this works now.
        images.ImageFetchHelper(inst, a).get_image()
        a.add_event2('artifact fetch complete')

    except (exceptions.HTTPError, requests.exceptions.RequestException,
            requests.exceptions.ConnectionError) as e:
        # Clean common problems to store in events
        msg = str(e)
        if msg.find('Name or service not known'):
            msg = 'DNS error'
        if msg.find('No address associated with hostname'):
            msg = 'DNS error'

        # If the artifact has never successfully downloaded, then we are
        # clearly in an error state. However, if we already have a copy of the
        # artifact and the serving web site is experiencing a transient error
        # we should not mark the entire artifact as in error.
        if (a.state.value in [Artifact.STATE_INITIAL, Artifact.STATE_CREATING] or
                msg != 'DNS error'):
            a.state = Artifact.STATE_ERROR
            a.error = msg
            raise exceptions.ImageFetchTaskFailedException(
                'Failed to fetch image: %s Exception: %s' % (url, e))
        else:
            a.add_event2('Updating image failed, using already cached version',
                         extra={'message': msg})


def instance_preflight(inst, netdescs):
    inst.state = instance.Instance.STATE_PREFLIGHT

    # Try to place on this node
    s = scheduler.Scheduler()
    try:
        s.place_instance(inst, netdescs, candidates=[config.NODE_NAME])
        return None

    except exceptions.LowResourceException as e:
        inst.add_event2('schedule failed, insufficient resources: ' + str(e))

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

        candidates = s.place_instance(inst, netdescs, candidates=candidates)
        inst.place_instance(candidates[0])
        return candidates[0]

    except exceptions.LowResourceException as e:
        inst.add_event2('schedule failed, insufficient resources: ' + str(e))
        # This raise implies delete above
        raise exceptions.AbortInstanceStartException(
            'Unable to find suitable node')


def instance_start(inst, netdescs):
    if inst.state.value.endswith('-error'):
        inst.add_event2('You cannot start an instance in an error state.')
        return
    if inst.state.value in (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED):
        inst.add_event2('You cannot start an instance which has been deleted.')
        return

    with inst.get_lock(ttl=900, op='Instance start') as lock:
        try:
            # Ensure networks are connected to this node
            iface_uuids = []
            for netdesc in netdescs:
                iface_uuids.append(netdesc['iface_uuid'])
                n = network.Network.from_db(netdesc['network_uuid'])
                if not n:
                    inst.enqueue_delete_due_error(
                        'missing network: %s' % netdesc['network_uuid'])
                    return

                if n.state.value != dbo.STATE_CREATED:
                    inst.enqueue_delete_due_error(
                        'network is not active: %s' % n.uuid)
                    return

                # We must record interfaces very early for the vxlan leak
                # detection code in the net daemon to work correctly.
                ni = networkinterface.NetworkInterface.from_db(
                    netdesc['iface_uuid'])
                if ni.state.value not in dbo.ACTIVE_STATES:
                    inst.add_event2(
                        'You cannot start an instance with an inactive network '
                        'interface.', extra={
                            'networkinterface': ni.uuid,
                            'state': ni.state.value
                        })
                    inst.enqueue_delete_due_error(
                        'Network interface is inactive')
                    return

                ni.state = dbo.STATE_CREATED
                n.create_on_hypervisor()
                n.ensure_mesh()
                n.update_dhcp()

            # Allocate console and VDI ports
            inst.allocate_instance_ports()

            # Now we can start the instance
            with util_general.RecordedOperation('instance creation', inst):
                inst.create(iface_uuids, lock=lock)

        except exceptions.InvalidStateException as e:
            # This instance is in an error or deleted state. Given the check
            # at the top of this method, that indicates a race.
            inst.enqueue_delete_due_error('invalid state transition: %s' % e)
            return


def instance_delete(inst):
    with inst.get_lock(op='Instance delete'):
        # There are two delete state flows:
        #   - error transition states (preflight-error etc) to error
        #   - created to deleted

        # If the instance is deleted already, we're done here.
        if inst.state.value == dbo.STATE_DELETED:
            return

        # We don't need delete_wait for the error states as they're already
        # in a transition state.
        if not inst.state.value.endswith('-error'):
            inst.state = dbo.STATE_DELETE_WAIT

        # Create list of networks used by instance. We cannot use the
        # interfaces cached in the instance here, because the instance
        # may have failed to get to the point where it populates that
        # field (an image fetch failure for example).
        instance_networks = []
        interfaces = []
        for ni in networkinterface.interfaces_for_instance(inst):
            if ni:
                interfaces.append(ni)
                if ni.network_uuid not in instance_networks:
                    instance_networks.append(ni.network_uuid)

        # Stop the instance
        inst.power_off()

        # Delete the instance's interfaces
        for ni in interfaces:
            ni.delete()

        # Create list of networks used by all other instances
        host_networks = []
        for i in instance.Instances([instance.this_node_filter,
                                     instance.active_states_filter]):
            if not i.uuid == inst.uuid:
                for iface_uuid in inst.interfaces:
                    ni = networkinterface.NetworkInterface.from_db(iface_uuid)
                    if ni and ni.network_uuid not in host_networks:
                        host_networks.append(ni.network_uuid)

        inst.delete()

        # Check each network used by the deleted instance
        for network_uuid in instance_networks:
            n = network.Network.from_db(network_uuid)
            if not n:
                continue

            if n.state.value == dbo.STATE_DELETE_WAIT:
                continue

            n.update_dhcp()

            if not config.NODE_IS_NETWORK_NODE and network_uuid not in host_networks:
                # We are not the network node and the network not used by any
                # other instance on this hypervisor, therefore clean it up
                n.delete_on_hypervisor()


def snapshot(inst, disk, artifact_uuid, blob_uuid, thin=False):
    a = Artifact.from_db(artifact_uuid)
    if a.state.value == Artifact.STATE_DELETED:
        # The artifact was deleted before the queued blob creation occurred
        return

    try:
        b = blob.snapshot_disk(disk, blob_uuid, thin=thin)
    except exceptions.BlobDependencyMissing:
        return

    if a.state.value == Artifact.STATE_DELETED:
        # The artifact was deleted while we were creating the blob, just delete
        # the blob too.
        b.state = blob.Blob.STATE_DELETED

    try:
        b.ref_count_inc()
        a.state = Artifact.STATE_CREATED
    except exceptions.BlobDeleted:
        if a.state.value != Artifact.STATE_DELETED:
            a.state = Artifact.STATE_ERROR
    except exceptions.InvalidStateException:
        b.ref_count_dec()


class Monitor(daemon.WorkerPoolDaemon):
    def run(self):
        LOG.info('Starting')

        while not self.exit.is_set():
            try:
                self.reap_workers()

                if not self.exit.is_set():
                    if not self.dequeue_work_item(config.NODE_NAME, handle):
                        self.exit.wait(0.2)
                elif len(self.workers) > 0:
                    LOG.info('Waiting for %d workers to finish'
                             % len(self.workers))
                    self.exit.wait(0.2)
                else:
                    return

            except Exception as e:
                util_general.ignore_exception('queue worker', e)

        LOG.info('Terminating')
