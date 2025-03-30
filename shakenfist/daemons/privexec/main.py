# A deliberately very small python daemon which knows how to execute commands
# as root. It only communicates via a unix domain socket with other SF daemons
# on a single node. The protocol on the unix domain socket is binary serialized
# protobufs.

import os
import signal
import socket
import subprocess
import threading
import time

from google.protobuf.message import DecodeError
import psutil
import setproctitle
from shakenfist_utilities import random      # noreorder
from shakenfist_utilities import logs

from shakenfist.protos import common_pb2
from shakenfist.protos import privexec_pb2


LOG, _ = logs.setup(__name__)
SOCKET_PATH = '/srv/shakenfist/.privexec'
EXIT = threading.Event()


def exit_gracefully(sig, _frame):
    if sig == signal.SIGTERM:
        LOG.info('Received SIGTERM')
        EXIT.set()


signal.signal(signal.SIGTERM, exit_gracefully)


# Mid-range best effort, equivalent to not specifying a value
IO_PRIORITIES = {
    common_pb2.ExecuteRequest.NORMAL: (2, 4),
    common_pb2.ExecuteRequest.LOW: (2, 7),
    common_pb2.ExecuteRequest.HIGH: (2, 0)
}


class PrivExecJob:
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.task_details = None
        self.pid = None

    def _execute(self, request):
        command = request.command
        if request.network_namespace != '':
            command = f'ip netns exec {request.network_namespace} {command}'

        env_variables = {}
        for env_var in request.environment_variables:
            env_variables[env_var.name] = env_var.value
        if not env_variables:
            env_variables = None

        ioclass, iovalue = list(psutil.Process().ionice())
        current_iopriority = (int(ioclass), int(iovalue))
        requested_iopriority = IO_PRIORITIES.get(
            request.io_priority, IO_PRIORITIES[common_pb2.ExecuteRequest.NORMAL])

        if current_iopriority != requested_iopriority:
            command = (f'ionice -c {requested_iopriority[0]} '
                       f'-n {requested_iopriority[1]} {command}')

        working_directory = None
        if request.working_directory != '':
            working_directory = request.working_directory

        start_time = time.time()
        pipe = subprocess.PIPE
        obj = subprocess.Popen(
            command, stdin=pipe, stdout=pipe, stderr=pipe, close_fds=True,
            shell=True, cwd=working_directory, env=env_variables)
        self.pid = obj.pid

        stdout, stderr = obj.communicate(None, timeout=None)
        obj.stdin.close()
        exit_code = obj.returncode

        duration = round(time.time() - start_time, 2)
        LOG.with_fields({
            'request_id': request.request_id,
            'execution_id': request.execution_id,
            'command': command,
            'working_directory': working_directory,
            'environment_variables': env_variables,
            'current_io_priority': current_iopriority,
            'requested_io_priority': requested_iopriority,
            'exit_code': exit_code,
            'duration': duration
        }).debug('Executed command')

        return privexec_pb2.PrivExecReply(
            execute_reply=common_pb2.ExecuteReply(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                request_id=request.request_id,
                execution_id=request.execution_id,
                execution_seconds=duration
            )
        )

    def run(self):
        buffered = bytearray()

        while True:
            input = self.conn.recv(102400)
            if not input:
                break
            buffered += input

            try:
                request = privexec_pb2.PrivExecRequest()
                consumed = request.ParseFromString(buffered)
                if consumed == 0:
                    continue
                buffered = buffered[consumed:]

                if request.HasField('execute_request'):
                    er = request.execute_request
                    self.task_details = f'execute: {er.command}'
                    reply = self._execute(er)
                    self.conn.sendall(reply.SerializeToString())
                else:
                    LOG.error('Unknown execute request type')
                break

            except DecodeError:
                ...

        self.conn.close()


def write_pid_file():
    with open('/run/sf/privexec.pid', 'w') as f:
        f.write(f'{os.getpid()}')


def main():
    write_pid_file()
    setproctitle.setproctitle('sf-privexec')

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCKET_PATH)
    s.listen(1)
    s.settimeout(0.2)
    LOG.info('Listening for incoming requests')

    workers = {}
    while not EXIT.is_set():
        try:
            conn, _ = s.accept()
        except socket.timeout:
            conn = None

        if conn:
            thread_name = random.random_id()
            worker_object = PrivExecJob(conn)
            worker_thread = threading.Thread(
                target=worker_object.run, daemon=True, name=thread_name)
            workers[thread_name] = {
                'object': worker_object,
                'thread': worker_thread
            }
            worker_thread.start()

        remaining_workers = {}
        for thread_name in workers:
            if workers[thread_name]['thread'].is_alive():
                remaining_workers[thread_name] = workers[thread_name]
            else:
                thread_ident = workers[thread_name]['thread'].ident
                workers[thread_name]['thread'].join(0.2)
        workers = remaining_workers

    LOG.info('Stopping')

    start_time = time.time()
    while workers:
        LOG.info(f'There are {len(workers)} remaining workers')

        remaining_workers = {}
        for thread_name in workers:
            if workers[thread_name]['thread'].is_alive():
                remaining_workers[thread_name] = workers[thread_name]
                LOG.with_fields({
                    'thread_name': thread_name,
                    'task': workers[thread_name]['object'].task_info
                }).info('Thread is still executing')

                pid = workers[thread_name]['object'].pid
                if time.time() - start_time > 30 and pid:
                    os.kill(pid)
                    LOG.with_fields({
                        'thread_name': thread_name,
                        'task': workers[thread_name]['object'].task_info
                    }).info('Associated PID sent kill signal')
            else:
                thread_ident = workers[thread_name]['thread'].ident
                LOG.with_fields({
                    'thread_name': thread_name,
                    'thread_ident': thread_ident
                }).info('Reaping thread')
                workers[thread_name]['thread'].join(0.2)

        workers = remaining_workers
        if workers:
            time.sleep(5)

    LOG.info(f'There are {len(workers)} remaining workers')
    LOG.info('Stopped')

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    LOG.info('Terminating ourselves')
    raise SystemExit(0)
