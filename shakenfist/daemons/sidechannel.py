import base64
import os
import select
import setproctitle
from shakenfist_utilities import logs
import signal
from shakenfist_agent import protocol
import tempfile
import time

from shakenfist.agentoperation import AgentOperation
from shakenfist.blob import Blob
from shakenfist.daemons import daemon
from shakenfist import eventlog
from shakenfist.eventlog import EVENT_TYPE_AUDIT, EVENT_TYPE_STATUS
from shakenfist import instance
from shakenfist.util import general as util_general
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import process as util_process


LOG, _ = logs.setup(__name__)


class ConnectionIdle(Exception):
    ...


class PutException(Exception):
    ...


class SFSocketAgent(protocol.SocketAgent):
    NEVER_TALKED = 'not ready (no contact)'
    STOPPED_TALKING = 'not ready (unresponsive)'
    AGENT_STARTED = 'not ready (agent startup)'
    AGENT_STOPPED = 'not ready (agent stopped)'
    AGENT_READY = 'ready'

    def __init__(self, inst, path, logger=None):
        super(SFSocketAgent, self).__init__(path, logger=logger)
        self.log = LOG.with_fields({'instance': inst})

        self.instance = inst
        self.system_boot_time = 0
        self.poll_tasks.append(self.is_system_running)

        self.incomplete_file_get = None

        self.add_command('agent-start', self.agent_start)
        self.add_command('agent-stop', self.agent_start)
        self.add_command('is-system-running-response',
                         self.is_system_running_response)
        self.add_command('gather-facts-response', self.gather_facts_response)
        self.add_command('put-file-response', self.put_file_response)
        self.add_command('get-file-response', self.get_file_response)
        self.add_command('watch-file-response', self.watch_file_response)
        self.add_command('execute-response', self.execute_response)
        self.add_command('chmod-response', self.chmod_response)
        self.add_command('chown-response', self.chown_response)

        self.instance_ready = self.NEVER_TALKED
        self.instance.agent_state = self.NEVER_TALKED

    def poll(self):
        if time.time() - self.last_data > 5:
            if self.instance_ready == self.AGENT_READY and not self.incomplete_file_get:
                agentop = self.instance.agent_operation_dequeue()
                if agentop:
                    self.instance.add_event(
                        EVENT_TYPE_AUDIT, 'Dequeued agent operation',
                        extra={'agentoperation': agentop.uuid})

                    agentop.state = AgentOperation.STATE_EXECUTING
                    count = 0
                    for command in agentop.commands:
                        if command['command'] == 'put-blob':
                            b = Blob.from_db(command['blob_uuid'])
                            if not b:
                                agentop.error = 'blob missing: %s' % command['blob_uuid']
                                return
                            self.put_file(Blob.filepath(b.uuid), command['path'],
                                          'agentop:%s:%d' % (agentop.uuid, count))

                        elif command['command'] == 'chmod':
                            self.chmod(command['path'], command['mode'],
                                       'agentop:%s:%d' % (agentop.uuid, count))

                        elif command['command'] == 'execute':
                            self.execute(
                                command['commandline'], 'agentop:%s:%d' % (agentop.uuid, count),
                                block_for_result=True)

                        else:
                            self.instance.add_event(
                                EVENT_TYPE_AUDIT,
                                'Unknown agent operation command, aborting operation',
                                extra={
                                    'agentoperation': agentop.uuid,
                                    'command': command.get('command'),
                                    'count': count
                                    })
                            break

                        count += 1

        elif time.time() - self.last_data > 15:
            if self.instance.agent_state.value != self.NEVER_TALKED:
                self.instance_ready = self.STOPPED_TALKING
                self.instance.agent_state = self.STOPPED_TALKING
            self.log.debug('Not receiving traffic, aborting.')
            if self.system_boot_time != 0:
                self.instance.add_event(
                    EVENT_TYPE_STATUS, 'agent has gone silent, restarting channel')

            # Attempt to close, but the OS might already think its closed
            try:
                self.close()
            except OSError:
                pass

            raise ConnectionIdle()

        super(SFSocketAgent, self).poll()

    def _record_system_boot_time(self, sbt):
        if sbt != self.system_boot_time:
            if self.system_boot_time != 0:
                self.instance.add_event(EVENT_TYPE_AUDIT, 'reboot detected')
            self.system_boot_time = sbt
            self.instance.agent_system_boot_time = sbt

    def _record_result(self, packet):
        unique = packet.get('unique', '')
        if unique.startswith('agentop:'):
            _, agentop_uuid, index = unique.split(':')
            agentop = AgentOperation.from_db(agentop_uuid)
            if agentop:
                del packet['command']
                del packet['unique']
                agentop.add_result(index, packet)

                # We define complete as "have received a result for every command
                # we sent".
                num_commands = len(agentop.commands)
                num_results = len(agentop.results)
                if num_results == num_commands:
                    agentop.add_event(EVENT_TYPE_STATUS, 'Commands complete')
                    agentop.state = AgentOperation.STATE_COMPLETE
                else:
                    agentop.add_event(
                        EVENT_TYPE_STATUS, 'Commands not yet complete',
                        extra={'commands': num_commands, 'results': num_results})

    def agent_start(self, packet):
        self.instance_ready = self.AGENT_STARTED
        self.instance.agent_state = self.AGENT_STARTED
        self.instance.agent_start_time = time.time()
        sbt = packet.get('system_boot_time', 0)
        self._record_system_boot_time(sbt)

        if self.is_system_running not in self.poll_tasks:
            self.poll_tasks.append(self.is_system_running)

    def agent_stop(self, _packet):
        self.instance_ready = self.AGENT_STOPPED
        self.instance.agent_state = self.AGENT_STOPPED

    def is_system_running(self):
        self.send_packet({
            'command': 'is-system-running',
            'unique': str(time.time())
            })

    def is_system_running_response(self, packet):
        ready = packet.get('result', 'False')
        sbt = packet.get('system_boot_time', 0)
        self._record_system_boot_time(sbt)

        if ready:
            new_state = self.AGENT_READY
            if self.is_system_running in self.poll_tasks:
                self.poll_tasks.remove(self.is_system_running)
        else:
            # Special case the degraded state here, as the system is in fact
            # as ready as it is ever going to be, but isn't entirely happy.
            if packet.get('message', 'none') == 'degraded':
                new_state = 'ready (degraded)'
            else:
                new_state = 'not ready (%s)' % packet.get('message', 'none')

        self.log.debug('Agent state: old = %s; new = %s'
                       % (self.instance_ready, new_state))

        # We cache the agent state to reduce database load, and then
        # trigger facts gathering when we transition into the self.AGENT_READY state.
        if self.instance_ready != new_state:
            self.instance_ready = new_state
            self.instance.agent_state = new_state
            if new_state == self.AGENT_READY:
                self.gather_facts()

    def gather_facts(self):
        self.send_packet({
            'command': 'gather-facts',
            'unique': str(time.time())
            })

    def gather_facts_response(self, packet):
        self.instance.add_event(EVENT_TYPE_AUDIT, 'received system facts')
        self.instance.agent_facts = packet.get('result', {})

    def put_file(self, source_path, destination_path, unique):
        if not os.path.exists(source_path):
            raise PutException('source path %s does not exist' % source_path)
        self._send_file('put-file', source_path, destination_path, unique)

    def put_file_response(self, packet):
        self._record_result(packet)

    def get_file(self, path, unique):
        self.incomplete_file_get = {
            'flo': tempfile.NamedTemporaryFile(),
            'source_path': path,
            'callback': self.get_file_complete,
            'callback_args': {},
            'unique': unique
        }
        self.send_packet({
            'command': 'get-file',
            'path': path,
            'unique': unique
            })

    def get_file_response(self, packet):
        if not self.incomplete_file_get:
            self.log.with_fields(packet).warning('Unexpected file response')
            return

        if not packet['result']:
            self.log.with_fields(packet).warning('File get failed')
            return

        if 'chunk' not in packet:
            # A metadata packet
            self.incomplete_file_get.update(packet['stat_result'])
            return

        if packet['chunk'] is None:
            self.incomplete_file_get['flo'].close()
            self.incomplete_file_get['callback'](
                **self.incomplete_file_get['callback_args']
            )
            self.incomplete_file_get = None
            self.log.with_fields(packet).info('File get complete')
            return

        d = base64.b64decode(packet['chunk'])
        self.incomplete_file_get['flo'].write(d)

    def get_file_complete(self):
        pass

    def watch_file(self, path):
        self.send_packet({
            'command': 'watch-file',
            'path': path
            })

    def watch_file_response(self, packet):
        self.log.info('Received watch content for %s' % packet['path'])

    def execute(self, command, unique, block_for_result=False):
        self.send_packet({
            'command': 'execute',
            'command-line': command,
            'block-for-result': block_for_result,
            'unique': unique
            })

    def execute_response(self, packet):
        self._record_result(packet)

    def chmod(self, path, mode, unique):
        self.send_packet({
            'command': 'chmod',
            'path': path,
            'mode': mode,
            'unique': unique
            })

    def chmod_response(self, packet):
        self._record_result(packet)

    def chown(self, path, user, group, unique):
        self.send_packet({
            'command': 'chown',
            'user': user,
            'group': group,
            'unique': unique
            })

    def chown_response(self, packet):
        self._record_result(packet)


