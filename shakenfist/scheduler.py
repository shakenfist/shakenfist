# Make scheduling decisions
import math
import random
import time
import uuid
from collections import defaultdict
from typing import NamedTuple

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

# Why an active node is missing from the resources summary's per_node,
# as published in its `excluded` mapping. Clients match on these exact
# strings, so they are a closed set: adding one is an API change.
EXCLUSION_QUEUE_TOO_LONG = 'queue too long'
EXCLUSION_STALE_METRICS = 'stale metrics'
EXCLUSION_NO_METRICS = 'no metrics'
EXCLUSION_METRICS_UNREADABLE = 'metrics unreadable'
EXCLUSION_REASONS = frozenset((
    EXCLUSION_QUEUE_TOO_LONG,
    EXCLUSION_STALE_METRICS,
    EXCLUSION_NO_METRICS,
    EXCLUSION_METRICS_UNREADABLE,
))

# The figures summarize_resources() publishes from the cluster_capacity
# singleton, in the order they are published: the row's last write, its
# nine stored counters, and the headroom derived from them.
CLUSTER_CAPACITY_FIGURES = (
    ('updated_at',)
    + mariadb.CLUSTER_CAPACITY_COUNTERS
    + tuple(f'unallocated_{dim}' for dim in mariadb.CAPACITY_DIMENSIONS))


class PlacementShape(NamedTuple):
    """What placement has been asked to fit, as the filters read it.

    The filter, affinity and ranking stages are denominated in a
    requested shape rather than in an Instance, so a placement decision
    can be evaluated for a request which has no instance row of its own.
    The five figures below are everything those stages ever read about
    the thing being placed.

    instance_uuid and placed_on describe a request which does already
    have a row. The uuid is the affinity self-skip -- an instance is not
    its own neighbour -- and placed_on is the node the request is
    presently placed on, which the capacity filters must not charge a
    second time when a reschedule lands it back where it already is.
    Both are None for a request which is not placed anywhere.

    disk_spec is read by no stage at all. It is carried because the
    audit event recording the inputs to a decision publishes the disks
    which produced disk_gb as well as the total, and an operator reading
    a refusal wants both. A request with no disk list to show -- one
    sized rather than specified -- leaves it None rather than inventing
    one.
    """

    cpus: int
    memory_mb: int
    disk_gb: int
    affinity: dict
    namespace: str
    instance_uuid: uuid.UUID | None = None
    placed_on: str | None = None
    disk_spec: list | None = None

    @classmethod
    def from_instance(cls, inst):
        """The shape an instance is asking placement for.

        The only place a shape is read out of an Instance, and therefore
        the only place which has to know how an instance expresses its
        request. It is also the only place placement sums a disk_spec:
        the audit event recording the inputs to a decision and the disk
        filter which acts on them each summed it separately, which was
        two chances to disagree about how large a request is.

        placed_on is the same field instance.placement_filter() compares
        a node against, so the reschedule self-charge exemption cannot
        drift from the placement it is exempting.

        The disk sum is mariadb.disk_spec_virtual_gb(), which is the one
        implementation of "how many gigabytes does this disk claim".
        Instance._capacity_claim charges the ledger with that helper, so
        delegating to it is what makes the filters evaluate the same
        request the ledger then charges: summing here instead with
        int() truncated, so a disk_spec of [{"size": 10.5}] was filtered
        as 10 GB and charged as 11. The helper also absorbs the
        sizeless-disk case this factory used to carry a TODO about -- a
        missing or null size is the size of the base image, and
        contributes nothing to the claim.

        It is called strictly, because this is a request rather than a
        stored row: a size the sum cannot read raises here, as it did
        when this factory read it with int(), instead of being counted
        as a claim of zero gigabytes and admitted.

        cpus and memory are cast, as the disk sizes are by that helper.
        The create API deliberately accepts a numeric string for an integer
        body field -- a validator which refused "2" would be narrower
        than the handler behind it -- so an instance can carry its vCPU
        count as a string, and this is the one place which reads a
        request out of an instance and therefore the place where it
        becomes the integer this type says it is. Arithmetic on a
        string that looks like a number is a question nothing
        downstream should have to ask.

        The cast closes the arithmetic and not the input: a body field
        of "abc" or "2.5" still arrives here and still fails the
        create with an unhandled 500, as a ValueError raised from this
        factory rather than a TypeError raised later by a filter. The
        create 500s on a string either way -- admission reads the
        instance too, and multiplies its vCPU count by a float.
        Typing the request where it enters the API is what closes
        that, and this type is the wrong place for it.

        int() truncates, and nothing non-integral reaches the two casts
        which remain. A JSON number with a fractional part is refused by
        the integer field in shakenfist/external_api/validation.py, and
        an integral float is the same JSON number as the integer, so
        int(8.0) is exact. The cheap filter and the admission guard
        therefore cannot disagree about the size of a request by a
        truncated vCPU.
        """
        return cls(
            cpus=int(inst.cpus), memory_mb=int(inst.memory),
            disk_gb=mariadb.disk_spec_virtual_gb(
                inst.disk_spec, strict=True),
            affinity=inst.affinity, namespace=inst.namespace,
            instance_uuid=inst.uuid,
            placed_on=inst.placement.get('node'),
            disk_spec=inst.disk_spec)


