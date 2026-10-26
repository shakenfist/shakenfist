# Copyright 2026 Michael Still and contributors

"""Every per-object events endpoint reads limit the way it publishes it.

The validation layer is check-only, so a limit the schema accepts reaches
the handler as the caller sent it. The shipped client sends it as a JSON
string ("500"), which the schema's integer check accepts, and the events
endpoints passed that string on to a comparison with an int: every one of
them answered 500 whenever limit was given (#4627, and #3609 before it).

object_events_response() now reads limit for all of them. This file sends
each endpoint the same limit as a JSON integer and as a string, and
requires both to reach the database read as the same int. The endpoints
are derived from the declarations, so an events endpoint added later is
swept or fails here until it is.
"""

import json
from unittest import mock

from shakenfist.external_api import base as api_base
from shakenfist.external_api import declarations
from shakenfist.namespace_claim import NamespaceClaim
from shakenfist.tests.external_api.test_required_sweep import (
    SweepFixtureTestCase)


# Each endpoint which declares an integer body limit, and where to send
# it. {claim} is the claim setUp creates; the rest are
# SweepFixtureTestCase's fixtures.
EVENTS_URLS = {
    'ArtifactEventsEndpoint': '/artifacts/{artifact}/events',
    'AuthNamespaceClaimEventsEndpoint':
        '/auth/namespaces/scratch/claims/{claim}/events',
    'AuthNamespaceEventsEndpoint': '/auth/namespaces/scratch/events',
    'BlobEventsEndpoint': '/blobs/{blob}/events',
    'InstanceEventsEndpoint': '/instances/{instance}/events',
    'NetworkEventsEndpoint': '/networks/{network}/events',
    'NodeEventsEndpoint': '/nodes/{node}/events',
}


def events_limit_declarations():
    """The (class, method) of every handler declaring an integer body limit."""
    rows = []
    for _, _, cls, fn in declarations.handlers():
        for dec in declarations.declarations(fn, cls=cls.name):
            if (dec.name == 'limit' and dec.location == 'body' and
                    dec.argtype == 'integer'):
                rows.append((cls.name, fn.name))
    return sorted(rows)


class EventsLimitTestCase(SweepFixtureTestCase):
    mode = 'enforce'

    def setUp(self):
        super().setUp()
        self.fixtures['claim'] = str(NamespaceClaim.new(
            'scratch', 1, 1024, 10, 3600).uuid)

    def _limit_read(self, url, limit):
        """Send limit to url, returning (status, the limit the read got)."""
        with mock.patch.object(api_base.mariadb, 'get_object_events',
                               return_value=[]) as read:
            status, recorded, response = self._send(
                'get', {'url': url, 'body': {'limit': limit}})
        self.assertFalse(recorded, response.get_data(as_text=True))
        if not read.call_args_list:
            return status, 'not called'
        return status, read.call_args.kwargs['limit']

    def test_every_events_endpoint_is_swept(self):
        self.assertEqual(
            sorted((cls, 'get') for cls in EVENTS_URLS),
            events_limit_declarations())

    def test_a_string_limit_is_read_as_the_integer(self):
        for cls, url in sorted(EVENTS_URLS.items()):
            with self.subTest(cls):
                # The JSON integer is the control: it must reach the read,
                # or the string spelling below would be compared with a
                # request which never got that far.
                self.assertEqual((200, 5), self._limit_read(url, 5))

                status, limit = self._limit_read(url, '5')
                self.assertEqual(200, status)
                self.assertEqual(5, limit)
                self.assertIs(int, type(limit))

    def test_no_limit_reads_the_default(self):
        status, limit = self._limit_read('/instances/{instance}/events', None)
        self.assertEqual((200, 100), (status, limit))


class ObjectEventsResponseTestCase(SweepFixtureTestCase):
    """object_events_response() with validation off, where nothing checked limit first."""

    mode = 'off'

    def test_a_limit_which_is_not_an_integer_is_a_400(self):
        with mock.patch.object(api_base.mariadb, 'get_object_events',
                               return_value=[]) as read:
            status, recorded, response = self._send(
                'get', {'url': '/instances/{instance}/events',
                        'body': {'limit': 'lots'}})
        self.assertEqual(400, status, response.get_data(as_text=True))
        self.assertFalse(recorded)
        self.assertEqual('limit is not an integer',
                         json.loads(response.get_data())['error'])
        read.assert_not_called()
