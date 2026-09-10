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
    """
    available = node_entry.get('cpu_available', 0)
    limit = node_entry.get('cpu_limit')
    if limit is not None:
        available = min(available, limit - node_entry.get('cpu_committed', 0))
    return available


def available_cpus(per_node, node=None):
    """The largest create the roster says could be placed right now.

    For a pinned create that is the target node's own figure, and a
    target absent from per_node is zero rather than a KeyError: the
    roster skips a node whose queue is over the unreasonable length
    and a node which has not published metrics yet, both of which are
    transient and both of which are what a caller here is waiting out.

    For an unpinned create it is the best single node, never
    total['cpu_available'] -- that field is a sum across nodes and an
    instance has to fit on one of them.
    """
    if node is not None:
        entry = per_node.get(node)
        if entry is None:
            return 0
        return node_available_cpus(entry)

    if not per_node:
        return 0
    return max(node_available_cpus(entry) for entry in per_node.values())


def wait_for_capacity(poll, cpus, node, deadline, clock=time.time,
                      sleep=time.sleep, interval=10):
    """Wait until the cluster could admit a create of this size.

    poll() returns the /admin/resources dict. It is injected rather
    than called through a client because this module imports nothing
    from the suite or from shakenfist_client, so that the unit tests
    can load it by path.

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
    """
    started = clock()
    record = {
        'cpus': cpus,
        'node': node,
        'mode': 'informed',
        'polls': 0,
        'degraded_polls': 0,
        'poll_errors': [],
        'headroom_at_start': None,
        'headroom_at_end': None,
        'per_node': None,
        'satisfied': False,
        'seconds_waited': 0.0,
    }

    def done():
        record['seconds_waited'] = clock() - started
        return record

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

            headroom = available_cpus(per_node, node)
            record['headroom_at_end'] = headroom
            if record['headroom_at_start'] is None:
                record['headroom_at_start'] = headroom

            if headroom >= cpus:
                record['satisfied'] = True
                return done()

        if clock() > deadline:
            return done()
        sleep(interval)
