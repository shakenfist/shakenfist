# Copyright 2026 Michael Still and contributors
#
# The in-flight and terminal cluster operation state sets are defined once,
# in schema/operations/baseclusteroperation.py, and read by mariadb's
# queries, BaseClusterOperation.is_outstanding(), the mock database and the
# functional suite. These tests pin that the two sets partition the states
# an operation can actually reach, and that every reader agrees with them.

import ast
import os
from unittest import mock

import shakenfist
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import mariadb
from shakenfist.operations.baseoperation import BaseClusterOperation
from shakenfist.schema.operations.baseclusteroperation import ACTIVE_OPERATION_STATES
from shakenfist.schema.operations.baseclusteroperation import TERMINAL_OPERATION_STATES
from shakenfist.tests import base
from shakenfist.tests import mock_mariadb


def _reachable_cluster_op_states():
    seen = set()
    frontier = [None]
    while frontier:
        state = frontier.pop()
        for nxt in BaseClusterOperation.state_targets.get(state) or ():
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return seen


class StateListCoherenceTestCase(base.ShakenFistTestCase):
    def test_lists_partition_reachable_states(self):
        reachable = _reachable_cluster_op_states()
        self.assertEqual(
            {'queued', 'executing', 'complete', 'abort', 'error', 'deleted'},
            reachable)

        active = set(ACTIVE_OPERATION_STATES)
        terminal = set(TERMINAL_OPERATION_STATES)
        self.assertEqual(set(), active & terminal)
        self.assertEqual(reachable, (active | terminal) & reachable)

        # preflight is an instance state. It is in the in-flight set, but
        # nothing can put a cluster operation there.
        self.assertNotIn('preflight', reachable)

    def test_mariadb_reads_the_shared_sets(self):
        self.assertIs(ACTIVE_OPERATION_STATES, mariadb._ACTIVE_OPERATION_STATES)
        self.assertIs(TERMINAL_OPERATION_STATES, mariadb._TERMINAL_OPERATION_STATES)

    def test_is_outstanding_agrees_with_sql_on_reachable_states(self):
        active = set(ACTIVE_OPERATION_STATES)
        for state in _reachable_cluster_op_states():
            with self.subTest(state=state):
                op = mock.MagicMock()
                op.state.value = state
                self.assertEqual(
                    state in active, BaseClusterOperation.is_outstanding(op))

    def test_mock_database_agrees_on_pending(self):
        db = mock_mariadb.MockMariaDB(self, node_count=1)
        db.setup()
        for state in _reachable_cluster_op_states():
            with self.subTest(state=state):
                db.mariadb_states['net_op/op'] = {'state_value': state}
                row = mock.MagicMock(operation_type='net_op', operation_uuid='op')
                self.assertEqual(
                    state in ACTIVE_OPERATION_STATES, db._cot_is_pending(row))

    def test_functional_suite_terminal_states_match_server(self):
        # cluster_ci_tests runs against a deployed cluster without importing
        # the server, so it carries its own copy of the terminal set.
        path = os.path.join(
            os.path.dirname(shakenfist.__file__), 'deploy', 'shakenfist_ci',
            'cluster_ci_tests', 'test_observational_sweeps.py')
        with open(path) as f:
            tree = ast.parse(f.read())
        found = None
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and node.targets[0].id == 'TERMINAL_STATES'):
                found = ast.literal_eval(node.value)
        self.assertEqual(set(TERMINAL_OPERATION_STATES), found)
        self.assertEqual(
            {BaseClusterOperation.STATE_COMPLETE,
             BaseClusterOperation.STATE_ABORT,
             dbo.STATE_DELETED, dbo.STATE_ERROR},
            found)
