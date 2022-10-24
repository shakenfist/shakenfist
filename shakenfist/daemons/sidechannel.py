import multiprocessing
import os
import select
import setproctitle
import signal
from shakenfist_agent import protocol
import time

from shakenfist.daemons import daemon
from shakenfist import eventlog
from shakenfist import instance
from shakenfist import logutil
from shakenfist.util import libvirt as util_libvirt


LOG, _ = logutil.setup(__name__)


class ConnectionIdle(Exception):
    pass


class SFSocketAgent(protocol.SocketAgent):
    def __init__(self, inst, path, logger=None):
        super(SFSocketAgent, self).__init__(path, logger=logger)
        self.instance = inst
        self.instance_ready = None
        self.system_boot_time = 0
        self.last_response = time.time()

        self.poll_tasks.append(self.is_system_running)

        self.add_command('agent-start', self.agent_start)
        self.add_command('agent-stop', self.agent_start)
        self.add_command('is-system-running-response',
                         self.is_system_running_response)
        self.add_command('gather-facts-response',
                         self.gather_facts_response)

        self.instance.agent_state = 'not ready (no contact)'

    def poll(self):
        if time.time() - self.last_response > 15:
            self.instance.agent_state = 'not ready (unresponsive)'
            self.log.debug('Not receiving traffic, aborting.')
            if self.system_boot_time != 0:
                self.instance.add_event2(
                    'agent has gone silent, restarting channel')

            # Attempt to close, but the OS might already think its closed
            try:
                self.close()
            except OSError:
                pass

            raise ConnectionIdle()

        super(SFSocketAgent, self).poll()

    def dispatch_packet(self, packet):
        self.last_response = time.time()
        super(SFSocketAgent, self).dispatch_packet(packet)

    def _record_system_boot_time(self, sbt):
        if sbt != self.system_boot_time:
            if self.system_boot_time != 0:
                self.instance.add_event2('reboot detected')
            self.system_boot_time = sbt
            self.instance.agent_system_boot_time = sbt

    def agent_start(self, packet):
        self.instance.agent_state = 'not ready (agent start)'
        self.instance.agent_start_time = time.time()
        sbt = packet.get('system_boot_time', 0)
        self._record_system_boot_time(sbt)

    def agent_stop(self, _packet):
        self.instance.agent_state = 'not ready (stopped)'

    def is_system_running(self):
        self.send_packet({'command': 'is-system-running'})

    def is_system_running_response(self, packet):
        ready = packet.get('result', 'False')
        sbt = packet.get('system_boot_time', 0)
        self._record_system_boot_time(sbt)

        if ready:
            new_state = 'ready'
        else:
            # Special case the degraded state here, as the system is in fact
            # as ready as it is ever going to be, but isn't entirely happy.
            if packet.get('message', 'none') == 'degraded':
                new_state = 'ready (degraded)'
            else:
                new_state = 'not ready (%s)' % packet.get('message', 'none')

        # We cache the agent state to reduce database load, and then
        # trigger facts gathering when we transition into the 'ready' state.
        if self.instance_ready != new_state:
            self.instance.agent_state = new_state
            self.instance_ready = new_state
            if new_state == 'ready':
                self.gather_facts()

    def gather_facts(self):
        self.send_packet({'command': 'gather-facts'})

    def gather_facts_response(self, packet):
        self.instance.add_event2('received system facts')
        self.instance.agent_facts = packet.get('result', {})


