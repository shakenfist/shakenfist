# Copyright 2026 Michael Still and contributors
#
# The invariant that makes cluster operation coalescing safe.
#
# Neither dedup path keys on the queue: the enqueue-side lookup and the
# worker-side fold both match on (op_type, COALESCIBLE_KEY_COLUMNS,
# task, state), and cluster_operations has no queue column for them to
# filter on. The key is therefore what has to distinguish one queue's
# work from another's, and a coalescible task enqueued where it cannot
# is folded across nodes with one host's work silently never applied --
# a stale FDB, invisible until something much later fails.
#
# The key does distinguish two cases: the cluster-wide network-node
# queue, which one elected worker drains, and a per-node queue when the
# key names node_uuid and the operation carries one. Everything else is
# refused at enqueue time, and this module is where that is checked.
# While COALESCIBLE_KEY_COLUMNS remains ('network_uuid',) that means
# every per-node enqueue of a coalescible task, which is what the cases
# below assert. See also the note on COALESCIBLE_TASKS in
# shakenfist/schema/operations/net_op.py and the PARTITIONED-WORKER
# SAFETY INVARIANT in shakenfist/daemons/network/workitem.py.

import ast
import os
from unittest import mock
from uuid import uuid4

from shakenfist import exceptions
from shakenfist.schema.operations import net_op
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.net_op import COALESCIBLE_TASKS
from shakenfist.schema.operations.net_op import model_tasks
from shakenfist.tests import base


class NetOpCoalescingGuardTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        self.enqueue = mock.patch(
            'shakenfist.schema.operations.net_op.enqueue_cluster_operation')
        self.mock_enqueue = self.enqueue.start()
        self.addCleanup(self.enqueue.stop)

        self.find = mock.patch(
            'shakenfist.mariadb.find_existing_coalescible_op',
            return_value=None)
        self.mock_find = self.find.start()
        self.addCleanup(self.find.stop)

    def test_ensure_mesh_is_not_coalescible(self):
        # It is the one NetOp task which does node-local work, and the
        # fold's key (the network) cannot tell one node's mesh op from
        # another's. See issue #3884 for the multi-column key which would
        # let it back in.
        self.assertNotIn(model_tasks.network_ensure_mesh, COALESCIBLE_TASKS)

    def test_a_coalescible_task_may_not_be_enqueued_per_node(self):
        for task in sorted(COALESCIBLE_TASKS, key=lambda t: t.name):
            self.assertRaises(
                exceptions.InvalidCoalescibleEnqueue,
                net_op.create_and_enqueue,
                str(uuid4()), [task], PRIORITY.user_facing,
                target=str(uuid4()), family='network')

    def test_the_guard_sees_a_coalescible_task_in_a_multi_task_list(self):
        # The fold takes its task_names from every coalescible task in
        # the survivor's list, whatever else is alongside them, so the
        # guard has to look at the whole list too.
        self.assertRaises(
            exceptions.InvalidCoalescibleEnqueue,
            net_op.create_and_enqueue,
            str(uuid4()),
            [model_tasks.network_remove_nat,
             model_tasks.network_apply_update_dnsmasq],
            PRIORITY.user_facing,
            target=str(uuid4()), family='network')

    def test_a_non_coalescible_task_may_still_be_enqueued_per_node(self):
        # The guard must not become a blanket ban on per-node NetOps --
        # network_ensure_mesh is enqueued that way on every hypervisor.
        net_op.create_and_enqueue(
            str(uuid4()), [model_tasks.network_ensure_mesh],
            PRIORITY.user_facing, target=str(uuid4()), family='network')
        self.mock_enqueue.assert_called_once()

    def test_a_coalescible_task_is_fine_on_the_networknode_queue(self):
        net_op.create_and_enqueue(
            str(uuid4()), [model_tasks.network_apply_update_dnsmasq],
            PRIORITY.user_facing)
        self.mock_enqueue.assert_called_once()


class NetOpEnqueueSiteTestCase(base.ShakenFistTestCase):
    """Every per-node enqueue site, checked statically.

    The runtime guard above turns a violation into an exception, but only
    on the code path that runs. This walks the source instead, so a
    per-node enqueue of a coalescible task fails the suite whether or not
    any test happens to execute that line.
    """

    def _source_root(self):
        return os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))

    def _net_op_enqueue_calls(self):
        root = self._source_root()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d not in ('tests', 'protos', '__pycache__', 'deploy')]
            for filename in filenames:
                if not filename.endswith('.py'):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path) as f:
                    tree = ast.parse(f.read(), filename=path)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    name = (getattr(node.func, 'id', None)
                            or getattr(node.func, 'attr', None))
                    if name != 'net_create_and_enqueue':
                        continue
                    yield path, node

    def test_no_caller_enqueues_a_coalescible_task_per_node(self):
        coalescible = {t.name for t in COALESCIBLE_TASKS}
        seen_any = False
        for path, node in self._net_op_enqueue_calls():
            seen_any = True
            keywords = {k.arg: k.value for k in node.keywords if k.arg}
            target = keywords.get('target')
            # No target keyword means the 'networknode' default.
            if target is None:
                continue
            if (isinstance(target, ast.Constant)
                    and target.value == 'networknode'):
                continue

            tasks_node = keywords.get('tasks')
            if tasks_node is None and len(node.args) > 1:
                tasks_node = node.args[1]
            if not isinstance(tasks_node, ast.List):
                continue
            task_names = {
                getattr(elt, 'attr', None) for elt in tasks_node.elts}
            overlap = task_names & coalescible
            self.assertEqual(
                set(), overlap,
                f'{path}:{node.lineno} enqueues coalescible task(s) '
                f'{sorted(overlap)} to a per-node target. Coalescing folds '
                f'those together by network, so one node\'s work would be '
                f'silently dropped.')

        # If the walk stops finding call sites the test has quietly
        # stopped checking anything.
        self.assertTrue(seen_any, 'found no net_create_and_enqueue calls')
