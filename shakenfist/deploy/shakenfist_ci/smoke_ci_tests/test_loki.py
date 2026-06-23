import json
import time

import requests
from testtools import content

from shakenfist_ci import base


# Where the functional tests run (the inner primary node) Loki is reachable
# on localhost. The install helper (tools/ci-install-loki.sh) binds Loki to
# 0.0.0.0:3100, and getsf renders SHAKENFIST_LOKI_BASE_URL into every node's
# config so all daemons ship to it.
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

    def _query_loki(self, token, start_ns, end_ns):
        """Query Loki for log lines containing token. Returns the streams."""
        params = {
            'query': '{job="shakenfist"} |= "%s"' % token,
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

        token = net['uuid']

        # Poll Loki until the network's UUID appears in shipped log lines,
        # or we hit the eventual-consistency deadline.
        streams = []
        deadline = time.time() + LOKI_ARRIVAL_DEADLINE
        last_error = None
        while time.time() < deadline:
            end_ns = int((time.time() + 5) * 1e9)
            try:
                streams = self._query_loki(token, start_ns, end_ns)
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
            'No Loki log lines containing network UUID %s arrived within '
            '%d seconds (last query error: %s)'
            % (token, LOKI_ARRIVAL_DEADLINE, last_error))

        # Prove the label contract: the phase-2 shipper tags every stream
        # with {job, daemon, host}. We queried by job already; assert the
        # daemon label is present too.
        labels = streams[0].get('stream', {})
        self.assertIn(
            'daemon', labels,
            'Loki stream is missing the expected "daemon" label: %s' % labels)
