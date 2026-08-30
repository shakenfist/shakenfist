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


def _binary_affinity_spec(requested_affinity):
    """Any affinity specification, as the binary form.

    The two shapes live under one metadata key and are told apart by
    their keys. A weighted specification is mapped here, at the point
    the scheduler reads it, so that there is exactly one scoring path
    rather than two which can drift apart.

    Anything the validator would have refused is dropped rather than
    raising: the scheduler reads specifications which were validated
    when they were accepted, and one which predates a validation rule
    should place somewhere rather than making an instance
    unschedulable.
    """
    if not isinstance(requested_affinity, dict) or not requested_affinity:
        return instance.map_weighted_affinity({})

    if not set(requested_affinity) & set(instance.Instance.AFFINITY_BINARY_KEYS):
        return instance.map_weighted_affinity(requested_affinity)

    spec = {}
    for k in instance.Instance.AFFINITY_BINARY_KEYS:
        v = requested_affinity.get(k)
        if not isinstance(v, list):
            v = []
        spec[k] = [t for t in v if isinstance(t, str) and t]
    return spec


def _hard_affinity_constraints(requested_affinity):
    """The (require_with, require_without) tag lists for a spec."""
    spec = _binary_affinity_spec(requested_affinity)
    return (spec[instance.Instance.AFFINITY_REQUIRE_WITH],
            spec[instance.Instance.AFFINITY_REQUIRE_WITHOUT])


