import os
import psutil
import time

from prometheus_client import Gauge
from prometheus_client import start_http_server

from shakenfist.daemons import daemon
from shakenfist.config import config
from shakenfist import db
from shakenfist import logutil
from shakenfist import util


LOG, _ = logutil.setup(__name__)


def _get_stats():
    libvirt = util.get_libvirt()
    retval = {}
    conn = libvirt.open('qemu:///system')

    # CPU info
    present_cpus, _, available_cpus = conn.getCPUMap()
    retval.update({
        'cpu_max': present_cpus,
        'cpu_available': available_cpus,
    })

    retval['cpu_max_per_instance'] = conn.getMaxVcpus(None)

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
        util.ignore_exception('load average', e)

    # System memory info, converting bytes to mb
    stats = psutil.virtual_memory()
    retval.update({
        'memory_max': stats.total // 1024 // 1024,
        'memory_available': stats.available // 1024 // 1024
    })

    # libvirt memory info, converting kb to mb
    memory_status = conn.getMemoryStats(
        libvirt.VIR_NODE_MEMORY_STATS_ALL_CELLS)
    retval.update({
        'memory_max_libvirt': memory_status['total'] // 1024,
        'memory_available_libvirt': memory_status['free'] // 1024,
    })

    # Kernel Shared Memory (KSM) information
    ksm_details = {}
    for ent in os.listdir('/sys/kernel/mm/ksm'):
        with open('/sys/kernel/mm/ksm/%s' % ent) as f:
            ksm_details['memory_ksm_%s' % ent] = int(f.read().rstrip())
    retval.update(ksm_details)

    # Disk info
    s = os.statvfs(config.get('STORAGE_PATH'))
    disk_counters = psutil.disk_io_counters()
    retval.update({
        'disk_total': s.f_frsize * s.f_blocks,
        'disk_free': s.f_frsize * s.f_bavail,
        'disk_used': s.f_frsize * (s.f_blocks - s.f_bfree),
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

    for guest in conn.listAllDomains():
        try:
            active = guest.isActive() == 1
            if active:
                _, maxmem, mem, cpus, cpu_time = guest.info()

        except libvirt.libvirtError as e:
            LOG.debug('During resource calc ignored libvirt error: %s' % e)
            active = False

        if active:
            total_instances += 1
            total_active_instances += 1
            total_instance_max_memory += maxmem
            total_instance_actual_memory += mem
            total_instance_vcpus += cpus
            total_instance_cpu_time += cpu_time

    # Queue health statistics
    node_queue_processing, node_queue_waiting = db.get_queue_length(
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

    if util.is_network_node():
        network_queue_processing, network_queue_waiting = db.get_queue_length(
            'networknode')

        retval.update({
            'network_queue_processing': network_queue_processing,
            'network_queue_waiting': network_queue_waiting,
        })

    return retval


class Monitor(daemon.Daemon):
    def __init__(self, id):
        super(Monitor, self).__init__(id)
        start_http_server(config.get('PROMETHEUS_METRICS_PORT'))

    def run(self):
        LOG.info('Starting')
        gauges = {'updated_at': Gauge('updated_at',
                                      'The last time metrics were updated')
                  }

        last_metrics = 0

        def update_metrics():
            global last_metrics

            stats = _get_stats()
            for metric in stats:
                if metric not in gauges:
                    gauges[metric] = Gauge(metric, '')
                gauges[metric].set(stats[metric])

            db.update_metrics_bulk(stats)
            gauges['updated_at'].set_to_current_time()

        while True:
            try:
                jobname, _ = db.dequeue('%s-metrics' % config.NODE_NAME)
                if jobname:
                    if time.time() - last_metrics > 2:
                        update_metrics()
                        last_metrics = time.time()
                    db.resolve('%s-metrics' % config.NODE_NAME, jobname)
                else:
                    time.sleep(0.2)

                timer = time.time() - last_metrics
                if timer > config.get('SCHEDULER_CACHE_TIMEOUT'):
                    update_metrics()
                    last_metrics = time.time()

            except Exception as e:
                util.ignore_exception('resource statistics', e)
