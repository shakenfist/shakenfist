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
            blob.ensure_blob_path()
            check_this_pass = []

            with etcd.ThreadLocalReadOnlyCache():
                for ent in os.listdir(blob_path):
                    st = os.stat(os.path.join(blob_path, ent))
                    if time.time() - st.st_mtime < 60:
                        # File is too new to verify
                        continue

                    b = blob.Blob.from_db(ent)
                    if not b:
                        continue

                    this_node_last_checked = b.checksums.get(
                        'nodes', {}).get(config.NODE_NAME, 0)
                    if (time.time() - this_node_last_checked >
                            config.CHECKSUM_VERIFICATION_FREQUENCY):
                        # This node has either not recently checked this blob, or
                        # has never checked it. Generate a checksum.
                        check_this_pass.append(b.uuid)
                        if len(check_this_pass) > 50:
                            break

                    if self.exit.is_set():
                        break

            for blob_uuid in check_this_pass:
                b = blob.Blob.from_db(blob_uuid)
                # TODO(mikal): we should not do this if the node is really
                # busy.
                if b.verify_size():
                    b.update_checksum(hash)

                self.exit.wait(5)
                if self.exit.is_set():
                    break

            self.exit.wait(300)

        LOG.info('Terminating')
