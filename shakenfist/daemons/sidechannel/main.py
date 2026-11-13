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
from shakenfist import mariadb
from shakenfist.operations.agentoperation import AgentOperation
from shakenfist.operations.agentoperation import CASCADE_EVENT_GAVE_UP
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

# How far past its deadline an operation must be before the reaper
# expires it for never having been queued. The preflight task runs on
# this node and checks the same deadline immediately before promoting
# the operation, so the reaper only races a live task if that check and
# the promotion are further apart than this. They are adjacent
# statements, so a minute is ample.
UNPROMOTED_HEAD_GRACE = 60

# How often the dispatcher asks one instance whether it has an agent
# operation to run. A failed dispatch (the executor couldn't connect, or
# the agent never welcomed us) leaves the operation at the head of the
# queue, so without this we would retry every loop iteration. The idle
# case matters just as much: agent_operation_next() opens with an
# uncached instance attributes read, so an unthrottled check polls the
# database at 1Hz per ready instance.
DISPATCH_CHECK_INTERVAL = 5

# How often we retry starting a monitor for an instance which libvirt
# says is running but which has no monitor thread. Each attempt costs an
# instance attributes read for vsock_cid(), so an instance which will
# never have an agent channel -- a guest with no sf-agent2 side channel,
# or one whose CID has not been published yet -- must not be retried on
# every pass of the monitor management loop.
MONITOR_START_INTERVAL = 30

# Together, the three intervals above set what one instance costs this
# daemon in GetInstanceAttributes reads while nothing is happening to it:
# 1/DISPATCH_CHECK_INTERVAL for the dispatch check plus
# 1/EXECUTOR_REAP_INTERVAL for the reaper, and 1/MONITOR_START_INTERVAL
# instead of both while it has no monitor. That arithmetic is the
# per_instance_qps term of the (GetInstanceAttributes, sidechannel) entry
# in shakenfist/data/database_load_budget.yaml, which is why these are
# named constants rather than literals at their use sites: the budget is
# derived from them, and tools/derive-database-load-budget.py restates
# them so a drift here shows up as a failing unit test rather than as a
# budget term nobody can source. See issue #4039.

# How long the monitor can go without receiving anything from the agent
# before it treats its cached agent state as stale. The monitor only
# writes agent_state when its cached view changes, but other code writes
# it too: pause() and unpause() do, and a pause short enough that the
# monitor management loop never tears this monitor down leaves the cache
# saying ready while the database says "no contact" -- forever, because
# ping replies never touch agent_state. A guest which was frozen shows up
# here as a gap in replies, so a gap resets the cache and the next ping
# is an is_system_running request whose reply rewrites the database.
#
# unpause(), and a power on which starts the domain, also have this
# monitor restarted (see Instance._restart_sidechannel_monitor()), which
# covers a pause of any length. This check is the backstop for when that
# cannot happen -- the abort file could not be written, or the guest was
# frozen and thawed by something other than pause() and unpause(), such
# as a host stall or a suspend and resume made directly through libvirt.
#
# Only the cache is reset, never agent_state itself. Whoever froze the
# guest has already written something more informative than we could
# (pause() writes "instance paused"), and the reply to the next
# is_system_running request writes the truth through the usual change
# detection in _handle_is_system_running(). That includes the move into
# ready, so facts are gathered again after a pause, which is wanted: the
# guest may have changed while frozen.
#
# A healthy agent is never silent for long: a ping goes out once nothing
# has been seen for two seconds, and the socket timeout is one second,
# so the normal gap between replies is two to three seconds. Six seconds
# is two missed ping rounds beyond that. Lower would mistake a briefly
# busy agent for a frozen one more often; higher would miss more of the
# short pauses this exists for, since a pause long enough to be seen by
# the management loop is already handled by restarting the monitor. A
# false positive costs one is_system_running round trip, one gather
# facts and one agent_state read, and never a change of state in the
# database, because the reply restores the value the cache held.
AGENT_REPLY_GAP = 6

# What the dependency evaluator does about each state a dependency can
# be observed in. Between them these two tuples and complete are the
# whole of AgentOperation's vocabulary, so the arm below them is
# complete rather than a fall through.
#
# The failed set is why a depends_on edge is called fate sharing: an
# operation whose dependency reached any of these will never see the
# thing it was waiting for, so it is aborted rather than run. deleted is
# in it because a user deleting a dependency is a decision about the
# chain, not an accident. A runs_after edge is satisfied by every one of
# them, because it says "not before" and nothing about the outcome.
DEPENDENCY_FAILED_STATES = (AgentOperation.STATE_ERROR,
                            AgentOperation.STATE_ABORT,
                            AgentOperation.STATE_EXPIRED,
                            AgentOperation.STATE_DELETED)

