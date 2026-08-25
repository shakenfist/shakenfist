# Copyright 2026 Michael Still and contributors

import json
import time

from shakenfist_client import apiclient
from testtools import content

from shakenfist_ci import base


# The two messages which mean a coalescing query matched a row. The
# enqueue-side dedup found a pending sibling and reused it; the
# worker-side fold found pending siblings and marked them complete.
# Both are emitted against the network as well as the operation,
# because an operation is hard deleted thirty seconds after it
# completes and takes its events with it (#3864), and the network
# outlives it.
DEDUP_EVENT = 'enqueue-side dedup: reused pending op'
FOLD_EVENT = 'coalesced sibling ops'

# How many instances to start at once on the one network. Each
# interface on a network triggers a network_apply_update_dnsmasq
# enqueue during instance start, and all of them land on the single
# cluster-wide networknode queue which one elected worker drains. Six
# is the number from the original report which motivated the
# queue-performance plan -- six instance starts on one network, each
# enqueueing one update_dnsmasq, serviced strictly serially -- and it
# is deliberately not larger: this test has to buy overlap on a loaded
# CI cluster without adding a materially longer instance-create burst
# to the suite. If it ever stops overlapping reliably, raise this
# before weakening the assertion.
BURST = 6


class TestCoalescing(base.BaseNamespacedTestCase):
    """Assert that cluster operation coalescing matches rows on a real cluster.

    This exists because it did not. Coalescing shipped in 2026-05 and was
    dead on arrival: both of its SQL primitives joined an undashed uuid to
    a dashed one and an enum value to an enum name, so neither could ever
    match a row (#3878). It took three months and a hand audit to notice,
    because nothing anywhere asserted that coalescing did anything, and a
    fold which matches nothing is indistinguishable from a fold which is
    switched off.

    The assertion is deliberately "one of the two mechanisms fired"
    rather than a specific one. Coalescing has two halves and #3878 broke
    both at once, so either firing proves the join works. Demanding the
    worker-side fold specifically would be demanding a race: the
    enqueue-side dedup is the common path -- it returns the pending op's
    uuid instead of inserting a second row -- so a sibling for the fold
    to find only exists when two callers both missed that lookup.
    """

    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'coalescing'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()
        self.net = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-net' % self.namespace)
        self._await_networks_ready([self.net['uuid']])

        # The namespaced test client pauses for up to sixty seconds on an
        # async operation, which would serialise the burst below into six
        # sequential instance creates and leave nothing to coalesce. Fire
        # them through a client which does not wait instead.
        self.burst_client = apiclient.Client(
            base_url=self.system_client.base_url,
            namespace=self.namespace, key=self.namespace_key,
            async_strategy=apiclient.ASYNC_CONTINUE)

    def _coalescing_events(self):
        events = self.test_client.get_network_events(self.net['uuid'])
        return events, [
            e for e in events
            if str(e.get('message', '')) in (DEDUP_EVENT, FOLD_EVENT)]

    def test_duplicate_network_work_is_coalesced(self):
        instances = []
        for i in range(BURST):
            instances.append(self.burst_client.create_instance(
                'coalesce-%d' % i, 1, 1024,
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
                ], None, None))

        self.addDetail('instances', content.text_content(json.dumps(
            [i['uuid'] for i in instances], indent=4, sort_keys=True)))

        # Events are eventually consistent: the emitting daemons spool
        # locally and a per-process drainer ships them to sf-eventlog in
        # ~100ms batches, and the operations themselves are still being
        # dispatched while this polls. Poll to a deadline rather than
        # reading once -- the same reason test_events.py does.
        events = []
        coalesced = []
        deadline = time.time() + 300
        while time.time() < deadline:
            events, coalesced = self._coalescing_events()
            if coalesced:
                break
            time.sleep(5)

        self.addDetail('coalescing_events', content.text_content(json.dumps(
            coalesced, indent=4, sort_keys=True)))
        # Which mechanism fired, recorded even on a pass. A shift from
        # one to the other is a real change in behaviour and a human
        # reading a green run should be able to see it without having to
        # make the test fail first.
        counts = {}
        for event in coalesced:
            message = str(event.get('message', ''))
            counts[message] = counts.get(message, 0) + 1
        self.addDetail('coalescing_mechanisms', content.text_content(
            json.dumps(counts, indent=4, sort_keys=True)))

        if not coalesced:
            self.addDetail('network_events', content.text_content(json.dumps(
                events, indent=4, sort_keys=True)))

        self.assertNotEqual(
            0, len(coalesced),
            'Starting %d instances on one network produced neither a '
            '"%s" nor a "%s" event on that network. Either coalescing is '
            'matching no rows -- which is the #3878 defect, and what this '
            'test exists to catch -- or the burst no longer overlaps. '
            'Check the coalesce_outcome field on the operations\' '
            '"execution duration" events to tell those apart.'
            % (BURST, DEDUP_EVENT, FOLD_EVENT))