class Monitor(daemon.Daemon):
    def monitor(self, instance_uuid):
        setproctitle.setproctitle(
            '%s-%s' % (daemon.process_name('sidechannel'), instance_uuid))

        inst = instance.Instance.from_db(instance_uuid)
        log_ctx = LOG.with_instance(instance_uuid)
        if inst.state.value == instance.Instance.STATE_DELETED:
            return

        console_path = os.path.join(inst.instance_path, 'console.log')
        while not os.path.exists(console_path):
            time.sleep(1)
        inst.add_event2('detected console log')

        sc_clients = {}
        sc_connected = {}

        def build_side_channel_sockets():
            if not inst.side_channels:
                return

            for sc in inst.side_channels:
                if sc not in sc_clients:
                    sc_path = os.path.join(inst.instance_path, 'sc-%s' % sc)
                    if os.path.exists(sc_path):
                        try:
                            sc_clients[sc] = SFSocketAgent(
                                inst, sc_path, logger=log_ctx)
                            sc_connected[sc] = False
                            sc_clients[sc].send_ping()
                        except (BrokenPipeError,
                                ConnectionRefusedError,
                                ConnectionResetError,
                                FileNotFoundError,
                                OSError):
                            if sc in sc_clients:
                                del sc_clients[sc]

        build_side_channel_sockets()

        while True:
            readable = []
            for sc in sc_clients.values():
                readable.append(sc.input_fileno)
            readable, _, exceptional = select.select(readable, [], readable, 1)

            for fd in readable:
                chan = None
                for sc in sc_clients:
                    if fd == sc_clients[sc].input_fileno:
                        chan = sc

                if chan:
                    try:
                        for packet in sc_clients[chan].find_packets():
                            try:
                                if not sc_connected.get(chan, False):
                                    inst.add_event2(
                                        'sidechannel %s connected' % chan)
                                    sc_connected[chan] = True

                                sc_clients[chan].dispatch_packet(
                                    packet)
                            except protocol.UnknownCommand:
                                log_ctx.with_field('sidechannel', chan).info(
                                    'Ignored sidechannel packet: %s' % packet)

                    except (BrokenPipeError,
                            ConnectionRefusedError,
                            ConnectionResetError,
                            FileNotFoundError,
                            OSError):
                        del sc_clients[chan]

            for fd in exceptional:
                for sc_name in sc_clients:
                    if fd == sc_clients[sc_name].input_fileno:
                        sc_clients[sc_name].close()
                        del sc_clients[sc_name]

            build_side_channel_sockets()

            if inst.side_channels:
                for sc in inst.side_channels:
                    if sc in sc_clients:
                        try:
                            sc_clients[sc].poll()
                        except (BrokenPipeError,
                                ConnectionRefusedError,
                                ConnectionResetError,
                                FileNotFoundError,
                                ConnectionIdle):
                            if sc in sc_clients:
                                del sc_clients[sc]

    def run(self):
        LOG.info('Starting')
        monitors = {}

        while not self.exit.is_set():
            with util_libvirt.LibvirtConnection() as lc:
                # Cleanup terminated monitors
                all_monitors = list(monitors.keys())
                for instance_uuid in all_monitors:
                    if not monitors[instance_uuid].is_alive():
                        # Reap process
                        monitors[instance_uuid].join(1)
                        eventlog.add_event2(
                            'instance', instance_uuid, 'sidechannel monitor crashed')
                        del monitors[instance_uuid]

                # Audit desired monitors
                extra_instances = list(monitors.keys())
                missing_instances = []

                # The goal here is to find all instances running on this node so
                # that we can monitor them. We used to query etcd for this, but
                # we needed to do so frequently and it created a lot of etcd load.
                # We also can't use the existence of instance folders (which once
                # seemed like a good idea at the time), because some instances might
                # also be powered off. Instead, we ask libvirt what domains are
                # running.
                for domain in lc.get_sf_domains():
                    state = lc.extract_power_state(domain)
                    if state in ['off', 'crashed', 'paused']:
                        # If the domain isn't running, it shouldn't have a
                        # sidechannel monitor.
                        continue

                    instance_uuid = domain.name().split(':')[1]
                    if instance_uuid in extra_instances:
                        extra_instances.remove(instance_uuid)
                    if instance_uuid not in monitors:
                        missing_instances.append(instance_uuid)

                # Start missing monitors
                for instance_uuid in missing_instances:
                    p = multiprocessing.Process(
                        target=self.monitor, args=(instance_uuid,),
                        name='%s-%s' % (daemon.process_name('sidechannel'),
                                        instance_uuid))
                    p.start()

                    monitors[instance_uuid] = p
                    eventlog.add_event2(
                        'instance', instance_uuid, 'sidechannel monitor started')

                # Cleanup extra monitors
                for instance_uuid in extra_instances:
                    p = monitors[instance_uuid]
                    try:
                        os.kill(p.pid, signal.SIGKILL)
                        monitors[instance_uuid].join(1)
                    except Exception:
                        pass

                    del monitors[instance_uuid]
                    eventlog.add_event2(
                        'instance', instance_uuid, 'sidechannel monitor finished')

                self.exit.wait(1)

        for instance_uuid in monitors:
            os.kill(monitors[instance_uuid].pid, signal.SIGKILL)

        LOG.info('Terminating')
