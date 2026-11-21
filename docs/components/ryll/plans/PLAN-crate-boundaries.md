# Crate boundaries and transcoding out

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, data flow, and code organisation.
Consult `docs/development.md` for build commands, and
`AGENTS.md` for project conventions and a table of protocol
reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

This plan is about repository and crate boundaries, so it has
its own required reading, and most of it is outside this
repository:

- `ARCHITECTURE.md`, the *Crate boundaries* section, and
  decision 27 in `docs/design-decisions.md` — the existing
  written argument for the current layout. This plan does not
  overturn either; it adds the distinction they are missing.
- `docs/releasing.md` and `tools/publish-crates.sh` — how the
  seven crates reach crates.io, and what `make propose-release`
  does.
- `docs/plans/PLAN-rdp-frontend.md`, especially *Moving to
  Kerbside* under Future work. That section already carries the
  Kerbside RDP integration work in detail (process shape,
  credentials, authorisation, control plane, routing, capacity,
  testing, framing). **Do not restate it here.** Phase 5's job
  is to give it a home in Kerbside and to widen it to `--web`,
  not to rewrite it.
- `shakenfist/kerbside`: `docs/plans/PLAN-rdp-gateway.md` (the
  superseded statement of direction),
  `docs/plans/PLAN-spice-performance-phase-03-transcoding.md`
  (the spike that measured SPICE-to-SPICE transcoding and
  returned no-go), `rust/kerbside-proxy/Cargo.toml` (the
  comment on the `rev` pin and the wheel publish trigger), and
  `rust/kerbside-proxy/src/transcode/`.
- `shakenfist/andris`: `andris/andris/Cargo.toml` and
  `README.md` — the second consumer of the crates, and the one
  whose README already describes itself as "a sibling of ryll".

Ground every claim about those repositories in their code and
their plan files rather than in this document, and say so when a
claim here has gone stale.

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

Written 2026-11-21, after Michael asked two questions that are
usually asked together and have opposite answers:

1. Was it a mistake to land the web and RDP transcodes in ryll
   rather than in Kerbside?
2. Should the `shakenfist-spice-*` crates move out of ryll into a
   central shared crates repository, taking the crates Kerbside
   and andris use with them?

The survey that followed said **no** to both, and found that what
actually hurts is three smaller facts that are each fixable
without moving a repository. This plan fixes those, writes down
the distinction the existing architecture documents are missing,
and records the direction the first question was really reaching
for: transcoding out of Kerbside as a proxy feature, with ryll
keeping its own modes.

### What the survey found

Every claim below was checked against the trees on 2026-11-21 and
carries its evidence, because the next reader will reasonably
doubt the conclusion.

**The two transcodes are not the same product.** ryll's `--web`
and `--rdp` serve one session to the one person already running
ryll, on top of state ryll holds anyway: the decoded framebuffer,
cursor, input state, audio and session lifecycle. They are output
modes in the sense the GUI is an output mode, which is what the
README already claims when it calls ryll "multi-modal". A
Kerbside gateway is many sessions, nobody's desktop, and needs
brokering, authentication, tenancy and placement. Moving `--web`
and `--rdp` into Kerbside today would mean Kerbside grows a
complete SPICE client in order to re-serve from it — which is to
say, importing ryll as a library and calling it a relocation.

**Kerbside's existing transcoding is a different thing again.**
`rust/kerbside-proxy/src/transcode/` decodes the backend's
display channel and re-encodes it **as SPICE**, for the
inspection story, behind a non-default `transcode` feature.
Policy transcoding in the proxy, delivery transcoding in the
client. The proxy's spike
(`PLAN-spice-performance-phase-03-transcoding.md`) returned
**no-go** on it — but it judged SPICE-to-SPICE transcoding on
*performance*, and concluded that relay with
`streaming-video=all` wins except on incompressible activity on
links of 10 Mbit or less. Reachability is a different question
and that verdict does not settle it. Phase 5 must say so
explicitly, because the two will otherwise be read as one.

**The crate split already anticipates the gateway.**
`shakenfist-spice-rdp` holds all the IronRDP-facing code and does
not depend on the `ryll` binary; `ARCHITECTURE.md` says in as
many words that "a gateway could serve RDP from SPICE sessions
without it". The substrate is egui-free for the same reason. The
code is already positioned for a Kerbside gateway without a file
moving.

