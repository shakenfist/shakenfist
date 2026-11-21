# Copyright 2026 Michael Still and contributors

"""The CI steps which reach the test cluster's nodes over ssh.

Two of them do: the node lifecycle lane's cached-image upload, which wants
a hypervisor rather than the primary, and ``tools/ci-restart-cluster-nodes.sh``,
which power cycles every node after the lifecycle test has killed some.

Both used to carry the same hand-written list of five addresses, indexed
positionally against ``sf1_uuid``..``sf5_uuid``. Those addresses are the
``slim-primary`` topology, which lives in ``shakenfist/actions`` and is
reached through ``build-smoke-cluster@main``, unpinned -- so the list was
correct, nothing made it stay correct, and both failure modes were quiet: a
renumbered node failed for a reason that said nothing about topology, and an
*added* node was never chosen, narrowing the coverage the upload step exists
to provide without anything noticing (issue #4320).

The node set and each node's address now come from the cluster build's own
``nodes`` list in ``ci-environment.sh``. These tests hold that: nothing here
writes the node set down again, both callers go through the one helper, and
the helper -- run for real against a constructed environment, not read --
lists every node but the primary, whatever the nodes are called, and prints
nothing at all when any of them cannot be resolved.
"""

import os
import re
import shutil
import subprocess
import tempfile

from shakenfist.tests import base


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HELPER = 'tools/ci-cluster-nodes.sh'
RESTART = 'tools/ci-restart-cluster-nodes.sh'

# The two places which need a node's address. Named rather than discovered,
# so that a third one has to be added here deliberately.
CALLERS = (
    '.github/workflows/functional-lanes.yml',
    'tools/ci-restart-cluster-nodes.sh',
)

# An address on the under-cloud network the test nodes sit on. 10.0.0.1 and
# 10.0.0.0/24 appear in unrelated fixtures (kerbside wiring cases, a network
# the lifecycle test creates), so this matches only the node range the
# retired table used.
NODE_ADDRESS = re.compile(r'\b10\.0\.0\.(?:1[0-9]|2[0-9]|3[0-9])\b')

# A node's uuid variable named literally, which is how the retired arrays
# wrote the node set down. nodelifecycletests.sh builds the same name
# dynamically from a node it was given, which is not this.
LITERAL_UUID_VAR = re.compile(r'\bsf[0-9]+_uuid\b')


def _read(relative):
    with open(os.path.join(ROOT, relative)) as f:
        return f.read()


def _code(relative):
    """``_read`` with whole-line comments removed.

    The assertions below are about what a file *does*, not what it explains.
    Both callers and the helper describe the retired table in prose, naming
    its addresses and its variables, and that history is worth keeping.

    Whole-line comments only, deliberately. Cutting at the first '#'
    anywhere would also cut shell parameter expansions -- '${#uuids[@]}' is
    one, and this test looks for it -- and it is not needed: a trailing
    comment leaves the code in front of it on the line, which is what
    should still match.
    """
    return '\n'.join(
        line for line in _read(relative).splitlines()
        if not line.lstrip().startswith('#'))


class CiClusterNodesSeamsTestCase(base.ShakenFistTestCase):
    def test_the_helper_exists_and_is_executable(self):
        path = os.path.join(ROOT, HELPER)
        self.assertTrue(os.path.exists(path), '%s is missing' % HELPER)
        self.assertTrue(
            os.access(path, os.X_OK),
            '%s is not executable, and its callers run it with bash, so '
            'this is a warning rather than a break -- but keep it +x' % HELPER)

    def test_no_caller_writes_a_node_address_down(self):
        for caller in CALLERS:
            found = NODE_ADDRESS.findall(_code(caller))
            self.assertEqual(
                [], found,
                '%s names cluster node address(es) %s. The node set belongs '
                'to the topology in shakenfist/actions, so writing it here '
                'is what issue #4320 was about: ask %s instead.'
                % (caller, ', '.join(found), HELPER))

    def test_no_caller_writes_the_node_set_down(self):
        for caller in CALLERS:
            found = LITERAL_UUID_VAR.findall(_code(caller))
            self.assertEqual(
                [], found,
                '%s names %s literally, which fixes the number of nodes. '
                '%s enumerates whatever the cluster build exported, so a '
                'node added to the topology needs no edit here.'
                % (caller, ', '.join(sorted(set(found))), HELPER))

    def test_both_callers_go_through_the_helper(self):
        for caller in CALLERS:
            self.assertIn(
                'ci-cluster-nodes.sh', _read(caller),
                '%s does not call %s, so it has some other idea of which '
                'nodes exist' % (caller, HELPER))


# What ci-environment.sh exports for a slim-primary cluster, shaped as
# shakenfist/actions' ci-include-common-localhost.yml writes it: a "nodes"
# list, and for each node its address and its under-cloud instance uuid.
SLIM_PRIMARY = {
    'nodes': 'primary sf1 sf2 sf3',
    'primary': '10.0.0.10', 'primary_uuid': 'uuid-primary',
    'sf1': '10.0.0.20', 'sf1_uuid': 'uuid-sf1',
    'sf2': '10.0.0.21', 'sf2_uuid': 'uuid-sf2',
    'sf3': '10.0.0.22', 'sf3_uuid': 'uuid-sf3',
}


def _run(script, environment, path_prefix=None):
    """Run a script with only ``environment`` and PATH set.

    Nothing from the developer's or runner's own environment leaks in, so
    an ambient ``nodes`` cannot make a case pass.
    """
    env = dict(environment)
    env['PATH'] = os.environ['PATH']
    if path_prefix:
        env['PATH'] = '%s:%s' % (path_prefix, env['PATH'])
    return subprocess.run(
        ['bash', os.path.join(ROOT, script)], env=env,
        capture_output=True, text=True, timeout=60)