class PlacementSnapshot:
    """The figures a placement decision reads, with its own charges applied.

    Placement draws on two separate accounts. The measured figures the
    resources daemons publish say what each node is actually doing, and
    the committed counters the capacity reconciler maintains say what
    has already been admitted to it. Every capacity filter reads both,
    because either one alone is wrong: the measurement lags admission by
    as long as an instance spends fetching its image, and a node the
    reconciler has not sized has no counters at all.

    Placing a set of shapes charges each shape to the node it was
    assigned before the next shape is evaluated, so the set cannot spend
    the same headroom twice. Charging only one of the two accounts would
    still allow exactly that, because the filters reading the other
    would see an unchanged node -- which is issue 3636's failure mode
    one level up. Charging goes through this object for that reason, and
    this object always charges both.

    The charges are tentative. Nothing has been admitted, the real guard
    is still the atomic UPDATE that place_instance() makes, and this
    object is discarded when the decision ends. It holds copies for the
    same reason: the Scheduler which lent it its metrics outlives the
    decision and other decisions read the same dicts, and the committed
    counters may be a caller's fixture, so charging either in place
    would corrupt something outside this decision.
    """

    def __init__(self, metrics, capacity_reader):
        # The outer mapping is copied now and a node's own figures are
        # copied when that node is first charged, so an uncharged node
        # costs nothing and nothing handed in here is ever written to.
        self._metrics = dict(metrics)
        self._capacity_reader = capacity_reader
        self._capacity = None
        self._degraded = False

    def nodes(self):
        """Every node this decision was offered, in the order they were read.

        Which includes a node whose metrics row was stale or empty:
        get_active_node_metrics() maps those to an empty dict and keeps
        the key, and leaves out only a node whose read raised. The
        is_hypervisor stage is what refuses an empty one, so this is
        not a list of nodes anything can be placed on.
        """
        return list(self._metrics.keys())

    def has_node(self, node):
        """Whether this decision was offered a node at all.

        True for a node whose metrics row was stale or empty, false
        only for one left out of the active node list entirely. The
        forced-candidate check reads it for exactly that distinction.
        """
        return node in self._metrics

    def node_metrics(self, node):
        """A node's measured figures, charged for what has been placed on it.

        Raises KeyError for a node which was not in the active node
        list, exactly as reading the scheduler's own snapshot does: a
        caller naming a node this decision was never offered has made a
        mistake, and must not be told the node measures as empty.
        """
        return self._metrics[node]

    def ensure_capacity(self):
        """Read the committed counters once, and say whether the read failed.

        Read where the first filter which needs them runs rather than
        when the decision starts, so a decision refused on measurement
        alone -- a candidate which is not a hypervisor, a request larger
        than any node's per-instance ceiling -- does not pay for a query
        it never looks at. Every shape in a set then reads that one
        read, charged with the set's own earlier assignments.

        A failed read degrades the capacity filters to measurement only
        rather than raising, so it is reported rather than acted on
        here.
        """
        if self._capacity is None:
            capacity, self._degraded = self._capacity_reader()
            self._capacity = dict(capacity)
        return self._degraded

    def capacity_row(self, node):
        """A node's committed counters, or None if it has no row.

        A node with no row is unguarded at admission and must not be
        refused on a ledger which does not exist (P7), so it is never
        given one here either -- not even a zeroed one, which would read
        as a node with no headroom at all. Such a node is still charged
        on the measured figures, which is all a filter has to go on
        there.
        """
        self.ensure_capacity()
        return self._capacity.get(node)

    def charge(self, shape, node):
        """Charge a shape to a node, on both accounts at once.

        The debits below are every figure the capacity filters read,
        each in its own units, so a shape which has been assigned is
        invisible to none of the filters the next shape walks through.

        No "free" figure can be driven negative by this, because a shape
        is only ever charged to a node whose filters it has already
        passed and each of those filters refuses a request larger than
        the figure it reads.

        used_disk_gb is in the committed row and is deliberately not
        debited: no filter reads it, and a figure charged here but read
        nowhere would be a second ledger which nothing could catch
        drifting.

        The ranking stages are a weaker guarantee. Load bucketing
        reads cpu_load_1, which nothing here can move -- a shape which
        has not started yet has no load -- and ram_fraction, which is
        zero on a node with no committed row. So a charge reranks a
        node only where the reconciler has sized it; on an unsized
        cluster the shapes after the first are ranked against an
        unchanged node and spread by the weighted shuffle alone, until
        a capacity filter refuses. That is deliberate: a notional load
        for an instance which is not running would be a figure no
        measurement would ever produce, and the filters still refuse
        correctly, so such a set is less evenly spread rather than
        over-placed.

        A shape assigned to the node it is already on is charged
        nothing, on either account. A reschedule which lands an
        instance back where it started changes nothing about that node:
        place_instance() early-outs on an unchanged placement so the
        ledger does not move, and the measurement counted the instance
        before the decision and counts it after. Charging it would make
        the node look fuller to every later shape in the set by the
        size of a request that was never added. This is the mirror of
        the exemption _has_sufficient_cpu() and _has_sufficient_ram()
        apply to the committed account, and the rule it belongs to: an
        exemption a filter applies has to be applied to the charge, or
        the two disagree about the same node. Those filters exempt only
        the committed account, and that asymmetry is theirs and
        predates the snapshot -- it is not copied here, because a
        charge is answering what the node looks like afterwards rather
        than deciding a refusal.
        """
        if shape.placed_on == node:
            return

        self.ensure_capacity()

        measured = dict(self._metrics[node])
        measured['cpu_total_instance_vcpus'] = (
            measured.get('cpu_total_instance_vcpus', 0) + shape.cpus)
        measured['memory_available'] = (
            measured.get('memory_available', 0) - shape.memory_mb)
        measured['memory_total_instance_actual'] = (
            measured.get('memory_total_instance_actual', 0) + shape.memory_mb)
        # disk_free_instances is published in bytes, and arrives as a
        # string from some metrics rows, which is why the disk filter
        # casts it before dividing by GiB. A shape's disk is denominated
        # in GB, so the debit is scaled rather than subtracted.
        measured['disk_free_instances'] = (
            int(measured.get('disk_free_instances', '0'))
            - shape.disk_gb * GiB)
        self._metrics[node] = measured

        row = self._capacity.get(node)
        if row is None:
            return
        charged = dict(row)
        charged['used_cpus'] = row['used_cpus'] + shape.cpus
        charged['used_memory_mb'] = row['used_memory_mb'] + shape.memory_mb
        self._capacity[node] = charged


def _shapes_largest_first(shapes):
    """The indexes of a set of shapes, largest shape first.

    Memory, then vCPUs, then disk, each descending, with the order the
    caller gave as the final tie break so that two identical shapes are
    walked in the order they were asked for. Placing the largest first
    is first-fit-decreasing: a small shape placed first can leave a
    cluster with room for nothing large, where the reverse order leaves
    the small one the choice of whatever is left.
    """
    return sorted(
        range(len(shapes)),
        key=lambda index: (-shapes[index].memory_mb, -shapes[index].cpus,
                           -shapes[index].disk_gb, index))


def get_network_node():
    global CACHED_NETWORK_NODE

    if CACHED_NETWORK_NODE:
        return CACHED_NETWORK_NODE

    for n in Nodes([], prefilter='active'):
        if n.ip == config.NETWORK_NODE_IP:
            CACHED_NETWORK_NODE = n
            return CACHED_NETWORK_NODE

    raise exceptions.NoNetworkNode('Cannot find network node')