**But `--web` has no such crate, and that is the real
asymmetry.** Everything that would make a Kerbside web gateway
cheap lives in the ryll binary instead:

| File | Lines | Kerbside would need it |
|------|-------|------------------------|
| `ryll/src/web/server.rs` | 1,219 | Mostly — HTTP, TLS, token auth, routing |
| `ryll/src/web/inputs.rs` | 1,036 | Yes — browser key/pointer translation |
| `ryll/src/web/signalling.rs` | 830 | Yes — SDP/ICE exchange |
| `ryll/src/web/lifecycle.rs` | 432 | Yes |
| `ryll/src/web/cursor.rs` | 255 | Yes |
| `ryll/src/web/control.rs` | 183 | Yes |
| `ryll/src/web/audio.rs` | 177 | Yes |
| `ryll/src/web/assets/` | 2,000+ | Yes — the browser half |

8,379 lines under `ryll/src/web/`, 4,213 of them Rust. A Kerbside
web gateway written today duplicates nearly all of it. `--rdp`'s
glue is `ryll/src/rdp.rs` at 1,448 lines, which is also larger
than glue should be and worth auditing for the same reason. This
is the concrete form of Michael's own instinct — *"having a
similar feature in ryll is both useful and not a lot of extra
code if we get the crates right"*. Today the crates are right for
RDP and wrong for web.

**The downstreams consume ryll's development branch, not its
releases.** Both pins are by `rev` on ryll `develop`, they do not
agree with each other, and both are *ahead of* the newest
published version:

| Consumer | Pinned rev | Date | In `v0.3.0`? |
|----------|-----------|------|--------------|
| `kerbside-proxy` (protocol, renderer, compression) | `b33dea44` | 2026-10-27 | No |
| `andris` (protocol, compression) | `38c51eff` | 2026-10-17 | No |

`v0.3.0` is `f12d9b20`, tagged 2026-10-17, and
`tools/publish-crates.sh` published all seven crates at it. So
the published crates do **not** carry the API either downstream
uses: a release is a precondition for moving the pins, not an
alternative to it. Meanwhile both downstreams are frozen a month
behind, receiving no fixes, and every crate change is a
three-repository rev dance regardless of which directory the
crates sit in. *This* is what makes the layout feel wrong — not
the directory.

`kerbside-proxy`'s manifest also makes the pin load-bearing:
`dev-proxy-wheel.yml` excludes `Cargo.lock` from its publish
trigger on the grounds that a lockfile-only change cannot alter
the binary's interface, "that holds only while every git
dependency is pinned by rev here". Phase 4 has to resolve that
rather than break it.

**The RDP crate's MSRV leaks onto every published crate.**
`ironrdp-server` declares 1.89, `[workspace.package]` sets
`rust-version = "1.89"`, and all seven members take it with
`rust-version.workspace = true`. So `shakenfist-spice-protocol`
— which andris and Kerbside both depend on, and which needs
nothing of the sort — tells crates.io it requires 1.89 because of
a default-off feature of a sibling. The real floor beneath it is
1.88, for `slice::as_chunks` in `shakenfist-spice-compression`.
No CI lane checks any of this, so the declared floor is currently
a claim rather than a fact.

### Why the crates stay in ryll

Decision 27 in `docs/design-decisions.md` already argues this and
the argument holds: one commit and one `Cargo.lock` for a type
change and every call site, one CI and pre-commit configuration,
and versioning as a set so the independent versioning a separate
repository buys goes unused. ryll is overwhelmingly the heaviest
consumer — `shakenfist-spice-renderer` alone is 37,724 lines, and
the crates co-evolve with ryll's frontends. A shared crates
repository would relocate the rev-pin problem rather than solve
it, and would add a repository needing its own CI, `PUSH-AUDIT.md`,
`AGENTS.md`, review tooling and consistency-audit entry.

What decision 27 does not give is a **trigger**, which is why the
question keeps returning. Phase 1 adds one:
`shakenfist-spice-protocol` is the crate where "the SPICE
protocol" is genuinely not ryll's property — client, server and
proxy all sit on it — so the signal is when consumers other than
ryll start driving its change rate. Today they consume it. When
they author it, extract that crate alone, with
`git filter-repo` as decision 27 already anticipates.

### Why the frontends stay in ryll

