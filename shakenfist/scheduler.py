# Make scheduling decisions

import copy
import math
from functools import partial
import random
import time
import uuid

from shakenfist import artifact
from shakenfist import baseobject
from shakenfist.config import config
from shakenfist import db
from shakenfist import exceptions
from shakenfist import images
from shakenfist import instance
from shakenfist import logutil
from shakenfist import networkinterface
from shakenfist.node import (
    Nodes, active_states_filter as node_active_states_filter)
from shakenfist import util


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

    return None


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
        self.log.debug('Refreshing metrics')
        metrics = {}

        for n in Nodes([node_active_states_filter]):
            node_name = n.uuid
            try:
                new_metrics = db.get_metrics(node_name)
                self.log.with_object(n).debug(
                    'Metrics for node: %s', new_metrics)
                if new_metrics:
                    metrics[node_name] = new_metrics
                else:
                    self.log.with_object(n).warning(
                        'Empty metrics from database for node')
            except exceptions.ReadException:
                self.log.with_object(n).warning(
                    'Refreshing metrics for node failed')

        self.metrics = metrics
        self.metrics_updated = time.time()

    # Note this method returns a three-way value: true (yes), false (no), -1 (if you must)
    def _has_sufficient_cpu(self, log_ctx, cpus, node, cpu_ratio):
        preferred_max_cpus = self.metrics[node].get('cpu_max', 0) * cpu_ratio
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

        if current_cpu + cpus > preferred_max_cpus:
            log_ctx.with_fields({
                'node': node,
                'current_cpus': current_cpu,
                'requested_cpus': cpus,
                'preferred_max_cpus': preferred_max_cpus
            }).debug('Scheduling on node would exceed preferred maximum CPUs, a soft failure')
            return -1

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

    def _has_sufficient_disk(self, log_ctx, instance, node):
        requested_disk = 0
        for disk in instance.disk_spec:
            # TODO(mikal): this ignores "sizeless disks", that is ones that
            # are exactly the size of their base image, for example CD ROMs.
            if 'size' in disk:
                if not disk['size'] is None:
                    requested_disk += int(disk['size'])

        if requested_disk > (int(self.metrics[node].get('disk_free', '0')) / 1024 / 1024 / 1024):
            log_ctx.with_fields({
                'node': node,
                'requested_disk': requested_disk,
                'disk_free': self.metrics[node].get('disk_free', 0),
            }).debug('Node has insufficient disk')
            return False
        return True

    def _find_most_matching_networks(self, requested_networks, candidates):
        if not candidates:
            return []

        # Find number of matching networks on each node. We need to be careful
        # how we do this to avoid repeatedly scanning the etcd repository.
        per_node = {}
        for inst in instance.Instances([]):
            n = inst.placement
            if n.get('node'):
                per_node.setdefault(n['node'], [])
                per_node[n['node']].append(inst)

        candidates_network_matches = {}
        for n in candidates:
            candidates_network_matches[n] = 0

            # Make a list of networks for the node
            present_networks = []
            for inst in per_node.get(n, []):
                for ni in networkinterface.interfaces_for_instance(inst):
                    if ni.network_uuid not in present_networks:
                        present_networks.append(ni.network_uuid)

            # Count the requested networks present on this node
            for network in present_networks:
                if network in requested_networks:
                    candidates_network_matches[n] += 1

        # Store candidate nodes keyed by number of matches
        candidates_by_network_matches = {}
        for n in candidates:
            matches = candidates_network_matches[n]
            candidates_by_network_matches.setdefault(matches, []).append(n)

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

    def _find_most_matching_images(self, requested_images, candidates):
        # Determine number of matching images per node
        candidates_image_matches = {}
        for n in candidates:
            candidates_image_matches[n] = 0

        for image in requested_images:
            for i in artifact.Artifacts(filters=[
                    partial(artifact.type_filter,
                            artifact.Artifact.TYPE_IMAGE),
                    partial(artifact.url_filter, image),
                    baseobject.active_states_filter]):
                b = i.most_recent_index
                if b:
                    for loc in b.locations:
                        candidates_image_matches[loc] += 1

        # Create dict of candidate lists keyed by number of image matches
        candidates_by_image_matches = {}
        for n in candidates:
            matches = candidates_image_matches[n]
            candidates_by_image_matches.setdefault(matches, [])
            candidates_by_image_matches[matches].append(n)

        # If no matches, return the original candidate list
        if len(candidates_by_image_matches) == 0:
            return candidates

        # Return all candidates that have the highest number of image matches
        max_matches = max(candidates_by_image_matches.keys())
        return candidates_by_image_matches[max_matches]

    def place_instance(self, instance, network, candidates=None):
        with util.RecordedOperation('schedule', instance):
            log_ctx = self.log.with_object(instance)

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
                log_ctx.with_field('candidates', candidates).info(
                    'Scheduling: forced candidates')
                instance.add_event('schedule',
                                   'Forced candidates', None, str(candidates))
                for n in candidates:
                    if n not in self.metrics:
                        raise exceptions.CandidateNodeNotFoundException(n)
            else:
                candidates = []
                for n in self.metrics.keys():
                    candidates.append(n)
            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: Initial candidates')
            instance.add_event('schedule',
                               'Initial candidates', None, str(candidates))
            if not candidates:
                raise exceptions.LowResourceException('No nodes with metrics')

            # Can we host that many vCPUs?
            for n in copy.copy(candidates):
                max_cpu = self.metrics[n].get('cpu_max_per_instance', 0)
                if instance.cpus > max_cpu:
                    candidates.remove(n)
            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: have enough actual CPU')
            instance.add_event('schedule',
                               'Have enough actual CPU', None, str(candidates))
            if not candidates:
                raise exceptions.LowResourceException(
                    'Requested vCPUs exceeds vCPU limit')

            # Do we have enough idle CPU? The config value CPU_OVERCOMMIT_RATIO
            # specifies the maximum overcommit that the cluster is willing to do
            # on a per-node basis. However, we try to keep the actual overcommit
            # even across the hypervisor nodes. So, we calculate the current
            # overcommit ratio for the cluster, and then cap that at the configured
            # maximum. That is then compared with the overcommit ratio for this node.
            current_cluster_cpu = 0
            actual_cluster_cpu = 0
            for node in self.metrics:
                current_cluster_cpu += self.metrics[node].get(
                    'cpu_total_instance_vcpus', 0)
                actual_cluster_cpu += self.metrics[node].get(
                    'cpu_available', 0)
            cpu_ratio = min(config.CPU_OVERCOMMIT_RATIO,
                            math.ceil(current_cluster_cpu / actual_cluster_cpu))
            log_ctx.info('Current cluster CPU overcommit ratio is %d, configured maximum %d',
                         cpu_ratio, config.CPU_OVERCOMMIT_RATIO)

            for n in copy.copy(candidates):
                preference = self._has_sufficient_cpu(
                    log_ctx, instance.cpus, n, cpu_ratio)
                if preference == -1:
                    candidates.remove(n)
                    candidates.append(n)
                elif not preference:
                    candidates.remove(n)

            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: have enough idle CPU')
            instance.add_event('schedule',
                               'Have enough idle CPU', None, str(candidates))
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough idle CPU')

            # Do we have enough idle RAM?
            for n in copy.copy(candidates):
                if not self._has_sufficient_ram(log_ctx, instance.memory, n):
                    candidates.remove(n)
            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: Have enough idle RAM')
            instance.add_event('schedule',
                               'Have enough idle RAM', None, str(candidates))
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough idle RAM')

            # Do we have enough idle disk?
            for n in copy.copy(candidates):
                if not self._has_sufficient_disk(log_ctx, instance, n):
                    candidates.remove(n)
            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: Have enough idle disk')
            instance.add_event('schedule',
                               'Have enough idle disk', None, str(candidates))
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough disk space')

            # What nodes have the highest number of networks already present?
            if network:
                requested_networks = []
                for net in network:
                    network_uuid = net['network_uuid']
                    if network_uuid not in requested_networks:
                        requested_networks.append(network_uuid)

                candidates = self._find_most_matching_networks(
                    requested_networks, candidates)
                log_ctx.with_field('candidates', candidates).info(
                    'Scheduling: Have most matching networks')
                instance.add_event('schedule', 'Have most matching networks',
                                   None, str(candidates))

            # What nodes have the base image already?
            requested_images = []
            for disk in instance.disk_spec:
                if disk.get('base'):
                    img = images.Image.new(disk['base'])
                    requested_images = img.url

            candidates = self._find_most_matching_images(
                requested_images, candidates)
            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: Have most matching images')
            instance.add_event('schedule', 'Have most matching images',
                               None, str(candidates))

            # Avoid allocating to network node if possible
            net_node = get_network_node()
            if len(candidates) > 1 and net_node.uuid in candidates:
                candidates.remove(net_node.uuid)
                log_ctx.with_field('candidates', candidates).info(
                    'Scheduling: Are non-network nodes')
                instance.add_event('schedule', 'Are non-network nodes',
                                   None, str(candidates))

            # Return a shuffled list of options
            random.shuffle(candidates)
            instance.add_event('schedule', 'Final candidates',
                               None, str(candidates))
            return candidates
