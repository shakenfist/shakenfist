# Copyright 2026 Michael Still and contributors
"""A repeated instance DELETE, shared by the smoke and cluster suites.

The assertion needs one instance on one network, so it is portable
across every CI topology. It lives here rather than in a suite
directory because stestr discovers tests per directory and the two
suites are disjoint: a test defined in ``cluster_ci_tests/`` runs for
the first time in the merge queue. Subclassing
``RepeatedDeleteTestsMixin`` from both suites runs it in PR CI as well,
without giving up the multi-node coverage.
"""

import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


class RepeatedDeleteTestsMixin:
    """A second DELETE does not abort the delete already in flight.

    ``Instance.enqueue_delete()`` used to abort every outstanding
    operation targeting the instance, the earlier ``instance_delete``
    included, so a client which sent DELETE twice left an aborted
    delete behind and had the executor's own completion write refused --
    ``abort`` has no transition to ``complete`` (issue 4722). An
    outstanding delete is a survivor now, and the delete enqueued by
    the second request waits for it instead.

    Mix into a ``base.BaseNamespacedTestCase`` subclass in a suite
    directory; this class defines no ``__init__`` so the concrete class
    sets its own namespace prefix.
    """

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

    def test_second_delete_does_not_abort_the_first(self):
        inst = self.create_instance(
            'repeated-delete', 1, 1024,
            [{'network_uuid': self.net['uuid']}],
            [{'size': 8, 'base': base.CLUSTER_CI_IMAGE, 'type': 'disk'}],
            None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))
        self._await_instance_ready(inst['uuid'])

        # Both requests go through a client which returns as soon as one
        # is accepted, so the second arrives while the first delete is
        # still queued or executing. Which of the two it caught is
        # recorded rather than required.
        non_blocking = apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=self.namespace, key=self.namespace_key,
            async_strategy=apiclient.ASYNC_CONTINUE)
        non_blocking.delete_instance(inst['uuid'])
        try:
            non_blocking.delete_instance(inst['uuid'])
            second = 'accepted'
        except apiclient.ResourceNotFoundException:
            # A 404 means the first delete had already finished, which is
            # a legitimate outcome of this race rather than a failure.
            second = '404, the first delete had already finished'
        self.addDetail('second-delete', content.text_content(second))

        self._await_instance_deleted(inst['uuid'])

        # Read promptly: an operation is eligible for hard delete thirty
        # seconds after it goes terminal, and takes the evidence with it.
        every = self.system_client.list_cluster_operations_for_target(
            'instance', inst['uuid'])
        self.addDetail(
            'every',
            content.text_content(json.dumps(every, indent=4, sort_keys=True)))
        deletes = [op for op in every if 'instance_delete' in op['tasks']]
        self.assertNotEqual(
            [], deletes, 'No instance_delete operation was found: %s' % every)
        self.assertEqual(
            [], [op for op in deletes if op['state'] == 'abort'],
            'A repeated delete request aborted an instance_delete: %s'
            % deletes)
