# Make scheduling decisions
import math
import random
import time
import uuid
from collections import defaultdict

from shakenfist_utilities import logs

from shakenfist import etcd
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import networkinterface
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

    def _find_most_matching_networks(self, requested_networks, candidates):
        if not candidates:
            return []

        # Find number of matching networks on each node. We need to be careful
        # how we do this to avoid repeatedly scanning the etcd repository.
        per_node = defaultdict(list)
        for inst in instance.all_instances():
            n = inst.placement
            if n.get('node'):
                per_node[n['node']].append(inst)

        candidates_network_matches = {}
        for n in candidates:
            candidates_network_matches[n] = 0

            # Make a list of networks for the node
            present_networks = []
            for inst in per_node.get(n, []):
                for iface_uuid in inst.interfaces:
                    ni = networkinterface.NetworkInterface.from_db(iface_uuid)
                    if not ni:
                        LOG.with_fields({
                            'instance': inst.uuid,
                            'networkinterface': iface_uuid
                        }).error('Interface missing while attempting schedule')
                    elif ni.network_uuid not in present_networks:
                        present_networks.append(ni.network_uuid)

            # Count the requested networks present on this node
            for network in present_networks:
                if network in requested_networks:
                    candidates_network_matches[n] += 1

        # Store candidate nodes keyed by number of matches
        candidates_by_network_matches = defaultdict(list)
        for n in candidates:
            matches = candidates_network_matches[n]
            candidates_by_network_matches[matches].append(n)

        # Find maximum matches of networks on a node
        max_matches = max(candidates_by_network_matches.keys())

        # Check that the maximum is not just the network node.
        # (Network node always has every network.)
        net_node = get_network_node()
        if (max_matches == 1 and
                candidates_by_network_matches[max_matches][0] == net_node.uuid):
            # No preference, all candidates are a reasonable choice
            return candidates

        # Return list of candidates that has maximum networks
        return candidates_by_network_matches[max_matches]

    def find_candidates(self, inst, network, candidates=None):
        with util_general.RecordedOperation('schedule', inst):
            log_ctx = self.log.with_fields({'instance': inst})

            # Refresh metrics if its too old, or there are no nodes.
            diff = time.time() - self.metrics_updated
            if diff > config.SCHEDULER_CACHE_TIMEOUT or len(self.metrics) == 0:
                self.refresh_metrics()

            if candidates:
                inst.add_event(EVENT_TYPE_AUDIT, 'schedule forced candidates',
                               extra={'candidates': candidates})
                for n in candidates:
                    if n not in self.metrics:
                        raise exceptions.CandidateNodeNotFoundException(n)
            else:
                candidates = []
                for n in self.metrics.keys():
                    candidates.append(n)
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule initial candidates',
                           extra={'candidates': candidates})

            # Ensure all specified nodes are hypervisors
            for c in list(candidates):
                if not self.metrics[c].get('is_hypervisor', False):
                    candidates.remove(c)
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule are hypervisors',
                           extra={'candidates': candidates})

            if not candidates:
                raise exceptions.LowResourceException('No nodes with metrics')

            # Don't use nodes which aren't keeping up with queue jobs
            for c in list(candidates):
                if not self._has_reasonable_queue_state(log_ctx, c):
                    candidates.remove(c)
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule have reasonable queue state',
                           extra={'candidates': candidates})

            # Can we host that many vCPUs?
            for c in list(candidates):
                max_cpu = self.metrics[c].get('cpu_max_per_instance', 0)
                if inst.cpus > max_cpu:
                    candidates.remove(c)
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule have enough actual cpu',
                           extra={'candidates': candidates})
            if not candidates:
                raise exceptions.LowResourceException(
                    'Requested vCPUs exceeds vCPU limit')

            # Do we have enough idle CPU?
            for c in list(candidates):
                if not self._has_sufficient_cpu(log_ctx, inst.cpus, c):
                    candidates.remove(c)
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule have enough idle cpu',
                           extra={'candidates': candidates})
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough idle CPU')

            # Do we have enough idle RAM?
            for c in list(candidates):
                if not self._has_sufficient_ram(log_ctx, inst.memory, c):
                    candidates.remove(c)
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule have enough idle ram',
                           extra={'candidates': candidates})
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough idle RAM')

            # Do we have enough idle disk?
            for c in list(candidates):
                if not self._has_sufficient_disk(log_ctx, inst, c):
                    candidates.remove(c)
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule have enough idle disk',
                           extra={'candidates': candidates})
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough disk space')

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
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule have highest affinity',
                           extra={'candidates': candidates})

            # Order candidates by current CPU load
            by_load = defaultdict(list)
            for c in list(candidates):
                load = math.floor(self.metrics[c].get('cpu_load_1', 0))
                by_load[load].append(c)

            lowest_load = sorted(by_load)[0]
            candidates = by_load[lowest_load]
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule have lowest cpu load',
                           extra={'candidates': candidates})

            # Return a shuffled list of options
            random.shuffle(candidates)
            inst.add_event(EVENT_TYPE_AUDIT, 'schedule final candidates',
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
