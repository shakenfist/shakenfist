import os

from oslo_concurrency import processutils

from shakenfist.daemons import daemon
from shakenfist import config


class Monitor(daemon.Daemon):
    def run(self):
        processutils.execute(
            ('gunicorn3 --workers 10 --bind 0.0.0.0:%d '
             '--log-syslog --log-syslog-prefix sf '
             '--timeout %s --name "%s" '
             'shakenfist.external_api.app:app'
             % (config.parsed.get('API_PORT'),
                config.parsed.get('API_TIMEOUT'),
                daemon.process_name('api'))),
            shell=True, env_variables=os.environ)
