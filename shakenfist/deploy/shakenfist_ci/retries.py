# Copyright 2019 Michael Still and contributors
"""Wait out an answer the cluster is entitled to change its mind about.

Two loops, deliberately not one. retry_while_transient() re-issues the
same request until its status stops being transient.
wait_for_capacity() polls a *different* thing -- the published resource
roster -- and hands control back once, so that its caller can re-issue
a create which would otherwise leave an error-deleted instance behind
on every attempt.

Kept free of imports from the rest of this suite (and of
shakenfist_client, which is not a test dependency of the repository) so
the unit tests in shakenfist/tests can load it by path and exercise the
loops with a fake clock. The functional suite is a client of a deployed
cluster and is not otherwise importable from there. That is also why
wait_for_capacity() takes its poll as a callable rather than a client.
"""

import time


def retry_while_transient(request, transient_statuses, deadline,
                          clock=time.time, sleep=time.sleep, interval=10):
    """Call request() until its status stops being transient, or time is up.

    request returns a (status, body) pair. A status in transient_statuses
    is retried every interval seconds until clock() passes deadline, at
    which point the transient answer is returned as it stands -- giving
    up is the caller's assertion to fail with the body in hand, not an
    exception from here. Any other status is returned immediately, so a
    refusal the caller means to assert on is never waited out into
    something else.
    """
    while True:
        status, body = request()
        if status not in transient_statuses or clock() > deadline:
            return status, body
        sleep(interval)


# The three dimensions the scheduler pre-filters a candidate node on
# (_has_sufficient_cpu(), _has_sufficient_ram() and
# _has_sufficient_disk()). A predicate which models only one of them can
# be permanently satisfied while the server is permanently refusing, and
# the caller then re-issues its create as fast as two HTTP round trips
# allow until its deadline -- turning one 507 into hundreds. Requests are
# denominated in the same units /admin/resources publishes: whole cpus,
# megabytes, gigabytes.
DIMENSIONS = ('cpus', 'memory_mb', 'disk_gb')


def requested_disk_gb(disk_spec):
    """How much disk a create asks for, counted as the scheduler counts it.

    _has_sufficient_disk() sums the 'size' of each disk which has one and
    ignores the sizeless ones (a CD ROM is exactly the size of its base
    image), so this does the same. A caller which passes no disk spec at
    all asks for nothing.
    """
    requested = 0
    for disk in disk_spec or []:
        if not isinstance(disk, dict):
            continue
        size = disk.get('size')
        if size is None:
            continue
        try:
            requested += int(size)
        except (TypeError, ValueError):
            continue
    return requested


def node_available_cpus(node_entry):
    """How many cpus a create may actually be admitted against on one node.

    /admin/resources publishes two ledgers deliberately, and they
    disagree during exactly the window this wait exists to survive.
    'cpu_available' is the live overcommit arithmetic; 'cpu_limit' is
    the capacity row's limit_cpus, which is what admission's guarded
    UPDATE is measured against and which refreshes only once a
    reconcile period. Whichever binds is what a create would be
    allowed, so take the smaller of the two.

    'cpu_limit' is None exactly when the node has no capacity row, in
    which case admission is guarded by nothing and the published
    headroom is all there is to read.

    'cpu_max_per_instance' is a third bound of a different kind: it
    caps the largest single instance the node will take however much
    aggregate headroom it has, so a create larger than it is refused
    permanently rather than transiently. A zero there means the node
    has published no metrics for it rather than that it will take
    nothing, so it is read only when it is non-zero.
    """
    available = node_entry.get('cpu_available', 0)
    limit = node_entry.get('cpu_limit')
    if limit is not None:
        available = min(available, limit - node_entry.get('cpu_committed', 0))
    largest = node_entry.get('cpu_max_per_instance')
    if largest:
        available = min(available, largest)
    return available


def node_available_memory_mb(node_entry):
    """How much memory a create may actually be admitted against on one node.

    Unlike cpus this needs no manual clamp against the capacity row:
    summarize_resources() has already taken min(overcommit arithmetic,
    limit_memory_mb - committed) before publishing 'ram_available'.

    'ram_max_per_instance' is the node's memory_available less its
    published reservation, which is _has_sufficient_ram()'s first
    check and is a bound on one instance rather than on the aggregate.
    It is legitimately zero or negative on a full node, so unlike the
    cpu equivalent it is read whenever it is present at all.
    """
    available = node_entry.get('ram_available', 0)
    largest = node_entry.get('ram_max_per_instance')
    if largest is not None:
        available = min(available, largest)
    return available


def node_available_disk_gb(node_entry):
    """How much instance disk one node has, in gigabytes.

    summarize_resources() has already subtracted that node's own
    published reservation, which is the same arithmetic
    _has_sufficient_disk() does, so the published figure is the answer.
    """
    return node_entry.get('disk_available', 0)


HEADROOM_READERS = {
    'cpus': node_available_cpus,
    'memory_mb': node_available_memory_mb,
    'disk_gb': node_available_disk_gb,
}

ZERO_HEADROOM = {dimension: 0 for dimension in DIMENSIONS}


def node_headroom(node_entry):
    """What one roster entry says the node has left, per dimension."""
    return {dimension: HEADROOM_READERS[dimension](node_entry)
            for dimension in DIMENSIONS}


