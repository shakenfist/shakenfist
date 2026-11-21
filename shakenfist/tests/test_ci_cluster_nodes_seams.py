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

The node set and each node's address now come from the cluster. These tests
hold that: nothing here writes the node set down again, and both callers go
through the one helper that asks.
"""

import os
import re

from shakenfist.tests import base


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HELPER = 'tools/ci-cluster-nodes.sh'

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

    def test_the_helper_enumerates_rather_than_counting(self):
        helper = _code(HELPER)
        self.assertIn(
            'compgen -v', helper,
            '%s no longer enumerates the environment. A loop to a fixed '
            'count is the assumption it exists to remove.' % HELPER)
        self.assertEqual(
            [], LITERAL_UUID_VAR.findall(helper),
            '%s names a node uuid variable literally' % HELPER)

    def test_the_helper_refuses_rather_than_printing_a_short_list(self):
        # The property both callers rely on: a caller which silently got
        # fewer nodes than the cluster has is the failure being replaced,
        # so every unhappy path has to exit non-zero.
        helper = _code(HELPER)
        self.assertIn('exit 1', helper)
        self.assertNotIn(
            'exit 0', helper,
            '%s has an explicit success exit, which risks a path that '
            'reports success having printed nothing' % HELPER)

    def test_the_restart_script_checks_the_helper_refused(self):
        # It does not run under "set -e", and a process substitution cannot
        # fail the read loop it feeds, so the refusal has to be checked by
        # hand or the script power cycles nothing and reports success.
        self.assertIn(
            '${#uuids[@]}', _code('tools/ci-restart-cluster-nodes.sh'),
            'tools/ci-restart-cluster-nodes.sh no longer checks that '
            '%s listed any nodes. Without that check the read loop it '
            'feeds cannot fail, so the script power cycles nothing and '
            'exits zero.' % HELPER)
