from functools import partial
import os
import time
from shakenfist_utilities import logs

from shakenfist import blob
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd


LOG, _ = logs.setup(__name__)


class Monitor(daemon.Daemon):
    def run(self):
        LOG.info('Starting')

        blob_path = os.path.join(config.STORAGE_PATH, 'blobs')
        while not self.exit.is_set():
            check_this_pass = []

            with etcd.ThreadLocalReadOnlyCache():
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
                            check_this_pass.append(b)
                            if len(check_this_pass) > 50:
                                break

                        if self.exit.is_set():
                            break

            for b in check_this_pass:
                # TODO(mikal): we should not do this if the node is really
                # busy.
                if b.verify_size():
                    b.verify_checksum()

                self.exit.wait(5)
                if self.exit.is_set():
                    break

            self.exit.wait(300)

        LOG.info('Terminating')
