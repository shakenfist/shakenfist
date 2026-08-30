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
import re

import requests
import yaml


METRICS_PORT = 13006
METRICS_TIMEOUT = 5

# The load check watches several consecutive windows rather than one long
# one, because the cluster it runs on is not idle: stestr runs the suite in
# parallel, so other tests are creating and deleting things throughout. A
# single window cannot tell a new polling loop from the test in the next
# worker, and a check which cannot tell them apart is a flaky check, which
# gets disabled, which is worse than no check at all because a disabled
# check still reads as coverage.
#
# Two windows were the first attempt, and two windows are not enough. In
# the merge queue run which first exercised this check on a multi node
# cluster it reported a different set of "fixed rate" pairs in each of the
# three jobs, with no pair common to all three: twelve pairs of blob and
# transfer traffic in one, a different five in another. Nothing there was
# polling. A blob heavy test simply ran at a level rate for the two minutes
# being measured, and two minutes is well inside the length of one test --
# two of those pairs reported rates equal to fifteen decimal places because
# they are written once each per blob fetched.
#
# Four windows is four minutes, which crosses test boundaries in the
# parallel suite, and gives the spread comparisons below more than two
# points to work from. It is a compromise rather than a maximum: each
# window costs its own sixty seconds in three merge queue jobs, and the
# more windows a pair must appear in the likelier a slow loop is to miss
# one and be dropped by fixed_rate()'s "absent from any window" rule.
LOAD_WINDOW_SECONDS = 60
LOAD_WINDOW_COUNT = 4

# How alike two windows must be before a pair counts as fixed-rate,
# expressed as the largest ratio allowed between the highest and lowest
# observation. A poll is far steadier than this; the slack is for a slow
# loop whose period straddles a window boundary, which can legitimately
# land one sample in one window and two in the next. That is a factor of
# two on its own, so anything much under 2 would make this check blind to
# exactly the slow polls which are hardest to spot by reading code -- see
# test_a_slow_loop_straddling_a_window_survives.
#
# One constant, because two were only ever used as a quotient: the code
# read "high / low > MAX / MIN", so the pair 0.6 and 1.7 was really 2.83
# and halving either one moved the threshold somewhere nobody predicted.
FIXED_RATE_MAX_SPREAD = 2.83

# Steadiness on its own does not separate a poll from workload, because
# workload on this cluster is frequently steady too -- see the note on
# LOAD_WINDOW_COUNT for what that cost. What does separate them is how each
# behaves when the rest of the suite gets busier around it. A poll runs at
# a rate set by configuration, so its rate holds and its share of the
# tier's traffic falls. Work the suite drives rises and falls with
# everything else, so its share is the steadier of its two measurements.
# independent_of_activity() is that comparison and this is what it means
# by "clearly steadier".
#
# The margin is also which way the comparison errs. A pair is kept only
# when its absolute rate beats its share by this factor, so a pair the
# data cannot decide about is dropped rather than reported. That is the
# right way round here: this check exists to catch a polling loop nobody
# meant to add, and missing one until the next release costs an afternoon,
# while failing merge queue runs on somebody else's blob test costs the
# check its life.
ACTIVITY_INDEPENDENCE_MARGIN = 1.25

# The comparison means nothing unless the cluster's activity actually
# moved while it was being measured. If the suite happened to run level
# throughout, every pair's share is exactly as steady as its rate -- polls
# and workload alike, because dividing a window by a constant cannot
# change a ratio -- and the run has no way to tell them apart. It says so
# and stops rather than reporting whichever way the tie broke.
#
# Set high enough that a poll whose own rate is not perfectly flat is
# still recognised on a run which only just cleared the gate. The
# arithmetic is not the obvious one, and getting it wrong is easy: a pair
# whose rate rises by p while the traffic around it rises by a has its
# share move by a/p rather than by a, because the pair sits in the
# numerator. The condition in independent_of_activity() therefore works
# out at p squared x ACTIVITY_INDEPENDENCE_MARGIN < a. At 1.8 that allows
# p up to exactly 1.2, so a poll may wobble by a fifth between windows and
# still read as one. That is a floor rather than the real allowance: this
# gate is measured against the whole tier, while the comparison itself is
# measured against the traffic other than the pair, and a flat pair damps
# the total it is excluded from. Below 1.8, a healthy cluster measured on
# an even run reports its own polls as workload, which is the vacuous pass
# the positive control in database_tier.py exists to prevent.
#
# This is also the rate at which the check gives up: the higher it sits,
# the more runs skip for want of a busy enough suite. Four minutes of a
# parallel suite starting and finishing instance heavy tests should clear
# it comfortably, but that is a prediction rather than a measurement, so
# the summary records activity_spread on every run -- the skipped ones
# included -- and that is the number to look at before moving this.
ACTIVITY_DISCRIMINATION_SPREAD = 1.8