class Monitor(daemon.Daemon):
    def __init__(self, name):
        super(Monitor, self).__init__(name)
        self.monitors = {}

    def single_instance_monitor(self, instance_uuid):
        setproctitle.setproctitle('sf-sidechannel-%s' % instance_uuid)
        inst = instance.Instance.from_db(instance_uuid)
        log = LOG.with_fields({'instance': instance_uuid})
        if inst.state.value == instance.Instance.STATE_DELETED:
            return

        if 'sf-agent' not in inst.side_channels:
            return

        # We use the existence of a console.log file in the instance directory
        # to indicate the instance has been created. This will be true even if
        # the instance doesn't actually every write to the serial console.
        console_path = os.path.join(inst.instance_path, 'console.log')
        while not os.path.exists(console_path):
            time.sleep(1)
        inst.add_event(EVENT_TYPE_STATUS, 'detected console log')

        # Ensure side channel path exists.
        sc_path = os.path.join(inst.instance_path, 'sc-sf-agent')
        if not os.path.exists(sc_path):
            log.error('sf-agent side channel file missing, aborting')
            return

        sc_client = None
        sc_connected = False

        # Spin trying to setup a connection to the client
        while not sc_client:
            try:
                sc_client = SFSocketAgent(inst, sc_path, logger=log)
                break
            except (BrokenPipeError, ConnectionRefusedError, ConnectionResetError,
                    FileNotFoundError, OSError):
                time.sleep(1)

        # Spin reading packets and responding until we see an error or are asked
        # to exit.
        while not self.exit.is_set():
            readable, _, exceptional = select.select(
                [sc_client.input_fileno], [], [sc_client.input_fileno], 1)

            if readable:
                try:
                    for packet in sc_client.find_packets():
                        if not sc_connected:
                            inst.add_event(EVENT_TYPE_AUDIT, 'sidechannel connected')
                            sc_connected = True

                        sc_client.dispatch_packet(packet)

                except (BrokenPipeError, ConnectionRefusedError, ConnectionResetError,
                        FileNotFoundError, OSError):
                    return

            if exceptional:
                return

            try:
                sc_client.poll()
            except (BrokenPipeError, ConnectionRefusedError, ConnectionResetError,
                    FileNotFoundError, ConnectionIdle):
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
