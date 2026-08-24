import glob
import math
import os
import platform
import re
import threading
import time

import psutil
from prometheus_client import Gauge
from prometheus_client import start_http_server
from shakenfist_utilities import logs  # noreorder

from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist import instance
from shakenfist import node_health
from shakenfist.network import network
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_RESOURCES
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.constants import EVENT_TYPE_USAGE
from shakenfist.constants import get_object_class
from shakenfist.constants import METRICS_DELTA_PER_SECOND_SUFFIX
from shakenfist.constants import OBJECT_NAMES_TO_CLASSES
from shakenfist.daemons import daemon
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.node import Node
from shakenfist.operations.baseoperation import get_all_background_node_queues
from shakenfist.operations.baseoperation import get_all_network_queues
from shakenfist.operations.baseoperation import get_node_network_queues
from shakenfist.operations.baseoperation import get_node_user_facing_node_queues
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)


# /usr/bin/kvm -name guest=sf:ec069949-eb19-4f7a-aaf2-a6020c877b95,...
LIBVIRT_KVM_CMDLINE_RE = re.compile('.* guest=sf:([a-z0-9\\-]+).*')


def _parse_cpu_list(cpu_list):
    # Parse the kernel's CPU list format ('0-15', '0-3,8-11', '5') into a
    # list of CPU (thread) numbers.
    cpus = []
    for chunk in cpu_list.strip().split(','):
        if not chunk:
            continue
        if '-' in chunk:
            start, end = chunk.split('-')
            cpus.extend(range(int(start), int(end) + 1))
        else:
            cpus.append(int(chunk))
    return cpus


def _count_physical_cores(thread_ids, sysfs_root='/sys'):
    # A physical core's threads share a topology core_id; dedup to count
    # cores without assuming how many threads each core has.
    core_ids = set()
    for t in thread_ids:
        with open(f'{sysfs_root}/devices/system/cpu/cpu{t}/topology/core_id') as f:
            core_ids.add(int(f.read().strip()))
    return len(core_ids)


def _get_hybrid_core_counts(sysfs_root='/sys'):
    """Return performance and efficiency core counts on hybrid CPUs.

    On Intel hybrid parts /sys/devices/cpu_core/cpus and
    /sys/devices/cpu_atom/cpus list the threads of the performance and
    efficiency cores respectively. Returns an empty dict on non-hybrid
    hardware (paths absent) or on any parse failure -- these fields are
    informational and nothing in scheduling consumes them yet.
    """
    core_path = os.path.join(sysfs_root, 'devices/cpu_core/cpus')
    atom_path = os.path.join(sysfs_root, 'devices/cpu_atom/cpus')
    if not (os.path.exists(core_path) and os.path.exists(atom_path)):
        return {}

    try:
        with open(core_path) as f:
            performance_threads = _parse_cpu_list(f.read())
        with open(atom_path) as f:
            efficiency_threads = _parse_cpu_list(f.read())
        return {
            'cpu_cores_performance': _count_physical_cores(
                performance_threads, sysfs_root=sysfs_root),
            'cpu_cores_efficiency': _count_physical_cores(
                efficiency_threads, sysfs_root=sysfs_root),
        }
    except Exception as e:
        util_exceptions.ignore_exception('hybrid cpu topology', e)
        return {}


