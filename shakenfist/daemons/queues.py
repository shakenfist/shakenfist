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
from shakenfist import logutil
from shakenfist import net
from shakenfist import scheduler
from shakenfist import util
from shakenfist import virt
from shakenfist.tasks import (QueueTask,
                              DeleteInstanceTask,
                              ErrorInstanceTask,
                              FetchImageTask,
                              InstanceTask,
                              PreflightInstanceTask,
                              StartInstanceTask,
                              )


LOG, _ = logutil.setup(__name__)


def handle(jobname, workitem):
    log = LOG.withField('workitem', jobname)
    log.info('Processing workitem')

    setproctitle.setproctitle(
        '%s-%s' % (daemon.process_name('queues'), jobname))

    instance_uuid = None
    task = None
    try:
        for task in workitem.get('tasks', []):
            if not QueueTask.__subclasscheck__(type(task)):
                raise exceptions.UnknownTaskException(
                    'Task was not decoded: %s' % task)

            if (InstanceTask.__subclasscheck__(type(task)) or
                    isinstance(task, FetchImageTask)):
                instance_uuid = task.instance_uuid()

            if instance_uuid:
                log_i = log.withInstance(instance_uuid)
            else:
                log_i = log

            log_i.withField('task_name', task.name()).info('Starting task')

            # TODO(andy) Should network events also come through here eventually?
            # Then this can be generalised to record events on networks/instances

            # TODO(andy) This event should be recorded when it is recorded as
            # dequeued in the DB. Currently it's reporting action on the item
            # and calling it 'dequeue'.

            if instance_uuid:
                # TODO(andy) move to QueueTask
                db.add_event('instance', instance_uuid, task.pretty_task_name(),
                             'dequeued', None, 'Work item %s' % jobname)

            if isinstance(task, FetchImageTask):
                image_fetch(task.url(), instance_uuid)

            elif isinstance(task, PreflightInstanceTask):
                redirect_to = instance_preflight(instance_uuid, task.network())
                if redirect_to:
                    log_i.info('Redirecting instance start to %s'
                               % redirect_to)
                    db.place_instance(instance_uuid, redirect_to)
                    db.enqueue(redirect_to, workitem)
                    return

            elif isinstance(task, StartInstanceTask):
                instance_start(instance_uuid, task.network())
                db.update_instance_state(instance_uuid, 'created')
                db.enqueue('%s-metrics' % config.NODE_NAME, {})

            elif isinstance(task, DeleteInstanceTask):
                try:
                    instance_delete(instance_uuid)
                    db.update_instance_state(instance_uuid, 'deleted')
                except Exception as e:
                    util.ignore_exception(daemon.process_name('queues'), e)

            elif isinstance(task, ErrorInstanceTask):
                try:
                    instance_delete(instance_uuid)
                    db.update_instance_state(instance_uuid, 'error')

                    if task.error_msg():
                        db.update_instance_error_message(
                            instance_uuid, task.error_msg())
                    db.enqueue('%s-metrics' % config.NODE_NAME, {})
                except Exception as e:
                    util.ignore_exception(daemon.process_name('queues'), e)

            else:
                log_i.withField('task', task).error('Unhandled task - dropped')

            log_i.info('Task complete')

    except exceptions.ImageFetchTaskFailedException as e:
        # Usually caused by external issue and not an application error
        log.info('Fetch Image Error: %s', e)
        if instance_uuid:
            db.enqueue_instance_error(instance_uuid,
                                      'failed queue task: %s' % e)

    except Exception as e:
        util.ignore_exception(daemon.process_name('queues'), e)
        if instance_uuid:
            db.enqueue_instance_error(instance_uuid,
                                      'failed queue task: %s' % e)

    finally:
        db.resolve(config.NODE_NAME, jobname)
        if instance_uuid:
            db.add_event('instance', instance_uuid, 'tasks complete',
                         'dequeued', None, 'Work item %s' % jobname)
        log.info('Completed workitem')


