# Copyright 2026 Michael Still and contributors

"""The stray-lock sweep reaps, instead of narrating forever.

A lock whose holder process is gone was warned about once and then
reported at ERROR on every later pass, because the escalation recorded
the time of its first warning and never updated it. One lock left on
sf-3 by an sf-queues restart taken mid-replication produced 1,567 ERROR
lines in 13 hours, and nothing in the loop ever cleared the row: the
reap in the startup tasks had already had its one chance (#3432).
"""

from unittest import mock

from shakenfist.daemons.queues import main
from shakenfist.tests import base


THIS_NODE = 'sf-3'
DEAD_PID = 1117019
LIVE_PID = 1375716


def _holder(pid, node=THIS_NODE):
    """A holder_json as ClusterLock writes it."""
    return {'node': node, 'pid': pid, 'thread': 140234, 'line': 'blob.py:1',
            'operation': 'Request replication', 'id': 'by4r9AZtbm2yaUBv'}


class StrayLockSweepTestCase(base.ShakenFistTestCase):
    def setUp(self):
        super().setUp()
        # The sweep reads no instance state, and Monitor.__init__ is a
        # daemon constructor.
        self.monitor = main.Monitor.__new__(main.Monitor)

    def sweep(self, locks, live_pids):
        with mock.patch.object(main.sf_locks, 'get_existing_locks',
                               return_value=locks), \
                mock.patch.object(main.sf_locks,
                                  'clear_stale_locks') as clear, \
                mock.patch.object(main.psutil, 'pid_exists',
                                  side_effect=lambda p: p in live_pids), \
                mock.patch.object(main, 'config') as config, \
                mock.patch.object(main, 'LOG') as log:
            config.NODE_NAME = THIS_NODE
            reaped = self.monitor._reap_stray_locks()
        return reaped, clear, log

    def test_a_lock_whose_holder_is_gone_is_cleared(self):
        reaped, clear, _ = self.sweep(
            {'blob/replication/b1': _holder(DEAD_PID)}, [LIVE_PID])
        self.assertEqual(['blob/replication/b1'], reaped)
        clear.assert_called_once_with()

    def test_a_live_holder_is_left_alone(self):
        reaped, clear, log = self.sweep(
            {'blob/replication/b1': _holder(LIVE_PID)}, [LIVE_PID])
        self.assertEqual([], reaped)
        clear.assert_not_called()
        self.assertEqual([], log.mock_calls)

    def test_another_nodes_lock_is_not_this_nodes_to_clear(self):
        reaped, clear, _ = self.sweep(
            {'blob/replication/b1': _holder(DEAD_PID, node='sf-6')},
            [LIVE_PID])
        self.assertEqual([], reaped)
        clear.assert_not_called()

    def test_the_holder_pid_does_not_overwrite_the_records_own(self):
        # with_fields(lock_details) wrote the dead holder's pid into the
        # record's pid field, so these two lines meant something
        # different by pid than every other line the daemon logs.
        _, _, log = self.sweep(
            {'blob/replication/b1': _holder(DEAD_PID)}, [LIVE_PID])
        fields = log.with_fields.call_args[0][0]
        self.assertNotIn('pid', fields)
        self.assertEqual(DEAD_PID, fields['holder']['pid'])
        self.assertEqual('blob/replication/b1', fields['lock'])

    def test_every_pass_reaps_rather_than_escalating(self):
        # The old sweep logged one warning and then an ERROR on every
        # pass for as long as the row existed, without ever clearing it.
        locks = {'blob/replication/b1': _holder(DEAD_PID)}
        for _ in range(3):
            _, clear, log = self.sweep(locks, [LIVE_PID])
            clear.assert_called_once_with()
            log.error.assert_not_called()
            log.with_fields.return_value.error.assert_not_called()
