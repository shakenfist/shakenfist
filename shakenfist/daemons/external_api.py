import os

from oslo_concurrency import processutils

from shakenfist import config
from shakenfist.daemons import daemon
from shakenfist import logutil


class Monitor(daemon.Daemon):
    def run(self):
        logutil.info(None, 'Starting')
        processutils.execute(
            (config.parsed.get('API_COMMAND_LINE')
             % {
                 'port': config.parsed.get('API_PORT'),
                 'timeout': config.parsed.get('API_TIMEOUT'),
                 'name': daemon.process_name('api')
            }),
            shell=True, env_variables=os.environ)
