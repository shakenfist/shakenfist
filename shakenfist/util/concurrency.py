import flask
import json
import os
import socket
import threading
import time
from types import TracebackType
from typing import Any, Self

from google.protobuf.message import DecodeError
from shakenfist_utilities import logs                     # noreorder
from shakenfist_utilities import random as sf_random      # noreorder

from shakenfist.exceptions import AddFloatingIPFailed
from shakenfist.exceptions import CreateNetworkNamespaceFailed
from shakenfist.exceptions import CreateVXLANInterfaceFailed
from shakenfist.exceptions import EnableNATFailed
from shakenfist.exceptions import EnsureMeshFailed
from shakenfist.exceptions import HashFailed
from shakenfist.exceptions import MissingNodeLockSocket
from shakenfist.exceptions import MissingPrivExecSocket
from shakenfist.exceptions import ProcessExecutionError
from shakenfist.exceptions import RemoveFloatingIPFailed
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
    def __init__(self) -> None:
        self.exit = threading.Event()

    def run(self) -> None:
        LOG.debug('Starting job execution')
        self.execute()
        LOG.debug('Finished job execution')


def _log_results(**kwargs: Any) -> None:
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


def _marshal_privexec_request(request: Any, expected_field: str) -> Any:
    if not os.path.exists(PRIVEXEC_SOCKET_PATH):
        raise MissingPrivExecSocket()

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(PRIVEXEC_SOCKET_PATH)

    try:
        client.sendall(request.SerializeToString())

        buffered = bytearray()
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

                if reply.HasField(expected_field):
                    return reply
                else:
                    raise UnknownPrivExecReplyException()

            except DecodeError:
                ...

    finally:
        client.close()


def execute(
    command: str,
    check_exit_code: list[int] | None = None,
    env_variables: dict[str, str] | None = None,
    netns: Any = None,
    iopriority: int | None = None,
    cwd: str | None = None,
    suppress_command_logging: bool = False
) -> tuple[str, str]:
    if check_exit_code is None:
        check_exit_code = [0]

    try:
        request_id = flask.request.environ.get('FLASK_REQUEST_ID')
    except RuntimeError:
        request_id = None

    # Convert netns to string if it's a UUID object
    netns_str = str(netns) if netns else None

    execution_id = sf_random.random_id()
    request = privexec_pb2.PrivExecRequest(
        execute_request=common_pb2.ExecuteRequest(
            command=command,
            network_namespace=netns_str,
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
            'netns': netns_str,
            'iopriority': iopriority,
            'environment_variables': env_variables,
            'working_directory': cwd,
            'request_id': request_id,
            'execution_id': execution_id
        }).info('Executing command')

    reply = _marshal_privexec_request(request, 'execute_reply')
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

    raise ProcessExecutionError(
        exit_code=response.exit_code,
        stdout=response.stdout,
        stderr=response.stderr,
        cmd=command
    )


def hash_file(path: str, algorithm_str: str) -> str:
    hash_algorithms = {
        'sha1': privexec_pb2.HashAlgorithm.SHA1,
        'sha256': privexec_pb2.HashAlgorithm.SHA256,
        'sha512': privexec_pb2.HashAlgorithm.SHA512,
        'xxh128': privexec_pb2.HashAlgorithm.XXH128
    }

    request = privexec_pb2.PrivExecRequest(
        hash_file_request=privexec_pb2.HashFileRequest(
            path=path,
            algorithm=hash_algorithms[algorithm_str]
        )
    )

    reply = _marshal_privexec_request(request, 'hash_file_reply')
    response = reply.hash_file_reply
    if response.error != privexec_pb2.HashFileReply.OK:
        raise HashFailed()
    return response.hash


def enable_nat(
    network_uuid: Any, network_address: str, network_mask: str, vxid: int
) -> None:
    # Convert network_uuid to string if it's a UUID object
    network_uuid_str = str(network_uuid)
    request = privexec_pb2.PrivExecRequest(
        enable_nat_request=privexec_pb2.EnableNATRequest(
            network_uuid=network_uuid_str,
            network_address=network_address,
            network_mask=network_mask,
            vxid=vxid
        )
    )
    reply = _marshal_privexec_request(request, 'enable_nat_reply')
    response = reply.enable_nat_reply
    if response.error != privexec_pb2.EnableNATReply.OK:
        raise EnableNATFailed()


