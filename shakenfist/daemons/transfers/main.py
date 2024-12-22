import os
import socket
import threading

from shakenfist_utilities import logs  # noreorder

from shakenfist import blob
from shakenfist import etcd
from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.util import general as util_general
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


class TransferJob(util_concurrency.Job):
    def __init__(self, name, data):
        super().__init__()
        self.name = name
        self.data = data

    def execute(self):
        etcd.reset_client()
        log = LOG.with_fields(self.data).with_fields({'name': self.name})
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.settimeout(60)
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
                conn.close()

            log.info('Transfer complete, sent %d bytes' % sent_bytes)

        finally:
            etcd.delete('transfer', config.NODE_NAME, self.name)
            log.info('Deleted transfer request')


class Monitor(daemon.WorkerPoolDaemon):
    def _run_inner(self):
        # Note this while look is different from many of the other daemons
        # because we need to wait for work to terminate before exiting.
        done = False
        while not done:
            try:
                self.reap_workers()

                if not self.exit.is_set():
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
                    self.exit.wait(0.2)

                elif len(self.workers) > 0:
                    LOG.info('Waiting for %d workers to finish'
                             % len(self.workers))

                    for worker in self.workers:
                        worker['object'].exit.set()

                    self.reap_workers()
                    self.exit.wait(0.2)

                else:
                    done = True

            except Exception as e:
                util_general.ignore_exception('transfer worker', e)

            self.check_daemon_state()

        LOG.info('Terminated')
        self.record_exit()


def main():
    m = Monitor('transfers')
    m.run()
