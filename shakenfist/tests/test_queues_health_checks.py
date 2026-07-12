# Copyright 2019 Michael Still and contributors

from unittest import mock

from shakenfist.daemons.queues import main as queues_main
from shakenfist.tests import base


class HealthChecksMissingNodeTestCase(base.ShakenFistTestCase):
    """_health_checks() must treat an unreadable node record as unhealthy.

    During cluster startup, or while the database service is unreachable,
    Node.from_db() returns None. That must read as "not healthy yet" rather
    than raising AttributeError into the queue worker's generic exception
    handler (issue 3370).
    """

    @mock.patch('shakenfist.daemons.queues.main.Node.from_db',
                return_value=None)
    def test_missing_node_is_unhealthy(self, mock_from_db):
        self.assertFalse(queues_main._health_checks())
        mock_from_db.assert_called_once_with(
            mock.ANY, suppress_failure_audit=True)

    @mock.patch('shakenfist.daemons.queues.main._check_other_daemon',
                return_value=True)
    @mock.patch('shakenfist.daemons.queues.main.Node.from_db')
    def test_present_node_runs_daemon_checks(
            self, mock_from_db, mock_check):
        mock_from_db.return_value = mock.MagicMock()
        self.assertTrue(queues_main._health_checks())
        self.assertEqual(3, mock_check.call_count)