# A daemon which runs the Daemon base class' loop polls its own
# node_daemon_states row from Daemon.idle(), rate-limited to
# DAEMON_STATE_POLL_INTERVAL. This is that constant, and it is duplicated
# rather than imported because this suite is standalone and does not import
# the server package. Not every daemon runs that loop -- see
# NON_POLLING_DAEMONS.
DAEMON_STATE_POLL_INTERVAL = 2.0

# The elected cluster daemon is the one exception. Its maintenance loop
# sleeps on lock.lost_event.wait(ELECTED_LOOP_POLL_SECONDS) rather than in
# idle(), so it polls once per loop instead of once per interval. Before
# #3874 it did not poll at all; if these two constants ever disagree with
# shakenfist/daemons/cluster/main.py the positive control below is wrong,
# which is why test_database_tier_harness.py asserts both against the
# daemons they are copied from.
ELECTED_CLUSTER_LOOP_SECONDS = 5.0

# Daemons whose daemon state row is never read over the tier, however
# healthy they are. Predicting a poll rate for one of these makes the
# positive control fail on a perfectly healthy cluster, which is what
# happened when this list held only the first three entries. There are two
# distinct reasons to be here, and
# test_non_polling_daemons_do_not_reach_the_tier derives the whole list
# from both so that a new daemon cannot quietly land in the wrong half:
#
#   It does not reach the tier. sf-database has direct MariaDB access (it
#   is in mariadb.DIRECT_MARIADB_CALLERS) and would otherwise be calling
#   itself, so its reads never pass through the interceptor which
#   increments this counter.
#
#   It never runs Daemon.idle(), so there is no poll to count. The
#   sentinels are one-shot. sf-api is gunicorn over external_api and is
#   not a daemon module at all. sf-nodelock is a bespoke Unix socket
#   accept() loop and sf-privexec a gRPC serve loop; neither subclasses
#   Daemon. eventlog and checksums are names in Node.VALID_DAEMONS with no
#   module behind them, and so can never report themselves running.
NON_POLLING_DAEMONS = [
    'api', 'checksums', 'database', 'eventlog', 'nodelock', 'privexec',
    'sentinel-first', 'sentinel-last'
]

DAEMON_STATE_RUNNING = 'daemon-running'

# The positive control is one-sided. A pair reading below expectation means
# the harness cannot see part of the cluster, which is the vacuous pass this
# exists to prevent; a pair reading above it means a daemon is busier than
# idle, which on shared CI hardware is ordinary.
POLL_UNDERCOUNT_TOLERANCE = 0.75
POLL_OVERCOUNT_TOLERANCE = 1.60

