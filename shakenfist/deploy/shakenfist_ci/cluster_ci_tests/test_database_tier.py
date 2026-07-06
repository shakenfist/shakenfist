# Copyright 2019 Michael Still and contributors
import json

import requests
from testtools import content

from shakenfist_ci import base


METRICS_PORT = 13006
METRICS_TIMEOUT = 5
CALL_COUNT = 100
PER_INSTANCE_FLOOR = 0.05


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