Both modes are load-bearing for ryll's own identity and testing:
`--web` exists to dogfood ryll in place of Kasm plus Apache
Guacamole (`PLAN-web-frontend.md`), and `--rdp` is a reachability
fallback for users who can install nothing (`PLAN-rdp-frontend.md`).
Neither depends on Kerbside being in the path, and a developer
debugging a transcode wants it in a single process with the
diagnostics ryll already has, not behind a proxy and a control
plane.

The honest cost of keeping them is narrower than the question
assumed: it is the MSRV leak, which phase 2 fixes, and the fact
that ryll is where IronRDP's API churn and its security findings
land. That second cost is real — a held branch waits on
Devolutions as this is written — but a shared *crates* repository
does nothing for it, and one embargo does not justify a repository
for `shakenfist-spice-rdp`.

## Mission and problem statement

**Mission.** Make the crate boundaries honest, so that ryll pays
nothing for frontends it should not pay for, andris and Kerbside
consume released crates rather than ryll's development branch,
and when Kerbside grows web and RDP transcoding it is glue over
published crates rather than a second implementation.

The problems, in the order the plan takes them:

1. The architecture documents argue for the current layout but do
   not distinguish a single-session frontend from a multi-tenant
   gateway, so the question of where the transcodes belong has no
   recorded answer and gets re-litigated.
2. A default-off RDP feature imposes a 1.89 MSRV on published
   crates that do not need it, and nothing verifies the floor.
3. `--web`'s reusable half is trapped in the `ryll` binary, so
   the Kerbside web gateway Michael intends would have to
   duplicate about 4,200 lines of Rust and the browser assets.
4. andris and Kerbside pin `rev`s on ryll `develop` that no
   published version contains, so they are a month stale and a
   crate change is a three-repository dance.
5. Kerbside's statement of direction for transcoding out is
   marked `Superseded` and covers only RDP, so Michael's intent
   to serve both web and RDP from the proxy is recorded nowhere
   that a Kerbside reader would find it.

**Out of scope**, explicitly, so that no phase drifts into it:

- Moving any crate to its own repository or to a shared crates
  repository. Phase 1 records the trigger; nothing acts on it.
- Moving `--web` or `--rdp` out of ryll, now or when the Kerbside
  gateway exists. Both stay.
- Implementing the Kerbside gateway. Phase 5 writes the direction
  and the preconditions; the build is a Kerbside master plan of
  its own, which phase 5 commissions and does not write.
- Re-opening the SPICE-to-SPICE transcoding verdict. It was
  measured and it stands.

## Open questions

1. **One crate or two for the web split?** A new
   `shakenfist-spice-web` beside `shakenfist-spice-webrtc`, or
   fold the server, signalling, inputs and lifecycle into
   `-webrtc` itself? Two crates keep the heavy webrtc-rs tree
   separable from an HTTP server that a gateway might want
   without it; one crate avoids a dependency edge that is always
   traversed in practice. Phase 3 decides, with the dependency
   direction as the deciding evidence rather than taste.
