# Rework the artifact, blob, label, upload, and snapshot user interface

> **Status: placeholder.** This is a problem statement and a sketch of a
> direction, not yet a costed, phased plan. The underlying operations are
> believed to be roughly correct; what is wrong is the *surface* a human (or
> an Ansible module, or another API client) has to drive to use them. The
> Execution section is intentionally a stub — the open questions below must be
> resolved before phases are cut.

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
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

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

Shaken Fist's storage model has four user-visible object types plus an
operation that produces them:

* **Blob** — an immutable, content-addressed, reference-counted lump of
  bytes. Identified only by UUID. Defined in `shakenfist/blob.py`.
* **Artifact** — a named, versioned, namespaced *reference* to a sequence of
  blobs. Has a type (`image`, `snapshot`, `label`, `other`). Defined in
  `shakenfist/artifact.py`. Lookup by ref accepts either a UUID or a name.
* **Label** — an artifact of type `label`: a manually-curated pointer where
  the user explicitly chooses which blob each version uses. Path-like name
  (`label` or `namespace/label`). Created implicitly on first update.
* **Snapshot** — an artifact of type `snapshot`, produced from an instance's
  disk(s). Created and listed through *instance* routes, not artifact routes.
* **Upload** — a short-lived staging resource (`shakenfist/upload.py`) used to
  stream bytes to a node before they are turned into a blob and then an
  artifact.

The current surface, mapped from the code:

**CLI** (`client-python/shakenfist_client/commandline/`): `artifact`
(`cache`, `upload`, `download`, `list`, `show`, `versions`, `delete`,
`delete-version`, `max-versions`, `share`, `unshare`, `events`,
`set-metadata`, `delete-metadata`); `blob` (`list`, `show`, `events`,
`sha512`, `set-metadata`, `delete-metadata`); `label` (`update` — and nothing
else); snapshots only via `instance snapshot`.

**REST** (`shakenfist/external_api/`): `/artifacts*`, `/blobs*`, `/upload*`,
`/label/<path:label_name>`, `/instances/<ref>/snapshot`.

**Python client** (`shakenfist_client/apiclient.py`): `cache_artifact`,
`upload_artifact`, `blob_artifact`, `create_upload` / `send_upload` /
`send_upload_file` / `truncate_upload`, `update_label`, `snapshot_instance`,
plus the obvious getters.

### The sharp edges, grounded in code and the issue tracker

1. **Name resolution is ambiguous and inconsistent.** A bare `name:tag` can
   match a local label, a system label, *and* a well-known auto-fetched image,
   and the resolution between them is silent and surprising. This is the wart
   that triggered this plan.
   * #3271 — `400@centos:7` silently fetched the upstream UEFI image instead
     of the explicitly-uploaded `sf://label/system/centos:7`, and only failed
     much later as a boot failure. Asks for an *ambiguous reference* error at
     create time.
   * #1634 — name lookup raises a bare `TooManyMatches` traceback; open
     question of whether the local namespace should win.
   * `artifact.py` `from_db_by_ref()` treats UUIDs and names differently with
     respect to namespace filtering — `/artifacts/foo` means different things
     depending on whether `foo` parses as a UUID.

2. **Blobs leak as a user-facing primitive.** They have no names and no
   namespaces (#1030, closed, gave artifacts namespaces but not blobs), yet
   the user must paste blob UUIDs to drive `label update` and to reason about
   `artifact show` output. There is no name-based blob lookup. Reference
   counting is visible but hard to reason about. #832 (allow deletion of
   blobs) is the operator-facing tail of the same problem.

3. **The upload → blob → artifact flow is a three-UUID, multi-call dance.**
   `create_upload` → repeated `send_upload` → `POST /artifacts/upload/<name>`
   with either an `upload_uuid` *or* a `blob_uuid`, with client-side checksum
   short-circuiting. Three resources, two of them with UUIDs the user must
   carry between calls. Cross-node proxying happens invisibly.

