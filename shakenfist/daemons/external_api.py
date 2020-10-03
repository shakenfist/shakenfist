import os

from shakenfist import config
from shakenfist.daemons import daemon
from shakenfist import logutil
from shakenfist import util


LOG, _ = logutil.setup(__name__)


class Monitor(daemon.Daemon):
    def run(self):
        LOG.info('Starting')
        util.execute(None,
                     (config.parsed.get('API_COMMAND_LINE')
                      % {
                         'port': config.parsed.get('API_PORT'),
                          'timeout': config.parsed.get('API_TIMEOUT'),
                          'name': daemon.process_name('api')
                     }),
                     env_variables=os.environ)