# A dependency in one of these has not finished yet, so its dependent
# waits -- bounded by AGENT_OPERATION_DEPENDENCY_WAIT, because a
# dependency which never goes terminal must not park an instance's queue
# head forever.
DEPENDENCY_PENDING_STATES = (AgentOperation.STATE_INITIAL,
                             AgentOperation.STATE_PREFLIGHT,
                             AgentOperation.STATE_QUEUED,
                             AgentOperation.STATE_EXECUTING)

# The sentinel recorded as a dependency's state when the dependency does
# not exist at all. Deliberately not a state any operation can hold, so
# that a client reading dependency_failure can tell a dependency which
# failed from one which is gone -- the two are remedied differently even
# though they cost the dependent the same fate.
DEPENDENCY_MISSING = 'missing'

# The sentinel recorded when a dependency's state could not be read at
# all. Separate from DEPENDENCY_MISSING because the two say opposite
# things: missing means the read succeeded and there is no such
# operation, while unreadable means nothing was learned about it. A
# client which finds unreadable in dependency_failure should look at the
# node's database connectivity rather than at the chain it submitted.
DEPENDENCY_UNREADABLE = 'unreadable'

# What a satisfied edge's settle delay has to say for itself. Three
# answers rather than two, because the two walks treat the last of them
# differently: a depends_on edge cascades when the wait budget has been
# spent on a settle, and a runs_after edge warns and runs. Naming them
# keeps that fork in the walks, which is where the asymmetry belongs,
# instead of hiding it in a helper's return convention.
SETTLE_SATISFIED = 'satisfied'
SETTLE_SETTLING = 'settling'
SETTLE_EXHAUSTED = 'exhausted'

# How many terminal dependency observations the dispatcher remembers.
# Not because memory is tight -- an entry is a uuid and a short string --
# but because an unbounded dictionary in a daemon which runs for weeks
# is a leak waiting to be found by an audit rather than by a test.
TERMINAL_DEPENDENCY_CACHE_SIZE = 4096

# How many runs_after warnings the dispatcher remembers having already
# audited, bounded for the reason above and not for a different one.
# Smaller because the condition is rare -- an ordering edge whose
# dependency has gone, or which outlasted the budget -- and because
# evicting an entry costs one repeated event rather than a wrong answer,
# which is the gentlest consequence an eviction in this file has.
RUNS_AFTER_WARNING_CACHE_SIZE = 1024


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
        # The derivation is shared with Instance.unpause(), which sets the
        # monitor's path to have it restarted.
        self.abort_path = daemon.sidechannel_abort_path(
            abort_name or self.instance.uuid)
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

        # Conditional, because the management loop can have listed this
        # domain as running just before a pause() or power_off() finished.
        # An unconditional write then replaces "instance paused" or
        # "instance powered off" with "no contact", and nothing rewrites
        # it until the next unpause or power on.
        self.instance.record_observed_agent_state(constants.AGENT_NEVER_TALKED)
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
        if self.instance.record_observed_agent_state(constants.AGENT_STARTED):
            self.instance_ready = constants.AGENT_STARTED
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
            if not self.instance.record_observed_agent_state(new_state):
                # The recorded power state says the guest is not running,
                # so this reply describes an agent which was frozen under
                # us: a pause or a power off wrote a truer value than we
                # could, and that is what is recorded. The cache is left
                # alone deliberately, so that the next reply asks again --
                # power_state is a cache too, and a guest resumed outside
                # Shaken Fist is running while it still says paused. No
                # facts are gathered either: a frozen guest will not answer.
                self.log.with_fields({
                    'observed_agent_state': new_state
                }).info('Agent state not recorded, the instance is not running')
                return

            self.instance_ready = new_state

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

    def _note_data_received(self):
        # Why a gap in replies resets the cache at all is beside
        # AGENT_REPLY_GAP. Two things about where the check sits:
        #
        # self.last_data cannot detect a gap, because sending a ping
        # moves it too. The check is made when data arrives rather than
        # on every pass of the loop: a frozen guest then costs nothing
        # while it stays frozen, the reset happens exactly once per gap,
        # and a reply which arrives just as the gap crosses the threshold
        # is not missed between two passes.
        #
        # Every cached state is reset, not just the ready ones -- a
        # degraded guest is asked is_system_running on every ping, but
        # its reply is still only written when it differs from the cache.
        now = time.time()
        if now - self.last_received > AGENT_REPLY_GAP:
            if self.instance_ready != constants.AGENT_NEVER_TALKED:
                self.log.with_fields({
                    'gap': now - self.last_received,
                    'cached_agent_state': self.instance_ready
                }).info('Agent was silent, re-checking its state')
                self.instance_ready = constants.AGENT_NEVER_TALKED
        self.last_received = now

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
        self.last_received = self.last_data

        buffered = bytearray()
        while daemon.check_abort_path(self.abort_path):
            if time.time() - self.last_data > 2:
                self._send_ping(vsock.sock)
                self.last_data = time.time()

            try:
                input = vsock.sock.recv(102400)
                if not input:
                    return

                self._note_data_received()
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
    retryable = True            # read by operation_is_retryable()
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
    which a retry must therefore never repeat. The cheaper per-command
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
    exit which cannot retry errors. Callers pass agentop.fail, or
    agentop.expire with its budget bound via functools.partial -- the
    caller is the one which knows which budget it detected running
    out, and expire() records that as the expiry_reason a client can
    branch on.

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

    # The next attempt is a new tenant of the queue head, so it gets the
    # whole dependency wait budget rather than what the abandoned
    # attempt left of it. See clear_dependency_wait().
    agentop.clear_dependency_wait()

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


