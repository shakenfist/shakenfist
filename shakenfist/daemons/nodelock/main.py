# Shaken Fist originally used local file locks using fnctl ala fasteners but...
# they suck. They leak lock files all over the place, and there isn't an easy
# way to tell who is holding the lock. Instead, this is a simple daemon which
# implements machine-local locking without using a huge number of files. The
# alernative would be to use etcd for distributed locks, but given these are
# just for operations within a single node that would be expensive.

import os
import signal
import socket
import threading

from google.protobuf.message import DecodeError
import setproctitle
from shakenfist_utilities import logs

from shakenfist.daemons.daemon import send_systemd_ready
from shakenfist.protos import nodelock_pb2
from shakenfist.util import exceptions as util_exceptions


LOG, _ = logs.setup(__name__)
SOCKET_PATH = '/srv/shakenfist/.nodelock'
EXIT = threading.Event()


def exit_gracefully(sig, _frame):
    if sig == signal.SIGTERM:
        LOG.info('Received SIGTERM')
        EXIT.set()


signal.signal(signal.SIGTERM, exit_gracefully)


def write_pid_file():
    with open('/run/sf/nodelock.pid', 'w') as f:
        f.write(f'{os.getpid()}')


def main():
    util_exceptions.install_exception_tracking()
    write_pid_file()
    setproctitle.setproctitle('sf-nodelock')

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    locks = {}

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCKET_PATH)
    s.listen(1)
    s.settimeout(0.2)
    LOG.info('Listening for incoming requests')
    send_systemd_ready()

    while not EXIT.is_set():
        try:
            conn, _ = s.accept()
        except socket.timeout:
            conn = None

        if conn:
            buffered = bytearray()

            while True:
                input = conn.recv(102400)
                if not input:
                    break
                buffered += input

                try:
                    request = nodelock_pb2.NodeLockRequest()
                    consumed = request.ParseFromString(buffered)
                    if consumed == 0:
                        continue
                    buffered = buffered[consumed:]

                    if request.HasField('lock_request'):
                        lr = request.lock_request

                        if lr.key not in locks:
                            locks[lr.key] = lr.requester
                            outcome = nodelock_pb2.LockReply.OK
                        elif locks[lr.key] == lr.requester:
                            outcome = nodelock_pb2.LockReply.ALREADY_HELD
                        else:
                            outcome = nodelock_pb2.LockReply.DENIED

                        reply = nodelock_pb2.NodeLockReply(
                            lock_reply=nodelock_pb2.LockReply(
                                outcome=outcome
                            )
                        )

                        conn.sendall(reply.SerializeToString())

                    elif request.HasField('unlock_request'):
                        lr = request.unlock_request

                        if lr.key in locks and locks[lr.key] == lr.requester:
                            del locks[lr.key]
                            outcome = nodelock_pb2.UnlockReply.OK
                        else:
                            outcome = nodelock_pb2.UnlockReply.NOT_HELD

                        reply = nodelock_pb2.NodeLockReply(
                            unlock_reply=nodelock_pb2.UnlockReply(
                                outcome=outcome
                            )
                        )
                        conn.sendall(reply.SerializeToString())

                    else:
                        LOG.error('Unknown nodelock request type')

                except DecodeError:
                    ...

            conn.close()

    LOG.info('Stopped')

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    raise SystemExit(0)
