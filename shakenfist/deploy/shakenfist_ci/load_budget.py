# Copyright 2026 Michael Still and contributors
"""The database load budget, and the model it expresses, for the CI suite.

This is the CI side of shakenfist/data/database_load_budget.yaml. It lives
apart from database_tier.py so that it imports nothing from the test base
class, which means the unit test suite can load it and assert it agrees
with the server side implementation in
shakenfist/schema/database_load_budget.py. Two implementations of the same
arithmetic is how a check and the thing it checks stop agreeing about what
normal is, so that parity assertion is the point.

The suite is otherwise standalone -- it imports the client and nothing from
the server -- and keeping it that way matters, because it runs wherever the
harness puts it. What must not be duplicated is the data.
"""

import os

import requests


METRICS_PORT = 13006
METRICS_TIMEOUT = 5

# The load check watches two consecutive windows rather than one long one,
# because the cluster it runs on is not idle: stestr runs the suite in
# parallel, so other tests are creating and deleting things throughout. A
# single window cannot tell a new polling loop from the test in the next
# worker, and a check which cannot tell them apart is a flaky check, which
# gets disabled, which is worse than no check at all because a disabled
# check still reads as coverage.
#
# Two windows can tell them apart, because the thing being looked for has a
# property test churn does not: a fixed-rate poll runs at the same rate in
# both windows. That is the idea worth having here -- not "how much load is
# there" but "is any of this load metronomic".
LOAD_WINDOW_SECONDS = 60
LOAD_WINDOW_COUNT = 2

# How alike two windows must be before a pair counts as fixed-rate. A poll
# is far steadier than this; the slack is for windows which straddle the
# period of a slow loop.
FIXED_RATE_MIN_RATIO = 0.6
FIXED_RATE_MAX_RATIO = 1.7

# Every daemon polls its own node_daemon_states row from Daemon.idle(),
# rate-limited to DAEMON_STATE_POLL_INTERVAL. This is that constant, and it
# is duplicated rather than imported because this suite is standalone and
# does not import the server package.
DAEMON_STATE_POLL_INTERVAL = 2.0

# The elected cluster daemon is the one exception. It sleeps on
# lock.lost_event.wait(ELECTED_CLUSTER_LOOP_SECONDS) rather than in idle(),
# so it polls once per loop instead of once per interval. Before #3874 it
# did not poll at all; if these two constants ever disagree with
# shakenfist/daemons/cluster/main.py the positive control below is wrong,
# which is why test_database_load_budget.py asserts the shipped budget
# against the real constants.
ELECTED_CLUSTER_LOOP_SECONDS = 5.0

# Daemons which do not reach the tier over gRPC never appear in this
# counter, however healthy they are. sf-database has direct MariaDB access
# and would otherwise be calling itself; the sentinels are one-shot and do
# not idle.
NON_POLLING_DAEMONS = ['database', 'sentinel-first', 'sentinel-last']

DAEMON_STATE_RUNNING = 'daemon-running'

# The positive control is one-sided. A pair reading below expectation means
# the harness cannot see part of the cluster, which is the vacuous pass this
# exists to prevent; a pair reading above it means a daemon is busier than
# idle, which on shared CI hardware is ordinary.
POLL_UNDERCOUNT_TOLERANCE = 0.75
POLL_OVERCOUNT_TOLERANCE = 1.60


def load_budget():
    """The shipped database load budget, as plain dicts.

    Read from the checkout this suite is running out of, falling back to
    the installed server package for the case where it is not running from
    one. Never a copy: a second copy of the numbers is the failure this
    module exists to avoid.
    """
    import yaml

    here = os.path.dirname(os.path.abspath(__file__))
    checkout = os.path.join(
        os.path.dirname(os.path.dirname(here)), 'data',
        'database_load_budget.yaml')
    if os.path.exists(checkout):
        with open(checkout, encoding='utf-8') as f:
            return yaml.safe_load(f)

    from shakenfist.schema import database_load_budget
    return yaml.safe_load(database_load_budget.budget_text())


def expected_qps(entry, nodes, standing_instances):
    """The budget's model, evaluated for a cluster of this shape.

    Kept in step with BudgetEntry.expected_qps() in
    shakenfist/schema/database_load_budget.py, and asserted to be so by
    shakenfist/tests/test_database_tier_harness.py. Clamped at zero because
    GetNodeDaemonState/cluster carries a negative cluster term.
    """
    qps = 0.0
    qps += entry.get('per_node_base_qps', 0.0) * nodes
    qps += entry.get('cluster_base_qps', 0.0)
    qps += entry.get('per_instance_qps', 0.0) * standing_instances
    return max(0.0, qps)


def enforced(entry):
    """Whether exceeding this entry should fail the build.

    A provisional entry records a known defect and an activity coupled one
    records somebody else's workload. Both are worth printing and neither
    is worth failing on.
    """
    return 'provisional' not in entry and not entry.get('activity_coupled')


def scrape_request_pairs(mesh_ip):
    """Every (operation, caller_daemon) counter on one tier node.

    scrape_operation_requests() answers "how much of this one thing", which
    is what a before-and-after assertion about a known call site needs. This
    answers "what is this node serving at all", which is what a check for
    traffic nobody budgeted for needs -- it cannot ask about a pair whose
    name it does not know yet.
    """
    url = 'http://%s:%d/metrics' % (mesh_ip, METRICS_PORT)
    resp = requests.get(url, timeout=METRICS_TIMEOUT)
    resp.raise_for_status()

    pairs = {}
    for line in resp.text.splitlines():
        if not line.startswith('database_requests_total{'):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        labels = parts[0][len('database_requests_total{'):].rstrip('}')
        operation = caller = None
        for label in labels.split(','):
            name, _, value = label.partition('=')
            value = value.strip('"')
            if name.strip() == 'operation':
                operation = value
            elif name.strip() == 'caller_daemon':
                caller = value
        if operation is None or caller is None:
            continue
        try:
            pairs[(operation, caller)] = (
                pairs.get((operation, caller), 0.0) + float(parts[-1]))
        except ValueError:
            continue
    return pairs


def fixed_rate(rates_per_window):
    """The pairs whose rate is the same in every window, and that rate.

    A polling loop runs at a rate set by configuration, so it looks the
    same in every window it is measured over. Work driven by what the tests
    are doing does not. Given one rate dict per window, this returns the
    pairs which look metronomic, keyed to their lowest observed rate --
    lowest rather than mean, because everything this feeds is an assertion
    that a rate is too high, and the conservative choice there is the one
    least likely to fail a build over someone else's test.

    A pair absent from any window is not fixed-rate: it cannot be polling
    if it stopped.
    """
    if not rates_per_window:
        return {}

    keys = set(rates_per_window[0])
    for window in rates_per_window[1:]:
        keys &= set(window)

    steady = {}
    for key in keys:
        observed = [window[key] for window in rates_per_window]
        low, high = min(observed), max(observed)
        if low <= 0.0:
            continue
        if high / low > FIXED_RATE_MAX_RATIO / FIXED_RATE_MIN_RATIO:
            continue
        steady[key] = low
    return steady
