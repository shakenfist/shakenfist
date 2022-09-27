import multiprocessing
import os
from shakenfist_utilities import logs
import socket

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


def transfer_server(name, data):
    log = LOG.with_fields(data).with_fields({'name': name})
    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.settimeout(60)
        server.bind((config.NODE_MESH_IP, data['port']))
        log.info('Awaiting transfer connection')
        server.listen()

        conn, addr = server.accept()
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

        blob_path = os.path.join(
            config.STORAGE_PATH, 'blobs', data['blob_uuid'])
        if not os.path.exists(blob_path):
            log.warning('Blob is missing, aborting')
            return

        with open(blob_path, 'rb') as f:
            d = f.read(8000)
            while d:
                conn.send(d)
                d = f.read(8000)
            conn.close()

        log.info('Transfer complete')

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
                    missing = []
                    with etcd.ThreadLocalReadOnlyCache():
                        for name, data in etcd.get_all('transfer', config.NODE_NAME):
                            name = name.split('/')[-1]
                            if name not in self.workers:
                                missing.append((name, data))

                    for name, data in missing:
                        p = multiprocessing.Process(
                            target=transfer_server, args=(name, data),
                            name='%s-%s' % (daemon.process_name('transfers'), name))
                        p.start()
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