# The two states an operation is in before it is first queued, and the
# reason recorded when the reaper finds one which never left. Initial
# lasts only while the API server is between creating the operation
# and finishing its enqueue; preflight until a NodeAgentopOp preflight
# task promotes it.
UNPROMOTED_HEAD_REASONS = {
    AgentOperation.STATE_INITIAL:
        'the operation deadline passed and its enqueue never completed',
    AgentOperation.STATE_PREFLIGHT:
        'the operation deadline passed and no preflight task promoted it',
}


def expire_unpromoted_head(agentop, state):
    """Expire a never-queued head whose deadline is long past.

    Instance.agent_operation_next() deliberately leaves an initial or
    preflight head alone, deadline or not, because expiring it there
    would race the preflight task, which enforces the deadline itself.
    That only works if the preflight task runs. If its cluster
    operation was lost, or the API server died mid-enqueue, nothing
    else will ever move the head, and every later operation for the
    instance waits behind it forever (issue #4539).

    The reaper can close that without the race, because the preflight
    task runs on this node and checks the deadline immediately before
    it promotes: waiting UNPROMOTED_HEAD_GRACE past the deadline means
    a live task has either expired the operation already or is not
    going to promote it. A late task finds the operation terminal and
    leaves it alone. The outcome is expire(), not fail(), and with the
    deadline budget, so that it matches what _preflight() records when
    it runs late: in both cases the caller's budget is what ran out.

    An operation with no wall-clock deadline (0.0 alongside a live
    progress timeout) would otherwise never qualify, and its progress
    timeout cannot help because only an executor applies it. It is
    bounded by AGENT_OPERATION_MAX_DEADLINE from its last transition
    instead, the same backstop effective_deadline() gives an operation
    with neither budget.

    state is the State already read for agentop, which anchors a NULL
    deadline without a second read. Returns True if the operation was
    expired.
    """
    deadline = agentop.effective_deadline(state=state)
    if deadline is None:
        deadline = state.update_time + config.AGENT_OPERATION_MAX_DEADLINE
    if time.time() <= deadline + UNPROMOTED_HEAD_GRACE:
        return False

    LOG.with_fields({
        'instance': agentop.instance_uuid,
        'agentoperation': agentop.uuid,
        'state': state.value
    }).warning('Agent operation was never queued and its deadline has '
               'passed; expiring it')
    agentop.expire(UNPROMOTED_HEAD_REASONS[state.value],
                   AgentOperation.EXPIRY_REASON_DEADLINE)
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
            # otherwise. See issues #3516 and #2240.
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
        otherwise is a behaviour change with no end to end test to
        prove it. That gap is pre-existing rather than introduced
        here, and interrupting live work belongs with the node-local
        reaper in reap_instance_executors(), not here. abort is
        excluded because it cannot be reached from executing at all,
        so an aborted operation never has a command in flight for this
        to abandon.
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

        # Dependency states observed terminal, by operation uuid. Only
        # terminal states are ever stored here; see _dependency_state().
        self.terminal_dependency_cache = {}

        # runs_after warnings already audited, keyed by (operation,
        # dependency, message); see _warn_runs_after(). A dict used as a
        # set, because the eviction policy both caches share needs
        # insertion order.
        self.runs_after_warnings = {}

    def start_instance_monitor(self, instance_uuid):
        # Rate limited for the reason MONITOR_START_INTERVAL records.
        last_attempt = self.monitor_attempts.get(instance_uuid, 0)
        if time.time() - last_attempt < MONITOR_START_INTERVAL:
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

        Four cases, and only four. Two of them are the absence of a
        thread, which is directly observable; the other two are a
        wall-clock deadline, which is an absolute timestamp and so
        cannot be wrong about a thread which is still making progress.
        A live executor inside its budgets is left entirely alone,
        because the progress timeout is that executor's own job and it
        holds state this method does not. So is an initial or preflight
        head inside its deadline plus UNPROMOTED_HEAD_GRACE, because
        promoting it is the preflight task's job; see
        expire_unpromoted_head() for the one which never runs.

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

        # Read once and passed down to the deadline checks, which would
        # otherwise resolve a NULL deadline's anchor with a second
        # uncached GetState.
        state = agentop.state
        if state.value in UNPROMOTED_HEAD_REASONS:
            # Case four: an operation which was never queued, and whose
            # deadline is long enough past that whatever should have
            # queued it is not going to. Expiring it lets
            # agent_operation_next() retire it and drain the queue.
            expire_unpromoted_head(agentop, state)
            return

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
            # The terminal outcome is fail(), not expire(), because the
            # two are split on what went wrong rather than on who
            # noticed: a stall which cannot be retried expires, an
            # executor which went away errors. This is an executor
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

        # start_instance_executor() registers an executor before it
        # starts the thread, on purpose, so there is a window in which
        # this entry exists and join() would raise "cannot join thread
        # before it is started". reap_instance_executors() tolerates
        # the same window; this path has to as well, or a SIGTERM
        # landing inside it takes the graceful shutdown down with a
        # RuntimeError and leaves the remaining threads unsignalled.
        # The abort file is already set above, so an unstarted thread
        # exits as soon as it runs.
        if t['thread'].ident is None:
            return

        t['thread'].join(0.5)

        if not t['thread'].is_alive():
            del threads[instance_uuid]
            daemon.clear_abort_path(t['object'].abort_path)
            add_event(
                EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                f'side channel {thread_type} finished')

    def _dependency_state(self, dep):
        """Read one dependency's state, by uuid and without hydrating it.

        Returns (readable, state value): (True, value) for a dependency
        which exists, (True, None) for one which does not exist any
        more, and (False, None) when the read itself failed.

        That first element is load bearing. Both of mariadb.get_state()'s
        backends catch their transport error and return None, so a
        plain state read cannot tell a deleted-and-swept dependency from
        an sf-database restart or a wedged subchannel -- and the
        depends_on walk acts irreversibly on the former. The
        get_state_checked() variant keeps the two apart, using the found
        flag the gRPC reply already carries.

        Not hydrating is a correctness requirement rather than a saving.
        AgentOperation.delete() removes an operation's static and
        attribute rows and only then assigns it the deleted state, so
        from_db() returns nothing for an operation a user has deleted --
        an evaluator which hydrated would call that dependency missing
        when it was deleted, which is a different answer for the
        dependent's dependency_failure. The state row is the one row a
        delete leaves behind, and this is the read AgentOperation.state
        itself makes.

        A dependency observed terminal is remembered for the life of this
        dispatcher generation and never read again. Terminal is
        absorbing, so that answer cannot go stale. A non-terminal
        observation is deliberately not cached: re-reading it on every
        pass is the entire mechanism by which a blocked operation
        eventually unblocks.

        The cache entry carries the time of that first terminal
        observation as well as the state, because it is the closest this
        evaluator can get to when the dependency finished, and a settle
        delay has to be measured from that rather than from when the
        dependent was parked.
        """
        # Keyed on the uuid alone, which is correct only while
        # VALID_DEPENDENCY_OPERATIONS admits exactly one op_type. The
        # mixed-type edges of issue #4824 would make this key ambiguous,
        # so it has to become (op_type, op_uuid) with them.
        op_uuid = str(dep['op_uuid'])

        cached = self.terminal_dependency_cache.get(op_uuid)
        if cached is not None:
            return True, cached[0]

        readable, state = mariadb.get_state_checked(
            ObjectType(dep['op_type']), op_uuid)
        if not readable:
            return False, None

        value = state.value if state else None
        if value in AgentOperation.TERMINAL_STATES:
            self._cache_terminal_dependency(op_uuid, value)
        return True, value

    def _remember_capped(self, cache, key, value, limit):
        """Add to one of the dispatcher's caches, evicting the oldest first.

        Insertion order is dict iteration order, so the oldest entry is
        the first one. pop() rather than del throughout because
        start_dispatcher() may have replaced this dictionary underneath a
        generation which has not noticed it was superseded yet.

        Shared by both of this class's caches so that there is one
        eviction policy rather than two which have to be kept the same.
        """
        if key not in cache and len(cache) >= limit:
            oldest = next(iter(cache), None)
            if oldest is not None:
                cache.pop(oldest, None)
        cache[key] = value

    def _cache_terminal_dependency(self, op_uuid, state):
        """Remember a terminal dependency state, and when it was seen."""
        self._remember_capped(
            self.terminal_dependency_cache, op_uuid,
            (state, time.time()), TERMINAL_DEPENDENCY_CACHE_SIZE)

    def _dependency_finished_at(self, dep, dep_state):
        """When this dispatcher first observed this dependency finished.

        The state row carries no completion time, and hydrating the
        dependency to find one is forbidden, so the first pass which saw
        it terminal is the best available answer -- later than the truth
        by up to one DISPATCH_CHECK_INTERVAL.

        A missing entry means the cache was evicted, or replaced by a new
        dispatcher generation, since the state was read. Observing it
        again now restarts the settle, which delays an operation rather
        than releasing it early, and the wait budget still bounds how
        long that can go on.
        """
        op_uuid = str(dep['op_uuid'])
        cached = self.terminal_dependency_cache.get(op_uuid)
        if cached is None:
            self._cache_terminal_dependency(op_uuid, dep_state)
            cached = self.terminal_dependency_cache.get(op_uuid)
        return cached[1] if cached else time.time()

    def _wait_budget_spent(self, agentop, budget):
        """Record the dependency wait anchor, and say if the budget is gone.

        Every arm of both walks which holds an operation comes through
        here, so there is one place which knows that the budget is
        measured from the anchor and one place which writes the anchor.
        Callers which are about to dispatch must not call it at all: the
        column means "blocked at least once", and an operation which was
        never held should leave it NULL.
        """
        anchor = agentop.note_dependency_wait_started()
        return time.time() - anchor > budget

    def _dependencies_met(self, inst, agentop):
        """Decide whether this operation's dependency edges permit dispatch.

        Returns (may dispatch, reason), where the reason is a short
        string for the caller's log when dispatch is refused and None
        when it is not.

        This mirrors the cluster side's dequeue-time walk in
        shakenfist/daemons/queues/workitem.py, including the asymmetry
        between the two edge lists, which is deliberate there and
        deliberate here:

        * depends_on is fate sharing. A dependency which is absent or
          which failed aborts this operation and refuses dispatch, and a
          wait which outlives AGENT_OPERATION_DEPENDENCY_WAIT does the
          same, because an operation parked behind a dependency which
          never finishes would hold its instance's queue head forever.
        * runs_after is ordering only. A dependency which is absent
          warns and the walk moves to the next edge, and a wait which
          outlives the budget warns and lets this operation run. Neither
          ever writes dependency_failure, which belongs to the cascade.

        It writes, which is why the caller runs it only after confirming
        this dispatcher generation is still the live one: a cascade is a
        terminal state transition and a first block records the budget
        anchor, and neither should be done by a thread which has been
        replaced.
        """
        # An operation which declared no edges must cost nothing. This
        # runs once per agent-ready instance per DISPATCH_CHECK_INTERVAL
        # whether anything in the cluster uses dependencies or not, so
        # the common case reads no state at all.
        if not agentop.depends_on and not agentop.runs_after:
            return True, None

        budget = config.AGENT_OPERATION_DEPENDENCY_WAIT

        for dep in agentop.depends_on:
            readable, dep_state = self._dependency_state(dep)

            if not readable:
                # A failed read is evidence of nothing, so it must not
                # reach either cascade below. The absence comment there
                # reasons from "only the hard delete sweep removes a
                # state row", which is a fact about the database and not
                # about a read of it. Holding costs a transient blip one
                # DISPATCH_CHECK_INTERVAL instead of costing every
                # queued dependent on this node its operation.
                #
                # The budget below bounds an outage which does not clear
                # only while the anchor can still be written, which
                # means only while the failure is confined to this read.
                # A backend which is down for everything fails the
                # anchor write too, _attributes() then answers with a
                # default carrying no anchor, and every pass re-anchors
                # at now -- so the operation holds indefinitely rather
                # than being aborted for a dependency nobody could read.
                # That is the direction to fail in, and it is why this
                # arm is a hold and not a judgement.
                if self._wait_budget_spent(agentop, budget):
                    self._cascade_abort(
                        agentop, dep, DEPENDENCY_UNREADABLE,
                        event=CASCADE_EVENT_GAVE_UP)
                    return False, 'gave up trying to read a dependency'
                return False, 'dependency state could not be read'

            if dep_state is None:
                # Absence is treated as a failure, and it is narrower
                # than it looks. A user deleted dependency still has
                # its state row and reads as deleted, so it never
                # arrives here. Only a hard delete sweep removes the
                # state row, and it does that CLEANER_DELAY after the
                # dependency reached a final state -- so an absent
                # dependency finished at least an hour ago, and
                # finished in one of the states the sweep collects.
                # error is not one of them, so absence cannot
                # distinguish a success from an abort or an expiry.
                # Refusing is the safe side of that ambiguity: it
                # declines work whose precondition probably held,
                # where running would perform work whose precondition
                # may have failed, and a fate-sharing edge should fail
                # safe. A caller who needs to avoid the ambiguity
                # keeps the dependent's deadline under CLEANER_DELAY,
                # so it cannot still be queued when the sweep runs.
                self._cascade_abort(agentop, dep, DEPENDENCY_MISSING)
                return False, 'dependency is missing'

            if dep_state in DEPENDENCY_FAILED_STATES:
                self._cascade_abort(agentop, dep, dep_state)
                return False, f'dependency is {dep_state}'

            if dep_state in DEPENDENCY_PENDING_STATES:
                if self._wait_budget_spent(agentop, budget):
                    # The analogue of the cluster side's
                    # abandon_dependency_wait(): the dependency may yet
                    # finish, but not inside a budget this instance's
                    # other callers can afford to wait out.
                    self._cascade_abort(
                        agentop, dep, dep_state,
                        event=CASCADE_EVENT_GAVE_UP)
                    return False, f'gave up waiting for a {dep_state} dependency'
                return False, f'waiting for a {dep_state} dependency'

            if dep_state != AgentOperation.STATE_COMPLETE:
                # Named, rather than reached by falling through the two
                # tuples above. They do enumerate the whole of
                # AgentOperation's vocabulary, and a derived test holds
                # them to it, but a fall through would let a state added
                # later satisfy a fate sharing edge silently -- the one
                # direction this edge must never fail in. An unknown
                # state earns a hold instead, bounded like every other
                # hold here.
                if self._wait_budget_spent(agentop, budget):
                    self._cascade_abort(
                        agentop, dep, dep_state,
                        event=CASCADE_EVENT_GAVE_UP)
                    return False, f'gave up waiting for an unknown {dep_state} dependency'
                return False, f'waiting for an unknown {dep_state} dependency'

            # Complete, which satisfies the edge -- once any settle
            # delay has elapsed.
            settle = self._settle_verdict(agentop, dep, dep_state, budget)
            if settle == SETTLE_EXHAUSTED:
                self._cascade_abort(
                    agentop, dep, dep_state,
                    event=CASCADE_EVENT_GAVE_UP)
                return False, 'gave up waiting for a settle delay'
            if settle == SETTLE_SETTLING:
                return False, 'settling'

        for dep in agentop.runs_after:
            readable, dep_state = self._dependency_state(dep)

            if not readable:
                # Held rather than warned past, for the reason the
                # depends_on walk holds: dispatching on a failed read
                # would break exactly the ordering this edge asked for,
                # and it would do it silently.
                if self._wait_budget_spent(agentop, budget):
                    self._warn_runs_after(
                        inst, agentop, dep, DEPENDENCY_UNREADABLE,
                        'warning, gave up trying to read runs_after dependency')
                    continue
                return False, 'runs_after dependency state could not be read'

            if dep_state is None:
                # Not fatal, for the reason the cluster side gives: an
                # ordering hint whose target has gone must not be able
                # to stop an object being managed. The warning is the
                # whole of the response, and the walk continues to the
                # next edge rather than returning.
                self._warn_runs_after(
                    inst, agentop, dep, DEPENDENCY_MISSING,
                    'warning, runs_after dependency is missing')
                continue

            if dep_state in DEPENDENCY_PENDING_STATES:
                if self._wait_budget_spent(agentop, budget):
                    # Ordering again: a missing runs_after edge already
                    # warns and proceeds, so an exhausted wait on one
                    # does the same. This is where the two lists differ
                    # most sharply -- the same condition cascades above.
                    self._warn_runs_after(
                        inst, agentop, dep, dep_state,
                        'warning, gave up waiting for runs_after dependency')
                    continue
                return False, f'waiting for a {dep_state} runs_after dependency'

            if dep_state not in AgentOperation.TERMINAL_STATES:
                # The depends_on walk's guard, for the same reason: this
                # edge says "not before", so a state which cannot be
                # shown to have finished has not earned a dispatch.
                # Warned past once the budget is spent, as everything
                # else in this walk is.
                if self._wait_budget_spent(agentop, budget):
                    self._warn_runs_after(
                        inst, agentop, dep, dep_state,
                        'warning, gave up waiting for an unknown '
                        'runs_after dependency state')
                    continue
                return False, f'waiting for an unknown {dep_state} runs_after dependency'

            # Any terminal state satisfies an ordering edge, whatever it
            # says about the outcome, subject to the settle delay.
            settle = self._settle_verdict(agentop, dep, dep_state, budget)
            if settle == SETTLE_EXHAUSTED:
                self._warn_runs_after(
                    inst, agentop, dep, dep_state,
                    'warning, gave up waiting for a runs_after settle delay')
                continue
            if settle == SETTLE_SETTLING:
                return False, 'settling for a runs_after dependency'

        return True, None

    def _settle_verdict(self, agentop, dep, dep_state, budget):
        """Where a satisfied edge's settle delay has got to.

        SETTLE_SATISFIED, SETTLE_SETTLING, or SETTLE_EXHAUSTED once the
        wait budget has been spent waiting for the settle. The last of
        those is reported rather than acted on, because the two walks
        answer it differently.

        The delay runs from when the dependency was first observed
        finished, which is what the parameter means: a caller asks for a
        settle because the guest needs to quiesce after whatever the
        dependency did, and that cannot start before the dependency
        does. It is up to one DISPATCH_CHECK_INTERVAL later than the
        dependency actually finished, which is the one imprecision the
        no-hydration rule forces.

        It deliberately does not run from the dependency wait anchor.
        That anchor is set on the first pass which found this operation
        blocked on anything, so for a dependency still running then, a
        settle measured from it would have been partly or entirely spent
        by the time the dependency finished -- and a settle longer than
        the dependency's remaining runtime would not have delayed
        anything at all.

        The budget is still measured from the anchor, because it bounds
        how long this operation may hold the head of its instance's
        queue, and it has been holding it since the anchor -- but the
        declared settle is added to it. Without that the two clocks have
        different origins and the API ceiling buys nothing: a dependency
        observed finished N seconds after the anchor leaves only
        budget - N for the settle, so a settle at the ceiling, which the
        API explicitly accepts, is guaranteed to be abandoned whenever
        N is above zero. Extending the bound by the declared settle is
        what makes a settle inside the ceiling always able to elapse,
        which is the whole argument the ceiling rests on, and it bounds
        the hold at budget + settle rather than removing the bound.

        The anchor is written only once the settle is known not to have
        elapsed. An operation whose dependency finished long ago has not
        been blocked by anything, and the column says it was never
        blocked.
        """
        settle = dep.get('settle_seconds')
        if not settle:
            return SETTLE_SATISFIED

        finished = self._dependency_finished_at(dep, dep_state)
        if time.time() - finished >= settle:
            return SETTLE_SATISFIED

        # Reachable now only by a hold which outlived budget + settle:
        # a dependency observed finished after the budget had already
        # run down, or a settle restarted by a lost cache entry often
        # enough to outlast it. Giving up beats blocking forever.
        if self._wait_budget_spent(agentop, budget + settle):
            return SETTLE_EXHAUSTED
        return SETTLE_SETTLING

    def _cascade_abort(self, agentop, dep, dep_state,
                       event=None):
        """Abort an operation because a depends_on edge cannot be met.

        event overrides the audit event text, for the two cases which
        are a give up rather than a failed dependency: a wait or a
        settle delay which outran the operator's budget.
        """
        kwargs = {'event': event} if event else {}
        if not agentop.cascade_abort_for_dependency(dep, dep_state,
                                                    **kwargs):
            # The operation finished underneath us -- deadline
            # enforcement expires one out of band -- so there is no
            # cascade to have performed. The method audits that itself;
            # this log line says the dispatcher saw it, because a
            # cascade which did not land means the state moved between
            # agent_operation_next() and here.
            LOG.with_fields({
                'agentoperation': agentop.uuid,
                'dependency': dep['op_uuid'],
                'dependency_state': dep_state
            }).info('Dependency cascade found the operation already finished')

    def _warn_runs_after(self, inst, agentop, dep, dep_state, message):
        """Audit an unsatisfiable runs_after edge which is being proceeded past.

        Against the instance as well as the operation, for the reason
        the cascade event gives: a finished operation is hard delete
        swept and takes its own events with it, and the instance is what
        an operator looks at.

        Once per operation, edge and message, because both walks restart
        from their first edge on every pass: an operation held by a
        later edge would otherwise re-emit this for an earlier one every
        DISPATCH_CHECK_INTERVAL, which is roughly sixty durable rows
        over one budget and the only per-pass write either walk makes.
        The record is kept for the life of a dispatcher generation, the
        same lifetime the terminal dependency cache has, so a restart
        re-warns once rather than going quiet forever, and it is capped
        the same way for the same reason: a container which only ever
        grows, in a daemon which runs for weeks, is a leak.
        """
        warned = (str(agentop.uuid), str(dep['op_uuid']), message)
        if warned in self.runs_after_warnings:
            return
        self._remember_capped(
            self.runs_after_warnings, warned, True,
            RUNS_AFTER_WARNING_CACHE_SIZE)

        add_event_multi(
            EVENT_TYPE_AUDIT,
            [(ObjectType.AGENTOPERATION, agentop.uuid),
             (ObjectType.INSTANCE, inst.uuid)],
            message,
            extra={'op_type': dep['op_type'], 'op_uuid': dep['op_uuid'],
                   'dependency_state': dep_state,
                   'budget': config.AGENT_OPERATION_DEPENDENCY_WAIT})

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

                    # Rate limited for the reason
                    # DISPATCH_CHECK_INTERVAL records.
                    last_attempt = self.executor_attempts.get(
                        instance_uuid, 0)
                    if time.time() - last_attempt < DISPATCH_CHECK_INTERVAL:
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

                        # After the generation check, because evaluation
                        # writes -- a cascade moves an operation to a
                        # terminal state and a first block records the
                        # budget anchor -- and a superseded generation
                        # must no more write state than it may dispatch.
                        # Before the event below, because that event
                        # claims a dispatch, and a blocked operation is
                        # not dispatched.
                        dispatchable, reason = self._dependencies_met(
                            inst, agentop)
                        if not dispatchable:
                            # Debug, not info: a blocked operation is
                            # re-evaluated once every
                            # DISPATCH_CHECK_INTERVAL for as long as it
                            # stays blocked.
                            log.with_fields({
                                'instance': instance_uuid,
                                'agentoperation': agentop.uuid
                            }).debug(
                                'Not dispatching agent operation: %s'
                                % reason)
                            continue

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

        # The terminal dependency cache and the runs_after warning
        # record both live exactly as long as a dispatcher generation
        # does, so a replacement thread starts cold. Fresh containers
        # rather than clear(), so a superseded generation which has not
        # noticed yet cannot be iterating one being emptied.
        self.terminal_dependency_cache = {}
        self.runs_after_warnings = {}
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

    def _running_instance_uuids(self, lc):
        """The instances whose domains are running here, and so need a monitor.

        A delete can undefine a domain after it was listed. That domain is
        gone, so it needs no monitor, and the rest of the audit still has to
        run: raising here would leave every other instance's monitor
        unstarted or uncleaned until the next pass.
        """
        running = []
        for domain in lc.get_active_sf_domains():
            try:
                state = lc.extract_power_state(domain)
            except lc.libvirt.libvirtError as e:
                if not lc.is_domain_gone(e):
                    raise
                LOG.debug(f'Domain {domain.name()} went away while being audited')
                continue

            if state in ['off', 'crashed', 'paused']:
                # If the domain isn't running, it shouldn't have a
                # sidechannel monitor.
                continue

            running.append(domain.name().split(':')[1])
        return running

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
                        for instance_uuid in self._running_instance_uuids(lc):
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
