import time

import os
import psutil
import pyprctl
import setproctitle
from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.daemons.queues import startup_tasks
from shakenfist.daemons.queues import workitem
from shakenfist.node import Node
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


class Monitor(daemon.WorkerPoolDaemon):
    def _check_other_daemon(self, daemon_name, override_daemon_name=None):
        daemon_running = False
        if os.path.exists(f'/run/sf-{daemon_name}.pid'):
            try:
                with open(f'/run/sf-{daemon_name}.pid') as f:
                    pid = int(f.read())
                psutil.Process(pid)
                daemon_running = True

            except (ValueError, psutil.NoSuchProcess):
                ...

        if override_daemon_name:
            daemon_name = override_daemon_name

        n = Node.from_db(config.NODE_NAME)
        if daemon_running:
            n.set_daemon_state(daemon_name, Node.DAEMON_STATE_RUNNING)
        else:
            n.set_daemon_state(daemon_name, Node.DAEMON_STATE_STOPPED)

    def _run_inner(self):
        warned_locks = {}
        last_checkin = 0
        last_length = 0
        last_third_party_health_check = 0

        # Note this while look is different from many of the other daemons
        # because we need to wait for work to terminate before exiting.
        done = False
        while not done:
            try:
                self.reap_workers()

                if time.time() - last_checkin > 5:
                    Node.observe_this_node()
                    last_checkin = time.time()

                if time.time() - last_third_party_health_check > 30:
                    # We also check in on the privexec and api daemon heres because
                    # they cannot do this for themselves...
                    self._check_other_daemon('privexec')
                    self._check_other_daemon(
                        'gunicorn', override_daemon_name='api')
                    last_third_party_health_check = time.time()

                # Check if we hold any locks for processes which don't exist any
                # more. That is, a process has ended but left a stray lock.
                #
                # NOTE(mikal): this doesn't really make sense with threads, but
                # given we want to get rid of locks anyways...
                locks = etcd.get_existing_locks()
                for lock in locks:
                    lock_details = locks[lock]
                    if lock_details.get('node') != config.NODE_NAME:
                        continue

                    pid = lock_details.get('pid')
                    if psutil.pid_exists(pid):
                        continue
                    if pid not in warned_locks:
                        LOG.with_fields(lock_details).warning(
                            'Lock held by missing process on this node')
                        warned_locks[pid] = time.time()
                    elif time.time() - warned_locks[pid] > 30:
                        LOG.with_fields(lock_details).error(
                            'Lock held by missing process on this node for more '
                            'than 30 seconds')

                if not self.exit.is_set():
                    if time.time() - last_length > 10:
                        processing, queued, deferred = etcd.get_queue_length(
                            config.NODE_NAME)
                        LOG.with_fields({
                            'processing': processing,
                            'queued': queued,
                            'deferred': deferred
                        }).debug('Queue length')
                        last_length = time.time()

                    if not self.dequeue_job(config.NODE_NAME, workitem.Job):
                        self.exit.wait(0.2)
                elif len(self.workers) > 0:
                    LOG.info('Waiting for %d workers to finish' %
                             len(self.workers))
                    self.exit.wait(0.2)
                else:
                    done = True

            except Exception as e:
                util_general.ignore_exception('queue worker', e)

            self.check_daemon_state()


def main():
    # Because we do work before starting the queue thread, we need to name
    # ourselves here too.
    name = f'{daemon.process_name("queues")} startup'
    setproctitle.setproctitle(name)
    pyprctl.set_name(name)
    startup_tasks.startup_tasks()

    m = Monitor('queues')
    m.run()
