# Copyright 2026 Michael Still and contributors

"""A refused filter reaches an API client as a 400, not a 500.

UnservableFilterCriteria is an ObjectException, so without a handler of
its own it escaped handle_database_unavailable() and became an
unqualified 500. It is a caller bug which no retry fixes, the same
judgement the database servicer makes when it answers INVALID_ARGUMENT
rather than INTERNAL for it (issue 4947).
"""

from shakenfist import exceptions
from shakenfist.external_api import app as external_api
from shakenfist.external_api import base as api_base
from shakenfist.tests import base


class UnservableFilterStatusTestCase(base.ShakenFistTestCase):
    def _call(self, exc):
        def handler():
            raise exc

        with external_api.app.test_request_context('/artifacts'):
            return api_base.handle_database_unavailable(handler)()

    def test_a_refused_filter_is_a_400(self):
        resp = self._call(exceptions.UnservableFilterCriteria(
            'artifact cannot be filtered on network_uuid'))
        self.assertEqual(400, resp.status_code)
        self.assertIn('network_uuid', resp.get_json()['error'])

    def test_an_outage_is_still_a_503(self):
        # The control: the new arm must not swallow the outage it sits
        # beside.
        resp = self._call(exceptions.DatabaseUnavailable('gone'))
        self.assertEqual(503, resp.status_code)
