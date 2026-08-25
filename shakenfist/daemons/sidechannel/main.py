import base64
import copy
import errno
import json
import os
import socket
import threading
import time
from uuid import uuid4

from google.protobuf.message import DecodeError
from google.protobuf.json_format import MessageToDict
from shakenfist_utilities import random as sf_random        # noreorder
from shakenfist_utilities import logs                       # noreorder
import symbolicmode

from shakenfist import blob
from shakenfist import constants
from shakenfist import mariadb
from shakenfist.constants import EVENT_TYPE_AUDIT
from shakenfist.constants import EVENT_TYPE_STATUS
from shakenfist.daemons import daemon
from shakenfist.daemons.daemon import send_systemd_stopping
from shakenfist.eventlog import add_event
from shakenfist.eventlog import add_event_multi
from shakenfist.exceptions import NoSuchChannel
from shakenfist import instance
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.protos import agent_pb2
from shakenfist.protos import common_pb2
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import exceptions as util_exceptions
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt


LOG, _ = logs.setup(__name__)


# This is the minimum version of the in-guest agent that we support. This
# generally gets bumped when the protocol changes.
MINIMUM_AGENT_VERSION = '0.5.13'


# Parameters for blob transfers
MAX_CHUNK_SIZE = 102400
MAX_OUTSTANDING = 5


# How often the executor persists an operation's last_progress
# attribute. The in-memory value moves on every observed reply, which
# is what the progress timeout below is measured against; the
# persisted one exists only so a future node-local reaper can tell a
# stalled transfer from a slow but healthy one, and a fast chunk
# stream must not turn into one attributes write per chunk.
PROGRESS_PERSIST_INTERVAL = 10


class ConnectionFailed(Exception):
    ...


class PutException(Exception):
    ...


class GetException(Exception):
    ...


