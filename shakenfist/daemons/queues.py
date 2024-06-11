import os
import requests
import setproctitle
from shakenfist_utilities import logs
import time
import uuid

from shakenfist.agentoperation import AgentOperation
from shakenfist.artifact import Artifact
from shakenfist import blob
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT, EVENT_TYPE_STATUS
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import images
from shakenfist import instance
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
                              FetchBlobTask,
                              ArchiveTranscodeTask,
                              PreflightAgentOperationTask,
                              HotPlugInstanceInterfaceTask)
from shakenfist import network
from shakenfist import networkinterface
from shakenfist import scheduler
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import process as util_process


LOG, _ = logs.setup(__name__)


def handle(jobname, workitem):
    libvirt = util_libvirt.get_libvirt()

    log = LOG.with_fields({'workitem': jobname})
    log.info('Processing workitem')

    setproctitle.setproctitle('{}-{}'.format(daemon.process_name('queues'), jobname))

    inst = None
    task = None
    try:
        for task in workitem.get('tasks', []):
            log = log.with_fields({'task': task})

            if not QueueTask.__subclasscheck__(type(task)):
                raise exceptions.UnknownTaskException(
                    'Task was not decoded: %s' % task)

            if InstanceTask.__subclasscheck__(type(task)):
                inst = instance.Instance.from_db(task.instance_uuid())
                if not inst:
                    raise exceptions.InstanceNotInDBException(
                        task.instance_uuid())

            for t in [FetchImageTask, SnapshotTask, HotPlugInstanceInterfaceTask]:
                if isinstance(task, t):
                    inst = instance.Instance.from_db(task.instance_uuid())
                    break

            if inst:
                log = log.with_fields({'instance': inst})

            log.with_fields({'task_name': task.name()}).info('Starting task')

            if isinstance(task, FetchImageTask):
                n = task.namespace()
                if not n:
                    n = 'system'
                image_fetch(task.url(), n, inst)

            elif isinstance(task, PreflightInstanceTask):
                s = inst.state.value
                if s == dbo.STATE_DELETED or s.endswith('-error'):
                    log.warning(
                        'You cannot preflight an instance in state %s, skipping task' % s)
                    continue

                redirect_to = instance_preflight(inst, task.network())
                if redirect_to:
                    log.info(f'Redirecting instance start to {redirect_to}')
                    etcd.enqueue(redirect_to, workitem)
                    return

            elif isinstance(task, StartInstanceTask):
                instance_start(inst, task.network())

            elif isinstance(task, HotPlugInstanceInterfaceTask):
                inst.hot_plug_interface(
                    task.network_uuid(), task.interface_uuid())

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
                etcd.enqueue('networknode', DestroyNetworkTask(task.network_uuid()))

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
                    try:
                        log.with_fields({'blob': b}).info('Replicating blob')
                        size = b.ensure_local([], wait_for_other_transfers=False)
                        log.with_fields({
                            'blob': b,
                            'transferred': size,
                            'expected': b.size
                        }).info('Replicating blob complete')
                    except exceptions.BlobMissing:
                        log.with_fields({'blob': b}).info(
                            'Cannot replicate blob, no online sources')

            elif isinstance(task, ArchiveTranscodeTask):
                if os.path.exists(task.cache_path()):
                    b = blob.Blob.from_db(task.blob_uuid())
                    if b:
                        transcode_blob_uuid = str(uuid.uuid4())
                        transcode_blob_path = blob.Blob.filepath(transcode_blob_uuid)
                        util_process.execute(
                            [], f'cp {task.cache_path()} {transcode_blob_path}')
                        st = os.stat(transcode_blob_path)

                        transcode_blob = blob.Blob.new(
                            transcode_blob_uuid, st.st_size, time.time(), time.time())
                        transcode_blob.state = blob.Blob.STATE_CREATED
                        transcode_blob.observe()
                        transcode_blob.verify_checksum(locks=[])
                        transcode_blob.request_replication()
                        log.with_fields({
                            'blob': b,
                            'transcode_blob_uuid': transcode_blob_uuid,
                            'description': task.transcode_description()}).info(
                            'Recorded transcode')

                        b.add_transcode(task.transcode_description(),
                                        transcode_blob_uuid)
                        transcode_blob.ref_count_inc(b)

            elif isinstance(task, PreflightAgentOperationTask):
                preflight_agent_operation(task.agentop_uuid())

            else:
                log.error('Unhandled task was dropped')

            log.info('Task complete')

    except exceptions.BlobAlreadyBeingTransferred:
        # Re-enqueue this job to run in a minute
        log.info('Deferring job as blob is already being transferred')
        etcd.enqueue(config.NODE_NAME, workitem, delay=60)

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
        a.add_event(EVENT_TYPE_AUDIT, 'artifact fetch complete')

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
                f'Failed to fetch image: {url} Exception: {e}')
        else:
            a.add_event(
                EVENT_TYPE_AUDIT, 'updating image failed, using already cached version',
                extra={'message': msg})