def ensure_vxlan_mesh(
    network_uuid: Any, vxid: int, node_ips: list[str]
) -> tuple[list[str], list[str]]:
    # Convert network_uuid to string if it's a UUID object
    network_uuid_str = str(network_uuid)
    request = privexec_pb2.PrivExecRequest(
        ensure_vxlan_mesh_request=privexec_pb2.EnsureVXLANMeshRequest(
            network_uuid=network_uuid_str,
            vxid=vxid,
            node_ips=node_ips
        )
    )
    reply = _marshal_privexec_request(request, 'ensure_vxlan_mesh_reply')
    response = reply.ensure_vxlan_mesh_reply
    if response.error != privexec_pb2.EnsureVXLANMeshReply.OK:
        raise EnsureMeshFailed()
    return list(response.added_addresses), list(response.removed_addresses)


def add_floating_ip(
    network_uuid: Any, floating_address: str, inner_address: str,
    vxid: int = 0
) -> None:
    # Convert network_uuid to string if it's a UUID object. The vxid
    # lets privexec derive the egress veth so it can announce the new
    # address with a gratuitous ARP; zero skips the announcement.
    network_uuid_str = str(network_uuid)
    request = privexec_pb2.PrivExecRequest(
        add_floating_ip_request=privexec_pb2.AddFloatingIPRequest(
            network_uuid=network_uuid_str,
            floating_address=floating_address,
            inner_address=inner_address,
            vxid=vxid
        )
    )
    reply = _marshal_privexec_request(request, 'add_floating_ip_reply')
    response = reply.add_floating_ip_reply
    if response.error != privexec_pb2.AddFloatingIPReply.OK:
        raise AddFloatingIPFailed(
            f'{privexec_pb2.AddFloatingIPReply.Errors.Name(response.error)}: '
            f'{response.error_text}')


def remove_floating_ip(network_uuid: Any, floating_address: str) -> None:
    # Convert network_uuid to string if it's a UUID object
    network_uuid_str = str(network_uuid)
    request = privexec_pb2.PrivExecRequest(
        remove_floating_ip_request=privexec_pb2.RemoveFloatingIPRequest(
            network_uuid=network_uuid_str,
            floating_address=floating_address
        )
    )
    reply = _marshal_privexec_request(request, 'remove_floating_ip_reply')
    response = reply.remove_floating_ip_reply
    if response.error != privexec_pb2.RemoveFloatingIPReply.OK:
        raise RemoveFloatingIPFailed(
            f'{privexec_pb2.RemoveFloatingIPReply.Errors.Name(response.error)}'
            f': {response.error_text}')


def create_vxlan_interface(vx_id: int, mesh_interface: str) -> None:
    request = privexec_pb2.PrivExecRequest(
        create_vxlan_interface_request=privexec_pb2.CreateVXLANInterfaceRequest(
            vx_id=vx_id,
            mesh_interface=mesh_interface
        )
    )
    reply = _marshal_privexec_request(request, 'create_vxlan_interface_reply')
    response = reply.create_vxlan_interface_reply
    if response.error != privexec_pb2.CreateVXLANInterfaceReply.OK:
        raise CreateVXLANInterfaceFailed()


def create_network_namespace(netns: Any) -> None:
    # Convert netns to string if it's a UUID object
    netns_str = str(netns)
    request = privexec_pb2.PrivExecRequest(
        create_network_namespace_request=privexec_pb2.CreateNetworkNamespaceRequest(
            namespace=netns_str
        )
    )
    reply = _marshal_privexec_request(
        request, 'create_network_namespace_reply')
    response = reply.create_network_namespace_reply
    if response.error != privexec_pb2.CreateNetworkNamespaceReply.OK:
        raise CreateNetworkNamespaceFailed()


def set_thread_name(name: str) -> None:
    try:
        import pyprctl
        pyprctl.set_name(name)
    except (ImportError, AttributeError) as e:
        LOG.debug(f'Failed to change thread name to {name}: {e}')


def _node_lock_request(request: Any) -> bool:
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
    def __init__(self, name: str) -> None:
        self.name = name
        self.requester = json.dumps({
            'caller': util_callstack.get_caller(offset=-3),
            'lock_id': sf_random.random_id(),
            'request_id': util_general.get_request_id()
        }, indent=4, sort_keys=True)

        self.log = LOG.with_fields(self.requester).with_fields({
            'name': name
        })

    def __enter__(self) -> Self:
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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        traceback: TracebackType | None
    ) -> None:
        request = nodelock_pb2.NodeLockRequest(
            unlock_request=nodelock_pb2.UnlockRequest(
                requester=self.requester,
                key=self.name
            )
        )
        _node_lock_request(request)
