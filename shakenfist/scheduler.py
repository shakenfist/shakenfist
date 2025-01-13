# Make scheduling decisions
import math
import random
import time
import uuid
from collections import defaultdict

from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist.eventlog import add_event_multi
from shakenfist import exceptions
from shakenfist import instance
from shakenfist.config import config
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import GiB
from shakenfist.node import Node
from shakenfist.node import Nodes
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


# Lookup of the FQDN (called a UUID by the node object) is expensive,
# and the network node doesn't move around, so just do it once here
# and cache the result. This can't be done until config is loaded, so
# the cache is populated by the first caller.
CACHED_NETWORK_NODE = None

UNREASONABLE_QUEUE_LENGTH = 20


def get_network_node():
    global CACHED_NETWORK_NODE

    if CACHED_NETWORK_NODE:
        return CACHED_NETWORK_NODE

    for n in Nodes([], prefilter='active'):
        if n.ip == config.NETWORK_NODE_IP:
            CACHED_NETWORK_NODE = n
            return CACHED_NETWORK_NODE

    raise exceptions.NoNetworkNode('Cannot find network node')


def get_active_node_metrics():
    metrics = {}

    for n in Nodes([], prefilter='active'):
        try:
            new_metrics = etcd.get('metrics', n.uuid, None)
            if new_metrics:
                if time.time() - new_metrics.get('timestamp', 0) < 120:
                    new_metrics = new_metrics.get('metrics', {})
                else:
                    n.add_event(EVENT_TYPE_AUDIT, 'stale metrics from database for node')
                    new_metrics = {}
            else:
                n.add_event(EVENT_TYPE_AUDIT, 'empty metrics from database for node')
                new_metrics = {}
            metrics[n.uuid] = new_metrics

        except exceptions.ReadException:
            n.add_event(EVENT_TYPE_AUDIT, 'refreshing metrics for node failed')

    return metrics


