# NOTE(mikal): this daemon's role is to notice that you've exited the Shaken
# Fist target run-level and therefore the node is stopping not going missing.
# You should never manually stop this daemon!
import setproctitle
import signal
import sys
import threading
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.node import Node


LOG, _ = logs.setup(__name__)
EXIT = threading.Event()


def exit_gracefully(sig, _frame):
    global EXIT
    if sig == signal.SIGTERM:
        LOG.info('Received SIGTERM')
        EXIT.set()


signal.signal(signal.SIGTERM, exit_gracefully)


def main():
    global EXIT
    LOG.info('Started')
    setproctitle.setproctitle('sf-sentinel-last')

    n = Node.from_db(config.NODE_NAME)
    n.set_daemon_state('sentinel-last', Node.DAEMON_STATE_RUNNING)

    while not EXIT.is_set():
        LOG.debug('Checking in')
        Node.observe_this_node()
        time.sleep(15)

    LOG.info('Stopping')
    n.set_daemon_state('sentinel-last', Node.DAEMON_STATE_STOPPED)
    n.state = Node.STATE_STOPPING
    LOG.info('Stopped')

    # This is here because sometimes the grpc bits don't shut down cleanly
    # by themselves.
    sys.exit(0)
