# Make scheduling decisions

import copy
from functools import partial
import random
import time

from shakenfist import baseobject
from shakenfist.config import config
from shakenfist import db
from shakenfist import exceptions
from shakenfist import images
from shakenfist import logutil
from shakenfist import util
from shakenfist import virt


LOG, _ = logutil.setup(__name__)


class Scheduler(object):
    def __init__(self):
        self.refresh_metrics()

    def refresh_metrics(self):
        metrics = {}

        for node in db.get_nodes(seen_recently=True):
            node_name = node['fqdn']
            try:
                metrics[node_name] = db.get_metrics(node_name)
            except exceptions.ReadException:
                pass

        self.metrics = metrics
        self.metrics_updated = time.time()

    def _has_sufficient_cpu(self, cpus, node):
        max_cpu = (self.metrics[node].get('cpu_max', 0) *
                   config.get('CPU_OVERCOMMIT_RATIO'))
        current_cpu = self.metrics[node].get('cpu_total_instance_vcpus', 0)
        if current_cpu + cpus > max_cpu:
            return False
        return True

    def _has_sufficient_ram(self, memory, node):
        # There are two things to track here... We must always have
        # RAM_SYSTEM_RESERVATION gb of RAM for operating system tasks -- assume
        # there is no overlap with existing VMs when checking this. Note as
        # well that metrics are in MB...
        available = (self.metrics[node].get('memory_available', 0) -
                     (config.get('RAM_SYSTEM_RESERVATION') * 1024))
        if available - memory < 0.0:
            return False

        # ...Secondly, if we're using KSM and over committing memory, we
        # shouldn't overcommit more than by RAM_OVERCOMMIT_RATIO
        instance_memory = (
            self.metrics[node].get('memory_total_instance_actual', 0) + memory)
        if (instance_memory / self.metrics[node].get('memory_max', 0) >
                config.get('RAM_OVERCOMMIT_RATIO')):
            return False

        return True

    def _has_sufficient_disk(self, instance, node):
        requested_disk = 0
        for disk in instance.disk_spec:
            # TODO(mikal): this ignores "sizeless disks", that is ones that
            # are exactly the size of their base image, for example CD ROMs.
            if 'size' in disk:
                if not disk['size'] is None:
                    requested_disk += int(disk['size'])

        if requested_disk > (int(self.metrics[node].get('disk_free', '0')) / 1024 / 1024 / 1024):
            return False
        return True

    def _find_most_matching_networks(self, requested_networks, candidates):
        if not candidates:
            return []

        # Find number of matching networks on each node. We need to be careful
        # how we do this to avoid repeatedly scanning the etcd repository.
        per_node = {}
        for inst in virt.Instances([]):
            node = inst.placement
            if node.get('node'):
                per_node.setdefault(node['node'], [])
                per_node[node['node']].append(inst)

        candidates_network_matches = {}
        for node in candidates:
            candidates_network_matches[node] = 0

            # Make a list of networks for the node
            present_networks = []
            for inst in per_node.get(node, []):
                for iface in db.get_instance_interfaces(inst.uuid):
                    if not iface['network_uuid'] in present_networks:
                        present_networks.append(iface['network_uuid'])

            # Count the requested networks present on this node
            for network in present_networks:
                if network in requested_networks:
                    candidates_network_matches[node] += 1

        # Store candidate nodes keyed by number of matches
        candidates_by_network_matches = {}
        for node in candidates:
            matches = candidates_network_matches[node]
            candidates_by_network_matches.setdefault(matches, []).append(node)

        # Find maximum matches of networks on a node
        max_matches = max(candidates_by_network_matches.keys())

        # Check that the maximum is not just the network node.
        # (Network node always has every network.)
        net_node = db.get_network_node()['fqdn']
        if (max_matches == 1 and
                candidates_by_network_matches[max_matches][0] == net_node):
            # No preference, all candidates are a reasonable choice
            return candidates

        # Return list of candidates that has maximum networks
        return candidates_by_network_matches[max_matches]

    def _find_most_matching_images(self, requested_images, candidates):
        # Determine number of matching images per node
        candidates_image_matches = {}
        for node in candidates:
            candidates_image_matches[node] = 0

        for image in requested_images:
            for i in images.Images(filters=[partial(images.url_filter, image),
                                            baseobject.active_states_filter]):
                candidates_image_matches[i.node] += 1

        # Create dict of candidate lists keyed by number of image matches
        candidates_by_image_matches = {}
        for node in candidates:
            matches = candidates_image_matches[node]
            candidates_by_image_matches.setdefault(matches, [])
            candidates_by_image_matches[matches].append(node)

        # If no matches, return the original candidate list
        if len(candidates_by_image_matches) == 0:
            return candidates

        # Return all candidates that have the highest number of image matches
        max_matches = max(candidates_by_image_matches.keys())
        return candidates_by_image_matches[max_matches]

    def place_instance(self, instance, network, candidates=None):
        with util.RecordedOperation('schedule', instance):
            log_ctx = LOG.with_object(instance)

            diff = time.time() - self.metrics_updated
            if diff > config.get('SCHEDULER_CACHE_TIMEOUT'):
                self.refresh_metrics()

            if candidates:
                log_ctx.with_field('candidates', candidates).info(
                    'Scheduling: forced candidates')
                instance.add_event('schedule',
                                   'Forced candidates', None, str(candidates))
                for node in candidates:
                    if node not in self.metrics:
                        raise exceptions.CandidateNodeNotFoundException(node)
            else:
                candidates = []
                for node in self.metrics.keys():
                    candidates.append(node)
            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: Initial candidates')
            instance.add_event('schedule',
                               'Initial candidates', None, str(candidates))
            if not candidates:
                raise exceptions.LowResourceException('No nodes with metrics')

            # Can we host that many vCPUs?
            for node in copy.copy(candidates):
                max_cpu = self.metrics[node].get('cpu_max_per_instance', 0)
                if instance.cpus > max_cpu:
                    candidates.remove(node)
            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: have enough actual CPU')
            instance.add_event('schedule',
                               'Have enough actual CPU', None, str(candidates))
            if not candidates:
                raise exceptions.LowResourceException(
                    'Requested vCPUs exceeds vCPU limit')

            # Do we have enough idle CPU?
            for node in copy.copy(candidates):
                if not self._has_sufficient_cpu(
                        instance.cpus, node):
                    candidates.remove(node)
            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: have enough idle CPU')
            instance.add_event('schedule',
                               'Have enough idle CPU', None, str(candidates))
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough idle CPU')

            # Do we have enough idle RAM?
            for node in copy.copy(candidates):
                if not self._has_sufficient_ram(
                        instance.memory, node):
                    candidates.remove(node)
            log_ctx.with_field('candidates', candidates).info(
                'Scheduling: Have enough idle RAM')
            instance.add_event('schedule',
                               'Have enough idle RAM', None, str(candidates))
            if not candidates:
                raise exceptions.LowResourceException(
                    'No nodes with enough idle RAM')

            # Do we have enough idle disk?
            for node in copy.copy(candidates):
                if not self._has_sufficient_disk(instance, node):
                    candidates.remove(node)
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
            net_node = db.get_network_node()
            if len(candidates) > 1 and net_node['fqdn'] in candidates:
                candidates.remove(net_node['fqdn'])
                log_ctx.with_field('candidates', candidates).info(
                    'Scheduling: Are non-network nodes')
                instance.add_event('schedule', 'Are non-network nodes',
                                   None, str(candidates))

            # Return a shuffled list of options
            random.shuffle(candidates)
            return candidates
