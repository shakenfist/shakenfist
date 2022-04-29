# A simple shim to start internal daemons. This should only be called from
# main.py.

import sys

from shakenfist.daemons import external_api as external_api_daemon
from shakenfist.daemons import cleaner as cleaner_daemon
from shakenfist.daemons import cluster as cluster_daemon
from shakenfist.daemons import eventlog as eventlog_daemon
from shakenfist.daemons import queues as queues_daemon
from shakenfist.daemons import net as net_daemon
from shakenfist.daemons import resources as resource_daemon
from shakenfist.daemons import sidechannel as sidechannel_daemon


DAEMON_IMPLEMENTATIONS = {
    'api': external_api_daemon,
    'cleaner': cleaner_daemon,
    'cluster': cluster_daemon,
    'eventlog': eventlog_daemon,
    'net': net_daemon,
    'queues': queues_daemon,
    'resources': resource_daemon,
    'sidechannel': sidechannel_daemon
}


def main():
    d = sys.argv[1]
    DAEMON_IMPLEMENTATIONS[d].Monitor(d).run()
