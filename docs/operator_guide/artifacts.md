# Artifacts

## Checksums

As of Shaken Fist v0.7, blob replicas are regularly checksummed to verify that
data loss has not occurred. The following events imply a checksum operation:

* snapshotting an NVRAM template.
* creation of a new blob replica by transfer of a blob from another machine in
  the cluster (the destination is checksummed to verify the transfer).
* transcode of a blob into a new format (the new format is stored as a
  separate blob).
* conversion of an upload to an artifact.

The following events _should_ imply an artifact checksum, but we found that
performance suffered too much for very large blobs:

* download of a new blob from an external source (artifact fetch for example).
* snapshotting a disk.

Additionally, all blob replicas are regularly checksummed and compared with what
the record in etcd believes the correct value should be. These comparisons are
rate limited, but should happen with a maximum frequency of
CHECKSUM_VERIFICATION_FREQUENCY seconds, which defaults to every 24 hours. It is
possible if you have a large number of blob replicas on a given node that the node
will be unable to keep up with checksum operations.

If a blob replica fails the checksum verification, CHECKSUM_ENFORCEMENT is set
to True _and is not in use on that node_, then the replica is deleted and the
cluster will re-replicate the blob as required. If the blob replica is in use,
there isn't much Shaken Fist can do without disturbing running instances, so the
error is logged and then ignored for now.

Checksums are also used when a new version of an artifact is created. If the
checksum of the previous version is the same as the checksum for the proposed
new version, the proposed new version is skipped. Artifact uploads from v0.7 can
also skip actual upload of the contents of the artifact if there is already a
blob in the cluster with a matching checksum.

## Sharing artifacts

Artifacts in the system namespace can be shared with all other namespaces.
Artifacts shared like this appear to the other namespaces as if they are local
to the other namespace, although non-system namespaces should not be able to
update such an artifact. This is useful if you have official or commonly used
images which you want to provide all users of a cluster -- for example an
official CentOS image that many users will want.

???+ info

    Another option for sharing artifacts is the "trusts" relationship between two
    namespaces, which is discussed in the [authentication section of the operator guide](authentication.md).

To share an artifact, use the command line client like this:

`sf-client artifact share ...uuid...`

To unshare an artifact, do this:

`sf-client artifact unshare ...uuid...`