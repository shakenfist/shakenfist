import os
import socket
import threading
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist import blob
from shakenfist import mariadb
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.schema.blob_transfer import BlobTransfer
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions


LOG, _ = logs.setup(__name__)


# How long a transfer waits for its requesting node to connect, and how often
# it looks at its abort path while waiting. The wait used to be one blocking
# accept() with a 30 second timeout, which could not see the abort path at
# all, so a worker waiting for a peer outlived SIGTERM and systemd SIGKILLed
# sf-transfers at its own 30 second TimeoutStopSec (#4524). A SIGKILL also
# skips the finally below, leaving the transfer row behind.
ACCEPT_TIMEOUT_SECONDS = 30
ACCEPT_POLL_SECONDS = 1


class TransferJob(util_concurrency.Job):
    def __init__(self, transfer: BlobTransfer):
        super().__init__()
        self.transfer = transfer

        self.abort_path = f'/run/sf/transfers-{transfer.transfer_name}.abort'
        daemon.clear_abort_path(self.abort_path)

    def execute(self):
        log = LOG.with_fields(self.transfer.external_view())
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.settimeout(ACCEPT_POLL_SECONDS)
            server.bind((config.NODE_MESH_IP, 0))

            # Listen before publishing the port: the requesting node connects
            # as soon as it sees the CREATED row, and a SYN arriving at a
            # socket that is bound but not yet listening is refused. Once we
            # are listening the kernel queues the connection until accept().
            # See issue #4515.
            server.listen()

            # Update MariaDB with where we are listening
            mariadb.update_blob_transfer(
                self.transfer.source_node,
                self.transfer.transfer_name,
                server_state=dbo.STATE_CREATED,
                port=server.getsockname()[1])

            log.info('Awaiting transfer connection')

            accepted = self._accept(server, log)
            if not accepted:
                return
            conn, addr = accepted

            log = log.with_fields({'remote_ip': addr[0]})
            log.info('New transfer connection')
            if addr[0] != self.transfer.requesting_node:
                log.warning('Connection not from %s, aborting'
                            % self.transfer.requesting_node)
                return

            auth = conn.recv(64).decode('utf-8')
            if auth != self.transfer.token:
                log.warning('Connection with incorrect token, aborting')
                return

            blob_path = blob.Blob.filepath(self.transfer.blob_uuid)
            if not os.path.exists(blob_path):
                log.warning('Blob is missing, aborting')
                return

            st = os.stat(blob_path)
            if st.st_size == 0:
                log.warning('Blob is empty, aborting')
                return

            sent_bytes = 0
            blob_size = st.st_size
            next_percentage_update = 10
            with open(blob_path, 'rb') as f:
                while d := f.read(8000):
                    conn.send(d)
                    sent_bytes += len(d)

                    # Update progress percentage
                    percentage = (sent_bytes / blob_size) * 100.0
                    if percentage >= next_percentage_update:
                        mariadb.update_blob_transfer(
                            self.transfer.source_node,
                            self.transfer.transfer_name,
                            percentage=percentage)
                        next_percentage_update += 10

                    if os.path.exists(self.abort_path):
                        break
                conn.close()

            log.info(f'Transfer complete or aborted, sent {sent_bytes} bytes')

        finally:
            mariadb.delete_blob_transfer(
                self.transfer.source_node, self.transfer.transfer_name)
            log.info('Deleted transfer request')

    def _accept(self, server, log):
        """Wait for the peer's connection, or None on a timeout or an abort."""
        deadline = time.monotonic() + ACCEPT_TIMEOUT_SECONDS
        while True:
            try:
                return server.accept()
            except socket.timeout:
                pass
            if os.path.exists(self.abort_path):
                log.info('Aborted while awaiting a transfer connection')
                return None
            if time.monotonic() >= deadline:
                log.info('No connection before timeout, aborting')
                return None


class Monitor(daemon.WorkerPoolDaemon):
    def _run_inner(self):
        # Adaptive backoff: poll fast while there are transfers to run, back
        # off towards IDLE_POLL_MAX_SECONDS while idle so a node with nothing
        # to transfer stops issuing ~5 GetBlobTransfersForNode/s. See issue
        # #3500.
        poll_backoff = daemon.IdlePollBackoff()

        while daemon.check_abort_path(self.abort_path):
            self.wait_for_nodelock()

            transfers = []
            try:
                self.reap_workers()

                transfers = mariadb.get_blob_transfers_for_node(
                    config.NODE_NAME)
                for transfer in transfers:
                    name = transfer.transfer_name
                    if name not in self.workers:
                        t_obj = TransferJob(transfer)
                        t_thread = threading.Thread(
                            target=t_obj.run, daemon=True, name=name)
                        t_thread.start()

                        self.workers[name] = {
                            'object': t_obj,
                            'thread': t_thread
                        }

            except Exception as e:
                util_exceptions.ignore_exception('transfer worker', e)

            if transfers:
                poll_backoff.reset()
                self.idle(daemon.IDLE_POLL_FAST_SECONDS)
            else:
                self.idle(poll_backoff.next_empty_interval())


def main():
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('transfers')
    m = Monitor('transfers')

    while not daemon.health_check_nodelock():
        LOG.info('Waiting for nodelock daemon to be healthy')
        time.sleep(1)
    LOG.info('nodelock daemon reports healthy')

    m.run()

    daemon.force_clean_exit()
