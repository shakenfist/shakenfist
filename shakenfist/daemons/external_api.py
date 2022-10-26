import os
import signal

from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import logutil
from shakenfist.util import libvirt as util_libvirt
from shakenfist.util import process as util_process


LOG, _ = logutil.setup(__name__)


class Monitor(daemon.Daemon):
    def run(self):
        LOG.info('Starting')

        present_cpus = util_libvirt.get_cpu_count()
        os.makedirs('/var/run/sf', exist_ok=True)
        util_process.execute(None, (config.API_COMMAND_LINE
                                    % {
                                        'port': config.API_PORT,
                                        'timeout': config.API_TIMEOUT,
                                        'name': daemon.process_name('api'),
                                        'workers': present_cpus * 2 + 1,
                                        'threads': present_cpus * 2 + 1
                                    }),
                             env_variables=os.environ,
                             check_exit_code=[0, 1, -15])

        LOG.info('Terminating')

    def exit_gracefully(self, sig, _frame):
        if sig == signal.SIGTERM:
            if os.path.exists('/var/run/sf/gunicorn.pid'):
                with open('/var/run/sf/gunicorn.pid') as f:
                    pid = int(f.read())
                    os.kill(pid, signal.SIGTERM)
                self.log.info(
                    'Caught SIGTERM, requested shutdown of gunicorn pid %d' % pid)
            else:
                self.log.info('No recorded gunicorn pid, could not terminate')
