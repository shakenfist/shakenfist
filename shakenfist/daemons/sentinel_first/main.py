# NOTE(mikal): this daemon's role is to notice that the node has been started
# or shutdown. You should never manually stop this daemon!
#
# This daemon starts after the database service, so it uses the database
# service for both etcd and MariaDB access like other daemons.
import setproctitle
import signal
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.daemons.daemon import send_systemd_ready
from shakenfist.daemons.daemon import send_systemd_stopping
from shakenfist.node import Node
from shakenfist.util import exceptions as util_exceptions


LOG, _ = logs.setup(__name__)
ABORT_PATH = '/run/sf/sentinel-first.abort'


def exit_gracefully(sig, _frame):
    if sig == signal.SIGTERM:
        LOG.info('Received SIGTERM')
        daemon.set_abort_path(
            ABORT_PATH, 'from sentinel first exit_gracefully')


signal.signal(signal.SIGTERM, exit_gracefully)


def record_stop(n):
    """Record this daemon stopping, and the node stopped if it is.

    The handshake is that a whole-node stop (sf.target, a reboot) stops
    sentinel-last first, which marks the node stopping, and this daemon last,
    which completes it to stopped. A deploy does not stop sf.target: it
    restarts each unit on its own, in start order, so this daemon stops while
    sentinel-last is still running and the node is not stopping. Marking it
    stopped then would be false, and is refused anyway -- recording this
    daemon stopped has just reconciled the node to degraded, and degraded to
    stopped is not a valid transition -- which used to raise out of main()
    and fail the unit on every hypervisor on every deploy (#4507). So the
    lifecycle is only completed when sentinel-last has started it, and a
    restart of this daemon alone leaves it for sentinel-last's restart to
    return to created. Returns True if the node was marked stopped.
    """
    n.set_daemon_state('sentinel-first', Node.DAEMON_STATE_STOPPED)
    current = n.state.value
    if current != Node.STATE_STOPPING:
        LOG.info('Node is %s rather than stopping, so this is a restart of '
                 'sentinel-first alone; leaving the node lifecycle unchanged'
                 % current)
        return False
    return n.set_lifecycle_state(Node.STATE_STOPPED)


def main():
    util_exceptions.install_exception_tracking()
    daemon.clear_abort_path(ABORT_PATH)
    setproctitle.setproctitle('sf-sentinel-first')
    from shakenfist.util.caller_identity import set_caller_identity
    set_caller_identity('sentinel-first')
    LOG.info('Started')

    n = Node.from_db(config.NODE_NAME)
    n.set_daemon_state('sentinel-first', Node.DAEMON_STATE_RUNNING)
    n.set_lifecycle_state(Node.STATE_DEGRADED)
    send_systemd_ready()

    while daemon.check_abort_path(ABORT_PATH):
        LOG.debug('Checking in')
        Node.observe_this_node()
        time.sleep(15)

    LOG.info('Stopping')
    send_systemd_stopping()

    record_stop(n)
    LOG.info('Stopped')

    daemon.force_clean_exit()