def _hard_affinity_description(require_with, require_without):
    """Human readable text naming the constraints, for the 409 body."""
    parts = []
    if require_with:
        parts.append('require_with_tag=%s' % sorted(require_with))
    if require_without:
        parts.append('require_without_tag=%s' % sorted(require_without))
    return ', '.join(parts)


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

    def _capacity_by_node(self):
        """The materialised capacity counters, keyed by node uuid.

        Read fresh on every scheduling decision rather than cached
        beside the metrics: the counters move on every admission, and a
        burst of concurrent creates -- precisely the case a cached
        snapshot gets wrong -- is what reading them is for. Only
        hypervisors the reconciler considers schedulable have a row, so
        an absent node is not an error (P7).

        An unreadable table returns empty rather than raising, which
        degrades a caller to whatever measurement it also holds. That
        is the right direction for a pre-filter -- the guard still
        refuses correctly, only the cheap pruning in front of it is
        lost -- but it is another reason this is not the admission
        decision.
        """
        return {row['node_uuid']: row
                for row in mariadb.get_scheduler_node_capacity()}

    def _satisfies_hard_affinity(self, inst, node, memo,
                                 require_with, require_without):
        """Does this node satisfy the instance's hard affinity constraints?

        Constraints match the tags of *instances already placed on the
        node*, within the requesting namespace only, exactly as the
        scorer does. Shaken Fist has no node capability tags, so there
        is nothing else they could match. The namespace scope is
        inherited rather than chosen: crossing it would let a caller
        learn what another tenant is running by watching where their
        own instances refuse to land, which is why require_without_tag
        is a within-namespace constraint and not an isolation
        primitive.
        """
        node_instances = self._placed_instances(node, memo)
        if node_instances is None:
            return False, {'reason': 'node row not found'}

        present = set()
        for _, i in node_instances:
            if i is None or i.uuid == inst.uuid:
                continue
            if i.namespace != inst.namespace:
                continue
            for tag in (i.tags or []):
                present.add(tag)

        missing = [t for t in require_with if t not in present]
        if missing:
            return False, {
                'reason': 'no co-located instance carries a required tag',
                'require_with_tag': missing,
            }

        excluded = [t for t in require_without if t in present]
        if excluded:
            return False, {
                'reason': 'a co-located instance carries an excluded tag',
                'require_without_tag': excluded,
            }

        return True, None

    def _placed_instances(self, node, memo):
        """The instances placed on a node, as (uuid, Instance or None) pairs.

        Memoised into the caller's dict, because the affinity pass can
        consider a node more than once and a scheduling decision should
        only fetch its placements once. Returns None if the node's row
        has gone away.
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

    def _has_sufficient_cpu(self, log_ctx, inst, node, capacity):
        """A cheap CPU pre-filter (P2), denominated in both ledgers.

        This is not the admission decision. Since phase 3 of the
        scheduler-reservations plan the real guard is the atomic
        UPDATE that ``Instance.place_instance()`` makes against
        ``scheduler_node_capacity``, which cannot admit two concurrent
        creates into one remaining slot. This filter's job is to prune
        the candidate list cheaply so that the guard misses less often.

        It has to see what the guard sees to do that. The measurement
        alone cannot: ``cpu_total_instance_vcpus`` counts *running*
        libvirt domains and is republished only once a minute, so a
        node whose ledger is full still measures as idle for the whole
        time its instances spend fetching images. Filtering on the
        measurement alone let such a node stay in the candidate list,
        win the load ordering below, and then be refused by the guard
        -- which cost merge CI a whole suite of creates on 2026-08-14
        (the walk had been narrowed to that one node, so the refusal
        was a 507 rather than a fall-through). The counters are
        therefore read alongside the metrics and the node is charged
        whichever of the two is larger, which is the same arithmetic
        summarize_resources() publishes.

        ``capacity`` is the counters keyed by node uuid. A node with no
        row is charged nothing and measured against the live figure: it
        is a node mid-upgrade, or one the reconciler declined to size
        (P7), and admission will let it through unguarded, so this
        filter must not refuse on a ledger which does not exist.
        """
        cpu_base, from_fallback = self._schedulable_threads(node)
        hard_max_cpus = cpu_base * config.CPU_OVERCOMMIT_RATIO
        measured_cpus = self.metrics[node].get('cpu_total_instance_vcpus', 0)
        cpus = inst.cpus

        # A node with a capacity row is guarded by that row's own limit,
        # so test the guard's arithmetic exactly rather than a live
        # re-derivation which can differ from it by a floor().
        row = capacity.get(node)
        limit_cpus = row['limit_cpus'] if row else hard_max_cpus
        committed_cpus = row['used_cpus'] if row else 0

        # An instance already placed here is in used_cpus, and a
        # reschedule which lands it back on the same node does not
        # charge it a second time (place_instance() early-outs on an
        # unchanged placement), so neither does this filter.
        if committed_cpus and instance.placement_filter(node, inst):
            committed_cpus = max(0, committed_cpus - cpus)

        current_cpu = max(measured_cpus, committed_cpus)

        if current_cpu + cpus > limit_cpus:
            reason = {
                'reason': 'would exceed hard max CPUs',
                'current_cpus': current_cpu,
                'measured_cpus': measured_cpus,
                'committed_cpus': committed_cpus,
                'capacity_row_present': row is not None,
                'requested_cpus': cpus,
                'limit_cpus': limit_cpus,
                'hard_max_cpus': hard_max_cpus,
                'cpu_schedulable': cpu_base,
                'cpu_schedulable_from_fallback': from_fallback,
            }
            log_ctx.with_fields({'node': node, **reason}).debug(
                'Scheduling on node would exceed hard maximum CPUs')
            return False, reason

        return True, None

    def _has_sufficient_ram(self, log_ctx, inst, node, capacity):
        # There are two things to track here... We must always leave the
        # node's published memory reservation (operating system tasks, plus
        # cluster-wide daemons on infra-role nodes) untouched -- assume
        # there is no overlap with existing VMs when checking this. Note as
        # well that metrics are in MB...
        memory = inst.memory
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

        # ...Both checks above are measurements, and both lag placement:
        # a just-placed instance which has not yet booted reduces neither
        # memory_available nor memory_total_instance_actual, so a burst
        # of near-simultaneous creates passes them all against the same
        # stale snapshot (issue 3636). As with _has_sufficient_cpu(), the
        # node is therefore also charged the memory its capacity ledger
        # already records, against the ledger's own limit -- exactly the
        # arithmetic the guard will apply -- so a node whose RAM is fully
        # committed leaves the candidate list here rather than surviving
        # to attract, and then be refused, an entire burst. A node with
        # no row is guarded by nothing and must not be refused on a
        # ledger which does not exist (P7).
        row = capacity.get(node)
        if row:
            committed_mb = row['used_memory_mb']
            # An instance already placed here is in used_memory_mb, and a
            # reschedule which lands it back on the same node does not
            # charge it a second time.
            if committed_mb and instance.placement_filter(node, inst):
                committed_mb = max(0, committed_mb - memory)
            if committed_mb + memory > row['limit_memory_mb']:
                reason = {
                    'reason': 'would exceed committed memory',
                    'committed_memory_mb': committed_mb,
                    'requested_memory_mb': memory,
                    'limit_memory_mb': row['limit_memory_mb'],
                }
                log_ctx.with_fields({'node': node, **reason}).debug(
                    'Scheduling on node would exceed committed memory')
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
            self, related_objects, stage, candidates, dropped=None,
            exception_class=exceptions.LowResourceException,
            detail=None):
        """Publish a filter stage's outcome, and raise if it emptied the set.

        exception_class is defaulted so that every pre-existing caller
        is unchanged. The hard affinity stage passes
        AffinityConstraintUnsatisfiable, because "no node carries the
        tag you required" is a conflict with the state of the cluster
        and not a shortage of resources, and the create path answers
        the two with different status codes.

        detail is appended to the raised message. The message is built
        here rather than by the caller, so a stage which wants to name
        the constraint it refused on has to hand that text in.
        """
        extra = {'candidates': candidates}
        if dropped:
            extra['dropped'] = dropped

        if not candidates:
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                f'schedule has no candidates at stage {stage}, aborting',
                extra=extra)
            message = f'No nodes remaining at scheduling stage {stage}'
            if detail:
                message = f'{message}: {detail}'
            raise exception_class(message)

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

        # The filter passes below read this instance's attributes once
        # per candidate -- placement_filter() in the CPU stage, affinity
        # in the stage after it -- and every one of those reads fetches
        # the same instance_attributes row. Memoising for the duration of
        # the decision makes it one fetch. Nothing in this block writes an
        # attribute, and the memo is discarded on the way out, so no
        # caller observes different staleness than it did before.
        with util_general.RecordedOperation('schedule', inst), \
                inst.attribute_memo():
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

            # Do we have enough idle CPU? This reads the capacity
            # counters as well as the metrics, because a node whose
            # ledger is full measures as idle until its instances boot.
            capacity = self._capacity_by_node()
            dropped = {}
            for c in list(candidates):
                ok, reason = self._has_sufficient_cpu(
                    log_ctx, inst, c, capacity)
                if not ok:
                    dropped[c] = reason
                    candidates.remove(c)
            self._log_and_raise_on_error(
                related_objects, 'sufficient_idle_cpu', candidates,
                dropped=dropped)

            # Do we have enough idle RAM? Like the CPU stage this reads
            # the capacity counters as well as the metrics, because a
            # node whose RAM ledger is full measures as unchanged until
            # its instances boot and fault their allocations in.
            dropped = {}
            for c in list(candidates):
                ok, reason = self._has_sufficient_ram(log_ctx, inst, c, capacity)
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

            # Each candidate's placements are read once and memoised, so
            # a node considered twice is only fetched once. The memo is
            # shared by the hard constraint stage below and the scorer
            # after it, so a create which uses both pays for one read.
            placements = {}

            requested_affinity = inst.affinity
            require_with, require_without = _hard_affinity_constraints(
                requested_affinity)

            # Hard affinity constraints are admission, not ranking: a node
            # which does not satisfy them cannot host the instance at all,
            # so they filter here rather than contributing to a score.
            #
            # This stage returns without touching the placements memo when
            # no hard constraint was requested. That matters because it
            # runs before the load shedding filters have pruned anything,
            # so populating the memo here would read placements for the
            # full candidate set on every create -- including the great
            # majority which request no affinity at all.
            if require_with or require_without:
                dropped = {}
                for c in list(candidates):
                    ok, reason = self._satisfies_hard_affinity(
                        inst, c, placements, require_with, require_without)
                    if not ok:
                        dropped[c] = reason
                        candidates.remove(c)
                self._log_and_raise_on_error(
                    related_objects, 'affinity_constraints', candidates,
                    dropped=dropped,
                    exception_class=exceptions.AffinityConstraintUnsatisfiable,
                    detail=_hard_affinity_description(
                        require_with, require_without))

            # Filter by affinity, if any has been specified. This runs on the
            # set of nodes which can actually fit the instance, but before the
            # queue depth and disk bandwidth filters below, which describe how
            # busy a node is right now rather than whether it can host the
            # instance at all. We record the full per-candidate scoring
            # breakdown so that incorrect placement decisions (e.g. a tagged
            # neighbour being invisible) can be diagnosed from audit events.
            by_affinity = defaultdict(list)
            affinity_detail = {}
            binary_spec = _binary_affinity_spec(requested_affinity)
            scoring_tags = (
                binary_spec[instance.Instance.AFFINITY_PREFER_WITH]
                + binary_spec[instance.Instance.AFFINITY_PREFER_WITHOUT])

            # A scorer with no tags to score cannot change the ordering,
            # but the walk below reads every candidate's placements --
            # one Node.from_db() plus one Instance.from_db() per placed
            # instance -- so running it for the great majority of creates
            # which request no affinity at all was pure cost. Skipping it
            # reduces the measured database load rather than holding it
            # flat. Everything downstream sees what it saw before: one
            # tier, containing every candidate, scoring zero.
            if not scoring_tags:
                by_affinity[0] = list(candidates)

            for c in (list(candidates) if scoring_tags else []):
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

                    # Count proportional, not set membership: a node
                    # carrying five instances of a group really is more
                    # "with the group" than one carrying a single
                    # instance, and the same in reverse for the avoid
                    # direction. Note the score is a sum across
                    # neighbours *and* across tags, so a
                    # prefer_without_tag match can be outweighed by
                    # neighbour count on the other axis.
                    #
                    # Weighted specifications reach here already mapped,
                    # so there is one scoring path and not two.
                    matched = {}
                    contribution = 0
                    for tag in binary_spec[
                            instance.Instance.AFFINITY_PREFER_WITH]:
                        if tag in i.tags:
                            matched[tag] = matched.get(tag, 0) + 1
                            contribution += 1
                    for tag in binary_spec[
                            instance.Instance.AFFINITY_PREFER_WITHOUT]:
                        if tag in i.tags:
                            matched[tag] = matched.get(tag, 0) - 1
                            contribution -= 1
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
            ram_headrooms = {}
            for c in list(candidates):
                raw_load = self.metrics[c].get('cpu_load_1', 0)
                denom, from_fallback = self._schedulable_threads(c)
                denom = denom or 1
                normalised = raw_load / denom

                # RAM commitment ranks alongside CPU load (issue 3636): a
                # node carrying RAM-heavy but CPU-idle instances otherwise
                # looks like the best candidate precisely because of the
                # workload that makes it dangerous, and attracts every
                # large instance in a burst until the capacity guard
                # finally refuses it. The committed fraction is read from
                # the same counters the guard draws down, so unlike the
                # metrics it moves with every admission. A node ranks by
                # whichever of its two pressures is worse, in the same
                # coarse 0.25-wide bands so that similar nodes stay
                # interchangeable and a burst still spreads.
                ram_fraction = 0.0
                row = capacity.get(c)
                if row and row['limit_memory_mb'] > 0:
                    committed_mb = max(
                        row['used_memory_mb'],
                        self.metrics[c].get('memory_total_instance_actual', 0))
                    ram_fraction = committed_mb / row['limit_memory_mb']
                ram_headrooms[c] = max(0.1, 1.0 - ram_fraction)

                bucket = max(
                    math.floor(normalised / 0.25),
                    math.floor(ram_fraction / 0.25))
                denominators[c] = denom
                load_detail[c] = {
                    'cpu_load_1': raw_load,
                    'cpu_schedulable': denom,
                    'cpu_schedulable_from_fallback': from_fallback,
                    'normalised_load': normalised,
                    'ram_committed_fraction': ram_fraction,
                    'bucket': bucket,
                }
                by_load[bucket].append(c)

            lowest_load = sorted(by_load)[0]
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule have lowest cpu load',
                extra={
                    'candidates': by_load[lowest_load],
                    'lowest_load': lowest_load,
                    'load_detail': load_detail,
                })

            # Return every candidate, ordered best bucket first, and
            # within a bucket a weighted shuffle where a node's weight
            # is its load headroom toward the target sustained load --
            # so bigger or idler machines draw a proportionally larger
            # share of a burst. This is weighted sampling without
            # replacement (Efraimidis-Spirakis A-Res).
            #
            # The bucketing orders rather than filters, because a
            # bucket says a node looks busier right now, not that it
            # cannot host the instance -- every node still here has
            # passed every admission filter above. Since phase 3 the
            # caller walks this list against a capacity guard which can
            # refuse the head of it, so discarding the rest turns one
            # refusal into a failed create on a cluster with room: in
            # merge CI on 2026-08-14 three viable nodes were cut to one,
            # that one was refused, and five tests got a 507 apiece.
            # Callers walk the list on failure, so the tail order
            # matters as much as the head.
            weights = {}
            ordered = []
            for bucket in sorted(by_load):
                tier = by_load[bucket]
                for c in tier:
                    raw_load = self.metrics[c].get('cpu_load_1', 0)
                    # Load headroom toward the target, scaled by the
                    # node's uncommitted RAM fraction, so that within a
                    # band a RAM-committed node draws a proportionally
                    # smaller share of a burst.
                    weights[c] = max(
                        0.1,
                        (config.SCHEDULER_TARGET_LOAD * denominators[c] -
                         raw_load)) * ram_headrooms[c]
                tier.sort(
                    key=lambda c: random.random() ** (1.0 / weights[c]),
                    reverse=True)
                ordered.extend(tier)
            candidates = ordered
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

        # The capacity counters are what admission actually draws down,
        # so they are read once here rather than recomputed per node --
        # publishing a second, independently derived ledger beside the
        # real one is how the two come to disagree. This is the same
        # read the CPU pre-filter makes, through the same helper.
        capacity = self._capacity_by_node()

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

            # CPU. The charge is max(measured, committed), exactly as
            # the pre-filter computes it (_has_sufficient_cpu()), so the
            # headroom published here is bounded by whichever of the two
            # binds -- which is what a create would be allowed. The two
            # differ in one respect: the pre-filter measures that charge
            # against the capacity row's own limit_cpus, because that is
            # what the guard it is standing in front of will use. The
            # counters derive that limit by the same arithmetic
            # (mariadb._derive_cpu_memory_limits()) but refresh only
            # once a reconcile period, where the metrics here are live,
            # so this report publishes the live figure.
            resources['per_node'][n]['cpu_max_per_instance'] = \
                self.metrics[n].get('cpu_max_per_instance', 0)

            cpu_base, _ = self._schedulable_threads(n)
            resources['per_node'][n]['cpu_schedulable'] = cpu_base
            hard_max_cpus = cpu_base * config.CPU_OVERCOMMIT_RATIO
            measured_cpus = self.metrics[n].get('cpu_total_instance_vcpus', 0)
            # A node the reconciler has not given a capacity row -- one
            # mid-upgrade, or one it declined to size (P7) -- is charged
            # nothing, because it is also guarded by nothing: admission
            # will let it through. The flag says which of those a zero
            # is, so "why is this node's committed total zero?" is
            # answerable from the response alone.
            row = capacity.get(n)
            committed_cpus = row['used_cpus'] if row else 0
            resources['per_node'][n]['cpu_committed_row_present'] = \
                row is not None
            # No fallback to cpu_hard_max here: the whole point of this
            # field is to let a reader see the two ledgers disagree, and
            # a fallback would hide exactly that disagreement.
            resources['per_node'][n]['cpu_limit'] = \
                row['limit_cpus'] if row else None
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

            # Memory. As with CPU, this must match _has_sufficient_ram():
            # published headroom is bounded by the committed ledger as
            # well as the measurements, because that ledger is what both
            # the pre-filter and the guard refuse on.
            reserved = self._memory_reserved_mb(n)
            resources['per_node'][n]['memory_reserved_mb'] = reserved
            resources['per_node'][n]['ram_max_per_instance'] = \
                (self.metrics[n].get('memory_available', 0) - reserved)
            resources['per_node'][n]['ram_max'] = \
                self.metrics[n].get('memory_max', 0) * \
                config.RAM_OVERCOMMIT_RATIO
            committed_mb = row['used_memory_mb'] if row else 0
            resources['per_node'][n]['ram_committed'] = committed_mb
            ram_available = \
                (self.metrics[n].get('memory_max', 0) * config.RAM_OVERCOMMIT_RATIO -
                 self.metrics[n].get('memory_total_instance_actual', 0))
            if row:
                ram_available = min(
                    ram_available, row['limit_memory_mb'] - committed_mb)
            resources['per_node'][n]['ram_available'] = ram_available
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
