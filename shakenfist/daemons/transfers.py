import os
from shakenfist_utilities import logs
import socket

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import blob
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist.util import general as util_general
from shakenfist.util import process as util_process


LOG, _ = logs.setup(__name__)


def transfer_server(name, data):
    log = LOG.with_fields(data).with_fields({'name': name})
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.settimeout(60)
        server.bind((config.NODE_MESH_IP, 0))

        # Update etcd with where we are listening
        data['server_state'] = dbo.STATE_CREATED
        data['port'] = server.getsockname()[1]
        etcd.put('transfer', config.NODE_NAME, name, data)

        log.info('Awaiting transfer connection')
        server.listen()

        try:
            conn, addr = server.accept()
        except socket.timeout:
            log.info('No connection before timeout, aborting')
            return

        log = log.with_fields({'remote_ip': addr[0]})
        log.info('New transfer connection')
        if addr[0] != data['requestor']:
            log.warning('Connection not from %s, aborting'
                        % data['requestor'])
            return

        auth = conn.recv(16).decode('utf-8')
        if auth != data['token']:
            log.warning('Connection with incorrect token, aborting')
            return

        blob_path = blob.Blob.filepath(data['blob_uuid'])
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
        etcd.delete('transfer', config.NODE_NAME, name)
        log.info('Deleted transfer request')


class Monitor(daemon.WorkerPoolDaemon):
    def run(self):
        LOG.info('Starting')

        while not self.exit.is_set():
            try:
                self.reap_workers()

                if not self.exit.is_set():
                    for name, data in etcd.get_all('transfer', config.NODE_NAME):
                        name = name.split('/')[-1]
                        if name not in self.workers:
                            p = util_process.fork(
                                transfer_server, [name, data],
                                '%s-%s' % (daemon.process_name('transfers'), name))
                            self.workers[name] = p
                    self.exit.wait(0.2)
                elif len(self.workers) > 0:
                    LOG.info('Waiting for %d workers to finish'
                             % len(self.workers))
                    self.exit.wait(0.2)
                else:
                    return

            except Exception as e:
                util_general.ignore_exception('transfer worker', e)

        LOG.info('Terminated')
