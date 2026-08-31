# A deliberately very small python daemon which knows how to execute commands
# as root. It only communicates via a unix domain socket with other SF daemons
# on a single node. The protocol on the unix domain socket is binary serialized
# protobufs.

import ipaddress
import os
import re
import shutil
import signal
import socket
import subprocess
import threading
import time

from google.protobuf.message import DecodeError
import psutil
import setproctitle
from shakenfist_utilities import random      # noreorder
from shakenfist_utilities import logs        # noreorder

from shakenfist.daemons.daemon import apply_log_level
from shakenfist.daemons.daemon import force_clean_exit
from shakenfist.daemons.daemon import send_systemd_ready
from shakenfist.daemons.daemon import send_systemd_stopping
from shakenfist.daemons.privexec import util as privexec_util
from shakenfist.protos import common_pb2
from shakenfist.protos import privexec_pb2
from shakenfist.util import exceptions as util_exceptions


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
    def __init__(self, conn):
        super().__init__()
        self.conn = conn
        self.task_details = None

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
            command = (f'{privexec_util.locate_command("ionice")} -c '
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

        if not os.path.exists(req.path):
            log.error('Failed to hash file, file not found')
            return privexec_pb2.PrivExecReply(
                hash_file_reply=privexec_pb2.HashFileReply(
                    path=req.path,
                    algorithm=req.algorithm,
                    error=privexec_pb2.HashFileReply.FILE_NOT_FOUND
                )
            )

        if req.algorithm not in hash_commands:
            log.error('Failed to hash file, no hasher found')
            return privexec_pb2.PrivExecReply(
                hash_file_reply=privexec_pb2.HashFileReply(
                    path=req.path,
                    algorithm=req.algorithm,
                    error=privexec_pb2.HashFileReply.UNKNOWN_ALGORITHM
                )
            )

        hasher = privexec_util.locate_command(hash_commands[req.algorithm])
        if not hasher:
            log.error('Failed to hash file, could not resolve hasher')
            return privexec_pb2.PrivExecReply(
                hash_file_reply=privexec_pb2.HashFileReply(
                    path=req.path,
                    algorithm=req.algorithm,
                    error=privexec_pb2.HashFileReply.ALGORITHM_NOT_FOUND
                )
            )

        stdout, stderr, returncode = privexec_util.command_helper(
            privexec_util.locate_command('ionice'), '-c', '2', '-n', '7',
            hasher, req.path
        )
        if returncode != 0:
            log.with_fields({
                'returncode': returncode,
                'stderr': stderr
            }).error('Failed to hash file, hasher exited non-zero')
            return privexec_pb2.PrivExecReply(
                hash_file_reply=privexec_pb2.HashFileReply(
                    path=req.path,
                    algorithm=req.algorithm,
                    hash=stdout,
                    error=privexec_pb2.HashFileReply.ALGORITHM_FAILED,
                    error_text=f'hasher {hasher} exited {returncode}: {stderr}'
                )
            )

        if len(stdout) == 0:
            log.with_fields({
                'returncode': returncode,
                'stderr': stderr
            }).error('Failed to hash file, hasher exited zero but produced no output')
            return privexec_pb2.PrivExecReply(
                hash_file_reply=privexec_pb2.HashFileReply(
                    path=req.path,
                    algorithm=req.algorithm,
                    error=privexec_pb2.HashFileReply.HASHER_NO_OUTPUT,
                    error_text=f'hasher {hasher} exited 0 but produced no output; stderr: {stderr}'
                )
            )

        return privexec_pb2.PrivExecReply(
            hash_file_reply=privexec_pb2.HashFileReply(
                path=req.path,
                algorithm=req.algorithm,
                hash=stdout.split(' ')[0],
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
        stdout, _, returncode = privexec_util.command_helper(
            privexec_util.locate_command('iptables'), '-w', '10', '-t', 'nat',
            '-L', 'POSTROUTING', '-n', '-v'
        )
        if returncode != 0:
            return iptables_failed_error

        # Output looks like this:
        # Chain POSTROUTING (policy ACCEPT 199 packets, 18189 bytes)
        # pkts bytes target     prot opt in     out     source               destination
        #   23  1736 MASQUERADE  all  --  *      ens4    192.168.242.0/24     0.0.0.0/0
        for line in stdout.split('\n'):
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

        _, _, returncode = privexec_util.command_helper(
            privexec_util.locate_command('ip'), 'netns', 'exec',
            req.network_uuid, privexec_util.locate_command('iptables'),
            '-w', '10', '-A', 'FORWARD', '-o', egress_veth_inner,
            '-i', vx_veth_inner, '-j', 'ACCEPT'
        )
        if returncode != 0:
            return iptables_failed_error

        _, _, returncode = privexec_util.command_helper(
            privexec_util.locate_command('ip'), 'netns', 'exec',
            req.network_uuid, privexec_util.locate_command('iptables'),
            '-w', '10', '-A', 'FORWARD', '-i', egress_veth_inner,
            '-o', 'vx_veth_inner', '-j', 'ACCEPT'
        )
        if returncode != 0:
            return iptables_failed_error

        _, _, returncode = privexec_util.command_helper(
            privexec_util.locate_command('ip'), 'netns', 'exec',
            req.network_uuid, privexec_util.locate_command('iptables'),
            '-w', '10', '-t', 'nat', '-A', 'POSTROUTING', '-s',
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
        # The vxlan device may have been torn down since the caller
        # built its EnsureVXLANMesh request — networks are deleted
        # asynchronously and the next mesh refresh can race the
        # teardown. ``bridge fdb show`` writes ``Cannot find device``
        # to stderr and exits non-zero in that case, which the caller
        # already turns into a benign FAILURE reply. Pass
        # ``failure_is_error=False`` so command_helper logs the miss
        # at debug level instead of producing an ``ERROR sf-privexec``
        # line that fails the post-test stable-log check.
        stdout, _, returncode = privexec_util.command_helper(
            privexec_util.locate_command('bridge'), 'fdb', 'show', 'brport',
            vx_interface, failure_is_error=False
        )
        if returncode != 0:
            raise VXLANMeshDiscoveryFailure()

        for line in stdout.split('\n'):
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
                _, _, returncode = privexec_util.command_helper(
                    privexec_util.locate_command('bridge'), 'fdb', 'del', 'to',
                    '00:00:00:00:00:00', 'dst', n, 'dev', vx_interface
                )
                if returncode != 0:
                    return vxlan_failed_error
                removed.append(n)

        for n in node_ips:
            _, _, returncode = privexec_util.command_helper(
                privexec_util.locate_command('bridge'), 'fdb', 'append', 'to',
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

    def _add_floating_ip(self, req):
        floating_interface = \
            f'flt-{int(ipaddress.IPv4Address(req.floating_address)):08x}'
        inner_floating_interface = f'{floating_interface}-i'

        success, create_error = privexec_util.create_interface(
            floating_interface, 'veth',
            ['peer', 'name', inner_floating_interface],
            inner_namespace=req.network_uuid
        )

        # create_interface returns success without doing anything if the
        # outer end already exists, but that doesn't mean the inner end is
        # in the network namespace we need it in -- a previous user of this
        # floating IP on another network may have left the pair stranded.
        # Deleting the outer end destroys the pair wherever the inner end
        # is, letting us recreate it cleanly.
        error_text = ''
        if success and not privexec_util.check_for_interface(
                inner_floating_interface, namespace=req.network_uuid):
            _, stderr, returncode = privexec_util.command_helper(
                privexec_util.locate_command('ip'), 'link', 'del',
                floating_interface)
            success = returncode == 0
            if success:
                success, create_error = privexec_util.create_interface(
                    floating_interface, 'veth',
                    ['peer', 'name', inner_floating_interface],
                    inner_namespace=req.network_uuid
                )
            else:
                error_text = (
                    f'failed to delete stranded interface '
                    f'{floating_interface}: {stderr}')

        if not success:
            if not error_text:
                error_text = (
                    f'failed to create veth pair {floating_interface} / '
                    f'{inner_floating_interface} with inner end in '
                    f'namespace {req.network_uuid}: {create_error}')
            return privexec_pb2.PrivExecReply(
                add_floating_ip_reply=privexec_pb2.AddFloatingIPReply(
                    network_uuid=req.network_uuid,
                    floating_address=req.floating_address,
                    inner_address=req.inner_address,
                    error=privexec_pb2.AddFloatingIPReply.CREATE_INTERFACE_FAILED,
                    error_text=error_text
                )
            )

        # The floating address lives on the inner end of the veth pair,
        # inside the network namespace, so that is where we must look when
        # deciding if it is already configured.
        if req.floating_address not in privexec_util.get_interface_addresses(
            inner_floating_interface, namespace=req.network_uuid
        ):
            success = privexec_util.add_address_to_interface(
                inner_floating_interface, req.network_uuid,
                req.floating_address, '32')
            if not success:
                return privexec_pb2.PrivExecReply(
                    add_floating_ip_reply=privexec_pb2.AddFloatingIPReply(
                        network_uuid=req.network_uuid,
                        floating_address=req.floating_address,
                        inner_address=req.inner_address,
                        error=privexec_pb2.AddFloatingIPReply.ADD_ADDRESS_FAILED,
                        error_text=(
                            f'failed to add {req.floating_address}/32 to '
                            f'{inner_floating_interface} in namespace '
                            f'{req.network_uuid}')
                    )
                )

        # The floating address is an address anchor only -- neither end
        # of the veth pair carries traffic (ARP for the address is
        # answered via the egress veth thanks to arp_ignore=0). It
        # historically worked with both ends left admin-DOWN because
        # the kernel keeps the /32's local route in that state, but
        # that is subtle enough to be fragile. Bring both ends up so
        # the interface state matches intent. Best effort: an already
        # working datapath should not fail because of this.
        _, _, returncode = privexec_util.command_helper(
            privexec_util.locate_command('ip'), 'link', 'set',
            floating_interface, 'up', failure_is_error=False)
        if returncode != 0:
            LOG.with_fields({
                'interface': floating_interface}).warning(
                'Failed to bring floating veth outer end up')
        _, _, returncode = privexec_util.command_helper(
            privexec_util.locate_command('ip'), 'netns', 'exec',
            req.network_uuid, privexec_util.locate_command('ip'),
            'link', 'set', inner_floating_interface, 'up',
            failure_is_error=False)
        if returncode != 0:
            LOG.with_fields({
                'interface': inner_floating_interface,
                'netns': req.network_uuid}).warning(
                'Failed to bring floating veth inner end up')

        # Only append the DNAT rule if an identical rule is not already
        # present. Duplicated rules aren't just clutter -- the first match
        # wins, so a duplicate from an earlier partial attempt would mask
        # later changes.
        dnat_rule = [
            'PREROUTING', '-d', req.floating_address, '-j', 'DNAT',
            '--to-destination', req.inner_address]
        _, _, returncode = privexec_util.command_helper(
            privexec_util.locate_command('ip'), 'netns', 'exec',
            req.network_uuid, privexec_util.locate_command('iptables'),
            '-w', '10', '-t', 'nat', '-C', *dnat_rule,
            failure_is_error=False)
        if returncode != 0:
            _, stderr, returncode = privexec_util.command_helper(
                privexec_util.locate_command('ip'), 'netns', 'exec',
                req.network_uuid, privexec_util.locate_command('iptables'),
                '-w', '10', '-t', 'nat', '-A', *dnat_rule)
            if returncode != 0:
                return privexec_pb2.PrivExecReply(
                    add_floating_ip_reply=privexec_pb2.AddFloatingIPReply(
                        network_uuid=req.network_uuid,
                        floating_address=req.floating_address,
                        inner_address=req.inner_address,
                        error=privexec_pb2.AddFloatingIPReply.IPTABLES_FAILED,
                        error_text=(
                            f'failed to append DNAT rule for '
                            f'{req.floating_address} in namespace '
                            f'{req.network_uuid}: {stderr}')
                    )
                )

        # Announce the address with a gratuitous ARP out the egress
        # veth once the datapath is complete. Floating addresses are
        # recycled between networks (each with a distinct egress veth
        # MAC), so upstream ARP caches can hold the previous holder's
        # MAC; the announcement converges them immediately. This is
        # the same L2 advertisement the network carrier model plans to
        # use on lease acquire (PLAN-network-carrier-model phase 9),
        # and one of the exec sites PLAN-replace-exec-with-netlink
        # phase 6 will absorb. Best effort: reachability usually works
        # without it, so a missing arping binary or a transient
        # failure should not fail the float add.
        self._announce_floating_ip(req)

        return privexec_pb2.PrivExecReply(
            add_floating_ip_reply=privexec_pb2.AddFloatingIPReply(
                network_uuid=req.network_uuid,
                floating_address=req.floating_address,
                inner_address=req.inner_address,
                error=privexec_pb2.AddFloatingIPReply.OK
            )
        )

    def _announce_floating_ip(self, req):
        """Send a gratuitous ARP for a just-added floating address.

        Emitted from inside the network namespace out the egress veth
        (derived from the vxid), announcing the namespace's MAC for
        the floating address to the upstream L2 segment. A zero vxid
        means the caller predates the field; skip silently.
        """
        if not req.vxid:
            return

        arping = shutil.which('arping')
        if not arping:
            LOG.warning(
                'arping not found, skipping gratuitous ARP for '
                'floating address')
            return

        egress_veth_inner = f'egr-{req.vxid:06x}-i'
        _, stderr, returncode = privexec_util.command_helper(
            privexec_util.locate_command('ip'), 'netns', 'exec',
            req.network_uuid, arping, '-c', '2', '-U',
            '-i', egress_veth_inner, '-S', req.floating_address,
            req.floating_address, failure_is_error=False)
        if returncode != 0:
            LOG.with_fields({
                'floating_address': req.floating_address,
                'netns': req.network_uuid,
                'interface': egress_veth_inner,
                'stderr': stderr}).warning(
                'Gratuitous ARP for floating address failed')

    def _remove_floating_ip(self, req):
        floating_interface = \
            f'flt-{int(ipaddress.IPv4Address(req.floating_address)):08x}'

        # Removal is best effort cleanup: a failure removing any one piece
        # of state must not abort the removal of the rest. Aborting midway
        # strands more than it cleans -- the veth pair this PR exists to
        # stop leaking would be left behind whenever a single DNAT delete
        # failed. Accumulate failures and keep going, then report FAILED
        # at the end if anything did not clean up. The operations are
        # idempotent, so a caller retry converges on a clean state.
        errors = []

        # Remove DNAT rules for this floating IP from the network namespace
        # before removing the interface. A stale rule matches in preference
        # to the rule added by any later user of this floating IP on the
        # same network, silently misdirecting traffic to the old inner
        # address.
        if os.path.exists(f'/var/run/netns/{req.network_uuid}'):
            stdout, _, returncode = privexec_util.command_helper(
                privexec_util.locate_command('ip'), 'netns', 'exec',
                req.network_uuid, privexec_util.locate_command('iptables'),
                '-w', '10', '-t', 'nat', '-S', 'PREROUTING',
                failure_is_error=False)
            if returncode == 0:
                for rule in stdout.split('\n'):
                    if f'-d {req.floating_address}/32 ' not in rule:
                        continue
                    _, stderr, returncode = privexec_util.command_helper(
                        privexec_util.locate_command('ip'), 'netns', 'exec',
                        req.network_uuid,
                        privexec_util.locate_command('iptables'),
                        '-w', '10', '-t', 'nat', '-D', *rule.split()[1:],
                        failure_is_error=False)
                    if returncode != 0:
                        errors.append(
                            f'failed to remove DNAT rule "{rule}" in '
                            f'namespace {req.network_uuid}: {stderr}')

        # This name must match the outer interface created by
        # _add_floating_ip. Deleting the outer end also destroys the inner
        # end in the network namespace, along with the address on it.
        if privexec_util.check_for_interface(floating_interface):
            _, stderr, returncode = privexec_util.command_helper(
                privexec_util.locate_command('ip'), 'link', 'del',
                floating_interface, failure_is_error=False)
            if returncode != 0:
                errors.append(
                    f'failed to delete interface {floating_interface}: '
                    f'{stderr}')

        if errors:
            return privexec_pb2.PrivExecReply(
                remove_floating_ip_reply=privexec_pb2.RemoveFloatingIPReply(
                    network_uuid=req.network_uuid,
                    floating_address=req.floating_address,
                    error=privexec_pb2.RemoveFloatingIPReply.FAILED,
                    error_text='; '.join(errors)
                )
            )

        return privexec_pb2.PrivExecReply(
            remove_floating_ip_reply=privexec_pb2.RemoveFloatingIPReply(
                network_uuid=req.network_uuid,
                floating_address=req.floating_address,
                error=privexec_pb2.RemoveFloatingIPReply.OK
            )
        )

    def _create_vx_interface(self, req):
        vx_interface = f'vxlan-{req.vx_id:06x}'
        vx_bridge = f'br-vxlan-{req.vx_id:06x}'

        if not privexec_util.create_vx_interface(
            vx_interface, req.vx_id, vx_bridge, req.mesh_interface
        ):
            return privexec_pb2.PrivExecReply(
                create_vxlan_interface_reply=privexec_pb2.CreateVXLANInterfaceReply(
                    vx_id=req.vx_id,
                    mesh_interface=req.mesh_interface,
                    error=privexec_pb2.CreateVXLANInterfaceReply.FAILED
                )
            )

        return privexec_pb2.PrivExecReply(
            create_vxlan_interface_reply=privexec_pb2.CreateVXLANInterfaceReply(
                vx_id=req.vx_id,
                mesh_interface=req.mesh_interface,
                error=privexec_pb2.CreateVXLANInterfaceReply.OK
            )
        )

    def _create_network_namespace(self, req):
        if not privexec_util.create_network_namespace(req.namespace):
            return privexec_pb2.PrivExecReply(
                create_network_namespace_reply=privexec_pb2.CreateNetworkNamespaceReply(
                    namespace=req.namespace,
                    error=privexec_pb2.CreateVXLANInterfaceReply.FAILED
                )
            )

        return privexec_pb2.PrivExecReply(
            create_network_namespace_reply=privexec_pb2.CreateNetworkNamespaceReply(
                namespace=req.namespace,
                error=privexec_pb2.CreateVXLANInterfaceReply.OK
            )
        )

    def run(self):
        buffered = bytearray()
        command_found = False
        error = False

        while not error:
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

                request_map = {
                    'execute_request': self._execute,
                    'hash_file_request': self._hash_file,
                    'enable_nat_request': self._enable_nat,
                    'ensure_vxlan_mesh_request': self._ensure_mesh,
                    'add_floating_ip_request': self._add_floating_ip,
                    'remove_floating_ip_request': self._remove_floating_ip,
                    'create_vxlan_interface_request': (
                        self._create_vx_interface
                    ),
                    'create_network_namespace_request': (
                        self._create_network_namespace
                    )
                }

                for request_field in request_map:
                    if request.HasField(request_field):
                        req = getattr(request, request_field)
                        reply = request_map[request_field](req)
                        self.conn.sendall(reply.SerializeToString())
                        command_found = True
                        break

                if not command_found:
                    error = True

            except DecodeError:
                ...

        self.conn.close()


def write_pid_file():
    with open('/run/sf/privexec.pid', 'w') as f:
        f.write(f'{os.getpid()}')


def main():
    util_exceptions.install_exception_tracking()
    write_pid_file()
    # This daemon has its own minimal write_pid_file rather than the
    # universal hook in daemons.daemon, so the configured log level
    # must be applied explicitly.
    apply_log_level('privexec')
    setproctitle.setproctitle('sf-privexec')
    from shakenfist.util.caller_identity import set_caller_identity
    set_caller_identity('privexec')

    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(SOCKET_PATH)
    s.listen(1)
    s.settimeout(0.2)
    LOG.info('Listening for incoming requests')
    send_systemd_ready()

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
    send_systemd_stopping()

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

    force_clean_exit()
