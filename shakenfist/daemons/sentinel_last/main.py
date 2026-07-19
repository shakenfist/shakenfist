# NOTE(mikal): this daemon's role is to notice that you've exited the Shaken
# Fist target run-level and therefore the node is stopping not going missing.
# You should never manually stop this daemon!
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
ABORT_PATH = '/run/sf/sentinel-last.abort'


def exit_gracefully(sig, _frame):
    if sig == signal.SIGTERM:
        LOG.info('Received SIGTERM')
        daemon.set_abort_path(ABORT_PATH, 'from sentinel last exit_gracefully')


signal.signal(signal.SIGTERM, exit_gracefully)


def main():
    util_exceptions.install_exception_tracking()
    daemon.clear_abort_path(ABORT_PATH)
    setproctitle.setproctitle('sf-sentinel-last')
    from shakenfist.util.caller_identity import set_caller_identity
    set_caller_identity('sentinel-last')
    LOG.info('Started')

    n = Node.from_db(config.NODE_NAME)
    n.set_daemon_state('sentinel-last', Node.DAEMON_STATE_RUNNING)

    # sentinel-last starts after every other daemon (the systemd ordering
    # contract), so reaching here means the node is fully up: declare it
    # created, mirroring how our shutdown declares it stopping. Without
    # this a restarted node is stranded in stopping or degraded -- the
    # only other path back to created is the cluster daemon's
    # missing-node recovery, which never fires while checkins stay
    # fresh. Being the last daemon to start also makes this the final
    # state write in any restart interleaving.
    n.state = Node.STATE_CREATED
    send_systemd_ready()

    while daemon.check_abort_path(ABORT_PATH):
        LOG.debug('Checking in')
        Node.observe_this_node()
        time.sleep(15)

    LOG.info('Stopping')
    send_systemd_stopping()
    n.set_daemon_state('sentinel-last', Node.DAEMON_STATE_STOPPED)
    n.state = Node.STATE_STOPPING
    LOG.info('Stopped')

    daemon.force_clean_exit()
