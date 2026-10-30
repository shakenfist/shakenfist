import json

from testtools import content

from shakenfist_ci import base
from shakenfist_client import apiclient


# The terminal operation states, as cluster_ci_tests/test_observational_sweeps
# spells them. Repeated rather than imported because this suite runs from an
# installed shakenfist_ci package against a remote cluster.
TERMINAL_STATES = {'complete', 'error', 'abort', 'deleted'}


class TestPerObjectClusterOperations(base.BaseNamespacedTestCase):
    """The per-object cluster operation listings, and the pending flag.

    GET /instances/<ref>/clusteroperations and its artifact sibling, the
    has_pending_operations field on those views, and the namespace
    refusal on the instance and network listings had no functional
    coverage: only the network listing was read, by test_api.py, and the
    artifact listing's namespace refusal was pinned by a unit test alone
    (issue 4669). Each test here reads through the client wrapper, which
    is the path a caller uses.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'objops'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self.addDetail(
            'net',
            content.text_content(json.dumps(self.net, indent=4, sort_keys=True)))
        self._await_networks_ready([self.net['uuid']])

        # A second tenant, for the namespace refusals. A uuid is not a
        # secret, so knowing one must not be a way to read what targets
        # another namespace's object.
        self.stranger_namespace = 'ci-objops-stranger-%s' % self._uniquifier()
        self.stranger = self._make_namespace(
            self.stranger_namespace, self._uniquifier())
        self.addCleanup(self._remove_namespace, self.stranger_namespace)

    def _instance_spec(self, name):
        return (
            name, 1, 1024,
            [
                {
                    'network_uuid': self.net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': base.CLUSTER_CI_IMAGE,
                    'type': 'disk'
                }
            ], None, None)

    def _record(self, name, value):
        self.addDetail(name, content.text_content(
            json.dumps(value, indent=4, sort_keys=True)))

    def test_instance_listing_and_pending_flag(self):
        # The suite's client pauses until the instance leaves its
        # transitional states, by which time the create may well have
        # finished. The view is read through a client which does not
        # wait, so that the instance is seen while its create is still
        # outstanding.
        non_blocking = apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=self.namespace, key=self.namespace_key,
            async_strategy=apiclient.ASYNC_CONTINUE)
        inst = self.create_instance(
            *self._instance_spec('objops-inst'), client=non_blocking)
        self._record('inst', inst)

        # The listing is read before the view: a create which finishes
        # between the two reads then shows as active in the listing and
        # the flag is not asserted on a stale view, rather than failing
        # on one. A create takes seconds, so the listing is expected to
        # show it; a run on which it did not is recorded as such.
        outstanding = self.test_client.get_instance_clusteroperations(
            inst['uuid'])
        self._record('outstanding', outstanding)
        active = [op for op in outstanding
                  if op['state'] not in TERMINAL_STATES
                  and not op.get('observational')]
        if active:
            view = self.test_client.get_instance(inst['uuid'])
            self.assertTrue(
                view.get('has_pending_operations'),
                'The instance has a mutating operation outstanding but its '
                'view does not say so: %s' % view)
        else:
            self.addDetail('pending-flag-while-outstanding',
                           content.text_content(
                               'not asserted: the create had already '
                               'finished when the listing was read'))

        self._await_instance_ready(inst['uuid'])
        self._await_instance_operations_complete(inst['uuid'])

        # Creating the instance enqueued at least one cluster operation
        # and every one of them has now completed, so all=True reveals
        # completed operations and the default listing hides them.
        every = self.test_client.get_instance_clusteroperations(
            inst['uuid'], all=True)
        self._record('every', every)
        completed = [op['uuid'] for op in every
                     if op['state'] in TERMINAL_STATES]
        self.assertNotEqual(
            [], completed,
            'all=True did not reveal any completed operation: %s' % every)
        for op in every:
            self.assertIn('observational', op)
        for op in self.test_client.get_instance_clusteroperations(inst['uuid']):
            self.assertNotIn(op['uuid'], completed)

        view = self.test_client.get_instance(inst['uuid'])
        self.assertFalse(
            view.get('has_pending_operations'),
            'has_pending_operations is true after every operation '
            'completed: %s' % view)

        # The listing is an owner's route. The refusal is a 404, as on
        # every other owner's route, so that it does not confirm the uuid.
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.stranger.get_instance_clusteroperations, inst['uuid'])
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.stranger.get_instance_clusteroperations, inst['uuid'],
            all=True)

    def test_network_listing_refuses_a_stranger(self):
        # The network's own listing is read by test_api.py; what was
        # missing is that another namespace cannot read it.
        self.assertNotEqual(
            [], self.test_client.get_network_clusteroperations(
                self.net['uuid'], all=True))
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.stranger.get_network_clusteroperations, self.net['uuid'])
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.stranger.get_network_clusteroperations, self.net['uuid'],
            all=True)

    def test_artifact_listing_and_pending_flag(self):
        url = 'https://sfcbr.shakenfist.com/gw-basic/gwbasic.qcow2'
        art = self.test_client.cache_artifact(url)
        self._record('art', art)

        # As for the instance: the listing first, and the flag only on a
        # view read while the fetch is known to be outstanding.
        outstanding = self.test_client.get_artifact_clusteroperations(
            art['uuid'])
        self._record('outstanding', outstanding)
        active = [op for op in outstanding
                  if op['state'] not in TERMINAL_STATES
                  and not op.get('observational')]
        if active:
            view = self.test_client.get_artifact(art['uuid'])
            self.assertTrue(
                view.get('has_pending_operations'),
                'The artifact has a mutating operation outstanding but its '
                'view does not say so: %s' % view)
        else:
            self.addDetail('pending-flag-while-outstanding',
                           content.text_content(
                               'not asserted: the fetch had already '
                               'finished when the listing was read'))

        results = self._await_artifacts_ready([art['uuid']])
        self._record('results', results)
        self.system_client.await_no_pending_operations(
            'artifact', art['uuid'], timeout=5 * 60, interval=5)

        every = self.test_client.get_artifact_clusteroperations(
            art['uuid'], all=True)
        self._record('every', every)
        completed = [op['uuid'] for op in every
                     if op['state'] in TERMINAL_STATES]
        self.assertNotEqual(
            [], completed,
            'all=True did not reveal any completed operation: %s' % every)
        for op in every:
            self.assertIn('observational', op)
        for op in self.test_client.get_artifact_clusteroperations(art['uuid']):
            self.assertNotIn(op['uuid'], completed)

        view = self.test_client.get_artifact(art['uuid'])
        self.assertFalse(
            view.get('has_pending_operations'),
            'has_pending_operations is true after every operation '
            'completed: %s' % view)

        # An artifact_fetch_op names the namespace and instance whose boot
        # caused the fetch, so the listing is an owner's route (issue
        # 4666). The unit suite pins the guard; this reads it end to end.
        self.assertRaises(
            apiclient.ResourceNotFoundException,
            self.stranger.get_artifact_clusteroperations, art['uuid'])

    def test_delete_during_create_leaves_no_operation_outstanding(self):
        """Deleting an instance cancels what targets it.

        Instance.enqueue_delete() aborts every outstanding operation
        which targets the instance before it enqueues the delete, so
        that none of them runs on against an instance being torn down.
        The unit suite drives that walk; nothing functional had ever
        deleted an instance while an operation was outstanding (issue
        4669). The create is the operation here, because it is the one
        this suite can reliably catch in flight: the delete is sent the
        moment the create is accepted, through a client which does not
        wait.

        What is asserted is the contract rather than the race: the
        instance ends deleted, and no operation which targeted it is
        left non-terminal. Whether the create was still outstanding when
        the delete arrived is recorded, not required.
        """
        non_blocking = apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=self.namespace, key=self.namespace_key,
            async_strategy=apiclient.ASYNC_CONTINUE)
        inst = self.create_instance(
            *self._instance_spec('objops-delete-during-create'),
            client=non_blocking)
        self._record('inst', inst)

        outstanding = self.system_client.list_cluster_operations_for_target(
            'instance', inst['uuid'])
        self._record('outstanding-at-delete', outstanding)
        non_blocking.delete_instance(inst['uuid'])
        self._await_instance_deleted(inst['uuid'])

        # The by-target listing, through the system client: the instance
        # is deleted, so its own listing may no longer answer.
        every = self.system_client.list_cluster_operations_for_target(
            'instance', inst['uuid'])
        self._record('every', every)
        self.assertNotEqual([], every)
        lingering = [op for op in every if op['state'] not in TERMINAL_STATES]
        self.assertEqual(
            [], lingering,
            'Operations were left outstanding on a deleted instance: %s'
            % lingering)
        self.assertIn(
            'complete',
            [op['state'] for op in every if 'instance_delete' in op['tasks']],
            'No instance_delete operation completed: %s' % every)
        self.addDetail('aborted', content.text_content(json.dumps(
            [op['uuid'] for op in every if op['state'] == 'abort'])))
