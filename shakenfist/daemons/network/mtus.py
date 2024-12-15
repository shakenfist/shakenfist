from collections import defaultdict
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.util import concurrency as util_concurrency
from shakenfist.util import network as util_network


LOG, _ = logs.setup(__name__)


class Job(util_concurrency.Job):
    def execute(self):
        LOG.info('Starting MTU watchdog')
        last_loop = 0

        while not self.exit.is_set():
            if time.time() - last_loop < 30:
                time.sleep(1)
                continue

            last_loop = time.time()
            LOG.info('Validating network interface MTUs')

            by_mtu = defaultdict(list)
            for iface, mtu in util_network.get_interface_mtus():
                by_mtu[mtu].append(iface)

            for mtu in sorted(by_mtu):
                log = LOG.with_fields({
                    'mtu': mtu,
                    'interfaces': by_mtu[mtu]
                })
                if mtu < 1501:
                    log.warning('Interface MTU is 1500 bytes or less')
                else:
                    log.debug('Interface MTU is normal')
