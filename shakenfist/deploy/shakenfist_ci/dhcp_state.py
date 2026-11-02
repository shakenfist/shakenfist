# Copyright 2026 Michael Still and contributors
"""Network node dnsmasq state assertions, shared by both CI suites.

These need one DHCP network and host access to the network node, which
every topology has -- on the single machine topology the one node is the
network node -- so they live here rather than in a suite directory.
stestr discovers tests per directory and the two suites are disjoint, so
a test defined only in ``cluster_ci_tests/`` runs for the first time in
the merge queue. Subclassing ``DhcpStateTestsMixin`` from both suites
runs it in PR CI as well, without giving up the multi-node coverage.
"""

import json
import time

from testtools import content

from shakenfist_ci import process


# shakenfist.config.STORAGE_PATH, which the deployer does not move, and
# ObjectType.DHCP's string value.
DHCP_CONFIG_ROOT = '/srv/shakenfist/dhcp'

# How long to wait for the network node to finish tearing a deleted
# network down. The delete is asynchronous and queued behind whatever
# else the net worker is doing.
DELETE_TEARDOWN_TIMEOUT = 300


class DhcpStateTestsMixin:
    """Deleting a network must remove its dnsmasq state from the network node.

    Mix into a ``base.BaseNamespacedTestCase`` subclass in a suite
    directory; this class defines no ``__init__`` so the concrete class
    sets its own namespace prefix.

    A DHCP network's dnsmasq runs on the network node out of a
    per-network configuration directory under STORAGE_PATH. The network
    delete kills dnsmasq and then removes that directory, in that order,
    in ``ManagedExecutable.terminate()``.

    Those two steps are coupled more tightly than they look. dnsmasq is
    started through sf-privexec and so is nobody's child, and the wait
    for it to die after SIGKILL could raise a bare ``OSError`` out of
    psutil -- EINVAL from ``pidfd_open()`` against a killed but unreaped
    process (github issue #4753). That aborted ``terminate()`` before the
    directory was removed, and the network operation worker logged and
    swallowed the exception, so the rest of the network node teardown was
    skipped too.

    The race is rare, about one in 1,600 kills on a production cluster,
    so this does not try to provoke it. It asserts the postcondition the
    race broke, which nothing checked before: once the network is
    deleted, the network node holds no dnsmasq state for it.
    """

    def setUp(self):
        super().setUp()

        self.network_node = self._network_node()
        self._require_node_exec(self.network_node)
        self._require_node_sudo(self.network_node)

        # provide_dhcp must be true, it is what causes a dnsmasq to exist
        # at all.
        self.net = self.test_client.allocate_network(
            '192.168.246.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def _require_node_sudo(self, node):
        """Skip the test loudly if node cannot run a command as root.

        The configuration directory is read as root rather than relying
        on its mode, so that a deploy which tightens that mode makes this
        skip rather than report a directory it cannot see as removed.
        """
        try:
            self._node_exec(node, ['true'], sudo=True)
        except process.ProcessExecutionError as e:
            self.skipTest(
                'Cannot exec as root on node %s (%s): %s'
                % (node.get('name'), node.get('ip'), e))

    def _dhcp_config_directory(self):
        return '%s/%s' % (DHCP_CONFIG_ROOT, self.net['uuid'])

    def _dhcp_config_present(self):
        path = self._dhcp_config_directory()
        out, _ = self._node_exec(
            self.network_node,
            ['sh', '-c',
             'if [ -d %s ]; then echo present; else echo absent; fi' % path],
            sudo=True)
        answer = out.strip()
        self.assertIn(
            answer, ['present', 'absent'],
            'Could not tell whether %s exists on node %s: %r'
            % (path, self.network_node.get('name'), out))
        return answer == 'present'

    def test_delete_removes_the_dhcp_config_directory(self):
        # A ready DHCP network has its configuration on the network node.
        # Without this the assertion after the delete proves nothing: a
        # directory which was never there is also a directory which was
        # removed.
        self.assertTrue(
            self._dhcp_config_present(),
            'Ready network %s has no dnsmasq configuration at %s on network '
            'node %s' % (self.net['uuid'], self._dhcp_config_directory(),
                         self.network_node.get('name')))

        self.test_client.delete_network(self.net['uuid'])

        start_time = time.time()
        while time.time() - start_time < DELETE_TEARDOWN_TIMEOUT:
            if not self._dhcp_config_present():
                return
            time.sleep(5)

        self.fail(
            'Network %s was deleted but its dnsmasq configuration at %s is '
            'still present on network node %s after %d seconds'
            % (self.net['uuid'], self._dhcp_config_directory(),
               self.network_node.get('name'), DELETE_TEARDOWN_TIMEOUT))
