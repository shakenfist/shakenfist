import os
import time

import psutil
import setproctitle
from shakenfist_utilities import logs  # noreorder

from shakenfist import etcd
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.daemons.queues import startup_tasks
from shakenfist.daemons.queues import workitem
from shakenfist.node import Node
from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import general as util_general


LOG, _ = logs.setup(__name__)


def _check_other_daemon(n, daemon_name, override_daemon_name=None):
    recorded_daemon_name = daemon_name
    if override_daemon_name:
        recorded_daemon_name = override_daemon_name

    if not os.path.exists(f'/run/sf/{daemon_name}.pid'):
        n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                           message='pid file missing')

    try:
        with open(f'/run/sf/{daemon_name}.pid') as f:
            pid = int(f.read())
            psutil.Process(pid)
            n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_RUNNING)
            return
    except FileNotFoundError:
        n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                           message='pid file missing on read')
    except ValueError:
        n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                           message='pid file not parsable')
    except psutil.NoSuchProcess:
        n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                           message='process absent')

    n.set_daemon_state(recorded_daemon_name, Node.DAEMON_STATE_STOPPED,
                       message='unknown issue')


def _health_checks():
    n = Node.from_db(config.NODE_NAME)
    n.set_daemon_state('queues', Node.DAEMON_STATE_RUNNING)
    _check_other_daemon(n, 'privexec')
    _check_other_daemon(n, 'gunicorn', override_daemon_name='api')


class Monitor(daemon.WorkerPoolDaemon):
    def _run_inner(self):
        daemon.health_check_privexec()
        daemon.health_check_nodelock()

        warned_locks = {}
        last_third_party_health_check = 0

        n = Node.from_db(config.NODE_NAME)

        while daemon.check_abort_path(self.abort_path):
            try:
                self.reap_workers()

                if time.time() - last_third_party_health_check > 30:
                    # We also check in on the privexec and api daemon heres because
                    # they cannot do this for themselves...
                    _check_other_daemon(n, 'privexec')
                    _check_other_daemon(
                        n, 'gunicorn', override_daemon_name='api')
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

                if not self.dequeue_job(workitem.Job):
                    self.idle(0.2)

            except Exception as e:
                util_general.ignore_exception('queue worker', e)

            self.check_daemon_state()


def main():
    daemon.write_pid_file('queues')

    # Because we do work before starting the queue thread, we need to name
    # ourselves here too.
    name = f'{daemon.process_name("queues")} startup'
    setproctitle.setproctitle(name)
    util_concurrency.set_thread_name(name)
    _health_checks()

    start_time = time.time()
    startup_tasks.startup_tasks()
    duration = time.time() - start_time
    LOG.info(f'Startup tasks took {duration:.2f} seconds')

    m = Monitor('queues')
    m.run()

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    LOG.info('Terminating ourselves')
    raise SystemExit(0)