# Traffic this suite produces itself, which nothing below can tell apart
# from a new polling loop in the server.
#
# The await helpers in shakenfist_ci/base.py -- _await_agent_state,
# _await_instance_create, _await_instance_event, _await_objects_ready --
# read an object's events endpoint on a time.sleep(5) timer for as long as
# any worker is waiting on an object, and every one of those requests
# becomes one GetObjectEvents from api. sf-api runs no loop of its own (it
# is gunicorn over external_api, which is why it is in NON_POLLING_DAEMONS
# above), so that rate is set by a constant in the harness rather than by
# what the cluster is doing: it holds steady across windows, which is
# fixed_rate(), and it does not move with the tier's activity, which is
# independent_of_activity(). That is precisely the signature the check
# calls a new poll, and in the merge queue job which found this it read
# 0.37/s against a 0.30/s ceiling -- about two workers waiting, at a fifth
# of a request each per second.
#
# Excluded here rather than written into the budget on purpose. The budget
# in shakenfist/data/database_load_budget.yaml models a deployed cluster,
# and no deployed cluster polls the events API on a timer -- the pair does
# not even clear that file's 0.10/s inclusion cut on sfcbr. Adding it
# would raise a real deployment's ceiling to cover load only CI produces,
# and every consumer of that file -- sf-ctl database-load, the generated
# Prometheus rules, the nightly report -- would inherit the fiction. The
# yaml header says not to hand-edit levels to make a check pass, and that
# is the same instruction.
#
# What it costs, said plainly because a silent exemption reads as
# coverage: this check can no longer see a regression which raises the
# number of event reads the API makes per request, since the pair is
# skipped whatever its rate. That blind spot is CI's alone.
# ShakenFistUnbudgetedDatabasePolling takes its exclusions from the budget
# file and not from here, so the same pair is still watched at the same
# ceiling on every real cluster.
#
# The CI headroom probe is the same shape of thing, one layer further out.
# tools/ci_headroom_probe.py samples GET /nodes and GET /admin/resources on
# an --interval timer for the whole of the functional test step, started by
# ci_headroom_launch.sh in shakenfist/actions rather than by this suite (see
# docs/plans/PLAN-ci-cloud-sizing-phase-01-headroom-probe.md). Reading the
# roster runs Node.external_view() per node, which is one GetNodeAttributes
# and one GetAllNodeDaemonStates each, and /admin/resources refreshes node
# metrics, which is one GetNodeMetrics each. On a cluster of N nodes at a
# 15s interval that is N/15 per second for all three, and on the six node
# merge queue cluster which found this all three read 0.3997/s against the
# same 0.30/s ceiling -- flat across windows and indifferent to what the
# suite was doing, because a poller started by the workflow and paced by a
# constant is precisely what the check is built to notice (issue 3975).
#
# The budget is the wrong home for these for the reason above and one more:
# no deployed cluster runs the headroom probe at all, so an entry would
# model load which exists nowhere outside a CI job. It would also have to be
# a per_node term, since the traffic scales with node count, and that raises
# every real cluster's ceiling in proportion to its size.
#
# What it costs, again said plainly: this check can no longer see a new
# fixed-rate poll of node state made through sf-api, whatever its rate. That
# is a wider blind spot than the events one. It is still CI's alone -- none
# of these three pairs is budgeted for the api caller, so
# ShakenFistUnbudgetedDatabasePolling goes on watching all three at the
# unbudgeted ceiling on every real cluster. The exemption is also load
# bearing only while the probe is: phase 1 instrumentation comes out once
# the sizing question it answers is closed, and
# test_the_suite_still_probes_cluster_headroom fails when it does, so this
# gets revisited rather than quietly outliving its reason.
#
# Anything added here needs the same two things: a named loop which produces
# it, and a reason the budget is the wrong home for it.
# test_harness_driven_pairs_are_not_budgeted,
# test_the_suite_still_polls_an_events_endpoint and
# test_the_suite_still_probes_cluster_headroom hold both ends of that up.
HARNESS_DRIVEN_PAIRS = frozenset([
    # shakenfist_ci/base.py's await helpers, on a time.sleep(5) timer.
    ('GetObjectEvents', 'api'),
    # tools/ci_headroom_probe.py, on its --interval timer.
    ('GetAllNodeDaemonStates', 'api'),
    ('GetNodeAttributes', 'api'),
    ('GetNodeMetrics', 'api'),
])


