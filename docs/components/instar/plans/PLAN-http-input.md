# Streaming image input over HTTP and HTTPS

## Status: In progress

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

Consult `ARCHITECTURE.md` for the overall system structure
(host VMM, KVM guest, call table, device emulation).
Consult `AGENTS.md` for build commands, project conventions,
code organisation, and the security model summary. Consult
`docs/` for format-specific documentation (`docs/qcow2/`,
`docs/raw/`, etc.) and `docs/commentary/` for architectural
decisions and design rationale.

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->

## Situation

instar takes every input as a local path. The host opens the file,
exposes it to the guest as a virtio-block device, and never looks
inside it; the guest parses the format. Nothing in that design
assumes the bytes come from a filesystem, and the code that would
have to change is small and already in one place.

**The I/O boundary.** `src/vmm/src/backing.rs` holds `BackingStore`:
a `File`, a capacity, and `read_at(offset, &mut [u8])` / `write_at`
/ `sync` / `size`. Every device is built on it -- 36 construction
sites and 26 `VirtioBlockDevice::new` calls -- and every one of them
already addresses bytes by offset. A second backing kind with the
same methods is invisible above `open()`.

**No network, and nothing to build a partition from.** The VMM's
dependencies are kvm-ioctls, kvm-bindings, vm-memory, virtio-queue,
virtio-bindings, vmm-sys-util, clap, libc, log, env_logger,
guest-protocol, shared, the format crates, toml, dirs, serde and
rand. There is no HTTP client, no TLS stack and no URL parser in the
process that holds `/dev/kvm`. There is also no privilege-separation
machinery: the VMM never forks a helper (`version.rs` shells out to
`qemu-img --version`, and that is all), installs no seccomp filter,
applies no Landlock ruleset, and sets no rlimits.

**What the guest actually reads.** Measured with `strace` on the
prebuilt binary running `convert -O raw`, counting reads on the
input descriptor:

| Source | Input reads | Pattern |
|--------|-------------|---------|
| 8 MiB qcow2 written by `qemu-img convert` | 131 x 64 KiB | offsets strictly ascending |
| 8 MiB qcow2, ten clusters written out of order | 13 x 64 KiB | `0 196608 262144 393216 655360 917504 524288 786432 589824 851968 458752 720896 327680` |

Three things follow. Requests are 64 KiB, because convert's
`--sector-size` defaults to 65536. Unallocated regions are never
read, so a sparse source costs only its allocated clusters. And the
order is whatever the image's own metadata dictates, including
backward jumps, which the host cannot predict because the host does
not parse the format -- that is the security model, not an
oversight. Some paths still issue 512-byte requests (issue #860 for
`create`), so a remote backing store has to coalesce rather than
assume 64 KiB.

**The guest does not block on I/O; it spins.**
`src/core/src/virtio.rs` `do_request` submits one request, polls the
used ring for `MAX_WAIT_ITERATIONS = 100_000_000` iterations of
`spin_loop()`, and calls `send_error("timeout", ...)` if the count
runs out. Measured on this host, 100M `pause` instructions take
1.3 s. The budget is counted in iterations, not time, so a
descheduled vCPU does not consume it; what consumes it is the host
taking longer than that to answer one read. Queue depth is one.

**Unix-socket limits, measured here.** `net.core.wmem_max` is
212992, so a `SOCK_SEQPACKET` socketpair's `SO_SNDBUF` saturates at
425984 however much is requested: a single 256 KiB datagram sends,
and a single 1 MiB datagram fails. So a design that carries a block
of image data in one datagram is capped at 425952 bytes here once a
side asks for the buffer, and at 212960 if it does not -- the kernel
clamps a request to `wmem_max` and then doubles it, so neither
figure follows from the sysctl by inspection, and both move if the
user lowers it. Phase 2 measured this precisely; see open question 2,
which this paragraph used to contradict by splitting the difference
at "near 256 KiB".

**What the host already reads for itself.** Backing-chain discovery
(`src/vmm/src/chain.rs`) runs the sandboxed info operation once per
chain level, and checks each discovered path against an allowlist
(`get_backing_allowlist`, default `$IMAGE_DIR`) before touching the
filesystem. Mutating operations probe the target on the host with
plain `std::fs` before launch: `probe_resize_target`,
`probe_amend_target`, `probe_bitmap_target`,
`probe_rebase_target`. Configuration layers `/etc/instar/config`
then `~/.config/instar/config`, and the allowlist is settable in
both, so the user-writable file can widen the system one.

**Confinement available, measured on this host.** Landlock is
compiled in and present in the active LSM list, unprivileged user
namespaces are enabled (`max_user_namespaces` 114274), and
`/dev/fuse`, `fusermount3`, `bwrap` and `curl` all exist. Kernel
6.12 carries Landlock ABI v4 or later, which restricts `connect` by
TCP port as well as filesystem access. None of this is portable
across the distro matrix, so what the running kernel refuses has to
be reported rather than assumed.

## Mission and problem statement

Let read-only operations take an `https://` input, start work before
the transfer finishes, and fetch only the bytes the image's own
metadata asks for -- without putting a line of network, TLS or URL
code in the process that holds `/dev/kvm`.

**Why this is not just a download.** Spooling first already works
and needs no code: `curl -o image.qcow2 ... && instar convert ...`.
This feature earns its place only through the two things spooling
cannot do -- starting work immediately, and never transferring what
is not read. The fragmented measurement above would move 900 KiB
instead of 8 MiB.

**Why the buffer is the easy part.** A bounded block cache over
range requests is about a day's work. Three other things are not.

1. *The guest's wait is a fixed spin budget.* A cache miss that
   takes longer than about 1.3 s -- a TLS handshake, a cold origin,
   a 429 -- is reported by the guest as a hard I/O failure rather
   than retried. Nothing downstream can be made reliable until the
   guest waits properly.
2. *Latency is serialised.* Queue depth is one and the guest blocks
   per request, so 10 GiB at 64 KiB is about 164,000 sequential
   round trips: thirteen minutes of pure waiting at 5 ms each. The
   host has to fetch ahead of the guest, which means the fetch must
   be asynchronous and the cache must usually hit.
3. *Posture.* An HTTP client in the VMM puts a response parser, a
   redirect follower, a DNS resolver and a TLS stack inside the
   process that holds `/dev/kvm` -- and holds the invoking user's
   entire ambient authority, which running as a non-root user does
   not reduce. Half the objection is supply chain rather than
   memory safety: `reqwest` pulls tokio and several hundred crates
   into a binary whose dependency list is fifteen lines long and
   whose `.deb` and `.rpm` dependencies are generated from it.