def get_active_node_metrics(unavailable=None):
    """The usable metrics of every active node, keyed by node uuid.

    A node whose row is stale or missing maps to an empty dict, and one
    whose read raised is left out. The scheduler cannot use any of
    those, but it cannot say why from the returned mapping either: an
    empty dict carries no is_hypervisor, so a hypervisor whose daemon
    stopped publishing looks exactly like a network node.

    If ``unavailable`` is a dict, the reason each such node was unusable
    is recorded in it against the node's uuid, at the point the
    decision is made. The return value is the same whether or not it is
    given, because placement reads it.
    """
    metrics = {}

    for n in Nodes([], prefilter='active'):
        try:
            # Metrics are stored under the node UUID
            node_uuid = str(n.uuid)
            new_metrics = mariadb.get_node_metrics(node_uuid)
            if new_metrics:
                row_timestamp = new_metrics.get('timestamp', 0)
                metrics_age = time.time() - row_timestamp
                if metrics_age < 120:
                    new_metrics = new_metrics.get(
                        'metrics', {})
                    # When the resources daemon wrote this row. Kept so a
                    # placement decision can publish the real staleness of
                    # what it read, not just the age of this scheduler's
                    # cache of it (issue 4521).
                    new_metrics['metrics_timestamp'] = row_timestamp
                else:
                    n.add_event(
                        EVENT_TYPE_AUDIT,
                        'stale metrics from database '
                        'for node')
                    new_metrics = {}
                    # The row's own timestamp rather than its age: the
                    # caller may hold this for as long as its metrics
                    # cache lives, and an age computed now would be wrong
                    # by however long that was.
                    if unavailable is not None:
                        unavailable[node_uuid] = {
                            'reason': EXCLUSION_STALE_METRICS,
                            'metrics_timestamp': row_timestamp,
                        }
            else:
                n.add_event(
                    EVENT_TYPE_AUDIT,
                    'empty metrics from database '
                    'for node')
                new_metrics = {}
                if unavailable is not None:
                    unavailable[node_uuid] = {'reason': EXCLUSION_NO_METRICS}
            metrics[node_uuid] = new_metrics

        except exceptions.ReadException:
            n.add_event(
                EVENT_TYPE_AUDIT,
                'refreshing metrics for node '
                'failed')
            if unavailable is not None:
                unavailable[str(n.uuid)] = {
                    'reason': EXCLUSION_METRICS_UNREADABLE}

    return metrics


def _queue_exclusion_reason(node_metrics):
    """Why a node's queue is too long to place on, or None if it is not.

    The one statement of the queue-length rule, shared by the candidate
    filter and the resources summary so the two cannot disagree about
    which nodes it excludes. It does not log: the summary calls it for
    every hypervisor on every poll.
    """
    waiting = node_metrics.get('node_queue_waiting', 0)
    if waiting > UNREASONABLE_QUEUE_LENGTH:
        return {
            'reason': EXCLUSION_QUEUE_TOO_LONG,
            'node_queue_waiting': waiting,
            'unreasonable_threshold': UNREASONABLE_QUEUE_LENGTH,
        }
    return None


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


def _affinity_neighbour_skip(shape, instance_uuid, i):
    """Should this co-located instance be ignored when matching tags?

    Returns the audit record describing why it was skipped, or None if
    it is a neighbour whose tags count. Both the hard require_* filter
    and the soft scoring loop call this rather than restating the four
    conditions, because a filter and a scorer which disagreed about
    what counts as a neighbour would place an instance somewhere its
    own audit trail says it should not be (coding_rules.md, "Never
    restate a visibility predicate").

    The namespace skip is the trust boundary: crossing it would let a
    caller learn what another tenant is running by watching where
    their own instances refuse to land.

    The order of the last two tests is the cheap one rather than the
    historical one. ``namespace`` is a field of the instance's own row
    and is already in hand; ``tags`` is a metadata read which nothing
    caches, so each access is a database round trip. Testing tags
    first read, and then discarded, the metadata of every instance in
    every other namespace on every candidate node -- work whose result
    could not change the answer, because a foreign instance is skipped
    whatever its tags say.

    One consequence is accepted rather than avoided. An instance which
    is both tagless *and* foreign is now reported as skipped for
    ``different namespace`` where it used to be reported as ``no
    tags``. Both are true of it and neither is more true; nothing
    reads the reason, which is audit prose rather than an input to any
    decision. The namespace reason is arguably the better answer for
    such an instance anyway, since it is the test which would go on
    skipping it if somebody tagged it tomorrow.
    """
    if not i:
        return {
            'instance_uuid': instance_uuid,
            'skipped': 'instance row not found',
        }
    if i.uuid == shape.instance_uuid:
        return {
            'instance_uuid': instance_uuid,
            'skipped': 'self',
        }
    if i.namespace != shape.namespace:
        return {
            'instance_uuid': instance_uuid,
            'skipped': 'different namespace',
            'namespace': i.namespace,
        }
    if not i.tags:
        return {
            'instance_uuid': instance_uuid,
            'skipped': 'no tags',
        }
    return None


def _hard_affinity_description(require_with, require_without):
    """Human readable text naming the constraints, for the 409 body."""
    parts = []
    if require_with:
        parts.append('require_with_tag=%s' % sorted(require_with))
    if require_without:
        parts.append('require_without_tag=%s' % sorted(require_without))
    return ', '.join(parts)


def _schedulable_threads(metrics):
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
    # exact per-host value, and *this fallback* applies no role-aware
    # bump (test_old_dialect_fallback_ignores_infra_role), so we use
    # the local config value; the fallback is transient and stops the
    # moment that node's daemon republishes cpu_schedulable.
    #
    # The deployer does still bump infra roles, per host, so a real
    # node's reservation is usually not the local config value:
    # examples/_shared/site.yml gives a network or database node twice
    # the plain-hypervisor thread reservation. On a small node that is
    # load-bearing -- CI's 4-thread infra hypervisors reserve all four
    # and are floored at one schedulable thread by
    # _compute_reservations() -- so do not read this comment as saying
    # the bump is gone. It is not applied *here*, which is a statement
    # about the fallback and not about the system (issue 4201).
    threads = metrics.get('cpu_schedulable')
    if threads:
        return threads, False

    cpu_max = metrics.get('cpu_max', 0)
    if not cpu_max:
        return 0, True

    return max(1, cpu_max - config.NODE_CPU_RESERVATION_THREADS), True


def _memory_reserved_mb(metrics):
    # The resources daemon publishes the node's per-host memory
    # reservation (operating system plus host-level system services).
    # Metrics rows written by an older resources daemon lack it and
    # fall back to this node's own configured RAM reservation.
    return metrics.get(
        'memory_reserved_mb', int(config.NODE_RAM_RESERVATION_GB * 1024))


