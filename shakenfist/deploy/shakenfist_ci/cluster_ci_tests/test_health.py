# Copyright 2019 Michael Still and contributors
"""Functional tests for the sf-api HTTP health endpoints.

These tests hit /livez, /readyz, and /healthz without any authentication
token so they exercise the unauthenticated surface of the API on port 13000.

URL discovery: the harness initialises ``self.system_client`` (a
``shakenfist_client.apiclient.Client``) whose ``base_url`` attribute is the
bare gunicorn URL (e.g. ``http://10.0.0.5:13000``). We append the endpoint
paths directly to that URL — the same pattern used in ``test_api.py`` for
the Flasgger CSS endpoint.

Drain-behaviour coverage: the SIGTERM / API_DRAIN_GRACE drain is covered by
unit tests in ``shakenfist/tests/test_gunicorn_drain.py``. A live end-to-end
assertion (restart sf-api and observe the 503 window) would require stopping a
daemon in the shared CI cluster, which is too invasive for a single test
module. That assertion is tracked for phase 4's rolling-upgrade operator
documentation.
"""

import requests

from shakenfist_ci import base


class TestHealthEndpoints(base.BaseTestCase):
    """Verify sf-api health endpoints are live and unauthenticated."""

    def _health_url(self, path):
        """Return the full URL for a health endpoint path."""
        return f'{self.system_client.base_url}{path}'

    def test_livez_returns_200_ok(self):
        """GET /livez must return 200 with body 'ok' and no auth required."""
        r = requests.get(self._health_url('/livez'))
        self.assertEqual(200, r.status_code,
                         f'/livez returned unexpected status: {r.status_code}')
        self.assertEqual('ok', r.text.strip(),
                         f'/livez body was not "ok": {r.text!r}')

    def test_readyz_returns_200_ready_on_healthy_node(self):
        """GET /readyz must return 200 with body 'ready' on a healthy node.

        The CI cluster's sf-api workers poll sf-database continuously; by the
        time functional tests run the readiness flag should be True. A 503 here
        means sf-database is unreachable from the API node or the checker has
        not yet passed its READINESS_FAIL_THRESHOLD — both are genuine cluster
        health failures.
        """
        r = requests.get(self._health_url('/readyz'))
        self.assertEqual(200, r.status_code,
                         f'/readyz returned unexpected status (sf-api may not '
                         f'be ready): {r.status_code}, body={r.text!r}')
        self.assertEqual('ready', r.text.strip(),
                         f'/readyz body was not "ready": {r.text!r}')

    def test_healthz_alias_matches_readyz(self):
        """GET /healthz must behave identically to /readyz.

        Both should return the same status code and body because /healthz is
        registered as an alias of the Readyz resource.
        """
        r_readyz = requests.get(self._health_url('/readyz'))
        r_healthz = requests.get(self._health_url('/healthz'))
        self.assertEqual(r_readyz.status_code, r_healthz.status_code,
                         f'/healthz status ({r_healthz.status_code}) does not '
                         f'match /readyz status ({r_readyz.status_code})')
        self.assertEqual(r_readyz.text.strip(), r_healthz.text.strip(),
                         f'/healthz body ({r_healthz.text!r}) does not match '
                         f'/readyz body ({r_readyz.text!r})')

    def test_livez_requires_no_auth(self):
        """GET /livez without an Authorization header must not return 401."""
        r = requests.get(self._health_url('/livez'))
        self.assertNotEqual(401, r.status_code,
                            '/livez should not require authentication '
                            f'but returned 401. Body: {r.text!r}')

    def test_readyz_requires_no_auth(self):
        """GET /readyz without an Authorization header must not return 401."""
        r = requests.get(self._health_url('/readyz'))
        self.assertNotEqual(401, r.status_code,
                            '/readyz should not require authentication '
                            f'but returned 401. Body: {r.text!r}')

    def test_healthz_requires_no_auth(self):
        """GET /healthz without an Authorization header must not return 401."""
        r = requests.get(self._health_url('/healthz'))
        self.assertNotEqual(401, r.status_code,
                            '/healthz should not require authentication '
                            f'but returned 401. Body: {r.text!r}')
