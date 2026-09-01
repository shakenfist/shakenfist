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
# key names node_uuid and the operation carries one. The second case
# additionally requires family='network', because that is what routes
# the operation to sf-net, which hashes every operation for a network
# onto one worker thread; the default clusteroperation family sends
# per-node queues to sf-queues, which partitions nothing. Everything
# else is refused at enqueue time, and this module is where that is
# checked -- once at runtime through the guard, and once statically
# over every call site, so a violation fails the suite whether or not
# a test happens to execute that line. See also the note on
# COALESCIBLE_TASKS in shakenfist/schema/operations/net_op.py and the
# PARTITIONED-WORKER SAFETY INVARIANT in
# shakenfist/daemons/network/workitem.py.

import ast
import os
from unittest import mock
from uuid import uuid4

from shakenfist import exceptions
from shakenfist.schema.operations import net_op
from shakenfist.schema.operations.baseclusteroperation import PRIORITY
from shakenfist.schema.operations.net_op import COALESCIBLE_KEY_COLUMNS
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

    def test_ensure_mesh_is_coalescible_only_with_a_node_aware_key(self):
        # network_ensure_mesh is the one NetOp task which does
        # node-local work. It may only be in COALESCIBLE_TASKS while the
        # key names node_uuid -- otherwise one hypervisor's mesh op and
        # another's are indistinguishable to both dedup paths. The two
        # facts are asserted together because either one alone is the
        # bug: see issue #3884.
        self.assertIn(model_tasks.network_ensure_mesh, COALESCIBLE_TASKS)
        self.assertIn(
            'node_uuid', COALESCIBLE_KEY_COLUMNS,
            'network_ensure_mesh does node-local work, so it cannot be '
            'coalescible unless the key can tell one node apart from '
            'another')

    def test_a_coalescible_task_may_not_be_enqueued_off_the_network_family(
            self):
        # A per-node target on the default clusteroperation family goes
        # to sf-queues, which starts one worker per claimed item with no
        # routing key. A node-aware key is not enough there.
        for task in sorted(COALESCIBLE_TASKS, key=lambda t: t.name):
            self.assertRaises(
                exceptions.InvalidCoalescibleEnqueue,
                net_op.create_and_enqueue,
                str(uuid4()), [task], PRIORITY.user_facing,
                target=str(uuid4()))

    def test_a_coalescible_task_may_be_enqueued_per_node_on_sf_net(self):
        # The case this whole phase exists to allow: a per-node target
        # on the network family, with a key which names node_uuid.
        for task in sorted(COALESCIBLE_TASKS, key=lambda t: t.name):
            self.mock_enqueue.reset_mock()
            net_op.create_and_enqueue(
                str(uuid4()), [task], PRIORITY.user_facing,
                target=str(uuid4()), family='network')
            self.mock_enqueue.assert_called_once()

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
            target=str(uuid4()))

    def test_a_non_coalescible_task_may_still_be_enqueued_per_node(self):
        # The guard must not become a blanket ban on per-node NetOps,
        # and it must not become a blanket ban on the clusteroperation
        # family either -- a task which never folds is unaffected by
        # where it is drained.
        net_op.create_and_enqueue(
            str(uuid4()), [model_tasks.network_remove_nat],
            PRIORITY.user_facing, target=str(uuid4()))
        self.mock_enqueue.assert_called_once()

    def test_a_coalescible_task_is_fine_on_the_networknode_queue(self):
        net_op.create_and_enqueue(
            str(uuid4()), [model_tasks.network_apply_update_dnsmasq],
            PRIORITY.user_facing)
        self.mock_enqueue.assert_called_once()


