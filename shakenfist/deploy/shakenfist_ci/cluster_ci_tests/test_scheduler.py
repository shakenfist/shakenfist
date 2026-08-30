import json

from testtools import content

from shakenfist_ci import base


class TestAffinity(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'affinity'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

    def _scheduler_events(self, instance_uuid):
        """The scheduler's audit trail for an instance.

        Read with event_type='audit' and the API's maximum limit rather
        than its default of 100. The rows come back newest first, and
        the scheduling events are the *oldest* an instance has -- behind
        all of its networking, image, boot and agent events -- so a
        default read returns the newest hundred and drops exactly the
        ones this test needs. Both arguments matter: 1000 is the
        endpoint's hard cap, so the audit filter is what keeps a busy
        create inside it rather than being tidiness.
        """
        return [
            e for e in self.system_client.get_instance_events(
                instance_uuid, event_type='audit', limit=1000)
            if str(e.get('message', '')).startswith('schedule')]

    def _add_scheduler_detail(self, name, events):
        """Attach the scheduler's audit trail for an instance.

        The scheduler records its candidate set, each filter stage's
        dropped map and the full per-candidate affinity breakdown as
        audit events with an "extra" payload. base._log_events() renders
        only the message column, so the payload is attached here instead:
        without it a placement assertion failure dies with the ephemeral
        CI cluster and cannot be diagnosed afterwards.
        """
        self.addDetail(
            '%s scheduler events' % name,
            content.text_content(json.dumps(
                events, indent=4, sort_keys=True, default=str)))

    def _unforced_affinity_event(self, name, events):
        """The 'schedule have highest affinity' event of the create pass.

        find_candidates() runs more than once per instance: unforced
        from the create path, forced when the instance is already
        placed, and forced again by preflight against the node it landed
        on. Each forced call publishes its own affinity event carrying a
        single candidate, so matching on the message alone finds a pass
        that had no choice to make and reads as a degenerate run.

        The unforced pass is identified by its 'schedule inputs' event,
        which publishes forced_candidates=False, and then paired to the
        affinity event by request_id. That id is set for every API
        request by the RequestID WSGI middleware, and is absent for the
        preflight calls because they run in the queue daemon with no
        flask request at all -- which is what makes it a discriminator
        rather than merely an identifier.
        """
        inputs = [
            e for e in events
            if str(e.get('message', '')) == 'schedule inputs'
            and not (e.get('extra') or {}).get('forced_candidates')]
        affinity = [
            e for e in events
            if str(e.get('message', '')) == 'schedule have highest affinity']

        if not inputs or not affinity:
            self.fail(
                '%s: no unforced scheduling pass in its events (%d inputs, '
                '%d affinity). A missing event means the read is wrong, not '
                'that the run was degenerate.' % (
                    name, len(inputs), len(affinity)))

        # Pair on request_id where there is one to pair on. A null id on
        # both sides would match every candidate event, and taking the
        # first or last could select a forced pass -- whose single-entry
        # affinity_detail then reads as a degenerate run and skips
        # forever. Fall back to adjacency only when there is no id, and
        # fail rather than skip if that cannot identify a pass either.
        request_id = inputs[0].get('request_id')
        if request_id:
            matched = [
                e for e in affinity if e.get('request_id') == request_id]
            if matched:
                return matched[0]
            self.fail(
                '%s: no affinity event shares request_id %s with the '
                'unforced schedule inputs event' % (name, request_id))

        ordered = sorted(events, key=lambda e: e.get('timestamp') or 0)
        try:
            index = ordered.index(inputs[0])
        except ValueError:
            index = -1
        for candidate in ordered[index + 1:]:
            if str(candidate.get('message', '')) == \
                    'schedule have highest affinity':
                return candidate

        self.fail(
            '%s: the unforced schedule inputs event carries no request_id '
            'and no affinity event follows it' % name)

    def test_affinity(self):
        nodes = self.system_client.get_nodes()
        self.addDetail('nodes', content.text_content(json.dumps(
            nodes, indent=4, sort_keys=True)))
        if len(nodes) < 3:
            self.skipTest('Insufficient nodes for test')

        # Create an instance with a tag
        inst1 = self.test_client.create_instance(
            'inst1', 1, 1024,
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
            ], None, None, metadata={
                'tags': ['first-node']
                }
            )
        self._await_instance_create(inst1['uuid'])

        # Now create two more instances, one with affinity one without
        inst2 = self.test_client.create_instance(
            'inst2', 1, 1024,
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
            ], None, None, metadata={
                'affinity': {
                    'first-node': 100
                    }
                }
            )
        inst3 = self.test_client.create_instance(
            'inst3', 1, 1024,
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
            ], None, None, metadata={
                'affinity': {
                    'first-node': -100
                    }
                }
            )

        self._await_instance_create(inst2['uuid'])
        self._await_instance_create(inst3['uuid'])

        # Refresh out view of the instances
        inst1 = self.test_client.get_instance(inst1['uuid'])
        inst2 = self.test_client.get_instance(inst2['uuid'])
        inst3 = self.test_client.get_instance(inst3['uuid'])

        self.addDetail('inst1', content.text_content(json.dumps(
            inst1, indent=4, sort_keys=True)))
        self.addDetail('inst2', content.text_content(json.dumps(
            inst2, indent=4, sort_keys=True)))
        self.addDetail('inst3', content.text_content(json.dumps(
            inst3, indent=4, sort_keys=True)))

        events = {}
        for name, inst in [('inst1', inst1), ('inst2', inst2),
                           ('inst3', inst3)]:
            events[name] = self._scheduler_events(inst['uuid'])
            self._add_scheduler_detail(name, events[name])

        # What follows asserts what soft affinity actually promises: that
        # the scorer preferred, or avoided, the node inst1 is on. It does
        # not assert final co-location. A preference is consulted when
        # there is a choice, and the scheduler is free to place elsewhere
        # when there is not -- so an assertion on where inst2 landed passes or
        # fails on whether the cluster happened to offer a choice, which
        # is what made issue 3565 look like an affinity bug for months.
        self._assert_affinity_tier(
            'inst2', events['inst2'], inst1['node'], expected=True)
        self._assert_affinity_tier(
            'inst3', events['inst3'], inst1['node'], expected=False)

    def _assert_affinity_tier(self, name, events, affine_node, expected):
        """Assert whether the scorer put affine_node in the winning tier.

        Two degeneracies make a run say nothing about affinity, and both
        skip rather than pass. A pass would be a false green: the
        assertion cannot fail, so it is not evidence.
        """
        event = self._unforced_affinity_event(name, events)
        extra = event.get('extra') or {}
        detail = extra.get('affinity_detail') or {}

        # The candidate count comes from affinity_detail, which has one
        # entry per node the scorer actually considered. It does not come
        # from extra['candidates'], which is the *winning tier* after
        # scoring and is 1 whenever affinity works -- the trap being that
        # 'candidates' means a real candidate list in the sibling events.
        if len(detail) < 2:
            self.skipTest(
                '%s: the scorer had only %d candidate(s), so affinity was '
                'never consulted and this run carries no information about '
                'it' % (name, len(detail)))

        # The other degeneracy: the affinity target itself was ejected by
        # an admission filter before scoring ran. The count guard does not
        # catch this, because two or three other candidates can remain.
        # This is issue 3565's real mechanism and it is not an affinity
        # defect, so the run is uninformative rather than failing.
        if affine_node not in detail:
            self.skipTest(
                '%s: affine node %s was not a candidate, having been '
                'dropped by an admission filter before affinity scoring; '
                'this run says nothing about affinity in either direction'
                % (name, affine_node))

        # extra['candidates'] is the winning tier, which is the wrong
        # source for the count above and the right one for this.
        tier = extra.get('candidates') or []
        if expected:
            self.assertIn(
                affine_node, tier,
                '%s asked to be near %s, and the scorer had a choice of %d '
                'nodes, but that node is not in the winning affinity tier '
                '%s (scoring detail: %s)' % (
                    name, affine_node, len(detail), tier, detail))
        else:
            self.assertNotIn(
                affine_node, tier,
                '%s asked to avoid %s, and the scorer had a choice of %d '
                'nodes, but that node is in the winning affinity tier %s '
                '(scoring detail: %s)' % (
                    name, affine_node, len(detail), tier, detail))
