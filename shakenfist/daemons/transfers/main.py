import os
import socket
import threading
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist import blob
from shakenfist import etcd
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions


LOG, _ = logs.setup(__name__)


class TransferJob(util_concurrency.Job):
    def __init__(self, name, data):
        super().__init__()
        self.name = name
        self.data = data

        self.abort_path = f'/run/sf/transfers-{name}.abort'
        daemon.clear_abort_path(self.abort_path)

    def execute(self):
        etcd.reset_client()
        log = LOG.with_fields(self.data).with_fields({'name': self.name})
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.settimeout(30)
            server.bind((config.NODE_MESH_IP, 0))

            # Update etcd with where we are listening
            self.data['server_state'] = dbo.STATE_CREATED
            self.data['port'] = server.getsockname()[1]
            etcd.put('transfer', config.NODE_NAME, self.name, self.data)

            log.info('Awaiting transfer connection')
            server.listen()

            try:
                conn, addr = server.accept()
            except socket.timeout:
                log.info('No connection before timeout, aborting')
                return

            log = log.with_fields({'remote_ip': addr[0]})
            log.info('New transfer connection')
            if addr[0] != self.data['requestor']:
                log.warning('Connection not from %s, aborting'
                            % self.data['requestor'])
                return

            auth = conn.recv(16).decode('utf-8')
            if auth != self.data['token']:
                log.warning('Connection with incorrect token, aborting')
                return

            blob_path = blob.Blob.filepath(self.data['blob_uuid'])
            if not os.path.exists(blob_path):
                log.warning('Blob is missing, aborting')
                return

            st = os.stat(blob_path)
            if st.st_size == 0:
                log.warning('Blob is empty, aborting')
                return

            sent_bytes = 0
            with open(blob_path, 'rb') as f:
                while d := f.read(8000):
                    conn.send(d)
                    sent_bytes += len(d)

                    if os.path.exists(self.abort_path):
                        break
                conn.close()

            log.info(f'Transfer complete or aborted, sent {sent_bytes} bytes')

        finally:
            etcd.delete('transfer', config.NODE_NAME, self.name)
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

                for name, data in etcd.get_all('transfer', config.NODE_NAME):
                    name = name.split('/')[-1]
                    if name not in self.workers:
                        t_obj = TransferJob(name, data)
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
