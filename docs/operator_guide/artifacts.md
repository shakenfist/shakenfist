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
the record in the database believes the correct value should be. These comparisons are
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

## Verifying a download against the mirror

The checksums above answer "does this replica still hold the bytes we
stored?". They say nothing about whether the download arrived intact —
for that, it has to be compared against a digest the mirror published.

When an image shortcut resolves to a mirror which publishes a digest
file beside its images, Shaken Fist fetches it, hashes the download as
it streams past, and refuses the image if the two disagree. A
mismatched download is deleted rather than registered, so it cannot
become a usable image, and the artifact fetch fails with the
`fetched image does not match its published checksum` event carrying
both the expected and the actual digest.

The digest is only read when the image is about to be fetched from the
mirror. An image the cluster can already serve from a cached blob is
not re-verified against the mirror's digest -- that digest describes
the mirror's copy, and the cached copy was checked when it was fetched
-- so no digest file is requested on that path. Nor is one requested
when a fetch falls back to the cached copy because the source could
not be reached at all: that path is the one which exists for a broken
mirror, and it no longer asks the broken mirror anything.

Which digest is used is whatever the mirror actually publishes,
strongest first. For cirros that is `SHA256SUMS` where it exists and
`MD5SUMS` otherwise: cirros publishes SHA256SUMS only from 0.6.3
onwards, and releases before that have MD5SUMS and nothing else, so
md5 is kept as a fallback rather than dropped. The algorithm which
actually verified an image is recorded on the event, so an operator
can tell a sha256-verified fetch from an md5-verified one without
guessing. (md5 is deliberately absent from `BLOB_HASH_ALGORITHMS` and
is not used for anything else.)

A fetch from a mirror which publishes no digest we can read is
recorded as `fetched image had no published checksum to verify
against`. That is a different fact from a fetch which was checked and
passed, and reading these events back should not require guessing
which happened.

The check fails open by default. A digest file which cannot be read — a
404, a timeout, a connection failure, or a file which does not name the
image — lets the fetch go ahead unverified, with that event as the only
record. So by default anyone able to stop the digest request arriving
turns the check off, on an `https://` mirror as much as a plain
`http://` one. Watch for the `no published checksum` event on a mirror
known to publish digests.

Set `REQUIRE_IMAGE_CHECKSUM` to `True` to ask for the stronger property
instead: a mirror fetch for which no digest could be read is refused
before the download starts, with a `refusing image fetch with no
published checksum` audit event, and the artifact (and any instance
waiting on it) goes to `error`. The default is `False` because much of
an existing fleet points at mirrors which publish nothing at all, and
turning this on there would refuse every fetch. Turn it on when the
mirrors you have configured — `DOWNLOAD_URL_CIRROS` and
`CHECKSUM_URL_CIRROS`, or an `IMAGE_DOWNLOAD_URL` of your own — are
known to publish digests you trust.

Note that "no digest could be read" includes an artifact whose source
URL is an arbitrary one, because nothing publishes a digest beside it
for us to find: with `REQUIRE_IMAGE_CHECKSUM` set, those fetches are
refused too. The rule is simply that wherever the fail-open event above
would have been recorded, the fetch is refused instead. A copy served
from elsewhere in the cluster is not a mirror fetch, so it is
unaffected — including the fallback to an already cached version when
the source cannot be reached.

What this establishes is integrity, not authenticity. It catches a
truncated or corrupted download and a mirror serving the wrong file. It
does not catch a deliberate substitution: the digest is fetched from
the same mirror, by the same route, as the image, so anyone able to
replace one can replace the other. The default cirros URLs
(`DOWNLOAD_URL_CIRROS` and `CHECKSUM_URL_CIRROS`) are already
`https://`, which keeps someone on the path from replacing either, but
not the mirror itself or whoever controls it: that the two agree only
says they came from the same place. An override pointing at a plain
`http://` mirror loses even the protection from someone on the path.
md5 adds nothing against an adversary either way, which is one more
reason sha256 is preferred when a release publishes it.

Each digest is labelled by its length (32 hex characters is md5, 64 is
sha256, 128 is sha512), never by the name of the file it came from, so
a mirror serving `MD5SUMS` under the name `SHA256SUMS` costs a warning
rather than failing every fetch as a mismatch. A `CHECKSUM_URL_CIRROS`
override written before the template took `%(digest_file)s` — one
pointing at `.../%(vernum)s/MD5SUMS`, which was the shipped default —
is still honoured: the one file it names is read, and its digest is
used under whatever algorithm its length identifies.

A copy served from elsewhere in the cluster is not a mirror fetch, and
does not carry the mirror's digest: that blob was verified when it was
originally fetched, and the mirror's digest describes the mirror's
copy rather than this one.

## Transient fetch failures

A fetch which fails on the network rather than on the content is retried
before the artifact is given up on. An unreachable mirror, a 5xx, a DNS
hiccup, or a blob replication whose every source timed out awaiting our
transfer connection, re-queues the fetch after 15, then 30, then 60
seconds. Each attempt is recorded on the artifact as a `transient fetch
failure, will retry` or `transient blob replication failure, will retry`
event carrying the attempt number. Only once that budget is spent does
the artifact enter `error`, and only then is an instance waiting on the
fetch torn down.

So an artifact which sat in `creating` for a couple of minutes and then
succeeded hit a blip rather than anything wanting attention, and one
which errored was unreachable for the better part of two minutes. The
retry exists because the alternative was worse than the outage: a
one-second blip during an `instance create` errored the artifact within
about fifteen seconds and deleted the instance with it, so a patch
reboot of a mirror cost a user manual cleanup.

Neither path errors an artifact which already has a good version, since
other nodes still hold valid copies: only one still in `initial` or
`creating` is errored. An upstream fetch which was updating such an
artifact falls back to the already cached version first, and retries
only if that cached copy is unusable too.

The retry budget is a general cluster-operation facility rather than
something artifacts have of their own; [the database
internals](../developer_guide/database_internals.md#the-transient-failure-retry-budget-rides-on-the-work-item)
describe where the counter lives and why.

## What an image fetch cost, and whether it was cached

An instance which needs an image either gets it from a copy the cluster
already holds, or the node pulls it from the source. Which way it went
has always been visible as a status event on the artifact -- `fetching
image from within the cluster` or `fetching image from the internet` --
but only as a message, so a hit rate, or the bytes a cluster pulls from
the internet, meant reading every fetch by hand.

Each fetch now also records an `image fetch complete` usage event
carrying:

* `cached` -- true when the image came from within the cluster, false
  when it came from the source. Counting these gives the cache hit
  rate.
* `bytes` -- the size of the blob fetched.
* `seconds` -- how long the fetch took.
* `blob_uuid` -- the blob the fetch produced.

A fetch happens at most once per artifact version per node, so this is
nothing like the rate at which a maintenance pass or a poll writes
events, and the per-fetch cost of recording it is not worth avoiding.

This covers the image fetch only. The transcode cache -- the separate
decision about whether a post-transcode version of an image already
exists -- is not instrumented, so a transcode hit rate still has to be
read out of the surrounding events.

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