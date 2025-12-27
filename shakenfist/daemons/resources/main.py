import os
import platform
import re
import time

import psutil
from prometheus_client import Gauge
from prometheus_client import start_http_server
from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import instance
from shakenfist.network import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_RESOURCES
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import get_object_class
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.daemons import daemon
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.node import Node
from shakenfist.operations.baseoperation import get_all_background_node_queues
from shakenfist.operations.baseoperation import get_all_network_queues
from shakenfist.operations.baseoperation import get_node_user_facing_node_queues
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)


# /usr/bin/kvm -name guest=sf:ec069949-eb19-4f7a-aaf2-a6020c877b95,...
LIBVIRT_KVM_CMDLINE_RE = re.compile('.* guest=sf:([a-z0-9\\-]+).*')


class Monitor(daemon.Daemon):
    def __init__(self, id):
        super().__init__(id)
        start_http_server(config.RESOURCES_METRICS_PORT)

        self.last_logged_resources = 0

    def _get_stats(self):
        n = Node.from_db(config.NODE_NAME)

        old_metrics = etcd.get('metrics', config.NODE_NAME, {})
        timestamp = time.time()

        with util_libvirt.LibvirtConnection() as lc:
            # What's special about this node?
            retval = {
                'is_etcd_master': config.NODE_IS_ETCD_MASTER,
                'is_hypervisor': config.NODE_IS_HYPERVISOR,
                'is_network_node': config.NODE_IS_NETWORK_NODE,
                'is_eventlog_node': config.NODE_IS_EVENTLOG_NODE,
            }

            # CPU info
            present_cpus, _, available_cpus = lc.get_cpu_map()
            retval.update({
                'cpu_max': present_cpus,
                'cpu_available': available_cpus,
            })

            retval['cpu_max_per_instance'] = lc.get_max_vcpus()

            # This is disabled as data we don't currently use
            # for i in range(present_cpus):
            #    per_cpu_stats = conn.getCPUStats(i)
            #    for key in per_cpu_stats:
            #        retval['cpu_core%d_%s' % (i, key)] = per_cpu_stats[key]

            try:
                load_1, load_5, load_15 = psutil.getloadavg()
                retval.update({
                    'cpu_load_1': load_1,
                    'cpu_load_5': load_5,
                    'cpu_load_15': load_15,
                })
            except Exception as e:
                util_exceptions.ignore_exception('load average', e)

            # System memory info, converting bytes to mb
            stats = psutil.virtual_memory()
            retval.update({
                'memory_max': stats.total // 1024 // 1024,
                'memory_available': stats.available // 1024 // 1024
            })

            # libvirt memory info, converting kb to mb
            memory_stats = lc.get_memory_stats()
            retval.update({
                'memory_max_libvirt': memory_stats['total'] // 1024,
                'memory_available_libvirt': memory_stats['free'] // 1024,
            })

            # Kernel Shared Memory (KSM) information
            ksm_details = {}
            for ent in os.listdir('/sys/kernel/mm/ksm'):
                with open('/sys/kernel/mm/ksm/%s' % ent) as f:
                    ksm_details['memory_ksm_%s' % ent] = int(f.read().rstrip())
            retval.update(ksm_details)

            # Disk info. There could be more than one filesystem here, so we track
            # all of the paths we're fond of.
            fsids = []
            minimum = -1
            total = 0
            used = 0

            log_fields = {}
            for path in ['', 'blobs', 'events', 'image_cache', 'instances', 'uploads']:
                # We need to make the paths we check if they don't exist, otherwise
                # they wont be included in the metrics and things get confused.
                fullpath = os.path.join(config.STORAGE_PATH, path)
                os.makedirs(fullpath, exist_ok=True)
                s = os.statvfs(fullpath)
                free = s.f_frsize * s.f_bavail

                if s.f_fsid not in fsids:
                    total += s.f_frsize * s.f_blocks
                    used += s.f_frsize * (s.f_blocks - s.f_bfree)
                    if minimum == -1 or free < minimum:
                        minimum = free

                if path == '':
                    path = 'sfroot'
                retval['disk_free_%s' % path] = free
                log_fields[path] = free
            LOG.with_fields(log_fields).debug('Disk free')

            retval.update({
                'disk_total': total,
                'disk_free': minimum,
                'disk_used': used
            })

            # NOTE(mikal): these are _counters_ -- that is, like gauges in
            # prometheus the numbers continue to increase forever and aren't
            # all that meaningful unless you know the number from last time
            # you read the counter and how long has passed in between.
            disk_counters = psutil.disk_io_counters()
            retval.update({
                'disk_read_bytes': disk_counters.read_bytes,
                'disk_write_bytes': disk_counters.write_bytes,
                'disk_busy_time': disk_counters.busy_time
            })

            net_counters = psutil.net_io_counters()
            retval.update({
                'network_read_bytes': net_counters.bytes_recv,
                'network_write_bytes': net_counters.bytes_sent,
            })

            if old_metrics and 'timestamp' in old_metrics:
                spacing = timestamp - old_metrics['timestamp']
                old_metrics_values = old_metrics.get('metrics', {})
                retval['timestamp_spacing'] = spacing

                for counter in ['disk_read_bytes', 'disk_write_bytes',
                                'disk_busy_time', 'network_read_bytes',
                                'network_write_bytes']:
                    if counter not in old_metrics_values:
                        continue

                    old_counter_value = int(old_metrics_values[counter])
                    new_counter_value = int(retval[counter])
                    if old_counter_value > new_counter_value:
                        continue

                    delta = new_counter_value - old_counter_value
                    retval[f'{counter}_delta'] = delta
                    retval[f'{counter}_delta_per_second'] = \
                        delta / spacing
            else:
                LOG.info('Skipping delta metrics as we have no previous reading')

            # Virtual machine consumption info
            total_instances = 0
            total_active_instances = 0
            total_instance_max_memory = 0
            total_instance_actual_memory = 0
            total_instance_vcpus = 0
            total_instance_cpu_time = 0

            for domain in lc.get_all_domains():
                try:
                    active = domain.isActive() == 1
                    if active:
                        _, maxmem, mem, cpus, cpu_time = domain.info()

                    if active:
                        total_instances += 1
                        total_active_instances += 1
                        total_instance_max_memory += maxmem
                        total_instance_actual_memory += mem
                        total_instance_vcpus += cpus
                        total_instance_cpu_time += cpu_time

                except lc.libvirt.libvirtError:
                    # The domain has likely been deleted.
                    pass

            # Metric name helper
            def _safe_metric_name(name):
                name = name.lower()
                return re.sub(r'[^a-z0-9_]', '_', name)

            # Queue health statistics
            node_queue_waiting = 0
            node_queue_processing = 0
            node_queue_deferred = 0
            node_background_queue_waiting = 0
            node_background_queue_processing = 0
            node_background_queue_deferred = 0

            def _log_and_update_metrics_for_queue(
                    queue, log_prefix):
                processing, queued, deferred = etcd.get_queue_length(queue)
                LOG.with_fields({
                    'processing': processing,
                    'queued': queued,
                    'deferred': deferred,
                    'queue': queue
                }).debug(f'{log_prefix} queue length')

                safe_metric_queue_name = _safe_metric_name(f'queue_{queue}')
                retval.update({
                    f'{safe_metric_queue_name}_processing': processing,
                    f'{safe_metric_queue_name}_queued': queued,
                    f'{safe_metric_queue_name}_deferred': deferred
                })

                return processing, queued, deferred

            for queue in get_node_user_facing_node_queues(config.NODE_NAME):
                processing, queued, deferred = _log_and_update_metrics_for_queue(
                    queue, 'User facing')

                node_queue_processing += processing
                node_queue_waiting += queued
                node_queue_deferred += deferred

            for queue in get_all_background_node_queues(config.NODE_NAME):
                processing, queued, deferred = _log_and_update_metrics_for_queue(
                    queue, 'Background')

                node_background_queue_processing += processing
                node_background_queue_waiting += queued
                node_background_queue_deferred += deferred

            retval.update({
                'cpu_total_instance_vcpus': total_instance_vcpus,
                'cpu_total_instance_cpu_time': total_instance_cpu_time,
                'memory_total_instance_max': total_instance_max_memory // 1024,
                'memory_total_instance_actual': total_instance_actual_memory // 1024,
                'instances_total': total_instances,
                'instances_active': total_active_instances,
                'node_queue_processing': node_queue_processing,
                'node_queue_waiting': node_queue_waiting,
                'node_queue_deferred': node_queue_deferred,
                'node_background_queue_processing': node_background_queue_processing,
                'node_background_queue_waiting': node_background_queue_waiting,
                'node_background_queue_deferred': node_background_queue_deferred
            })

            if config.NODE_IS_NETWORK_NODE:
                network_waiting = 0
                network_processing = 0
                network_deferred = 0

                for queue in get_all_network_queues():
                    processing, queued, deferred = _log_and_update_metrics_for_queue(
                        queue, 'Network node')

                    network_waiting += queued
                    network_processing += processing
                    network_deferred += deferred

                retval.update({
                    'network_queue_processing': network_processing,
                    'network_queue_waiting': network_waiting,
                    'network_queue_deferred': network_deferred
                })

            if config.NODE_IS_EVENTLOG_NODE:
                queued = len(list(etcd.get_all('event', None, limit=10000)))
                retval.update({
                    'events_waiting': queued,
                })

            # What object versions do we support?
            for obj in OBJECT_NAMES_TO_CLASSES:
                retval['object_version_%s' % obj] = \
                    get_object_class(obj).current_version

            # How much CPU time have the various SF components consumed since restart?
            # We only traverse two layers here, so its not worth doing something
            # recursive.
            def _emit_process_metrics(p):
                if time.time() - p.create_time() < 60:
                    # Ignore new processes
                    return {}

                if p.name().startswith('sf-queues'):
                    # Ignore queue workers
                    return {}

                smn = _safe_metric_name(p.name())
                out = {}
                times = p.cpu_times()
                usage = (times.user + times.system)
                age = round(time.time() - p.create_time(), 2)
                out['process_cpu_time_%s' % smn] = usage
                out['process_age_%s' % smn] = age

                fraction = usage / age
                out['process_cpu_fraction_%s' % smn] = fraction
                if fraction > 0.25:
                    n.add_event(EVENT_TYPE_STATUS, 'process %s is a CPU hog' % smn,
                                extra={'fraction': fraction})
                return out

            if time.time() - self.last_logged_resources > 300:
                # Record SF process metrics
                process_metrics = {}
                me = psutil.Process(os.getpid())
                shim = me.parent()
                for child in shim.children():
                    try:
                        with child.oneshot():
                            process_metrics.update(
                                _emit_process_metrics(child))

                            for subchild in child.children():
                                with subchild.oneshot():
                                    process_metrics.update(
                                        _emit_process_metrics(subchild))
                    except (psutil.NoSuchProcess, FileNotFoundError):
                        ...
                # Record etcd process metrics
                if config.NODE_IS_ETCD_MASTER:
                    for p in psutil.process_iter():
                        try:
                            if p.name().endswith('/etcd'):
                                process_metrics.update(
                                    _emit_process_metrics(p))
                        except (psutil.NoSuchProcess, FileNotFoundError):
                            ...

                n.process_metrics = process_metrics

                # What package versions do we have? Debian package versions are
                # a mess and this will need tweaking if other host distributions
                # are added.
                try:
                    vers_out, _ = util_concurrency.execute(
                        ('dpkg-query --show --showformat=\'${Package}==${Version}\\n\' '
                         '--no-pager'),
                        suppress_command_logging=True)
                    versions = {}
                    for line in vers_out.split():
                        package, version = line.split('==')
                        versions[package] = version
                    n.dependency_versions = versions

                    # Some versions are especially important and we make them easier
                    # to lookup
                    for package, attr in [('qemu-utils', 'qemu_version'),
                                          ('libvirt-daemon', 'libvirt_version')]:
                        # Versions like:
                        #     libvirt-daemon==7.0.0-3+deb11u3
                        #     qemu-system-x86==1:5.2+dfsg-11+deb11u3
                        #     xxhash==0.8.0-2
                        ver = versions.get(package, 'none')
                        if ':' in ver:
                            ver = ver.split(':')[1]
                        ver = ver.split('-')[0]
                        ver = ver.split('+')[0]

                        elements = ver.split('.')
                        while len(elements) < 3:
                            elements.append('0')
                        int_elements = [int(x) for x in elements]
                        n.__setattr__(attr, int_elements)

                except ProcessExecutionError:
                    LOG.warning('Failed to lookup package versions')

                # Log resources
                n.add_event(
                    EVENT_TYPE_RESOURCES,
                    'updated node resources and package versions',
                    extra=retval,
                    suppress_event_logging=True)
                self.last_logged_resources = time.time()
            return retval

    def _run_inner(self):
        gauges = {
            'updated_at': Gauge('updated_at', 'The last time metrics were updated')
        }

        # Clear out any old metrics entries for this node
        for k, d in etcd.get_all('metrics', None):
            node_name = d['fqdn']
            if node_name == config.NODE_NAME:
                etcd.delete_raw(k)

        # Some versions are static and only looked up at startup
        n = Node.from_db(config.NODE_NAME)
        if not n:
            raise exceptions.NodeShouldExist()

        n.python_version = platform.python_version_tuple()
        n.python_implementation = platform.python_implementation()

        last_metrics = 0
        last_billing = 0

        def update_metrics():
            stats = self._get_stats()
            for metric in stats:
                if metric not in gauges:
                    gauges[metric] = Gauge(metric, '')
                gauges[metric].set(stats[metric])

            etcd.put(
                'metrics', config.NODE_NAME, None,
                {
                    'fqdn': config.NODE_NAME,
                    'timestamp': time.time(),
                    'metrics': stats
                })
            gauges['updated_at'].set_to_current_time()

        def emit_billing_statistics():
            if not config.NODE_IS_NETWORK_NODE:
                return

            for n in network.Networks([], prefilter='active'):
                if not n.provide_nat:
                    continue
                if n.state.value in [dbo.STATE_DELETED, dbo.STATE_ERROR]:
                    continue

                interface = 'egr-%06x-o' % n.vxid
                try:
                    n.add_event(
                        EVENT_TYPE_USAGE, 'usage',
                        extra=util_network.get_interface_statistics(interface),
                        suppress_event_logging=True)
                except exceptions.NoInterfaceStatistics as e:
                    LOG.with_fields({'network': n}).info(
                        'Failed to collect network usage: %s' % e)

        def identify_libvirt_processes():
            # KVM processes are owned by init
            init = psutil.Process(1)
            for child in init.children():
                try:
                    with child.oneshot():
                        m = LIBVIRT_KVM_CMDLINE_RE.match(
                            ' '.join(child.cmdline()))
                        if m:
                            instance_uuid = m.group(1)
                            i = instance.Instance.from_db(instance_uuid)
                            if i:
                                i.kvm_pid = child.pid

                except (psutil.NoSuchProcess, FileNotFoundError):
                    ...

        while daemon.check_abort_path(self.abort_path):
            while not daemon.health_check_nodelock():
                LOG.info('Waiting for nodelock daemon to be healthy')
                time.sleep(1)
                continue

            try:
                if time.time() - last_metrics > 60:
                    update_metrics()
                    last_metrics = time.time()

                if time.time() - last_billing > config.USAGE_EVENT_FREQUENCY:
                    emit_billing_statistics()
                    identify_libvirt_processes()
                    last_billing = time.time()

            except Exception as e:
                util_exceptions.ignore_exception('resource statistics', e)

            self.idle(1)


def main():
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('resources')
    m = Monitor('resources')

    while not daemon.health_check_nodelock():
        LOG.info('Waiting for nodelock daemon to be healthy')
        time.sleep(1)
    LOG.info('nodelock daemon reports healthy')

    m.run()

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    raise SystemExit(0)