class NetOpReusablePrioritiesTestCase(base.ShakenFistTestCase):
    """Which pending ops an enqueue is allowed to be deduped onto.

    Reuse is deliberately one-sided. A more urgent pending op is free
    to adopt -- the work runs sooner than asked. A less urgent one is
    not: queues are named '{target}-{family}-{priority}', so adopting
    it means the caller in raise_for_error(), and the runs_after
    dependency an instance start hangs off, wait out the slower lane's
    queue-sit tail instead of their own.

    Both coalescible tasks with two enqueue sites straddle the line.
    Network.ensure_mesh enqueues network_ensure_mesh at user_facing
    while daemons/network/maintain.py enqueues the same task, for the
    same network and the same node, at background -- and it does so
    precisely when is_mesh_okay() reports drift, which is the state a
    network is in immediately after an instance starts elsewhere on it.
    create_on_network_node and the maintainer straddle it the same way
    for network_apply_create_network_node.
    """

    def setUp(self):
        super().setUp()
        self.enqueue = mock.patch(
            'shakenfist.schema.operations.net_op.enqueue_cluster_operation')
        self.mock_enqueue = self.enqueue.start()
        self.addCleanup(self.enqueue.stop)

    def test_more_urgent_priorities_are_reusable(self):
        self.assertEqual(
            ['user_waiting', 'user_facing'],
            net_op._reusable_priorities(PRIORITY.user_facing))

    def test_the_most_urgent_priority_may_only_reuse_itself(self):
        self.assertEqual(
            ['user_waiting'],
            net_op._reusable_priorities(PRIORITY.user_waiting))

    def test_the_least_urgent_priority_may_reuse_anything(self):
        self.assertEqual(
            [p.name for p in PRIORITY],
            net_op._reusable_priorities(PRIORITY.background_high_io))

    def test_no_priority_may_reuse_a_less_urgent_one(self):
        # Stated as the property rather than as five hand written
        # lists, so a PRIORITY member added later is covered without
        # anyone remembering to come back here.
        for priority in PRIORITY:
            reusable = net_op._reusable_priorities(priority)
            for name in reusable:
                self.assertLessEqual(
                    PRIORITY[name].value, priority.value,
                    f'{priority.name} may not reuse {name}, which is less '
                    f'urgent')
            for other in PRIORITY:
                if other.value <= priority.value:
                    self.assertIn(other.name, reusable)

    @mock.patch('shakenfist.mariadb.find_existing_coalescible_op',
                return_value=None)
    def test_the_enqueue_passes_the_reusable_set(self, mock_find):
        net_op.create_and_enqueue(
            str(uuid4()), [model_tasks.network_apply_update_dnsmasq],
            PRIORITY.user_facing)
        mock_find.assert_called_once()
        self.assertEqual(
            ['user_waiting', 'user_facing'],
            mock_find.call_args.kwargs['priorities'])


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

    @staticmethod
    def _argument(node, keywords, name, position):
        """One argument of a call, by keyword or by position."""
        if name in keywords:
            return keywords[name]
        if len(node.args) > position:
            return node.args[position]
        return None

    def test_per_node_coalescible_enqueues_are_key_aware_and_partitioned(self):
        # create_and_enqueue(network_uuid, tasks, priority, request_id,
        #                    depends_on, runs_after, target, family, ...)
        coalescible = {t.name for t in COALESCIBLE_TASKS}
        seen_any = False
        per_node_sites = 0

        for path, node in self._net_op_enqueue_calls():
            seen_any = True
            keywords = {k.arg: k.value for k in node.keywords if k.arg}

            target = self._argument(node, keywords, 'target', 6)
            # No target argument means the 'networknode' default.
            if target is None:
                continue
            if (isinstance(target, ast.Constant)
                    and target.value == 'networknode'):
                continue
            per_node_sites += 1

            tasks_node = self._argument(node, keywords, 'tasks', 1)
            if not isinstance(tasks_node, ast.List):
                continue
            task_names = {
                getattr(elt, 'attr', None) for elt in tasks_node.elts}
            overlap = task_names & coalescible
            if not overlap:
                continue

            # Rule one: the key has to be able to tell this node's work
            # apart from another node's. This is the check which catches
            # the phase 8 bug -- a coalescible task routed per-node
            # while COALESCIBLE_KEY_COLUMNS is the network alone.
            self.assertIn(
                'node_uuid', COALESCIBLE_KEY_COLUMNS,
                f'{path}:{node.lineno} enqueues coalescible task(s) '
                f'{sorted(overlap)} to a per-node target, but the coalescing '
                f'key {COALESCIBLE_KEY_COLUMNS} cannot tell one node\'s work '
                f'from another\'s. Coalescing would fold those together by '
                f'network, silently dropping one node\'s work.')

            # Rule two: a node-aware key is necessary but not
            # sufficient. Only the network family reaches sf-net, whose
            # per-target worker partitioning is what stops a fold
            # marking complete an operation another thread is running.
            family = self._argument(node, keywords, 'family', 7)
            self.assertTrue(
                isinstance(family, ast.Constant) and family.value == 'network',
                f'{path}:{node.lineno} enqueues coalescible task(s) '
                f'{sorted(overlap)} to a per-node target on family '
                f'{getattr(family, "value", None)!r}. Only family=\'network\' '
                f'is drained by sf-net, which partitions its workers by '
                f'target; sf-queues does not, so two of its workers can hold '
                f'two operations for the same (network, node) at once.')

        # If the walk stops finding call sites the test has quietly
        # stopped checking anything.
        self.assertTrue(seen_any, 'found no net_create_and_enqueue calls')
        self.assertTrue(
            per_node_sites, 'found no per-node net_create_and_enqueue calls, '
            'so the rules above checked nothing')
