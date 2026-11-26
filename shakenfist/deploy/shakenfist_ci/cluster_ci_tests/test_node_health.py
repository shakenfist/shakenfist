# Copyright 2026 Michael Still and contributors
"""Node resource health, as far as a shared cluster can be asked about it.

Node resource health drives ``node.state`` from the health of the
storage paths the object types a node hosts declare, so a dead disk or
a hung NFS mount takes the node out of scheduling. See
docs/operator_guide/node_health.md.

This module asserts the *healthy* side of that on every node: each one
publishes ``node_resource_health`` 1, and each one carries a write
heartbeat in every path its role declares. Both are cheap, read-only
and say something a unit test cannot -- that the probe thread is
actually running in the deployed sf-resources, that it is probing the
paths this node's capabilities imply rather than some other set, and
that the authoritative write tier reaches the real filesystem. A health
thread which died at startup, or a gauge which was never registered,
publishes nothing here and fails.

The unhealthy side is not asserted here, and that is deliberate rather
than an omission (issue 4960). Forcing a real storage fault errors a
hypervisor, and this suite runs many tests concurrently against one
shared cluster: ``slim-tier``'s three nodes are all hypervisors summing
to exactly the cluster ledger floor ``test_nodes.py`` asserts, and the
two non-network hypervisors ``test_network_lifecycle.py`` pins onto are
the only two a fault could choose from, so erroring one here would fail
tests which have nothing to do with node health. The fault injection
lives in ``shakenfist/deploy/noderesourcehealthtests.py``, run from
``nodelifecycletests.sh``, which disturbs its own cluster by design and
runs nothing else while it does -- the same placement
``apivalidationrollbacktests.py`` was given for the same reason.
"""

import json
import time

import requests
from testtools import content

from shakenfist_ci import base
from shakenfist_ci import load_budget as lb


# The gauge the node-health thread registers on sf-resources' metrics
# port. The port itself, the scrape timeout and the gauge parser come
# from load_budget, which already reads unlabelled sf-resources gauges
# off that port and whose port constant is asserted against the server's
# real default by shakenfist/tests/test_database_tier_harness.py -- a
# second copy here would be a second thing to keep right.
HEALTH_GAUGE = 'node_resource_health'

# A metrics scrape is a plain HTTP GET against a daemon's port, so a
# single failure is not evidence about the property under test. Same
# judgement, and the same numbers, as database_tier.py's scrapes.
SCRAPE_ATTEMPTS = 3
SCRAPE_RETRY_SECONDS = 2

# shakenfist.resource_health.HEARTBEAT_FILENAME, and
# shakenfist.config.STORAGE_PATH, which the deployer does not move.
HEARTBEAT_FILENAME = '_heartbeat'
STORAGE_PATH = '/srv/shakenfist'

# The health_dependencies of the object types node_health.py maps a
# node's capabilities to: Blob and Upload everywhere (any node can hold
# a replica, any node can receive an upload), Instance on hypervisors
# only. Written down here so that a dependency added to an object type
# without a thought for the node it lands on fails this test by name.
EVERY_NODE_PATHS = ('blobs', 'uploads')
HYPERVISOR_PATHS = ('instances', 'image_cache', 'blobs')

# shakenfist.constants.NODE_ACTIVE_STATES. A node which is not active
# is not asserted about: a stopped or missing node is not publishing
# anything, and this module has nothing to say about why.
NODE_ACTIVE_STATES = frozenset({'initial', 'created', 'degraded'})


class TestNodeResourceHealth(base.BaseTestCase):
    def _active_nodes(self):
        nodes = [n for n in self._get_cluster_nodes()
                 if n.get('state') in NODE_ACTIVE_STATES]
        self.addDetail('nodes', content.text_content(
            json.dumps(nodes, indent=4, sort_keys=True, default=str)))
        if not nodes:
            self.fail('No node is in an active state, so there is nothing to '
                      'ask about node resource health.')
        return nodes

    def _scrape_health_gauge(self, node):
        """node_resource_health as one node publishes it.

        Fails the test on a scrape which never succeeds rather than
        returning None for it, because a resources daemon which does not
        answer on its metrics port at all is a finding, not a reason to
        report the gauge as absent.
        """
        url = 'http://%s:%d/metrics' % (node['ip'], lb.RESOURCES_METRICS_PORT)
        last = None
        for attempt in range(SCRAPE_ATTEMPTS):
            try:
                resp = requests.get(url, timeout=lb.METRICS_TIMEOUT)
                resp.raise_for_status()
                return lb.parse_gauge(resp.text, HEALTH_GAUGE)
            except requests.exceptions.RequestException as e:
                last = e
                if attempt < SCRAPE_ATTEMPTS - 1:
                    time.sleep(SCRAPE_RETRY_SECONDS)
        self.fail(
            'Could not scrape %s in %d attempts: %s. sf-resources is not '
            'answering on its metrics port.' % (url, SCRAPE_ATTEMPTS, last))

    def _expected_paths(self, node):
        paths = set(EVERY_NODE_PATHS)
        if node.get('is_hypervisor'):
            paths |= set(HYPERVISOR_PATHS)
        return sorted(paths)

    def test_every_active_node_reports_healthy_storage(self):
        """The gauge reads 1 on every node, and is published at all.

        A CI cluster's storage is local and healthy, so 1 is the only
        correct answer -- and an absent gauge is reported separately,
        because that is the node-health thread never having started
        rather than its probes failing.
        """
        unhealthy = {}
        absent = []
        for node in self._active_nodes():
            value = self._scrape_health_gauge(node)
            if value is None:
                absent.append(node['name'])
            elif value != 1.0:
                unhealthy[node['name']] = value

        self.assertEqual(
            [], absent,
            'These nodes publish no %s gauge, so sf-resources started its '
            'metrics server but never its node-health thread: %s'
            % (HEALTH_GAUGE, absent))
        self.assertEqual(
            {}, unhealthy,
            'These nodes report a failed storage health check, which also '
            'means they are no longer scheduling candidates. Read the health '
            'event on each for the failed path: %s' % unhealthy)

    def test_every_node_probes_the_paths_its_roles_declare(self):
        """A write heartbeat exists in every path this node's roles imply.

        The authoritative tier of each path check writes and fsyncs a
        ``_heartbeat`` file, so its presence is proof the probe reached
        a real filesystem -- which ``os.makedirs(exist_ok=True)`` on an
        existing directory is not. Checking the whole expected set, not
        just one path, is what catches a node probing fewer paths than
        its capabilities declare: a hypervisor which never probes
        ``instances`` would fail nothing else until the disk under it
        died.
        """
        missing = {}
        for node in self._active_nodes():
            self._require_node_exec(node)
            expected = self._expected_paths(node)
            # One exec per node rather than one per path: these run over
            # ssh and the suite pays a connection for each.
            out, _ = self._node_exec(
                node,
                ['ls', '-1d'] + ['%s/%s/%s' % (STORAGE_PATH, p,
                                               HEARTBEAT_FILENAME)
                                 for p in expected],
                sudo=True, check_exit_code=False)
            self.addDetailUniqueName(
                'heartbeats on %s' % node['name'], content.text_content(out))
            found = {line.strip() for line in out.splitlines() if line.strip()}
            absent = [p for p in expected
                      if '%s/%s/%s' % (STORAGE_PATH, p, HEARTBEAT_FILENAME)
                      not in found]
            if absent:
                missing[node['name']] = absent

        self.assertEqual(
            {}, missing,
            'These nodes carry no write heartbeat in a storage path their '
            'roles declare, so that path is not being probed and a failure '
            'of it would not take the node out of scheduling: %s' % missing)
