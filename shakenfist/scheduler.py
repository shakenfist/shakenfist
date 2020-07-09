# Make scheduling decisions

import copy
import logging
from logging import handlers as logging_handlers
import random
import time

from shakenfist import config
from shakenfist import db
from shakenfist import db
from shakenfist import util

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.INFO)
LOG.addHandler(logging_handlers.SysLogHandler(address='/dev/log'))


class CandidateNodeNotFoundException(Exception):
    pass


class Scheduler(object):
    def __init__(self):
        self.refresh_metrics()

    def refresh_metrics(self):
        metrics = {}

        for node in db.get_nodes():
            node_name = node['fqdn']
            try:
                metrics[node_name] = db.get_metrics(node_name)
            except etcd.ReadException:
                pass

        self.metrics = metrics
        self.metrics_updated = time.time()

    def _has_sufficient_cpu(self, cpus, node):
        max_cpu = (self.metrics[node].get('cpu_max', 0) *
                   config.parsed.get('CPU_OVERCOMMIT_RATIO'))
        current_cpu = self.metrics[node].get('cpu_total_instance_vcpus', 0)
        if current_cpu + cpus > max_cpu:
            return False
        return True

    def _has_sufficient_ram(self, memory, node):
        max_ram = (self.metrics[node].get('memory_max', 0) *
                   config.parsed.get('RAM_OVERCOMMIT_RATIO'))
        current_ram = self.metrics[node].get(
            'memory_total_instance_actual_memory', 0)
        if current_ram + memory > max_ram:
            return False
        return True

    def _has_sufficient_disk(self, instance, node):
        requested_disk = 0
        for disk in instance.db_entry.get('block_devices', {}).get('devices', []):
            # TODO(mikal): this ignores "sizeless disks", that is ones that
            # are exactly the size of their base image, for example CD ROMs.
            if 'size' in disk:
                if not disk['size'] is None:
                    requested_disk += int(disk['size'])

        if requested_disk > (int(self.metrics[node].get('disk_free', '0')) / 1024 / 1024 / 1024):
            return False
        return True

    def _find_most_matching_networks(self, requested_networks, candidates):
        candidates_network_matches = {}
        for node in candidates:
            candidates_network_matches[node] = 0

            present_networks = []
            for inst in list(db.get_instances(only_node=node)):
                for iface in db.get_instance_interfaces(inst['uuid']):
                    if not iface['network_uuid'] in present_networks:
                        present_networks.append(iface['network_uuid'])

            for network in present_networks:
                if network in requested_networks:
                    candidates_network_matches[node] += 1

        candidates_by_network_matches = {}
        for node in candidates:
            matches = candidates_network_matches[node]
            candidates_by_network_matches.setdefault(matches, [])
            candidates_by_network_matches[matches].append(node)

        if len(candidates_by_network_matches) == 0:
            return candidates

        max_matches = max(candidates_by_network_matches.keys())
        return candidates_by_network_matches[max_matches]

    def _find_most_matching_images(self, requested_images, candidates):
        candidates_image_matches = {}
        for node in candidates:
            candidates_image_matches[node] = 0

            present_images = []
            for inst in list(db.get_instances(only_node=node)):
                if inst['block_devices']:
                    for disk in inst['block_devices']['devices']:
                        if (disk.get('base') and
                                not disk.get('base') in present_images):
                            present_images.append(disk.get('base'))

            for image in present_images:
                if image in requested_images:
                    candidates_image_matches[node] += 1

        candidates_by_image_matches = {}
        for node in candidates:
            matches = candidates_image_matches[node]
            candidates_by_image_matches.setdefault(matches, [])
            candidates_by_image_matches[matches].append(node)

        if len(candidates_by_image_matches) == 0:
            return candidates

        max_matches = max(candidates_by_image_matches.keys())
        return candidates_by_image_matches[max_matches]

    def place_instance(self, instance, network, candidates=None):
        with util.RecordedOperation('schedule', instance) as _:
            if time.time() - self.metrics_updated > config.parsed.get('SCHEDULER_CACHE_TIMEOUT'):
                self.refresh_metrics()

            if candidates:
                LOG.info('Scheduling %s, %s forced as candidates' %
                         (instance, candidates))
                db.add_event('instance', instance.db_entry['uuid'],
                             'schedule', 'Forced candidates', None, str(candidates))
                for node in candidates:
                    if node not in self.metrics:
                        raise CandidateNodeNotFoundException(node)
            else:
                candidates = []
                for node in self.metrics.keys():
                    candidates.append(node)
            LOG.info('Scheduling %s, %s start as candidates' %
                     (instance, candidates))
            db.add_event('instance', instance.db_entry['uuid'],
                         'schedule', 'Initial candidates', None, str(candidates))

            # Can we host that many vCPUs?
            for node in copy.copy(candidates):
                if instance.db_entry['cpus'] > self.metrics[node].get('cpu_max_per_instance', 0):
                    candidates.remove(node)
            LOG.info('Scheduling %s, %s have enough actual CPU' %
                     (instance, candidates))
            db.add_event('instance', instance.db_entry['uuid'],
                         'schedule', 'Have enough actual CPU', None, str(candidates))

            # Do we have enough idle CPU?
            for node in copy.copy(candidates):
                if not self._has_sufficient_cpu(instance.db_entry['cpus'], node):
                    candidates.remove(node)
            LOG.info('Scheduling %s, %s have enough idle CPU' %
                     (instance, candidates))
            db.add_event('instance', instance.db_entry['uuid'],
                         'schedule', 'Have enough idle CPU', None, str(candidates))

            # Do we have enough idle RAM?
            for node in copy.copy(candidates):
                if not self._has_sufficient_ram(instance.db_entry['memory'], node):
                    candidates.remove(node)
            LOG.info('Scheduling %s, %s have enough idle RAM' %
                     (instance, candidates))
            db.add_event('instance', instance.db_entry['uuid'],
                         'schedule', 'Have enough idle RAM', None, str(candidates))

            # Do we have enough idle disk?
            for node in copy.copy(candidates):
                if not self._has_sufficient_disk(instance, node):
                    candidates.remove(node)
            LOG.info('Scheduling %s, %s have enough idle disk' %
                     (instance, candidates))
            db.add_event('instance', instance.db_entry['uuid'],
                         'schedule', 'Have enough idle disk', None, str(candidates))

            # What nodes have the highest number of networks already present?
            if network:
                requested_networks = []
                for net in network:
                    network_uuid = net['network_uuid']
                    if network_uuid not in requested_networks:
                        requested_networks.append(network_uuid)

                candidates = self._find_most_matching_networks(
                    requested_networks, candidates)
                LOG.info('Scheduling %s, %s have most matching networks'
                            % (instance, candidates))
                db.add_event('instance', instance.db_entry['uuid'],
                                'schedule', 'Have most matching networks', None, str(candidates))

            # What nodes have the base image already?
            requested_images = []
            for disk in instance.db_entry['block_devices']['devices']:
                if disk.get('base'):
                    requested_images = disk.get('base')

            candidates = self._find_most_matching_images(
                requested_images, candidates)
            LOG.info('Scheduling %s, %s have most matching images'
                     % (instance, candidates))
            db.add_event('instance', instance.db_entry['uuid'],
                         'schedule', 'Have most matching images', None, str(candidates))

            # Return a shuffled list of options
            random.shuffle(candidates)
            return candidates
