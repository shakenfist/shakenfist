import os

from oslo_concurrency import processutils

from shakenfist.daemons import daemon
from shakenfist import config


class Monitor(daemon.Daemon):
    def run(self):
        processutils.execute(
            (config.parsed.get('API_COMMAND_LINE')
             % {
                 'port': config.parsed.get('API_PORT'),
                 'timeout': config.parsed.get('API_TIMEOUT'),
                 'name': daemon.process_name('api')
            }),
            shell=True, env_variables=os.environ)
