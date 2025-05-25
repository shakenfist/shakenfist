# A deliberately very small python daemon which knows how to execute commands
# as root. It only communicates via a unix domain socket with other SF daemons
# on a single node. The protocol on the unix domain socket is binary serialized
# protobufs.

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
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


# vxlan mesh discovery
MESH_RE = re.compile(r'00:00:00:00:00:00 dst (.*) self permanent')


class VXLANMeshDiscoveryFailure(Exception):
    ...


class PrivExecJob:
    def __init__(self, conn, cached_executables):
        super().__init__()
        self.conn = conn
        self.cached_executables = cached_executables

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
            command = (f'{self.cached_executables["ionice"]} -c '
                       f'{requested_iopriority[0]} '
                       f'-n {requested_iopriority[1]} {command}')

        working_directory = None
        if request.working_directory != '':
            working_directory = request.working_directory

        start_time = time.time()
        obj = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True,
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

    def _command_helper(self, *command):
        obj = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True)
        self.pid = obj.pid

        stdout, stderr = obj.communicate(None, timeout=None)
        if obj.returncode != 0:
            LOG.with_fields({
                'command': command,
                'stdout': stdout,
                'stderr': stderr,
                'exit_code': obj.returncode
            }).error('Command failed')
        return stdout, stderr, obj.returncode

    def _hash_file(self, req):
        log = LOG.with_fields({
            'path': req.path,
            'algorithm': req.algorithm
        })

        hash_commands = {
            privexec_pb2.HashAlgorithm.SHA1: 'sha1sum',
            privexec_pb2.HashAlgorithm.SHA256: 'sha256sum',
            privexec_pb2.HashAlgorithm.SHA512: 'sha512sum',
            privexec_pb2.HashAlgorithm.XXH128: 'xxh128sum'
        }

        if req.algorithm not in hash_commands:
            log.error('Failed to hash file, no hasher found')
            return privexec_pb2.PrivExecReply(
                hash_file_reply=privexec_pb2.HashFileReply(
                    path=req.path,
                    algorithm=req.algorithm,
                    error=privexec_pb2.HashFileReply.UNKNOWN_ALGORITHM
                )
            )

        hasher = shutil.which(hash_commands[req.algorithm])
        if not hasher:
            log.error('Failed to hash file, could not resolve hasher')
            return privexec_pb2.PrivExecReply(
                hash_file_reply=privexec_pb2.HashFileReply(
                    path=req.path,
                    algorithm=req.algorithm,
                    error=privexec_pb2.HashFileReply.ALGORITHM_NOT_FOUND
                )
            )

        stdout, stderr, returncode = self._command_helper(
            self.cached_executables['ionice'], '-c', '2', '-n', '7',
            hasher, req.path
        )
        if returncode != 0 or len(stdout) == 0:
            log.error(
                'Failed to hash file, bad exit code or no output from hasher')
            return privexec_pb2.PrivExecReply(
                hash_file_reply=privexec_pb2.HashFileReply(
                    path=req.path,
                    algorithm=req.algorithm,
                    hash=stdout,
                    error=privexec_pb2.HashFileReply.ALGORITHM_FAILED,
                    error_text=stderr
                )
            )

        return privexec_pb2.PrivExecReply(
            hash_file_reply=privexec_pb2.HashFileReply(
                path=req.path,
                algorithm=req.algorithm,
                hash=stdout.decode().split(' ')[0],
                error=privexec_pb2.HashFileReply.OK
            )
        )

    def _enable_nat(self, req):
        iptables_failed_error = privexec_pb2.PrivExecReply(
            enable_nat_reply=privexec_pb2.EnableNATReply(
                network_uuid=req.network_uuid,
                network_address=req.network_address,
                network_mask=req.network_mask,
                vxid=req.vxid,
                error=privexec_pb2.EnableNATReply.IPTABLES_FAILED
            )
        )

        # Determine if we have NAT already
        stdout, _, returncode = self._command_helper(
            self.cached_executables['iptables'], '-w', '10', '-t', 'nat',
            '-L', 'POSTROUTING', '-n', '-v'
        )
        if returncode != 0:
            return iptables_failed_error

        # Output looks like this:
        # Chain POSTROUTING (policy ACCEPT 199 packets, 18189 bytes)
        # pkts bytes target     prot opt in     out     source               destination
        #   23  1736 MASQUERADE  all  --  *      ens4    192.168.242.0/24     0.0.0.0/0
        for line in stdout.decode().split('\n'):
            if line.find(str(req.network_address)) != -1:
                return privexec_pb2.PrivExecReply(
                    enable_nat_reply=privexec_pb2.EnableNATReply(
                        network_uuid=req.network_uuid,
                        network_address=req.network_address,
                        network_mask=req.network_mask,
                        vxid=req.vxid,
                        error=privexec_pb2.EnableNATReply.RULES_ALREADY_PRESENT
                    )
                )

        # Ensure IP forwarding is enabled
        with open('/proc/sys/net/ipv4/ip_forward') as f:
            forwarding_enabled = f.read().rstrip() == '1'

        if not forwarding_enabled:
            with open('/proc/sys/net/ipv4/ip_forward', 'w') as f:
                f.write('1\n')

        # Create iptables rules
        egress_veth_inner = f'egr-{req.vxid:06x}-i'
        vx_veth_inner = f'veth-{req.vxid:06x}-i'

        _, _, returncode = self._command_helper(
            self.cached_executables['ip'], 'netns', 'exec', req.network_uuid,
            self.cached_executables['iptables'], '-w', '10',
            '-A', 'FORWARD', '-o', egress_veth_inner, '-i', vx_veth_inner,
            '-j', 'ACCEPT'
        )
        if returncode != 0:
            return iptables_failed_error

        _, _, returncode = self._command_helper(
            self.cached_executables['ip'], 'netns', 'exec', req.network_uuid,
            self.cached_executables['iptables'], '-w', '10',
            '-A', 'FORWARD', '-i', egress_veth_inner,
            '-o', 'vx_veth_inner', '-j', 'ACCEPT'
        )
        if returncode != 0:
            return iptables_failed_error

        _, _, returncode = self._command_helper(
            self.cached_executables['ip'], 'netns', 'exec', req.network_uuid,
            self.cached_executables['iptables'], '-w', '10',
            '-t', 'nat', '-A', 'POSTROUTING', '-s',
            f'{req.network_address}/{req.network_mask}',
            '-o', egress_veth_inner, '-j', 'MASQUERADE'
        )
        if returncode != 0:
            return iptables_failed_error

        return privexec_pb2.PrivExecReply(
            enable_nat_reply=privexec_pb2.EnableNATReply(
                network_uuid=req.network_uuid,
                network_address=req.network_address,
                network_mask=req.network_mask,
                vxid=req.vxid,
                error=privexec_pb2.EnableNATReply.OK
            )
        )

    def _discover_mesh(self, vx_interface):
        stdout, _, returncode = self._command_helper(
            self.cached_executables['bridge'], 'fdb', 'show', 'brport',
            vx_interface
        )
        if returncode != 0:
            raise VXLANMeshDiscoveryFailure()

        for line in stdout.decode().split('\n'):
            m = MESH_RE.match(line)
            if m:
                yield m.group(1)

    def _ensure_mesh(self, req):
        removed = []
        added = []
        node_ips = list(req.node_ips)

        vx_interface = f'vxlan-{req.vxid:06x}'

        vxlan_failed_error = privexec_pb2.PrivExecReply(
            ensure_vxlan_mesh_reply=privexec_pb2.EnsureVXLANMeshReply(
                network_uuid=req.network_uuid,
                vxid=req.vxid,
                error=privexec_pb2.EnsureVXLANMeshReply.FAILURE
            )
        )

        try:
            discovered = list(self._discover_mesh(vx_interface))
        except VXLANMeshDiscoveryFailure:
            return vxlan_failed_error

        for n in discovered:
            if n in node_ips:
                node_ips.remove(n)
            else:
                _, _, returncode = self._command_helper(
                    self.cached_executables['bridge'], 'fdb', 'del', 'to',
                    '00:00:00:00:00:00', 'dst', n, 'dev', vx_interface
                )
                if returncode != 0:
                    return vxlan_failed_error
                removed.append(n)

        for n in node_ips:
            _, _, returncode = self._command_helper(
                self.cached_executables['bridge'], 'fdb', 'append', 'to',
                '00:00:00:00:00:00', 'dst', n, 'dev', vx_interface
            )
            if returncode != 0:
                return vxlan_failed_error
            added.append(n)

        return privexec_pb2.PrivExecReply(
            ensure_vxlan_mesh_reply=privexec_pb2.EnsureVXLANMeshReply(
                network_uuid=req.network_uuid,
                vxid=req.vxid,
                error=privexec_pb2.EnsureVXLANMeshReply.OK,
                added_addresses=added,
                removed_addresses=removed
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

                elif request.HasField('hash_file_request'):
                    req = request.hash_file_request
                    reply = self._hash_file(req)
                    self.conn.sendall(reply.SerializeToString())

                elif request.HasField('enable_nat_request'):
                    req = request.enable_nat_request
                    reply = self._enable_nat(req)
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

    cached_executables = {}
    for command in ['ionice', 'iptables', 'ip', 'bridge']:
        command_path = shutil.which(command)
        if not command_path:
            LOG.error(f'Cannot find {command} command in path')
            sys.exit(1)
        cached_executables[command] = command_path

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
            worker_object = PrivExecJob(conn, cached_executables)
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
    raise SystemExit(0)
