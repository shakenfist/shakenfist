import base64
import copy
import errno
import json
import os
import socket
import threading
import time

from google.protobuf.message import DecodeError
from shakenfist_utilities import random as sf_random        # noreorder
from shakenfist_utilities import logs                       # noreorder

from shakenfist import blob
from shakenfist import constants
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import NoSuchChannel
from shakenfist import instance
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.protos import agent_pb2
from shakenfist.protos import common_pb2
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


# This is the minimum version of the in-guest agent that we support. This
# generally gets bumped when the protocol changes.
MINIMUM_AGENT_VERSION = '0.5.9'


# Parameters for blob transfers
MAX_CHUNK_SIZE = 102400
MAX_OUTSTANDING = 5


class ConnectionFailed(Exception):
    ...


class PutException(Exception):
    ...


class SideChannelJob(util_concurrency.Job):
    def __init__(self, inst):
        super().__init__()
        self.instance = inst
        self.instance_ready = constants.AGENT_NEVER_TALKED
        self.thread_name = self.instance.uuid

        self.abort_path = f'/run/sf/sidechannel-{self.thread_name}.abort'
        daemon.clear_abort_path(self.abort_path)

        # A count of the number of sent but not yet acknowledged command
        # messages. Does not include lower level protocol messages like "ping".
        # For now this is only used by SideChannelExecutorJob.
        self.outstanding_message_count = 0

    def _send_commands_single_envelope(
            self, sock, commands, register_as_outstanding=False):
        out = agent_pb2.AgentRequest()
        for cmd in commands:
            out.commands.append(cmd)
            if register_as_outstanding:
                self.outstanding_message_count += 1
                self.log.with_fields({
                    'outstanding_messages': self.outstanding_message_count
                }).debug('...increment outstanding commands')
        sock.sendall(out.SerializeToString())

    def _handle_command_error(self, reply):
        self.log.debug('...command error')
        response = reply.command_error
        self.instance.add_event(
            EVENT_TYPE_STATUS, 'command error',
            extra={
                'error': response.error
            })

    def execute(self):
        etcd.reset_client()
        util_concurrency.set_thread_name(self.thread_name)
        self.log.debug('Attempt channel connection')

        self.last_data = time.time()

        # We use the existence of a console.log file in the instance directory
        # to indicate the instance has been created. This will be true even if
        # the instance doesn't actually every write to the serial console.
        console_path = os.path.join(self.instance.instance_path, 'console.log')
        while not os.path.exists(console_path):
            time.sleep(1)
        self.log.debug('Detected console log')

        # Attempt to connect to the agent
        try:
            with self.instance.socket_on_vsock_channel('sf-agent2') as vsock:
                vsock.sock.settimeout(1)
                self.instance.add_event(
                    EVENT_TYPE_STATUS, 'connected to agent')
                self._execute_inner(vsock)

        except NoSuchChannel:
            self.log.debug('No such channel')

        except socket.error as e:
            if e.errno != errno.ECONNRESET:
                self.log.debug(f'socket.error: {e}')

        except OSError as e:
            self.log.debug(f'OSError: {e}')


