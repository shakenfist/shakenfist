# NOTE(mikal): this daemon's role is to notice that you've exited the Shaken
# Fist target (run-level) in old speak and therefore the node is stopping not
# going missing. You should never manually stop this daemon!
import signal
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
        LOG.info('Sentinel received SIGTERM')
        EXIT.set()


signal.signal(signal.SIGTERM, exit_gracefully)


def main():
    global EXIT
    last_checkin = 0
    LOG.info('Sentinel started')

    n = Node.from_db(config.NODE_NAME)
    n.set_daemon_state('sentinel', Node.DAEMON_STATE_RUNNING)

    while not EXIT.is_set():
        if time.time() - last_checkin > 5:
            Node.observe_this_node()
            last_checkin = time.time()

        time.sleep(0.5)

    n.state = Node.STATE_STOPPING
    LOG.info('Sentinel requested node stop')

    start_time = time.time()
    all_daemons = n.get_registered_daemons()
    while all_daemons:
        for degraded in n.get_degraded_daemons():
            if degraded in all_daemons:
                all_daemons.remove(degraded)

        duration = time.time() - start_time
        LOG.with_fields({
            'have_waited': duration,
            'remaining_daemons': all_daemons
        }).info('Waiting for daemons to stop')
        time.sleep(5)

    LOG.info('All daemons not stopped')
    n.state = Node.STATE_STOPPED