class SideChannelJob(util_concurrency.Job):
    def __init__(self, inst):
        super().__init__()
        self.instance = inst
        self.instance_ready = constants.AGENT_NEVER_TALKED
        self.thread_name = str(self.instance.uuid)

        self.abort_path = f'/run/sf/sidechannel-{self.thread_name}.abort'
        daemon.clear_abort_path(self.abort_path)

        # A count of the number of sent but not yet acknowledged command
        # messages. Does not include lower level protocol messages like "ping".
        # For now this is only used by SideChannelExecutorJob.
        self.outstanding_message_count = 0

    def _send_commands_single_envelope(
            self, sock, commands, register_as_outstanding=False):
        out = agent_pb2.HypervisorToAgent()
        for cmd in commands:
            out.commands.append(cmd)
            if register_as_outstanding:
                self.outstanding_message_count += 1
                self.log.with_fields({
                    'outstanding_messages': self.outstanding_message_count
                }).debug('...increment outstanding commands')
        sock.sendall(out.SerializeToString())

    def _send_replies_single_envelope(self, sock, replies):
        out = agent_pb2.HypervisorToAgent()
        for cmd in replies:
            out.commands.append(cmd)
        sock.sendall(out.SerializeToString())

    def _handle_command_error(self, reply):
        self.log.with_fields({
            'outstanding_messages': self.outstanding_message_count
        }).info('Received command error from agent')
        response = reply.command_error
        self.instance.add_event(
            EVENT_TYPE_STATUS, 'command error from agent',
            extra={
                'error': response.error,
                'last_envelope': MessageToDict(response.last_envelope)
            })

    def execute(self):
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
            request = agent_pb2.HypervisorToAgentCommand(
                command_id=sf_random.random_id(),
                ping_request=agent_pb2.PingRequest()
            )
            self.log.debug('...ping request')
        else:
            request = agent_pb2.HypervisorToAgentCommand(
                command_id=sf_random.random_id(),
                is_system_running_request=agent_pb2.IsSystemRunningRequest()
            )

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

            request = agent_pb2.HypervisorToAgentCommand(
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
        request = agent_pb2.HypervisorToAgentCommand(
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

                envelope = agent_pb2.AgentToHypervisor()
                try:
                    consumed = envelope.ParseFromString(buffered)
                except DecodeError:
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


class AgentCommandHandler:
    """Dispatch and capabilities for one agent command verb."""

    name: str = ''
    reports_progress = False    # read in phase 4
    retryable = True            # read in phase 5
    register_as_outstanding = False

    def __init__(self, job):
        self.job = job

    def dispatch(self, command_id, cmd):
        raise NotImplementedError


class ExecuteCommand(AgentCommandHandler):
    name = 'execute'
    retryable = False

    def dispatch(self, command_id, cmd):
        request = agent_pb2.HypervisorToAgentCommand(
            command_id=command_id,
            execute_request=common_pb2.ExecuteRequest(
                command=cmd['commandline'],
                io_priority=common_pb2.ExecuteRequest.NORMAL
            )
        )
        self.job.command_cache[command_id] = cmd['commandline']
        return [request]


class PutBlobCommand(AgentCommandHandler):
    name = 'put-blob'
    reports_progress = True
    register_as_outstanding = True

    def dispatch(self, command_id, cmd):
        if 'blob_uuid' not in cmd:
            self.job.agentop.fail('missing blob uuid')
            return []

        b = blob.Blob.from_db(cmd['blob_uuid'])
        if not b:
            self.job.agentop.fail('missing blob')
            return []

        # This should already have been done by preflight, but hey
        b.ensure_local()
        self.job.chunk_iterator = self.job._chunk_reader(
            command_id, cmd, blob.Blob.filepath(b.uuid))

        # Try to send MAX_OUTSTANDING chunks
        out = []
        try:
            for _ in range(MAX_OUTSTANDING):
                out.append(self.job.chunk_iterator.__next__())
        except StopIteration:
            self.job.chunk_iterator = None

        return out


class ChmodCommand(AgentCommandHandler):
    name = 'chmod'

    def dispatch(self, command_id, cmd):
        self.job.log.with_fields({
            'outstanding_messages': self.job.outstanding_message_count
        }).debug('...chmod request')

        mode = None
        try:
            mode = int(cmd['mode'])
        except ValueError:
            ...

        if not mode:
            try:
                mode = symbolicmode.symbolic_to_numeric_permissions(
                    cmd['mode'])
            except Exception as e:
                self.job.log.with_fields({
                    'outstanding_messages': self.job.outstanding_message_count
                }).debug(f'symbolic mode conversion failed: {e}')

        if not mode:
            add_event_multi(
                EVENT_TYPE_AUDIT, self.job.affected_objects,
                'failed to decode chmod mode argument',
                extra=cmd)
            self.job.log.with_fields({
                'outstanding_messages': self.job.outstanding_message_count,
                'command': cmd
            }).error('Ignoring chmod command with undecoded mode argument')
            return

        return [
            agent_pb2.HypervisorToAgentCommand(
                command_id=command_id,
                chmod_request=agent_pb2.ChmodRequest(
                    path=cmd['path'],
                    mode=mode
                )
            )
        ]


class GetFileCommand(AgentCommandHandler):
    name = 'get-file'
    reports_progress = True

    def dispatch(self, command_id, cmd):
        # INFO, not debug: get-file is a known-flaky path (issues #3516, #2240)
        # and at the daemon's default INFO level we otherwise have no journal
        # trace of where a transfer stalled.
        self.job.log.with_fields({
            'path': cmd['path'],
            'outstanding_messages': self.job.outstanding_message_count
        }).info('Requesting file from agent')

        self.job._agent_path_for_get = cmd['path']
        self.job._blob_uuid = str(uuid4())
        self.job._blob_partial_file = open(
            blob.Blob.filepath(self.job._blob_uuid) + '.partial', 'wb')
        self.job._stat_result = None

        return [
            agent_pb2.HypervisorToAgentCommand(
                command_id=command_id,
                get_file_request=agent_pb2.GetFileRequest(
                    path=cmd['path']
                )
            )
        ]


AGENT_COMMAND_HANDLERS = [ExecuteCommand, PutBlobCommand, ChmodCommand, GetFileCommand]


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

        # In-flight get-file transfer state, set by GetFileCommand.dispatch().
        # Initialising them here is what lets _handle_stat_result() and
        # _handle_file_chunk() raise GetException('Unknown file transfer')
        # when no transfer is in flight, instead of AttributeError.
        self._agent_path_for_get = None
        self._blob_uuid = None
        self._blob_partial_file = None
        self._stat_result = None

        self.command_handlers = {
            cls.name: cls(self) for cls in AGENT_COMMAND_HANDLERS}

        # Progress tracking. in_flight_handler is the handler for the
        # command currently awaiting replies, which is what says
        # whether the progress timeout applies at all. _last_progress
        # is seeded when a command is dispatched, so the window
        # measures time since that command was sent rather than since
        # the connection opened.
        self.in_flight_handler = None
        self._last_progress = time.time()
        self._last_progress_persisted = 0.0

        self.ready = False
        self.welcomed = False
        self.log = LOG.with_fields({
            'instance': self.instance.uuid,
            'agent_operation': self.agentop.uuid
        })

    def execute(self):
        try:
            super().execute()
        finally:
            # An operation is popped from the instance's queue as soon as it
            # reaches EXECUTING (see Instance.agent_operation_next), so it is
            # never re-dispatched. If the executor exits for any reason with the
            # operation still EXECUTING -- a dropped connection or socket error
            # swallowed by the base execute(), an unexpected exception (e.g. a
            # database error from Blob.register in the get-file path), or the
            # execution deadline in _execute_inner -- fail it cleanly here
            # rather than leaving it orphaned in EXECUTING until the caller
            # times out. See issues #3516 and #2240.
            if self.agentop.state.value == AgentOperation.STATE_EXECUTING:
                self.log.error(
                    'Executor exited with the operation still executing; '
                    'marking it errored')
                self.agentop.state = AgentOperation.STATE_ERROR
                self.agentop.error = (
                    'sidechannel executor exited before the operation '
                    'completed')

    def _send_ping(self, sock):
        request = agent_pb2.HypervisorToAgentCommand(
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
        self.welcomed = True

    def _handle_execute_reply(self, reply):
        self.log.with_fields({
            'outstanding_messages': self.outstanding_message_count
        }).debug('...execute reply')
        self.observe_progress()
        result = {
            'command': 'execute-response',
            'command-line': self.command_cache[reply.command_id],
            'return-code': reply.execute_reply.exit_code
        }

        # Convert long stdouts and stderrs to blobs
        stdout = reply.execute_reply.stdout
        if len(stdout) > 10 * constants.KiB:
            b = blob.from_memory(stdout.encode('utf-8'))
            b.add_agent_output_reference(self.agentop.uuid, 'stdout')
            result['stdout_blob'] = str(b.uuid)
        else:
            result['stdout'] = stdout

        stderr = reply.execute_reply.stderr
        if len(stderr) > 10 * constants.KiB:
            b = blob.from_memory(stderr.encode('utf-8'))
            b.add_agent_output_reference(self.agentop.uuid, 'stderr')
            result['stderr_blob'] = str(b.uuid)
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

            yield agent_pb2.HypervisorToAgentCommand(
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
                yield agent_pb2.HypervisorToAgentCommand(
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
            yield agent_pb2.HypervisorToAgentCommand(
                command_id=command_id,
                file_chunk=agent_pb2.FileChunk(
                    offset=offset,
                    encoding=agent_pb2.FileChunk.BASE64,
                    payload=None
                )
            )

    def _handle_stat_result(self, reply):
        self.log.with_fields({
            'size': reply.stat_result.size,
            'mode': reply.stat_result.mode,
            'outstanding_messages': self.outstanding_message_count
        }).info('Received file stat from agent')

        if not self._blob_partial_file:
            self.log.with_fields({
                'outstanding_messages': self.outstanding_message_count
            }).error('Unknown file transfer')
            raise GetException('Unknown file transfer')

        # Below the guard deliberately: a reply for a transfer which is
        # not in flight is not progress on anything.
        self.observe_progress()

        sr = reply.stat_result
        self._stat_result = {
            'mode': sr.mode,
            'size': sr.size,
            'uid': sr.uid,
            'gid': sr.gid,
            'atime': sr.atime,
            'mtime': sr.mtime,
            'ctime': sr.ctime
        }

    def _handle_file_chunk(self, sock, reply):
        self.log.with_fields({
            'outstanding_messages': self.outstanding_message_count
        }).debug('...file chunk')

        if not self._blob_partial_file:
            self.log.with_fields({
                'outstanding_messages': self.outstanding_message_count
            }).error('Unknown file transfer')
            raise GetException('Unknown file transfer')

        # Below the guard deliberately: a chunk for a transfer which is
        # not in flight is not progress on anything.
        self.observe_progress()

        chunk = reply.file_chunk
        if chunk.encoding != agent_pb2.FileChunk.BASE64:
            self._send_replies_single_envelope(
                sock,
                [
                    agent_pb2.HypervisorToAgentCommand(
                        command_id=reply.command_id,
                        command_error=agent_pb2.CommandError(
                            error='unknown payload encoding')
                    )
                ]
            )
            raise GetException('Unknown chunk encoding')

        if chunk.payload:
            d = base64.b64decode(chunk.payload)
            self._blob_partial_file.write(d)
            self.log.debug(f'... wrote {len(d)} bytes to partial blob')
        else:
            self._blob_partial_file.close()

            if chunk.offset == 0:
                os.unlink(blob.Blob.filepath(self._blob_uuid) + '.partial')
                result = {
                    'stat_result': self._stat_result
                }

            else:
                b = blob.Blob.new(self._blob_uuid, time.time(), time.time())
                ao = copy.copy(self.affected_objects)
                ao.append(b)
                add_event_multi(
                    EVENT_TYPE_STATUS, ao, 'fetched content from instance',
                    extra={
                        'remote_stat_result': self._stat_result,
                        'transferred': chunk.offset,
                        'content_blob': b.uuid,
                        'instance_uuid': self.instance.uuid
                    })
                b.register()

                result = {
                    'stat_result': self._stat_result,
                    'content_blob': str(b.uuid)
                }

            self.agentop.add_result(self.command_count, result)
            self.num_results += 1
            self.command_count += 1

            # INFO so the successful completion of this known-flaky path is
            # visible in the journal at the default log level (issue #3516).
            self.log.with_fields({
                'transferred': chunk.offset,
                'content_blob': result.get('content_blob')
            }).info('Completed file transfer from agent')

            self._blob_partial_file = None
            self._blob_uuid = None
            self._stat_result = None
            self._agent_path_for_get = None
            self.ready = True

        self._send_replies_single_envelope(
            sock,
            [
                agent_pb2.HypervisorToAgentCommand(
                    command_id=reply.command_id,
                    file_chunk_reply=agent_pb2.FileChunkReply(
                        path=self._agent_path_for_get,
                        offset=chunk.offset
                    )
                )
            ]
        )

    def expire_if_out_of_budget(self):
        """Expire this operation if a caller-set timing budget is spent.

        Returns True if the executor should stop. Expiring before the
        caller returns is what makes this safe: execute()'s finally
        block only rewrites an operation which is still executing, so
        a terminal state set here is preserved.

        This replaces a fixed 900 second backstop. Two budgets are
        checked, and exhausting either is the same outcome -- expired,
        with a message saying which -- because both are numbers the
        caller chose rather than faults of the operation.
        """
        # The wall-clock deadline. Queue time and preflight time have
        # already been spent from it, so this can fire almost
        # immediately after dispatch.
        if self.agentop.deadline_passed():
            self.log.error(
                'Operation deadline passed while executing, aborting '
                'executor')
            self.agentop.expire(
                'the operation deadline passed while executing')
            return True

        # The progress timeout, which applies only while a command
        # which can actually report progress is in flight. This is what
        # detects the issue #3516 wedge, in tens of seconds rather than
        # the 900 the fixed backstop took.
        #
        # Note that self.last_data is not a progress signal and must
        # never be used as one: it is refreshed by any socket traffic,
        # and the executor pings every two seconds, so it never ages.
        # Progress is observed explicitly in the reply handlers via
        # observe_progress().
        if (self.in_flight_handler is None
                or not self.in_flight_handler.reports_progress
                or self.ready):
            return False

        window = self.agentop.effective_progress_timeout()
        if window is None:
            return False

        if time.time() - self._last_progress <= window:
            return False

        self.log.with_fields({
            'command': self.in_flight_handler.name,
            'progress_timeout': window
        }).error('No progress from agent, aborting executor')
        self.agentop.expire(
            f'no progress from the agent for {window} seconds')
        return True

    def observe_progress(self):
        """Record that the agent made forward progress just now.

        Called from the reply handlers rather than from the socket
        read loop, because any traffic at all -- a ping reply, most
        often -- refreshes self.last_data, and a liveness signal is
        not a progress signal.

        The in-memory timestamp moves on every call, and is what the
        progress timeout in _execute_inner() is measured against. The
        persisted attribute is throttled to one write every
        PROGRESS_PERSIST_INTERVAL seconds: it exists for a future
        node-local reaper reasoning about an executor which died,
        and a 100KiB-chunk transfer would otherwise write it hundreds
        of times a second.
        """
        now = time.time()
        self._last_progress = now

        if now - self._last_progress_persisted < PROGRESS_PERSIST_INTERVAL:
            return
        self._last_progress_persisted = now

        # The field mask is not optional. add_result() reads, merges
        # and writes the results column on this same row, and an
        # unmasked write here would push a stale snapshot of it over
        # a concurrent update.
        attrs = self.agentop._attributes()
        attrs.last_progress = now
        mariadb.update_agent_operation_attributes(
            attrs, fields=['last_progress'])

    def _abort_commands_if_terminal(self):
        """Drop the rest of an operation's commands if it has failed.

        The commands list is a fail-fast transaction: a put-blob whose
        transfer errored must never run its chmod. Expiry counts the
        same way as an error here -- an operation whose caller has run
        out of budget has no business continuing to the next command.
        """
        if self.agentop.state.value in (AgentOperation.STATE_ERROR,
                                        AgentOperation.STATE_EXPIRED):
            self.commands = []

    def _execute_inner(self, vsock):
        self._send_commands_single_envelope(
            vsock.sock,
            [
                agent_pb2.HypervisorToAgentCommand(
                    command_id=sf_random.random_id(),
                    hypervisor_welcome=agent_pb2.HypervisorWelcome(
                        version=util_general.get_version()
                    )
                )
            ]
        )
        self.last_data = time.time()
        connected_at = time.time()

        buffered = bytearray()
        while daemon.check_abort_path(self.abort_path):
            # If the agent never welcomes us the connection is not going to
            # become useful -- without this deadline we would spin here
            # forever, holding the executor slot for this instance and so
            # blocking every later operation. Exiting is safe: the operation
            # is still at the head of the instance's queue and dispatch will
            # be retried on a fresh connection.
            if not self.welcomed and time.time() - connected_at > 30:
                self.log.info(
                    'Agent did not welcome us within 30 seconds, aborting '
                    'executor for later retry')
                return

            if self.expire_if_out_of_budget():
                return

            if time.time() - self.last_data > 2:
                self._send_ping(vsock.sock)
                self.last_data = time.time()

            try:
                input = vsock.sock.recv(102400)
                if not input:
                    return

                self.last_data = time.time()
                buffered += input

                envelope = agent_pb2.AgentToHypervisor()
                try:
                    consumed = envelope.ParseFromString(buffered)
                except DecodeError:
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
                        self.observe_progress()
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
                                    vsock.sock, [out], register_as_outstanding=True)

                        except StopIteration:
                            self.chunk_iterator = None

                        if self.outstanding_message_count == 0:
                            self.ready = True
                        elif self.outstanding_message_count < 0:
                            self.log.with_fields({
                                'outstanding_messages': self.outstanding_message_count
                            }).error('Negative outstanding messages, aborting')
                            return

                    elif reply.HasField('chmod_reply'):
                        self.log.with_fields({
                            'outstanding_messages': self.outstanding_message_count
                        }).debug('...chmod reply')
                        self.ready = True

                    elif reply.HasField('stat_result'):
                        self._handle_stat_result(reply)

                    elif reply.HasField('file_chunk'):
                        try:
                            self._handle_file_chunk(vsock.sock, reply)

                        except GetException as e:
                            self.agentop.fail(str(e))

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
                            agent_pb2.HypervisorToAgentCommand(
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
                        handler = self.command_handlers.get(cmd['command'])
                        if not handler:
                            add_event_multi(
                                EVENT_TYPE_STATUS, self.affected_objects,
                                'unknown command', extra=cmd)
                            self.agentop.fail('unknown command')
                        else:
                            # The progress window measures from when
                            # this command was sent, and only applies
                            # while this handler's command is the one
                            # in flight.
                            self.in_flight_handler = handler
                            self._last_progress = time.time()
                            requests = handler.dispatch(command_id, cmd)
                            register_as_outstanding = handler.register_as_outstanding

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
                        self._abort_commands_if_terminal()

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
        self.executor_attempts = {}

        # Dispatcher liveness. The dispatch thread records the completion
        # time of each pass; the monitor management loop supervises it and
        # starts a replacement thread (a new generation) if passes stop
        # completing. The generation counter lets a wedged-then-recovered
        # old thread notice it has been replaced and exit without side
        # effects, preserving the one-executor-per-instance invariant.
        self.dispatcher_generation = 0
        self.dispatcher_last_pass = time.time()
        self.dispatcher_thread = None

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
        self.executor_attempts[instance_uuid] = time.time()

        # Note that returning early here (or indeed failing anywhere between
        # the operation being selected and the executor moving it to the
        # executing state) is safe: the operation remains at the head of the
        # instance's queue and will simply be dispatched again later.
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
        add_event(
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
                add_event(
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
        add_event(
            EVENT_TYPE_AUDIT, 'instance', instance_uuid,
            'side channel monitor instructed to exit')
        self.monitors[instance_uuid]['thread'].join(0.5)

        if not t['thread'].is_alive():
            del self.monitors[instance_uuid]
            daemon.clear_abort_path(t['object'].abort_path)
            add_event(
                EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                'side channel monitor finished')

    def _dispatch_loop(self, generation):
        """Dispatch queued agent operations to instances with ready agents.

        This deliberately runs in its own thread, separate from the monitor
        management loop in _run_inner(). Both loops make calls that can stall
        for a long time without raising -- libvirt domain audits, and gRPC
        database reads whose deadline-and-retry cascade can silently consume
        ~90 seconds per call when a channel subchannel wedges (the gRPC
        channel is thread-local, so a wedge affects exactly one thread).
        When dispatch shared the monitor loop's thread, any such stall
        starved agent operation dispatch for every instance on the node,
        which presented as an operation stuck in the queued state while the
        instance's agent was ready. In its own thread (and therefore with
        its own gRPC channel), dispatch is isolated from monitor management
        stalls, and vice versa.
        """
        util_concurrency.set_thread_name(f'dispatcher-{generation}')
        log = LOG.with_fields({'dispatcher_generation': generation})
        log.info('Dispatcher starting')

        last_heartbeat = time.time()
        while daemon.check_abort_path(self.abort_path):
            # If the supervisor has replaced us (because we stopped
            # completing passes, usually a wedged thread-local gRPC channel
            # that swallowed a call without ever firing its deadline), exit
            # without further side effects so we can never race the
            # replacement into a double dispatch.
            if generation != self.dispatcher_generation:
                log.warning(
                    'Dispatcher replaced by a newer generation, exiting')
                return

            try:
                self.reap_instance_executors()

                # Run jobs for healthy instances. For now we only support
                # running one job at once per instance.
                for instance_uuid in list(self.monitors.keys()):
                    if instance_uuid in self.executors:
                        continue

                    # Rate limit dispatch checks per instance. A failed
                    # dispatch (the executor couldn't connect, or the
                    # agent never welcomed us) leaves the operation at
                    # the head of the queue, so we would otherwise retry
                    # every loop iteration. The idle case matters just as
                    # much: agent_operation_next() costs an uncached
                    # instance attributes read per call, so an unthrottled
                    # check polls the database at 1Hz per ready instance.
                    last_attempt = self.executor_attempts.get(
                        instance_uuid, 0)
                    if time.time() - last_attempt < 5:
                        continue

                    # The monitor management loop can remove entries while
                    # we iterate our snapshot, so fetch defensively.
                    monitor = self.monitors.get(instance_uuid)
                    if not monitor:
                        continue
                    ready = monitor['object'].instance_ready
                    if ready not in [constants.AGENT_READY,
                                     constants.AGENT_READY_DEGRADED]:
                        continue

                    self.executor_attempts[instance_uuid] = time.time()
                    inst = instance.Instance.from_db(instance_uuid)
                    if not inst:
                        continue

                    agentop = inst.agent_operation_next()
                    if agentop:
                        if generation != self.dispatcher_generation:
                            # Replaced mid-pass (see above); the operation
                            # stays at the head of the queue for the new
                            # generation to dispatch.
                            log.warning(
                                'Dispatcher replaced by a newer generation, '
                                'exiting without dispatching')
                            return

                        inst.add_event(
                            EVENT_TYPE_AUDIT, 'dispatching agent operation',
                            extra={'agentoperation': agentop.uuid})
                        log.with_fields({
                            'instance': instance_uuid,
                            'agentoperation': agentop.uuid
                        }).info('Dispatching agent operation')

                        self.start_instance_executor(
                            instance_uuid, agentop)

                # Record pass completion for the supervisor in _run_inner,
                # and emit a periodic positive liveness signal so a silent
                # dispatcher is diagnosable from logs alone.
                self.dispatcher_last_pass = time.time()
                if time.time() - last_heartbeat > 300:
                    last_heartbeat = time.time()
                    log.with_fields({
                        'monitors': len(self.monitors),
                        'executors': len(self.executors)
                    }).info('Dispatcher heartbeat')

                time.sleep(1)

            except Exception as e:
                util_exceptions.ignore_exception('side channel dispatcher', e)
                time.sleep(1)

        log.info('Dispatcher stopping')

    def start_dispatcher(self):
        """Start a new dispatcher thread generation."""
        self.dispatcher_generation += 1
        generation = self.dispatcher_generation
        self.dispatcher_last_pass = time.time()
        self.dispatcher_thread = threading.Thread(
            target=self._dispatch_loop, args=(generation,), daemon=True,
            name=f'dispatcher-{generation}')
        self.dispatcher_thread.start()

    def supervise_dispatcher(self):
        """Replace the dispatcher if it has stopped completing passes.

        A dispatch pass normally completes in a few seconds. The known way
        for it to stop entirely without logging anything is a wedged
        thread-local gRPC channel where the completion queue no longer
        fires deadlines, so the blocked call never returns and never
        raises (observed taking ~21 minutes to self-recover at the TCP
        layer). A replacement thread gets a fresh thread-local channel,
        which recovers dispatch in bounded time; the generation counter
        stops the old thread acting further if it later unwedges.
        """
        if self.dispatcher_thread and not self.dispatcher_thread.is_alive():
            LOG.error('Dispatcher thread died, starting a replacement')
            self.start_dispatcher()
            return

        stalled = time.time() - self.dispatcher_last_pass
        if stalled > 120:
            LOG.with_fields({
                'stalled_seconds': int(stalled),
                'old_generation': self.dispatcher_generation
            }).error(
                'Dispatcher has not completed a pass in too long '
                '(wedged thread-local gRPC channel is the usual cause), '
                'starting a replacement thread with a fresh channel')
            self.start_dispatcher()

    def _run_inner(self):
        self.start_dispatcher()

        while daemon.check_abort_path(self.abort_path):
            try:
                self.wait_for_nodelock()

                self.supervise_dispatcher()
                self.reap_instance_monitors()

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

                    self.idle(1)

            except Exception as e:
                util_exceptions.ignore_exception('side channel monitor', e)
                time.sleep(1)

        LOG.info('Stopping')
        send_systemd_stopping()

        while self.monitors:
            LOG.info(f'There are {len(self.monitors)} threads remaining')
            self._request_all_threads_exit()
            if self.monitors:
                time.sleep(5)

        LOG.info(f'There are {len(self.monitors)} threads remaining')
        LOG.info('Stopped')


def main():
    util_exceptions.install_exception_tracking()
    daemon.write_pid_file('sidechannel')
    m = Monitor('sidechannel')

    while not daemon.health_check_nodelock():
        LOG.info('Waiting for nodelock daemon to be healthy')
        time.sleep(1)
    LOG.info('nodelock daemon reports healthy')

    m.run()

    daemon.force_clean_exit()
