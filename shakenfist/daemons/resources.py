import logging
from logging import handlers as logging_handlers
import os
import psutil
import setproctitle
import time

from prometheus_client import Gauge
from prometheus_client import start_http_server

from shakenfist import config
from shakenfist import db
from shakenfist import util


LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


def _get_stats():
    libvirt = util.get_libvirt()
    retval = {}
    conn = libvirt.open(None)

    # CPU info
    present_cpus, _, available_cpus = conn.getCPUMap()
    retval.update({
        'cpu_max': present_cpus,
        'cpu_available': available_cpus,
    })

    retval['cpu_max_per_instance'] = conn.getMaxVcpus(None)

    # This is disable as data we don't currently use
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
    except Exception:
        pass

    # Memory info - libvirt returns memory in KiB
    memory_status = conn.getMemoryStats(
        libvirt.VIR_NODE_MEMORY_STATS_ALL_CELLS)
    retval.update({
        'memory_max': memory_status['total'] // 1024,
        'memory_available': memory_status['free'] // 1024,
    })

    # Disk info
    s = os.statvfs(config.parsed.get('STORAGE_PATH'))
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
        active = guest.isActive() == 1
        _, maxmem, mem, cpus, cpu_time = guest.info()

        total_instances += 1
        if active:
            total_active_instances += 1

        total_instance_max_memory += maxmem
        total_instance_actual_memory += mem
        total_instance_vcpus += cpus
        total_instance_cpu_time += cpu_time

    retval.update({
        'cpu_total_instance_vcpus': total_instance_vcpus,
        'cpu_total_instance_cpu_time': total_instance_cpu_time,
        'memory_total_instance_max_memory': total_instance_max_memory // 1024,
        'memory_total_instance_actual_memory': total_instance_actual_memory // 1024,
        'instances_total': total_instances,
        'instances_active': total_active_instances,
    })

    return retval


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf resources')
        start_http_server(config.parsed.get('PROMETHEUS_METRICS_PORT'))

    def run(self):
        gauges = {'updated_at': Gauge(
            'updated_at', 'The last time metrics were updated')}

        while True:
            try:
                stats = _get_stats()
                for metric in stats:
                    if metric not in gauges:
                        gauges[metric] = Gauge(metric, '')
                    gauges[metric].set(stats[metric])
                db.update_metrics_bulk(stats)

                gauges['updated_at'].set_to_current_time()

            except Exception as e:
                LOG.error('Failed to collect resource statistics: %s' % e)

            time.sleep(config.parsed.get('SCHEDULER_CACHE_TIMEOUT'))
