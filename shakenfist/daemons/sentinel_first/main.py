# NOTE(mikal): this daemon's role is to notice that the node has been started
# or shutdown. You should never manually stop this daemon!
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
        LOG.info('Received SIGTERM')
        EXIT.set()


signal.signal(signal.SIGTERM, exit_gracefully)


def main():
    global EXIT
    last_checkin = 0
    LOG.info('Started')

    n = Node.from_db(config.NODE_NAME)
    n.set_daemon_state('sentinel-first', Node.DAEMON_STATE_RUNNING)
    n.state = Node.STATE_DEGRADED

    while not EXIT.is_set():
        if time.time() - last_checkin > 5:
            Node.observe_this_node()
            last_checkin = time.time()

        time.sleep(0.5)

    LOG.info('Stopping')

    # The default systemd timeout is 90 seconds, so wait just a bit less than
    # that, although it shouldn't be needed.
    start_time = time.time()
    all_daemons = n.get_registered_daemons()
    while all_daemons and time.time() - start_time < 80:
        for degraded in n.get_degraded_daemons():
            if degraded in all_daemons:
                all_daemons.remove(degraded)

        duration = round(time.time() - start_time, 2)
        LOG.with_fields({
            'have_waited': duration,
            'remaining_daemons': all_daemons
        }).info('Waiting for daemons to stop')
        time.sleep(5)

    n.set_daemon_state('sentinel-first', Node.DAEMON_STATE_STOPPED)
    n.state = Node.STATE_STOPPED
    LOG.info('Stopped')