**The argument that makes a cheap partition sufficient.** instar
already assumes its input bytes are hostile; that is what the
sandbox is for. A fetcher confined so that it can do nothing but
return bytes therefore degrades, when compromised, to *the attacker
controls the image* -- the baseline assumption, already handled. The
fetcher does not need to be trusted, so it does not need to be a
guest. It needs to be unable to do anything else.

**Shape.** A separate `instar-fetch` process holds every line of
network code. It is started before the VMM opens `/dev/kvm`, so it
never inherits that descriptor; it is given one socket and no other
capability; and it is confined with Landlock, seccomp, namespaces
and rlimits as far as the running kernel allows, saying so when the
kernel allows less. The VMM speaks a fixed-layout,
offset-addressed byte protocol to it and parses nothing else.

**Out of scope.** Remote output; any in-place mutation over the
network; authentication; remote backing-file references; and
protocols other than HTTP and HTTPS. Each is refused by name rather
than left to fail obscurely.

## Open questions

All nine are resolved. Question 5 was settled by measurement on
2026-11-18; the operator settled the other eight the same day,
taking the planner's recommendation in every case. Each question
keeps the reasoning that was offered for it, which is now the
rationale of record rather than an argument for a decision still to
be made -- a reviewer asking "why a framed protocol and not a
virtqueue?" should find the answer here, along with what was
rejected and on what grounds. Nothing in the phase table is waiting
on a decision; phase 2 can be planned as written.

A decision recorded here is not immovable. Questions 2 and 4 are
the two most likely to be revisited by measurement -- the datagram
ceiling and block size against phase 6's round-trip counts, and the
HTTP client if `ureq` turns out to want something the fetcher cannot
give it -- and both say so in their own text.

1. **A virtio-derived protocol over the socket, or a plain framed
   one?** *Resolved 2026-11-18: a plain framed protocol that borrows
   virtio-blk's model and not its mechanism.*

   *What virtio-blk is for does not survive the move.* A descriptor
   is a guest-physical address and a length; the ring lives in
   memory both sides map; the doorbell and the used ring exist so
   that neither side has to copy. Over a Unix socket none of that
   exists. Transliterating the structures keeps the awkward half --
   sector addressing, a status byte in its own descriptor, `F_NEXT`
   chains -- and loses the entire point.

   *Shared memory could be reintroduced, and should not be.*
   `SCM_RIGHTS` passes a `memfd` and an eventfd over the socket;
   `ioevent.rs` already does eventfd notification and `virtio-queue`
   already implements rings over `vm-memory`, so a real virtqueue
   between the VMM and the fetcher would work. It is the version
   that deserves the name, and it is still the wrong answer: it
   hands the untrusted fetcher a writable mapping shared with the
   privileged process, and whoever controls the ring controls
   indices the VMM dereferences. That is precisely the
   memory-corruption channel the partition exists to remove.
   Defending it means writing adversarial-ring validation in
   privileged code -- the same class of work as the existing virtio
   boundary audit -- to save a copy that is noise: 256 KiB of
   `memcpy` is tens of microseconds against tens of milliseconds of
   network.

   *The asymmetry that decides it.* The payload is bytes already
   assumed hostile, so copying them costs only cycles. Control
   structures are not hostile-tolerant. Pay the copy, give the
   fetcher one socket and no shared memory, and `docs/security.md`
   can state that the partition is total.

   *What is worth borrowing.* The model: capacity discovered at
   init, offset-addressed reads, a typed status rather than a bare
   errno, an explicit flush -- and, from the used ring,
   **out-of-order completion**. A request id with several requests
   outstanding is what lets the fetcher prefetch without shared
   memory, and it is the one piece of virtqueue thinking the stream
   protocol genuinely needs.

   *This shape is NBD, and we should still not speak NBD.* NBD is
   offset-addressed reads over a socket with request handles and
   out-of-order replies, and `nbdkit`'s curl plugin is an
   off-the-shelf implementation of nearly this whole feature.
   Speaking real NBD means parsing a negotiated protocol with
   option haggling and structured replies from an untrusted peer in
   the privileged process, which is a far larger surface than the
   few fixed-size headers here. Steal the shape; do not link the
   protocol. Phase 2's plan should record the comparison, because a
   reviewer will ask.

   *And not protobuf, unlike `guest-protocol`.* That crate uses
   micropb because the guest's serial channel carries
   variable-shape result messages. This channel carries fixed-size
   headers and raw bytes, so it should be `#[repr(C)]` headers with
   explicit bounds checks and no serialisation library at all. It is
   the only new untrusted parse in the privileged process and it
   should be small enough to fuzz exhaustively.

2. **`SOCK_SEQPACKET` or `SOCK_STREAM`, and what block size?**
   *Resolved 2026-11-18: `SOCK_SEQPACKET`, 64 KiB default, 256 KiB
   ceiling negotiated at init.* SEQPACKET makes message boundaries
   the kernel's problem, which removes framing desynchronisation
   from the privileged parser entirely. 64 KiB matches the guest's
   own request size, and readahead comes from many outstanding
   requests rather than larger ones.

   *Corrected by phase 2's survey, 2026-11-19.* This question
   originally said that "the measured `wmem_max` of 212992 caps a
   single datagram near 256 KiB here". It does not, in either
   direction. A fresh `SOCK_SEQPACKET` socketpair on this host gets
   `SO_SNDBUF` 212992 and caps a datagram at 212960 bytes, which is
   208 KiB and below 256 KiB; after an explicit
   `setsockopt(SO_SNDBUF, ...)` the kernel clamps the request to
   `wmem_max` and then doubles it, giving 425984 and a 425952-byte
   ceiling, which is 416 KiB and above it. Neither number follows
   from the sysctl by inspection, so phase 2 negotiates from the
   `SO_SNDBUF` the kernel actually granted on each side rather than
   from any sysctl, and 64 KiB is the default because it is the only
   size that fits everywhere without asking for anything. If phase
   6's measurement shows round-trip count
   dominating, phase 6 revisits it -- `SOCK_STREAM` with fixed
   headers, or scatter-gather across several datagrams -- rather
   than phase 2 guessing now.

