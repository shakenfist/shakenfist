# Blobs as containers: server-side extraction and a record of what contains what

> **Status: placeholder.** This is a problem statement, the facts a survey of
> `develop` established, and a sketch of a direction. It is not yet a costed,
> phased plan. The Execution section is intentionally a stub, and the open
> questions below must be resolved before phases are cut.

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf),
research as needed to give a confident answer. Flag any
uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`AGENTS.md` for build commands and project conventions, and
through it `docs/developer_guide/standards.md` and
`docs/developer_guide/database_internals.md` for coding
standards and database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/blob.py` (blob lifecycle, references and
deletion), `shakenfist/mariadb.py` (three-layer database
access pattern, and the `object_references` table),
`shakenfist/schema/` (Pydantic models),
`shakenfist/external_api/blob.py` and `artifact.py` (the
blob and upload API), and `shakenfist/daemons/cluster/`
(blob garbage collection).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table in this master plan under the Execution
section.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

The OVA import plugin
([shakenfist/client-python-ova](https://github.com/shakenfist/client-python-ova))
reads an `.ova` locally, checks it against its manifest, and uploads each disk
as its own `image` artifact. The OVA itself, its OVF descriptor, its manifest
and any signing certificate never reach the cluster. Once the import is done,
nothing in Shaken Fist records which appliance a disk came from. A second
import repeats the work, and nothing can boot the appliance again without the
original file.

The motivating idea is to keep the whole appliance in the cluster: upload the
OVA once, record each member as a blob of its own, and record that the OVA
*contains* each member under its name. Later imports and boots can then reuse
that work. The idea generalises well beyond OVAs. Any format that stores its
members as contiguous byte ranges (tar, an uncompressed zip, most of an ISO)
fits, and so does a planned S3-compatible API, where a bucket version contains
keys and a folder is a prefix. A blob-backed filesystem fits too: a directory
version contains entries.

### What a survey of `develop` found

Surveyed at 33be36b8aa on 2026-10-17. These facts constrain the design.

* **Blobs are immutable and have no namespace.** The documented access model
  is that knowing a blob's UUID implies you may read it
  (`docs/developer_guide/authentication.md:39-42`). There is no ownership
  check anywhere a blob is referenced: booting from `sf://blob/<uuid>`
  (`external_api/instance.py:920-921`) and wrapping one in an artifact with
  `blob_uuid=` (`external_api/artifact.py`) both accept any blob. Artifact
  ownership (`@requires_artifact_ownership`) is the only ownership check
  near blobs.
* **No public endpoint creates a blob without an artifact.** The routes to a
  blob are an artifact upload or fetch, a snapshot, and agent output. No
  endpoint lets a client create a relationship, and `transcode` and
  `depends_on` are written only by server internals.
