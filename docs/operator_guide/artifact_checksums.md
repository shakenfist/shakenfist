# Artifact checksums

As of Shaken Fist v0.7, blobs are regularly checksumed to verify that data loss
has not occurred. The following events imply a checksum operation:

* snapshotting an NVRAM template.
* transfer of a blob from another machine in the cluster (the destination is
  checksumed to verify the transfer).
* transcode of a blob into a new format (the new format is stored as a
  separate blob).
* conversion of an upload to an artifact.

The following events _should_ imply an artifact checksum, but we found that
performance suffered too much:

* download of a new blob from an external source (artifact fetch for example).
* snapshotting a disk.

Additionally, all blob copies are regularly checksumed and compared with what
the record in etcd believes the correct value should be. These comparisons are
rate limited, but should happen with a maximum frequency of
CHECKSUM_VERIFICATION_FREQUENCY seconds, which defaults to every 24 hours.

If a copy of a blob fails the checksum verification _and is not in use on that node_,
the copy is deleted and the cluster will re-replicate the blob as required. If
the blob is in use, there isn't much Shaken Fist can do without disturbing
running instances, so the error is ignored for now.

Checksums are also used when a new version of an artifact is created. If the
checksum of the previous version is the same as the checksum for the proposed
new version, the proposed new version is skipped.