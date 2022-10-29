import os
import psutil
import time

from prometheus_client import Gauge
from prometheus_client import start_http_server

from shakenfist import baseobject
from shakenfist.baseobjectmapping import OBJECT_NAMES_TO_CLASSES
from shakenfist.daemons import daemon
from shakenfist.config import config
from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import logutil
from shakenfist import instance
from shakenfist import network
from shakenfist.node import Node
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import network as util_network


LOG, _ = logutil.setup(__name__)


def _get_stats():
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
            util_general.ignore_exception('load average', e)

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

        retval.update({
            'disk_total': total,
            'disk_free': minimum,
            'disk_used': used
        })

        disk_counters = psutil.disk_io_counters()
        retval.update({
            'disk_read_bytes': disk_counters.read_bytes,
            'disk_write_bytes': disk_counters.write_bytes,
        })

        # Network info
        net_counters = psutil.net_io_counters()
        retval.update({
            'network_read_bytes': net_counters.bytes_recv,
            'network_write_bytes': net_counters.bytes_sent,
        })

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

        # Queue health statistics
        node_queue_processing, node_queue_waiting = etcd.get_queue_length(
            config.NODE_NAME)

        retval.update({
            'cpu_total_instance_vcpus': total_instance_vcpus,
            'cpu_total_instance_cpu_time': total_instance_cpu_time,
            'memory_total_instance_max': total_instance_max_memory // 1024,
            'memory_total_instance_actual': total_instance_actual_memory // 1024,
            'instances_total': total_instances,
            'instances_active': total_active_instances,
            'node_queue_processing': node_queue_processing,
            'node_queue_waiting': node_queue_waiting,
        })

        if config.NODE_IS_NETWORK_NODE:
            network_queue_processing, network_queue_waiting = etcd.get_queue_length(
                'networknode')

            retval.update({
                'network_queue_processing': network_queue_processing,
                'network_queue_waiting': network_queue_waiting,
            })

        if config.NODE_IS_EVENTLOG_NODE:
            queued = len(list(etcd.get_all('event', None, limit=10000)))
            retval.update({
                'events_waiting': queued,
            })

        # What object versions do we support?
        for obj in OBJECT_NAMES_TO_CLASSES:
            retval['object_version_%s' % obj] = \
                OBJECT_NAMES_TO_CLASSES[obj].current_version

        n = Node.from_db(config.NODE_NAME)
        n.add_event2('Updated node resources', extra=retval,
                     suppress_event_logging=True)
        return retval


class Monitor(daemon.Daemon):
    def __init__(self, id):
        super(Monitor, self).__init__(id)
        start_http_server(config.PROMETHEUS_METRICS_PORT)

    def run(self):
        LOG.info('Starting')
        gauges = {
            'updated_at': Gauge('updated_at', 'The last time metrics were updated')
        }

        last_metrics = 0
        last_billing = 0

        def update_metrics():
            stats = _get_stats()
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
                },
                ttl=120)
            gauges['updated_at'].set_to_current_time()

        def emit_billing_statistics():
            with util_libvirt.LibvirtConnection() as lc:
                try:
                    for domain in lc.get_sf_domains():
                        if domain.name().startswith('sf:'):
                            instance_uuid = domain.name().split(':')[1]
                            inst = instance.Instance.from_db(instance_uuid)
                            statistics = util_libvirt.extract_statistics(
                                domain)

                            # Add in actual size on disk
                            for disk in inst.block_devices.get('devices', [{}]):
                                disk_path = disk.get('path')
                                disk_device = disk.get('device')
                                if disk_path and disk_device and os.path.exists(disk_path):
                                    # Because nvme disks don't exist as full libvirt
                                    # disks, they are missing from the statistics
                                    # results.
                                    if disk_device not in statistics['disk usage']:
                                        statistics['disk usage'][disk_device] = {}

                                    statistics['disk usage'][disk_device][
                                        'actual bytes on disk'] = os.stat(disk_path).st_size

                            if inst:
                                inst.add_event2(
                                    'usage', extra=statistics,
                                    suppress_event_logging=True)

                except lc.libvirt.libvirtError as e:
                    self.log.warning('Ignoring libvirt error: %s' % e)

            if not config.NODE_IS_NETWORK_NODE:
                return

            for n in network.Networks([baseobject.active_states_filter]):
                if not n.provide_nat:
                    continue

                interface = 'egr-%06x-o' % n.vxid
                try:
                    n.add_event2(
                        'usage', extra=util_network.get_interface_statistics(interface),
                        suppress_event_logging=True)
                except exceptions.NoInterfaceStatistics as e:
                    LOG.with_field('network', n).info(
                        'Failed to collect network usage: %s' % e)

        while not self.exit.is_set():
            try:
                if time.time() - last_metrics > config.SCHEDULER_CACHE_TIMEOUT:
                    update_metrics()
                    last_metrics = time.time()

                if time.time() - last_billing > config.USAGE_EVENT_FREQUENCY:
                    emit_billing_statistics()
                    last_billing = time.time()

                self.exit.wait(1)

            except Exception as e:
                util_general.ignore_exception('resource statistics', e)

        LOG.info('Terminating')
