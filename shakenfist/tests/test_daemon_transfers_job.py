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
                mock.patch('shakenfist.daemons.transfers.main.config'):
            mock_mdb.update_blob_transfer.side_effect = \
                lambda *args, **kwargs: order.append('update_blob_transfer')
            job.execute()

        self.assertEqual(['listen', 'update_blob_transfer'], order)
        mock_mdb.delete_blob_transfer.assert_called_once_with('sf-2', 't1')
