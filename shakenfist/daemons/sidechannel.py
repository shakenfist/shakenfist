import base64
import os
import select
import setproctitle
from shakenfist_utilities import logs
import signal
from shakenfist_agent import protocol
import time
from versions_comparison import Comparison
import uuid

from shakenfist.agentoperation import AgentOperation
from shakenfist import blob
from shakenfist import constants
from shakenfist.constants import EVENT_TYPE_AUDIT, EVENT_TYPE_STATUS
from shakenfist.daemons import daemon
from shakenfist import eventlog
from shakenfist import instance
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import process as util_process


LOG, _ = logs.setup(__name__)


# This is the minimum version of the in-guest agent that we support. This
# generally gets bumped when the protocol changes.
MINIMUM_AGENT_VERSION = '0.3.16'


class ConnectionFailed(Exception):
    ...


class PutException(Exception):
    ...


class SFSocketAgent(protocol.SocketAgent):
    def __init__(self, inst, path, logger=None):
        super(SFSocketAgent, self).__init__(path, logger=logger)
        self.log = LOG.with_fields({'instance': inst})
        self.instance = inst

    def poll(self):
        raise NotImplementedError('Please don\'t call poll() in the sidechannel monitor')


class Monitor(daemon.Daemon):
    def __init__(self, name):
        super(Monitor, self).__init__(name)
        self.monitors = {}

    def _record_system_boot_time(self, sbt):
        if sbt != self.system_boot_time:
            if self.system_boot_time != 0:
                self.instance.add_event(EVENT_TYPE_AUDIT, 'reboot detected')
            self.system_boot_time = sbt
            self.instance.agent_system_boot_time = sbt

    def _handle_background_message(self, packet):
        command = packet.get('command')
        if command == 'agent-start':
            self.instance.agent_start_time = time.time()
            sbt = packet.get('system_boot_time', 0)
            self._record_system_boot_time(sbt)

            agent_version = packet.get('message')
            if agent_version:
                self.instance.add_event(
                    EVENT_TYPE_AUDIT, 'detected agent version',
                    extra={'version': agent_version})
                versions = Comparison(agent_version.split(' ')[1], MINIMUM_AGENT_VERSION)
                lesser = versions.get_lesser()
                if lesser and lesser == agent_version:
                    self.instance_ready = constants.AGENT_TOO_OLD
                    self.instance.agent_state = constants.AGENT_TOO_OLD
                    return True

            self.instance_ready = constants.AGENT_STARTED
            self.instance.agent_state = constants.AGENT_STARTED
            return True

        elif command == 'agent-stop':
            self.instance_ready = constants.AGENT_STOPPED
            self.instance.agent_state = constants.AGENT_STOPPED
            return True

        elif command == 'is-system-running-response':
            ready = packet.get('result', 'False')
            sbt = packet.get('system_boot_time', 0)
            self._record_system_boot_time(sbt)

            if ready:
                new_state = constants.AGENT_READY
            else:
                # Special case the degraded state here, as the system is in fact
                # as ready as it is ever going to be, but isn't entirely happy.
                if packet.get('message', 'none') == 'degraded':
                    new_state = constants.AGENT_READY_DEGRADED
                else:
                    new_state = constants.AGENT_DEGRADED

            # We cache the agent state to reduce database load, and then
            # trigger facts gathering when we transition into the constants.AGENT_READY state.
            if self.instance_ready != new_state:
                self.instance_ready = new_state
                self.instance.agent_state = new_state
                if new_state in [constants.AGENT_READY, constants.AGENT_READY_DEGRADED]:
                    self.sc_client.send_packet({
                        'command': 'gather-facts',
                        'unique': str(time.time())
                        })
            return True

        elif command == 'gather-facts-response':
            self.instance.add_event(EVENT_TYPE_AUDIT, 'received system facts')
            self.instance.agent_facts = packet.get('result', {})
            return True

        elif command == 'ping':
            self.sc_client.send_packet({
                'command': 'pong',
                'unique': packet['unique']
            })
            return True

        elif command == 'pong':
            return True

        return False

    def _await_client(self):
        readable, _, exceptional = select.select(
            [self.sc_client.input_fileno], [], [self.sc_client.input_fileno], 1)

        if readable:
            self.last_data = time.time()
            try:
                for packet in self.sc_client.find_packets():
                    if not self.agent_has_talked:
                        self.instance.add_event(EVENT_TYPE_AUDIT, 'sidechannel connected')
                        self.agent_has_talked = True

                    if not self._handle_background_message(packet):
                        yield packet

            except (BrokenPipeError, ConnectionRefusedError, ConnectionResetError,
                    FileNotFoundError, OSError):
                raise ConnectionFailed()

        if exceptional:
            raise ConnectionFailed()

    # Prototype new version of send_file(), playing here before doing yet another
    # agent release.
    def _send_file(self, command, source_path, destination_path, unique):
        st = os.stat(source_path, follow_symlinks=True)
        yield {
            'command': command,
            'path': destination_path,
            'stat_result': {
                'mode': st.st_mode,
                'size': st.st_size,
                'uid': st.st_uid,
                'gid': st.st_gid,
                'atime': st.st_atime,
                'mtime': st.st_mtime,
                'ctime': st.st_ctime
            },
            'unique': unique
        }

        offset = 0
        with open(source_path, 'rb') as f:
            while d := f.read(1024):
                yield {
                    'command': command,
                    'path': destination_path,
                    'offset': offset,
                    'encoding': 'base64',
                    'chunk': base64.b64encode(d).decode('utf-8'),
                    'unique': unique
                }
                offset += len(d)

            yield {
                'command': command,
                'path': destination_path,
                'offset': offset,
                'encoding': 'base64',
                'chunk': None,
                'unique': unique
            }

    def single_instance_monitor(self, instance_uuid):
        setproctitle.setproctitle('sf-sidechannel-%s' % instance_uuid)

        self.instance = instance.Instance.from_db(instance_uuid)
        if not self.instance:
            return
        if 'sf-agent' not in self.instance.side_channels:
            return
        if self.instance.state.value == instance.Instance.STATE_DELETED:
            return

        self.instance_ready = constants.AGENT_NEVER_TALKED
        self.instance.agent_state = constants.AGENT_NEVER_TALKED
        self.system_boot_time = 0
        self.last_data = time.time()
        self.log = LOG.with_fields({'instance': instance_uuid})

        # We use the existence of a console.log file in the instance directory
        # to indicate the instance has been created. This will be true even if
        # the instance doesn't actually every write to the serial console.
        console_path = os.path.join(self.instance.instance_path, 'console.log')
        while not os.path.exists(console_path):
            time.sleep(1)
        self.instance.add_event(EVENT_TYPE_STATUS, 'detected console log')

        # Ensure side channel path exists.
        sc_path = os.path.join(self.instance.instance_path, 'sc-sf-agent')
        if not os.path.exists(sc_path):
            self.log.info('sf-agent side channel file missing, aborting')
            return

        self.sc_client = None
        self.agent_has_talked = False

        # Setup a connection to the client
        while not self.sc_client:
            try:
                self.sc_client = SFSocketAgent(self.instance, sc_path, logger=self.log)
                break
            except (BrokenPipeError, ConnectionRefusedError, ConnectionResetError,
                    FileNotFoundError, OSError):
                time.sleep(1)

        # We really want to see one of a small number of packets from the client
        # as our initial conversation. Its possible if this is a restart of the
        # monitor because of an error that we will receive unexpected packets.
        # Just ignore them for now.
        first_attempt = time.time()
        last_attempt = time.time()
        try:
            self.sc_client.send_packet({
                'command': 'is-system-running',
                'unique': str(time.time())
                })

            while not self.exit.is_set():
                for packet in self._await_client():
                    self.log.with_fields({'packet': packet}).error(
                        'Unexpected sidechannel client packet during startup, ignoring')

                if self.instance_ready in [constants.AGENT_READY,
                                           constants.AGENT_READY_DEGRADED]:
                    break

                # Retry every now and then
                if time.time() - last_attempt > 30:
                    self.sc_client.send_packet({
                        'command': 'is-system-running',
                        'unique': str(time.time())
                        })
                    last_attempt = time.time()

                # If its been a long time and we've heard nothing, then we should
                # exit so we can re-attempt.
                if time.time() - first_attempt > 300 and not self.agent_has_talked:
                    self.log.debug('We waited a long time but the agent never spoke, aborting')
                    return

        except (BrokenPipeError, ConnectionRefusedError, ConnectionResetError,
                FileNotFoundError, OSError, ConnectionFailed) as e:
            self.log.with_fields({'error': str(e)}).debug(
                'Unexpected sidechannel communication error during '
                'connection setup, aborting')
            return

        self.instance.add_event(EVENT_TYPE_AUDIT, 'instance agent has completed start up')

        # If the agent is too old, then just sit here not doing the things we
        # should be doing
        if self.instance_ready == constants.AGENT_TOO_OLD:
            self.instance.add_event(
                EVENT_TYPE_AUDIT, 'instance agent is too old, not executing commands')
            while not self.exit.is_set():
                time.sleep(1)

        # Spin reading packets and responding until we see an error or are asked
        # to exit.
        try:
            while not self.exit.is_set():
                for packet in self._await_client():
                    self.log.with_fields({'packet': packet}).error(
                        'Unexpected sidechannel client packet')

                # If idle, try to do something
                if self.instance_ready in [constants.AGENT_READY,
                                           constants.AGENT_READY_DEGRADED]:
                    agentop = self.instance.agent_operation_dequeue()
                    if agentop:
                        self.instance.add_event(
                            EVENT_TYPE_AUDIT, 'dequeued agent operation',
                            extra={'agentoperation': agentop.uuid})

                        agentop.state = AgentOperation.STATE_EXECUTING
                        count = 0
                        num_results = 0
                        commands = agentop.commands

                        for command in commands:
                            if command['command'] == 'put-blob':
                                b = blob.Blob.from_db(command['blob_uuid'])
                                if not b:
                                    agentop.error = 'blob missing: %s' % command['blob_uuid']
                                    break
                                blob_path = blob.Blob.filepath(b.uuid)
                                if not os.path.exists(blob_path):
                                    agentop.error = 'blob file missing: %s' % command['blob_uuid']
                                    break

                                unique = 'agentop:%s:%d' % (agentop.uuid, count)
                                inpacket = {}
                                for outpacket in self._send_file(
                                        'put-file', blob_path, command['path'], unique):
                                    self.sc_client.send_packet(outpacket)

                                    # Wait for a matching ACK
                                    for inpacket in self._await_client():
                                        if (inpacket.get('command') == 'put-file-response'
                                                and inpacket.get('unique') == unique):
                                            break
                                        else:
                                            self.log.with_fields({'packet': inpacket}).error(
                                                'Unexpected sidechannel client packet in '
                                                'response to put-file command')
                                agentop.add_result(count, inpacket)
                                num_results += 1

                            elif command['command'] == 'get-file':
                                unique = 'agentop:%s:%d' % (agentop.uuid, count)
                                self.sc_client.send_packet({
                                    'command': 'get-file',
                                    'path': command['path'],
                                    'unique': unique
                                    })
                                get_done = False
                                blob_uuid = str(uuid.uuid4())
                                blob_path = blob.Blob.filepath(blob_uuid)
                                stat_result = {}
                                total_length = 0

                                while not get_done:
                                    with open(blob_path + '.partial', 'wb') as f:
                                        for inpacket in self._await_client():
                                            if (inpacket.get('command') == 'get-file-response'
                                                    and inpacket.get('unique') == unique):
                                                if 'stat_result' in inpacket:
                                                    stat_result = inpacket['stat_result']

                                                if 'chunk' in inpacket:
                                                    if not inpacket['chunk']:
                                                        # An empty chunk indicates completion
                                                        del inpacket['chunk']
                                                        del inpacket['offset']
                                                        del inpacket['encoding']
                                                        inpacket['stat_result'] = stat_result
                                                        inpacket['content_blob'] = blob_uuid

                                                        agentop.add_result(count, inpacket)
                                                        num_results += 1
                                                        get_done = True
                                                    else:
                                                        d = base64.b64decode(inpacket['chunk'])
                                                        d_len = len(d)
                                                        self.log.debug('Wrote %d bytes to %s.partial'
                                                                       % (d_len, blob_path))
                                                        total_length += d_len
                                                        f.write(d)
                                            else:
                                                self.log.with_fields({'packet': inpacket}).error(
                                                    'Unexpected sidechannel client packet in '
                                                    'response to get-file command')

                                # This os.sync() is here because _sometimes_ we wouldn't see the
                                # data on disk when we immediately try to replicate it.
                                os.sync()

                                # We don't remove the partial file until we've finished
                                # registering the blob to avoid deletion races. Note that
                                # this _must_ be a hard link, which is why we don't use
                                # util_general.link().
                                os.link(blob_path + '.partial', blob_path)
                                st = os.stat(blob_path)
                                if st.st_size == 0 and total_length > 0:
                                    self.log.error('Agent get-file blob is zero not %d bytes.'
                                                   % total_length)

                                b = blob.Blob.new(blob_uuid, total_length, time.time(), time.time())
                                b.ref_count_inc(agentop)
                                b.observe()
                                b.request_replication()
                                os.unlink(blob_path + '.partial')

                            elif command['command'] == 'chmod':
                                unique = 'agentop:%s:%d' % (agentop.uuid, count)
                                self.sc_client.send_packet({
                                    'command': 'chmod',
                                    'path': command['path'],
                                    'mode': command['mode'],
                                    'unique': unique
                                    })
                                chmod_done = False
                                while not chmod_done:
                                    for inpacket in self._await_client():
                                        if (inpacket.get('command') == 'chmod-response'
                                                and inpacket.get('unique') == unique):
                                            agentop.add_result(count, inpacket)
                                            num_results += 1
                                            chmod_done = True
                                        else:
                                            self.log.with_fields({'packet': inpacket}).error(
                                                'Unexpected sidechannel client packet in '
                                                'response to chmod command')

                            elif command['command'] == 'chown':
                                unique = 'agentop:%s:%d' % (agentop.uuid, count)
                                self.sc_client.send_packet({
                                    'command': 'chown',
                                    'user': command['user'],
                                    'group': command['group'],
                                    'unique': unique
                                    })
                                chown_done = False
                                while not chown_done:
                                    for inpacket in self._await_client():
                                        if (inpacket.get('command') == 'chown-response'
                                                and inpacket.get('unique') == unique):
                                            agentop.add_result(count, inpacket)
                                            num_results += 1
                                            chown_done = True
                                        else:
                                            self.log.with_fields({'packet': inpacket}).error(
                                                'Unexpected sidechannel client packet in '
                                                'response to chmod command')

                            elif command['command'] == 'execute':
                                unique = 'agentop:%s:%d' % (agentop.uuid, count)
                                self.sc_client.send_packet({
                                    'command': 'execute',
                                    'command-line': command['commandline'],
                                    'block-for-result': True,
                                    'unique': unique
                                    })
                                execute_done = False
                                while not execute_done:
                                    for inpacket in self._await_client():
                                        if (inpacket.get('command') == 'execute-response'
                                                and inpacket.get('unique') == unique):
                                            # Convert long stdouts and stderrs to blobs
                                            if len(inpacket.get('stdout')) > 10 * constants.KiB:
                                                b = blob.from_memory(
                                                    inpacket['stdout'].encode('utf-8'))
                                                b.ref_count_inc(agentop)
                                                del inpacket['stdout']
                                                inpacket['stdout_blob'] = b.uuid
                                            if len(inpacket.get('stderr')) > 10 * constants.KiB:
                                                b = blob.from_memory(
                                                    inpacket['stderr'].encode('utf-8'))
                                                b.ref_count_inc(agentop)
                                                del inpacket['stderr']
                                                inpacket['stderr_blob'] = b.uuid

                                            agentop.add_result(count, inpacket)
                                            num_results += 1
                                            execute_done = True
                                        else:
                                            self.log.with_fields({'packet': inpacket}).error(
                                                'Unexpected sidechannel client packet in '
                                                'response to execute command')

                            else:
                                self.instance.add_event(
                                    EVENT_TYPE_AUDIT,
                                    'unknown agent operation command, aborting operation',
                                    extra={
                                        'agentoperation': agentop.uuid,
                                        'command': command.get('command'),
                                        'count': count
                                        })
                                break

                            count += 1

                        if num_results == len(commands):
                            agentop.add_event(EVENT_TYPE_STATUS, 'commands complete')
                            agentop.state = AgentOperation.STATE_COMPLETE
                        else:
                            agentop.add_event(
                                EVENT_TYPE_STATUS, 'commands not yet complete',
                                extra={'commands': commands, 'results': num_results})

                # Ping if we've been idle for a small while
                if time.time() - self.last_data > 5:
                    self.sc_client.send_ping()

                # If very idle, something has gone wrong
                if time.time() - self.last_data > 15:
                    if self.instance.agent_state.value != constants.AGENT_NEVER_TALKED:
                        self.instance_ready = constants.AGENT_STOPPED_TALKING
                        self.instance.agent_state = constants.AGENT_STOPPED_TALKING
                    self.log.debug('Not receiving traffic, aborting.')
                    if self.system_boot_time != 0:
                        self.instance.add_event(
                            EVENT_TYPE_STATUS, 'agent has gone silent, restarting channel')

                    # Attempt to close, but the OS might already think its closed
                    try:
                        self.sc_client.close()
                    except OSError:
                        pass

                    return

        except (BrokenPipeError, ConnectionRefusedError, ConnectionResetError,
                FileNotFoundError, OSError, ConnectionFailed) as e:
            self.instance.add_event(
                EVENT_TYPE_STATUS,
                ('unexpected sidechannel communication error post '
                 'connection setup, restarting channel'), extra={'error': str(e)})
            return

    def reap_single_instance_monitors(self):
        all_monitors = list(self.monitors.keys())
        for instance_uuid in all_monitors:
            if not self.monitors[instance_uuid].is_alive():
                self.monitors[instance_uuid].join(1)
                LOG.info('Reaped dead sidechannel monitor with pid %d'
                         % self.monitors[instance_uuid].pid)
                eventlog.add_event(
                    EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                    'sidechannel monitor ended')
                del self.monitors[instance_uuid]

    def run(self):
        LOG.info('Starting')
        shutdown_commenced = 0
        running = True
        instance_sidechannel_cache = {}

        while True:
            try:
                self.reap_single_instance_monitors()

                if not self.exit.is_set():
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

                    # Start missing monitors. We only support sf-agent for now.
                    for instance_uuid in missing_instances:
                        if instance_uuid not in instance_sidechannel_cache:
                            inst = instance.Instance.from_db(instance_uuid)
                            if not inst:
                                continue
                            instance_sidechannel_cache[instance_uuid] = inst.side_channels

                        if 'sf-agent' not in instance_sidechannel_cache[instance_uuid]:
                            continue

                        p = util_process.fork(
                            self.single_instance_monitor, [instance_uuid],
                            'sidechannel-new')

                        self.monitors[instance_uuid] = p
                        eventlog.add_event(
                            EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                            'sidechannel monitor started')

                    # Cleanup extra monitors
                    for instance_uuid in extra_instances:
                        p = self.monitors[instance_uuid]
                        try:
                            os.kill(p.pid, signal.SIGTERM)
                            self.monitors[instance_uuid].join(1)
                        except Exception:
                            pass

                        del self.monitors[instance_uuid]
                        eventlog.add_event(
                            EVENT_TYPE_AUDIT, 'instance', instance_uuid,
                            'sidechannel monitor finished')

                elif len(self.monitors) > 0:
                    if running:
                        shutdown_commenced = time.time()
                        for instance_uuid in self.monitors:
                            pid = self.monitors[instance_uuid].pid
                            try:
                                LOG.info('Sent SIGTERM to sidechannel-%s (pid %s)'
                                         % (instance_uuid, pid))
                                os.kill(pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            except OSError as e:
                                LOG.warn('Failed to send SIGTERM to sidechannel-%s: %s'
                                         % (instance_uuid, e))

                        running = False

                    if time.time() - shutdown_commenced > 10:
                        LOG.warning('We have taken more than ten seconds to shut down')
                        LOG.warning('Dumping thread traces')
                        for instance_uuid in self.monitors:
                            pid = self.monitors[instance_uuid].pid
                            LOG.warning('sidechannel-%s daemon still running (pid %d)'
                                        % (instance_uuid, pid))
                            try:
                                os.kill(pid, signal.SIGUSR1)
                            except ProcessLookupError:
                                pass
                            except OSError as e:
                                LOG.warn('Failed to send SIGUSR1 to sidechannel-%s: %s'
                                         % (instance_uuid, e))

                else:
                    break

                self.exit.wait(1)

            except Exception as e:
                util_general.ignore_exception('sidechannel monitor', e)

        LOG.info('Terminated')