3. **Where do the cache and the readahead live?**
   *Resolved 2026-11-18: in the fetcher.* The VMM's `read_at` stays
   one round trip, and the fetcher owns the block cache, the
   coalescing of repeated and overlapping 64 KiB requests, and the
   asynchronous readahead,
   issuing overlapping HTTP requests without the VMM knowing. A
   compromised fetcher serving stale or wrong cached bytes is still
   only serving wrong bytes. Putting the cache in the VMM would mean
   the privileged process holding and indexing attacker-influenced
   data and scheduling the prefetch, which is more privileged code
   for no gain. Consequence: the VMM's memory does not grow, and any
   `--cache-size` control is a fetcher argument.

4. **What implements HTTP inside the fetcher?**
   *Resolved 2026-11-18: `ureq` with `rustls`, in the fetcher's own
   crate, never a dependency of the VMM binary.* It is blocking,
   which suits a thread-per-outstanding-request fetcher; it does not
   pull tokio; and rustls is memory-safe Rust, so the residual risk
   is low even before confinement. Being pure Rust it also adds no
   shared library, which matters because `cargo deb`'s `$auto` and
   `generate-rpm`'s `auto-req` scan the packaged binaries: a C TLS
   stack would show up in the package metadata, and rustls will not.
   Phase 5's plan should record the rejected alternatives: `reqwest`
   (tokio and hundreds of crates, which still have to be audited and
   packaged even though the VMM does not link them); `libcurl` (C in
   the fetcher, but mature and distro-patched -- the reasonable
   second choice); and exec'ing `curl --range` per block (zero HTTP
   code anywhere in instar and the most auditable of all, but one
   process per cache fill, which the operator has ruled out as the
   primary design).

