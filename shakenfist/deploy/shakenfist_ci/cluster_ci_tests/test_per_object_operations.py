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

    def _non_blocking_client(self):
        """A client which returns as soon as a request is accepted.

        The suite's client pauses until an instance leaves its
        transitional states, by which time the create may well have
        finished. Reading an object while its create is still
        outstanding needs a client which does not wait.
        """
        return apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=self.namespace, key=self.namespace_key,
            async_strategy=apiclient.ASYNC_CONTINUE)

    def _assert_pending_while_outstanding(self, kind, view, outstanding):
        """has_pending_operations is true while a mutating op is outstanding.

        The view is read before the listing, and the order matters. An
        operation which is still non-terminal in the later listing, and
        which was enqueued before the view was read, was non-terminal
        when the view was read too, so a false flag on that view is a
        defect. Read the other way round, an operation finishing between
        the two reads shows as active in the listing and false on the
        view, and the test fails on a race it did not mean to assert.

        When the listing shows nothing outstanding the flag cannot be
        asserted from this view. That is recorded as a tracing event as
        well as a detail, so a fleet where the create always beats the
        read is visible from the traces rather than only from a subunit
        stream nobody opens.
        """
        self._record('outstanding', outstanding)
        active = [op for op in outstanding
                  if op['state'] not in TERMINAL_STATES
                  and not op.get('observational')]
        if active:
            self.assertTrue(
                view.get('has_pending_operations'),
                'The %s has a mutating operation outstanding but its view '
                'does not say so: %s' % (kind, view))
            return

        self._emit_tracing_event({
            'msg': 'Pending flag not asserted while outstanding',
            'object_type': kind,
            'object_uuid': view.get('uuid'),
            'reason': 'no mutating operation was outstanding when the '
                      'listing was read',
        })
        self.addDetail('pending-flag-while-outstanding',
                       content.text_content(
                           'not asserted: no mutating operation was '
                           'outstanding when the listing was read'))

    def test_instance_listing_and_pending_flag(self):
        inst = self.create_instance(
            *self._instance_spec('objops-inst'),
            client=self._non_blocking_client())
        self._record('inst', inst)

        # The view first, then the listing; see the helper for why.
        view = self.test_client.get_instance(inst['uuid'])
        outstanding = self.test_client.get_instance_clusteroperations(
            inst['uuid'])
        self._assert_pending_while_outstanding('instance', view, outstanding)

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
        # Artifacts are cached per namespace, so this is a fresh artifact
        # in this test's own namespace however many times the fleet has
        # fetched the URL. The stranger refusal below rests on that, so
        # it is asserted rather than assumed.
        self.assertEqual(self.namespace, art.get('namespace'))

        # The view first, then the listing; see the helper for why.
        view = self.test_client.get_artifact(art['uuid'])
        outstanding = self.test_client.get_artifact_clusteroperations(
            art['uuid'])
        self._assert_pending_while_outstanding('artifact', view, outstanding)

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
        non_blocking = self._non_blocking_client()
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
        # Observational sweeps are left out, as the listings above leave
        # them out. enqueue_delete() aborts the ones it finds, and the
        # unit suite pins that, but the cleaner enqueues a sweep on its
        # own cadence and one which arrives after the walk is not this
        # test's finding.
        lingering = [op for op in every
                     if op['state'] not in TERMINAL_STATES
                     and not op.get('observational')]
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