def coverage_of(headroom, request):
    """How much of a create one node could take, and what holds it back.

    Returns a (fraction, dimension) pair. The fraction is the smallest
    available/requested ratio across the dimensions the create actually
    asks for, so 1.0 or more means every dimension fits and the node
    could take the whole create; the dimension is the one which produced
    that smallest ratio, which is the thing the create is waiting on.

    A dimension the create asks nothing of cannot bind and is skipped --
    a sizeless disk spec asks for no disk, and a node with no disk left
    would otherwise never satisfy it.
    """
    worst = None
    binding = None
    for dimension in DIMENSIONS:
        requested = request.get(dimension) or 0
        if requested <= 0:
            continue
        available = headroom.get(dimension) or 0
        covered = available / requested
        if worst is None or covered < worst:
            worst = covered
            binding = dimension
    if worst is None:
        return float('inf'), None
    return worst, binding


def best_node(per_node, request, node=None):
    """The node most likely to admit this create, with its headroom.

    Returns a (headroom, coverage, binding dimension) triple.

    For a pinned create that is the target node's own figures, and a
    target absent from per_node is zero rather than a KeyError: the
    roster skips a node whose queue is over the unreasonable length
    and a node which has not published metrics yet, both of which are
    transient and both of which are what a caller here is waiting out.
    A binding dimension of None means the roster said nothing about the
    node at all, rather than that nothing binds.

    For an unpinned create it is the single node which covers the
    largest fraction of the request, never a cluster total -- those
    fields are sums across nodes and an instance has to fit on one of
    them.
    """
    if node is not None:
        entry = per_node.get(node)
        if entry is None:
            return dict(ZERO_HEADROOM), 0.0, None
        headroom = node_headroom(entry)
        coverage, binding = coverage_of(headroom, request)
        return headroom, coverage, binding

    # Seeded from the first node rather than from a zero, so that a
    # roster of nodes which all cover nothing still names the dimension
    # they are short of. Seeding with 0.0 meant a node whose coverage was
    # exactly zero never beat the seed, and the wait recorded "nothing
    # bound" for a create which was refused for cpus.
    best = None
    for entry in per_node.values():
        headroom = node_headroom(entry)
        coverage, binding = coverage_of(headroom, request)
        if best is None or coverage > best[1]:
            best = (headroom, coverage, binding)
    if best is None:
        return dict(ZERO_HEADROOM), 0.0, None
    return best


def wait_for_capacity(poll, cpus, memory_mb, disk_gb, node, deadline,
                      clock=time.time, sleep=time.sleep, interval=10,
                      minimum_sleep=0):
    """Wait until the cluster could admit a create of this size.

    poll() returns the /admin/resources dict. It is injected rather
    than called through a client because this module imports nothing
    from the suite or from shakenfist_client, so that the unit tests
    can load it by path.

    The size is the whole size: a create is refused for whichever of
    cpus, memory and disk the candidate node is short of, so a wait
    which modelled only one of them would report "there is room now"
    while the server went on refusing. See DIMENSIONS above.

    Three things end the wait, and the returned record says which:

    * The predicate is satisfied ('satisfied' True), so the caller
      should re-issue its create immediately.
    * total['capacity_degraded'] is set, so the capacity mapping the
      predicate rests on could not be read and waiting informed would
      be waiting on noise. One interval is slept blind and the record
      says 'mode': 'degraded', which is the caller's signal to retry
      the create at this cadence rather than to conclude anything.
    * The deadline passed. The record is unsatisfied and carries the
      roster, which is the diagnosis a caller's failure message needs.

    A poll which raises is none of the three: the endpoint being
    briefly unreachable is not evidence about capacity, so it is
    recorded in 'poll_errors' and the wait continues.

    minimum_sleep is the floor under a satisfied wait, and exists
    because a satisfied wait hands control straight back to a caller
    which has just been refused. If the refusal was for something this
    predicate cannot see -- a pre-filter it does not model, or the
    admission guard losing a race it cannot observe -- then satisfied
    is the permanent answer and the caller would re-issue as fast as
    two HTTP round trips allow until its deadline. A caller passes zero
    for its first wait, where believing the roster immediately is the
    common and correct case, and one interval thereafter.
    """
    started = clock()
    request = {'cpus': cpus, 'memory_mb': memory_mb, 'disk_gb': disk_gb}
    record = {
        'cpus': cpus,
        'memory_mb': memory_mb,
        'disk_gb': disk_gb,
        'node': node,
        'mode': 'informed',
        'polls': 0,
        'degraded_polls': 0,
        'poll_errors': [],
        'headroom_at_start': None,
        'headroom_at_end': None,
        'per_node': None,
        'binding_dimension': None,
        'satisfied': False,
        'seconds_waited': 0.0,
    }

    def done():
        record['seconds_waited'] = clock() - started
        return record

    def done_paced():
        """Hand back no sooner than minimum_sleep after starting."""
        slept = clock() - started
        if slept < minimum_sleep:
            sleep(minimum_sleep - slept)
        return done()

    while True:
        record['polls'] += 1
        try:
            resources = poll()
        except Exception as e:
            record['poll_errors'].append('%s: %s' % (e.__class__.__name__, e))
            resources = None

        if resources is not None:
            total = resources.get('total') or {}
            per_node = resources.get('per_node') or {}
            record['per_node'] = per_node

            if total.get('capacity_degraded'):
                record['mode'] = 'degraded'
                record['degraded_polls'] += 1
                sleep(interval)
                return done()

            headroom, coverage, binding = best_node(per_node, request, node)
            record['headroom_at_end'] = headroom
            if record['headroom_at_start'] is None:
                record['headroom_at_start'] = headroom
                # What the create was short of when it was refused, which
                # is the question phase 5 asks of this data. Only the
                # first poll can answer it: by the last one the shortfall
                # is usually gone, which is why the wait ended.
                if coverage < 1.0:
                    record['binding_dimension'] = binding

            if coverage >= 1.0:
                record['satisfied'] = True
                return done_paced()

        if clock() > deadline:
            return done()
        sleep(interval)
