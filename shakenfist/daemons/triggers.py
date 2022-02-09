from functools import partial
import multiprocessing
import os
import re
import setproctitle
import signal
import time

from shakenfist import baseobject
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import eventlog
from shakenfist import logutil
from shakenfist import instance


LOG, _ = logutil.setup(__name__)


def observe(path, instance_uuid):
    setproctitle.setproctitle(
        '%s-%s' % (daemon.process_name('triggers'), instance_uuid))
    regexps = {
        'login prompt': re.compile('.* login: .*'),
        'user-data script start': re.compile('.*Starting.*Execute cloud user/final scripts.*'),
        'user-data script end': re.compile('.*Finished.*Execute cloud user/final scripts.*'),
        'cloud-init complete': re.compile('.*Reached target.*Cloud-init target.*')
    }

    while not os.path.exists(path):
        time.sleep(1)
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)

    log_ctx = LOG.with_instance(instance_uuid)
    log_ctx.with_field('path', path).info('Monitoring path for triggers')
    eventlog.add_event('instance', instance_uuid, 'trigger monitor',
                       'detected console log', None, None)

    # Sometimes the trigger process is slow to start, so rewind 4KB to ensure
    # that the last few log lines are not missed. (4KB since Cloud-Init can be
    # noisy after the login prompt.)
    os.lseek(fd, max(0, os.fstat(fd).st_size - 4096), os.SEEK_SET)

    # Record how long the file is, because we need to detect truncations and
    # re-open.
    previous_size = os.stat(path).st_size

    buffer = ''
    while True:
        # Detect file truncations, and die if we see one. We will be restarted
        # by the monitor process.
        if not os.path.exists(path):
            return
        size = os.stat(path).st_size
        if size < previous_size:
            return
        previous_size = size

        # Read data, os.read() is non-blocking by the way.
        d = os.read(fd, 1024).decode('utf-8', errors='ignore')
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
                            eventlog.add_event('instance', instance_uuid, 'trigger',
                                               None, None, trigger)
        else:
            # Only pause if there was no data to read
            time.sleep(0.2)


class Monitor(daemon.Daemon):
    def run(self):
        LOG.info('Starting')
        observers = {}

        while not self.exit.is_set():
            # Cleanup terminated observers
            all_observers = list(observers.keys())
            for instance_uuid in all_observers:
                if not observers[instance_uuid].is_alive():
                    # Reap process
                    observers[instance_uuid].join(1)
                    LOG.with_instance(instance_uuid
                                      ).info('Trigger observer has terminated')
                    eventlog.add_event(
                        'instance', instance_uuid, 'trigger monitor', 'crashed', None, None)
                    del observers[instance_uuid]

            # Audit desired observers
            extra_instances = list(observers.keys())
            missing_instances = []

            with etcd.ThreadLocalReadOnlyCache():
                for inst in instance.Instances([
                        instance.this_node_filter,
                        partial(baseobject.state_filter, [instance.Instance.STATE_CREATED])]):
                    if inst.uuid in extra_instances:
                        extra_instances.remove(inst.uuid)

                    if inst.uuid not in observers:
                        missing_instances.append(inst.uuid)

            # Start missing observers
            for instance_uuid in missing_instances:
                console_path = os.path.join(
                    config.STORAGE_PATH, 'instances', instance_uuid, 'console.log')
                p = multiprocessing.Process(
                    target=observe, args=(console_path, instance_uuid),
                    name='%s-%s' % (daemon.process_name('triggers'),
                                    instance_uuid))
                p.start()

                observers[instance_uuid] = p
                LOG.with_instance(instance_uuid).info(
                    'Started trigger observer')
                eventlog.add_event(
                    'instance', instance_uuid, 'trigger monitor', 'started', None, None)

            # Cleanup extra observers
            for instance_uuid in extra_instances:
                p = observers[instance_uuid]
                try:
                    os.kill(p.pid, signal.SIGKILL)
                    observers[instance_uuid].join(1)
                except Exception:
                    pass

                del observers[instance_uuid]
                LOG.with_instance(instance_uuid).info(
                    'Finished trigger observer')
                eventlog.add_event(
                    'instance', instance_uuid, 'trigger monitor', 'finished', None, None)

            self.exit.wait(1)

        # No longer running, clean up all trigger deaemons
        for instance_uuid in observers:
            os.kill(observers[instance_uuid].pid, signal.SIGKILL)
