import json
import time

import requests
from testtools import content

from shakenfist_ci import base


# Where the smoke tests run (the single-node localhost topology) Loki is
# reachable on localhost. The install helper (tools/ci-install-loki.sh) binds
# Loki to 0.0.0.0:3100, and the collection deploy renders
# SHAKENFIST_LOKI_BASE_URL into every node's config so all daemons ship to
# it. On multi-node topologies Loki lives at the first node's mesh IP, not
# localhost, so this test only belongs in the smoke (single-node) suite.
LOKI_BASE_URL = 'http://localhost:3100'

# How long to wait for a freshly emitted log line to become queryable in
# Loki. The push path is: daemon log -> local spool -> drainer thread ->
# HTTP push -> Loki ingest -> index flush. This mirrors the eventual-
# consistency deadline-poll style of test_events.py.
LOKI_ARRIVAL_DEADLINE = 30


class TestLoki(base.BaseNamespacedTestCase):
    def __init__(self, *args, **kwargs):
        kwargs['namespace_prefix'] = 'loki'
        super().__init__(*args, **kwargs)

    def setUp(self):
        super().setUp()

        # Skip cleanly in any topology where Loki was not stood up (for
        # example a deploy without GETSF_LOKI_BASE_URL). This keeps the
        # test a no-op rather than a failure where there is no Loki to
        # query.
        try:
            r = requests.get('%s/ready' % LOKI_BASE_URL, timeout=5)
        except requests.exceptions.RequestException as e:
            self.skipTest('Loki is not reachable at %s: %s'
                          % (LOKI_BASE_URL, e))

        if r.status_code != 200 or 'ready' not in r.text.lower():
            self.skipTest(
                'Loki is not ready at %s (status %d, body %r)'
                % (LOKI_BASE_URL, r.status_code, r.text))

    def _query_loki(self, tokens, start_ns, end_ns):
        """Query Loki for log lines containing all tokens. Returns streams."""
        if isinstance(tokens, str):
            tokens = [tokens]
        params = {
            'query': '{job="shakenfist"}' + ''.join(
                ' |= "%s"' % t for t in tokens),
            'start': str(start_ns),
            'end': str(end_ns),
            'limit': '100',
            'direction': 'backward',
        }
        r = requests.get(
            '%s/loki/api/v1/query_range' % LOKI_BASE_URL,
            params=params, timeout=10)
        r.raise_for_status()
        payload = r.json()
        return payload.get('data', {}).get('result', [])

    def _await_loki_lines(self, tokens, start_ns):
        """Poll Loki until lines matching all tokens arrive.

        Returns the matching streams, or fails the test at the
        eventual-consistency deadline.
        """
        streams = []
        deadline = time.time() + LOKI_ARRIVAL_DEADLINE
        last_error = None
        while time.time() < deadline:
            end_ns = int((time.time() + 5) * 1e9)
            try:
                streams = self._query_loki(tokens, start_ns, end_ns)
            except requests.exceptions.RequestException as e:
                last_error = e
                streams = []

            if streams and any(s.get('values') for s in streams):
                break

            time.sleep(2)

        self.addDetail(
            'loki_streams',
            content.text_content(json.dumps(streams, indent=4, sort_keys=True)))

        self.assertTrue(
            streams and any(s.get('values') for s in streams),
            'No Loki log lines containing %s arrived within '
            '%d seconds (last query error: %s)'
            % (tokens, LOKI_ARRIVAL_DEADLINE, last_error))
        return streams

    def test_logs_reach_loki(self):
        # Bracket the action with a generous window. The CI Loki is fresh
        # per run, so a wide window cannot pick up stale matches for our
        # unique UUID token.
        start_ns = int((time.time() - 60) * 1e9)

        # Creating a network reliably emits operational log lines (and,
        # with LOG_EVENTS_TO_LOKI on by default, 'Added event' lines) that
        # carry the network's UUID -- a token unique to this test run.
        net = self.test_client.allocate_network(
            '192.168.242.0/24', True, True, '%s-net' % self.namespace,
            provide_dns=True)
        self.addDetail(
            'net',
            content.text_content(json.dumps(net, indent=4, sort_keys=True)))
        self._await_networks_ready([net['uuid']])

        # Poll Loki until the network's UUID appears in shipped log lines,
        # or we hit the eventual-consistency deadline.
        streams = self._await_loki_lines(net['uuid'], start_ns)

        # Prove the label contract: the phase-2 shipper tags every stream
        # with {job, daemon, host}. We queried by job already; assert the
        # daemon label is present too.
        labels = streams[0].get('stream', {})
        self.assertIn(
            'daemon', labels,
            'Loki stream is missing the expected "daemon" label: %s' % labels)

    def test_event_extra_with_uuid_reaches_loki(self):
        # Regression test for issue 3573: creating an instance attached to
        # a network logs an 'allocated ip address' audit event whose extra
        # dict carried a raw uuid.UUID (the netdesc network_uuid). The
        # 'Added event' echo for it crashed the log shipper's JSON
        # formatter mid-emit, so the structured record never reached Loki
        # (a ~60 line raw traceback hit syslog instead). Assert the echo
        # now arrives.
        start_ns = int((time.time() - 60) * 1e9)

        net = self.test_client.allocate_network(
            '192.168.243.0/24', True, True, '%s-extra-net' % self.namespace,
            provide_dns=False)
        self.addDetail(
            'net',
            content.text_content(json.dumps(net, indent=4, sort_keys=True)))
        self._await_networks_ready([net['uuid']])

        inst = self.test_client.create_instance(
            'test-event-extra-uuid', 1, 1024,
            [
                {
                    'network_uuid': net['uuid']
                }
            ],
            [
                {
                    'size': 8,
                    'base': 'sf://upload/system/debian-12',
                    'type': 'disk'
                }
            ], None, None)
        self.addDetail(
            'inst',
            content.text_content(json.dumps(inst, indent=4, sort_keys=True)))

        # The event fires synchronously in the create API handler, so we
        # do not need to wait for the instance to boot -- just for the
        # log line to propagate to Loki.
        self._await_loki_lines(
            ['allocated ip address', inst['uuid']], start_ns)