5. **Does the spin budget already cause the kasm `test_convert`
   flake?** *Resolved 2026-11-18 by measurement: the mechanism is
   real, it explains one of the cluster's two messages, and the
   other was already fixed.* Injecting a single delayed `read` with
   `strace -e inject=read:delay_enter=...` into a release build of
   `develop` makes the guest declare
   `error op=timeout device=... status=57005` once the host takes
   longer than somewhere between 0.5 s and 1.0 s to answer one
   request, against the 1.3 s the budget measures on an idle host.
   It fires on output writes as well as input reads, because both
   go through the same `do_request`. `convert` then reports
   `Error: "convert operation failed"` with exit 1, says nothing
   about I/O unless `--verbose` is passed, and leaves a truncated
   output file behind -- 524288 bytes of an 8388608-byte conversion.
   That is the cluster's first message. `compare` handles the same
   timeout correctly: "compare: reading the second image, ref.raw,
   failed at virtual offset 327680 (0x50000); the images were not
   compared", exit 4, which is qemu-img's read-error code. So the
   cluster's other message, "Content mismatch at offset 0!", is not
   this. It was issue #807, fixed on 2026-11-12 by 360bfdf4: a
   pre-fix binary reproduces it under the same injection and a
   current one does not, which also means flake observations
   recorded before that date should not be assumed still live.
   Phase 1 therefore starts from a reproduction rather than a
   hypothesis, and carries two defects worth fixing with no network
   in sight: the budget itself (#923), and convert's silence about
   an I/O failure (#924).

6. **A server with no `Range` support: refuse, or spool?**
   *Resolved 2026-11-18: refuse by name, with `--spool` as an
   explicit opt-in that says what it is doing and where it is
   writing.* Silently spooling turns a streaming operation into a
   full download plus a disk write, which is what the user would
   have chosen `curl` for. Note the asymmetry: `Accept-Ranges` is
   advisory, so the only reliable probe is a ranged request checked
   for `206`, which phase 5 does once at open.

7. **Credentials.** *Resolved 2026-11-18: none in v1.* A URL
   containing userinfo is refused; no `.netrc`, no bearer token, no
   `--header`. The fetcher then never holds a secret, which is one
   fewer thing a compromise can take. Signed URLs carry their
   credential in the query string and work anyway, covering most
   object-store cases -- but they must then be kept out of verbose
   output and out of error strings, which phase 8 owns.

8. **How is the fetcher found and packaged?** *Resolved 2026-11-18:
   the same pattern as the guest `.bin` files* -- probe the
   executable's own directory first for developer builds, then
   `/usr/lib/instar/`. It is a workspace binary built by `make
   instar` and added to the `.deb` and `.rpm` asset lists at mode
   755.

9. **Remote output, and remote backing files.**
   *Resolved 2026-11-18: both out of scope, and both refused by
   name.* Output stays local because every writer seeks backwards.
   Backing files stay local as policy rather than as a limitation:
   an image-supplied URL is attacker-controlled data, and following
   one is server-side request forgery from a process the attacker is
   trying to reach. The only URL instar ever fetches is the one on
   the command line. Phase 7 enforces it; phase 10 writes it down.

## Execution

<!-- shared-block: plan-push-audit-phase v4 -->
Push audit phase (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-push-audit-phase.md`):

- Every master plan ends with a phase that runs the repository's
  `PUSH-AUDIT.md` over the whole plan's work. It is the last row of
  the Execution table and it is not optional. The rule binds every
  plan that carries the phase, which is decidable from the plan file
  alone: a plan that is already `Complete`, `Abandoned` or
  `Superseded` and does not carry the phase is not reopened to
  acquire one, and a plan that has the phase runs it even if it
  reaches `Complete` before the phase does.
- That phase audits the accumulated diff of every phase in the plan
  against the default branch, not the diff of the last phase alone.
  Auditing one phase at a time would miss what the phases did to
  each other -- the duplicated helper that only exists once phases
  three and six have both landed, the doc page that phase two made
  wrong and phase five never revisited.
- Once the plan's phases have merged, a diff against the default
  branch is empty and would read as a clean audit. The range is not
  reliably derivable after the fact either: unrelated work lands on
  the default branch between phases, so anything anchored on "since
  the plan file appeared" is far too wide. It has to be recorded. As
  each phase lands, what put it on the default branch goes into the
  plan: the merge commit of its pull request, whose diff against its
  first parent is the whole of what landed, or -- where the phase
  landed directly -- every commit of the phase, or its `first..last`
  range. A single commit is only ever enough when it is a merge
  commit.
- Where the Execution phases are a table, that record is a `Merged`
  column, added last so that a row which omits it still reaches
  `Status`; where they are prose sections it is a `Merged:` line in
  the phase's own section. The `Status` column keeps its single
  vocabulary term and nothing else (see `plan-status-vocabulary`).
  A phase that landed in another repository records `<repo> <sha>
  (#pr)` and is audited against that repository's default branch, as
  part of the pull request that lands it; the plan's own push-audit
  phase cites that audit rather than re-running it.
- Phases that landed before the plan started recording them are
  reconstructed rather than left blank. Recover what you can from
  `gh pr list --state merged` and `git rev-list --first-parent`, and
  say in the plan that the range was reconstructed. Do not trust a
  path-filtered `git log` on its own: it lists the commits that
  touched a path without saying which arrived directly and which
  arrived inside a pull request, and recording a commit that came in
  under a merge audits one commit of that pull request rather than
  the pull request. A reconstructed record may be a summary table in
  the audit phase's own section rather than a column or a line in
  the Execution table, which keeps retrospective archaeology out of
  a table that tracks live status. Where a phase accreted over
  months of unrelated commits and no range is recoverable, say that
  instead and name the paths the audit read -- an audit that says
  what it could not scope is a result; one that silently audits
  nothing is not.
- Findings land as their own pull request against the default
  branch, and the plan is not complete until they are resolved or
  explicitly declined in writing. A finding that is declined says
  why, in the plan, where the next reader will find it.
- Where the audit finds nothing, record that in the plan in one
  sentence. It is a real result, and a run of them is the evidence
  for making the phase conditional rather than mandatory.
- A repository with no `PUSH-AUDIT.md` still carries the phase, and
  the phase says that the runbook does not exist yet and what was
  done instead. Silently omitting it is what let the audit go
  untriggered for as long as it did. What is done instead is at
  least the checklist below, applied to the recorded ranges: it is
  a floor, not a substitute for a runbook, and a plan adds the
  questions its own repository's blast radius raises. Each item
  gets a verdict, or a sentence saying why it does not apply.
  Do not borrow another repository's runbook instead: a runbook is
  written for one repository's blast radius.
  1. Every recorded landing takes one of the forms above: a merge
     commit with two parents, whose diff is read against its first
     parent; every commit of a direct landing, or its `first..last`
     range; a landing in another repository, whose own audit is
     cited; or a statement that no range is recoverable, naming the
     paths the audit read.
  2. Anything the diff added that runs unattended -- cron, a timer,
     a scheduled workflow, a self-update -- reports its own failure
     somewhere a human sees, or is named as not doing so.
  3. No secret or token entered any repository, and no private
     hostname or IP address entered a repository more public than
     the one it came from.
  4. Anything the diff removed or renamed has no consumer left,
     checked by grep across every repository that could consume it
     rather than by assumption, with the greps stated.
  5. Where the repository publishes something -- an image, a
     release, a package -- a broken change fails closed: one that
     publishes nothing is recoverable, one that publishes something
     wrong is not.
<!-- shared-block-end -->

The phase order is chosen so that the two hard problems are solved
before the easy one is attempted. Phase 1 is first because nothing
downstream is reliable while the guest reports a slow read as a
failure, and because it is worth landing on its own merits. Phases 2
to 4 then build and confine the whole process boundary with no
network code anywhere in the tree, so that the protocol and the
sandbox are exercised where a failure cannot be blamed on a server;
they also leave a `file://` backend behind, which is the oracle
every later phase tests against. HTTP arrives only at phase 5, once
there is a confined process to put it in.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Guest I/O wait | [PLAN-http-input-phase-01-guest-wait.md](/components/instar/plans/PLAN-http-input-phase-01-guest-wait/) | Complete | a8387ffe |
| 2. Fetch protocol and a file-backed fetcher | [PLAN-http-input-phase-02-protocol.md](/components/instar/plans/PLAN-http-input-phase-02-protocol/) | Not started | |
| 3. Remote backing store | PLAN-http-input-phase-03-backing.md | Not started | |
| 4. Confinement | PLAN-http-input-phase-04-confinement.md | Not started | |
| 5. HTTP backend | PLAN-http-input-phase-05-http.md | Not started | |
| 6. Cache and prefetch | PLAN-http-input-phase-06-prefetch.md | Not started | |
| 7. Operation scope and chain policy | PLAN-http-input-phase-07-scope.md | Not started | |
| 8. Failure semantics and UX | PLAN-http-input-phase-08-failures.md | Not started | |
| 9. Tests and fuzzing | PLAN-http-input-phase-09-tests.md | Not started | |
| 10. Documentation and threat model | PLAN-http-input-phase-10-docs.md | Not started | |
| 11. Push audit | - | Not started | |

**1. Guest I/O wait.** Replace the iteration-counted spin in
`src/core/src/virtio.rs` `do_request` with a wait that does not
expire because the host was slow. Open question 5 settled this as a
bug fix rather than enabling work: a single host I/O slower than
about a second already fails a local operation, with no network in
sight, and the reproduction is in that question. The phase also
carries convert reporting such a failure as a bare "convert
operation failed" with no mention of I/O outside `--verbose`.
Touches every operation's I/O path, so it lands alone.

This section said, until the phase was planned, that the deadline
should move "to the host where it can be generous and per-request".
The phase's survey found there is no host deadline to move it to --
nothing in the VMM times a request, and the 100 ms `epoll_wait`
timeout in `src/vmm/src/io_thread.rs` exists only so the loop can
re-check its `running` flag -- and that neither thread which
services a virtio queue could enforce one, because both block
inside the synchronous `read_at`. The operator therefore decided on
2026-11-18 that the guest waits indefinitely and the host enforces
no deadline at all; the phase plan argues it, and
`src/core/src/serial.rs` `read_config` is the precedent, waiting
unbounded on the host already. The truncated output file, which this
section also used to claim, is #849 and is out of the phase's scope.

**2. Fetch protocol and a file-backed fetcher.** Define the wire
protocol -- fixed-layout headers, offset-addressed reads,
out-of-order completion by request id -- and build `instar-fetch`
serving a local file over it, with no network code at all. Records
the NBD and virtqueue comparisons from open question 1.

Planned 2026-11-19. Three things the phase plan decided that this
section did not anticipate. The **VMM-side client lands in this
phase too**, as a standalone type with `BackingStore::read_at`'s
exact signature, because otherwise phase 2 can only be tested
through throwaway code and phase 3 integrates a client written for
the first time at the point of integration. The fetcher **takes
descriptor numbers rather than a path** -- the VMM opens the file and
the fetcher inherits it -- which keeps path resolution on the side
that holds the user's authority and lets phase 4 confine the file
backend to no filesystem access at all. And the **decoder's fuzz
target lands here rather than in phase 9**, because this decoder is
the only new untrusted parse the plan adds to the privileged
process and phase 9's business is HTTP and fault injection.

The survey also found that `std` cannot create a `SOCK_SEQPACKET`
socket at all -- `std::os::unix::net` offers only `SOCK_STREAM` and
`SOCK_DGRAM` -- so the socket is a `libc::socketpair` behind a safe
wrapper in the pattern of `src/vmm/src/ioevent.rs`, rather than a
new dependency in the privileged process.

**3. Remote backing store.** `BackingStore` gains a second kind,
the read-only operations accept a URL, and the fetcher's lifecycle
is owned: started before `/dev/kvm` is opened, reaped on every exit
path. End state: `instar info file:///path` works end to end
through the socket.

Phase 2's survey sharpened two things here. `BackingStore` is a
plain struct today -- `src/vmm/src/backing.rs` holds a `File`, a
capacity, a size and a sparse flag, with no enum, trait or
indirection -- so "gains a second kind" means introducing the
polymorphism as well as the remote implementation. And the VMM has
never spawned a child process: the only `Command::new` under
`src/vmm/src/` runs `qemu-img --version`, so there is no
descriptor-passing, reaping or child signal handling to extend. Phase
2's review round added a third thing this phase does not have to do:
the fetch client's `read_at` zero-fills past the advertised capacity,
exactly as the local store does past the end of its file, because a
device's capacity is the image's length rounded up to a whole sector
and the guest's last read of a ragged image legitimately overhangs it.
The two are interchangeable there, so nothing above has to clamp. The
second review round added two facts this phase inherits rather than
decides: the advertised capacity is read once at init and never
re-read, so a source that grows afterwards reads as zeros past the old
size and one that shrinks fails with `UnexpectedEof` -- again what the
local store does with a file that changes under it -- and the
fetcher's stderr carries one line per refused or non-`Ok` request, so
whatever routes that stderr into a log owns a rate the peer sets and
should bound or sample it. This phase should also fix #932, the
virtqueue error the host cannot read that hangs the guest, because it
multiplies the ways the host can fail to answer a request.

**4. Confinement.** Landlock, seccomp-bpf, user and mount
namespaces, rlimits (`RLIMIT_FSIZE` 0 and `RLIMIT_NPROC` 0 are the
cheap wins), `PR_SET_NO_NEW_PRIVS`, `PR_SET_DUMPABLE` 0, and
descriptor hygiene, applied in the child before `exec`. What the
running kernel refuses is reported, not silently skipped. Still no
network, so the sandbox is tested against a fetcher that cannot be
blamed for network behaviour.

**5. HTTP backend.** Ranged GETs, sizing from `Content-Length`, the
`206` probe, `ETag`/`Last-Modified` pinning so a source that changes
mid-transfer fails cleanly, retry with backoff, redirects refused,
scheme and host policy including a default deny for link-local and
RFC1918, and resolve-then-connect-to-the-pinned-address so a DNS
rebind cannot walk past the policy check. Verifies that
`cargo tree -p instar` is unchanged.

**6. Cache and prefetch.** The block cache, coalescing of
sub-block requests, asynchronous readahead, several requests
outstanding, and the measurement that says whether phase 2's block
size was right.

**7. Operation scope and chain policy.** URLs accepted only by
read-only operations and refused by name everywhere else, including
outputs and image-supplied backing references. Chain discovery's
per-level VM launches reuse one fetcher rather than refetching
headers per launch.

**8. Failure semantics and UX.** Progress reporting, deadlines and
caps, the `--spool` fallback, resumption, and error messages that
report a failure without echoing a signed URL.

**9. Tests and fuzzing.** An integration HTTP fixture; a
fault-injecting server covering no-range, truncation, an `ETag`
change mid-transfer, 429 and a stall; a confinement test that reads
`/proc/<pid>/fd` and asserts the sandbox denies what it should; and
a `cargo-fuzz` target for the framing parser, which is the only new
untrusted parse in the privileged process.

**10. Documentation and threat model.** `docs/remote-input.md`, the
partition section of `docs/security.md` including what it does not
cover, `ARCHITECTURE.md`'s component inventory -- a second process
is a change in the shape of the system -- `CHANGELOG.md`, and the
README's documentation links.

!!! note "In this project"

    The Execution phases are `Phase | Plan | Status` table each
    master plan carries, so the record goes in a `Merged` column
    added after `Status` — last, so that a row which omits it still
    reaches `Status`. Most phases here land as their own pull
    request and the cell holds that one merge commit; a phase that
    landed directly on `develop` records its `first..last` range
    instead. The audit phase itself runs `PUSH-AUDIT.md` over the
    union of those ranges rather than over `git diff develop...HEAD`,
    which is empty once the phases have merged.

    The obligation starts with plans written from here on, and with
    any plan still open that carries an audit phase. Plans that were
    already `Complete` when the rule arrived are not being backfilled,
    so a `Phase | Plan | Status` table with no `Merged` column is a
    plan that predates this, not a plan that has drifted from it.

<!-- shared-block: plan-phase-landing v1 -->
Phase landing (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-phase-landing.md`):

A plan's status and a repository's review state both live in files
that every branch would otherwise rewrite. Left alone, that turns
each of them into a merge-conflict hot spot, and it spends a pull
request and a full CI run on a change that is entirely prose.
Three rules keep them out of the way.

- **A phase is closed out in the first commit of the next phase,
  not in a pull request of its own.** By the time the next phase
  branches, the previous one has merged, so its merge commit is
  known and its `Merged` cell can record the thing the push-audit
  phase actually needs. This is the only ordering that works: a
  phase cannot record its own merge commit, and a separate
  close-out pull request buys that record at the price of a round
  trip. The close-out sets the finished phase's `Status` and
  `Merged` cells and the plan's row in `docs/plans/index.md`, and
  it is committed before the next phase's own work, so that the
  branch never claims the plan is further along than the default
  branch is.

- **The last phase closes itself out.** The push-audit phase is
  the last row of every plan, so no next phase will carry its
  close-out. Where the audit raises findings, the plan is not
  complete until they are resolved or declined, and those land as
  their own pull request after the audit phase has merged -- so
  that pull request is the carrier, and it can record the audit
  phase's merge commit, which by then is known. Where the audit
  finds nothing there is no carrier, and no follow-up pull
  request is opened for the sake of one cell: the phase sets its
  own `Status`, and the plan's index row, to `Complete` in its
  own pull request, and records no `Merged` cell. It is the only
  row permitted to omit one. The column exists so that the
  push-audit phase can reconstruct what to audit; the audit phase
  is last, so nothing ever reads its own row.

- **`REVIEWS.md` is not pruned or regenerated in a pull request
  that changes code or documentation.** Editing a reviewed file
  stales its mark, and adding or removing an in-scope file moves
  the header count, but neither is the landing pull request's
  business. `prune` regenerates the file whether or not it dropped
  anything, so the `prune-reviews` workflow heals both on the next
  push to the default branch. Pruning from a branch is also wrong
  more often than it is right, though not for the reason it first
  appears: `prune` compares each stamp against `HEAD`, which on a
  branch is the branch tip, so it drops the marks for the files the
  pull request itself touched while keeping marks the default
  branch has already pruned. Committing that state merges a review
  file computed from a stale tree, and can resurrect marks
  `prune-reviews` has already removed. Accumulated staleness is
  reported by the `review-coverage` audit, which recomputes
  coverage against `HEAD` and raises an issue once the backlog is
  worth a review session.

  **A review session is the exception**, and it is not optional
  tidiness: `stamp` regenerates `REVIEWS.md` as well as writing the
  marks, and the rows, the sidecars and the marks are committed
  together (see `docs/code-review-tracking.md`). Where a repository
  requires a pull request to reach its default branch, that is how
  a review session lands, so "not in a pull request" is about the
  kind of change, not the mechanism.

These rules assume phases land one after another. Where two phase
branches are open at once, each closes out only the phase it
directly follows.
<!-- shared-block-end -->

!!! note "In this project"

    instar does not track per-file human review: there is no
    `REVIEWS.md`, no `prune-reviews` workflow and no
    `docs/code-review-tracking.md`, so the third rule has nothing to
    act on here until that tracking arrives. The first two rules
    apply as written to the `Phase | Plan | Status | Merged` table
    each master plan carries: the next phase's first commit sets the
    finished phase's `Status` and `Merged` cells and its row in
    `docs/plans/index.md`, and the push-audit phase closes itself out.

<!-- shared-block: plan-status-vocabulary v1 -->
Plan status vocabulary (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-status-vocabulary.md`):

A status cell -- in the master plan's own Execution phase table, and
in the row `docs/plans/index.md` carries for the plan -- holds
exactly one of these terms and nothing else:

- `Proposed` -- written down as a concept, not yet scheduled.
- `Not started` -- scheduled, but no work has begun.
- `In progress` -- work has begun and has not finished.
- `Blocked` -- cannot proceed until something outside the plan
  changes. Say what, in the plan.
- `Complete` -- the work is done.
- `Abandoned` -- deliberately dropped without being done.
- `Superseded` -- replaced by another plan, which the plan names.

The term is the whole cell. No dates, no phase arithmetic, no
parenthetical qualifiers, no summary of what happened: a status is
read to decide whether a plan still wants attention, and prose in
that column has repeatedly grown until it could no longer be read
either by a person scanning the table or by tooling. Detail belongs
in the plan file, and a one-line summary belongs in the index's own
Intent column.

Matching is case-insensitive, so `In Progress` is accepted, but the
spelling above is the one to write.
<!-- shared-block-end -->

## Agent guidance

### Execution model

<!-- shared-block: subagent-execution-model v1 -->
Sub-agent execution model (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-execution-model.md`):

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making. This keeps the management
context lean and avoids drowning it in implementation diffs.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step with the
   brief from the plan, at the recommended effort level and model.
3. **Review** the sub-agent's output in the management session.
   Check the actual files -- the sub-agent's summary describes
   what it intended, not necessarily what it did.
4. **Fix or retry** if the output is wrong. Diagnose whether the
   brief was insufficient (improve it) or the model was too light
   (upgrade it), then re-run.
5. **Commit** once the management session is satisfied.

This applies to all steps, including high-effort ones. If a
sub-agent cannot succeed even with a detailed brief and the right
model, that is a signal the brief needs improving, not that the
management session should do the implementation itself.

Use `isolation: "worktree"` for sub-agents when the change is
risky or experimental; the worktree is discarded if the output is
unsatisfactory. For safe, well-understood changes, sub-agents can
work directly in the main tree.
<!-- shared-block-end -->

### Planning effort

<!-- shared-block: plan-planning-effort v1 -->
Planning effort (shared block; do not edit -- the canonical copy
lives in shakenfist/development at
`templates/shared-blocks/plan-planning-effort.md`):

The master plan itself is always created at **high effort** -- it
requires broad codebase understanding, cross-referencing several
source files, and judgment calls about scope and sequencing.

Each phase plan states the recommended effort level for planning
that phase. Phases that turn on design decisions, cross-component
coordination, protocol changes, or subtle correctness questions
should be planned at high effort. Phases that are mechanical, or
that follow a pattern already established elsewhere in the
codebase, can be planned at medium effort.
<!-- shared-block-end -->

!!! note "In this project"

    Phases involving deep protocol research, format-spec
    interpretation, or architectural decisions (call-table
    changes, new operations, new shared crates, security
    boundary changes) should be planned at high effort. Phases
    that are mechanical or follow well-established patterns can
    be planned at medium effort.

### Step-level guidance

<!-- shared-block: subagent-step-guidance v1 -->
Sub-agent step guidance (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/subagent-step-guidance.md`):

Each phase plan includes a table like this:

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | One-sentence summary of what to do and which files to touch |
| 1b | high | opus | worktree | Why this needs high effort: requires understanding X to do Y |

**Effort levels**, from cheapest to most thorough:

- **low** -- Purely mechanical changes: rename, reformat, add a
  log line, regenerate generated code. The brief is a complete
  instruction.
- **medium** -- The plan provides enough context to follow a clear
  brief. The sub-agent may read a few files, but the approach is
  already decided.
- **high** -- Requires reading several files, making judgment
  calls, or understanding non-obvious invariants. The sub-agent
  needs to think about edge cases.
- **xhigh** -- The setting for hard coding and agentic steps:
  long-horizon changes, or steps where the sub-agent must both
  research and implement.
- **max** -- Correctness matters more than cost. Expect
  diminishing returns and occasional overthinking; reserve it for
  steps where a wrong answer would be expensive to detect.

**Brief for sub-agent:** this is the key field. Write it as if
briefing a colleague who has never seen the codebase. Include what
to change, which files to touch, what patterns to follow, and any
non-obvious constraints.

A good brief front-loads the research the planner already did, so
the implementing agent does not repeat it. Instead of "add storage
functions for the new object", name the functions to add, the file
they belong in, the existing equivalent to mirror (with line
numbers), and any registration the change also needs.

The better the brief, the lower the effort level needed and the
lighter the model that can succeed.
<!-- shared-block-end -->

!!! note "In this project"

    The external references that push a step to high effort are
    format specs, the qemu-img source, and the KVM/virtio docs.
    The non-obvious constraints a brief should always state are
    memory layout, the 768KB guest binary cap, the `no_std`
    requirement of the format crates, and the call table
    boundary.

    Format-conversion work is the usual reason to need a
    large-context model: it tends to span the source format
    parser, the destination format writer, the call table, and
    the host-side glue at the same time.

    A worked brief for this codebase: instead of "add tests for
    the QCOW2 L2 parser", write "add tests for
    `parse_l2_entry()` in `src/crates/qcow2/src/lib.rs`. Use the
    adversarial fixtures in
    `instar-testdata/adversarial/qcow2/` (cluster boundary
    edges, OFLAG_COMPRESSED set with extended L2 cluster,
    refcount underflow). The function takes `(entry: u64,
    cluster_bits: u32)` and returns `Option<L2Entry>`."

### Model choice

<!-- shared-block: subagent-model-roster v1 -->
Sub-agent model roster (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/subagent-model-roster.md`):

The planner recommends which model is best suited to each step.
This is a judgment call, not a rigid rule -- the right model
depends on what the step requires, not on whether it is "planning"
or "implementation". The models available to sub-agents are:

- **fable** -- The most capable model available, for the hardest
  reasoning and the longest-horizon work: multi-step changes a
  single sub-agent must carry end to end, or steps whose
  correctness depends on holding a whole subsystem in mind at
  once. It costs materially more than opus, so reserve it for
  steps that have already defeated opus or are expected to.
- **opus** -- The default for steps needing deep reasoning,
  architectural understanding, subtle correctness judgment
  (locking, state machines, migrations), or intricate
  implementation that would be costly to debug if it were wrong.
- **sonnet** -- A good default for well-briefed implementation
  work. Faster and cheaper than opus, and effective when the plan
  front-loads the research and the brief leaves no broad judgment
  calls to make.
- **haiku** -- Suitable for purely mechanical tasks:
  search-and-replace, regenerating generated code, adding log
  lines, running commands. The brief must be a near-complete
  instruction.

Model choice interacts with effort level and brief quality. A
detailed brief compensates for a lighter model -- sonnet at medium
effort with a thorough brief often matches opus at medium effort
with a vague brief. The planner's job is to write briefs good
enough that the recommended model can succeed.

The model also determines the context window: fable, opus and
sonnet have 1M tokens, haiku has 200K. A step that must hold many
files in context at once may need one of the larger-context models
for that reason alone, even when the reasoning itself is
straightforward.

**When in doubt, skew to the more capable model.** Saving money
only matters if the outcome is still acceptable. A failed or
low-quality implementation wastes more time -- and therefore more
money -- than the heavier model would have cost. Recommend a
lighter model only when you are confident the brief is detailed
enough for it to succeed.
<!-- shared-block-end -->

### Management session review checklist

<!-- shared-block: plan-review-checklist v1 -->
Management session review checklist (shared block; do not edit --
the canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-review-checklist.md`):

After a sub-agent completes, the management session verifies:

- [ ] The files that were supposed to change actually changed --
      read them, do not trust the summary.
- [ ] No unrelated files were modified.
- [ ] The changes match the intent of the brief: not merely
      syntactically correct, but semantically right.
- [ ] The project's own pre-merge checks pass, including any
      generated code that has to be regenerated and committed
      (see the project-specific checks below).
- [ ] The commit message follows project conventions, including
      the `Co-Authored-By` line recording model, context window,
      and effort level.
<!-- shared-block-end -->

!!! note "In this project"

    The project-specific checks referred to above are:

    - [ ] `make instar` builds and `make lint` is clean.
    - [ ] Guest binaries pass `make check-binary-sizes` (768KB
          limit per operation).
    - [ ] `make test-rust` and the relevant
          `make test-integration` targets pass.
    - [ ] `pre-commit run --all-files` passes.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (768KB limit).
* All Rust unit tests pass (`make test-rust`).
* All Python integration tests pass (`make test-integration`).
* `pre-commit run --all-files` passes.
* `cargo tree -p instar` gains no HTTP, TLS or async-runtime
  dependency, and the generated `.deb` and `.rpm` dependency lists
  gain no new shared library.
* The fetcher is started before the VMM opens `/dev/kvm` and holds
  no descriptor but its socket, asserted by a test that reads
  `/proc/<pid>/fd`.
* Confinement is applied, and whatever the running kernel refuses is
  reported rather than silently skipped.
* A read-only operation on an `https://` input produces
  byte-identical output to the same operation on the downloaded
  file, across the read-side format roster.
* A sparse source transfers materially less than its virtual size,
  measured rather than asserted.
* A URL is refused by name for mutating operations, for outputs, and
  for image-supplied backing-file references.
* The VMM's framing parser has a `cargo-fuzz` target.
* Documentation in `docs/` describes the feature, and
  `docs/security.md` states what the partition does and does not
  cover.
* `ARCHITECTURE.md`, `README.md`, `AGENTS.md`, and
  `CHANGELOG.md` have been updated as needed.

### Documentation index maintenance

When creating a new master plan from this template, update
the following files in `docs/plans/`:

* **`index.md`** — add a row to the *Master plans* table
  with the creation date, a link to the plan, a one-line
  intent summary, the initial status, and links to each
  phase plan file. Keep the table in chronological order.
* **`order.yml`** — add an entry for the new master plan
  so it appears in the documentation navigation bar. Phase
  files should *not* be added to `order.yml`.

When all phases of a plan are complete, update the status
column in `index.md` to *Complete*.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.


* **Queue depth greater than one.** The real fix for serialised
  latency is for the guest to have several reads outstanding, which
  would speed up local operation too. It is much wider than this
  plan -- it changes the call table and every operation's read loop
  -- and this plan works around it with host-side readahead instead.
* **The guest's wait is a busy poll, and phase 1 made its cost
  unbounded.** `do_request` spins on `core::hint::spin_loop()` with no
  yield, so a host read that takes N seconds now burns a vCPU core for
  N seconds where the old spin budget capped it at about 1.3 s before
  failing. That is a non-issue against a local disk and exactly the
  case this plan creates: a remote backing store with multi-second
  cache misses and roughly 164,000 serialised round trips would spin
  at 100% for the whole transfer. Making the wait yield -- an `hlt`
  woken by the completion interrupt, or a notification MMIO exit --
  belongs with phase 3 or phase 6, where the latency arrives, and it
  pairs with the queue-depth entry above because both are changes to
  how the guest waits. Raised as a `consider` item on the phase 1
  review.
* **Remote output**, which needs a writer that does not seek
  backwards, or a staging file, and is a different feature.
* **Authentication**, deliberately excluded by open question 7. If
  it ever arrives, the credential belongs in the fetcher and never
  in the VMM, and the error paths from phase 8 have to be revisited.
* **Remote backing chains**, which would need a URL allowlist
  analogous to the existing path allowlist, and a decision that
  open question 9 currently answers with "never".
* **The FUSE deployment pattern.** An unprivileged HTTP-to-FUSE
  mount (`httpdirfs`, `rclone mount`, or a small `instar-httpfs`)
  gives the same partition with no change to `BackingStore` at all,
  and the kernel page cache does the caching and readahead. It is
  worth documenting as a zero-code alternative even though
  `/dev/fuse` is not reliably available in containers. Phase 1 is a
  prerequisite for it too, since FUSE read latency also blocks the
  VMM's I/O thread.
* **`nbdkit` as an alternative front end**, which would deliver most
  of this feature today at the cost of speaking a negotiated
  protocol in the privileged process.
* **Issue #860**, the 512-byte-per-request `create` path, which
  phase 6 must coalesce around and which would be better fixed at
  source.
* **Caching across invocations**, so that chain discovery and a
  following convert do not refetch the same headers in separate
  runs.
* **The VMM installs no logger unless `--verbose` is passed**
  (`main.rs`'s single `init_verbose_logger` call), so every
  diagnostic record in the binary is discarded by default, not only
  guest I/O errors. Wider than #924 was, and filed as **#931**,
  which weighs always installing the logger at a lower level
  against documenting the current behaviour as deliberate.
* **`copy` reports a failed read as success.** Every `send_error`
  path in `src/operations/copy/src/main.rs` returns before the single
  `send_complete(..., true)` at the end, and the host's `run_copy`
  neither requires a completion nor inspects one, so a failed read
  gives exit 0, no message outside `--verbose`, and a silently short
  output file. Filed as **#930**; the host half is the one that
  matters, since `run_copy` should require a completion the way the
  other operations' hosts do.
* **Carrying the virtio status byte into the guest's I/O helpers.**
  `do_request` reduces it to `status == VIRTIO_BLK_S_OK`, and
  `read_sector`, `write_sector` and `flush` return `bool`, so
  `shared::IoFailure`'s `status` field is unused. Widening those three
  signatures would let the guest say why.
* **Issue #849** (convert and dd keep their output file when the guest
  fails) stays out of scope. The phase 1 plan's Measurements section
  records that qemu-img keeps its own output on the same failure,
  which is evidence for whoever takes it.
* **Issue #932**, a virtqueue error the host cannot read, which is
  never completed and so hangs the guest now that the wait is
  unbounded. Every trigger is an instar bug rather than image-supplied
  data, and the fix is shared fatal state plus a way to kick the vCPU
  out of `KVM_RUN`, so phase 1 documented the case in
  `docs/guest-architecture.md` instead of fixing it. It is the one
  real cost of removing the spin budget.
* **Issue #933**, `docs/testing.md`'s test-structure list naming 11 of
  35 test modules. Phase 1 added its own two and the shared rig, and
  marked the list as curated rather than complete; completing it, with
  a CI guard in `tools/ci/check-test-partition.py`, is the fix.
* **#932 should be fixed in phase 3, not later.** Phase 1 made the
  guest's wait unbounded and made the host complete a failed request
  with an error status, which is what makes a remote read's failure
  survivable. But an error the host cannot *read* is still never
  completed, and phase 3 multiplies the ways the host can fail to
  answer by putting a socket and another process behind every read,
  so the hang stops being a corner case once the fetcher exists.
* **The measured datagram ceiling, for phase 6's block-size
  decision.** On a stock Linux host a socket pair that asks for the
  largest buffers is granted 425984 bytes an end, giving a
  425952-byte datagram ceiling; a pair left alone gets 212992 and
  212960. The kernel clamps a request to `wmem_max` and then doubles
  it, so neither figure follows from the sysctl by inspection. The
  negotiation settles on the 64 KiB default rather than either, and
  phase 6 raising that default is a change to one constant, because
  the headers and the requested buffers are already built for the
  256 KiB maximum.

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.


* **The guest spin budget**, issue #923, reproduced on
  2026-11-18 (open question 5): one host read slower than about a
  second failed the operation. Fixed in phase 1: the guest now waits
  for the host to complete the request.
* **convert's reporting of an I/O failure**, issue #924, from the
  same reproduction: a bare "convert operation failed" at exit 1,
  with no mention of I/O unless `--verbose` was passed. Fixed in
  phase 1: `convert` and `dd` now name the image, the offset in
  decimal and hex, and the errno, at exit 1. The truncated output
  file left behind is #849, not part of #924.
* **The LUKS inner-format probe**, issue #926. The `&&`-chained
  `read_input_sector` call in `convert_native_luks` was not a
  silent-zero-fill bug but a transient-failure misdetection: a failed
  read of the first payload sector looked like "no QCOW2 magic", so a
  LUKS-wrapped QCOW2 was converted as a flat payload at exit 0. Fixed
  in phase 1: a failed read of a sector that exists now fails the
  operation.
* **A host-side hang, found and fixed with no issue of its own.**
  `BlockDevice::do_read` propagated a backing-store failure without
  completing the virtio request, so once the guest's wait became
  unbounded an ordinary `EIO` hung it forever. A failed request is now
  completed with `request_status::IOERR`. This commit and the guest
  wait change are a pair for a bisect: between them, one host read
  error hangs `convert`.
* Issue #860 (`create` issues one 512-byte virtio request per
  sector) is adjacent rather than in scope: it makes the remote case
  pathological, and phase 6 has to coalesce regardless.

No open issue tracks remote or streaming input; a search of the
tracker at planning time found none.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->
