# Copyright 2019 Michael Still and contributors
import logging
from unittest import mock

from shakenfist.config import config
from shakenfist.external_api import app as external_api
from shakenfist.tests import base


class _CaptureHandler(logging.Handler):
    """Collect emitted log records for assertion."""

    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class NodeUuidResolutionLoggingTestCase(base.ShakenFistTestCase):
    """An unresolvable node UUID is reported once per worker, not per request.

    resolve_node_uuid runs before every non-probe request, and on a
    worker which cannot resolve its own UUID every one of those
    requests used to emit a warning. That is one line per request
    forever in a misconfigured deployment, and it dominated the unit
    test output: shakenfist.tests.external_api.test_auth alone produced
    over six thousand copies. Resolution is still attempted every
    request -- the node may appear in the database later -- so what is
    asserted here is the log bound, not a bound on the attempts.
    """

    def setUp(self):
        super().setUp()

        self.saved_node_uuid = config.NODE_UUID
        config.NODE_UUID = None
        self.addCleanup(self._restore_node_uuid)

        external_api._node_uuid_warning_emitted = False
        self.addCleanup(
            setattr, external_api, '_node_uuid_warning_emitted', False)

        self.capture = _CaptureHandler()
        logging.getLogger('shakenfist.external_api.app').addHandler(
            self.capture)
        self.addCleanup(
            logging.getLogger('shakenfist.external_api.app').removeHandler,
            self.capture)

        self.client = external_api.app.test_client()

    def _restore_node_uuid(self):
        config.NODE_UUID = self.saved_node_uuid

    def _warnings(self):
        return [r for r in self.capture.records
                if r.getMessage() == 'Failed to resolve node UUID in API worker']

    def test_unresolvable_node_warns_once_across_many_requests(self):
        with mock.patch.object(external_api.Node, '_load_persisted_uuid',
                               return_value=None), \
                mock.patch.object(external_api.Node, 'from_db',
                                  return_value=None) as mock_from_db:
            for _ in range(20):
                self.client.get('/instances')

        # One warning, but every request still tried to resolve.
        self.assertEqual(1, len(self._warnings()))
        self.assertEqual(20, mock_from_db.call_count)

    def test_resolution_success_is_still_logged(self):
        with mock.patch.object(external_api.Node, '_load_persisted_uuid',
                               return_value='a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'):
            self.client.get('/instances')

        self.assertEqual([], self._warnings())
        self.assertEqual(
            'a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d', config.NODE_UUID)
