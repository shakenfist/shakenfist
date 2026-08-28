# Copyright 2019 Michael Still and contributors
"""Database tier assertions shared by the smoke and cluster suites.

The tests here need only a single sf-database instance, so they are
portable across every CI topology. They live in this module rather than
in one suite directory because stestr discovers tests per directory and
the two suites are disjoint: a test defined in ``cluster_ci_tests/``
runs for the first time in the merge queue, which is how #3694 landed
two broken things at once and blocked the queue for four days.
Subclassing ``DatabaseTierTestsMixin`` from both suites means these run
in PR CI, where a break is cheap to find, without giving up the
multi-node coverage the merge-queue jobs provide.

``test_grpc_lb_fans_out_across_sf_database_instances`` is deliberately
not here: it requires N>=2 sf-database instances, which only the
slim-tier topology has, so it stays in the cluster suite.
"""

import json
import time

import requests
from testtools import content

from shakenfist_ci import base
from shakenfist_ci import load_budget as lb


METRICS_PORT = 13006
METRICS_TIMEOUT = 5
CALL_COUNT = 100

# An instance GET builds an external view, which reads nine attributes
# from the single instance_attributes row. Those reads used to be nine
# separate GetInstanceAttributes RPCs and are now one. The ceiling is
# generous because the API handler may make its own reads outside the
# view, and because ambient traffic on a shared cluster is only
# approximately subtracted.
INSTANCE_GET_COUNT = 50
ATTRIBUTE_FETCH_CEILING_PER_GET = 4
AMBIENT_SAMPLE_SECONDS = 10

# Rendering a blob's "instances" field walks every healthy instance
# (one FindInstances, then a block_devices read and a dependency chain
# per disk). Handlers which render many blobs used to do that walk once
# per blob, so an artifact listing cost one walk per artifact (issue
# 3876). The walk is now hoisted to one per request, whatever the
# response contains.
#
# FindInstances is the right counter for this: it is issued exactly
# once per walk, so the measurement does not vary with how many
# instances the cluster happens to be running -- unlike the reference
# reads the walk performs, which do. The seeded artifacts make the old
# behaviour cost at least ARTIFACT_SEED_COUNT walks per listing, so a
# ceiling of two discriminates it with a whole call of headroom.
ARTIFACT_SEED_COUNT = 3
ARTIFACT_LIST_COUNT = 20
INSTANCE_WALK_CEILING_PER_GET = 2

# A metrics scrape is a plain HTTP GET against a daemon's port, so a
# single failure is not evidence about the property under test.
SCRAPE_ATTEMPTS = 3
SCRAPE_RETRY_SECONDS = 2


def scrape_database_counters(mesh_ip):
    url = 'http://%s:%d/metrics' % (mesh_ip, METRICS_PORT)
    resp = requests.get(url, timeout=METRICS_TIMEOUT)
    resp.raise_for_status()

    counters = {}
    for line in resp.text.splitlines():
        if not line or line.startswith('#'):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        if not name.startswith('database_') or not name.endswith('_total'):
            continue
        try:
            # parts[1], not parts[-1]: a sample may carry a trailing
            # millisecond timestamp, which the last field would return as
            # the counter. Unlabelled samples only, so splitting the line
            # on whitespace is safe here in a way it is not above.
            counters[name] = float(parts[1])
        except ValueError:
            continue
    return counters


def scrape_operation_requests(mesh_ip, operation, caller_daemon):
    """Sum database_requests_total for a caller, optionally one operation.

    ``operation`` may be None to sum every operation that caller has
    made. The samples this reads are labelled, so their names do not end
    in _total and scrape_database_counters() skips them.

    Shares lb.parse_request_samples() with scrape_request_pairs() rather
    than testing label substrings against the raw line. That was a third
    copy of this parser, and the only one still reading the last
    whitespace field as the value: a sample carrying a trailing timestamp
    counted the timestamp, and a label value containing a space truncated
    the line before the labels being matched on. Matching whole labels
    also means a crafted label value cannot forge one.
    """
    url = 'http://%s:%d/metrics' % (mesh_ip, METRICS_PORT)
    resp = requests.get(url, timeout=METRICS_TIMEOUT)
    resp.raise_for_status()

    total = 0.0
    for labels, value in lb.parse_request_samples(resp.text):
        if labels.get('caller_daemon') != caller_daemon:
            continue
        if operation and labels.get('operation') != operation:
            continue
        total += value
    return total


