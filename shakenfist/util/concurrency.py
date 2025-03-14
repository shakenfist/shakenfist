import flask
import json
import os
import socket
import threading
import time

from google.protobuf.message import DecodeError
from shakenfist_utilities import logs                     # noreorder
from shakenfist_utilities import random as sf_random      # noreorder

from shakenfist import etcd
from shakenfist.exceptions import MissingNodeLockSocket
from shakenfist.exceptions import MissingPrivExecSocket
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.exceptions import TruncatedNodeLockResponse
from shakenfist.exceptions import TruncatedPrivExecResponse
from shakenfist.exceptions import UnknownNodeLockReplyException
from shakenfist.exceptions import UnknownPrivExecReplyException
from shakenfist.protos import common_pb2
from shakenfist.protos import nodelock_pb2
from shakenfist.protos import privexec_pb2
from shakenfist.util import callstack as util_callstack
from shakenfist.util import general as util_general
# To avoid circular imports, util modules should only import a limited
# set of shakenfist modules, mainly exceptions, and specific
# other util modules.


LOG, _ = logs.setup(__name__)
PRIVEXEC_SOCKET_PATH = '/srv/shakenfist/.privexec'
NODELOCK_SOCKET_PATH = '/srv/shakenfist/.nodelock'


class Job:
    def __init__(self):
        self.exit = threading.Event()

    def run(self):
        LOG.debug('Starting job execution')
        self.execute()
        LOG.debug('Finished job execution')


def _is_gunicorn():
    return 'gunicorn' in os.environ.get('SERVER_SOFTWARE', '')


def _log_results(**kwargs):
    truncated = False
    if len(kwargs['stdout']) > 512:
        kwargs['stdout'] = kwargs['stdout'][:512] + '...'
        truncated = True
    if len(kwargs['stderr']) > 512:
        kwargs['stderr'] = kwargs['stderr'][:512] + '...'
        truncated = True

    if not truncated:
        LOG.with_fields(kwargs).debug('Command output')
    else:
        LOG.with_fields(kwargs).debug('Command output (truncated)')


PRIORITY_NORMAL = common_pb2.ExecuteRequest.NORMAL
PRIORITY_LOW = common_pb2.ExecuteRequest.LOW
PRIORITY_HIGH = common_pb2.ExecuteRequest.HIGH


def execute(locks, command, check_exit_code=[0], env_variables=None,
            namespace=None, iopriority=None, cwd=None,
            suppress_command_logging=False):
    try:
        request_id = flask.request.environ.get('FLASK_REQUEST_ID')
    except RuntimeError:
        request_id = None

    execution_id = sf_random.random_id()
    request = privexec_pb2.PrivExecRequest(
        execute_request=common_pb2.ExecuteRequest(
            command=command,
            network_namespace=namespace,
            io_priority=iopriority,
            working_directory=cwd,
            request_id=request_id,
            execution_id=execution_id
        )
    )

    if env_variables:
        for env_var in env_variables:
            ev = request.execute_request.environment_variables.add()
            ev.name = env_var
            ev.value = env_variables[env_var]
    else:
        env_variables = {}

    if 'PATH' not in env_variables:
        ev = request.execute_request.environment_variables.add()
        ev.name = 'PATH'
        ev.value = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'

    if not suppress_command_logging:
        LOG.with_fields({
            'command': command,
            'namespace': namespace,
            'iopriority': iopriority,
            'environment_variables': env_variables,
            'working_directory': cwd,
            'request_id': request_id,
            'execution_id': execution_id
        }).info('Executing command')

    if _is_gunicorn() and locks:
        caller = util_callstack.generate_traceback()
        LOG.warning(
            f'Lock refreshers should not be used under gunicorn: {caller}')

    if not os.path.exists(PRIVEXEC_SOCKET_PATH):
        raise MissingPrivExecSocket()

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(PRIVEXEC_SOCKET_PATH)

    try:
        client.sendall(request.SerializeToString())

        buffered = bytearray()
        last_refresh = 0
        while True:
            input = client.recv(102400)
            if not input:
                raise TruncatedPrivExecResponse()
            buffered += input

            try:
                reply = privexec_pb2.PrivExecReply()
                consumed = reply.ParseFromString(buffered)
                if consumed == 0:
                    continue
                buffered = buffered[consumed:]

                if reply.HasField('execute_reply'):
                    response = reply.execute_reply
                    _log_results(
                        request_id=response.request_id,
                        execution_id=response.execution_id,
                        stdout=response.stdout,
                        stderr=response.stderr,
                        exit_code=response.exit_code,
                        duration=response.execution_seconds)
                    if response.exit_code in check_exit_code:
                        return response.stdout, response.stderr
                    else:
                        raise ProcessExecutionError(
                            exit_code=response.exit_code,
                            stdout=response.stdout,
                            stderr=response.stderr,
                            cmd=command
                        )
                else:
                    raise UnknownPrivExecReplyException()

            except DecodeError:
                ...

            if locks and time.time() - last_refresh > 9:
                etcd.refresh_locks(locks)
                last_refresh = time.time()

    finally:
        client.close()


def set_thread_name(name):
    try:
        import pyprctl
        pyprctl.set_name(name)
    except (ImportError, AttributeError) as e:
        LOG.debug(f'Failed to change thread name to {name}: {e}')


def _node_lock_request(request):
    if not os.path.exists(NODELOCK_SOCKET_PATH):
        raise MissingNodeLockSocket()

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(NODELOCK_SOCKET_PATH)

    try:
        client.sendall(request.SerializeToString())

        buffered = bytearray()
        while True:
            input = client.recv(102400)
            if not input:
                raise TruncatedNodeLockResponse()
            buffered += input

            try:
                reply = nodelock_pb2.NodeLockReply()
                consumed = reply.ParseFromString(buffered)
                if consumed == 0:
                    continue
                buffered = buffered[consumed:]

                if reply.HasField('lock_reply'):
                    response = reply.lock_reply
                    if response.outcome == response.OK:
                        return True
                    return False

                elif reply.HasField('unlock_reply'):
                    # This is a bit silly -- the responses here are either "ok"
                    # (we unlocked the lock) or "not held" (we don't hold the
                    # lock). Either way we no longer hold the lock.
                    response = reply.unlock_reply
                    return True

                else:
                    raise UnknownNodeLockReplyException()

            except DecodeError:
                ...

    finally:
        client.close()


class NodeLock():
    def __init__(self, name):
        self.name = name
        self.requester = json.dumps({
            'caller': util_callstack.get_caller(offset=-3),
            'lock_id': sf_random.random_id(),
            'request_id': util_general.get_request_id()
        }, indent=4, sort_keys=True)

        self.log = LOG.with_fields(self.requester).with_fields({
            'name': name
        })

    def __enter__(self):
        start_time = time.time()
        slow_warned = False

        request = nodelock_pb2.NodeLockRequest(
            lock_request=nodelock_pb2.LockRequest(
                requester=self.requester,
                key=self.name
            )
        )
        while not _node_lock_request(request):
            duration = round(time.time() - start_time, 2)
            if duration > 5 and not slow_warned:
                self.log.with_fields({
                    'duration': duration
                }).info('Waiting to acquire lock')

            time.sleep(0.2)
        return self

    def __exit__(self, exc_type, exc_val, traceback):
        request = nodelock_pb2.NodeLockRequest(
            unlock_request=nodelock_pb2.UnlockRequest(
                requester=self.requester,
                key=self.name
            )
        )
        _node_lock_request(request)