2. **Does the web crate carry the browser assets?** They are
   2,000-odd lines including a vendored copy of `sfui`, which is
   already a consistency finding (issue #735). A Kerbside gateway
   needs the same assets and must not vendor a third copy. The
   options are: assets in the crate, assets as a separate
   published artefact, or Kerbside serving ryll's. Interacts with
   #735, so phase 3 coordinates rather than deciding alone.
3. **Exact or caret version pins downstream?** `kerbside-proxy`'s
   wheel publish trigger currently relies on git `rev` pins being
   exact. `=0.4.0` preserves that reasoning verbatim; a caret
   range needs `dev-proxy-wheel.yml` to stop treating
   `Cargo.lock` as uninteresting. Phase 4 decides and the
   manifest comment must be rewritten either way.
4. **Does `ryll` itself keep the workspace floor?** Once
   `shakenfist-spice-rdp` declares 1.89 alone, the `ryll` binary
   built `--features rdp` needs 1.89 while its default build
   needs 1.88. Declare 1.88 and let cargo's MSRV error name
   IronRDP, or declare 1.89 for the binary and accept that a
   client-only build overstates its floor?
5. **Does the Kerbside gateway own the transcoder or supervise
   it?** Kerbside's superseded plan proposed one sandboxed worker
   process per session over `shakenfist-spice-rdp`. The
   alternative is supervising `ryll --web` / `ryll --rdp`
   processes, which needs no new code at all but ships a GUI
   client's binary on a server. Phase 5 states the question for
   the Kerbside plan to answer; it is not this plan's to settle.
6. **How is the extraction trigger measured?** A per-crate count
   of authoring repositories is easy to state and easy to forget.
   Phase 1 decides whether it becomes a line in the
   consistency-audit pipeline, a question in a periodic review, or
   simply a sentence in the decision record.

## Execution

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| 1. Decision record | PLAN-crate-boundaries-phase-01-decision.md | Not started | |
| 2. MSRV de-leak | PLAN-crate-boundaries-phase-02-msrv.md | Not started | |
| 3. Web frontend crate | PLAN-crate-boundaries-phase-03-web-crate.md | Not started | |
| 4. Release and downstream pins | PLAN-crate-boundaries-phase-04-pins.md | Not started | |
| 5. Kerbside transcoding-out direction | PLAN-crate-boundaries-phase-05-kerbside.md | Not started | |
| 6. Push audit | This file, below | Not started | |

Phase plans are written at the start of each phase (see the
`next-phase` skill), so each can react to what the previous one
found.

Phases 1 and 2 are independent of the rest and can land in any
order. Phase 4 depends on phases 2 and 3 only in the sense that
it should carry their results in one release rather than cutting
two; if the stale pins start costing more than that saves, phase 4
can be pulled forward and the release repeated. Phase 5 depends on
phase 3, because the whole point of its claim — that the Kerbside
feature is small — is false until the web crate exists.

### Phase 1: Decision record

**Planning effort: medium.** The argument is already made in this
file; the work is placing it where it will be found and keeping
it short.

Add decision 28 to `docs/design-decisions.md`: *a single-session
frontend and a multi-tenant gateway are different products, and
the transcodes in ryll are the former*. It states the distinction,
why `--web` and `--rdp` stay in ryll even once Kerbside has its
own, and that the crate split is what lets both exist without a
second implementation. It is prose, not a survey: decision 27's
length is the model.

Extend the *Crate boundaries* section of `ARCHITECTURE.md` by one
paragraph with the same distinction and a link to the decision —
this is the shape of the system, which is what `ARCHITECTURE.md`
is for, and the section already makes the `-rdp` half of the
argument.

Add the extraction trigger to decision 27 rather than to a new
decision, since it is the missing half of an argument already
recorded there: name `shakenfist-spice-protocol` as the candidate,
name "consumers other than ryll author more of its changes than
ryll does" as the signal, and answer open question 6 by saying
where that gets noticed. Record the 2026-11-21 baseline so a later
reader can tell whether it has moved.

Nothing in this phase touches code. It must not grow into a
rewrite of either document: a reviewer should be able to read the
whole diff in a few minutes.

### Phase 2: MSRV de-leak

**Planning effort: medium.** Mechanical in shape, but the
verification is the point and is easy to get wrong.

Give `shakenfist-spice-rdp` its own `rust-version = "1.89"` with a
comment saying it is IronRDP's floor and deliberately not the
workspace's, and lower `[workspace.package].rust-version` to
`"1.88"`, keeping the existing comment's explanation of where 1.88
comes from (`slice::as_chunks`) while deleting the part that
blames IronRDP for the workspace floor. Answer open question 4 for
the `ryll` member.

Then make the floor a fact rather than a claim. There is no MSRV
lane in CI today: add one to the merge tier of `ci.yml`, pinning
`dtolnay/rust-toolchain` to the floor and running `cargo check
--workspace --exclude shakenfist-spice-rdp`, plus a second check
of `shakenfist-spice-rdp` alone at its own floor. The merge tier
rather than the smoke tier because it is a second toolchain
download; `PLAN-two-stage-ci.md` has the tiering rules. If a
member turns out not to build at 1.88, the finding is the result:
record the true floor and declare that instead of raising the
declaration quietly to whatever stable happens to be.

Verify locally before CI, in the devcontainer, with the pinned
toolchain rather than by reading the code — a `rust-version`
nobody has executed is how the current state arose.

### Phase 3: Web frontend crate

**Planning effort: high.** This is the phase that does real work,
and it is the one with judgment calls: where the seam falls, what
the gateway needs, and what stays CLI glue.

Answer open questions 1 and 2 first, in the phase plan, with
evidence from the files rather than from preference.

Then extract the reusable half of `ryll/src/web/` into a crate, so
that `--web` has the shape `--rdp` already has: a library crate
holding everything protocol-facing and frontend-facing, no
dependency on the `ryll` binary, and a thin module in `ryll` doing
CLI, configuration and process concerns. The target split, to be
confirmed by the phase plan and not assumed:

- **Into the crate**: `signalling.rs`, `inputs.rs`, `cursor.rs`,
  `control.rs`, `audio.rs`, `lifecycle.rs`, and the parts of
  `server.rs` that are HTTP routing, the WebRTC offer exchange and
  the static-asset handler.
- **Staying in `ryll`**: argument parsing, the token file and its
  on-disk conventions, local TLS certificate selection, anything
  that reads ryll's settings, and anything that assumes one
  session per process.
- **To be decided**: the browser assets (open question 2), and
  whether per-session supervision belongs to the crate or the
  consumer.

Audit `ryll/src/rdp.rs` (1,448 lines) in the same pass and record,
without necessarily acting, which of it is glue and which is
frontend logic that belongs in `shakenfist-spice-rdp` beside the
rest. The symmetry claim this plan rests on is only true if the
RDP glue is genuinely glue; if it is not, say so and let a later
phase or plan move it.

Reserve the crate name on crates.io before the code moves, per the
*Reserving a new crate name* procedure in `docs/releasing.md`, and
add it to `tools/publish-crates.sh` in dependency order. Add the
row to `ARCHITECTURE.md`'s crate table and extend *Crate
boundaries* with the reason it is separate, exactly as `-webrtc`
and `-rdp` already have. Behaviour must not change: `--web` works
the same before and after, and the `web-smoke` lane is the
evidence.

### Phase 4: Release and downstream pins

**Planning effort: high.** Three repositories, a release, and a
publish trigger whose correctness argument has to be rewritten
rather than broken. Cross-repository phases are where this
ecosystem has previously lost work.

Cut a ryll release carrying phases 2 and 3 (`make
propose-release`, per `docs/releasing.md`), so that the API andris
and Kerbside actually use is published. Confirm after publication
that each crate's declared `rust-version` on crates.io is the one
phase 2 intended.

Then move both downstreams off git `rev` pins, as one pull request
per repository, each audited against its own repository's default
branch and recorded in this plan's `Merged` cell in the `<repo>
<sha> (#pr)` form the push-audit block requires:

- **andris** — `shakenfist-spice-protocol` and
  `-compression` to the published version. The manifest's comments
  are unusually good and explain which transitive version each
  dependency shares with the crates; they must survive the edit,
  with any version numbers they name re-checked rather than
  carried over.
- **kerbside** — `-protocol`, `-renderer` and `-compression` to
  the published version, and answer open question 3. Whichever way
  it goes, the `rev`-pin comment in
  `rust/kerbside-proxy/Cargo.toml` is now wrong and must be
  rewritten to state the new reasoning, and
  `dev-proxy-wheel.yml`'s publish trigger must be shown to still
  be correct under it — shown, not assumed.

Both repositories keep a documented escape hatch for pinning a
`rev` again during cross-repository development, because the
pattern is legitimate while an API is in flight; what is not
legitimate is leaving it there for a month. Say in each manifest
how to do it and that it is temporary.

Verify each downstream builds and tests against the published
crates, including `kerbside-proxy` with `--features transcode`,
which is the only consumer of the renderer and compression
encoders outside ryll.

### Phase 5: Kerbside transcoding-out direction

**Planning effort: high.** Mostly writing, but it is the writing
that commits the ecosystem to a direction, and it has to hold two
things apart that will otherwise be conflated.

This phase lands in `shakenfist/kerbside`, as its own pull
request, and produces a statement of direction — not an
implementation and not a schedule.

Replace the superseded, RDP-only `PLAN-rdp-gateway.md` with a plan
covering **transcoding out of the proxy for both web and RDP** as
an opt-in Kerbside feature. It must:

- State Michael's intent plainly: Kerbside should be able to serve
  a console to a browser and to a stock RDP client, as a proxy
  feature, for clients that cannot speak SPICE to it.
- Keep the framing Kerbside's `docs/index.md` requires: SPICE
  relay stays the default and the recommended path, and
  transcoding out is an explicit fallback about **reachability**.
- Hold the performance verdict apart from this one, in as many
  words. `PLAN-spice-performance-phase-03-transcoding.md` measured
  SPICE-to-SPICE transcoding and said no-go; that is not a verdict
  on serving a client that cannot speak SPICE at all, and a reader
  who conflates them will think this plan contradicts a
  measurement. Cite the spike, say what it did and did not
  settle, and carry over what it *did* establish about CPU cost
  and node placement, since a transcoding gateway inherits that.
- Reference, and not restate, *Moving to Kerbside* in ryll's
  `PLAN-rdp-frontend.md` for the integration detail already worked
  out: process shape, credentials from `consoletoken`,
  authorisation and the gRPC contract hash, control plane and
  ports, routing tokens, capacity, testing and framing. Say that
  it now also applies to the web half, and work out where the two
  differ — a browser gets a URL and a cookie where mstsc gets a
  typed token, so the credential story is not shared.
- Record what the feature costs in code, now that phase 3 has
  made that answerable: glue over `shakenfist-spice-web`,
  `-webrtc` and `-rdp` plus the control-plane integration above.
  Give the figure, and say what it was before phase 3, because
  that difference is the argument for this plan.
- Say that ryll keeps `--web` and `--rdp` when this ships, and
  why: they cost ryll little once the crates are right, they are
  how the transcodes are developed and debugged, they serve users
  with no Kerbside in the path, and `--web` is how ryll is
  dogfooded against Kasm. Point at decision 28.
- Carry open question 5 forward as the first thing its own phase 1
  must decide.

Also update ryll's `PLAN-rdp-frontend.md` so *Moving to Kerbside*
points at the new Kerbside plan as its destination, and resolve its
open item *"the same question for `--web`"* by noting that this
plan answers it: both, together.

### Phase 6: Push audit

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

In this repository the Execution phases are the *Phase order*
table each master plan carries, and `Merged` is its last column,
after `Status` — last because a row that omits it must still
reach `Status`. Every phase here lands as its own pull request,
so the cell holds one merge commit; the block's other shapes are
for repositories where a phase lands directly. See *Two ways this
runbook is invoked* in `PUSH-AUDIT.md` for what the push-audit
phase then does with those commits.

This plan's audit has an unusual range: phases 4 and 5 land in
`shakenfist/andris` and `shakenfist/kerbside`, and are audited
against those repositories' default branches as part of the pull
requests that land them. This phase cites those audits rather than
re-running them, and audits the ryll phases itself. Kerbside has
its own `PUSH-AUDIT.md`; andris's absence or presence of one is to
be checked rather than assumed, and if it has none the shared
floor checklist applies, as `PLAN-spice-performance-phase-06`
did for `kerbside-patches`.

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

    Phases involving deep protocol research, algorithm
    understanding, or architectural decisions should be planned
    at high effort. Phases that are mechanical or follow
    well-established patterns can be planned at medium effort.

Each phase above states its own recommended planning effort.
Phases 3, 4 and 5 are high: a crate seam, a three-repository
release, and a statement of direction are all expensive to get
wrong and cheap to think about first.

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

    A worked brief for this codebase: instead of "add tests for
    the QUIC decoder", write "add tests for `quic_decode()` in
    `shakenfist-spice-compression/src/quic.rs`. Test vectors: a
    2x2 RGBA image encoded with the reference C encoder at
    `/srv/src-reference/spice/spice-common/...`. The function
    takes `(data, width, height)` and returns
    `Option<Vec<u8>>` of RGBA pixels."

For this plan specifically: a sub-agent working in andris or
Kerbside has not read ryll's architecture and must not be asked to
infer it. Briefs for phases 4 and 5 carry the facts from the
*Situation* section above — the pinned revs and their dates, the
tag they are ahead of, the wheel publish trigger's reasoning —
rather than telling the sub-agent to go and find them.

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

    - [ ] The code builds (`pre-commit run --all-files` or
          equivalent).
    - [ ] Tests pass (`cargo test --workspace` or equivalent).

Use `make` rather than `cargo` directly in this repository; the
Makefile wraps cargo in the devcontainer.

## Administration and logistics

### Success criteria

We will know when this plan has been successfully implemented
because the following statements will be true:

* `docs/design-decisions.md` records why a single-session
  frontend and a multi-tenant gateway are different products, and
  decision 27 records the trigger for extracting a crate to its
  own repository, with the 2026-11-21 baseline.
* No published `shakenfist-spice-*` crate declares an MSRV it
  does not need, and a CI lane fails if one stops building at its
  declared floor.
* `--web`'s reusable half is a published crate that does not
  depend on the `ryll` binary, `ryll/src/web/` is CLI and process
  glue, and `--web` behaves exactly as it did before.
* Neither andris nor Kerbside depends on a git `rev` of ryll;
  both build and test against published crates, with
  `kerbside-proxy --features transcode` among the things verified.
* `dev-proxy-wheel.yml`'s publish trigger has a correctness
  argument that matches how the dependency is actually specified.
* Kerbside carries a current statement of direction for serving
  both web and RDP out of the proxy, which states the reachability
  framing, holds the SPICE-to-SPICE performance verdict apart from
  it, gives the code cost in terms of the shared crates, and says
  ryll keeps its own modes and why.
* The code passes `pre-commit run --all-files` (rustfmt,
  clippy with `-D warnings`, shellcheck).
* New code follows existing patterns: channel handler
  structure, message parsing via `byteorder`, async tasks
  via tokio, event communication via mpsc channels.
* There are unit tests for new logic, and the existing tests
  still pass (`make test`).
* Lines are wrapped at 120 characters, single quotes for
  Rust strings where applicable.
* Documentation in `docs/` has been updated to describe any
  new features or configuration options — for this plan that is
  `docs/web-mode-internals.md` and `docs/web-frontend.md` for the
  crate split, and `docs/releasing.md` for the new crate in the
  publish order.
* `ARCHITECTURE.md` has been updated only if the shape of
  the system changed, and `AGENTS.md` only if a convention
  changed. Both are a summary and an index into `docs/`.
  Phase 3 does change the crate inventory, so `ARCHITECTURE.md`
  is in scope for it; phase 1 adds one paragraph to the same
  section, and nothing else in this plan should touch either file.
* If the changes affect SPICE protocol behaviour, the
  relevant documentation in `shakenfist/kerbside/docs/` has
  also been reviewed and updated if needed.

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

When all phases of a plan are complete — including the
push-audit phase, and every finding it raised — update the
status column in `index.md` to *Complete*.

Phases 4 and 5 land in other repositories, so each of those also
needs a row or a note in *that* repository's `docs/plans/index.md`
where it would otherwise be invisible: Kerbside's new plan is a
Kerbside index entry, and the andris manifest change is small
enough to need none.

<!-- shared-block: plan-closeout-sections v1 -->
Plan close-out sections (shared block; do not edit -- the
canonical copy lives in shakenfist/development at
`templates/shared-blocks/plan-closeout-sections.md`):

### Future work

We should list obvious extensions, known issues, unrelated bugs we
encountered, and anything else we should one day do but have
chosen to defer to here, so that we do not forget them.

...

### Bugs fixed during this work

This section should list any bugs we encounter during development
that we fixed. You should also scan the project's issue tracker,
where one exists, for directly related issues that we should
either resolve as part of this master plan or at least be aware of
while planning it.

...

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.
<!-- shared-block-end -->

Plan-specific close-out notes:

* **Future work.** A shared crates repository, if and when
  `shakenfist-spice-protocol`'s change rate stops being driven by
  ryll -- phase 1 records the trigger, nothing acts on it, and the
  extraction would be of that one crate rather than the set.
  `ryll/src/rdp.rs`'s glue, if phase 3's audit finds frontend logic
  in it that belongs in `shakenfist-spice-rdp`; moving it is a
  separate change, so that the web extraction and an RDP refactor
  do not land in one diff. Kerbside's web and RDP gateway itself,
  as the Kerbside master plan phase 5 commissions. A
  `shakenfist-spice-*` MSRV policy, if phase 2's lane turns out to
  need a floor-raising procedure rather than a one-off correction.
  RD Gateway (MS-TSGU) over 443 and the other items under *Other
  extensions* in `PLAN-rdp-frontend.md` stay where they are; this
  plan does not adopt them.
* **Bugs fixed.** None yet.
* **Related issues.** [#735](https://github.com/shakenfist/ryll/issues/735)
  (consistency: vendored `sfui` copy) is touched by phase 3's open
  question 2 but not owned by it. A third copy of `sfui` appearing
  in Kerbside is the outcome to avoid, so phase 3 coordinates with
  #735 rather than deciding the assets question alone.
  [#699](https://github.com/shakenfist/ryll/issues/699) (crates.io
  trusted publishing) is adjacent to phase 4's release but is not
  a precondition for it.
