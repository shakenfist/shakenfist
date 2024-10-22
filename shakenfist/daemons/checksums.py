# NOTE(mikal): do not use the etcd read only cache here -- it will cause cluster
# wide maintenance tasks to crash, and someone has to eventually run the upgrade.
#
# NOTE(mikal): there is a known issue with this implementation where if you have
# more bytes of blob on a node than we can checksum before the checksum renewal
# interval, we will never catch up. Our blob store isn't intended to be huge, so
# that is ok for now.
import os
import time
from functools import partial

from shakenfist_utilities import logs

from shakenfist import blob
from shakenfist.config import config
from shakenfist.daemons import daemon


LOG, _ = logs.setup(__name__)


class Monitor(daemon.Daemon):
    def run(self):
        LOG.info('Starting')

        blob_path = os.path.join(config.STORAGE_PATH, 'blobs')
        while not self.exit.is_set():
            for b in blob.Blobs([partial(blob.placement_filter, config.NODE_NAME)]):
                blob_path = blob.Blob.filepath(b.uuid)
                if os.path.exists(blob_path):
                    st = os.stat(blob_path)
                    if time.time() - st.st_mtime < 60:
                        # File is too new to verify
                        continue

                    this_node_last_checked = b.checksums.get(
                        'nodes', {}).get(config.NODE_NAME, 0)
                    if (time.time() - this_node_last_checked >
                            config.CHECKSUM_VERIFICATION_FREQUENCY):
                        # This node has either not recently checked this blob, or
                        # has never checked it. Generate a checksum.
                        # TODO(mikal): we should not do this if the node is really
                        # busy.
                        if b.verify_size():
                            b.verify_checksum(urgent=False)

                        self.exit.wait(5)
                        if self.exit.is_set():
                            break

            self.exit.wait(300)

        LOG.info('Terminated')