* **`object_references` cannot hold named containment.** Its primary key is
  `(source_object_type, source_uuid, relationship, target_object_type,
  target_uuid)` (`mariadb.py:3028-3031`). `relationship_value` is not part of
  it, and an insert of a duplicate key keeps the first value
  (`mariadb.py:7927-7936`). So a container can name a given member blob only
  once, and two members with identical bytes, or two S3 keys pointing at one
  object, lose every name but the first. `relationship_value` is capped at 64
  characters by the model but not on write, which is
  [#4559](https://github.com/shakenfist/shakenfist/issues/4559). A tar member
  name can exceed 100 characters, and an S3 key can be 1024 bytes. There is
  also nowhere to put an offset and length.
* **The same primary key breaks the blob storage roadmap's chunk list.** That
  roadmap stores a composite blob's chunks as `CHUNK` rows in
  `object_references`, with the position as `relationship_value`
  ([blob storage roadmap](blob-storage-roadmap.md), "Chunk Storage"). A
  composite that holds the same chunk at two positions, which disk images do
  constantly (runs of zeros), can record only one of them. Containment and
  chunking hit the same limitation, and should be solved once.
* **A new relationship type is unsafe in a rolling upgrade.** Old code reads
  rows back with `RelationshipType(row.relationship)` and catches only
  `OperationalError` (`mariadb.py:8038, 8094, 8281`). An old `sf-database`
  meeting a new type returns `[]` or INTERNAL
  (`daemons/database/main.py:1544-1547, 1600-1607`). An unknown relationship
  *filter* silently becomes an unfiltered query (`main.py:1519-1522`). A
  separate table avoids these read failures. It does not remove the
  upgrade risk, though, which moves into garbage collection instead (open
  question 7).
* **Garbage collection would leak a deleted container's members.**
  `Blob.ref_count` counts every reference except `BLOB_LOCATION`
  (`blob.py:438-449`), so anything a container points at stays alive. Only
  `cascading_delete` removes references a deleted blob holds, and only
  `TRANSCODE` and `DEPENDS_ON` (`blob.py:657-665`). The per-blob task and the
  expiry path set `STATE_DELETED` directly, and `hard_delete` removes nothing
  ([#4558](https://github.com/shakenfist/shakenfist/issues/4558)).
* **A failed reference count reads as "unreferenced".** `count_references_to`
  returns 0 on error (`mariadb.py:9254-9277`). Artifact-backed blobs survive
  that, because the cluster pass refreshes their `last_used` every time
  (`daemons/cluster/main.py:288-306, 468-472`). A blob kept alive only by a
  container would not be refreshed, so one failed count would delete it once
  its 300-second grace period had passed (`main.py:375-392`).
* **Uploads can create `other` artifacts on the server, but the client never
  asks for one.** `POST /artifacts/upload/<name>` accepts
  `artifact_type='other'` (`external_api/artifact.py:628-633`, capability
  `artifact-upload-types`). client-python's `upload_artifact()` never sends
  the field, and `blob_artifact()` has no such parameter
  ([client-python#430](https://github.com/shakenfist/client-python/issues/430)).
* **Capability detection is a token list.** A client detects a server feature
  only by its token on the root page (`external_api/app.py:335-468`). Tokens
  are append-only, and are pinned by `tests/external_api/test_root.py`.
* **The plugin's source URLs are not fetchable.** The plugin labels each disk
  artifact `ova://<path>/<member>`. That boots only through the cached-version
  fallback of issue 3603 (`operations/artifact_fetch_op.py:194`). The plugin
  is changing to the server's default `sf://upload/<namespace>/<name>`.
  Refusing an unfetchable base when an instance is created is
  [#4557](https://github.com/shakenfist/shakenfist/issues/4557).

## Mission and problem statement

Let a blob be a container. Specifically:

* A record that blob Y is the entry named `n`, in one namespace's view,
  inside container blob X, at bytes `[offset, offset + length)` (sketch
  point 5 explains the namespace). Each container can list its entries, a
  prefix query over the names. Entries keep their members alive exactly as
  long as the container exists.
* A server-side extraction call: "cut this byte range of blob X into a new
  blob, and record it as entry `n` of X". The server never needs to
  understand the container's format, and an entry is true by construction,
  because the server made the member from the container's own bytes.
* A client (the OVA plugin first) that uploads an archive once, reads its
  layout locally, asks the server to extract the members, and checks the
  checksums the server computes against the archive's own manifest.

Non-goals, at least initially:

* **Understanding any container format on the server.** The client knows
  where the members are; the server cuts where it is told.
* **Compressed containers.** A gzipped member is not a byte range of its
  archive. A later "transform" call could handle that.
* **Mutable containers (S3 buckets, directories).** Blobs are immutable, so a
  mutable container is an artifact whose versions are container blobs, each
  with its own fixed set of entries. This plan must not preclude that, but
  does not build it.
* **Content-defined chunking.** That is the
  [blob storage roadmap](blob-storage-roadmap.md). It interacts with this plan
  (see the decisions below), but neither waits for the other.

## Sketch of a direction

These are leanings from the discussion that produced this placeholder. The
decisions pass confirms or overturns them.

1. **Entries live in their own table, not in `object_references`.** Roughly
   `(container_blob_uuid, namespace, name, member_blob_uuid, offset, length)`,
   keyed on `(container_blob_uuid, namespace, name)` (see point 5), with the
   name sized for 1024-byte S3 keys
   and indexed for prefix queries. `Blob.ref_count` counts entries that point
   at the blob. This avoids the primary-key, 64-character and rolling-upgrade
   problems above.
2. **Containers are always blobs, so they are always immutable.** Their
   entries are append-only: an entry is added by an extraction and is
   never changed or removed while its container exists. Entries arrive
   one extraction at a time, so a reader can see a partial set (open
   question 8). Mutability lives where it already does, in artifact
   versions.
3. **Extraction is the only way to create an entry.** A client can name an
   entry but cannot claim one, so the containment record cannot lie. The
   proposed shape is `POST /blobs/<uuid>/extract` with `offset`, `length`
   and `name`. It runs as a cluster operation on a node that holds the
   container, because extracting a 10 GB member is a real copy. The new
   blob reports `creating` until it is done, as uploads do, and the entry
   is written only once the member is `created`, so a failed copy never
   leaves an entry pointing at a half-made blob (open question 10 covers
   what reaps that blob). The server checks that the range lies inside the
   blob. Repeating an extraction with the same name and range in the same
   namespace returns the existing entry; the same name with a different
   range is a conflict.
4. **Extraction is one storage-layer primitive.** It is implemented as a
   function such as `Blob.from_range(container, offset, length)`, not as a
   file copy inside an API handler. Today it copies bytes. Under
   content-defined chunking, the same function splices chunk references and
   makes at most two new edge chunks, with no API change. That is a
   requirement this plan hands to the blob storage roadmap, not a dependency
   on it.
5. **Entry names belong to the namespace that extracted them.** An
   ownership test cannot work here: blobs have no namespace, and anyone
   who knows a blob's UUID can wrap it in an artifact of their own with
   `blob_uuid=`, so "owns an artifact that references the container" is a
   test an outsider passes. Recording a creating namespace on the blob
   would not work either, because identical uploads are deduplicated by
   sha512 and a second namespace legitimately reuses the first one's
   blob. So entries are keyed on `(container, namespace, name)`: any
   caller who can read the container may extract from it, the entry's
   bytes are still true by construction, and the names a namespace gives
   are visible to that namespace (and to system callers) and to nobody
   else. A second namespace cannot claim `disk1.vmdk` in somebody else's
   OVA before its owner does, because it can only claim it in its own
   view. The member blob itself may be shared between namespaces, since
   identical ranges give identical bytes.
6. **Every deletion path clears entries.** `cascading_delete`, the per-blob
   `STATE_DELETED` path, the expiry path and `hard_delete` all remove the
   entries a deleted container holds. The fix for
   [#4558](https://github.com/shakenfist/shakenfist/issues/4558) is the
   natural home for that. Deleting a namespace removes that namespace's
   entries too. Otherwise a deleted namespace's entries would keep
   members, and their storage, alive for as long as somebody else's
   container lives.
7. **A failed count must not read as zero.** `count_references_to`, or the
   cluster pass's use of it, treats an error as "in use". This is an existing
   defect that this plan makes acute, so the plan fixes it rather than
   inheriting it.
8. **Capability `blob-extract`.** Clients detect support and fall back. The
   OVA plugin keeps today's per-disk upload, without entries, against a
   server that does not offer it.

## Open questions

These must be answered, likely in a phase-0 decisions pass at high effort,
before any implementation phase is cut:

1. **Should entries and the roadmap's chunk list share one table?** Both say
   that bytes `[a, b)` of blob X are blob Y. They differ in that a chunk list
   tiles its composite exactly and is positional, while entries are sparse and
   named. One "blob parts" table with a kind column, or two tables, are both
   viable. The answer changes the blob storage roadmap's phase 2 design,
   which is not started, so this is the cheapest moment to decide it.
2. **What does an extraction return, and how does the client wait?** The
   options are a blob in `creating` state to poll, a cluster operation, or
   both. The answer should match how uploads and artifact fetches report
   progress today.
3. **Are checksums computed on extraction, or lazily?** The OVA plugin
   verifies members against the manifest using the server's sha256 or
   sha512, so it needs them promptly. Under chunking, a whole-blob hash still
   reads every byte, because sha256 does not compose across chunks.
4. **Should an extracted member with the same sha512 as an existing blob
   reuse that blob?** That is deduplication by another route, and it
   overlaps the roadmap's lazy deduplication. It may be better left to that
   plan. A narrower form needs no hashing at all, and could be adopted
   even if sha512 reuse is not: an extraction that exactly matches an
   existing `(container, offset, length)` reuses that member blob,
   whatever the name or namespace. The bytes are identical by
   construction, and this is the common case, where every namespace that
   imports the same public appliance would otherwise copy every disk.
5. **What does the API show?** Probably an `entries` listing on
   `GET /blobs/<uuid>` with a name-prefix filter and paging, a
   `contained_by` view on the member, and client-python accessors. Both the
   listing and `contained_by` filter by the caller's namespace (system
   callers excepted), using the existing visibility rules. A member blob
   can be shared between namespaces, so an unfiltered `contained_by` would
   reveal other namespaces' names and containers. How it pages should
   follow the
   [API query batching roadmap](api-query-batching-roadmap.md).
6. **Name rules.** What is the maximum length, is the name bytes or UTF-8,
   and are `/`-separated paths given meaning, or left for S3 and the
   filesystem to interpret?
7. **What happens in a rolling upgrade?** The capability token can be visible
   one deploy early, so an extraction might be routed to an old node. How is
   that refused cleanly? There is a worse case. An old cluster daemon's
   `Blob.ref_count` counts only `object_references`. It would see a member
   kept alive only by an entry (an OVA's descriptor, manifest or
   certificate, which never become image artifacts) as unreferenced, and
   delete it once the grace period had passed. That is silent data loss.
   Possible answers: offer extraction, and its capability token, only once
   every node runs the new code, through the existing upgrade gating; or
   have an extracted member carry an `expires_at` or `last_used` refresh
   that old code respects until the upgrade completes.
8. **Does a container need to be marked complete?** Entries arrive one
   extraction at a time, so a listing can show a partial set. Is that
   acceptable to clients such as a future "boot this stored appliance"
   path, or does a namespace need to mark its view of a container as
   fully extracted?
9. **How does a re-import find the earlier one?** For a re-import to
   upload nothing, the client must find the existing container blob before
   it uploads, by the `other` artifact's name or a label, or by checksum.
   Checksum lookup (`/blob_checksums/<algorithm>/<hash>`) exists, but the
   client must hash the whole OVA first. If neither is good enough, the
   success criterion narrows to "extracts nothing", and upload
   deduplication is left to the blob storage roadmap.
10. **What happens to a member whose extraction fails, or whose container
    is deleted mid-copy?** Point 3 writes the entry only after the copy, so
    a failed copy leaves a `creating` blob with no reference. Does the
    existing reaper collect it, and on what timer? A container deleted
    during the copy must not gain an entry afterwards: the entry write has
    to check that the container still exists in the same transaction.
11. **What limits extraction?** Any reader of a container may extract
    from it, and until chunking lands every extraction is a full copy.
    One caller could ask for the same 10 GB range under many names, or
    for many overlapping ranges, and fill the cluster with blobs that
    live as long as somebody else's container. The options are to count
    extracted bytes against the extracting namespace's quota, cap the
    entries per (container, namespace), make exact-range reuse (question
    4) mandatory, or rate-limit the operation.

## Execution

> **To be determined.** Phases will be cut after the open questions are
> resolved. A plausible decomposition, *for discussion only*:
>
> 0. Decisions pass: resolve the open questions, and settle the table shape
>    jointly with the blob storage roadmap's chunk list. (high effort)
> 1. Garbage-collection groundwork: every deletion path clears references
>    (#4558), a failed count is not zero, and over-long reference values are
>    refused on write (#4559).
> 2. The entries table, its three-layer access functions, `ref_count`
>    integration, and deletion.
> 3. `Blob.from_range()` and the extraction cluster operation.
> 4. The REST API (extract, list entries, `contained_by`), the `blob-extract`
>    capability, and client-python methods, including the `artifact_type` fix
>    ([client-python#430](https://github.com/shakenfist/client-python/issues/430)).
> 5. The OVA plugin: upload the OVA as an `other` artifact, extract its
>    members, verify them against the manifest, and make bootable artifacts
>    from the disks. This phase's file lives in client-python-ova, because
>    the code does.
> 6. Push audit.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 0. Decisions pass | _to be created_ | Not started | — |
| (later phases) | _to be created_ | Not started | — |

The `Merged` column records what put each phase on `develop`: the merge commit
of its pull request, or an explicit `first..last` range where the phase landed
directly. It is filled in as each phase lands, so `—` means the phase has not
landed yet. A phase that lands in another repository records
`<repo> <sha> (#pr)`.

### Push audit

However the later phases are cut, the last one is the push audit. It runs
`PUSH-AUDIT.md` over the accumulated diff of every phase in this plan against
`develop`, not the last phase's diff alone. Findings land as their own pull
request, and this plan is not complete until each is resolved or declined in
writing here. If the audit finds nothing, that is recorded in one sentence.

## Agent guidance

This plan follows the standard Shaken Fist planning workflow described in
`PLAN-TEMPLATE.md`: all implementation work is done by sub-agents, the
management session plans and reviews, the master plan is created at high
effort, and each phase plan specifies per-step effort/model/isolation and a
detailed brief. See the template for the full execution model, planning-effort
guidance, step-level table format, and the management-session review
checklist. Nothing about this plan deviates from that model.

## Administration and logistics

### Success criteria

We will know this plan has been successfully implemented when:

* An OVA imported with the plugin is one `other` artifact. Its descriptor,
  manifest, certificate and disks are blobs recorded as its entries, and each
  disk is also a bootable `image` artifact.
* Re-importing the same OVA extracts nothing, and, if open question 9 finds
  a mechanism, uploads nothing.
* No deletion path leaves entries, or other references, behind a deleted blob
  or a deleted namespace, and a test proves it for each path.
* Extraction beyond whatever limit open question 11 sets is refused.
* A failed reference count never deletes a blob, and a test proves it.
* Extraction is refused for a range outside the blob.
* A second namespace extracting from the same container cannot add, change
  or see the first namespace's entry names, and a test proves it.
* The code passes `pre-commit run --all-files` (flake8, stestr unit tests,
  mypy).
* New code follows existing patterns: object lifecycle in `baseobject.py`,
  MariaDB access via the three-layer direct/gRPC/public pattern, Pydantic
  schemas in `shakenfist/schema/`. Filtering is pushed down to SQL.
* There are unit tests and functional CI coverage, including an extraction
  whose member checksum is compared with one computed independently.
* Lines wrap at 120 characters; single quotes for strings, double quotes for
  docstrings.
* gRPC proto changes are regenerated with `tox -e genprotos`.
* `docs/user_guide/artifacts.md` and the developer guide's
  `subsystem_internals.md` describe entries and extraction.

### Future work

* **Mutable containers.** An S3-compatible API and a blob-backed filesystem,
  built as artifacts whose versions are container blobs.
* **Server-side transforms** for compressed members, such as gunzip of a
  member into a new blob.
* **Booting a stored appliance** from its OVA artifact, without the original
  file, by reading the descriptor entry from the cluster. This belongs to the
  OVA plugin's instance-creation work.
* **A client that hashes members while it uploads,** so that the server need
  not re-read a member to checksum it. This is worth designing together with
  the blob storage roadmap's checksum handling under chunking.

### Bugs fixed during this work

_To be filled in as we go._ The survey for this placeholder filed
[#4558](https://github.com/shakenfist/shakenfist/issues/4558) (references
outlive a deleted blob),
[#4559](https://github.com/shakenfist/shakenfist/issues/4559) (over-long
`relationship_value` breaks reads) and
[client-python#430](https://github.com/shakenfist/client-python/issues/430)
(`artifact_type` is never sent). One further finding was reported
privately.

### Documentation index maintenance

This plan has been added to `docs/plans/index.md` (*Master plans* table) and
`docs/plans/order.yml`. Phase files, once created, are linked from the
Execution table above only -- not from `index.md`, which lists master plans
only, and not from `order.yml`. That table is the only path to them.

### Back brief

Before executing any step of this plan, please back brief the operator as to
your understanding of the plan and how the work you intend to do aligns with
that plan.
