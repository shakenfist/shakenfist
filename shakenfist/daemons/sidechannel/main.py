import base64
import copy
import errno
import functools
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
from shakenfist.config import config
from shakenfist import constants
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
from shakenfist.schema.object_types import ObjectType
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

# How often the executor's socket loop actually evaluates the timing
# budgets. The loop is not rate limited -- recv() returns as soon as a
# packet arrives, so an active transfer iterates thousands of times a
# second. Checking once a second is ample resolution for a 30 second
# window and bounds the cost of the checks themselves; the one database
# read a check can imply (resolving a NULL deadline's anchor) is paid
# at most once per executor, not once per check -- see the anchor cache
# in expire_if_out_of_budget() and issue #4014.
BUDGET_CHECK_INTERVAL = 1

# How often the executor reaper looks at a single instance's agent
# operation queue. Nothing it finds is urgent -- a dead executor's
# operation has already been abandoned, and a wedged one is out of
# budget either way -- and the peek is an uncached instance attributes
# read. Without this it would run for every instance on the node on
# every dispatcher pass, which is one read per instance per second: the
# cost the dispatch check's own rate limit exists to avoid.
EXECUTOR_REAP_INTERVAL = 30


class ConnectionFailed(Exception):
    ...


class PutException(Exception):
    ...


class GetException(Exception):
    ...


class SideChannelJob(util_concurrency.Job):
    def __init__(self, inst, abort_name=None):
        super().__init__()
        self.instance = inst
        self.instance_ready = constants.AGENT_NEVER_TALKED
        self.thread_name = str(self.instance.uuid)

        # abort_name is what keeps the two job types for one instance
        # from sharing an abort file, and it is a constructor argument
        # rather than something a subclass overwrites afterwards
        # because both halves of doing it later are wrong. The base
        # class clears whatever path it derives, so an executor built
        # while the daemon is shutting down would clear the monitor's
        # abort file and un-stop it; and a subclass which reassigns
        # abort_path after super().__init__() leaves the derivation
        # depending on the order of two assignments in two files,
        # which is how the monitor and the executor came to share one
        # file in the first place. Sharing matters because the reaper
        # sets this path to stop a wedged executor: with one file that
        # also stops the instance's monitor, and the monitor's restart
        # clears the file again -- possibly before the wedged
        # executor's one second poll has read it, leaving it wedged
        # with the instance's executor slot held.
        self.abort_path = (
            f'/run/sf/sidechannel-{abort_name or self.instance.uuid}.abort')
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
            if not daemon.check_abort_path(self.abort_path):
                self.log.with_fields({
                    'abort_path': self.abort_path
                }).debug('Abort path set, exiting')
                return
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
        # No abort_name: the monitor keeps the historical
        # per-instance path, because operators and the daemon's own
        # shutdown path both know it by that name.
        super().__init__(inst)
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
    # Read by SideChannelExecutorJob.expire_if_out_of_budget(), which
    # only applies the progress timeout while a command that can
    # actually report progress is in flight.
    reports_progress = False
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


def operation_is_retryable(agentop):
    """True if every command in this operation is safe to run again.

    Retryability is a property of the whole command list, not of the
    command which happened to be in flight when the attempt was
    abandoned. That is deliberate, and it is the reading a later
    reader is most likely to think is a mistake: a retry restarts the
    command list at index 0, so an [execute, get-file] operation which
    stalls in the get-file would re-run the execute on its second
    attempt -- exactly the side effect the agent cannot take back, and
    exactly what phase 0 decision 6 forbids. The cheaper per-command
    reading ("the stalled command is a get-file, get-file is
    retryable, so retry") would ship a smaller diff and be wrong.

    No API endpoint builds a mixed command list today, so the two
    readings currently agree and no test we can write demonstrates the
    difference. This is the version which stays correct when one does.

    An unrecognised command name is not retryable: we cannot know what
    running it a second time would do. Neither is an empty command
    list, which all() would otherwise call retryable vacuously: a
    second attempt at nothing cannot make progress, so it would burn
    dispatches to the attempt cap and then report a timing budget as
    the reason.
    """
    if not agentop.commands:
        return False

    handlers = {cls.name: cls for cls in AGENT_COMMAND_HANDLERS}
    for cmd in agentop.commands:
        handler = handlers.get(cmd.get('command'))
        if handler is None or not handler.retryable:
            return False
    return True


