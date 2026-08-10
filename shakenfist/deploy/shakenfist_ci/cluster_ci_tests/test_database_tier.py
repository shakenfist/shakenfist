# Copyright 2019 Michael Still and contributors
import json
import time

import requests
from testtools import content

from shakenfist_ci import base


METRICS_PORT = 13006
METRICS_TIMEOUT = 5
CALL_COUNT = 100
PER_INSTANCE_FLOOR = 0.05

# An instance GET builds an external view, which reads nine attributes
# from the single instance_attributes row. Those reads used to be nine
# separate GetInstanceAttributes RPCs and are now one. The ceiling is
# generous because the API handler may make its own reads outside the
# view, and because ambient traffic on a shared cluster is only
# approximately subtracted.
INSTANCE_GET_COUNT = 50
ATTRIBUTE_FETCH_CEILING_PER_GET = 4
AMBIENT_SAMPLE_SECONDS = 10


def _scrape_database_counters(mesh_ip):
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


def _scrape_operation_requests(mesh_ip, operation, caller_daemon):
    """Sum database_requests_total for one operation and one caller.

    The samples this reads are labelled, so their names do not end in
    _total and _scrape_database_counters() skips them.
    """
    url = 'http://%s:%d/metrics' % (mesh_ip, METRICS_PORT)
    resp = requests.get(url, timeout=METRICS_TIMEOUT)
    resp.raise_for_status()

    wanted = ['operation="%s"' % operation,
              'caller_daemon="%s"' % caller_daemon]

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


class TestDatabaseTier(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'dbtier'
        super().__init__(*args, **kwargs)

    def test_grpc_lb_fans_out_across_sf_database_instances(self):
        # Discover the database tier via the is_database_node role flag.
        # (This test previously keyed on a vestigial etcd-era flag that no
        # writer has ever set, so it always found zero tier nodes and
        # silently skipped.)
        nodes = self.system_client.get_nodes()
        database_nodes = [n for n in nodes if n.get('is_database_node')]

        if len(database_nodes) < 2:
            self.skipTest(
                'test_grpc_lb_fans_out_across_sf_database_instances requires '
                'N>=2 sf-database instances; saw N=%d' % len(database_nodes))

        self.addDetail(
            'database_nodes',
            content.text_content(json.dumps(
                database_nodes, indent=2, sort_keys=True, default=str)))

        before = {}
        for node in database_nodes:
            mesh_ip = node['ip']
            try:
                before[node['name']] = _scrape_database_counters(mesh_ip)
            except Exception as e:
                self.fail(
                    'Failed to scrape metrics from %s (%s) before traffic: %s'
                    % (node['name'], mesh_ip, e))

        self.addDetail(
            'before_counters',
            content.text_content(json.dumps(before, indent=2, sort_keys=True)))

        for _ in range(CALL_COUNT):
            self.system_client.get_namespaces()

        after = {}
        for node in database_nodes:
            mesh_ip = node['ip']
            try:
                after[node['name']] = _scrape_database_counters(mesh_ip)
            except Exception as e:
                self.fail(
                    'Failed to scrape metrics from %s (%s) after traffic: %s'
                    % (node['name'], mesh_ip, e))

        self.addDetail(
            'after_counters',
            content.text_content(json.dumps(after, indent=2, sort_keys=True)))

        per_node_delta = {}
        for node in database_nodes:
            name = node['name']
            before_total = sum(before[name].values())
            after_total = sum(after[name].values())
            per_node_delta[name] = after_total - before_total

        self.addDetail(
            'per_node_delta',
            content.text_content(json.dumps(
                per_node_delta, indent=2, sort_keys=True)))

        total_delta = sum(per_node_delta.values())
        self.assertGreater(
            total_delta, 0,
            'Expected non-zero database RPC traffic during the call loop; '
            'per_node_delta=%s' % per_node_delta)

        # The 5% floor catches silent LB degeneracy (e.g. a resolver returning
        # only one address, or all-but-one subchannel marked unhealthy) without
        # flaking on healthy round-robin jitter.
        floor = PER_INSTANCE_FLOOR * total_delta
        for name, delta in per_node_delta.items():
            self.assertGreaterEqual(
                delta, floor,
                'sf-database instance %s served %.1f RPCs which is below the '
                '5%% floor of %.1f (total_delta=%.1f). Per-node deltas: %s'
                % (name, delta, floor, total_delta, per_node_delta))

    def test_instance_get_fetches_the_attributes_row_once(self):
        # Every MariaDB backed instance attribute lives in one
        # instance_attributes row, and building an external view reads nine
        # of them. Fetching the row per read made a single instance GET cost
        # nine GetInstanceAttributes RPCs, which on a cluster with standing
        # instances was one of the largest per-instance database load lines.
        nodes = self.system_client.get_nodes()
        database_nodes = [n for n in nodes if n.get('is_database_node')]
        if not database_nodes:
            self.skipTest('no sf-database instances found')

        inst = self.test_client.create_instance(
            'dbtier-attributes', 1, 1024, None,
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)
        self.addCleanup(self.test_client.delete_instance, inst['uuid'])
        self._await_instance_create(inst['uuid'])

        def _fetches():
            return sum(
                _scrape_operation_requests(
                    n['ip'], 'GetInstanceAttributes', 'api')
                for n in database_nodes)

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
        delta = _fetches() - before - (ambient_rate * elapsed)

        measurement = {
            'ambient_rate_per_second': ambient_rate,
            'elapsed_seconds': elapsed,
            'gets': INSTANCE_GET_COUNT,
            'attribute_fetches': delta,
        }
        self.addDetail(
            'measurement',
            content.text_content(json.dumps(
                measurement, indent=2, sort_keys=True)))

        self.assertGreater(
            delta, 0,
            'Expected instance GETs to fetch the attributes row at all; '
            'measurement=%s' % measurement)

        per_get = delta / INSTANCE_GET_COUNT
        self.assertLess(
            per_get, ATTRIBUTE_FETCH_CEILING_PER_GET,
            'Each instance GET cost %.2f GetInstanceAttributes RPCs, which is '
            'above the ceiling of %d. The external view is fetching the '
            'attributes row more than once per call. measurement=%s'
            % (per_get, ATTRIBUTE_FETCH_CEILING_PER_GET, measurement))
