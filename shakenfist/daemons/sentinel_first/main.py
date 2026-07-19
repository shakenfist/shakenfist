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


def main():
    util_exceptions.install_exception_tracking()
    daemon.clear_abort_path(ABORT_PATH)
    setproctitle.setproctitle('sf-sentinel-first')
    from shakenfist.util.caller_identity import set_caller_identity
    set_caller_identity('sentinel-first')
    LOG.info('Started')

    n = Node.from_db(config.NODE_NAME)
    n.set_daemon_state('sentinel-first', Node.DAEMON_STATE_RUNNING)
    n.state = Node.STATE_DEGRADED
    send_systemd_ready()

    while daemon.check_abort_path(ABORT_PATH):
        LOG.debug('Checking in')
        Node.observe_this_node()
        time.sleep(15)

    LOG.info('Stopping')
    send_systemd_stopping()

    n.set_daemon_state('sentinel-first', Node.DAEMON_STATE_STOPPED)
    n.state = Node.STATE_STOPPED
    LOG.info('Stopped')

    daemon.force_clean_exit()
