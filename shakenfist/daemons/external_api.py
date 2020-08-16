import logging
import os

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist.daemons import daemon

LOG = logging.getLogger(__name__)


class Monitor(daemon.Daemon):
    def run(self):
        LOG.info('Starting')
        processutils.execute(
            (config.parsed.get('API_COMMAND_LINE')
             % {
                 'port': config.parsed.get('API_PORT'),
                 'timeout': config.parsed.get('API_TIMEOUT'),
                 'name': daemon.process_name('api')
            }),
            shell=True, env_variables=os.environ)