def _compute_reservations(cpu_cores, cpu_threads, cpu_reservation_threads,
                          ram_reservation_gb, memory_total_mb):
    """Compute schedulable capacity after the node's reservations.

    The CPU reservation is a single absolute per-node value expressed in
    hardware threads, subtracted directly from the thread count scheduling
    accounts in. The informational cpu_cores_reserved metric is derived
    back into physical cores at ceil(threads / cores) threads per core,
    which errs conservative on hybrid parts where threads-per-core is an
    average.

    The memory reservation is capped at half the machine so that a small
    node carrying every role (the single-node deployment case) can still
    schedule instances -- the analogue of cpu_schedulable's floor of one.
    It also bounds an oversized operator override.
    """
    memory_reserved_mb = int(ram_reservation_gb * 1024)
    if memory_total_mb:
        memory_reserved_mb = min(memory_reserved_mb, memory_total_mb // 2)

    threads_per_core = math.ceil(cpu_threads / cpu_cores)
    cpu_cores_reserved = math.ceil(cpu_reservation_threads / threads_per_core)
    return {
        'cpu_cores_reserved': cpu_cores_reserved,
        'cpu_schedulable': max(1, cpu_threads - cpu_reservation_threads),
        'cpu_cores_schedulable': max(1, cpu_cores - cpu_cores_reserved),
        'memory_reserved_mb': memory_reserved_mb,
    }


def _safe_metric_name(name):
    name = name.lower()
    return re.sub(r'[^a-z0-9_]', '_', name)


# Every Shaken Fist daemon runs as its own systemd unit named sf-*.service
# (grouped under sf.target), so the set of Shaken Fist processes is the set of
# processes in those units' cgroups. The first glob is the cgroup v2 unified
# hierarchy, the second the cgroup v1 systemd named hierarchy.
SF_UNIT_CGROUP_GLOBS = [
    '/sys/fs/cgroup/system.slice/sf-*.service/cgroup.procs',
    '/sys/fs/cgroup/systemd/system.slice/sf-*.service/cgroup.procs'
]


def _sf_daemon_pids():
    """Return the pids of every process in a sf-*.service systemd unit."""
    pids = set()
    for pattern in SF_UNIT_CGROUP_GLOBS:
        for path in glob.glob(pattern):
            try:
                with open(path) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            pids.add(int(line))
            except OSError:
                # The unit stopped between the glob and the read.
                continue
    return sorted(pids)


def _emit_process_metrics(p, n):
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


def _collect_process_metrics(n):
    """How much CPU time have the Shaken Fist components consumed?

    We enumerate the sf-*.service cgroups rather than walking process
    parentage: each daemon is its own systemd unit, so our parent is systemd
    itself and its children are every service on the node -- including the
    guest VMs under libvirtd (issue 3860).
    """
    process_metrics = {}
    for pid in _sf_daemon_pids():
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                process_metrics.update(_emit_process_metrics(p, n))
        except (psutil.NoSuchProcess, FileNotFoundError):
            ...
    return process_metrics


class Monitor(daemon.Daemon):
    def __init__(self, id):
        super().__init__(id)
        start_http_server(config.RESOURCES_METRICS_PORT)

        self.last_logged_resources = 0

    def _get_stats(self):
        # This periodic sweep can race deletion of this node from the cluster
        # (issue 3591). That is expected, not an error, so suppress the
        # "non-existent object" audit event and skip the sweep entirely --
        # publishing metrics or events for a deleted node would recreate rows
        # for it.
        n = Node.from_db(config.NODE_NAME, suppress_failure_audit=True)
        if not n:
            LOG.info('Skipping stats collection, node absent from database')
            return None

        old_metrics = mariadb.get_node_metrics(config.NODE_UUID) or {}
        timestamp = time.time()

        with util_libvirt.LibvirtConnection() as lc:
            # What's special about this node?
            retval = {
                # is_etcd_master and is_eventlog_node are vestigial
                # (pinned False, removed next release); node_attributes is
                # the authoritative store for role flags, this metrics copy
                # is informational only.
                'is_etcd_master': False,
                'is_hypervisor': config.NODE_IS_HYPERVISOR,
                'is_network_node': config.NODE_IS_NETWORK_NODE,
                'is_eventlog_node': False,
                'is_database_node': config.NODE_IS_DATABASE_NODE,
            }

            # CPU info
            present_cpus, _, available_cpus = lc.get_cpu_map()
            retval.update({
                'cpu_max': present_cpus,
                'cpu_available': available_cpus,
            })

            retval['cpu_max_per_instance'] = lc.get_max_vcpus()

            # CPU topology and system reservations. cpu_cores is physical
            # cores and cpu_threads is logical CPUs (cpu_max above is also
            # logical CPUs, from libvirt, and is retained for compatibility).
            # Scheduling accounts in threads, and the reservation is a single
            # absolute per-node value in threads. psutil can return None for
            # either count, in which case we publish nothing and the
            # scheduler falls back to its unreserved arithmetic for this node.
            cpu_cores = psutil.cpu_count(logical=False)
            cpu_threads = psutil.cpu_count(logical=True)
            if cpu_cores and cpu_threads:
                retval.update({
                    'cpu_cores': cpu_cores,
                    'cpu_threads': cpu_threads,
                })
                retval.update(_compute_reservations(
                    cpu_cores, cpu_threads,
                    config.NODE_CPU_RESERVATION_THREADS,
                    config.NODE_RAM_RESERVATION_GB,
                    psutil.virtual_memory().total // 1024 // 1024))
            retval.update(_get_hybrid_core_counts())

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

            # Kernel Shared Memory (KSM) information. The kernels before Debian 13 and
            # Ubuntu 24.04 (so about 6.8) just had files with numeric values in them.
            # So, we only include files with single line numeric values in them here.
            ksm_details = {}
            for ent in os.listdir('/sys/kernel/mm/ksm'):
                with open('/sys/kernel/mm/ksm/%s' % ent) as f:
                    d = f.read().rstrip()
                    if '\n' in d:
                        continue
                    try:
                        ksm_details[f'memory_ksm_{ent}'] = int(d)
                    except ValueError:
                        pass
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
                'disk_used': used,
                'disk_reservation_gb': config.NODE_DISK_RESERVATION_GB
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
                    retval[counter + METRICS_DELTA_PER_SECOND_SUFFIX] = \
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

            # Queue health statistics
            node_queue_waiting = 0
            node_queue_processing = 0
            node_queue_deferred = 0
            node_background_queue_waiting = 0
            node_background_queue_processing = 0
            node_background_queue_deferred = 0

            def _log_and_update_metrics_for_queue(
                    queue, log_prefix):
                processing, queued, deferred = mariadb.get_work_queue_length(
                    queue)
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

            for queue in get_node_user_facing_node_queues(config.NODE_UUID):
                processing, queued, deferred = _log_and_update_metrics_for_queue(
                    queue, 'User facing')

                node_queue_processing += processing
                node_queue_waiting += queued
                node_queue_deferred += deferred

            for queue in get_all_background_node_queues(config.NODE_UUID):
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

            # Per-node network queues ({node_uuid}-network-*) are drained by
            # this node's net-worker regardless of whether this node is the
            # elected network node, so emit their metrics on every node.
            node_network_waiting = 0
            node_network_processing = 0
            node_network_deferred = 0

            for queue in get_node_network_queues(config.NODE_UUID):
                processing, queued, deferred = _log_and_update_metrics_for_queue(
                    queue, 'Per-node network')

                node_network_waiting += queued
                node_network_processing += processing
                node_network_deferred += deferred

            retval.update({
                'node_network_queue_processing': node_network_processing,
                'node_network_queue_waiting': node_network_waiting,
                'node_network_queue_deferred': node_network_deferred
            })

            if config.NODE_IS_NETWORK_NODE:
                # Cluster-wide networknode-* queues are only drained by the
                # elected network node's net-worker, so only emit those metrics
                # here.  Summing them with the per-node metrics would create a
                # misleading combined total on non-network nodes.
                networknode_waiting = 0
                networknode_processing = 0
                networknode_deferred = 0

                for queue in get_all_network_queues():
                    processing, queued, deferred = _log_and_update_metrics_for_queue(
                        queue, 'Network node')

                    networknode_waiting += queued
                    networknode_processing += processing
                    networknode_deferred += deferred

                retval.update({
                    'networknode_queue_processing': networknode_processing,
                    'networknode_queue_waiting': networknode_waiting,
                    'networknode_queue_deferred': networknode_deferred
                })

            # What object versions do we support?
            for obj in OBJECT_NAMES_TO_CLASSES:
                retval['object_version_%s' % obj] = \
                    get_object_class(obj).current_version

            if time.time() - self.last_logged_resources > 300:
                # Record SF process metrics
                n.process_metrics = _collect_process_metrics(n)

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

    def _run_health_checks(self, checks, types_by_identity):
        # Runs in its own daemon thread (see _run_inner). Evaluates the node's
        # storage-dependency health every NODE_HEALTH_CHECK_INTERVAL and, on
        # failure, marks the node errored. Sleeps in one-second slices so it
        # stops promptly when the daemon aborts.
        #
        # The gauge is created once here (re-registering a Gauge name raises)
        # and exposes on the resources metrics port that _run_inner already
        # started -- the scrapeable companion to the health event apply_result
        # records, so a dead-storage node is visible to Prometheus, not only
        # in a node's event history.
        health_gauge = Gauge(
            'node_resource_health',
            "1 if all of this node's storage-dependency health checks pass, "
            'else 0')
        while daemon.check_abort_path(self.abort_path):
            try:
                result = node_health.evaluate(checks, types_by_identity)
                health_gauge.set(1.0 if result.healthy else 0.0)
                # Like _get_stats, this lookup can race deletion of this node
                # from the cluster (issue 3591) -- that's expected, not an
                # audit-worthy error.
                n = Node.from_db(config.NODE_NAME, suppress_failure_audit=True)
                if n:
                    node_health.apply_result(n, result)
            except Exception as e:
                util_exceptions.ignore_exception('node health check', e)

            for _ in range(max(1, config.NODE_HEALTH_CHECK_INTERVAL)):
                if not daemon.check_abort_path(self.abort_path):
                    break
                time.sleep(1)

    def _run_inner(self):
        gauges = {
            'updated_at': Gauge('updated_at', 'The last time metrics were updated')
        }

        # Clear out any old metrics entries for this node.
        for d in mariadb.get_all_node_metrics():
            if (d.get('node_uuid') == config.NODE_UUID
                    or d.get('fqdn') == config.NODE_NAME):
                mariadb.delete_node_metrics(d['node_uuid'])

        # Some versions are static and only looked up at startup
        n = Node.from_db(config.NODE_NAME)
        if not n:
            raise exceptions.NodeShouldExist()

        n.python_version = platform.python_version_tuple()
        n.python_implementation = platform.python_implementation()

        # Node resource health runs in its own thread, not this loop: a probe
        # can block up to the timeout the first time a path hangs (a hard NFS
        # mount blocks rather than erroring), and this loop holds the nodelock
        # and drives metrics -- a stall here would time out other nodelock
        # waiters. The checks are built once from this node's capabilities.
        health_checks, health_types = node_health.build_for_this_node()
        # Provision each probed directory once, here, before the probe thread
        # starts -- so a not-yet-created subdir (e.g. uploads on a node that
        # has never received an upload) is never misread as a MISSING fault,
        # and an ENOENT during probing unambiguously means the store vanished.
        for check in health_checks:
            check.ensure_present()
        threading.Thread(
            target=self._run_health_checks, name='node-health',
            args=(health_checks, health_types), daemon=True).start()

        last_metrics = 0
        last_billing = 0

        def update_metrics():
            stats = self._get_stats()
            if stats is None:
                # The sweep raced deletion of this node; nothing to publish.
                return
            for metric in stats:
                if metric not in gauges:
                    gauges[metric] = Gauge(metric, '')
                gauges[metric].set(stats[metric])

            mariadb.upsert_node_metrics(
                config.NODE_UUID, config.NODE_NAME,
                time.time(), stats)
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
            self.wait_for_nodelock()

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

    daemon.force_clean_exit()
