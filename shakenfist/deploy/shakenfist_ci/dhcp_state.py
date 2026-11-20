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

import ipaddress
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

# The interpreter Shaken Fist is installed into on a node. The system
# python3 has no shakenfist to import, which is the mistake the DHCP
# probe test below exists to catch.
SF_VENV_PYTHON = '/srv/shakenfist/venv/bin/python3'

# How long after a network becomes ready its readiness probe has
# certainly run: sf-net's maintain pass skips a network whose state
# changed in the last 60 seconds, and passes are 30 seconds apart. A
# pass may also queue behind the previous one, hence the slack.
DHCP_PROBE_SETTLE = 60 + 2 * 30 + 15

# The network events which say the DHCP readiness probe did not get a
# clean answer, either because dnsmasq did not answer or because the
# probe could not be run at all.
DHCP_PROBE_FAILURE_EVENTS = (
    'dnsmasq is running but not answering DHCP',
    'network not ok, dnsmasq not answering DHCP',
    'could not check whether dnsmasq is answering DHCP',
)


class DhcpStateTestsMixin:
    """A network's dnsmasq on the network node: it answers, and it is removed.

    The readiness tests check that a DHCP network's dnsmasq answers the
    probe sf-net uses to decide whether to rebuild it (issue 730); see
    their own comments. The rest of this docstring is about the delete.

    Deleting a network must remove its dnsmasq state from the network node.

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

    def _router(self):
        # A network's router is the first host address of its block,
        # as Network.router has it (ipam index 1).
        block = ipaddress.ip_network(self.net['netblock'])
        return str(block[1])

    def test_dnsmasq_answers_the_readiness_probe(self):
        # The probe, in the network's namespace, against the real
        # dnsmasq on the network node. Its unit tests script replies
        # into a fake socket, so they cannot say whether dnsmasq
        # answers a DHCPINFORM at all, or at which port. This can. Exit
        # 0 is an answer; 3 is no answer; anything else means the probe
        # did not run.
        router = self._router()
        out, _ = self._node_exec(
            self.network_node,
            ['sh', '-c',
             'ip netns exec %s %s -m shakenfist.util.dhcp_probe %s %s 5 '
             '2>&1; echo "exit=$?"'
             % (self.net['uuid'], SF_VENV_PYTHON, router, router)],
            sudo=True)
        self.addDetail('probe', content.text_content(out))
        self.assertEqual(
            'exit=0', out.strip().splitlines()[-1],
            'The dnsmasq for network %s on node %s did not answer the DHCP '
            'readiness probe: %r' % (self.net['uuid'],
                                     self.network_node.get('name'), out))

    def test_the_maintenance_pass_finds_dnsmasq_serving(self):
        # The probe as sf-net actually runs it, through sf-privexec,
        # with privexec's PATH and whatever interpreter the command
        # names. Running it by hand (above) cannot catch a command which
        # only works by hand: the first version ran a bare python3,
        # which on a node is an interpreter with no shakenfist, so every
        # network failed its probe on every pass and was rebuilt -- and
        # nothing in CI looked. This looks.
        deadline = time.time() + DHCP_PROBE_SETTLE
        events = []
        while True:
            events = self.test_client.get_network_events(
                self.net['uuid'], event_type='status')
            failures = [
                e for e in events
                if e.get('message') in DHCP_PROBE_FAILURE_EVENTS]
            if failures:
                self.addDetail('events', content.text_content(json.dumps(
                    events, indent=4, sort_keys=True, default=str)))
                self.fail(
                    'Network %s recorded a failed DHCP readiness probe: %s'
                    % (self.net['uuid'],
                       json.dumps(failures, sort_keys=True, default=str)))
            if time.time() > deadline:
                return
            time.sleep(10)
