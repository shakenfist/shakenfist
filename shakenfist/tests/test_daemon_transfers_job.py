# Copyright 2026 Michael Still and contributors

import socket
from unittest import mock

from shakenfist.daemons.transfers import main as transfers_main
from shakenfist.tests import base


class TransferJobOrderingTestCase(base.ShakenFistTestCase):
    def test_listen_before_publishing_created(self):
        # The requesting node polls get_blob_transfer and connects the moment
        # it sees the CREATED row, so the server must already be listening
        # when that row is written. A socket which is bound but not yet
        # listening refuses the connection, and the client's four retries all
        # land in the same window. See issue #4515.
        transfer = mock.MagicMock()
        transfer.transfer_name = 't1'
        transfer.source_node = 'sf-2'
        transfer.external_view.return_value = {}

        # Construct a TransferJob without running __init__ (which touches the
        # filesystem via clear_abort_path). execute() only reads the
        # attributes set below.
        job = transfers_main.TransferJob.__new__(transfers_main.TransferJob)
        job.transfer = transfer
        job.abort_path = '/run/sf/_test.abort'

        order = []
        server = mock.MagicMock()
        server.getsockname.return_value = ('192.0.2.1', 12345)
        server.listen.side_effect = lambda *args: order.append('listen')
        # End execute() at accept(): the ordering under test is before it.
        server.accept.side_effect = socket.timeout

        with mock.patch(
                'shakenfist.daemons.transfers.main.socket.socket',
                return_value=server), \
                mock.patch(
                    'shakenfist.daemons.transfers.main.mariadb') as mock_mdb, \
                mock.patch('shakenfist.daemons.transfers.main.config'), \
                mock.patch.object(transfers_main, 'ACCEPT_TIMEOUT_SECONDS', 0):
            mock_mdb.update_blob_transfer.side_effect = \
                lambda *args, **kwargs: order.append('update_blob_transfer')
            job.execute()

        self.assertEqual(['listen', 'update_blob_transfer'], order)
        mock_mdb.delete_blob_transfer.assert_called_once_with('sf-2', 't1')


def _job():
    transfer = mock.MagicMock()
    transfer.transfer_name = 't1'
    transfer.source_node = 'sf-2'
    job = transfers_main.TransferJob.__new__(transfers_main.TransferJob)
    job.transfer = transfer
    job.abort_path = '/run/sf/_test.abort'
    return job


class TransferJobAcceptTestCase(base.ShakenFistTestCase):
    """The wait for the peer must see the abort path.

    It used to be one blocking accept() with a 30 second timeout, so a worker
    waiting for its peer outlived SIGTERM and systemd SIGKILLed sf-transfers
    at its 30 second stop timeout, skipping the transfer row cleanup (#4524).
    """

    def _accept(self, accept_effects, aborted=False, clock=None):
        server = mock.MagicMock()
        server.accept.side_effect = accept_effects
        with mock.patch('shakenfist.daemons.transfers.main.os.path.exists',
                        side_effect=lambda path: aborted and path == '/run/sf/_test.abort'), \
                mock.patch('shakenfist.daemons.transfers.main.time.monotonic',
                           side_effect=clock or (lambda: 0)):
            result = _job()._accept(server, mock.MagicMock())
        return result, server

    def test_an_abort_ends_the_wait_at_the_next_poll(self):
        # The clock never moves, so only the abort path can end this wait.
        result, server = self._accept([socket.timeout] * 3, aborted=True)
        self.assertIsNone(result)
        self.assertEqual(1, server.accept.call_count)

    def test_the_wait_still_ends_at_its_deadline(self):
        clock = iter([0, 10, 20, transfers_main.ACCEPT_TIMEOUT_SECONDS])
        result, server = self._accept([socket.timeout] * 5, clock=lambda: next(clock))
        self.assertIsNone(result)
        self.assertEqual(3, server.accept.call_count)

    def test_a_connection_after_some_polls_is_returned(self):
        conn = (mock.MagicMock(), ('192.0.2.2', 4000))
        result, server = self._accept([socket.timeout, socket.timeout, conn])
        self.assertEqual(conn, result)

    def test_each_poll_is_short_enough_to_fit_the_stop_timeout(self):
        # systemd's TimeoutStopSec is 30s and the pool daemon polls its
        # workers every 5s, so a poll has to be a small fraction of that.
        self.assertLessEqual(transfers_main.ACCEPT_POLL_SECONDS, 5)
        self.assertLess(transfers_main.ACCEPT_POLL_SECONDS, transfers_main.ACCEPT_TIMEOUT_SECONDS)