def _capacity_by_node():
    """The counters keyed by node uuid, and whether the read failed.

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

    Returns ``(capacity, degraded)``: an empty mapping means either
    an unpopulated table or a failed read, and only the second value
    says which. Callers unpack rather than index so the degraded
    case is visible where the read is made -- a caller which does
    not act on it discards it explicitly.
    """
    read = mariadb.get_scheduler_node_capacity()
    return ({row['node_uuid']: row for row in read.rows}, read.degraded)


def _cluster_capacity():
    """The cluster_capacity singleton, as the resources summary publishes it.

    Only the summary calls this; placement never reads the singleton,
    because the guarded UPDATEs at admission test against it in SQL.

    Always carries ``row_present`` and ``degraded``. Every figure is
    None unless the row was read successfully, never zero: see the
    comment in ``_summarize_resources()`` for why. The figures are
    copied from the read, including the ``unallocated_*`` headroom,
    which mariadb derives once and this does not recompute.
    """
    read = mariadb.get_cluster_capacity()
    row = None if read.degraded else read.row
    published = {
        'row_present': row is not None,
        'degraded': read.degraded,
    }
    for key in CLUSTER_CAPACITY_FIGURES:
        published[key] = row[key] if row is not None else None
    return published


def _summarize_resources(metrics, metrics_unavailable):
    # The capacity counters are what admission actually draws down,
    # so they are read once here rather than recomputed per node --
    # publishing a second, independently derived ledger beside the
    # real one is how the two come to disagree. This is the same
    # read the CPU pre-filter makes, through the same helper.
    #
    # A degraded read is not acted on here: this summary has no
    # instance to record an event against, and a failed read leaves
    # every node reporting as uncounted, which is exactly what it
    # already did. That reasoning still only covers eventing --
    # it is not a reason to keep the flag out of the response body.
    # A caller sampling this endpoint over time (the CI headroom
    # probe, for one) has the same problem the pre-filter does: an
    # empty `capacity` mapping is indistinguishable from a genuinely
    # unpopulated table unless something says which. Publishing the
    # flag below costs nothing here and lets that caller tell the
    # two apart instead of discarding every sample from a failed
    # read alongside every sample from a cluster the reconciler
    # simply has not reached yet (P7).
    #
    # cluster_capacity is the cluster-wide ledger, published for the
    # same reason: it is the singleton row itself, the one both
    # cluster-wide guards test against -- an unclaimed placement
    # must fit in what is neither claimed nor used by unclaimed
    # namespaces, and so must a new claim. It is not a sum of claim
    # rows. Admission draws the row down directly and the reconciler
    # rebuilds it, so a total computed here from the claims would be
    # a second ledger which agrees with the first only until the
    # next placement. The headroom figures come from the same read,
    # derived once beside it, so no reader has to redo that
    # arithmetic and risk getting it subtly different. It is kept
    # out of `total`, because total sums per-node headroom bounded
    # by measurement, while this ledger's totals are sums of node
    # limits. Those are different quantities, and one key holding
    # both would invite comparing them.
    #
    # A singleton the reconciler has not yet written, and one which
    # could not be read, both publish every figure as None rather
    # than zero. A ledger of zeroes says the cluster is full, which
    # neither state means: before the first reconcile unclaimed
    # placements are not guarded at all. row_present and degraded
    # say which of the two a None is.
    capacity, capacity_degraded = _capacity_by_node()
    cluster_capacity = _cluster_capacity()

    # Only hypervisors with reasonable queue lengths are candidates.
    #
    # `excluded` says why an active node is missing from per_node.
    # Without it a reader can only infer the cause from an absence,
    # and for the metrics cases it cannot even do that: a node whose
    # metrics row is stale or missing has an empty metrics dict
    # here, so it carries no is_hypervisor and is skipped below
    # exactly as a network node is. Those reasons were decided when
    # the metrics were loaded and are reported for every active node
    # whatever its role, because a node's role is not knowable
    # without its metrics; a reader who wants only hypervisors
    # filters against the node list, which publishes the role. A
    # node which is not active is left out, as the node list already
    # publishes its state, and a non-hypervisor with fresh metrics
    # is not a candidate at all, so it is not "excluded" either.
    resources = {
        'total': {
            'cpu_available': 0,
            'ram_available': 0,
            'capacity_degraded': capacity_degraded,
        },
        'per_node': {},
        'excluded': {
            n: dict(reason)
            for n, reason in metrics_unavailable.items()},
        'cluster_capacity': cluster_capacity,
    }

    for n in metrics.keys():
        if not metrics[n].get('is_hypervisor', False):
            continue

        queue_reason = _queue_exclusion_reason(metrics[n])
        if queue_reason:
            resources['excluded'][n] = queue_reason
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
            metrics[n].get('cpu_max_per_instance', 0)

        cpu_base, _ = _schedulable_threads(metrics[n])
        resources['per_node'][n]['cpu_schedulable'] = cpu_base
        # A clamped reservation means cpu_schedulable (or
        # memory_reserved_mb below) is a safety floor or cap rather
        # than the configured reservation's arithmetic -- without the
        # flag a node reserving all of its threads is
        # indistinguishable from one with a genuinely spare thread
        # (issue 4201).
        resources['per_node'][n]['cpu_reservation_clamped'] = bool(
            metrics[n].get('cpu_reservation_clamped', False))
        resources['per_node'][n]['memory_reservation_clamped'] = bool(
            metrics[n].get('memory_reservation_clamped', False))
        hard_max_cpus = cpu_base * config.CPU_OVERCOMMIT_RATIO
        measured_cpus = metrics[n].get('cpu_total_instance_vcpus', 0)
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
        # When the node enumerated the domains cpu_measured counts, on
        # the node's own clock (None from a node which predates the
        # field). A figure on a shared node can move because of any
        # instance; this says whether the measurement is recent enough
        # to have seen a particular domain start or stop, compared with
        # the libvirt_returned_at on that instance's power event.
        resources['per_node'][n]['domains_enumerated_at'] = \
            metrics[n].get('domains_enumerated_at')
        resources['per_node'][n]['cpu_committed'] = committed_cpus
        current_cpu = max(measured_cpus, committed_cpus)
        resources['per_node'][n]['cpu_available'] = hard_max_cpus - current_cpu
        # A node packed beyond the cap (for example after the cap was
        # lowered) reports negative per-node headroom, which is honest,
        # but must not eat into the cluster total other nodes provide.
        resources['total']['cpu_available'] += max(
            0, resources['per_node'][n]['cpu_available'])

        resources['per_node'][n]['cpu_load_1'] = metrics[n].get(
            'cpu_load_1', 0)
        resources['per_node'][n]['cpu_load_5'] = metrics[n].get(
            'cpu_load_5', 0)
        resources['per_node'][n]['cpu_load_15'] = metrics[n].get(
            'cpu_load_15', 0)

        # Memory. As with CPU, this must match _has_sufficient_ram():
        # published headroom is bounded by the committed ledger as
        # well as the measurements, because that ledger is what both
        # the pre-filter and the guard refuse on.
        reserved = _memory_reserved_mb(metrics[n])
        resources['per_node'][n]['memory_reserved_mb'] = reserved
        resources['per_node'][n]['ram_max_per_instance'] = \
            (metrics[n].get('memory_available', 0) - reserved)
        resources['per_node'][n]['ram_max'] = \
            metrics[n].get('memory_max', 0) * \
            config.RAM_OVERCOMMIT_RATIO
        committed_mb = row['used_memory_mb'] if row else 0
        resources['per_node'][n]['ram_committed'] = committed_mb
        ram_available = \
            (metrics[n].get('memory_max', 0) * config.RAM_OVERCOMMIT_RATIO -
             metrics[n].get('memory_total_instance_actual', 0))
        if row:
            ram_available = min(
                ram_available, row['limit_memory_mb'] - committed_mb)
        resources['per_node'][n]['ram_available'] = ram_available
        resources['total']['ram_available'] += max(
            0, resources['per_node'][n]['ram_available'])

        # Disk. Each node publishes its own reservation as a metric, so we
        # subtract the candidate node's per-host floor (falling back to our
        # config default only for a stale metrics row mid-upgrade).
        reservation = metrics[n].get(
            'disk_reservation_gb', config.NODE_DISK_RESERVATION_GB)
        disk_free = int(metrics[n].get(
            'disk_free_instances', '0')) / GiB
        disk_free -= reservation
        resources['per_node'][n]['disk_available'] = disk_free
        # The capacity row's disk counters are published for the same
        # reason cpu_limit and cpu_committed are: disk_available is
        # headroom, which moves whenever a sibling creates or deletes,
        # where the ledger's ceiling is stable under load (issue 4208).
        # One capacity row covers every dimension, so
        # cpu_committed_row_present above already says whether these
        # zeros mean "unsized node" -- as does a None disk_limit_gb,
        # which deliberately has no fallback for the same reason
        # cpu_limit does not.
        resources['per_node'][n]['disk_limit_gb'] = \
            row['limit_disk_gb'] if row else None
        resources['per_node'][n]['disk_committed_gb'] = \
            row['used_disk_gb'] if row else 0

        # Instance count
        resources['per_node'][n]['instances_total'] = metrics[n].get(
            'instances_total', 0)
        resources['per_node'][n]['instances_active'] = metrics[n].get(
            'instances_active', 0)

    return resources


