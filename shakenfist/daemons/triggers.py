import multiprocessing
import os
import re
import select
import setproctitle
import signal
from shakenfist_agent import protocol
import time

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import eventlog
from shakenfist import instance
from shakenfist import logutil


LOG, _ = logutil.setup(__name__)


class SFSocketAgent(protocol.SocketAgent):
    def __init__(self, inst, path, logger=None):
        super(SFSocketAgent, self).__init__(path, logger=logger)
        self.instance = inst
        self.instance_ready = False

        self.poll_tasks.append(self.is_system_running)

        self.add_command('is-system-running-response',
                         self.is_system_running_response)

    def is_system_running(self):
        self.send_packet({'command': 'is-system-running'})

    def is_system_running_response(self, packet):
        ready = packet.get('result', 'False')
        if ready:
            if self.is_system_running in self.poll_tasks:
                self.poll_tasks.remove(self.is_system_running)

        if self.instance_ready != ready:
            if ready:
                self.instance.add_event2('instance is ready')
            else:
                self.instance.add_event2(
                    'instance not ready (%s)' % packet.get('message', 'none'))
            self.instance_ready = ready


class Monitor(daemon.Daemon):
    def monitor(self, instance_uuid):
        setproctitle.setproctitle(
            '%s-%s' % (daemon.process_name('triggers'), instance_uuid))
        regexps = {
            'login prompt': re.compile('.* login: .*'),
            'user-data script start': re.compile('.*Starting.*Execute cloud user/final scripts.*'),
            'user-data script end': re.compile('.*Finished.*Execute cloud user/final scripts.*'),
            'cloud-init complete': re.compile('.*Reached target.*Cloud-init target.*')
        }

        inst = instance.Instance.from_db(instance_uuid)
        log_ctx = LOG.with_instance(instance_uuid)
        if inst.state.value == instance.Instance.STATE_DELETED:
            return

        console_path = os.path.join(inst.instance_path, 'console.log')
        while not os.path.exists(console_path):
            time.sleep(1)
        console_fd = os.open(console_path, os.O_RDONLY | os.O_NONBLOCK)

        log_ctx = LOG.with_instance(instance_uuid)
        eventlog.add_event('instance', instance_uuid, 'trigger monitor',
                           'detected console log', None, None)

        # Sometimes the trigger process is slow to start, so rewind 4KB to ensure
        # that the last few log lines are not missed. (4KB since Cloud-Init can be
        # noisy after the login prompt.)
        os.lseek(console_fd, max(0, os.fstat(
            console_fd).st_size - 4096), os.SEEK_SET)

        # Record how long the console file is, because we need to detect truncations
        # and re-open.
        previous_size = os.stat(console_path).st_size

        # If we have any side channels, open those as well.
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
                                FileNotFoundError):
                            if sc in sc_clients:
                                del sc_clients[sc]

        build_side_channel_sockets()

        buffer = ''
        while True:
            # Detect file truncations, and die if we see one. We will be restarted
            # by the monitor process.
            if not os.path.exists(console_path):
                return

            try:
                size = os.stat(console_path).st_size
                if size < previous_size:
                    return
            except FileNotFoundError:
                return
            previous_size = size

            readable = [console_fd]
            for sc in sc_clients.values():
                readable.append(sc.input_fileno)
            readable, _, exceptional = select.select(readable, [], readable, 1)

            for fd in readable:
                if fd == console_fd:
                    d = os.read(fd, 102400).decode('utf-8', errors='ignore')
                    if d:
                        buffer += d
                        lines = buffer.split('\n')
                        buffer = lines[-1]

                        for line in lines:
                            if line:
                                for trigger in regexps:
                                    m = regexps[trigger].match(line)
                                    if m:
                                        log_ctx.with_field('trigger', trigger,
                                                           ).info('Trigger matched')
                                        inst.add_event(
                                            'trigger', None, None, trigger)
                else:
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
                                            'side channel %s connected' % chan)
                                        sc_connected[chan] = True

                                    sc_clients[chan].dispatch_packet(
                                        packet)
                                except protocol.UnknownCommand:
                                    log_ctx.with_field('sidechannel', chan).info(
                                        'Ignored side channel packet: %s' % packet)

                        except (BrokenPipeError,
                                ConnectionRefusedError,
                                ConnectionResetError,
                                FileNotFoundError):
                            del sc_clients[chan]

            for fd in exceptional:
                for sc_name in sc_clients:
                    if fd == sc_clients[sc_name].input_fileno:
                        sc_clients[sc_name].close()
                        del sc_clients[sc_name]

                if fd == console_fd:
                    return

            build_side_channel_sockets()

            if inst.side_channels:
                for sc in inst.side_channels:
                    if sc in sc_clients:
                        try:
                            sc_clients[sc].poll()
                        except (BrokenPipeError,
                                ConnectionRefusedError,
                                ConnectionResetError,
                                FileNotFoundError):
                            if sc in sc_clients:
                                del sc_clients[sc]

    def run(self):
        LOG.info('Starting')
        monitors = {}

        while not self.exit.is_set():
            # Cleanup terminated monitors
            all_monitors = list(monitors.keys())
            for instance_uuid in all_monitors:
                if not monitors[instance_uuid].is_alive():
                    # Reap process
                    monitors[instance_uuid].join(1)
                    LOG.with_instance(instance_uuid
                                      ).info('Trigger observer has terminated')
                    eventlog.add_event(
                        'instance', instance_uuid, 'trigger monitor', 'crashed', None, None)
                    del monitors[instance_uuid]

            # Audit desired monitors
            extra_instances = list(monitors.keys())
            missing_instances = []

            # The goal here is to find all instances running on this node so
            # that we can monitor them. We used to query etcd for this, but
            # we needed to do so frequently and it created a lot of etcd load.
            # Instead, we just use the instance folders to signal that an
            # instance should be monitored.
            instance_path = os.path.join(config.STORAGE_PATH, 'instances')
            if os.path.exists(instance_path):
                for instance_uuid in os.listdir(instance_path):
                    if instance_uuid in extra_instances:
                        extra_instances.remove(instance_uuid)

                    if instance_uuid not in monitors:
                        missing_instances.append(instance_uuid)

            # Start missing monitors
            for instance_uuid in missing_instances:
                p = multiprocessing.Process(
                    target=self.monitor, args=(instance_uuid,),
                    name='%s-%s' % (daemon.process_name('triggers'),
                                    instance_uuid))
                p.start()

                monitors[instance_uuid] = p
                LOG.with_instance(instance_uuid).info(
                    'Started trigger observer')
                eventlog.add_event(
                    'instance', instance_uuid, 'trigger monitor', 'started', None, None)

            # Cleanup extra monitors
            for instance_uuid in extra_instances:
                p = monitors[instance_uuid]
                try:
                    os.kill(p.pid, signal.SIGKILL)
                    monitors[instance_uuid].join(1)
                except Exception:
                    pass

                del monitors[instance_uuid]
                LOG.with_instance(instance_uuid).info(
                    'Finished trigger observer')
                eventlog.add_event(
                    'instance', instance_uuid, 'trigger monitor', 'finished', None, None)

            self.exit.wait(1)

        for instance_uuid in monitors:
            os.kill(monitors[instance_uuid].pid, signal.SIGKILL)