class CiClusterNodesHelperTestCase(base.ShakenFistTestCase):
    """The helper's behaviour, run rather than read."""

    def _assert_refuses(self, environment, reason):
        result = _run(HELPER, environment)
        self.assertNotEqual(0, result.returncode, result.stdout)
        self.assertIn(reason, result.stderr)
        # The property both callers rely on: a refusal prints nothing a
        # caller could mistake for a complete, or a partial, list.
        self.assertEqual('', result.stdout)

    def test_lists_every_node_but_the_primary_with_its_own_uuid(self):
        result = _run(HELPER, SLIM_PRIMARY)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            ['sf1 uuid-sf1 10.0.0.20',
             'sf2 uuid-sf2 10.0.0.21',
             'sf3 uuid-sf3 10.0.0.22'],
            result.stdout.splitlines())

    def test_a_node_named_outside_the_sf_convention_is_listed(self):
        # An enumeration keyed on a naming pattern would omit this node
        # quietly, which is #4320's narrowing again under another key.
        env = dict(SLIM_PRIMARY)
        env['nodes'] += ' hv_extra'
        env['hv_extra'] = '10.0.0.40'
        env['hv_extra_uuid'] = 'uuid-hv-extra'
        result = _run(HELPER, env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('hv_extra uuid-hv-extra 10.0.0.40',
                      result.stdout.splitlines())

    def test_a_variable_for_a_node_the_list_does_not_name_is_not_listed(self):
        # A variable for a node the list does not name is not a node.
        env = dict(SLIM_PRIMARY)
        env['sf9'] = '10.0.0.99'
        env['sf9_uuid'] = 'uuid-sf9'
        result = _run(HELPER, env)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn('sf9', result.stdout)

    def test_refuses_without_a_nodes_list(self):
        env = dict(SLIM_PRIMARY)
        del env['nodes']
        self._assert_refuses(env, '${nodes} is unset or empty')

    def test_refuses_an_empty_nodes_list(self):
        env = dict(SLIM_PRIMARY)
        env['nodes'] = ''
        self._assert_refuses(env, '${nodes} is unset or empty')

    def test_refuses_mid_list_when_a_uuid_is_missing(self):
        # sf1 resolves before sf2 fails. Streaming output would have handed
        # a caller sf1 and then an error it could not see.
        env = dict(SLIM_PRIMARY)
        del env['sf2_uuid']
        self._assert_refuses(env, '${sf2_uuid}')

    def test_refuses_mid_list_when_an_address_is_missing(self):
        env = dict(SLIM_PRIMARY)
        env['sf2'] = ''
        self._assert_refuses(env, '${sf2} (its address)')

    def test_refuses_a_node_name_which_is_not_a_variable_name(self):
        env = dict(SLIM_PRIMARY)
        env['nodes'] = 'primary sf-1'
        self._assert_refuses(env, "names 'sf-1'")

    def test_refuses_when_no_node_is_the_primary(self):
        # Without one it cannot tell which node to leave out, and a guess
        # would hand the upload step the primary.
        env = dict(SLIM_PRIMARY)
        env['nodes'] = 'sf1 sf2 sf3'
        self._assert_refuses(env, 'no node called primary')

    def test_refuses_when_only_the_primary_exists(self):
        self._assert_refuses(
            {'nodes': 'primary', 'primary': '10.0.0.10',
             'primary_uuid': 'uuid-primary'},
            'no node other than the primary')


class CiRestartClusterNodesTestCase(base.ShakenFistTestCase):
    """The restart script against stub sf-client and ssh commands."""

    def setUp(self):
        super().setUp()
        self.stubs = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.stubs)
        self.calls = os.path.join(self.stubs, 'calls')
        for command in ('sf-client', 'ssh', 'sleep'):
            stub = os.path.join(self.stubs, command)
            with open(stub, 'w') as f:
                f.write('#!/bin/bash\necho "%s $*" >> %s\nexit 0\n'
                        % (command, self.calls))
            os.chmod(stub, 0o755)

    def _calls(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as f:
            return f.read().splitlines()

    def test_power_cycles_each_node_but_the_primary(self):
        result = _run(RESTART, SLIM_PRIMARY, path_prefix=self.stubs)
        self.assertEqual(0, result.returncode, result.stderr)
        reboots = [c for c in self._calls() if c.startswith('sf-client')]
        self.assertEqual(
            ['sf-client instance reboot --hard uuid-sf1',
             'sf-client instance reboot --hard uuid-sf2',
             'sf-client instance reboot --hard uuid-sf3'],
            reboots)
        # And waits on each node's own address, so the pairing held.
        sshes = ' '.join(c for c in self._calls() if c.startswith('ssh'))
        for address in ('10.0.0.20', '10.0.0.21', '10.0.0.22'):
            self.assertIn('@%s ' % address, sshes)
        self.assertNotIn('10.0.0.10', sshes)

    def test_a_list_which_failed_part_way_restarts_nothing(self):
        # The helper resolves sf1 and then fails on sf2. The script must
        # not power cycle sf1 and report success.
        env = dict(SLIM_PRIMARY)
        del env['sf2_uuid']
        result = _run(RESTART, env, path_prefix=self.stubs)
        self.assertNotEqual(0, result.returncode)
        self.assertIn('could not list the cluster', result.stderr)
        self.assertEqual([], self._calls())
