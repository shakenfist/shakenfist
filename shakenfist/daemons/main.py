# Copyright 2019 Michael Still

import logging
import setproctitle
import time
import os

from shakenfist.daemons import api as api_daemon
from shakenfist.daemons import net as net_daemon
from shakenfist.db import impl as db
from shakenfist.net import impl as net
from shakenfist import util


logging.basicConfig(level=logging.DEBUG)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


def main():
    # Network mesh maintenance
    net_pid = os.fork()
    if net_pid == 0:
        net_daemon.monitor().run()

    # REST API
    api_pid = os.fork()
    if api_pid == 0:
        api_daemon.monitor().run()

    setproctitle.setproctitle('sf main')
    LOG.info('api pid is %s' % api_pid)
    LOG.info('net pid is %d' % net_pid)

    while True:
        time.sleep(10)
        wpid, status = os.waitpid(-1, os.WNOHANG)
        if wpid != 0:
            LOG.warning('Subprocess %d died' % wpid)