def resolve_abandoned_operation(agentop, reason, terminal):
    """Retry an abandoned attempt at an operation, or end it.

    This is the single place the retry decision is made. It is called
    by the executor's two exit paths and by the node local reaper, so
    it deliberately depends on nothing but the operation itself -- the
    reaper has no executor job to hand it.

    reason says what abandoned the attempt, and is recorded either
    way. terminal is the outcome to apply when no retry is possible,
    and belongs to the caller rather than to this function because the
    two callers differ: a stall which cannot retry expires, since the
    progress timeout is a budget the caller set, while an executor
    exit which cannot retry errors, preserving the message phase 4
    gave it. Callers pass agentop.fail, or agentop.expire with its
    budget bound via functools.partial -- the caller is the one which
    knows which budget it detected running out, and expire() records
    that as the expiry_reason a client can branch on.

    Retry is for a stalled attempt and never for a failed one. A
    passed deadline therefore never retries -- retrying spends time
    nobody is waiting for, because the caller's budget is the thing
    which just ran out -- and an agent reported command error never
    arrives here at all, because _handle_command_error() fails the
    operation outright.

    The attempt counter is written on dispatch rather than here, so an
    operation reads attempts == 1 while its first attempt is running
    and the cap is a count of dispatches. Returns True if the
    operation was requeued.
    """
    if not operation_is_retryable(agentop):
        terminal(f'{reason}, and the operation cannot be safely retried')
        return False

    if agentop.deadline_passed():
        terminal(f'{reason}, and the operation deadline has passed')
        return False

    attempts = agentop.attempts
    if attempts >= config.AGENT_OPERATION_MAX_ATTEMPTS:
        terminal(f'{reason}, after {attempts} attempts')
        return False

    # The next attempt rewrites every result index it reaches, so the
    # only rows cleared here are ones the retry is about to replace or
    # was never going to reach. Leaving them would present the caller
    # with a content_blob from an abandoned attempt as though it were
    # this operation's result.
    agentop.clear_results()
    agentop.state = AgentOperation.STATE_QUEUED

    # Recorded against the instance as well as the operation, for the
    # reason AgentOperation.expire() spells out: the operation is
    # swept for hard deletion once it does reach a terminal state, and
    # the instance is where an operator looks for the history of what
    # was run against a machine.
    add_event_multi(
        EVENT_TYPE_AUDIT,
        [(AgentOperation.object_type, agentop.uuid),
         (ObjectType.INSTANCE, agentop.instance_uuid)],
        'agent operation requeued for retry',
        extra={
            'reason': reason,
            'attempts': attempts,
            'max_attempts': config.AGENT_OPERATION_MAX_ATTEMPTS
        })
    return True