class SideChannelMonitorJob(SideChannelJob):
    def __init__(self, inst):
        super().__init__(inst)
        self.abort_path = f'/run/sf/sidechannel-{inst.uuid}.abort'
        self.log = LOG.with_fields({'instance': self.instance.uuid})

        self.instance.agent_state = constants.AGENT_NEVER_TALKED
        self.system_boot_time = 0

    def _send_ping(self, sock):
        if self.instance_ready in [constants.AGENT_READY,
                                   constants.AGENT_READY_DEGRADED]:
            request = agent_pb2.AgentRequestCommand(
                command_id=sf_random.random_id(),
                ping_request=agent_pb2.PingRequest()
            )
            self.log.debug('...ping request')
        else:
            request = agent_pb2.AgentRequestCommand(
                command_id=sf_random.random_id(),
                is_system_running_request=agent_pb2.IsSystemRunningRequest()
            )
            self.log.debug('...is system running request')

        self._send_commands_single_envelope(sock, [request])

    def _record_system_boot_time(self, sbt):
        if sbt != self.system_boot_time:
            if self.system_boot_time != 0:
                self.instance.add_event(EVENT_TYPE_AUDIT, 'reboot detected')
            self.system_boot_time = sbt
            self.instance.agent_system_boot_time = sbt

    def _handle_agent_welcome(self, reply):
        self.log.debug('...agent welcome')
        response = reply.agent_welcome
        self.instance.add_event(
            EVENT_TYPE_STATUS, 'agent metrics',
            extra={
                'version': response.version,
                'boot_time': response.boot_time
            })
        self.instance_ready = constants.AGENT_STARTED
        self.instance.agent_state = constants.AGENT_STARTED
        self._record_system_boot_time(response.boot_time)

    def _handle_is_system_running(self, reply, sock):
        self.log.debug('...is system running reply')
        response = reply.is_system_running_reply
        if response.result:
            new_state = constants.AGENT_READY
        else:
            # Special case the degraded state here, as the
            # system is in fact as ready as it is ever going
            # to be, but isn't entirely happy.
            if response.message == 'degraded':
                new_state = constants.AGENT_READY_DEGRADED
            else:
                new_state = constants.AGENT_DEGRADED % response.message

        # We cache the agent state to reduce database load,
        # and then trigger facts gathering when we transition
        # into the constants.AGENT_READY state.
        if self.instance_ready != new_state:
            self.instance_ready = new_state
            self.instance.agent_state = new_state

            request = agent_pb2.AgentRequestCommand(
                command_id=sf_random.random_id(),
                gather_facts_request=agent_pb2.GatherFactsRequest()
            )
            self._send_commands_single_envelope(sock, [request])
            self.log.debug('...gather facts request')

    def _handle_gather_facts(self, reply):
        self.log.debug('...gather facts reply')
        response = reply.gather_facts_reply

        facts = {
            'distribution': {},
            'mounts': [],
            'ssh-host-keys': {}
        }
        for f in response.distro_facts:
            facts['distribution'][f.name] = json.loads(f.value)
        for mp in response.mount_points:
            facts['mounts'].append(
                {
                    'device': mp.device,
                    'mount_point': mp.mount_point,
                    'vfs_type': mp.vfs_type
                }
            )
        for hk in response.ssh_host_keys:
            facts['ssh-host-keys'][hk.name] = hk.value

        self.instance.add_event(EVENT_TYPE_AUDIT, 'received system facts')
        self.instance.agent_facts = facts

    def _execute_inner(self, vsock):
        request = agent_pb2.AgentRequestCommand(
            command_id=sf_random.random_id(),
            hypervisor_welcome=agent_pb2.HypervisorWelcome(
                version=util_general.get_version()
            )
        )
        self.log.debug('...execute request')
        self._send_commands_single_envelope(vsock.sock, [request])
        self.last_data = time.time()

        buffered = bytearray()
        while daemon.check_abort_path(self.abort_path):
            if time.time() - self.last_data > 2:
                self._send_ping(vsock.sock)
                self.last_data = time.time()

            try:
                input = vsock.sock.recv(102400)
                if not input:
                    return

                self.last_data = time.time()
                buffered += input

                envelope = agent_pb2.AgentReply()
                try:
                    consumed = envelope.ParseFromString(buffered)
                except DecodeError as e:
                    self.log.debug(f'Decode error: {e}')
                    consumed = 0

                if consumed == 0:
                    continue
                buffered = buffered[consumed:]

                for reply in envelope.commands:
                    if reply.HasField('agent_welcome'):
                        self._handle_agent_welcome(reply)

                    elif reply.HasField('is_system_running_reply'):
                        self._handle_is_system_running(
                            reply, vsock.sock)

                    elif reply.HasField('ping_reply'):
                        self.log.debug('...ping reply')

                    elif reply.HasField('gather_facts_reply'):
                        self._handle_gather_facts(reply)

                    elif reply.HasField('command_error'):
                        self._handle_command_error(reply)

            except socket.timeout:
                ...

        self.log.with_fields({
            'abort_path': self.abort_path
        }).debug('Abort path set, exiting')


