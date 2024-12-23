# A deliberately very small python daemon which knows how to execute commands
# as root. It only communicates via a unix domain socket with other SF daemons
# on a single node. The protocol on the unix domain socket is binary serialized
# protobufs.

import os
import signal
import socket
import threading
import time

from shakenfist_utilities import random      # noreorder

from google.protobuf.message import DecodeError
import psutil
from oslo_concurrency import processutils
from shakenfist_utilities import logs

from shakenfist import privexec_pb2


LOG, _ = logs.setup(__name__)
SOCKET_PATH = '/srv/shakenfist/.privexec'
STOPPED = threading.Event()


def exit_gracefully(sig, _frame):
    global STOPPED
    if sig == signal.SIGTERM:
        LOG.info('Caught SIGTERM, terminating')
        STOPPED.set()


signal.signal(signal.SIGTERM, exit_gracefully)


# Mid-range best effort, equivalent to not specifying a value
IO_PRIORITIES = {
    privexec_pb2.ExecuteRequest.NORMAL: (2, 4),
    privexec_pb2.ExecuteRequest.LOW: (2, 7),
    privexec_pb2.ExecuteRequest.HIGH: (2, 0)
}


def execute(request):
    global IO_PRIORITIES

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
        request.io_priority, IO_PRIORITIES[privexec_pb2.ExecuteRequest.NORMAL])

    if current_iopriority != requested_iopriority:
        command = (f'ionice -c {requested_iopriority[0]} '
                   f'-n {requested_iopriority[1]} {command}')

    working_directory = None
    if request.working_directory != '':
        working_directory = request.working_directory

    LOG.with_fields({
        'request_id': request.request_id,
        'execution_id': request.execution_id,
        'command': command,
        'working_directory': working_directory,
        'environment_variables': env_variables,
        'current_io_priority': current_iopriority,
        'requested_io_priority': requested_iopriority
    }).debug('Executing command')

    start_time = time.time()
    exit_code = 0
    try:
        stdout, stderr = processutils.execute(
            command, env_variables=env_variables, shell=True,
            cwd=working_directory, check_exit_code=[0])
    except processutils.ProcessExecutionError as e:
        exit_code = e.exit_code
        stdout = e.stdout
        stderr = e.stderr
    except FileNotFoundError as e:
        exit_code = -1
        stdout = None
        stderr = str(e)

    duration = round(time.time() - start_time, 2)
    LOG.with_fields({
        'request_id': request.request_id,
        'execution_id': request.execution_id,
        'exit_code': exit_code,
        'duration': duration
    }).debug('Executed command')

    return privexec_pb2.Reply(
        execute_reply=privexec_pb2.ExecuteReply(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            request_id=request.request_id,
            execution_id=request.execution_id,
            execution_seconds=duration
        )
    )


class PrivExecJob:
    def __init__(self, conn):
        super().__init__()
        self.conn = conn

    def run(self):
        buffered = bytearray()

        while True:
            input = self.conn.recv(102400)
            if not input:
                break
            buffered += input

            try:
                request = privexec_pb2.Request()
                consumed = request.ParseFromString(buffered)
                if consumed == 0:
                    continue

                if request.HasField('execute_request'):
                    reply = execute(request.execute_request)
                    self.conn.sendall(reply.SerializeToString())
                else:
                    LOG.error('Unknown execute request type')
                break

            except DecodeError as e:
                ...

        self.conn.close()


def main():
    global LOG
    global SOCKET_PATH
    global STOPPED

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCKET_PATH)
    s.listen(1)
    s.settimeout(0.2)
    LOG.info('Listening for incoming requests')

    workers = {}
    while not STOPPED.set():
        try:
            conn, _ = s.accept()
        except socket.timeout:
            conn = None

        if conn:
            thread_name = random.random_id()
            LOG.with_fields({
                'thread_name': thread_name
            }).info('Accepted incoming request')

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
                LOG.with_fields({
                    'thread_name': thread_name,
                    'thread_ident': thread_ident
                }).info('Reaping thread.')
                workers[thread_name]['thread'].join(0.2)
        workers = remaining_workers
