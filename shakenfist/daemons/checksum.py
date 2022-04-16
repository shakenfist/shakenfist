import os
import time

from shakenfist import blob
from shakenfist.config import config
from shakenfist.daemons import daemon
from shakenfist import etcd
from shakenfist import logutil
from shakenfist.util import process as util_process


LOG, _ = logutil.setup(__name__)


class Monitor(daemon.Daemon):
    def run(self):
        LOG.info('Starting')

        blob_path = os.path.join(config.STORAGE_PATH, 'blobs')
        while not self.exit.is_set():
            blob.ensure_blob_path()
            check_this_pass = []

            with etcd.ThreadLocalReadOnlyCache():
                for ent in os.listdir(blob_path):
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

                    if self.exit.is_set():
                        break

            for blob_uuid in check_this_pass:
                b = blob.Blob.from_db(blob_uuid)
                # TODO(mikal): we should not do this if the node is really
                # busy.
                hash_out, _ = util_process.execute(
                    None,
                    'sha512sum %s/%s' % (blob_path, ent),
                    iopriority=util_process.PRIORITY_LOW)
                hash = hash_out.split(' ')[0]
                LOG.with_fields({
                    'blob': b.uuid,
                    'sha512': hash}).info('Checksum')
                b.update_checksum(hash)

                self.exit.wait(60)
                if self.exit.is_set():
                    break

            self.exit.wait(300)