class SideChannelExecutorJob(SideChannelJob):
    def __init__(self, inst, agentop):
        super().__init__(inst)
        self.agentop = agentop
        self.affected_objects = [self.instance, self.agentop]
        self.thread_name = f'{self.instance.uuid}-{self.agentop.uuid}'

        self.commands = agentop.commands
        self.command_count = 0
        self.num_results = 0
        self.command_cache = {}
        self.chunk_iterator = None

        self.ready = False
        self.log = LOG.with_fields({
            'instance': self.instance.uuid,
            'agent_operation': self.agentop.uuid
        })

    def _send_ping(self, sock):
        request = agent_pb2.AgentRequestCommand(
            command_id=sf_random.random_id(),
            ping_request=agent_pb2.PingRequest()
        )
        self.log.with_fields({
            'outstanding_messages': self.outstanding_message_count
        }).debug('...ping request')
        self._send_commands_single_envelope(sock, [request])

    def _handle_agent_welcome(self, reply):
        self.log.with_fields({
            'outstanding_messages': self.outstanding_message_count
        }).debug('...agent welcome')
        self.ready = True

    def _dispatch_execute(self, command_id, cmd):
        request = agent_pb2.AgentRequestCommand(
            command_id=command_id,
            execute_request=common_pb2.ExecuteRequest(
                command=cmd['commandline'],
                io_priority=common_pb2.ExecuteRequest.NORMAL
            )
        )
        self.command_cache[command_id] = cmd['commandline']
        return [request]

    def _handle_execute_reply(self, reply):
        self.log.with_fields({
            'outstanding_messages': self.outstanding_message_count
        }).debug('...execute reply')
        result = {
            'command': 'execute-response',
            'command-line': self.command_cache[reply.command_id],
            'return-code': reply.execute_reply.exit_code
        }

        # Convert long stdouts and stderrs to blobs
        stdout = reply.execute_reply.stdout
        if len(stdout) > 10 * constants.KiB:
            b = blob.from_memory(stdout.encode('utf-8'))
            b.ref_count_inc(self.agentop)
            result['stdout_blob'] = b.uuid
        else:
            result['stdout'] = stdout

        stderr = reply.execute_reply.stderr
        if len(stderr) > 10 * constants.KiB:
            b = blob.from_memory(stderr.encode('utf-8'))
            b.ref_count_inc(self.agentop)
            result['stderr_blob'] = b.uuid
        else:
            result['stderr'] = stderr

        self.agentop.add_result(self.command_count, result)
        self.num_results += 1
        self.command_count += 1

        extra = {
            'command': 'execute',
            'command-line': self.command_cache[reply.command_id],
            'return-code': reply.execute_reply.exit_code,
            'command_id': reply.command_id
        }
        add_event_multi(
            EVENT_TYPE_STATUS, self.affected_objects,
            'got result for agent execute command', extra=extra)

        self.ready = True

    def _chunk_reader(self, command_id, cmd, path):
        with open(path, 'rb') as f:
            offset = 0
            d = f.read(MAX_CHUNK_SIZE)
            self.log.with_fields({
                'outstanding_messages': self.outstanding_message_count
            }).debug('...put file request (including file chunk)')

            yield agent_pb2.AgentRequestCommand(
                command_id=command_id,
                put_file_request=agent_pb2.PutFileRequest(
                    path=cmd['path'],
                    mode=cmd.get('mode'),
                    length=os.stat(path).st_size,
                    first_chunk=agent_pb2.FileChunk(
                        offset=offset,
                        encoding=agent_pb2.FileChunk.BASE64,
                        payload=base64.b64encode(d)
                    )
                )
            )
            offset += len(d)

            while d := f.read(MAX_CHUNK_SIZE):
                self.log.with_fields({
                    'outstanding_messages': self.outstanding_message_count
                }).debug('...file chunk')
                yield agent_pb2.AgentRequestCommand(
                    command_id=command_id,
                    file_chunk=agent_pb2.FileChunk(
                        offset=offset,
                        encoding=agent_pb2.FileChunk.BASE64,
                        payload=base64.b64encode(d)
                    )
                )
                offset += len(d)

            self.log.with_fields({
                'outstanding_messages': self.outstanding_message_count
            }).debug('...file chunk (termination)')
            yield agent_pb2.AgentRequestCommand(
                command_id=command_id,
                file_chunk=agent_pb2.FileChunk(
                    offset=offset,
                    encoding=agent_pb2.FileChunk.BASE64,
                    payload=None
                )
            )

    def _dispatch_put_blob(self, command_id, cmd):
        if 'blob_uuid' not in cmd:
            self.agentop.error = 'missing blob uuid'
            return []

        b = blob.Blob.from_db(cmd['blob_uuid'])
        if not b:
            self.agentop.error = 'missing blob'
            return []

        # This should already have been done by preflight, but hey
        b.ensure_local([])
        self.chunk_iterator = self._chunk_reader(
            command_id, cmd, blob.Blob.filepath(b.uuid))

        # Try to send MAX_OUTSTANDING chunks
        out = []
        try:
            for _ in range(MAX_OUTSTANDING):
                out.append(self.chunk_iterator.__next__())
        except StopIteration:
            self.chunk_iterator = None

        return out

    def _dispatch_chmod(self, command_id, cmd):
        self.log.with_fields({
            'outstanding_messages': self.outstanding_message_count
        }).debug('...chmod request')
        return [
            agent_pb2.AgentRequestCommand(
                command_id=command_id,
                chmod_request=agent_pb2.ChmodRequest(
                    path=cmd['path'],
                    mode=cmd['mode']
                )
            )
        ]

    def _execute_inner(self, vsock):
        self._send_commands_single_envelope(
            vsock.sock,
            [
                agent_pb2.AgentRequestCommand(
                    command_id=sf_random.random_id(),
                    hypervisor_welcome=agent_pb2.HypervisorWelcome(
                        version=util_general.get_version()
                    )
                )
            ]
        )
        self.last_data = time.time()

        buffered = bytearray()
        while daemon.check_abort_path(self.abort_path):
            if time.time() - self.last_data > 2:
                self._send_ping(vsock.sock)
                self.last_data = time.time()

            try:
                input = vsock.sock.recv(102400)
                if not input:
                    return

                self.last_data = time.time()
                buffered += input

                envelope = agent_pb2.AgentReply()
                try:
                    consumed = envelope.ParseFromString(buffered)
                except DecodeError as e:
                    self.log.with_fields({
                        'outstanding_messages': self.outstanding_message_count
                    }).debug(f'Decode error: {e}')
                    consumed = 0

                if consumed == 0:
                    continue
                buffered = buffered[consumed:]
                self.log.with_fields({
                    'outstanding_messages': self.outstanding_message_count
                }).debug('Received message')

                for reply in envelope.commands:
                    if reply.HasField('agent_welcome'):
                        self._handle_agent_welcome(reply)

                    elif reply.HasField('ping_reply'):
                        self.log.with_fields({
                            'outstanding_messages': self.outstanding_message_count
                        }).debug('...ping reply')

                    elif reply.HasField('execute_reply'):
                        self._handle_execute_reply(reply)

                    elif reply.HasField('file_chunk_reply'):
                        self.log.with_fields({
                            'outstanding_messages': self.outstanding_message_count
                        }).debug('...file chunk reply')
                        self.outstanding_message_count -= 1
                        self.log.with_fields({
                            'outstanding_messages': self.outstanding_message_count
                        }).debug('...decrement outstanding commands')

                        try:
                            out = None
                            if self.chunk_iterator:
                                out = self.chunk_iterator.__next__()
                            if out:
                                self._send_commands_single_envelope(
                                    vsock.sock, out, register_as_outstanding=True)

                        except StopIteration:
                            self.chunk_iterator = None

                        if self.outstanding_message_count == 0:
                            self.ready = True
                        elif self.outstanding_message_count < 0:
                            self.log.with_fields({
                                'outstanding_messages': self.outstanding_message_count
                            }).error('Negative outstanding messages, aborting')

                    elif reply.HasField('chmod_reply'):
                        self.log.with_fields({
                            'outstanding_messages': self.outstanding_message_count
                        }).debug('...chmod reply')
                        self.ready = True

                    elif reply.HasField('command_error'):
                        self._handle_command_error(reply)

                self.log.with_fields({
                    'ready': self.ready,
                    'commands': self.commands,
                    'outstanding_messages': self.outstanding_message_count
                }).debug('Considering command execution')
                if self.ready and not self.commands:
                    # We've run out of things to execute, say goodbye and
                    # disconnect.
                    self._send_commands_single_envelope(
                        vsock.sock,
                        [
                            agent_pb2.AgentRequestCommand(
                                command_id=sf_random.random_id(),
                                hypervisor_departure=agent_pb2.HypervisorDeparture()
                            )
                        ]
                    )
                    if self.agentop.state.value == AgentOperation.STATE_EXECUTING:
                        add_event_multi(
                            EVENT_TYPE_STATUS, self.affected_objects,
                            'commands complete')
                        self.agentop.state = AgentOperation.STATE_COMPLETE
                    return

                if self.ready:
                    requests = []
                    register_as_outstanding = False
                    cmd = self.commands.pop(0)
                    command_id = sf_random.random_id()

                    try:
                        if cmd['command'] == 'execute':
                            requests = self._dispatch_execute(command_id, cmd)

                        elif cmd['command'] == 'put-blob':
                            requests = self._dispatch_put_blob(command_id, cmd)
                            register_as_outstanding = True

                        elif cmd['command'] == 'chmod':
                            requests = self._dispatch_chmod(command_id, cmd)

                        else:
                            self.agentop.error = 'unknown command'

                        if requests:
                            extra = copy.copy(cmd)
                            extra['command_id'] = command_id
                            add_event_multi(
                                EVENT_TYPE_STATUS, self.affected_objects,
                                'executing agent command', extra=extra)
                            self.agentop.state = AgentOperation.STATE_EXECUTING

                            self.log.with_fields({
                                'outstanding_messages': self.outstanding_message_count,
                                'register_as_outstanding': register_as_outstanding
                            }).debug(f'Sending {len(requests)} messages')
                            self._send_commands_single_envelope(
                                vsock.sock, requests,
                                register_as_outstanding=register_as_outstanding)
                            self.ready = False

                    finally:
                        if self.agentop.state.value == AgentOperation.STATE_ERROR:
                            add_event_multi(
                                EVENT_TYPE_STATUS, self.affected_objects,
                                'unknown command', extra=cmd)
                            self.agentop.error = 'unknown command'
                            self.commands = []

            except socket.timeout:
                ...

        self.log.with_fields({
            'abort_path': self.abort_path,
            'outstanding_messages': self.outstanding_message_count
        }).debug('Abort path set, exiting')