def instance_preflight(inst, netdescs):
    inst.state = instance.Instance.STATE_PREFLIGHT

    # Try to place on this node
    s = scheduler.Scheduler()
    try:
        s.find_candidates(inst, netdescs, candidates=[config.NODE_NAME])
        return None

    except exceptions.LowResourceException as e:
        inst.add_event(EVENT_TYPE_AUDIT, 'schedule failed, insufficient resources',
                       extra={'message': str(e)})

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

        candidates = s.find_candidates(inst, netdescs, candidates=candidates)
        inst.place_instance(candidates[0])
        return candidates[0]

    except exceptions.LowResourceException as e:
        inst.add_event(EVENT_TYPE_AUDIT, 'schedule failed, insufficient resources',
                       extra={'message': str(e)})
        # This raise implies delete above
        raise exceptions.AbortInstanceStartException(
            'Unable to find suitable node')


def instance_start(inst, netdescs):
    s = inst.state.value
    if s.endswith('-error'):
        inst.add_event(
            EVENT_TYPE_STATUS, 'you cannot start an instance in an error state.')
        return
    if s in (dbo.STATE_DELETE_WAIT, dbo.STATE_DELETED):
        inst.add_event(
            EVENT_TYPE_STATUS, 'you cannot start an instance which has been deleted.')
        return

    with inst.get_lock(ttl=900, op='Instance start', global_scope=False):
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
                    inst.add_event(
                        EVENT_TYPE_STATUS,
                        'you cannot start an instance with an inactive network '
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
                inst.create(iface_uuids)

        except exceptions.InvalidStateException as e:
            # This instance is in an error or deleted state. Given the check
            # at the top of this method, that indicates a race.
            inst.enqueue_delete_due_error('invalid state transition: %s' % e)
            return


def instance_delete(inst):
    with inst.get_lock(op='Instance delete', global_scope=False):
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
        for i in instance.Instances([instance.this_node_filter], prefilter='active'):
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
        return

    if inst.state.value == instance.Instance.STATE_DELETED:
        # If the instance we were snapshotting has been deleted by the time we
        # finish the snapshot, then just delete the blob.
        b.state = blob.Blob.STATE_DELETED
        return

    try:
        a.add_index(b.uuid)
        a.state = Artifact.STATE_CREATED
    except exceptions.BlobDeleted:
        if a.state.value != Artifact.STATE_DELETED:
            a.state = Artifact.STATE_ERROR
    except exceptions.InvalidStateException:
        b.ref_count_dec(a)


def preflight_agent_operation(agentop_uuid):
    agentop = AgentOperation.from_db(agentop_uuid)
    if not agentop:
        return

    if not agentop.state.value == AgentOperation.STATE_PREFLIGHT:
        return

    for command in agentop.commands:
        if command['command'] == 'put-blob':
            b = blob.Blob.from_db(command['blob_uuid'])
            if not b:
                agentop.error = 'preflight failure, blob missing: %s' % command['blob_uuid']
                return
            b.ensure_local([])

    agentop.state = AgentOperation.STATE_QUEUED


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
                    LOG.info('Waiting for %d workers to finish' % len(self.workers))
                    self.exit.wait(0.2)
                else:
                    return

            except Exception as e:
                util_general.ignore_exception('queue worker', e)

        LOG.info('Terminated')