def image_fetch(url, instance_uuid):
    instance = None
    if instance_uuid:
        instance = virt.from_db(instance_uuid)

    try:
        # TODO(andy): Wait up to 15 mins for another queue process to download
        # the required image. This will be changed to queue on a
        # "waiting_image_fetch" queue but this works now.
        with db.get_lock('image', config.NODE_NAME, Image.calc_unique_ref(url),
                         timeout=15*60, op='Image fetch') as lock:
            img = Image.from_url(url)
            img.get([lock], instance)
            db.add_event('image', url, 'fetch', None, None, 'success')

    except (exceptions.HTTPError, requests.exceptions.RequestException) as e:
        LOG.withField('image', url).info('Failed to fetch image')
        if instance_uuid:
            db.enqueue_instance_error(instance_uuid,
                                      'Image fetch failed: %s' % e)

        # Clean common problems to store in events
        msg = str(e)
        re_conn_err = re.compile(r'.*NewConnectionError\(\'\<.*\>: (.*)\'')
        m = re_conn_err.match(msg)
        if m:
            msg = m.group(1)
        db.add_event('image', url, 'fetch', None, None, 'Error: '+msg)

        raise exceptions.ImageFetchTaskFailedException(
            'Failed to fetch image %s' % url)


def instance_preflight(instance_uuid, network):
    db.update_instance_state(instance_uuid, 'preflight')

    s = scheduler.Scheduler()
    instance = virt.from_db(instance_uuid)

    try:
        s.place_instance(instance, network, candidates=[config.NODE_NAME])
        return None

    except exceptions.LowResourceException as e:
        db.add_event('instance', instance_uuid,
                     'schedule', 'retry', None,
                     'insufficient resources: ' + str(e))

    if instance.db_entry.get('placement_attempts') > 3:
        raise exceptions.AbortInstanceStartException(
            'Too many start attempts')

    try:
        if instance.db_entry.get('requested_placement'):
            candidates = [instance.db_entry.get('requested_placement')]
        else:
            candidates = []
            for node in s.metrics.keys():
                if node != config.NODE_NAME:
                    candidates.append(node)

        candidates = s.place_instance(instance, network,
                                      candidates=candidates)
        return candidates[0]

    except exceptions.LowResourceException as e:
        db.add_event('instance', instance_uuid,
                     'schedule', 'failed', None,
                     'insufficient resources: ' + str(e))
        # This raise implies delete above
        raise exceptions.AbortInstanceStartException(
            'Unable to find suitable node')


def instance_start(instance_uuid, network):
    log = LOG.withField('instance', instance_uuid)

    with db.get_lock(
            'instance', None, instance_uuid, ttl=900, timeout=120,
            op='Instance start') as lock:
        instance = virt.from_db(instance_uuid)

        # Collect the networks
        nets = {}
        for netdesc in network:
            if netdesc['network_uuid'] not in nets:
                n = net.from_db(netdesc['network_uuid'])
                if not n:
                    db.enqueue_instance_error(instance_uuid, 'missing network')
                    return

                nets[netdesc['network_uuid']] = n

        # Create the networks
        with util.RecordedOperation('ensure networks exist', instance):
            for network_uuid in nets:
                n = nets[network_uuid]
                try:
                    n.create()
                    n.ensure_mesh()
                    n.update_dhcp()
                except exceptions.DeadNetwork as e:
                    log.withField('network', n).warning(
                        'Instance tried to use dead network')
                    db.enqueue_instance_error(
                        instance_uuid, 'tried to use dead network: %s' % e)
                    return

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
                db.enqueue_instance_error(instance_uuid,
                                          'instance failed to start: %s' % e)
                return

        for iface in db.get_instance_interfaces(instance_uuid):
            db.update_network_interface_state(iface['uuid'], 'created')


def instance_delete(instance_uuid):
    with db.get_lock('instance', None, instance_uuid, timeout=120,
                     op='Instance delete'):
        db.add_event('instance', instance_uuid,
                     'queued', 'delete', None, None)

        # Create list of networks used by instance
        instance_networks = []
        for iface in list(db.get_instance_interfaces(instance_uuid)):
            if not iface['network_uuid'] in instance_networks:
                instance_networks.append(iface['network_uuid'])

        # Create list of networks used by all other instances
        host_networks = []
        for inst in list(db.get_instances(only_node=config.NODE_NAME)):
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
                                                instance_from_db_virt):
                        n.update_dhcp()
                else:
                    # Network not used by any other instance therefore delete
                    with util.RecordedOperation('remove network', n):
                        n.delete()


class Monitor(daemon.Daemon):
    def run(self):
        workers = []
        LOG.info('Starting Queues')

        libvirt = util.get_libvirt()
        conn = libvirt.open(None)
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