def summarize_cluster_resources():
    """Summarize the cluster's resources from a fresh metrics snapshot.

    This is what /admin/resources publishes. It needs only one fresh read of
    the node metrics and of the capacity counters, not a Scheduler, so the
    admin API does not have to construct a placement decider to call it.
    """
    unavailable = {}
    metrics = get_active_node_metrics(unavailable=unavailable)
    return _summarize_resources(metrics, unavailable)


class Scheduler:
    def __init__(self):
        # This UUID doesn't really mean much, except as a way of tracing the
        # behaviour of a single instance of the scheduler object in logs.
        self.__uuid = str(uuid.uuid4())
        self.log = LOG.with_fields({'scheduler_instance': self.__uuid})

        self.metrics = {}
        # Why each active node missing usable metrics was unusable, from
        # the same refresh as self.metrics. Only the resources summary
        # reads it; placement never does.
        self.metrics_unavailable = {}
        self.metrics_updated = 0

        self.refresh_metrics()

    def refresh_metrics(self):
        unavailable = {}
        self.metrics = get_active_node_metrics(unavailable=unavailable)
        self.metrics_unavailable = unavailable
        self.metrics_updated = time.time()

    def _oldest_node_metrics_age(self):
        # The age of the oldest metrics row behind any node offered to
        # this decision, which is the decision's real staleness bound:
        # metrics_updated says when this scheduler last fetched, not when
        # the resources daemons last measured. None when no node carried
        # a usable row (issue 4521).
        oldest = None
        for node_metrics in self.metrics.values():
            row_timestamp = node_metrics.get('metrics_timestamp')
            if not row_timestamp:
                continue
            age = time.time() - row_timestamp
            if oldest is None or age > oldest:
                oldest = age
        if oldest is None:
            return None
        return round(oldest, 2)

    def _has_reasonable_queue_state(self, log_ctx, node, snapshot):
        reason = _queue_exclusion_reason(snapshot.node_metrics(node))
        if reason:
            log_ctx.with_fields({'node': node, **reason}).debug(
                'Excluding node with many queued jobs')
            return False, reason

        return True, None

    def _capacity_by_node(self):
        """The counters keyed by node uuid, and whether the read failed."""
        return _capacity_by_node()

    def _satisfies_hard_affinity(self, shape, node, memo,
                                 require_with, require_without):
        """Does this node satisfy the shape's hard affinity constraints?

        Constraints match the tags of *instances already placed on the
        node*, exactly as the scorer does -- which here means calling
        the same _affinity_neighbour_skip() rather than restating its
        four conditions, so a filter and a scorer cannot disagree about
        what counts as a neighbour. Shaken Fist has no node capability
        tags, so there is nothing else they could match. The namespace
        scope is inherited rather than chosen: crossing it would let a
        caller learn what another tenant is running by watching where
        their own instances refuse to land, which is why
        require_without_tag is a within-namespace constraint and not an
        isolation primitive.
        """
        node_instances = self._placed_instances(node, memo)
        if node_instances is None:
            return False, {'reason': 'node row not found'}

        present = set()
        for instance_uuid, i in node_instances:
            if _affinity_neighbour_skip(shape, instance_uuid, i):
                continue
            for tag in i.tags:
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

    def _has_sufficient_cpu(self, log_ctx, shape, node, snapshot):
        """A cheap CPU pre-filter, denominated in both ledgers.

        This is not the admission decision. The real guard is the
        atomic UPDATE that ``Instance.place_instance()`` makes against
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

        ``snapshot`` carries both, charged for anything this decision
        has already placed. A node with no committed row is charged
        nothing on that account and measured against the live figure: it
        is a node mid-upgrade, or one the reconciler declined to size,
        and admission will let it through unguarded, so this
        filter must not refuse on a ledger which does not exist.
        """
        node_metrics = snapshot.node_metrics(node)
        cpu_base, from_fallback = _schedulable_threads(node_metrics)
        hard_max_cpus = cpu_base * config.CPU_OVERCOMMIT_RATIO
        measured_cpus = node_metrics.get('cpu_total_instance_vcpus', 0)
        cpus = shape.cpus

        # A node with a capacity row is guarded by that row's own limit,
        # so test the guard's arithmetic exactly rather than a live
        # re-derivation which can differ from it by a floor().
        row = snapshot.capacity_row(node)
        limit_cpus = row['limit_cpus'] if row else hard_max_cpus
        committed_cpus = row['used_cpus'] if row else 0

        # An instance already placed here is in used_cpus, and a
        # reschedule which lands it back on the same node does not
        # charge it a second time (place_instance() early-outs on an
        # unchanged placement), so neither does this filter.
        if committed_cpus and shape.placed_on == node:
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

    def _has_sufficient_ram(self, log_ctx, shape, node, snapshot):
        # There are two things to track here... We must always leave the
        # node's published memory reservation (operating system tasks, plus
        # cluster-wide daemons on infra-role nodes) untouched -- assume
        # there is no overlap with existing VMs when checking this. Note as
        # well that metrics are in MB...
        node_metrics = snapshot.node_metrics(node)
        memory = shape.memory_mb
        reserved = _memory_reserved_mb(node_metrics)
        available = node_metrics.get('memory_available', 0) - reserved
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
            node_metrics.get('memory_total_instance_actual', 0) + memory)
        memory_max = node_metrics.get('memory_max', 0)
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
        row = snapshot.capacity_row(node)
        if row:
            committed_mb = row['used_memory_mb']
            # An instance already placed here is in used_memory_mb, and a
            # reschedule which lands it back on the same node does not
            # charge it a second time.
            if committed_mb and shape.placed_on == node:
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

    def _has_sufficient_disk(self, log_ctx, shape, node, snapshot):
        requested_disk = shape.disk_gb
        node_metrics = snapshot.node_metrics(node)

        # The candidate node publishes its own disk reservation as a metric, so
        # a remote evaluator honours that node's per-host floor (falling back to
        # our config default only for a stale metrics row mid-upgrade).
        reservation = node_metrics.get(
            'disk_reservation_gb', config.NODE_DISK_RESERVATION_GB)
        disk_free = int(node_metrics.get('disk_free_instances', '0')) / GiB
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

    def _has_idle_disk_bandwidth(self, log_ctx, shape, node, snapshot):
        # shape is unread here, and is in the signature only so that
        # every filter stage takes the same arguments. This stage sheds
        # load rather than accounting for capacity: a saturated disk is
        # a bad place to start anything at all, whatever size it is, so
        # there is nothing about the request to read.
        #
        # We also avoid starting new instances on hypervisors with busy disk.
        # busy_time is in milliseconds per second, so a value of 1,000 is 100%
        # busy. You can record more than 100% if there is more than one disk
        # in the system doing IO at the time.
        busy_time = float(
            snapshot.node_metrics(node).get(DISK_BUSY_PER_SECOND_METRIC, 0))
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
            # The stage rides on the exception as well as in the
            # message, because the create path publishes it as a
            # machine-readable field and must not parse the prose.
            raise exception_class(message, stage=stage)

        add_event_multi(
            EVENT_TYPE_AUDIT, related_objects,
            f'schedule at stage {stage}', extra=extra)

    def find_candidates(self, inst, candidates=None):
        """Candidate nodes for one instance, best first.

        The one-shape caller of find_candidates_for_shapes(). It turns
        the instance into a shape, holds the attribute memo open for the
        whole decision, and addresses the decision's audit events to the
        instance and to any node the caller forced.
        """
        related_objects = [inst]
        if candidates:
            for node_uuid in candidates:
                related_objects.append(('node', node_uuid))

        # PlacementShape.from_instance() reads several of this instance's
        # attributes -- its placement and its affinity among them -- and
        # every one of those reads fetches the same instance_attributes
        # row. Memoising for the duration of the decision makes it one
        # fetch. Nothing in this block writes an attribute, and the memo
        # is discarded on the way out, so no caller observes different
        # staleness than it did before.
        with util_general.RecordedOperation('schedule', inst), \
                inst.attribute_memo():
            assignment = self.find_candidates_for_shapes(
                [PlacementShape.from_instance(inst)], candidates=candidates,
                related_objects=related_objects,
                log_ctx=self.log.with_fields({'instance': inst}))
            return assignment[0]

    def find_candidates_for_shapes(self, shapes, candidates=None,
                                   related_objects=None, log_ctx=None):
        """Candidate nodes for a set of requested shapes, all or nothing.

        Every shape is walked through the whole filter, affinity and
        ranking pipeline in its own right, and is then charged to the
        node at the head of its own ranked list before the next shape is
        walked. A set therefore cannot spend the same headroom twice:
        each shape sees the ones before it already sitting on their
        nodes, on the measured figures and on the committed counters
        both. Those charges live in a snapshot built for this call and
        discarded with it -- nothing here admits anything, and the real
        guard is still the atomic UPDATE at admission.

        All or nothing. A shape which runs out of candidates at any
        stage raises exactly what that stage raises for a single
        request, with the same exception class and the same stage, and
        the assignment built so far is discarded. The exception also
        carries the refused shape's index in ``shapes``, because a
        stage on its own tells a caller that something did not fit but
        not what, which a caller asking about several shapes cannot
        act on. A caller is given a placement for every shape it asked
        for, or none at all. There is nothing to roll back when that
        happens, because the charges were never anywhere but the
        snapshot. The audit events are not
        unwound, though: a set refused on its fourth shape has already
        published a full stage sequence for the three before it, which
        are durable records of placements that did not happen. A
        caller which cares has to decide what to do about that, like
        the shape index noted at the schedule inputs event below.

        Shapes are walked largest first -- by memory, then vCPUs, then
        disk. That is first-fit-decreasing, and it is greedy: a set this
        refuses may still be one which some other assignment could have
        hosted, so a refusal here is a lower bound on what the cluster
        will take rather than a proof that it will not take it. Searching
        for an assignment instead would describe a cluster we do not
        run, because the ranker which chooses each shape's node spreads
        allocation away from the most committed nodes rather than
        packing, and that is the behaviour a caller will actually meet.

        A set's own members are charged for capacity but are not
        affinity neighbours of each other. Both affinity stages match
        the tags of the instances already placed on a node, which they
        read as rows, and a shape has no row; so a shape assigned
        earlier in this call is invisible to the affinity stages of the
        shapes after it. A caller asking for a set whose members prefer
        each other's company gets that preference scored against the
        cluster as it stands, not against the set.

        ``related_objects`` addresses the decision's audit events, in
        the form add_event_multi() takes them, and a call given none
        publishes none -- which is how a speculative evaluation stays
        out of the event log.

        Returns a mapping of each shape's index in ``shapes`` to its
        ordered candidate list, best first, in the order the shapes were
        given. The head of each list is the node that shape was charged
        to.

        Only the heads are an assignment. A caller which walks past one
        of them -- as place_on_candidates() does when admission refuses
        the head -- has left this call's arithmetic behind, because
        every shape after it was ranked as though it were on the node
        it skipped. Two shapes of one set can then be placed on the
        same node, each having been told the other was elsewhere, so a
        caller which does not take a head has to ask again for the
        shapes it has not placed.
        """
        if not shapes:
            # Refused rather than answered with {}, which a caller
            # asking whether a set fits would read as "all of it does".
            raise ValueError(
                'find_candidates_for_shapes() needs at least one shape')

        if related_objects is None:
            related_objects = []
        if log_ctx is None:
            log_ctx = self.log

        add_event_multi(
            EVENT_TYPE_AUDIT, related_objects, 'started scheduling')

        # Refresh metrics if its too old, or there are no nodes. Once for
        # the whole set, because a set is one decision: every shape in it
        # is placed against one reading of the cluster, charged with the
        # set's own earlier assignments and with nothing else.
        metrics_refreshed = False
        diff = time.time() - self.metrics_updated
        if diff > config.SCHEDULER_CACHE_TIMEOUT or len(self.metrics) == 0:
            self.refresh_metrics()
            metrics_refreshed = True

        snapshot = PlacementSnapshot(self.metrics, self._capacity_by_node)

        # One memo for the whole set, beside the snapshot and for the
        # same reason: a set places nothing, so no node's placed
        # instances can change while it is being walked, and a node
        # considered by every shape should be read once rather than
        # once per shape. The committed counters are already read this
        # way.
        placements = {}

        assignment = {}
        for index in _shapes_largest_first(shapes):
            shape = shapes[index]
            try:
                assignment[index] = self._find_candidates_for_shape(
                    shape, candidates, related_objects, log_ctx, snapshot,
                    metrics_refreshed, placements)
            except exceptions.SchedulerException as e:
                # Which shape was being walked is attached here rather
                # than at each of the stages which raise, because this
                # loop is the only scope that knows the index: a stage
                # is handed a shape and has no idea where in the
                # caller's list it came from. Set only when it is not
                # already set, so a future refusal raised closer to
                # the shape than this stays authoritative.
                #
                # A bare re-raise, so the class, the message, the stage
                # and the traceback are all exactly what the stage
                # raised. Nothing else about the refusal changes, and a
                # caller which does not read the index cannot tell the
                # difference.
                if e.shape is None:
                    e.shape = index
                raise
            # The head of the list is where the caller will put this
            # shape, so that is what the shapes after it are told to
            # expect. Charged even for the last shape in a set, so that
            # the snapshot a decision ends with describes the whole of
            # what it handed out.
            snapshot.charge(shape, assignment[index][0])

        return {index: assignment[index] for index in range(len(shapes))}

    def _find_candidates_for_shape(self, shape, candidates, related_objects,
                                   log_ctx, snapshot, metrics_refreshed,
                                   placements):
        """Walk one shape through the filter, affinity and ranking stages.

        Every figure comes from the snapshot rather than from this
        scheduler's own metrics, which is what makes a shape see the
        shapes charged before it in the same set.
        """
        # Record the inputs to the scheduling decision so failures can be
        # diagnosed from the event log alone. The disk spec itself is
        # published alongside the shape's summed figure, because an
        # operator reading a refusal wants to see the disks which
        # produced the total.
        #
        # One of these is published per shape, and they carry nothing
        # which tells them apart: a caller asking for more than one
        # shape with event targets has to add the shape's index to this
        # payload, and amend the procedure in
        # docs/operator_guide/scheduler.md, which finds a decision's
        # inputs by looking for the pass whose forced_candidates is
        # false. That works today only because every production caller
        # asks for exactly one shape.
        add_event_multi(
            EVENT_TYPE_AUDIT, related_objects,
            'schedule inputs',
            extra={
                'requested_affinity': shape.affinity,
                'requested_cpus': shape.cpus,
                'requested_memory_mb': shape.memory_mb,
                'requested_disk_gb': shape.disk_gb,
                'disk_spec': shape.disk_spec,
                'namespace': shape.namespace,
                'forced_candidates': candidates is not None,
                # Measured after the decision's refresh, so this is the
                # age of the snapshot it reads. The pre-refresh
                # age is usually old precisely because exceeding the
                # cache timeout is what triggers the refresh, and
                # publishing it read as placement on hour-old metrics
                # when the decision used fresh ones (issue 4521).
                'metrics_age_seconds': round(
                    time.time() - self.metrics_updated, 2),
                'metrics_refreshed': metrics_refreshed,
                'oldest_node_metrics_age_seconds':
                    self._oldest_node_metrics_age(),
            })

        # An empty forced list is still forced: the preflight
        # redirect builds "every node except this one", and when
        # that set is empty it means there is nowhere else to go,
        # not "choose from the whole cluster" -- falling open here
        # re-offered the one node the caller built the list to
        # exclude (issue 4001). The pre_schedule stage below turns
        # the empty set into a LowResourceException.
        if candidates is not None:
            # Copied, because a forced list belongs to the caller and
            # the stages below filter in place -- and because every
            # shape in a set starts from the whole of it.
            candidates = list(candidates)
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule forced candidates',
                extra={'candidates': candidates})
            for n in candidates:
                if not snapshot.has_node(n):
                    # The metrics dict carries a key for every node
                    # the active prefilter returned, even one whose
                    # metrics were stale or empty, so a missing key
                    # means the candidate was not in the active-node
                    # list at all (or its metrics row was
                    # unreadable). Say so: the exception's message
                    # is recorded against the user's instance, and a
                    # bare node UUID explains nothing (issue 4113).
                    add_event_multi(
                        EVENT_TYPE_AUDIT, related_objects,
                        f'schedule candidate {n} is not an active node, '
                        'aborting',
                        extra={'candidates': candidates})
                    raise exceptions.CandidateNodeNotFoundException(
                        f'candidate node {n} is not in the active node '
                        'list, or its metrics were unreadable')
        else:
            candidates = snapshot.nodes()
        add_event_multi(
            EVENT_TYPE_AUDIT, related_objects,
            'schedule initial candidates',
            extra={'candidates': candidates})
        self._log_and_raise_on_error(
            related_objects, 'pre_schedule', candidates)

        # Ensure all specified nodes are hypervisors
        dropped = {}
        for c in list(candidates):
            if not snapshot.node_metrics(c).get('is_hypervisor', False):
                dropped[c] = {'reason': 'not a hypervisor'}
                candidates.remove(c)
        self._log_and_raise_on_error(
            related_objects, 'is_hypervisor', candidates, dropped=dropped)

        # Can we host that many vCPUs?
        dropped = {}
        for c in list(candidates):
            max_cpu = snapshot.node_metrics(c).get(
                'cpu_max_per_instance', 0)
            if shape.cpus > max_cpu:
                dropped[c] = {
                    'reason': 'requested vCPUs exceed per-instance max',
                    'cpu_max_per_instance': max_cpu,
                    'requested_cpus': shape.cpus,
                }
                candidates.remove(c)
        self._log_and_raise_on_error(
            related_objects, 'cpu_max_per_instance', candidates,
            dropped=dropped)

        # Do we have enough idle CPU? This reads the capacity
        # counters as well as the metrics, because a node whose
        # ledger is full measures as idle until its instances boot.
        #
        # A failed read is published before the stage runs, and only
        # then: an empty-but-readable table is the ordinary state of
        # a cluster the reconciler has not reached yet (P7), so
        # eventing on emptiness would fire on every create there and
        # say nothing. Nothing below behaves differently either way
        # -- the counters this decision cannot see simply do not
        # prune anything, and the guard still refuses correctly --
        # so this records that the pre-filter was flying on
        # measurement alone, which is otherwise invisible in the
        # instance's event trail.
        capacity_degraded = snapshot.ensure_capacity()
        if capacity_degraded:
            add_event_multi(
                EVENT_TYPE_AUDIT, related_objects,
                'schedule could not read the capacity counters',
                extra={
                    'candidates': candidates,
                    'degraded_stages': ['sufficient_idle_cpu',
                                        'sufficient_idle_memory'],
                    'effect': ('capacity pre-filtering falls back to '
                               'measurement only; admission is '
                               'unchanged and still guarded'),
                })
        dropped = {}
        for c in list(candidates):
            ok, reason = self._has_sufficient_cpu(
                log_ctx, shape, c, snapshot)
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
            ok, reason = self._has_sufficient_ram(
                log_ctx, shape, c, snapshot)
            if not ok:
                dropped[c] = reason
                candidates.remove(c)
        self._log_and_raise_on_error(
            related_objects, 'sufficient_idle_memory', candidates,
            dropped=dropped)

        # Do we have enough free disk?
        dropped = {}
        for c in list(candidates):
            ok, reason = self._has_sufficient_disk(
                log_ctx, shape, c, snapshot)
            if not ok:
                dropped[c] = reason
                candidates.remove(c)
        self._log_and_raise_on_error(
            related_objects, 'sufficient_free_disk', candidates,
            dropped=dropped)

        # Each candidate's placements are read once and memoised, so
        # a node considered twice is only fetched once. The memo is
        # shared by the hard constraint stage below and the scorer
        # after it, so a create which uses both pays for one read --
        # and it belongs to the whole decision, so a set of shapes
        # pays for one read however many shapes consider the node.

        requested_affinity = shape.affinity
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
                    shape, c, placements, require_with, require_without)
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
                skipped = _affinity_neighbour_skip(shape, instance_uuid, i)
                if skipped:
                    considered.append(skipped)
                    continue

                # This neighbour's tags, read once. Instance.tags is a
                # metadata read and nothing caches it, so each access
                # is a database round trip -- and the loops below read
                # it once *per tag the caller named*, not once each, so
                # the cost of scoring a neighbour grew with the size of
                # the affinity specification for no additional
                # information. Both loops and the audit payload below
                # now read this local.
                #
                # Safe because a decision is a snapshot. This call
                # places nothing, so no neighbour's tags can change
                # under it, and the placed instances these were read
                # from are memoised for the whole decision for the same
                # reason: a node considered by several shapes should be
                # read once, not once per shape.
                neighbour_tags = i.tags

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
                    if tag in neighbour_tags:
                        matched[tag] = matched.get(tag, 0) + 1
                        contribution += 1
                for tag in binary_spec[
                        instance.Instance.AFFINITY_PREFER_WITHOUT]:
                    if tag in neighbour_tags:
                        matched[tag] = matched.get(tag, 0) - 1
                        contribution -= 1
                considered.append({
                    'instance_uuid': instance_uuid,
                    'tags': list(neighbour_tags),
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
            ok, reason = self._has_reasonable_queue_state(
                log_ctx, c, snapshot)
            if not ok:
                dropped[c] = reason
                candidates.remove(c)
        self._log_and_raise_on_error(
            related_objects, 'queue_state', candidates, dropped=dropped)

        # Are the disks really busy?
        dropped = {}
        for c in list(candidates):
            ok, reason = self._has_idle_disk_bandwidth(
                log_ctx, shape, c, snapshot)
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
            raw_load = snapshot.node_metrics(c).get('cpu_load_1', 0)
            denom, from_fallback = _schedulable_threads(
                snapshot.node_metrics(c))
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
            row = snapshot.capacity_row(c)
            if row and row['limit_memory_mb'] > 0:
                committed_mb = max(
                    row['used_memory_mb'],
                    snapshot.node_metrics(c).get(
                        'memory_total_instance_actual', 0))
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
        # passed every admission filter above. The caller walks this
        # list against a capacity guard which can refuse the head of
        # it, so discarding the rest turns one refusal into a failed
        # create on a cluster with room: in merge CI on 2026-08-14
        # three viable nodes were cut to one, that one was refused,
        # and five tests got a 507 apiece.
        # Callers walk the list on failure, so the tail order
        # matters as much as the head.
        weights = {}
        ordered = []
        for bucket in sorted(by_load):
            tier = by_load[bucket]
            for c in tier:
                raw_load = snapshot.node_metrics(c).get('cpu_load_1', 0)
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
        """Summarize cluster resources from this scheduler's metrics snapshot.

        Refreshes the snapshot if it is stale. No production code calls
        this: ``/admin/resources`` uses the module-level
        ``summarize_cluster_resources()``, and this bound form is retained
        for the scheduler test suite, which drives it from a constructed
        Scheduler.
        """
        # Refresh metrics if its too old, or there are no nodes.
        diff = time.time() - self.metrics_updated
        if diff > config.SCHEDULER_CACHE_TIMEOUT or len(self.metrics) == 0:
            self.refresh_metrics()

        return _summarize_resources(self.metrics, self.metrics_unavailable)
