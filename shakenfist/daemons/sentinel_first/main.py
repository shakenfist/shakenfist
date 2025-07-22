# NOTE(mikal): this daemon's role is to notice that the node has been started
# or shutdown. You should never manually stop this daemon!
import setproctitle
import signal
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.node import Node


LOG, _ = logs.setup(__name__)
ABORT_PATH = '/run/sf/sentinel-first.abort'


def exit_gracefully(sig, _frame):
    if sig == signal.SIGTERM:
        LOG.info('Received SIGTERM')
        daemon.set_abort_path(
            ABORT_PATH, 'from sentinel first exit_gracefully')


signal.signal(signal.SIGTERM, exit_gracefully)


def main():
    daemon.clear_abort_path(ABORT_PATH)
    setproctitle.setproctitle('sf-sentinel-first')
    LOG.info('Started')

    n = Node.from_db(config.NODE_NAME)
    n.set_daemon_state('sentinel-first', Node.DAEMON_STATE_RUNNING)
    n.state = Node.STATE_DEGRADED

    while daemon.check_abort_path(ABORT_PATH):
        LOG.debug('Checking in')
        Node.observe_this_node()
        time.sleep(15)

    LOG.info('Stopping')

    n.set_daemon_state('sentinel-first', Node.DAEMON_STATE_STOPPED)
    n.state = Node.STATE_STOPPED
    LOG.info('Stopped')

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    raise SystemExit(0)
