import os
import signal

from shakenfist_utilities import logs  # noreorder

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist.node import Node
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


class Monitor(daemon.Daemon):
    def run(self):
        LOG.info('Starting')
        self.record_start()

        os.makedirs('/var/run/sf', exist_ok=True)
        util_concurrency.execute(
            None,
            config.API_COMMAND_LINE % {
                'port': config.API_PORT,
                'timeout': config.API_TIMEOUT,
                'name': daemon.process_name('api')
            },
            env_variables=os.environ,
            check_exit_code=[0, 1, -15])

        LOG.info('Terminated')
        self.record_exit()

    def exit_gracefully(self, sig, _frame):
        if sig == signal.SIGTERM:
            self.log.info('Caught SIGTERM, terminating')
            n = Node.from_db(config.NODE_NAME)
            n.set_daemon_state(self.daemon_name, Node.DAEMON_STATE_STOPPING)

            if os.path.exists('/var/run/sf/gunicorn.pid'):
                with open('/var/run/sf/gunicorn.pid') as f:
                    pid = int(f.read())
                    os.kill(pid, signal.SIGTERM)
                self.log.info(
                    'Caught SIGTERM, requested shutdown of gunicorn pid %d' % pid)
            else:
                self.log.info('No recorded gunicorn pid, could not terminate')


def main():
    m = Monitor('api')
    m.run()