4. **Labels are underpowered and the implicit-create behaviour bites.**
   `label update LABEL BLOB_UUID` is the *entire* label CLI. You cannot create
   a label from a URL, set `max_versions` at update time, or list labels by
   namespace. Referencing a label that does not exist historically created it
   in a bad state rather than erroring (#1310, #864, both closed but
   symptomatic). Sharing rules are muddled: #1385 (no way for system to create
   a shared label, closed) and #1386 (only system *should* be able to update a
   shared label, open) show the model was never coherent. Old label versions
   are not reaped (#833).

5. **Sharing is all-or-nothing and namespace-coupled.** Only `system`-
   namespace artifacts can be shared; sharing is a boolean visible-to-everyone
   flag with no per-namespace ACL. #422 (image UUIDs / per-namespace image
   security) is the long-standing request this blocks.

6. **Snapshots are artifacts wearing an instance costume.** Created and listed
   via `/instances/<ref>/snapshot`, returning a payload that mixes artifact
   and blob fields. There is no artifact-centric way to discover or manage
   them, and multi-disk instances fan out into multiple snapshot artifacts
   with little to tie them together. #751 (treat snapshots as images, closed)
   started this convergence; it is not finished.

7. **Error and URL handling is rough for clients.** `sf://` URL handling is
   inconsistent (#1167 — `sf://instance/.../vda` failed with "No connection
   adapters were found"); download failures surface as raw server tracebacks
   (#592); `user_data` base64 handling is unfriendly (#3269). These are the
   symptoms an API/Ansible consumer actually hits.

8. **Whole-namespace operations are missing.** #877 — clients want a single
   "clean this namespace" endpoint instead of orchestrating instance- and
   network-deletion ordering themselves.

9. **Listing and events have known defects** that make the surface feel
   unreliable: #1974 (artifact listing performance), #1283 (artifact events
   endpoint broken).

## Mission and problem statement

Make the everyday storage workflows — *get an image in, point a label at it,
share it, snapshot an instance, find and clean up what I made* — drivable
without sharp edges, by a human at a terminal, by the Ansible modules, and by
arbitrary API clients, **without changing the underlying object model where it
is already correct.**

Concretely, success looks like:

* A single, predictable name-resolution rule, with ambiguity surfaced as an
  actionable error at request time rather than a silent wrong choice or a late
  failure (#3271, #1634).
* A one-shot "publish this image/label from a file or URL" path that hides the
  upload/blob/artifact UUID juggling.
* Labels promoted to a first-class, fully-featured noun (create/list/show/
  update/delete, by name, with version policy), with coherent sharing rules.
* Snapshots reachable and manageable as the artifacts they already are.
* Blobs demoted to an implementation detail for normal workflows — reachable
  by operators, but never *required* to be hand-typed by a user doing ordinary
  things.
* Clean, typed error responses for the common failure modes (bad URL, missing
  reference, download failure) instead of raw tracebacks.

Non-goals (at least initially): redesigning blob storage internals (that is
the [blob storage roadmap](blob-storage-roadmap.md)), changing the
content-addressing or dedup scheme, or reworking instance disk specs beyond
what name-resolution clarity requires.

## Open questions

These must be answered (likely in a phase-0 decisions pass, at high effort)
before any implementation phase is cut:

1. **Name-resolution rule.** What is the single, documented precedence when a
   bare `name:tag` could be a local label, a system label, or a well-known
   image? Options: strict-ambiguity-errors (#3271's suggestion), local-wins
   (#1634's suggestion), or explicit-scheme-required. How does this interact
   with existing instance disk specs in the wild? Is there a migration/
   deprecation path, or is it a behaviour change gated on a major version?

2. **Should blobs ever be user-addressable by something other than UUID?** Or
   is the right move to ensure no normal workflow requires a raw blob UUID at
   all (e.g. `label update` accepts an artifact ref or version, not a blob
   UUID)? What do operators legitimately need raw blob access for?

3. **What is the unified "publish" verb?** Does `artifact upload` /
   `artifact cache` / `label update` collapse into one command with a source
   (`--file`, `--url`, `--from-blob`) and a target (`--label`, `--image`,
   namespace, sharing, max-versions)? Or do we keep distinct verbs but make
   each one-shot? What does the Python client and Ansible module surface
   become?

4. **Sharing model.** Stay with the boolean system-namespace flag, or move to
   per-namespace grants (which #422 wants)? If the latter, that is a schema
   and authz change with its own blast radius — does it belong in this plan or
   a dependent one? Resolve #1386 (who can update a shared label) as part of
   whichever direction we pick.

5. **Snapshot surface.** Do we add first-class `artifact`/new `snapshot`
   commands and routes that operate artifact-centrically, keeping the
   instance route as a thin creator? How do we group the per-disk snapshot
   artifacts of a single snapshot operation?

6. **Namespace cleanup (#877).** Is this a new endpoint that enqueues an
   ordered teardown (instances → networks → artifacts), and how does it
   interact with shared artifacts (cf. #1384, where namespace deletion once
   wrongly deleted shared artifacts)?

7. **Backwards compatibility.** Which of these are additive (new commands,
   new flags, better errors) versus behaviour changes that need a deprecation
   cycle? The CLI and the Python client version independently
   (`client-python` is a separate repo; cf. #3169 on workflow drift) — how do
   we stage server + client changes so neither half breaks the other?

8. **Where does validation live?** Pydantic schemas in `shakenfist/schema/`
   for request bodies and typed error responses — which of the rough edges
   (#592, #1167, #3269) are best fixed by tightening schemas versus by
   reshaping the endpoints?

## Execution

> **To be determined.** Phases will be cut after the open questions are
> resolved. A plausible decomposition, *for discussion only*:
>
> 0. Decisions pass — resolve the open questions above; produce a written
>    name-resolution spec and a target CLI/API surface. (high effort)
> 1. Name resolution: one rule, ambiguity-as-error, typed errors (#3271,
>    #1634, #1167, #592).
> 2. Unified "publish" path that hides upload/blob/artifact UUID juggling.
> 3. Labels as a first-class noun (full CRUD, version policy, coherent
>    sharing — #1385/#1386/#833).
> 4. Snapshots as artifacts (artifact-centric discovery/management; #751 tail).
> 5. Sharing model (boolean → per-namespace, if chosen; #422).
> 6. Namespace cleanup endpoint (#877).
> 7. Listing/events reliability cleanup (#1974, #1283).
> 8. Docs, CI (incl. `sf-client`-driven tests, #540), and client-python sync.

| Phase | Plan | Status |
|-------|------|--------|
| 0. Decisions pass | _to be created_ | Not started |
| (later phases) | _to be created_ | Not started |

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

* The motivating wart is gone: bringing up `400@centos:7` with a local
  `system/centos:7` label either uses the label or errors as ambiguous — it
  never silently fetches the wrong image (#3271).
* A new user can get an image in, point a label at it, and boot an instance
  from it without ever typing a blob UUID or making more than one "publish"
  call.
* The named open issues that this plan adopts are closed or explicitly
  re-scoped: at least #3271, #1634, #1167, #592, #877, #1386, #833, and a
  decision recorded on #422.
* The code passes `pre-commit run --all-files` (flake8, stestr unit tests,
  mypy).
* New code follows existing patterns: object lifecycle in `baseobject.py`,
  MariaDB access via the three-layer direct/gRPC/public pattern, Pydantic
  schemas in `shakenfist/schema/`. Filtering is pushed down to SQL.
* There are unit tests and functional CI coverage (`shakenfist/deploy/
  cluster_ci`), including `sf-client`-driven tests (#540).
* Lines wrap at 120 characters; single quotes for strings, double quotes for
  docstrings.
* gRPC proto changes (if any) are regenerated with `tox -e genprotos`.
* `docs/user_guide/artifacts.md` and any affected operator docs are updated;
  `ARCHITECTURE.md`, `README.md`, and `AGENTS.md` updated if modules/object
  types change.
* The `client-python` repo is updated in lockstep so server and client do not
  drift (#3169).

### Future work

* Per-namespace image ACLs beyond a first sharing rework (#422), if the
  decisions pass defers them.
* Auto-reaping of old label versions (#833) if not pulled into a core phase.
* Convergence with the [blob storage roadmap](blob-storage-roadmap.md) and
  [API query batching roadmap](api-query-batching-roadmap.md) where listing
  performance work overlaps (#1974).

### Bugs fixed during this work

_To be filled in as we go._

### Documentation index maintenance

This plan has been added to `docs/plans/index.md` (*Master plans* table) and
`docs/plans/order.yml`. Phase files, once created, are linked from the
Execution table above only -- not from `index.md`, which lists master plans
only, and not from `order.yml`. That table is the only path to them.

### Back brief

Before executing any step of this plan, please back brief the operator as to
your understanding of the plan and how the work you intend to do aligns with
that plan.