class DatabaseTierTestsMixin:
    """Tests which need one or more sf-database instances.

    Mix into a ``base.BaseNamespacedTestCase`` subclass in a suite
    directory; this class defines no ``__init__`` so the concrete class
    sets its own namespace prefix.
    """

    def _database_nodes(self):
        """Every node in the database tier, or fail.

        This deliberately fails rather than skipping. Every topology
        SF supports has a database tier -- site.yml asserts
        ``database_tier_hosts | length > 0`` and refuses to deploy
        without one -- so an empty list here is a broken cluster or a
        node role which stopped being reported, not a configuration
        this test does not apply to. Skipping would turn both of the
        assertions below into silent no-ops, which is the same vacuous
        pass the floor assertion in
        test_instance_get_fetches_the_attributes_row_once exists to
        prevent.
        """
        nodes = self.system_client.get_nodes()
        database_nodes = [n for n in nodes if n.get('is_database_node')]
        if not database_nodes:
            self.fail(
                'No node reports is_database_node, but every supported '
                'topology deploys a database tier. Either the cluster is '
                'broken or the node role is no longer reported -- both of '
                'which would make the assertions in this class pass '
                'vacuously. Nodes seen: %s'
                % json.dumps([n.get('name') for n in nodes], sort_keys=True))
        return database_nodes

    def _all_pairs(self, database_nodes):
        """Every (operation, caller_daemon) counter, summed across the tier.

        Retried the same way and for the same reason as _sum_requests():
        a refused scrape says nothing about the property under test, and
        these tests gate every PR.

        Returns the totals and the mean of the moments they were read at.
        The caller turns two of these into rates, and the interval it
        should divide by is the one between the reads, not the sleep it
        put between them: a sweep of this tier costs up to
        SCRAPE_ATTEMPTS x (METRICS_TIMEOUT + SCRAPE_RETRY_SECONDS) per
        node when nodes are slow, which against a 60s window is enough
        to push a budgeted pair past its ceiling and fail the build for
        a regression which is not there. Differencing the means is
        exact when every node is read at the same rate and close enough
        otherwise, and it is what makes a slow sweep cancel rather than
        accumulate.
        """
        totals = {}
        read_at = []
        for node in database_nodes:
            last = None
            for attempt in range(SCRAPE_ATTEMPTS):
                try:
                    for key, value in lb.scrape_request_pairs(node['ip']).items():
                        totals[key] = totals.get(key, 0.0) + value
                    read_at.append(time.monotonic())
                    break
                except Exception as e:
                    last = e
                    if attempt < SCRAPE_ATTEMPTS - 1:
                        time.sleep(SCRAPE_RETRY_SECONDS)
            else:
                self.fail(
                    'Failed to scrape database metrics from %s (%s) after '
                    '%d attempts: %s'
                    % (node['name'], node['ip'], SCRAPE_ATTEMPTS, last))
        return totals, sum(read_at) / len(read_at)

    def _sum_requests(self, database_nodes, operation, caller_daemon):
        """Sum one counter across the tier, retrying a failed scrape.

        The cluster LB test wraps its scrapes and calls self.fail() on
        error rather than letting a raw requests exception out, and
        these need the same treatment for the same reason: a scrape is
        a plain HTTP GET against a daemon's metrics port, and a single
        refused or timed-out connection says nothing about the property
        under test. These tests gate every PR, so a transient scrape
        failure would be a flake rather than a finding -- retry briefly
        first, and only then fail with something that names the node.
        """
        total = 0.0
        for node in database_nodes:
            last = None
            for attempt in range(SCRAPE_ATTEMPTS):
                try:
                    total += scrape_operation_requests(
                        node['ip'], operation, caller_daemon)
                    break
                except Exception as e:
                    last = e
                    if attempt < SCRAPE_ATTEMPTS - 1:
                        time.sleep(SCRAPE_RETRY_SECONDS)
            else:
                self.fail(
                    'Failed to scrape database metrics from %s (%s) after '
                    '%d attempts: %s'
                    % (node['name'], node['ip'], SCRAPE_ATTEMPTS, last))
        return total

    def test_api_database_traffic_reaches_the_database_tier(self):
        # sf-database is the only process with direct MariaDB access; every
        # other daemon reaches MariaDB through its gRPC tier. That is easy to
        # break silently, because MARIADB_HOST is rendered into /etc/sf/config
        # -- the shared systemd EnvironmentFile for every daemon on the node
        # -- so on a database-tier node every daemon sees the direct-access
        # config and could take it. A daemon which does is invisible to the
        # tier's metrics, connection accounting and caching, and no unit test
        # can see it because the routing decision depends on the deployed
        # config. This asserts the API's reads actually arrive at the tier.
        #
        # The single-node smoke topology is the sharpest version of this:
        # there the API and sf-database are the same machine, so the
        # direct-access config is right there for the taking.
        database_nodes = self._database_nodes()

        def _api_requests():
            return self._sum_requests(database_nodes, None, 'api')

        before = _api_requests()
        for _ in range(CALL_COUNT):
            self.system_client.get_namespaces()
        delta = _api_requests() - before

        self.addDetail(
            'api_request_delta', content.text_content(str(delta)))
        self.assertGreater(
            delta, 0,
            'Expected %d namespace GETs to produce sf-database RPCs '
            'attributed to the api daemon, but the counter did not move. '
            'The API is reaching MariaDB without going through the database '
            'tier. delta=%s' % (CALL_COUNT, delta))

    def test_instance_get_fetches_the_attributes_row_once(self):
        # Every MariaDB backed instance attribute lives in one
        # instance_attributes row, and building an external view reads nine
        # of them. Fetching the row per read made a single instance GET cost
        # nine GetInstanceAttributes RPCs, which on a cluster with standing
        # instances was one of the largest per-instance database load lines.
        database_nodes = self._database_nodes()

        # No addCleanup() to delete this instance: BaseNamespacedTestCase's
        # tearDown() already deletes every instance in the namespace and
        # blocks until they are gone, and testtools runs tearDown() before
        # cleanups. A cleanup which deleted it here could therefore only
        # ever 404, and unlike tearDown's own deletes it would not be
        # wrapped in the ResourceNotFoundException guard -- which is
        # exactly how this test failed every merge group from the day it
        # landed.
        inst = self.test_client.create_instance(
            'dbtier-attributes', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self._await_instance_create(inst['uuid'])

        def _fetches():
            return self._sum_requests(
                database_nodes, 'GetInstanceAttributes', 'api')

        # Other API traffic on the cluster reads attribute rows too, so
        # measure that rate and subtract it from the measurement below.
        ambient_before = _fetches()
        time.sleep(AMBIENT_SAMPLE_SECONDS)
        ambient_rate = (_fetches() - ambient_before) / AMBIENT_SAMPLE_SECONDS

        before = _fetches()
        start = time.time()
        for _ in range(INSTANCE_GET_COUNT):
            self.test_client.get_instance(inst['uuid'])
        elapsed = time.time() - start
        raw_delta = _fetches() - before
        delta = raw_delta - (ambient_rate * elapsed)

        measurement = {
            'ambient_rate_per_second': ambient_rate,
            'elapsed_seconds': elapsed,
            'gets': INSTANCE_GET_COUNT,
            'attribute_fetches': delta,
            'attribute_fetches_raw': raw_delta,
        }
        self.addDetail(
            'measurement',
            content.text_content(json.dumps(
                measurement, indent=2, sort_keys=True)))

        # The floor is asserted on the raw counter delta, not the
        # ambient-corrected one. What it exists to catch is the counter
        # not moving at all -- #3708 measured exactly 0.0 over 50 GETs,
        # because the API was reaching MariaDB directly and never
        # touching the tier's interceptor. A raw delta is monotonic and
        # cannot be pushed below zero by an ambient estimate sampled
        # over a different window than the one it is extrapolated
        # across, so this cannot fail for a cluster which is merely
        # busy. Correcting it before comparing against zero would
        # reintroduce that as a flake, which matters more now these run
        # in the smoke suite and gate every PR.
        self.assertGreater(
            raw_delta, 0,
            'Expected instance GETs to fetch the attributes row at all; '
            'measurement=%s' % measurement)

        # The ceiling keeps the correction, because ambient reads inflate
        # the delta and so push this assertion towards a spurious
        # failure; subtracting the measured background rate is what
        # protects it. The 4x headroom over the expected single fetch
        # then absorbs whatever the estimate did not.
        per_get = delta / INSTANCE_GET_COUNT
        self.assertLess(
            per_get, ATTRIBUTE_FETCH_CEILING_PER_GET,
            'Each instance GET cost %.2f GetInstanceAttributes RPCs, which is '
            'above the ceiling of %d. The external view is fetching the '
            'attributes row more than once per call. measurement=%s'
            % (per_get, ATTRIBUTE_FETCH_CEILING_PER_GET, measurement))

    def test_artifact_listing_walks_instances_once(self):
        # Every blob rendered with an "instances" field needs to know
        # which instances use it, which means walking every healthy
        # instance and following each disk's dependency chain. The
        # artifact handlers used to run that walk once per blob they
        # rendered, so a listing cost one walk per artifact and a
        # version listing one per version (issue 3876). It is now run
        # once per request.
        #
        # This asserts on FindInstances rather than on the reference
        # reads the walk performs, because FindInstances is issued
        # exactly once per walk: the measurement is therefore
        # independent of how many instances the cluster is running,
        # which the reference reads are not.
        database_nodes = self._database_nodes()

        # Seed enough artifacts that the old per-blob behaviour is
        # unambiguously above the ceiling. Uploading a few bytes is
        # much cheaper than fetching images, and an upload artifact
        # renders through exactly the same path.
        for i in range(ARTIFACT_SEED_COUNT):
            upload = self.test_client.create_upload()
            self.test_client.send_upload(
                upload['uuid'], ('dbtier-%d' % i).encode('ascii'))
            self.test_client.upload_artifact(
                'dbtier-artifact-%d' % i, upload['uuid'])

        # A listing which does not actually contain the seeded
        # artifacts would pass the ceiling vacuously.
        artifacts = self.test_client.get_artifacts()
        self.assertGreaterEqual(
            len(artifacts), ARTIFACT_SEED_COUNT,
            'Expected the listing to contain at least the %d seeded '
            'artifacts, but it returned %d. The ceiling below cannot '
            'discriminate the per-blob behaviour without them.'
            % (ARTIFACT_SEED_COUNT, len(artifacts)))

        def _walks():
            return self._sum_requests(
                database_nodes, 'FindInstances', 'api')

        # Other API traffic on the cluster lists instances too, so
        # measure that rate and subtract it from the measurement below.
        ambient_before = _walks()
        time.sleep(AMBIENT_SAMPLE_SECONDS)
        ambient_rate = (_walks() - ambient_before) / AMBIENT_SAMPLE_SECONDS

        before = _walks()
        start = time.time()
        for _ in range(ARTIFACT_LIST_COUNT):
            self.test_client.get_artifacts()
        elapsed = time.time() - start
        raw_delta = _walks() - before
        delta = raw_delta - (ambient_rate * elapsed)

        measurement = {
            'ambient_rate_per_second': ambient_rate,
            'artifacts_in_listing': len(artifacts),
            'elapsed_seconds': elapsed,
            'gets': ARTIFACT_LIST_COUNT,
            'instance_walks': delta,
            'instance_walks_raw': raw_delta,
        }
        self.addDetail(
            'measurement',
            content.text_content(json.dumps(
                measurement, indent=2, sort_keys=True)))

        # As in the test above, the floor is asserted on the raw delta
        # so a busy cluster's ambient estimate cannot push it below
        # zero, while the ceiling keeps the ambient correction because
        # background reads inflate it.
        self.assertGreater(
            raw_delta, 0,
            'Expected an artifact listing to walk instances at all; '
            'measurement=%s' % measurement)

        per_get = delta / ARTIFACT_LIST_COUNT
        self.assertLess(
            per_get, INSTANCE_WALK_CEILING_PER_GET,
            'Each artifact listing walked instances %.2f times, which is '
            'above the ceiling of %d. The handler is walking instances '
            'per blob rendered rather than once per request. '
            'measurement=%s'
            % (per_get, INSTANCE_WALK_CEILING_PER_GET, measurement))

    def test_no_unbudgeted_fixed_rate_database_polling(self):
        # Nothing in CI would have caught the load regression phase 6 spent
        # a fortnight on. It ran for eleven days before anybody looked,
        # because the only detector was a nightly job watching one
        # production cluster. This is the review-time version.
        #
        # It asserts shape, not level. A CI cluster is small, short lived,
        # shares hardware, and -- because stestr runs this suite in
        # parallel -- is never idle while this runs. So rather than ask
        # "how much load is there", which no assertion here could make
        # stick, it asks "is any of this load metronomic".
        #
        # Metronomic takes two measurements, because the first one alone
        # was not enough. A pair must run at the same rate in every window,
        # which the test in the next worker creating an instance does not
        # -- but a blob heavy test running flat out for the whole
        # measurement does, and that is what made the check report a
        # different handful of imaginary polling loops in each of three
        # merge queue jobs. So a pair must also have held its rate while
        # the rest of the suite got busier around it, which is what a rate
        # set by configuration does and what work driven by the suite
        # cannot.
        database_nodes = self._database_nodes()
        nodes = self.system_client.get_nodes()

        windows = []
        elapsed_seconds = []
        restarted = []
        counters, read_at = self._all_pairs(database_nodes)
        for _ in range(lb.LOAD_WINDOW_COUNT):
            time.sleep(lb.LOAD_WINDOW_SECONDS)
            later, later_read_at = self._all_pairs(database_nodes)

            # Divide by what was actually measured, not by what was
            # slept. See _all_pairs().
            elapsed = later_read_at - read_at
            elapsed_seconds.append(round(elapsed, 1))

            window = {}
            for key, value in later.items():
                delta = value - counters.get(key, 0.0)
                if delta < 0:
                    # A counter went backwards, so an sf-database
                    # restarted inside this window and began again from
                    # zero. sf-ctl's reader drops these for the same
                    # reason; here they are also recorded, because
                    # without that the assertions below see a pair which
                    # simply stopped and report it as this harness
                    # having lost sight of the cluster.
                    restarted.append({'operation': key[0],
                                      'caller_daemon': key[1],
                                      'delta': delta})
                    continue
                window[key] = delta / elapsed
            windows.append(window)
            counters, read_at = later, later_read_at

        # Said once, up front, rather than left to be inferred from
        # whichever assertion below trips over the gap it leaves.
        self.assertEqual(
            [], restarted,
            'A database_requests_total counter went backwards during the '
            'measurement, which means an sf-database restarted inside it. '
            'Every rate below is then measured across a window the tier '
            'did not survive, so this run cannot say whether the cluster '
            'is polling more than it should -- and the failure it would '
            'otherwise report is a restart, not a regression. Look at why '
            'sf-database restarted. counters=%s'
            % json.dumps(restarted, sort_keys=True))

        # The positive control, asserted first and without reference to the
        # budget, because it is the check which says whether any of the
        # rest of this measured anything at all. Every daemon polls its own
        # node_daemon_states row on a schedule set by configuration rather
        # than by workload, so its rate is predictable from the daemon
        # inventory: if a pair which must be there is missing, or runs well
        # under the rate the code dictates, then this harness cannot see
        # part of the cluster and every "nothing is over budget"
        # conclusion below is vacuous. That is not hypothetical -- until
        # #3708 this counter could not see daemons co-located with MariaDB,
        # which on our production cluster was two nodes of six, and phase 6
        # spent a fortnight chasing the difference as though it were load.
        daemon_nodes = lb.daemon_node_counts(nodes)
        self.assertNotEqual(
            {}, daemon_nodes,
            'No node reported a running daemon, so the poll rates below '
            'cannot be predicted and this test proves nothing. Nodes '
            'seen: %s' % json.dumps([n.get('name') for n in nodes]))

        # The highest rate seen in any window, for both halves of the
        # control, because each half asks a different question of it.
        #
        # Undercount asks "can this harness see the whole cluster", which
        # is a persistent condition: a daemon visible in either window is
        # a daemon the harness can see, and taking the minimum instead
        # fails the build whenever one window happens to straddle a
        # daemon restart.
        #
        # Overcount asks "has the rate limit in check_daemon_state()
        # stopped working", which is the opposite: a daemon polling at
        # twice the permitted rate in one window and normally in the
        # other has still lost its rate limit, and the minimum would let
        # that through.
        control = {}
        for daemon, node_count in sorted(daemon_nodes.items()):
            rates = [w.get(('GetNodeDaemonState', daemon), 0.0)
                     for w in windows]
            measured = max(rates)
            expected = node_count / lb.DAEMON_STATE_POLL_INTERVAL
            if daemon == 'cluster':
                # One cluster daemon cluster-wide holds the maintenance
                # lock, and its elected loop sleeps on the lock rather than
                # in idle(), so it polls once per loop instead of once per
                # interval. See #3874, which is what happened when it did
                # not poll at all.
                expected -= (1.0 / lb.DAEMON_STATE_POLL_INTERVAL
                             - 1.0 / lb.ELECTED_CLUSTER_LOOP_SECONDS)
            control[daemon] = {'nodes': node_count, 'expected_qps': expected,
                               'measured_qps': measured,
                               'measured_qps_per_window': [round(r, 3)
                                                           for r in rates]}

        self.addDetail('poll_positive_control', content.text_content(
            json.dumps({'daemons': control,
                        'measured_seconds': elapsed_seconds},
                       indent=2, sort_keys=True)))

        for daemon, seen in sorted(control.items()):
            self.assertGreater(
                seen['measured_qps'],
                seen['expected_qps'] * lb.POLL_UNDERCOUNT_TOLERANCE,
                'The %s daemon runs on %d node(s), and each one polls its '
                'own daemon state row every %.1fs, so the tier should be '
                'serving about %.2f GetNodeDaemonState/s for it. Its '
                'busiest window served %.2f. Either this harness cannot '
                'see the whole '
                'cluster -- in which case nothing else this test asserts '
                'means anything -- or those daemons have stopped polling. '
                'control=%s'
                % (daemon, seen['nodes'], lb.DAEMON_STATE_POLL_INTERVAL,
                   seen['expected_qps'], seen['measured_qps'],
                   json.dumps(control, sort_keys=True)))
            self.assertLess(
                seen['measured_qps'],
                seen['expected_qps'] * lb.POLL_OVERCOUNT_TOLERANCE + 0.5,
                'The %s daemon is polling its daemon state row faster than '
                'DAEMON_STATE_POLL_INTERVAL allows (%.2f/s against an '
                'expected %.2f/s). Either the rate limit in '
                'Daemon.check_daemon_state() has stopped working, or '
                'something else has started reading that row. control=%s'
                % (daemon, seen['measured_qps'], seen['expected_qps'],
                   json.dumps(control, sort_keys=True)))

        # Now the budget. Only the metronomic pairs are considered, and
        # each at its lowest observed rate.
        budget = lb.load_budget()
        defaults = budget['defaults']
        entries = {(e['operation'], e['caller_daemon']): e
                   for e in budget['entries']}
        node_count = len(nodes)

        # Powered on instances, not every instance. The per-instance
        # coefficients were fitted against instances_active, which counts
        # running libvirt domains, so a powered off instance contributes
        # nothing to the model and must not contribute here either --
        # counting it would raise every ceiling below by its coefficient
        # for load it does not produce. power_state is the closest thing
        # the API offers to that series; it is what the hypervisor last
        # reported for the domain.
        standing_instances = len(
            [i for i in self.system_client.get_instances()
             if i.get('power_state') == 'on'])
        # Steady is not yet metronomic. A blob heavy test running at a
        # level rate for the whole measurement is steady too, which is how
        # the first version of this check came to report twelve pairs of
        # blob and transfer traffic as new polling loops in one merge queue
        # job and a different five in the next, with no pair common to
        # both. So the steady set is narrowed to the pairs whose rate did
        # not follow the rest of the suite, which is the property a poll
        # has and a busy test does not.
        steady = lb.fixed_rate(windows)
        independent = lb.independent_of_activity(windows)
        metronomic = {key: rate for key, rate in steady.items()
                      if key in independent}
        spread = lb.activity_spread(windows)
        unbudgeted_ceiling = lb.unbudgeted_ceiling_qps(defaults, node_count)

        # The discriminator's own positive control, which is the same
        # argument as the poll control above and about a different thing:
        # that one asks whether the harness can see the cluster, this asks
        # whether this run's measurement can tell a poll from a test.
        # GetNodeDaemonState is the traffic on this cluster which is
        # definitely a poll -- every daemon reads its own row on a fixed
        # interval, at a rate the control above just finished asserting.
        # If the filter cannot recognise those, it would not recognise a
        # new poll either, and an empty unbudgeted list below would mean
        # nothing.
        recognised = sorted(
            '%s/%s' % (operation, daemon)
            for operation, daemon in set(metronomic)
            if operation == 'GetNodeDaemonState' and daemon in daemon_nodes)

        over = []
        unbudgeted = []
        harness = []
        reported = []
        for key, measured in sorted(metronomic.items()):
            entry = entries.get(key)
            if entry is None:
                if measured <= unbudgeted_ceiling:
                    continue
                # Traffic this suite polls for itself looks exactly like a
                # server side poll to everything above, because it is one
                # -- it is just on the wrong side of the API. Listed and
                # argued in lb.HARNESS_DRIVEN_PAIRS, and reported below
                # rather than dropped, because an exemption nobody can see
                # in the run detail is an exemption nobody will revisit.
                if lb.harness_driven(key):
                    harness.append((key, measured))
                else:
                    unbudgeted.append((key, measured))
                continue
            modelled = lb.expected_qps(entry, node_count, standing_instances)
            ceiling = (modelled * defaults['tolerance_multiplier']
                       + defaults['tolerance_floor_qps'])
            if measured <= ceiling:
                continue
            if lb.enforced(entry):
                over.append((key, measured, modelled, ceiling))
            else:
                reported.append((key, measured, modelled, ceiling))

        summary = {
            'nodes': node_count,
            'standing_instances': standing_instances,
            'window_seconds': lb.LOAD_WINDOW_SECONDS,
            'unbudgeted_ceiling_qps': round(unbudgeted_ceiling, 3),
            'measured_seconds': elapsed_seconds,
            'windows': lb.LOAD_WINDOW_COUNT,
            'pairs_seen': len(windows[0]),
            'pairs_fixed_rate': len(steady),
            'pairs_metronomic': len(metronomic),
            'activity_qps_per_window': [
                round(level, 3) for level in lb.activity_levels(windows)],
            'activity_spread': round(spread, 3),
            'activity_spread_required': lb.ACTIVITY_DISCRIMINATION_SPREAD,
            'known_polls_recognised': recognised,
            'over_budget': [
                {'operation': k[0], 'caller_daemon': k[1], 'measured': m,
                 'modelled': mod, 'ceiling': c} for k, m, mod, c in over],
            'unbudgeted': [
                {'operation': k[0], 'caller_daemon': k[1], 'measured': m}
                for k, m in unbudgeted],
            'harness_driven': [
                {'operation': k[0], 'caller_daemon': k[1], 'measured': m}
                for k, m in harness],
            'over_budget_but_not_enforced': [
                {'operation': k[0], 'caller_daemon': k[1], 'measured': m,
                 'modelled': mod} for k, m, mod, _ in reported],
        }
        self.addDetail('database_load', content.text_content(
            json.dumps(summary, indent=2, sort_keys=True)))

        # Both assertions below read the metronomic set, so both are
        # vacuous unless this run could tell metronomic from busy. Where it
        # could not, say so and stop: a skip is visible in the run log,
        # where a pass on a measurement that proved nothing reads as
        # coverage -- which is the failure this module's own header warns
        # about.
        if spread < lb.ACTIVITY_DISCRIMINATION_SPREAD:
            self.skipTest(
                'The tier ran at a level rate throughout the measurement '
                '(busiest window over quietest was %.2f, and %.2f is '
                'needed), so every pair here is exactly as steady in its '
                'share of the traffic as it is in its rate, and this run '
                'cannot tell a polling loop from a test which happened to '
                'run at a constant rate. summary=%s'
                % (spread, lb.ACTIVITY_DISCRIMINATION_SPREAD,
                   json.dumps(summary, sort_keys=True)))

        if not recognised:
            self.skipTest(
                'No GetNodeDaemonState pair survived the fixed-rate filter, '
                'and those are the one kind of traffic on this cluster '
                'which is certainly a poll. The filter therefore could not '
                'have recognised a new poll either, so the empty result '
                'below proves nothing. summary=%s'
                % json.dumps(summary, sort_keys=True))

        self.assertEqual(
            [], unbudgeted,
            'These (operation, caller_daemon) pairs are not in '
            'shakenfist/data/database_load_budget.yaml, and ran above '
            '%.2f/s at the same rate in every measurement window without '
            'following the rest of the suite, which is what a new '
            'fixed-rate poll looks like and what a busy test does not. If '
            'the traffic is meant to be there, add it to the budget with a '
            'note naming the loop which produces it -- or, if this suite '
            'is what produces it rather than the cluster, to '
            'HARNESS_DRIVEN_PAIRS in load_budget.py, which says what such '
            'an entry has to argue. summary=%s'
            % (unbudgeted_ceiling, json.dumps(summary, sort_keys=True)))

        self.assertEqual(
            [], over,
            'These pairs are budgeted and ran steadily well above what the '
            'model predicts for a cluster of this shape. Do not raise the '
            'budget to make this pass: either the load is a regression '
            'worth fixing, or the model has changed and that change '
            'belongs in a commit which says so. summary=%s'
            % json.dumps(summary, sort_keys=True))