class Monitor(daemon.Daemon):
    def __init__(self, name):
        super().__init__(name)

        self.instance_sidechannel_cache = {}
        self.monitors = {}
        self.monitor_attempts = {}
        self.executors = {}

    def start_instance_monitor(self, instance_uuid):
        # Rate limit how often we try to connect
        last_attempt = self.monitor_attempts.get(instance_uuid, 0)
        if time.time() - last_attempt < 30:
            return
        self.monitor_attempts[instance_uuid] = time.time()

        inst = instance.Instance.from_db(instance_uuid)
        if not inst:
            return

        if instance_uuid not in self.instance_sidechannel_cache:
            self.instance_sidechannel_cache[instance_uuid] = inst.side_channels

        if 'sf-agent2' not in self.instance_sidechannel_cache[instance_uuid]:
            return
        if inst.state.value == instance.Instance.STATE_DELETED:
            return
        if not inst.vsock_cid('sf-agent2'):
            return

        sc_obj = SideChannelMonitorJob(inst)
        sc_thread = threading.Thread(
            target=sc_obj.run, daemon=True, name=instance_uuid)
        sc_thread.start()

        self.monitors[instance_uuid] = {
            'object': sc_obj,
            'thread': sc_thread,
            'instance_uuid': instance_uuid
        }

    def reap_instance_monitors(self):
        all_monitors = list(self.monitors.keys())
        for instance_uuid in all_monitors:
            t = self.monitors[instance_uuid]
            if not t['thread'].is_alive():
                t['thread'].join(1)
                del self.monitors[instance_uuid]

    def start_instance_executor(self, instance_uuid, agentop):
        inst = instance.Instance.from_db(instance_uuid)
        if not inst:
            return

        if instance_uuid not in self.monitors:
            return

        sc_obj = SideChannelExecutorJob(inst, agentop)
        sc_thread = threading.Thread(
            target=sc_obj.run, daemon=True, name=instance_uuid)
        sc_thread.start()

        self.executors[instance_uuid] = {
            'object': sc_obj,
            'thread': sc_thread,
            'instance_uuid': instance_uuid
        }
        eventlog.add_event(
            EVENT_TYPE_AUDIT, 'instance', instance_uuid,
            'side channel executor started',
            extra={
                'thread_ident': sc_thread.ident
            })

    def reap_instance_executors(self):
        all_executors = list(self.executors.keys())
        for executor_id in all_executors:
            t = self.executors[executor_id]
            if not t['thread'].is_alive():
                t['thread'].join(1)
                eventlog.add_event(
                    EVENT_TYPE_AUDIT, 'instance', t['instance_uuid'],
                    'side channel executor ended',
                    extra={
                        'thread_ident': t['thread'].ident
                    })
                del self.executors[executor_id]

    def _request_all_threads_exit(self):
        LOG.info('Requesting all threads exit')

        all_monitors = list(self.monitors.keys())
        for instance_uuid in all_monitors:
            self._request_thread_exit(
                instance_uuid, self.monitors[instance_uuid])

        all_executors = list(self.executors.keys())
        for instance_uuid in all_executors:
            self._request_thread_exit(
                instance_uuid, self.executors[instance_uuid])

    def _request_thread_exit(self, instance_uuid, t):
        daemon.set_abort_path(
            t['object'].abort_path, 'from _request_thread_exit')
        eventlog.add_event(
            EVENT_TYPE_AUDIT, 'instance', instance_uuid,
            'side channel monitor instructed to exit')
        self.monitors[instance_uuid]['thread'].join(0.5)

        if not t['thread'].is_alive():
            del self.monitors[instance_uuid]
            daemon.clear_abort_path(t['object'].abort_path)
            eventlog.add_event(
                EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                'side channel monitor finished')

    def _run_inner(self):
        while daemon.check_abort_path(self.abort_path):
            try:
                while not daemon.health_check_nodelock():
                    LOG.info('Waiting for nodelock daemon to be healthy')
                    time.sleep(1)
                    continue

                self.reap_instance_monitors()
                self.reap_instance_executors()

                if not os.path.exists(self.abort_path):
                    # Audit desired self.monitors
                    extra_instances = list(self.monitors.keys())
                    missing_instances = []

                    # The goal here is to find all instances running on this node so
                    # that we can monitor them. We used to query etcd for this, but
                    # we needed to do so frequently and it created a lot of etcd load.
                    # We also can't use the existence of instance folders (which once
                    # seemed like a good idea at the time), because some instances might
                    # also be powered off. Instead, we ask libvirt what domains are
                    # running.
                    with util_libvirt.LibvirtConnection() as lc:
                        for domain in lc.get_sf_domains():
                            state = lc.extract_power_state(domain)
                            if state in ['off', 'crashed', 'paused']:
                                # If the domain isn't running, it shouldn't have a
                                # sidechannel monitor.
                                continue

                            instance_uuid = domain.name().split(':')[1]
                            if instance_uuid in extra_instances:
                                extra_instances.remove(instance_uuid)
                            if instance_uuid not in self.monitors:
                                missing_instances.append(instance_uuid)

                    # Start missing monitors. We only support sf-agent2 for now.
                    for instance_uuid in missing_instances:
                        self.start_instance_monitor(instance_uuid)

                    # Cleanup extra monitors
                    for instance_uuid in extra_instances:
                        self._request_thread_exit(
                            instance_uuid, self.monitors[instance_uuid])

                    # Run jobs for healthy instances. For now we only support
                    # running one job at once.
                    for instance_uuid in list(self.monitors.keys()):
                        if instance_uuid in self.executors:
                            continue

                        inst = instance.Instance.from_db(instance_uuid)
                        if not inst:
                            continue

                        ready = self.monitors[instance_uuid]['object'].instance_ready
                        if ready not in [constants.AGENT_READY,
                                         constants.AGENT_READY_DEGRADED]:
                            continue

                        agentop = inst.agent_operation_dequeue()
                        if agentop:
                            inst.add_event(
                                EVENT_TYPE_AUDIT, 'dequeued agent operation',
                                extra={'agentoperation': agentop.uuid})

                            self.start_instance_executor(
                                instance_uuid, agentop)

                    self.idle(1)

            except Exception as e:
                util_general.ignore_exception('side channel monitor', e)

        LOG.info('Stopping')

        while self.monitors:
            LOG.info('There are {len(self.monitors)} threads remaining')
            self._request_all_threads_exit()
            if self.monitors:
                time.sleep(5)

        LOG.info('There are {len(self.monitors)} threads remaining')
        LOG.info('Stopped')


def main():
    daemon.write_pid_file('sidechannel')
    m = Monitor('sidechannel')

    while not daemon.health_check_nodelock():
        LOG.info('Waiting for nodelock daemon to be healthy')
        time.sleep(1)
    LOG.info('nodelock daemon reports healthy')

    m.run()

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    LOG.info('Terminating ourselves')
    raise SystemExit(0)