class SideChannelExecutorJob(SideChannelJob):
    def __init__(self, inst, agentop):
        # Keyed by instance rather than by operation, which matches
        # the one-executor-per-instance invariant the dispatcher
        # enforces and means the file is reclaimed by the next
        # executor's clear rather than accumulating one per operation
        # in /run/sf.
        super().__init__(inst, abort_name=f'executor-{inst.uuid}')
        self.agentop = agentop
        self.affected_objects = [self.instance, self.agentop]
        self.thread_name = f'{self.instance.uuid}-{self.agentop.uuid}'

        # The executor's own working copy, deliberately not the
        # operation's list. AgentOperation.commands returns the
        # underlying list by reference, and that list is the one the
        # process wide object cache holds: get_agent_operation() caches
        # the AgentOperationData model, and frozen=True on that model
        # stops attribute assignment rather than mutation of a list
        # field's contents. Popping through an alias of it therefore
        # drained the operation itself, and two things broke.
        # operation_is_retryable() opens by refusing an empty command
        # list, so a single command get-file -- the case issue #3516 is
        # about -- called itself unretryable the moment its only
        # command went out, inverting the decision this exists to make.
        # And every later reader on the node saw the drained list for
        # as long as the cache entry lived, the reaper's own
        # AgentOperation.from_db() included.
        self.commands = list(agentop.commands)
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
        self._last_budget_check = 0.0

        # The State an anchored deadline (a NULL column's default, or
        # the no-budgets-at-all backstop) resolves against, read at
        # most once for the life of this executor rather than on every
        # budget check. See expire_if_out_of_budget().
        self._deadline_anchor = None

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
            # An operation's queue entry survives until the operation reaches a
            # terminal state (see Instance.agent_operation_next), so an
            # operation left in EXECUTING here is one nothing will finish and
            # nothing will re-dispatch: the queue entry is still at the head,
            # but the dispatcher skips a head which is already executing. The
            # exit is reached by a dropped connection or socket error swallowed
            # by the base execute(), by an unexpected exception (e.g. a
            # database error from Blob.register in the get-file path), or by
            # the execution deadline in _execute_inner. Resolve it here rather
            # than leaving it orphaned until the caller times out: the attempt
            # got nowhere rather than failing, so it is retried when the
            # command list allows it and the budgets have room, and errors
            # otherwise with the message phase 4 gave it. See issues #3516 and
            # #2240.
            if self.agentop.state.value == AgentOperation.STATE_EXECUTING:
                self.log.error(
                    'Executor exited with the operation still executing; '
                    'resolving it')
                resolve_abandoned_operation(
                    self.agentop,
                    'sidechannel executor exited before the operation '
                    'completed',
                    terminal=self.agentop.fail)

            # Unconditional, and outside the guard above: a half
            # finished get-file must not survive this exit whatever the
            # operation's state, and the call is a no-op unless a
            # transfer is actually in flight (the success path in
            # _handle_file_chunk() has already nilled it). It matters
            # more now than it did: a retried operation runs
            # GetFileCommand.dispatch() again, which mints a fresh blob
            # uuid, so without this an operation whose connection drops
            # mid-transfer leaves one orphaned .partial file per attempt
            # in blob storage for the cleaner to find. The budget path
            # in expire_if_out_of_budget() already does this; this exit
            # did not.
            self._abandon_get_file_transfer()

    def _handle_command_error(self, reply):
        """Fail the operation when the agent reports a command error.

        The base implementation only emits an event, which is right
        for the monitor job (it has no operation to fail) and was
        survivable for the executor while a fixed backstop eventually
        tore the connection down. It is not survivable now. The
        operation stays EXECUTING with a command in flight and
        self.ready False, so the next thing to notice is a timing
        budget, and the caller is told "no progress from the agent"
        about an agent which has just told us in detail what went
        wrong. This phase's whole distinction is that error means the
        operation failed and expired means the caller's budget ran
        out; this is a failure.

        Setting ready lets the socket loop take its ordinary exit --
        say goodbye, disconnect -- rather than spinning to a budget.
        That exit only marks an operation complete if it is still
        executing, so it cannot overwrite the error recorded here.
        """
        super()._handle_command_error(reply)
        self.agentop.fail(
            f'agent reported a command error: {reply.command_error.error}')
        self._abort_commands_if_terminal()
        self.ready = True

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
        """Resolve this operation if a caller-set timing budget is spent.

        Returns True if the executor should stop. Two budgets are
        checked, and which one ran out decides what happens, because
        since this phase they are no longer the same kind of event.

        An exhausted wall-clock deadline is final: the caller's time
        is gone, so there is nothing a further attempt could deliver
        in time, and the operation expires. A progress timeout says
        only that this attempt got nowhere, so it goes through
        resolve_abandoned_operation() and may return the operation to
        queued for another attempt; it expires when it cannot,
        because the timeout is still a number the caller chose rather
        than a fault of the operation.

        Writing the new state before returning is what makes both
        safe. execute()'s finally block only rewrites an operation
        which is still executing, so it cannot overwrite a terminal
        state set here, and it cannot overwrite a requeue either --
        queued is not executing.

        This replaced a fixed 900 second backstop.
        """
        # Rate limited, because the caller is the socket loop and the
        # checks below are work; see BUDGET_CHECK_INTERVAL.
        now = time.time()
        if now - self._last_budget_check < BUDGET_CHECK_INTERVAL:
            return False
        self._last_budget_check = now

        # The wall-clock deadline. Queue time and preflight time have
        # already been spent from it, so this can fire almost
        # immediately after dispatch.
        #
        # A NULL deadline -- a row written by an API server which
        # predates deadlines -- resolves the server default against the
        # operation's state row, and reading that is an uncached
        # GetObjectState round trip. Unanchored, this check is
        # therefore a fixed-rate database poll of up to 1/s per live
        # executor for the whole life of the operation (issue #4014).
        # The anchor cannot move while this executor exists: dispatch
        # and reaping for an instance serialise in this one process,
        # and a retry builds a new executor. So read it once and hand
        # it to every later check. The state in hand at the first check
        # is normally still queued, which makes the deadline this
        # enforces at most tighter than the per-read resolution it
        # replaces -- and it is the same anchor agent_operation_next()
        # already checked against at dispatch. An operation which needs
        # no anchor -- a stored deadline, or the 0.0 sentinel licensed
        # by a live progress timeout -- never reaches the read at all,
        # and deadline_passed() does not read state when its own
        # resolution short-circuits. The both-budgets-disabled backstop
        # (issue #4074) anchors here too.
        if (self._deadline_anchor is None
                and self.agentop.deadline_needs_state_anchor()):
            self._deadline_anchor = self.agentop.state
        if self.agentop.deadline_passed(state=self._deadline_anchor):
            self.log.error(
                'Operation deadline passed while executing, aborting '
                'executor')
            self.agentop.expire(
                'the operation deadline passed while executing',
                AgentOperation.EXPIRY_REASON_DEADLINE)
            self._abandon_get_file_transfer()
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

        # A stall is the case retry exists for: nothing failed, the
        # attempt merely got nowhere. The terminal outcome when it
        # cannot be retried stays expiry, because the progress timeout
        # is a budget the caller chose. The executor stops either way
        # -- a requeued operation is dispatched afresh, by a new
        # executor, from the head of the instance's queue.
        resolve_abandoned_operation(
            self.agentop,
            f'no progress from the agent for {window:g} seconds',
            terminal=functools.partial(
                self.agentop.expire,
                budget=AgentOperation.EXPIRY_REASON_PROGRESS))
        self._abandon_get_file_transfer()
        return True

    def _abandon_get_file_transfer(self):
        """Tear down a half finished get-file transfer.

        A no-op unless one is in flight. GetFileCommand.dispatch()
        opens a .partial file in the blob store and hands this job
        ownership of it; the success path in _handle_file_chunk()
        closes it and either registers or unlinks it. Every abnormal
        exit used to leave it open on disk until the job was collected
        and the cleaner swept it after CLEANER_DELAY * 2, which
        mattered little when the only route here was a 900 second
        backstop. The progress timeout reaches it in tens of seconds
        instead, so a wedged guest during a large get-file would
        otherwise leave partial files occupying blob storage
        routinely rather than rarely.
        """
        if not self._blob_partial_file:
            return

        partial = blob.Blob.filepath(self._blob_uuid) + '.partial'
        try:
            self._blob_partial_file.close()
            os.unlink(partial)
        except OSError as e:
            # Never fatal: this is cleanup on a path which is already
            # abandoning the operation, and the cleaner sweeps what we
            # leave behind.
            self.log.with_fields({
                'error': str(e), 'partial': partial
            }).warning('Failed to remove partial blob file')

        self._blob_partial_file = None
        self._blob_uuid = None
        self._stat_result = None
        self._agent_path_for_get = None

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

        # The persist is bookkeeping for a reaper which does not exist
        # yet, so it must never be able to fail an in-flight transfer.
        # Without this a DatabaseUnavailable would propagate out of the
        # reply handler, out of _execute_inner(), and be turned into an
        # errored operation by execute()'s finally block. The stamp is
        # moved only on success, so a failed write is retried on the
        # next call rather than suppressed for a whole interval.
        try:
            self.agentop.record_progress(now)
            self._last_progress_persisted = now
        except Exception as e:
            self.log.with_fields({'error': str(e)}).warning(
                'Failed to persist agent operation progress')

    def _abort_commands_if_terminal(self):
        """Drop the rest of an operation's commands if it has failed.

        The commands list is a fail-fast transaction: a put-blob whose
        transfer errored must never run its chmod. Expiry counts the
        same way as an error here -- an operation whose caller has run
        out of budget has no business continuing to the next command.
        Any half finished get-file transfer goes with them, since
        nothing will ever complete it.

        The two states tested are deliberately not the whole of
        AgentOperation.TERMINAL_STATES. complete is excluded because
        an operation only reaches it once its final command has
        finished, so there is nothing left to abort. deleted is
        excluded because abandoning a command sequence part way
        through leaves the guest in a state no caller asked for --
        a delete says the record is unwanted, not that a half applied
        change should be left in place -- and because deciding
        otherwise is a behaviour change this phase has no way to test
        end to end. That gap is pre-existing rather than introduced
        here, and interrupting live work belongs with the node-local
        reaper in phase 5 of
        docs/plans/PLAN-agent-operation-deadlines.md.
        """
        if self.agentop.state.value in (AgentOperation.STATE_ERROR,
                                        AgentOperation.STATE_EXPIRED):
            self.commands = []
            self._abandon_get_file_transfer()

    def _dispatch_next_command(self, sock):
        """Send the next command in the operation to the agent.

        Extracted from the socket loop so the progress window's start
        can be tested: it is only reachable through a live vsock
        connection otherwise.
        """
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
                # The progress window only applies while this handler's
                # command is the one in flight, so record that before
                # dispatch. The window itself is not started here:
                # dispatch() can block for a long time (PutBlobCommand
                # fetches the blob if preflight did not), and the window
                # measures time waiting on the agent, not time spent
                # moving bytes around the hypervisor. Starting it here
                # would expire a large put-blob before the agent had
                # been sent anything at all.
                self.in_flight_handler = handler
                requests = handler.dispatch(command_id, cmd)
                register_as_outstanding = handler.register_as_outstanding

            if requests:
                # The assignment below is a state machine transition,
                # and no terminal state has an edge to executing, so
                # an unguarded write raises InvalidStateException out
                # of the executor thread. It is normally unreachable:
                # an instance runs one executor at a time and the
                # dispatcher will not dequeue against a live one. The
                # exception is a replaced dispatcher generation, whose
                # descheduled predecessor can still call
                # agent_operation_next() -- which since this phase
                # expires an over-deadline queued head rather than
                # merely reading it again. Abandon the operation
                # instead. Dropping the commands here rather than
                # leaving it to the finally below matters: that only
                # fires for error and expired, and self.ready is true
                # for us to have been called at all, so a complete or
                # deleted operation with commands left would be
                # re-dispatched on every pass and the loop would never
                # reach its exit.
                state_value = self.agentop.state.value
                if state_value in AgentOperation.TERMINAL_STATES:
                    self.log.with_fields({
                        'state': state_value
                    }).warning('Operation became terminal during dispatch, '
                               'not sending its command')
                    self.commands = []
                    self._abandon_get_file_transfer()
                    return

                extra = copy.copy(cmd)
                extra['command_id'] = command_id
                add_event_multi(
                    EVENT_TYPE_STATUS, self.affected_objects,
                    'executing agent command', extra=extra)

                # An attempt is counted here, on the transition into
                # executing, and nowhere else. Counting it where the
                # dispatcher starts the thread instead looks simpler but
                # measures the wrong thing: an executor which never
                # reaches the agent -- NoSuchChannel or a socket error
                # swallowed by the base execute(), or the thirty second
                # wait for a welcome which never comes -- leaves the
                # operation queued for a later dispatch, and the
                # dispatcher retries every five seconds, so an instance
                # with a flaky agent channel would burn the whole cap
                # inside fifteen seconds without a single command having
                # been sent. The first genuine stall would then be
                # terminal immediately, which is the opposite of what
                # this cap is for.
                #
                # The guard makes this once per attempt rather than once
                # per command: the assignment below is a no-op for the
                # second and later commands of one operation (
                # baseobject._state_update() returns early when the value
                # is unchanged), and those are the same attempt.
                #
                # Ordering is safe by construction. Everything which
                # reads the counter -- resolve_abandoned_operation(),
                # from either executor exit or from the reaper -- can
                # only be reached with the operation already EXECUTING,
                # so the increment for this attempt has always happened
                # before its own comparison against the cap.
                if state_value != AgentOperation.STATE_EXECUTING:
                    self.agentop.record_attempt()
                self.agentop.state = AgentOperation.STATE_EXECUTING

                self.log.with_fields({
                    'outstanding_messages': self.outstanding_message_count,
                    'register_as_outstanding': register_as_outstanding
                }).debug(f'Sending {len(requests)} messages')
                self._send_commands_single_envelope(
                    sock, requests,
                    register_as_outstanding=register_as_outstanding)

                # The command is on the wire now, so the progress window
                # starts now. self.ready goes False in the same breath,
                # which is what arms the check at all.
                self._last_progress = time.time()
                self.ready = False

        finally:
            self._abort_commands_if_terminal()

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
                            self._abort_commands_if_terminal()
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
                    self._dispatch_next_command(vsock.sock)

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
        self.reaper_attempts = {}

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
        for instance_uuid in list(self.monitors.keys()):
            t = self.monitors[instance_uuid]
            if not t['thread'].is_alive():
                t['thread'].join(1)
                del self.monitors[instance_uuid]

                # The reaper only ever examines instances which have a
                # monitor, so its rate limiter has nothing to say about
                # one which does not. Dropping the entry here keeps the
                # dictionary the same size as self.monitors instead of
                # growing for the daemon's lifetime on a node with
                # instance churn, and costs nothing when the monitor
                # comes back: an absent entry reads as zero, which is
                # what makes the first pass after a restart immediate.
                self.reaper_attempts.pop(instance_uuid, None)

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

        # Registered before the thread starts, not after. The executor
        # is what the reaper reads to tell "nothing is working on this
        # operation" from "something is": between start() and this
        # assignment the operation can already be EXECUTING with no
        # entry here, and a reaper running in that window would take
        # case one and requeue an operation which is actively running.
        # Within one dispatcher thread that window cannot be observed,
        # but supervise_dispatcher() deliberately leaves a replaced
        # generation alive, so the old thread can be here while the new
        # generation reaps. thread_ident is therefore read after the
        # start, since it does not exist before it.
        self.executors[instance_uuid] = {
            'object': sc_obj,
            'thread': sc_thread,
            'instance_uuid': instance_uuid
        }

        # A start() which raises (RuntimeError, when the process cannot
        # make another thread) must not leave the entry behind. The
        # sweep skips an unstarted thread rather than joining it, so
        # the entry would never be collected, and while it sits there
        # the instance looks to the reaper like it has a live executor
        # -- so an operation left executing on this instance would
        # never be resolved again for the lifetime of the daemon.
        try:
            sc_thread.start()
        except RuntimeError:
            del self.executors[instance_uuid]
            raise

        add_event(
            EVENT_TYPE_AUDIT, 'instance', instance_uuid,
            'side channel executor started',
            extra={
                'thread_ident': sc_thread.ident
            })

    def reap_instance_executors(self):
        """Sweep finished executors, and free the queues they left stuck.

        An operation's queue entry survives until the operation reaches a
        terminal state (see Instance.agent_operation_next), which is what
        makes dispatch crash safe -- but it also means an operation left
        in EXECUTING with nothing working on it blocks its instance's
        queue for as long as it stays there. Nothing in the queue itself
        can tell a live executor from a dead one. This node can: the
        instance is placed here, its executor is a thread in this
        process, and so the absence of that thread is direct evidence
        rather than an inference. Converting that evidence back into a
        queue which drains is this reaper's whole purpose.
        """
        all_executors = list(self.executors.keys())
        for executor_id in all_executors:
            t = self.executors[executor_id]

            # ident is None until the thread has been started, and
            # is_alive() is False then too, so testing liveness alone
            # would take this branch for an entry which is registered
            # but not yet running and join() would raise "cannot join
            # thread before it is started". That entry exists on
            # purpose -- registering before start() is what stops a
            # replaced dispatcher generation reaping an operation which
            # is about to run -- so the window has to be tolerated
            # here rather than closed there.
            if t['thread'].ident is None:
                continue

            if not t['thread'].is_alive():
                t['thread'].join(1)

                # The reaper below sets this executor's abort path to
                # stop a wedged thread, and nothing else clears it.
                # Clearing it once the thread is confirmed dead is what
                # keeps /run/sf from accumulating a file per reaped
                # executor; the next executor for this instance would
                # otherwise start life already aborted if its
                # constructor's clear were ever removed.
                daemon.clear_abort_path(t['object'].abort_path)

                # Let the reaper look at this instance on the next
                # pass rather than making it wait out the rate limit.
                # An executor which has just died is the case the
                # reaper exists for, and the peek it costs is one, so
                # keeping the limiter's clock running here would block
                # the instance's queue for up to EXECUTOR_REAP_INTERVAL
                # seconds for no saving worth having.
                self.reaper_attempts.pop(executor_id, None)

                add_event(
                    EVENT_TYPE_AUDIT, 'instance', t['instance_uuid'],
                    'side channel executor ended',
                    extra={
                        'thread_ident': t['thread'].ident
                    })
                del self.executors[executor_id]

        # Deliberately after the sweep above, so an executor whose thread
        # ended without resolving its operation (the process was killed
        # between the agent's last reply and the finally block, say) is
        # handled in the same pass rather than the next one.
        for instance_uuid in list(self.monitors.keys()):
            self._resolve_stuck_queue_head(instance_uuid)

    def _resolve_stuck_queue_head(self, instance_uuid):
        """Resolve an operation this instance's agent queue is stuck behind.

        Three cases, and only three. Two of them are the absence of a
        thread, which is directly observable; the third is a wall-clock
        deadline, which is an absolute timestamp and so cannot be wrong
        about a thread which is still making progress. A live executor
        inside its budgets is left entirely alone, because the progress
        timeout is that executor's own job and it holds state this
        method does not.

        That leaves two shapes this cannot see, both of which need
        evidence a node-local reaper does not have. An instance with no
        monitor entry is never examined at all, so an operation which
        was executing when its monitor died waits for the monitor to be
        restarted (which happens within 30 seconds while the instance
        still has an agent channel, and never once it does not). And an
        operation created with deadline_seconds=0 alongside a live
        progress timeout asked for no wall-clock budget, so a live
        executor wedged before it connects holds that instance's
        executor slot with nothing able to prove it is stuck. That
        second hole used to include operations with no budget at all,
        wedged or healthily running a guest command forever (issue
        #4074); effective_deadline() now bounds those with the
        AGENT_OPERATION_MAX_DEADLINE backstop, so case two below
        eventually resolves them.
        """
        # The monitor management loop can remove entries while the
        # dispatcher iterates its snapshot, so fetch defensively.
        monitor = self.monitors.get(instance_uuid)
        if not monitor:
            return

        # Rate limited for the reason EXECUTOR_REAP_INTERVAL records.
        # Note that a restarted daemon starts with an empty dictionary,
        # so the first pass after a restart -- the case which most needs
        # this reaper -- is never delayed.
        last_attempt = self.reaper_attempts.get(instance_uuid, 0)
        if time.time() - last_attempt < EXECUTOR_REAP_INTERVAL:
            return
        self.reaper_attempts[instance_uuid] = time.time()

        # The same cheap unlocked peek Instance.agent_operation_next()
        # opens with, and for the same reason: this runs for every
        # instance on the node at the top of every dispatcher pass, so an
        # instance with nothing queued must not cost an operation read at
        # all. The monitor's own instance object is reused rather than
        # re-read, so the idle path is one attributes read and nothing
        # else.
        inst = monitor['object'].instance
        queue = inst.agent_operations.get('queue', [])
        if not queue:
            return

        agentop = AgentOperation.from_db(queue[0])
        if not agentop:
            # A queue entry with no backing operation. Retiring it takes
            # the instance's attribute lock, which this method
            # deliberately does not; agent_operation_next() does it on
            # the dispatch path instead.
            return

        # Read once and passed to deadline_passed(), which would
        # otherwise resolve a NULL deadline's anchor with a second
        # uncached GetState.
        state = agentop.state
        if state.value != AgentOperation.STATE_EXECUTING:
            return

        log = LOG.with_fields({
            'instance': instance_uuid,
            'agentoperation': agentop.uuid
        })
        executor = self.executors.get(instance_uuid)
        if not executor:
            # Case one: nothing is working on this operation. Either this
            # daemon restarted while it was executing, so the executor
            # died with the process and no finally block ever ran, or the
            # thread was swept out by the loop above without resolving
            # it. The evidence either way is the absence of the thread.
            #
            # The terminal outcome is fail(), not expire(), because
            # decision 3 splits the two on what went wrong rather than
            # on who noticed: a stall which cannot be retried expires,
            # an executor which went away errors. This is an executor
            # which went away -- the only difference from the exit
            # handled in SideChannelExecutorJob.execute()'s finally is
            # that the process died before the finally could run, and
            # the same failure should not report differently for that.
            # The difference is not cosmetic: expired is in
            # constants.FINAL_OBJECT_STATES and error is not, so
            # expiring here would sweep the daemon-restart case for
            # hard deletion while the finally case persisted, and the
            # restart is the one an operator most wants to still be
            # able to see.
            log.warning(
                'Agent operation is executing with no executor; resolving it')
            resolve_abandoned_operation(
                agentop,
                'no sidechannel executor was running for this operation',
                terminal=agentop.fail)
            return

        if not agentop.deadline_passed(state=state):
            # Case three: a live executor which still has wall-clock
            # budget. Leave it entirely alone.
            return

        # Case two: a live executor whose operation is out of wall-clock
        # budget. The executor checks both of its budgets itself, so
        # reaching here means it is wedged somewhere neither is evaluated
        # -- the pre-connection wait in SideChannelJob.execute(), which
        # blocks on the console log appearing before _execute_inner() is
        # ever entered.
        #
        # Resolve before aborting, not after. The executor's finally
        # block only rewrites an operation which is still EXECUTING, so
        # resolving first means the thread we are about to stop cannot
        # overwrite this verdict on its way out.
        log.warning(
            'Agent operation deadline passed while its executor was wedged; '
            'resolving it and aborting the executor')
        resolve_abandoned_operation(
            agentop,
            'the sidechannel executor was wedged and made no progress',
            terminal=functools.partial(
                agentop.expire,
                budget=AgentOperation.EXPIRY_REASON_DEADLINE))
        daemon.set_abort_path(
            executor['object'].abort_path,
            'from the side channel executor reaper')

    def _request_all_threads_exit(self):
        LOG.info('Requesting all threads exit')

        # Signal everything first, then join. Two reasons, and the
        # first is a correctness one this phase created.
        #
        # Until this phase an executor and its instance's monitor
        # shared one abort file, so the monitors loop below stopped
        # every executor as a side effect. Now that each has its own
        # path, an executor is only signalled by its own
        # _request_thread_exit() call -- and that method joins and
        # deletes out of self.monitors whichever dictionary the entry
        # came from, so it raises KeyError as soon as the monitors
        # loop has already removed that instance's entry, and there is
        # no try/except at the call site. The first executor would be
        # signalled and every executor after it would not, where
        # previously the shared file meant all of them were. That
        # bug is #3931, fixed separately in b915cb018 because it
        # predates this plan; this loop means shutdown does not
        # depend on which version of that method is in the tree.
        #
        # The second reason is plain: _request_thread_exit() joins for
        # half a second per thread, so signalling inside that loop
        # makes each thread's notice wait on the previous thread's
        # join. Threads which are told to stop together stop together.
        for t in list(self.monitors.values()) + list(self.executors.values()):
            daemon.set_abort_path(
                t['object'].abort_path, 'from _request_all_threads_exit')

        for instance_uuid in list(self.monitors.keys()):
            self._request_thread_exit(
                instance_uuid, self.monitors, 'monitor')

        for instance_uuid in list(self.executors.keys()):
            self._request_thread_exit(
                instance_uuid, self.executors, 'executor')

    def _request_thread_exit(self, instance_uuid, threads, thread_type):
        # threads is the dictionary the record lives in (self.monitors or
        # self.executors), so the join, the deletion and the audit events
        # all follow the thread actually being shut down.
        t = threads.get(instance_uuid)
        if not t:
            return

        daemon.set_abort_path(
            t['object'].abort_path, 'from _request_thread_exit')
        add_event(
            EVENT_TYPE_AUDIT, 'instance', instance_uuid,
            f'side channel {thread_type} instructed to exit')
        t['thread'].join(0.5)

        if not t['thread'].is_alive():
            del threads[instance_uuid]
            daemon.clear_abort_path(t['object'].abort_path)
            add_event(
                EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                f'side channel {thread_type} finished')

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
                            instance_uuid, self.monitors, 'monitor')

                    self.idle(1)

            except Exception as e:
                util_exceptions.ignore_exception('side channel monitor', e)
                time.sleep(1)

        LOG.info('Stopping')
        send_systemd_stopping()
        self._wait_for_all_threads_exit()
        LOG.info('Stopped')

    def _wait_for_all_threads_exit(self):
        while self.monitors or self.executors:
            LOG.info(f'There are {len(self.monitors) + len(self.executors)} '
                     'threads remaining')
            self._request_all_threads_exit()
            if self.monitors or self.executors:
                time.sleep(5)

        LOG.info('There are 0 threads remaining')


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
