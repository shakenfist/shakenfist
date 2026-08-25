# Copyright 2019 Michael Still and contributors
"""Database tier assertions shared by the smoke and cluster suites.

The two tests here need only a single sf-database instance, so they are
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

# A blob GET builds an external view which needs the blob's outbound
# references exactly once: depends_on, transcodes and references_from
# are all derived from the same unfiltered read. The filtered property
# reads used to make that three GetReferencesFrom RPCs per GET (issue
# 3876), so the ceiling of two discriminates the old behaviour while
# leaving one whole extra call of headroom over the expected single
# fetch.
BLOB_GET_COUNT = 50
REFERENCES_FROM_CEILING_PER_GET = 2

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
            counters[name] = float(parts[-1])
        except ValueError:
            continue
    return counters


def scrape_operation_requests(mesh_ip, operation, caller_daemon):
    """Sum database_requests_total for a caller, optionally one operation.

    ``operation`` may be None to sum every operation that caller has
    made. The samples this reads are labelled, so their names do not end
    in _total and scrape_database_counters() skips them.
    """
    url = 'http://%s:%d/metrics' % (mesh_ip, METRICS_PORT)
    resp = requests.get(url, timeout=METRICS_TIMEOUT)
    resp.raise_for_status()

    wanted = ['caller_daemon="%s"' % caller_daemon]
    if operation:
        wanted.append('operation="%s"' % operation)

    total = 0.0
    for line in resp.text.splitlines():
        if not line.startswith('database_requests_total{'):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        if not all(w in parts[0] for w in wanted):
            continue
        try:
            total += float(parts[-1])
        except ValueError:
            continue
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

    def test_blob_get_reads_references_from_once(self):
        # A blob GET builds an external view whose depends_on and
        # transcodes fields are derived from the same object_references
        # rows the view already reads unfiltered for references_from.
        # Issue 3876: fetching them via the filtered depends_on and
        # transcoded properties as well made a single blob GET cost
        # three GetReferencesFrom RPCs, which was most of the
        # GetReferencesFrom/GetReferencesTo imbalance on the api daemon.
        database_nodes = self._database_nodes()

        # No addCleanup() to delete this instance, for the same reason as
        # test_instance_get_fetches_the_attributes_row_once above:
        # tearDown() already deletes and awaits every instance in the
        # namespace, and runs before cleanups.
        inst = self.test_client.create_instance(
            'dbtier-blob-references', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self._await_instance_create(inst['uuid'])

        # The create reply predates disk allocation, so refresh to learn
        # which blob backs the instance's disk.
        inst = self.test_client.get_instance(inst['uuid'])
        blob_uuid = inst['disks'][0]['blob_uuid']
        self.assertIsNotNone(blob_uuid)

        def _fetches():
            return self._sum_requests(
                database_nodes, 'GetReferencesFrom', 'api')

        # Other API traffic on the cluster reads references too, so
        # measure that rate and subtract it from the measurement below.
        ambient_before = _fetches()
        time.sleep(AMBIENT_SAMPLE_SECONDS)
        ambient_rate = (_fetches() - ambient_before) / AMBIENT_SAMPLE_SECONDS

        before = _fetches()
        start = time.time()
        for _ in range(BLOB_GET_COUNT):
            self.test_client.get_blob(blob_uuid)
        elapsed = time.time() - start
        raw_delta = _fetches() - before
        delta = raw_delta - (ambient_rate * elapsed)

        measurement = {
            'ambient_rate_per_second': ambient_rate,
            'elapsed_seconds': elapsed,
            'gets': BLOB_GET_COUNT,
            'reference_fetches': delta,
            'reference_fetches_raw': raw_delta,
        }
        self.addDetail(
            'measurement',
            content.text_content(json.dumps(
                measurement, indent=2, sort_keys=True)))

        # As above, the floor is asserted on the raw delta so a busy
        # cluster's ambient estimate cannot push it below zero, while the
        # ceiling keeps the ambient correction because background reads
        # inflate it.
        self.assertGreater(
            raw_delta, 0,
            'Expected blob GETs to read the blob\'s references at all; '
            'measurement=%s' % measurement)

        per_get = delta / BLOB_GET_COUNT
        self.assertLess(
            per_get, REFERENCES_FROM_CEILING_PER_GET,
            'Each blob GET cost %.2f GetReferencesFrom RPCs, which is above '
            'the ceiling of %d. The external view is reading the blob\'s '
            'outbound references more than once per call. measurement=%s'
            % (per_get, REFERENCES_FROM_CEILING_PER_GET, measurement))
