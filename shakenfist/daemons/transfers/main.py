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
            server.settimeout(30)
            server.bind((config.NODE_MESH_IP, 0))

            # Update MariaDB with where we are listening
            mariadb.update_blob_transfer(
                self.transfer.source_node,
                self.transfer.transfer_name,
                server_state=dbo.STATE_CREATED,
                port=server.getsockname()[1])

            log.info('Awaiting transfer connection')
            server.listen()

            try:
                conn, addr = server.accept()
            except socket.timeout:
                log.info('No connection before timeout, aborting')
                return

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


class Monitor(daemon.WorkerPoolDaemon):
    def _run_inner(self):
        while daemon.check_abort_path(self.abort_path):
            while not daemon.health_check_nodelock():
                LOG.info('Waiting for nodelock daemon to be healthy')
                time.sleep(1)
                continue

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

            self.idle(0.2)


def main():
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('transfers')
    m = Monitor('transfers')

    while not daemon.health_check_nodelock():
        LOG.info('Waiting for nodelock daemon to be healthy')
        time.sleep(1)
    LOG.info('nodelock daemon reports healthy')

    m.run()

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    raise SystemExit(0)