class Scheduler:
    def __init__(self):
        # This UUID doesn't really mean much, except as a way of tracing the
        # behaviour of a single instance of the scheduler object in logs.
        self.__uuid = str(uuid.uuid4())
        self.log = LOG.with_fields({'scheduler_instance': self.__uuid})

        self.metrics = {}
        self.metrics_updated = 0

        self.refresh_metrics()

    def refresh_metrics(self):
        self.metrics = get_active_node_metrics()
        self.metrics_updated = time.time()

    def _has_reasonable_queue_state(self, log_ctx, node):
        waiting = self.metrics[node].get('node_queue_waiting', 0)
        if waiting > UNREASONABLE_QUEUE_LENGTH:
            log_ctx.with_fields({
                'node': node,
                'node_queue_waiting': waiting
            }).debug('Excluding node with many queued jobs')
            return False

        return True

    def _has_sufficient_cpu(self, log_ctx, cpus, node):
        hard_max_cpus = (self.metrics[node].get(
            'cpu_max', 0) * config.CPU_OVERCOMMIT_RATIO)
        current_cpu = self.metrics[node].get('cpu_total_instance_vcpus', 0)

        if current_cpu + cpus > hard_max_cpus:
            log_ctx.with_fields({
                'node': node,
                'current_cpus': current_cpu,
                'requested_cpus': cpus,
                'hard_max_cpus': hard_max_cpus
            }).debug('Scheduling on node would exceed hard maximum CPUs')
            return False

        return True

    def _has_sufficient_ram(self, log_ctx, memory, node):
        # There are two things to track here... We must always have
        # RAM_SYSTEM_RESERVATION gb of RAM for operating system tasks -- assume
        # there is no overlap with existing VMs when checking this. Note as
        # well that metrics are in MB...
        available = (self.metrics[node].get('memory_available', 0) -
                     (config.RAM_SYSTEM_RESERVATION * 1024))
        if available - memory < 0.0:
            log_ctx.with_fields({
                'node': node,
                'available': available,
                'requested_memory': memory
            }).debug('Insufficient memory')
            return False

        # ...Secondly, if we're using KSM and over committing memory, we
        # shouldn't overcommit more than by RAM_OVERCOMMIT_RATIO
        instance_memory = (
            self.metrics[node].get('memory_total_instance_actual', 0) + memory)
        if (instance_memory / self.metrics[node].get('memory_max', 0) >
                config.RAM_OVERCOMMIT_RATIO):
            log_ctx.with_fields({
                'node': node,
                'instance_memory': instance_memory,
                'memory_max': self.metrics[node].get('memory_max', 0),
                'overcommit_ratio': config.RAM_OVERCOMMIT_RATIO
            }).debug('KSM overcommit ratio exceeded')
            return False

        return True

    def _has_sufficient_disk(self, log_ctx, inst, node):
        requested_disk = 0
        for disk in inst.disk_spec:
            # TODO(mikal): this ignores "sizeless disks", that is ones that
            # are exactly the size of their base image, for example CD ROMs.
            if 'size' in disk:
                if not disk['size'] is None:
                    requested_disk += int(disk['size'])

        disk_free = int(self.metrics[node].get('disk_free_instances', '0')) / GiB
        disk_free -= config.MINIMUM_FREE_DISK
        if requested_disk > disk_free:
            log_ctx.with_fields({
                'node': node,
                'requested_disk_gb': requested_disk,
                'disk_free_gb': disk_free,
            }).debug('Node has insufficient disk')
            return False
        return True

    def _has_idle_disk_bandwidth(self, log_ctx, inst, node):
        # We also avoid starting new instances on hypervisors with busy disk.
        # busy_time is in milliseconds per second, so a value of 1,000 is 100%
        # busy. You can record more than 100% if there is more than one disk
        # in the system doing IO at the time.
        busy_time = int(self.metrics[node].get('disk_busy_time', '0'))
        if busy_time > 900:
            log_ctx.with_fields({
                'node': node
            }).debug('Scheduling on node would maximum disk bandwidth')
            return False

        return True

    def _log_and_raise_on_error(self, related_objects, stage, candidates):
        if not candidates:
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                f'schedule has no candidates at stage {stage}, aborting',
                extra={'candidates': candidates})
            raise exceptions.LowResourceException(
                f'No nodes remaining at scheduling stage {stage}')

        add_event_multi(
            EVENT_TYPE_AUDIT, related_objects,
            f'schedule at stage {stage}', extra={'candidates': candidates})

    def find_candidates(self, inst, candidates=None):
        related_objects = [inst]
        if candidates:
            for node_uuid in candidates:
                related_objects.append(('node', node_uuid))
        add_event_multi(
            EVENT_TYPE_AUDIT, related_objects, 'started scheduling')

        with util_general.RecordedOperation('schedule', inst):
            log_ctx = self.log.with_fields({'instance': inst})

            # Refresh metrics if its too old, or there are no nodes.
            diff = time.time() - self.metrics_updated
            if diff > config.SCHEDULER_CACHE_TIMEOUT or len(self.metrics) == 0:
                self.refresh_metrics()

            if candidates:
                add_event_multi(
                    EVENT_TYPE_AUDIT, related_objects,
                    'schedule forced candidates',
                    extra={'candidates': candidates})
                for n in candidates:
                    if n not in self.metrics:
                        add_event_multi(
                            EVENT_TYPE_AUDIT, related_objects,
                            f'schedule candidate {n} lacks metrics, aborting',
                            extra={'candidates': candidates})
                        raise exceptions.CandidateNodeNotFoundException(n)
            else:
                candidates = []
                for n in self.metrics.keys():
                    candidates.append(n)
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule initial candidates',
                extra={'candidates': candidates})

            # Ensure all specified nodes are hypervisors
            for c in list(candidates):
                if not self.metrics[c].get('is_hypervisor', False):
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'is_hypervisor', candidates)

            # Don't use nodes which aren't keeping up with queue jobs
            for c in list(candidates):
                if not self._has_reasonable_queue_state(log_ctx, c):
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'queue_state', candidates)

            # Can we host that many vCPUs?
            for c in list(candidates):
                max_cpu = self.metrics[c].get('cpu_max_per_instance', 0)
                if inst.cpus > max_cpu:
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'cpu_max_per_instance', candidates)

            # Do we have enough idle CPU?
            for c in list(candidates):
                if not self._has_sufficient_cpu(log_ctx, inst.cpus, c):
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'sufficient_idle_cpu', candidates)

            # Do we have enough idle RAM?
            for c in list(candidates):
                if not self._has_sufficient_ram(log_ctx, inst.memory, c):
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'sufficient_idle_memory', candidates)

            # Do we have enough free disk?
            for c in list(candidates):
                if not self._has_sufficient_disk(log_ctx, inst, c):
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'sufficient_free_disk', candidates)

            # Are the disks really busy?
            for c in list(candidates):
                if not self._has_idle_disk_bandwidth(log_ctx, inst, c):
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'sufficient_idle_disk', candidates)

            # Filter by affinity, if any has been specified
            by_affinity = defaultdict(list)
            requested_affinity = inst.affinity

            for c in list(candidates):
                n = Node.from_db(c)
                if n:
                    affinity = 0
                    instances = n.instances
                    for instance_uuid in instances:
                        i = instance.Instance.from_db(instance_uuid)
                        if not i:
                            continue
                        if i.uuid == inst.uuid:
                            continue
                        if not i.tags:
                            continue
                        if i.namespace != inst.namespace:
                            continue

                        for tag, val in requested_affinity.items():
                            if tag in i.tags:
                                affinity += int(val)

                    by_affinity[affinity].append(c)

            highest_affinity = sorted(by_affinity, reverse=True)[0]
            candidates = by_affinity[highest_affinity]
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule have highest affinity',
                extra={'candidates': candidates})

            # Order candidates by current CPU load
            by_load = defaultdict(list)
            for c in list(candidates):
                load = math.floor(self.metrics[c].get('cpu_load_1', 0))
                by_load[load].append(c)

            lowest_load = sorted(by_load)[0]
            candidates = by_load[lowest_load]
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule have lowest cpu load',
                extra={'candidates': candidates})

            # Return a shuffled list of options
            random.shuffle(candidates)
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule final candidates',
                extra={'candidates': candidates})
            return candidates

    def summarize_resources(self):
        # Refresh metrics if its too old, or there are no nodes.
        diff = time.time() - self.metrics_updated
        if diff > config.SCHEDULER_CACHE_TIMEOUT or len(self.metrics) == 0:
            self.refresh_metrics()

        # Only hypervisors with reasonable queue lengths are candidates
        resources = {
            'total': {
                'cpu_available': 0,
                'ram_available': 0
            },
            'per_node': {}
        }

        for n in self.metrics.keys():
            if not self.metrics[n].get('is_hypervisor', False):
                continue

            if (self.metrics[n].get('node_queue_waiting', 0) >
                    UNREASONABLE_QUEUE_LENGTH):
                continue

            resources['per_node'][n] = {}

            # CPU
            resources['per_node'][n]['cpu_max_per_instance'] = \
                self.metrics[n].get('cpu_max_per_instance', 0)

            hard_max_cpus = (self.metrics[n].get(
                'cpu_max', 0) * config.CPU_OVERCOMMIT_RATIO)
            current_cpu = self.metrics[n].get('cpu_total_instance_vcpus', 0)
            resources['per_node'][n]['cpu_available'] = hard_max_cpus - current_cpu
            resources['total']['cpu_available'] += resources['per_node'][n]['cpu_available']

            resources['per_node'][n]['cpu_load_1'] = self.metrics[n].get(
                'cpu_load_1', 0)
            resources['per_node'][n]['cpu_load_5'] = self.metrics[n].get(
                'cpu_load_5', 0)
            resources['per_node'][n]['cpu_load_15'] = self.metrics[n].get(
                'cpu_load_15', 0)

            # Memory
            resources['per_node'][n]['ram_max_per_instance'] = \
                (self.metrics[n].get('memory_available', 0) -
                 (config.RAM_SYSTEM_RESERVATION * 1024))
            resources['per_node'][n]['ram_max'] = \
                self.metrics[n].get('memory_max', 0) * \
                config.RAM_OVERCOMMIT_RATIO
            resources['per_node'][n]['ram_available'] = \
                (self.metrics[n].get('memory_max', 0) * config.RAM_OVERCOMMIT_RATIO -
                 self.metrics[n].get('memory_total_instance_actual', 0))
            resources['total']['ram_available'] += resources['per_node'][n]['ram_available']

            # Disk
            disk_free = int(self.metrics[n].get(
                'disk_free_instances', '0')) / GiB
            disk_free -= config.MINIMUM_FREE_DISK
            resources['per_node'][n]['disk_available'] = disk_free

            # Instance count
            resources['per_node'][n]['instances_total'] = self.metrics[n].get(
                'instances_total', 0)
            resources['per_node'][n]['instances_active'] = self.metrics[n].get(
                'instances_active', 0)

        return resources
