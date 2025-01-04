import os
import time

from shakenfist_utilities import logs  # noreorder

from shakenfist.baseobject import DatabaseBackedObject as dbo
from shakenfist import exceptions
from shakenfist import instance
from shakenfist import network
from shakenfist.networkinterface import NetworkInterface
from shakenfist.util import concurrency as util_concurrency


LOG, _ = logs.setup(__name__)


class Job(util_concurrency.Job):
    def __init__(self, name):
        super().__init__()
        self.name = name

        self.abort_path = f'/run/sf/net-{name}.abort'
        if os.path.exists(self.abort_path):
            os.unlink(self.abort_path)

    def execute(self):
        LOG.info('Starting NIC IP reaper')
        last_loop = 0

        while not os.path.exists(self.abort_path):
            if time.time() - last_loop < 30:
                time.sleep(1)
                continue

            last_loop = time.time()
            LOG.info('Scanning for stray network interfaces')
            for n in network.Networks([], prefilter='active'):
                try:
                    t = time.time()
                    for ni_uuid in n.networkinterfaces:
                        ni = NetworkInterface.from_db(ni_uuid)
                        if not ni:
                            continue

                        inst = instance.Instance.from_db(ni.instance_uuid)
                        if not inst:
                            ni.delete()
                            LOG.with_fields({
                                'networkinterface': ni,
                                'instance': ni.instance_uuid}).info(
                                'Deleted stray network interface for missing instance')
                        else:
                            s = inst.state
                            if (s.update_time + 30 < t and
                                    s.value in [dbo.STATE_DELETED, dbo.STATE_ERROR, 'unknown']):
                                ni.delete()
                                LOG.with_fields({
                                    'networkinterface': ni,
                                    'instance': ni.instance_uuid}).info(
                                    'Deleted stray network interface')

                except exceptions.LockException:
                    pass
