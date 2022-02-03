# Make scheduling decisions

from collections import defaultdict
import math
import random
import time
import uuid

from shakenfist.config import config
from shakenfist.constants import GiB
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import logutil
from shakenfist.metrics import get_active_node_metrics
from shakenfist import networkinterface
from shakenfist.node import (
    Nodes, active_states_filter as node_active_states_filter)
from shakenfist.util import general as util_general


LOG, _ = logutil.setup(__name__)


# Lookup of the FQDN (called a UUID by the node object) is expensive,
# and the network node doesn't move around, so just do it once here
# and cache the result. This can't be done until config is loaded, so
# the cache is populated by the first caller.
CACHED_NETWORK_NODE = None


def get_network_node():
    global CACHED_NETWORK_NODE

    if CACHED_NETWORK_NODE:
        return CACHED_NETWORK_NODE

    for n in Nodes([node_active_states_filter]):
        if n.ip == config.NETWORK_NODE_IP:
            CACHED_NETWORK_NODE = n
            return CACHED_NETWORK_NODE

    raise exceptions.NoNetworkNode('Cannot find network node')


class Scheduler(object):
    def __init__(self):
        # This UUID doesn't really mean much, except as a way of tracing the
        # behaviour of a single instance of the scheduler object in logs.
        self.__uuid = str(uuid.uuid4())
        self.log = LOG.with_field('scheduler_instance', self.__uuid)

        self.metrics = {}
        self.metrics_updated = 0

        self.refresh_metrics()

    def refresh_metrics(self):
        self.metrics = get_active_node_metrics()
        self.metrics_updated = time.time()

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

        disk_free = int(self.metrics[node].get(
            'disk_free_instances', '0')) / GiB
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
        for inst in instance.Instances([]):
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

    def place_instance(self, inst, network, candidates=None):
        with util_general.RecordedOperation('schedule', inst):
            log_ctx = self.log.with_object(inst)

            # Refresh metrics if its too old, or there are no nodes.
            diff = time.time() - self.metrics_updated
            log_ctx.debug(('Metrics are %.02f seconds old, max is %.02f seconds. '
                           'Cache has %d elements.'),
                          diff, config.SCHEDULER_CACHE_TIMEOUT, len(self.metrics))
            if diff > config.SCHEDULER_CACHE_TIMEOUT or len(self.metrics) == 0:
                self.refresh_metrics()
                log_ctx.debug('Cache has %d elements after refresh.',
                              len(self.metrics))

            if candidates:
                inst.add_event2('schedule forced candidates',
                                extra={'candidates': candidates})
                for n in candidates:
                    if n not in self.metrics:
                        raise exceptions.CandidateNodeNotFoundException(n)
            else:
                candidates = []
                for n in self.metrics.keys():
                    candidates.append(n)
            inst.add_event2('schedule initial candidates',
                            extra={'candidates': candidates})

            # Ensure all specified nodes are hypervisors
            for n in list(candidates):
                if not self.metrics[n].get('is_hypervisor', False):
                    candidates.remove(n)
            inst.add_event2('schedule are hypervisors',
                            extra={'candidates': candidates})

            if not candidates:
                raise exceptions.LowResourceException('No nodes with metrics')

            # Can we host that many vCPUs?
            for n in list(candidates):
                max_cpu = self.metrics[n].get('cpu_max_per_instance', 0)
                if inst.cpus > max_cpu:
                    candidates.remove(n)
            inst.add_event2('schedule have enough actual cpu',
                            extra={'candidates': candidates})
            if not candidates:
                raise exceptions.LowResourceException(
                    'Requested vCPUs exceeds vCPU limit')

            # Do we have enough idle CPU?
            for n in list(candidates):
                if not self._has_sufficient_cpu(log_ctx, inst.cpus, n):
                    candidates.remove(n)
            inst.add_event2('schedule have enough idle cpu',
                            extra={'candidates': candidates})
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough idle CPU')

            # Do we have enough idle RAM?
            for n in list(candidates):
                if not self._has_sufficient_ram(log_ctx, inst.memory, n):
                    candidates.remove(n)
            inst.add_event2('schedule have enough idle ram',
                            extra={'candidates': candidates})
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough idle RAM')

            # Do we have enough idle disk?
            for n in list(candidates):
                if not self._has_sufficient_disk(log_ctx, inst, n):
                    candidates.remove(n)
            inst.add_event2('schedule have enough idle disk',
                            extra={'candidates': candidates})
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough disk space')

            # Calc pseudo CPU load from inst affinity
            pseudo_load = defaultdict(int)
            cpu_affinity = inst.affinity.get('cpu')
            if cpu_affinity:
                for i in instance.Instances([instance.healthy_states_filter]):
                    if i.uuid == inst.uuid or not i.tags:
                        continue
                    for tag, val in cpu_affinity.items():
                        if tag in i.tags:
                            # Allow for unplaced healthy instances
                            n = i.placement.get('node')
                            if n:
                                pseudo_load[n] += int(val)

                inst.add_event2('schedule pseudo load',
                                extra=dict(pseudo_load))

            # Order candidates by current CPU load
            by_load = defaultdict(list)
            for n in list(candidates):
                load = math.floor(self.metrics[n].get('cpu_load_1', 0))
                load -= pseudo_load.get(n, 0)
                by_load[load].append(n)

            lowest_load = sorted(by_load)[0]
            candidates = by_load[lowest_load]
            inst.add_event2('schedule have lowest cpu load',
                            extra={'candidates': candidates})

            # Return a shuffled list of options
            random.shuffle(candidates)
            inst.add_event2('schedule final candidates',
                            extra={'candidates': candidates})
            return candidates
