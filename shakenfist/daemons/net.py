import copy
import logging
import re
import setproctitle
import time

from oslo_concurrency import processutils

from shakenfist.db import impl as db
from shakenfist.net import impl as net
from shakenfist import util


logging.basicConfig(level=logging.DEBUG)

LOG = logging.getLogger(__file__)
LOG.setLevel(logging.DEBUG)


VXLAN_RE = re.compile('[0-9]+: vxlan-([0-9]+).*')


def _get_deployed_vxlans():
    stdout, _ = processutils.execute('ip link', shell=True)
    for line in stdout.split('\n'):
        m = VXLAN_RE.match(line)
        if m:
            yield int(m.group(1))


class monitor(object):
    def __init__(self):
        setproctitle.setproctitle('sf net')

    def run(self):
        while True:
            time.sleep(30)

            # And then maintain the mesh with new nodes / networks
            #node_ips = list(db.get_node_ips())
            # for n in _get_list_of_network_uuids():
            #    network = net.from_db(n)
            #    network.ensure_mesh(copy.deepcopy(node_ips))

            # And remove any stray networks