def load_budget():
    """The shipped database load budget, as plain dicts.

    Read from the checkout this suite is running out of, falling back to
    the installed server package for the case where it is not running from
    one. Never a copy: a second copy of the numbers is the failure this
    module exists to avoid.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    checkout = os.path.join(
        os.path.dirname(os.path.dirname(here)), 'data',
        'database_load_budget.yaml')
    if os.path.exists(checkout):
        with open(checkout, encoding='utf-8') as f:
            return yaml.safe_load(f)

    from shakenfist.schema import database_load_budget
    return yaml.safe_load(database_load_budget.budget_text())


def daemon_node_counts(nodes):
    """How many nodes run each daemon, from the daemon state rows.

    The node external view carries a ``daemon-<name>-state`` key per
    daemon in Node.VALID_DAEMONS. Counting the running ones is how the
    positive control in database_tier.py knows what the poll rate ought to
    be without assuming every daemon runs on every node -- which is true
    on the clusters we build and is not something a check should depend
    on.

    Here rather than in database_tier.py because that module needs a
    cluster to import and this needs nothing, so the key parsing and the
    NON_POLLING_DAEMONS filter can be asserted on every commit rather
    than only when a cluster gets built.
    """
    counts = {}
    for node in nodes:
        for key, value in node.items():
            if not key.startswith('daemon-') or not key.endswith('-state'):
                continue
            daemon = key[len('daemon-'):-len('-state')]
            if daemon in NON_POLLING_DAEMONS:
                continue
            if value == DAEMON_STATE_RUNNING:
                counts[daemon] = counts.get(daemon, 0) + 1
    return counts


def unbudgeted_ceiling_qps(defaults, nodes):
    """The rate at which a pair with no budget entry looks like a new poll.

    Kept in step with BudgetDefaults.unbudgeted_ceiling_qps() in
    shakenfist/schema/database_load_budget.py, and asserted to be so by
    shakenfist/tests/test_database_tier_harness.py.
    """
    return max(defaults['unbudgeted_fixed_rate_qps'],
               defaults['unbudgeted_fixed_rate_per_node_qps'] * nodes)


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


def harness_driven(key):
    """Whether a pair is traffic this test suite produces itself.

    Separate from enforced() because the two answer different questions
    about different things: that one asks whether a budgeted pair running
    high is worth failing on, this asks whether an unbudgeted pair is the
    measuring instrument rather than the thing measured. See
    HARNESS_DRIVEN_PAIRS for why the answer is not simply a budget entry.
    """
    return tuple(key) in HARNESS_DRIVEN_PAIRS


def enforced(entry):
    """Whether exceeding this entry should fail the build.

    A provisional entry records a known defect and an activity coupled one
    records somebody else's workload. Both are worth printing and neither
    is worth failing on.
    """
    return 'provisional' not in entry and not entry.get('activity_coupled')


# The deliberate copy of the sample parser in
# shakenfist/util/metrics_scrape.py, kept here because this suite imports
# nothing from the server package. Every comment justifying the shape of
# these three is on that copy; test_parser_matches_the_server_side_parser
# asserts the two agree, which is only worth anything while the fixture it
# runs on carries the awkward cases.
_SAMPLE_RE = re.compile(
    r'^database_requests_total\{(?P<labels>.*)\}\s+(?P<value>\S+)')
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
_ESCAPES = {'n': '\n', '"': '"', '\\': '\\'}


def _unescape(value):
    """Undo label value escaping, per the exposition format."""
    if '\\' not in value:
        return value

    out = []
    index = 0
    while index < len(value):
        char = value[index]
        if char != '\\' or index + 1 >= len(value):
            out.append(char)
            index += 1
            continue
        following = value[index + 1]
        if following in _ESCAPES:
            out.append(_ESCAPES[following])
            index += 2
        else:
            out.append(char)
            index += 1
    return ''.join(out)


def parse_request_samples(text):
    """Every database_requests_total sample, as (labels, value)."""
    for line in text.splitlines():
        match = _SAMPLE_RE.match(line)
        if not match:
            continue
        try:
            value = float(match.group('value'))
        except ValueError:
            continue
        yield ({name: _unescape(raw)
                for name, raw in _LABEL_RE.findall(match.group('labels'))},
               value)


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
    for labels, value in parse_request_samples(resp.text):
        operation = labels.get('operation')
        caller = labels.get('caller_daemon')
        if operation is None or caller is None:
            continue
        pairs[(operation, caller)] = pairs.get((operation, caller), 0.0) + value
    return pairs


def activity_levels(rates_per_window):
    """How busy the tier was in each window, as total requests per second.

    The sum over every pair, which stands in for what the rest of the
    suite is doing, because the pairs large enough to move it are the ones
    the tests drive. Used only as a covariate: nothing asserts a level,
    since on shared CI hardware no level assertion would stick.
    """
    return [sum(window.values()) for window in rates_per_window]


def activity_spread(rates_per_window):
    """The ratio between the busiest window and the quietest.

    One -- no variation at all -- when there is nothing to compare or the
    tier was idle, which are both cases where the caller must not go on to
    draw a conclusion. See ACTIVITY_DISCRIMINATION_SPREAD.
    """
    levels = activity_levels(rates_per_window)
    if not levels:
        return 1.0
    low, high = min(levels), max(levels)
    if low <= 0.0:
        return 1.0
    return high / low


def independent_of_activity(rates_per_window):
    """The pairs whose rate did not follow the cluster's activity level.

    A poll holds its rate while the suite gets busier around it, so its
    share of the tier's traffic moves and its rate does not. Work the suite
    drives does the opposite. Comparing how steady each of those two
    measurements is asks which of them a pair looks like, without needing
    to know what any operation means -- which matters, because the pairs
    this has to judge are by definition ones nobody has written down.

    A pair's share is measured against the traffic other than its own.
    Including it would let a large pair damp its own denominator: at a
    quarter of the tier's traffic, doubling lifts the total it is divided
    by too, and the share would hold still for a pair that plainly did
    not. That is the direction which hides a big new poll, so it is worth
    the subtraction.

    Returns a set, not a dict: the rate a caller wants is the one
    fixed_rate() already chose.
    """
    if len(rates_per_window) < 2:
        return set()

    levels = activity_levels(rates_per_window)

    keys = set(rates_per_window[0])
    for window in rates_per_window[1:]:
        keys &= set(window)

    independent = set()
    for key in keys:
        rates = [window[key] for window in rates_per_window]
        if min(rates) <= 0.0:
            continue

        others = [level - rate for level, rate in zip(levels, rates)]
        if min(others) <= 0.0:
            # This pair is the only traffic there is, so there is no
            # activity to be independent of.
            continue

        shares = [rate / other for rate, other in zip(rates, others)]
        rate_spread = max(rates) / min(rates)
        share_spread = max(shares) / min(shares)
        if rate_spread * ACTIVITY_INDEPENDENCE_MARGIN < share_spread:
            independent.add(key)
    return independent


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
        if high / low > FIXED_RATE_MAX_SPREAD:
            continue
        steady[key] = low
    return steady
