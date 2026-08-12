# Make scheduling decisions
import math
import random
import time
import uuid
from collections import defaultdict

from shakenfist_utilities import logs  # noreorder

from shakenfist.eventlog import add_event_multi
from shakenfist import exceptions
from shakenfist import mariadb
from shakenfist import instance
from shakenfist.config import config
from shakenfist.constants import DISK_BUSY_PER_SECOND_METRIC
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import GiB
from shakenfist.node import Node
from shakenfist.node import Nodes
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


# The network node doesn't move around, so just look it up once
# and cache the result. This can't be done until config is loaded,
# so the cache is populated by the first caller.
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
            # Metrics are stored under the node UUID
            node_uuid = str(n.uuid)
            new_metrics = mariadb.get_node_metrics(node_uuid)
            if new_metrics:
                if (time.time()
                        - new_metrics.get('timestamp', 0)
                        < 120):
                    new_metrics = new_metrics.get(
                        'metrics', {})
                else:
                    n.add_event(
                        EVENT_TYPE_AUDIT,
                        'stale metrics from database '
                        'for node')
                    new_metrics = {}
            else:
                n.add_event(
                    EVENT_TYPE_AUDIT,
                    'empty metrics from database '
                    'for node')
                new_metrics = {}
            metrics[node_uuid] = new_metrics

        except exceptions.ReadException:
            n.add_event(
                EVENT_TYPE_AUDIT,
                'refreshing metrics for node '
                'failed')

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
            reason = {
                'reason': 'queue too long',
                'node_queue_waiting': waiting,
                'unreasonable_threshold': UNREASONABLE_QUEUE_LENGTH,
            }
            log_ctx.with_fields({'node': node, **reason}).debug(
                'Excluding node with many queued jobs')
            return False, reason

        return True, None

    def _schedulable_threads(self, node):
        # Scheduling is denominated in schedulable threads -- the thread
        # count left after the resources daemon subtracts the node's
        # per-host thread reservation for the operating system and
        # host-level system services. Admission, load ordering and
        # summarize_resources() must all size a node through this helper
        # so they cannot disagree. Returns (threads, from_fallback).
        #
        # Metrics rows written by an older resources daemon lack
        # cpu_schedulable. For those we approximate the reservation the
        # node will publish once its daemon restarts by subtracting this
        # node's own thread reservation. We cannot know a remote node's
        # exact per-host value, and there is no longer an infra-role bump,
        # so we use the local config value; this fallback is transient and
        # stops the moment that node's daemon republishes cpu_schedulable.
        metrics = self.metrics[node]
        threads = metrics.get('cpu_schedulable')
        if threads:
            return threads, False

        cpu_max = metrics.get('cpu_max', 0)
        if not cpu_max:
            return 0, True

        return max(1, cpu_max - config.NODE_CPU_RESERVATION_THREADS), True

    def _memory_reserved_mb(self, node):
        # The resources daemon publishes the node's per-host memory
        # reservation (operating system plus host-level system services).
        # Metrics rows written by an older resources daemon lack it and
        # fall back to this node's own configured RAM reservation.
        return self.metrics[node].get(
            'memory_reserved_mb', int(config.NODE_RAM_RESERVATION_GB * 1024))

    def _placed_instances(self, node, memo):
        """The instances placed on a node, as (uuid, Instance or None) pairs.

        Memoised into the caller's dict, because CPU admission and the
        affinity pass both walk a candidate's placements and a scheduling
        decision should only fetch them once. Returns None if the node's
        row has gone away.
        """
        if node not in memo:
            n = Node.from_db(node)
            if n is None:
                memo[node] = None
            else:
                memo[node] = [
                    (instance_uuid, instance.Instance.from_db(instance_uuid))
                    for instance_uuid in n.instances]
        return memo[node]

    def _committed_vcpus(self, node, memo, exclude_uuid=None):
        """The vCPUs a node has already been committed to by placement.

        A node's metrics count the vCPUs of its *running* libvirt domains
        and are republished only once a minute, so an instance which has
        been placed but has not booted yet -- in practice the whole time
        it spends fetching its image -- is invisible to
        cpu_total_instance_vcpus. Every create in a burst therefore sees
        the same idle node, all of them are admitted onto it, and it ends
        up well past its hard maximum once they do start, at which point
        every later request naming that node is refused (issue 3498).
        place_instance() writes the placement row synchronously as each
        create is admitted, so counting placements closes the window that
        the measurement alone leaves open.

        exclude_uuid is the instance being scheduled: the preflight path
        reschedules an instance which is already placed here, and it must
        not be charged for itself twice.
        """
        placed = self._placed_instances(node, memo)
        if not placed:
            return 0
        return sum(i.cpus for instance_uuid, i in placed
                   if i is not None and instance_uuid != exclude_uuid)

    def _has_sufficient_cpu(self, log_ctx, inst, node, memo):
        cpu_base, from_fallback = self._schedulable_threads(node)
        hard_max_cpus = cpu_base * config.CPU_OVERCOMMIT_RATIO
        measured_cpus = self.metrics[node].get('cpu_total_instance_vcpus', 0)
        committed_cpus = self._committed_vcpus(
            node, memo, exclude_uuid=str(inst.uuid))
        current_cpu = max(measured_cpus, committed_cpus)
        cpus = inst.cpus

        if current_cpu + cpus > hard_max_cpus:
            reason = {
                'reason': 'would exceed hard max CPUs',
                'current_cpus': current_cpu,
                'measured_cpus': measured_cpus,
                'committed_cpus': committed_cpus,
                'requested_cpus': cpus,
                'hard_max_cpus': hard_max_cpus,
                'cpu_schedulable': cpu_base,
                'cpu_schedulable_from_fallback': from_fallback,
            }
            log_ctx.with_fields({'node': node, **reason}).debug(
                'Scheduling on node would exceed hard maximum CPUs')
            return False, reason

        return True, None

    def _has_sufficient_ram(self, log_ctx, memory, node):
        # There are two things to track here... We must always leave the
        # node's published memory reservation (operating system tasks, plus
        # cluster-wide daemons on infra-role nodes) untouched -- assume
        # there is no overlap with existing VMs when checking this. Note as
        # well that metrics are in MB...
        reserved = self._memory_reserved_mb(node)
        available = self.metrics[node].get('memory_available', 0) - reserved
        if available - memory < 0.0:
            reason = {
                'reason': 'insufficient memory',
                'available_mb': available,
                'requested_memory_mb': memory,
                'memory_reserved_mb': reserved,
            }
            log_ctx.with_fields({'node': node, **reason}).debug(
                'Insufficient memory')
            return False, reason

        # ...Secondly, if we're using KSM and over committing memory, we
        # shouldn't overcommit more than by RAM_OVERCOMMIT_RATIO
        instance_memory = (
            self.metrics[node].get('memory_total_instance_actual', 0) + memory)
        memory_max = self.metrics[node].get('memory_max', 0)
        if not memory_max:
            reason = {
                'reason': 'no memory_max in node metrics',
            }
            log_ctx.with_fields({'node': node, **reason}).debug(
                'Node metrics lack memory_max')
            return False, reason
        if (instance_memory / memory_max > config.RAM_OVERCOMMIT_RATIO):
            reason = {
                'reason': 'KSM overcommit ratio exceeded',
                'instance_memory_mb': instance_memory,
                'memory_max_mb': memory_max,
                'overcommit_ratio': config.RAM_OVERCOMMIT_RATIO,
            }
            log_ctx.with_fields({'node': node, **reason}).debug(
                'KSM overcommit ratio exceeded')
            return False, reason

        return True, None

    def _has_sufficient_disk(self, log_ctx, inst, node):
        requested_disk = 0
        for disk in inst.disk_spec:
            # TODO(mikal): this ignores "sizeless disks", that is ones that
            # are exactly the size of their base image, for example CD ROMs.
            if 'size' in disk:
                if not disk['size'] is None:
                    requested_disk += int(disk['size'])

        # The candidate node publishes its own disk reservation as a metric, so
        # a remote evaluator honours that node's per-host floor (falling back to
        # our config default only for a stale metrics row mid-upgrade).
        reservation = self.metrics[node].get(
            'disk_reservation_gb', config.NODE_DISK_RESERVATION_GB)
        disk_free = int(self.metrics[node].get('disk_free_instances', '0')) / GiB
        disk_free -= reservation
        if requested_disk > disk_free:
            reason = {
                'reason': 'insufficient disk',
                'requested_disk_gb': requested_disk,
                'disk_free_gb': disk_free,
                'minimum_free_disk_gb': reservation,
            }
            log_ctx.with_fields({'node': node, **reason}).debug(
                'Node has insufficient disk')
            return False, reason
        return True, None

    def _has_idle_disk_bandwidth(self, log_ctx, inst, node):
        # We also avoid starting new instances on hypervisors with busy disk.
        # busy_time is in milliseconds per second, so a value of 1,000 is 100%
        # busy. You can record more than 100% if there is more than one disk
        # in the system doing IO at the time.
        busy_time = float(
            self.metrics[node].get(DISK_BUSY_PER_SECOND_METRIC, 0))
        if busy_time > 1200:
            reason = {
                'reason': 'disk bandwidth saturated',
                'busy_time_delta_per_second': busy_time,
                'busy_time_threshold': 1200,
            }
            log_ctx.with_fields({'node': node, **reason}).debug(
                'Scheduling on node would exceed maximum disk bandwidth')
            return False, reason

        return True, None

    def _log_and_raise_on_error(
            self, related_objects, stage, candidates, dropped=None):
        extra = {'candidates': candidates}
        if dropped:
            extra['dropped'] = dropped

        if not candidates:
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                f'schedule has no candidates at stage {stage}, aborting',
                extra=extra)
            raise exceptions.LowResourceException(
                f'No nodes remaining at scheduling stage {stage}')

        add_event_multi(
            EVENT_TYPE_AUDIT, related_objects,
            f'schedule at stage {stage}', extra=extra)

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

            # Record the inputs to the scheduling decision so failures can be
            # diagnosed from the event log alone.
            requested_disk_gb = 0
            for disk in inst.disk_spec:
                if 'size' in disk and disk['size'] is not None:
                    requested_disk_gb += int(disk['size'])
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule inputs',
                extra={
                    'requested_affinity': inst.affinity,
                    'requested_cpus': inst.cpus,
                    'requested_memory_mb': inst.memory,
                    'requested_disk_gb': requested_disk_gb,
                    'disk_spec': inst.disk_spec,
                    'namespace': inst.namespace,
                    'forced_candidates': bool(candidates),
                    'metrics_age_seconds': diff,
                })

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
            self._log_and_raise_on_error(
                related_objects, 'pre_schedule', candidates)

            # Ensure all specified nodes are hypervisors
            dropped = {}
            for c in list(candidates):
                if not self.metrics[c].get('is_hypervisor', False):
                    dropped[c] = {'reason': 'not a hypervisor'}
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'is_hypervisor', candidates, dropped=dropped)

            # Can we host that many vCPUs?
            dropped = {}
            for c in list(candidates):
                max_cpu = self.metrics[c].get('cpu_max_per_instance', 0)
                if inst.cpus > max_cpu:
                    dropped[c] = {
                        'reason': 'requested vCPUs exceed per-instance max',
                        'cpu_max_per_instance': max_cpu,
                        'requested_cpus': inst.cpus,
                    }
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'cpu_max_per_instance', candidates,
                dropped=dropped)

            # Do we have enough idle CPU? Placements are memoised here and
            # reused by the affinity pass below, which walks the same lists.
            placements = {}
            dropped = {}
            for c in list(candidates):
                ok, reason = self._has_sufficient_cpu(
                    log_ctx, inst, c, placements)
                if not ok:
                    dropped[c] = reason
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'sufficient_idle_cpu', candidates,
                dropped=dropped)

            # Do we have enough idle RAM?
            dropped = {}
            for c in list(candidates):
                ok, reason = self._has_sufficient_ram(log_ctx, inst.memory, c)
                if not ok:
                    dropped[c] = reason
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'sufficient_idle_memory', candidates,
                dropped=dropped)

            # Do we have enough free disk?
            dropped = {}
            for c in list(candidates):
                ok, reason = self._has_sufficient_disk(log_ctx, inst, c)
                if not ok:
                    dropped[c] = reason
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'sufficient_free_disk', candidates,
                dropped=dropped)

            # Filter by affinity, if any has been specified. This runs on the
            # set of nodes which can actually fit the instance, but before the
            # queue depth and disk bandwidth filters below, which describe how
            # busy a node is right now rather than whether it can host the
            # instance at all. We record the full per-candidate scoring
            # breakdown so that incorrect placement decisions (e.g. a tagged
            # neighbour being invisible) can be diagnosed from audit events.
            by_affinity = defaultdict(list)
            requested_affinity = inst.affinity
            affinity_detail = {}

            for c in list(candidates):
                node_instances = self._placed_instances(c, placements)
                affinity = 0
                considered = []
                if node_instances is None:
                    affinity_detail[c] = {
                        'score': 0,
                        'reason': 'node row not found',
                    }
                    by_affinity[affinity].append(c)
                    continue

                for instance_uuid, i in node_instances:
                    if not i:
                        considered.append({
                            'instance_uuid': instance_uuid,
                            'skipped': 'instance row not found',
                        })
                        continue
                    if i.uuid == inst.uuid:
                        considered.append({
                            'instance_uuid': instance_uuid,
                            'skipped': 'self',
                        })
                        continue
                    if not i.tags:
                        considered.append({
                            'instance_uuid': instance_uuid,
                            'skipped': 'no tags',
                        })
                        continue
                    if i.namespace != inst.namespace:
                        considered.append({
                            'instance_uuid': instance_uuid,
                            'skipped': 'different namespace',
                            'namespace': i.namespace,
                        })
                        continue

                    matched = {}
                    contribution = 0
                    for tag, val in requested_affinity.items():
                        if tag in i.tags:
                            matched[tag] = int(val)
                            contribution += int(val)
                    considered.append({
                        'instance_uuid': instance_uuid,
                        'tags': list(i.tags),
                        'matched': matched,
                        'contribution': contribution,
                    })
                    affinity += contribution

                affinity_detail[c] = {
                    'score': affinity,
                    'instance_count': len(node_instances),
                    'considered': considered,
                }
                by_affinity[affinity].append(c)

            highest_affinity = sorted(by_affinity, reverse=True)[0]
            preferred = by_affinity[highest_affinity]
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule have highest affinity',
                extra={
                    'candidates': preferred,
                    'requested_affinity': requested_affinity,
                    'highest_affinity': highest_affinity,
                    'by_affinity': {
                        str(k): v for k, v in by_affinity.items()},
                    'affinity_detail': affinity_detail,
                })

            # Don't use nodes which aren't keeping up with queue jobs
            dropped = {}
            for c in list(candidates):
                ok, reason = self._has_reasonable_queue_state(log_ctx, c)
                if not ok:
                    dropped[c] = reason
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'queue_state', candidates, dropped=dropped)

            # Are the disks really busy?
            dropped = {}
            for c in list(candidates):
                ok, reason = self._has_idle_disk_bandwidth(log_ctx, inst, c)
                if not ok:
                    dropped[c] = reason
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'sufficient_idle_disk', candidates,
                dropped=dropped)

            # The two filters above are load shedding, not admission: they say
            # a node is momentarily busy, not that it cannot host the
            # instance. They may therefore narrow the winning affinity tier,
            # but must never move placement out of it. Previously they ran
            # before affinity scoring, so a transient burst on the node an
            # instance was affine to silently placed it anywhere at all, and
            # the mirror case left an anti-affinity instance on the single
            # node it was asked to avoid (issue 3565). If every node is busy
            # we still refuse to schedule, above.
            narrowed = [c for c in candidates if c in preferred]
            if not narrowed:
                add_event_multi(
                    EVENT_TYPE_AUDIT, related_objects,
                    'schedule keeping affinity despite transient load',
                    extra={
                        'candidates': preferred,
                        'load_shed_candidates': candidates,
                        'highest_affinity': highest_affinity,
                    })
                narrowed = preferred
            candidates = narrowed

            # Order candidates by current CPU load, normalised by the
            # schedulable thread count so that differently sized machines
            # are comparable. The buckets are deliberately coarse (0.25
            # load per thread wide): the metrics snapshot can be up to a
            # minute stale, so fine-grained ranking would stack an entire
            # burst of requests onto whichever node looked best at the
            # last refresh. Nodes without the reservation-aware
            # cpu_schedulable field (written by an older resources daemon)
            # fall back to the raw thread count in cpu_max.
            by_load = defaultdict(list)
            load_detail = {}
            denominators = {}
            for c in list(candidates):
                raw_load = self.metrics[c].get('cpu_load_1', 0)
                denom, from_fallback = self._schedulable_threads(c)
                denom = denom or 1
                normalised = raw_load / denom
                bucket = math.floor(normalised / 0.25)
                denominators[c] = denom
                load_detail[c] = {
                    'cpu_load_1': raw_load,
                    'cpu_schedulable': denom,
                    'cpu_schedulable_from_fallback': from_fallback,
                    'normalised_load': normalised,
                    'bucket': bucket,
                }
                by_load[bucket].append(c)

            lowest_load = sorted(by_load)[0]
            candidates = by_load[lowest_load]
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule have lowest cpu load',
                extra={
                    'candidates': candidates,
                    'lowest_load': lowest_load,
                    'load_detail': load_detail,
                })

            # Return a weighted shuffle of the winning bucket, where a
            # node's weight is its load headroom toward the target
            # sustained load -- so bigger or idler machines draw a
            # proportionally larger share of a burst. This is weighted
            # sampling without replacement (Efraimidis-Spirakis A-Res):
            # callers walk the list on failure, so the tail order matters
            # as much as the head.
            weights = {}
            for c in candidates:
                raw_load = self.metrics[c].get('cpu_load_1', 0)
                weights[c] = max(
                    0.1,
                    config.SCHEDULER_TARGET_LOAD * denominators[c] - raw_load)
            candidates.sort(
                key=lambda c: random.random() ** (1.0 / weights[c]),
                reverse=True)
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule final candidates',
                extra={'candidates': candidates, 'weights': weights})
            return candidates

    def summarize_resources(self):
        # Refresh metrics if its too old, or there are no nodes.
        diff = time.time() - self.metrics_updated
        if diff > config.SCHEDULER_CACHE_TIMEOUT or len(self.metrics) == 0:
            self.refresh_metrics()

        # Only hypervisors with reasonable queue lengths are candidates
        placements = {}
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

            # CPU. This must use the same arithmetic as
            # _has_sufficient_cpu() so that the resources this API reports
            # agree with what admission would actually allow.
            resources['per_node'][n]['cpu_max_per_instance'] = \
                self.metrics[n].get('cpu_max_per_instance', 0)

            cpu_base, _ = self._schedulable_threads(n)
            resources['per_node'][n]['cpu_schedulable'] = cpu_base
            hard_max_cpus = cpu_base * config.CPU_OVERCOMMIT_RATIO
            measured_cpus = self.metrics[n].get('cpu_total_instance_vcpus', 0)
            committed_cpus = self._committed_vcpus(n, placements)
            # Both inputs to the admission decision are published, because
            # "this node measures as idle but is refusing work" is only
            # diagnosable if you can see which of the two is binding.
            resources['per_node'][n]['cpu_hard_max'] = hard_max_cpus
            resources['per_node'][n]['cpu_measured'] = measured_cpus
            resources['per_node'][n]['cpu_committed'] = committed_cpus
            current_cpu = max(measured_cpus, committed_cpus)
            resources['per_node'][n]['cpu_available'] = hard_max_cpus - current_cpu
            # A node packed beyond the cap (for example after the cap was
            # lowered) reports negative per-node headroom, which is honest,
            # but must not eat into the cluster total other nodes provide.
            resources['total']['cpu_available'] += max(
                0, resources['per_node'][n]['cpu_available'])

            resources['per_node'][n]['cpu_load_1'] = self.metrics[n].get(
                'cpu_load_1', 0)
            resources['per_node'][n]['cpu_load_5'] = self.metrics[n].get(
                'cpu_load_5', 0)
            resources['per_node'][n]['cpu_load_15'] = self.metrics[n].get(
                'cpu_load_15', 0)

            # Memory. As with CPU, this must match _has_sufficient_ram().
            reserved = self._memory_reserved_mb(n)
            resources['per_node'][n]['memory_reserved_mb'] = reserved
            resources['per_node'][n]['ram_max_per_instance'] = \
                (self.metrics[n].get('memory_available', 0) - reserved)
            resources['per_node'][n]['ram_max'] = \
                self.metrics[n].get('memory_max', 0) * \
                config.RAM_OVERCOMMIT_RATIO
            resources['per_node'][n]['ram_available'] = \
                (self.metrics[n].get('memory_max', 0) * config.RAM_OVERCOMMIT_RATIO -
                 self.metrics[n].get('memory_total_instance_actual', 0))
            resources['total']['ram_available'] += max(
                0, resources['per_node'][n]['ram_available'])

            # Disk. Each node publishes its own reservation as a metric, so we
            # subtract the candidate node's per-host floor (falling back to our
            # config default only for a stale metrics row mid-upgrade).
            reservation = self.metrics[n].get(
                'disk_reservation_gb', config.NODE_DISK_RESERVATION_GB)
            disk_free = int(self.metrics[n].get(
                'disk_free_instances', '0')) / GiB
            disk_free -= reservation
            resources['per_node'][n]['disk_available'] = disk_free

            # Instance count
            resources['per_node'][n]['instances_total'] = self.metrics[n].get(
                'instances_total', 0)
            resources['per_node'][n]['instances_active'] = self.metrics[n].get(
                'instances_active', 0)

        return resources
