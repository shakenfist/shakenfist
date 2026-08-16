import json
import re
import time

import requests
from testtools import content

from shakenfist_ci import base


# The shape of a key secret the cluster mints for itself: sfk_ followed
# by 32 base62 random characters and a 6 character base62 CRC32
# checksum. Matched on shape alone, because neither a log query nor a
# repository scanner can compute the checksum. Kept identical to the
# expression in .gitleaks.toml and examples/loki-secret-alert.yaml --
# three copies of one pattern is two too many, but they live in three
# different languages, so the binding is a test rather than a constant
# (see shakenfist/tests/test_credentials.py).
#
# Deliberately not '|= "sfk_"'. Several log lines legitimately mention
# the prefix -- key creation refuses an operator secret carrying it,
# and says so -- and a detector which fires on its own documentation
# gets switched off.
SECRET_SHAPE = 'sfk_[A-Za-z0-9]{38}'
SECRET_SHAPE_RE = re.compile(SECRET_SHAPE)

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

    def _query_loki(self, tokens, start_ns, end_ns, regex=None,
                    exclude=None, limit=100):
        """Query Loki for log lines containing all tokens. Returns streams.

        ``regex`` adds a line filter matched by Loki's own engine
        rather than by us, which is what lets a caller assert on the
        same expression an operator's alert rule uses. ``exclude``
        drops lines containing any of the given tokens, which matters
        when the caller is looking for a needle in its own noise: a
        filter Loki applies costs nothing against ``limit``, where
        discarding the same lines in Python spends the result budget
        on them and can push the interesting line off the end.
        """
        if isinstance(tokens, str):
            tokens = [tokens]
        query = '{job="shakenfist"}' + ''.join(' |= "%s"' % t for t in tokens)
        if regex:
            query += ' |~ "%s"' % regex
        for token in (exclude or []):
            query += ' != "%s"' % token
        params = {
            'query': query,
            'start': str(start_ns),
            'end': str(end_ns),
            'limit': str(limit),
            'direction': 'backward',
        }
        r = requests.get(
            '%s/loki/api/v1/query_range' % LOKI_BASE_URL,
            params=params, timeout=10)
        r.raise_for_status()
        payload = r.json()
        return payload.get('data', {}).get('result', [])

    def _await_loki_lines(self, tokens, start_ns, regex=None):
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
                streams = self._query_loki(
                    tokens, start_ns, end_ns, regex=regex)
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

    def _control_token(self):
        """A token of the credential shape which cannot be a credential.

        The scanners match the shape, so a control has to have the
        shape. Making it safe is therefore a question of making it
        impossible for the checksum to be right, rather than merely
        unlikely.

        The trailing six characters are the base62 CRC32 of everything
        before them, so the largest checksum any real secret can carry
        is 0xffffffff, or 4294967295. 'zzzzzz' in base62 is
        56800235583. No input to CRC32 produces it, so a token ending
        that way is refused by credentials.looks_valid() by
        construction and not by luck. Pinned in
        shakenfist/tests/test_credentials.py, which can import the real
        implementation -- this suite deliberately does not, because it
        is a client of the cluster rather than part of it.
        """
        body = ('CIleakcontrol%s' % self._uniquifier()).ljust(32, 'X')
        return 'sfk_%s%s' % (body[:32], 'zzzzzz')

    def _emit_token(self, token, netblock):
        """Get a token into the log stream through an ordinary API call.

        Network creation logs the network's name in the API, the
        network daemon and the event echo, so naming a network after
        the token puts it in front of the shipper the same way a real
        leak would arrive.
        """
        net = self.test_client.allocate_network(
            netblock, True, True, token)
        self._await_networks_ready([net['uuid']])
        return net

    @staticmethod
    def _redact(secret):
        """Enough of a secret to correlate it, not enough to use it."""
        return '%s...' % secret[:8]

    def _matches_in(self, streams):
        """Every credential-shaped token in a query result.

        Returns a list of (token, labels, timestamp). The token is the
        real matched text, because the caller has to compare it against
        the controls; redact with _redact() before it reaches any
        output.
        """
        found = []
        for stream in streams:
            labels = stream.get('stream', {})
            for timestamp, line in stream.get('values', []):
                for match in SECRET_SHAPE_RE.findall(line):
                    found.append((match, labels, timestamp))
        return found

    def test_no_credential_reaches_loki(self):
        """No cluster-minted key secret may appear in the log stream.

        This is the detector phase 7 exists to build. It is worth being
        precise about why it is shaped the way it is: an assertion that
        a query returned nothing is indistinguishable from an assertion
        that the query was malformed, that the log shipper was down, or
        that Loki was empty. Phase 6 emptied six leak guards in exactly
        that way without one of them failing.

        So the test proves it can fire before it asserts that it did
        not. A control token of the credential shape is emitted and
        must be found by the same regex an operator's alert rule uses.
        A second control, emitted after the real credential is minted,
        is the watermark: once Loki has it, everything logged earlier
        has had at least as long to arrive, which is what makes the
        negative assertion sound without an arbitrary sleep.
        """
        start_ns = int((time.time() - 60) * 1e9)

        # 1. Prove the detector fires. If the control never arrives the
        #    failure is the interesting one -- it means this test could
        #    not have caught a leak either.
        control_one = self._control_token()
        self._emit_token(control_one, '192.168.244.0/24')
        self._await_loki_lines(
            [control_one], start_ns, regex=SECRET_SHAPE)

        # 2. Mint a real credential, so the operator key creation path
        #    runs inside the window this test examines. The cluster is
        #    already minting _service_key secrets on every node for its
        #    own inter-node authentication, so a passing result covers
        #    those too -- but that path is not one a test controls, and
        #    a detector should exercise the path a person would use.
        #
        #    Called through _request_url() because the client library
        #    has no wrapper for the generate-a-secret-for-me form (its
        #    add_namespace_key() requires a secret, and the sfk_ prefix
        #    is reserved so no supplied secret can have this shape).
        #    Recorded as Future work on PLAN-auth-federation.md; the
        #    federation tests reach the API the same way.
        key_name = 'leakcheck-%s' % self._uniquifier()
        minted = self.system_client._request_url(
            'POST', '/auth/namespaces/%s/keys' % self.namespace,
            data={'key_name': key_name}).json()
        self.addCleanup(
            self.system_client.delete_namespace_key,
            self.namespace, key_name)

        # Assert on the shape without recording the secret anywhere.
        secret = minted.get('key', '')
        self.assertTrue(
            SECRET_SHAPE_RE.fullmatch(secret),
            'The cluster did not mint a credential of the expected '
            'shape, so this test would not detect one leaking. Got a '
            '%d character response key.' % len(secret))

        # 3. Watermark, and sweep.
        control_two = self._control_token()
        self._emit_token(control_two, '192.168.245.0/24')
        self._await_loki_lines(
            [control_two], start_ns, regex=SECRET_SHAPE)

        #    The controls are excluded by Loki rather than by us. They
        #    are noisy -- a network's name is logged by the API, the
        #    network daemon and the event echo -- and filtering them
        #    here instead would spend the result limit on lines we
        #    already know about, so a genuine leak could fall off the
        #    end of a truncated result and be read as "nothing found".
        end_ns = int((time.time() + 5) * 1e9)
        controls = [control_one, control_two]
        streams = self._query_loki(
            [], start_ns, end_ns, regex=SECRET_SHAPE, exclude=controls)
        leaked = [(self._redact(token), labels, timestamp)
                  for token, labels, timestamp in self._matches_in(streams)
                  if token not in controls]

        # Redacted deliberately: CI output is itself shipped and
        # retained, so a test which printed the credential it found
        # would have moved the leak rather than reported it.
        self.addDetail(
            'credential_shaped_matches',
            content.text_content(json.dumps(
                [{'token': t, 'labels': ls, 'timestamp': ts}
                 for t, ls, ts in leaked],
                indent=4, sort_keys=True)))

        self.assertEqual(
            [], leaked,
            'A cluster-minted credential reached the log stream. Each '
            'entry below is a real key secret shipped off the node, '
            'shown as its first eight characters with the stream '
            'labels and timestamp needed to find it: %s' % leaked)
