# RDP frontend phase 6: documentation and parity

Master plan: [PLAN-rdp-frontend.md](/components/ryll/plans/PLAN-rdp-frontend/).

**Planning effort:** medium, as the master plan's guidance sets
for phases 3 to 6: this phase documents what phases 1 to 5
built and changes no code. Review step 3 (the parity column) at
high effort, because a wrong cell there reads as a fact about
the product.

## Prompt

Read the master plan's *Prompt* first; everything there
applies. For this phase in particular:

- Describe the code, not the plans. Phases 1 to 5 recorded
  their designs in `docs/plans/`, and some of those plans were
  corrected as the work went; the code on `develop` is the
  authority. Every behavioural claim a step writes is checked
  against the source it describes, and the brief names the
  files.
- The RDP side is IronRDP **0.13.0** (`ironrdp-server` 0.13.0).
  `make fetch` puts its source under `.cargo-cache/registry/`
  in the worktree; read it there when a claim depends on it,
  and never build or run anything from inside it.
- `--rdp` is a reachability fallback, not a better path. Every
  page that introduces it says so, in the words the crate
  README and `docs/configuration.md` already use.
- Builds, where a step needs one, run in Docker through the
  Makefile, never with a host toolchain.
- No plan, phase, decision or survey-item citations in code or
  in docs outside `docs/plans/`.

## Scope

In scope:

- `docs/rdp-frontend.md`, an operator guide matching
  `docs/web-frontend.md` where the two modes overlap.
- `docs/rdp-mode-internals.md`, matching
  `docs/web-mode-internals.md` in purpose, much shorter in
  length.
- An RDP column in `docs/multi-mode-parity.md`, every cell
  filled, and the page's "three modes" framing updated.
- `ARCHITECTURE.md` (seven crates, four modes, the new
  crate's boundary), `README.md` (one usage example and one
  link), `docs/index.md`, `docs/features.md`.
- Moving the behaviour prose out of `docs/configuration.md`'s
  *RDP Mode* section into the guide, leaving the flag table.
- The crate README's claim about which clients it serves.
- The master plan: answers to open questions 5 and 8 as far as
  they can be given, phase 5's deferred items lifted into the
  master plan's *Future work*, and a record of the success
  criteria this phase can check.

Out of scope:

- Any code change. If a step finds a defect while checking a
  claim, it records it (file and line, what is wrong) and the
  management session files an issue; the doc describes the
  code as it is.
- Turning the `rdp` feature on in release builds (open
  question 8). Phase 5 set the criterion, fourteen consecutive
  green nightly runs of the RDP smoke lane, and that record
  does not exist yet.
- Kerbside's documentation. The survey found nothing there to
  change (item 9).
- Testing new RDP clients, unless Mikal can supply one at the
  back brief (decision 6).

## What the survey found

Checked against `develop` at 280c047.

1. **`docs/rdp-frontend.md` and `docs/rdp-mode-internals.md`
   do not exist.** `docs/configuration.md:76-129` holds the
   only user-facing description of the mode, and says "An
   operator guide is to come" (`:82`).
2. **`docs/multi-mode-parity.md` has no RDP column.** It lists
   RDP as a mode (`:26`, "the column lands with the operator
   guide") but the matrix (`:78` on) has three columns, GUI,
   Headless and Web, and about 120 rows, plus a *Web frontend
   (--web mode)* section of web-only rows. Its opening line
   (`:3`) says "three delivery modes".
3. **`ARCHITECTURE.md` does not mention RDP at all.** It says
   the workspace has 6 crates (`:24`) and lists them without
   `shakenfist-spice-rdp`; the mode table (`:52-56`) and "All
   three modes ship" (`:58`) know three modes; the *Where the
   detail lives* table (`:9-20`) has no RDP row; *Crate
   boundaries* (`:81-91`) explains why `shakenfist-spice-webrtc`
   is separate but not the RDP crate, which is separate for
   the same reason and is also behind a default-off feature.
4. **`README.md` and `docs/index.md` do not mention `--rdp`.**
   `README.md:73` has a `--web` usage example and `:88` a link
   to the web guide; `docs/index.md`'s *Documentation Index*
   (`:83`) links the web guide only. `docs/features.md:64`
   has a *Web mode* bullet and no RDP one.
5. **No released binary has `--rdp`.** The `rdp` feature is
   default-off and `make release` leaves it out (`AGENTS.md:236-
   238`, `ryll/Cargo.toml:58-62`). The latest release, v0.3.0
   (2026-10-17), predates the `shakenfist-spice-rdp` crate,
   which is not on crates.io. An operator has to build from
   source today (`make release-rdp`, or cargo with
   `--features rdp`; `docs/development.md:587` covers the
   developer side).
6. **Only FreeRDP has been tested.** Every phase's *Results*
   tested FreeRDP 3.15.0 on Linux and recorded mstsc as not
   tested, for want of a Windows machine
   (`PLAN-rdp-frontend-phase-02-rdp-skeleton.md:1105`,
   phase 3 `:614`, phase 4 `:567`). The crate README
   (`shakenfist-spice-rdp/README.md:4-6`) says the crate
   serves "mstsc on Windows, FreeRDP, and the RDP apps on
   macOS, iOS and Android", which is a claim of reach no test
   backs. The master plan's first success criterion names
   mstsc.
7. **Open question 5 (Unicode key events) was implemented but
   never answered.** `shakenfist-spice-rdp/src/input.rs:411-417`
   drops `UnicodePressed`/`UnicodeReleased` with a warn-once,
   as the recommendation said, and a test covers it (`:848`).
   Open question 8 (packaging) waits on the smoke lane's
   record (item 5).
8. **Phase 5's deferred items live only in its phase plan.**
   Its *Future work* lists SPICE reconnect for `--web` and
   `--headless`, a text overlay for the outage frame, the
   lane's promotion, reporting a red nightly, replaying a
   resize made during an outage, per-peer limits on the
   pre-authentication slot, and a cap on reconnects to a
   server that drops every session. None is in the master
   plan's *Future work*, which is what outlives the plan.
9. **Kerbside needs nothing.** The master plan's last success
   criterion asks that Kerbside documentation mentioning RDP
   out point here. On Kerbside's `develop`, nothing outside
   `docs/plans/` mentions RDP, and the superseded
   `docs/plans/PLAN-rdp-gateway.md` and its index row already
   link to this plan. The *Framing* item under *Moving to
   Kerbside* covers Kerbside's docs for when the code moves.
10. **What `docs/configuration.md` already gets right.** Its
    RDP prose (`:84-117`) is accurate as of phase 5: cursor,
    resize, clipboard, audio, takeover and reconnect. It is in
    the wrong place for its length, not wrong; the guide takes
    it over (decision 3).

The master plan's phase 6 section is accurate as far as it
goes; this survey adds items 5 to 8. They are recorded in the
master plan's phase 6 section in the planning commit.

## Decisions

1. **The operator guide follows `docs/web-frontend.md`'s
   outline where the modes overlap, and leaves out what
   `--rdp` does not have.** Sections: what it is and when to
   use it (the fallback framing first); quick start, starting
   with getting a binary that has the mode (item 5); clients
   (decision 6); what works and what is lost; credentials and
   security modes; certificates; one client at a time and
   reconnect, summarised with a link to
   `docs/session-lifecycle.md` rather than repeated; what it
   costs (the phase 2 measurements, as numbers with their date
   and build); running it as a service; troubleshooting; a
   security note; the CI smoke lane. No reverse-proxy section:
   RDP is not HTTP.
2. **A short internals page, not none.** The master plan made
   it conditional ("if the internals warrant it"). They do:
   the per-connection IronRDP servers behind ryll's own accept
   loop, the takeover rules, the display handler's damage and
   outage paths, input back-pressure, and the reconnect
   supervisor's per-attempt channels are design that today
   lives only in phase plans, and `ARCHITECTURE.md` needs a
   page to point at. Aim for a few hundred lines, organised by
   module, each section naming the invariant a change must
   keep, not a narrative of how it was built.
3. **`docs/configuration.md` keeps the flags, the guide keeps
   the behaviour.** The *RDP Mode* section becomes one
   paragraph (what the mode is, that it needs the `rdp`
   feature, a link to the guide) plus the flag table. The
   behaviour prose moves to the guide, so no fact about the
   mode is stated on two pages. The `--rdp` row's description
   is cut to match.
4. **The parity column states what an RDP client can reach.**
   A cell reads `available` only when a stock RDP client can
   use the feature through `--rdp`; a feature ryll offers only
   to its own operator (a control socket verb, `--capture`) is
   judged as the Web column judges it, by what the mode wires
   up. "n/a — intrinsic" needs a reason in parentheses, as
   elsewhere. RDP-only features (security modes, takeover,
   outage frame, auto-reconnect) get an *RDP frontend (--rdp
   mode)* section beside the web one, with every column
   filled. Existing rows that describe reconnect behaviour
   (*Reconnect / Lifecycle*) are checked for the new mode, not
   just extended.
5. **README gets one example and one link, nothing more.**
   README is a pitch: `ryll --rdp --file connection.vv` under *Usage*,
   with a line saying it needs a build with the `rdp` feature,
   and the guide under *Documentation*. No feature bullets.
6. **Clients: say what was tested, and nothing more.** The
   guide lists FreeRDP 3.15.0 as tested, with the flags that
   matter (`/cert:tofu`, `/dynamic-resolution`, `/sound`,
   `/sec:nla`), and lists mstsc and the Microsoft Remote
   Desktop / Windows App clients as expected to work and
   untested. The crate README is reworded the same way. If
   Mikal can run another client at the back brief (Windows
   App on a Mac is the likeliest), the management session
   runs a short session with it and the guide records it as
   tested. The master plan's first success criterion is
   reworded to what can be verified, and the change is noted
   under the criteria rather than made silently.
7. **Open questions get the answers the code gives.** 5:
   answered as implemented (dropped with a warn-once; the
   paste translator stays Future work). 8: marked deferred to
   the lane's promotion, citing phase 5's criterion, rather
   than answered.

The decision a reviewer is likeliest to argue with is 3:
moving accurate text out of `docs/configuration.md`, which is
where some operators will look first. The alternative is the
same facts in two places, which is how they drift: phase 5
already had to correct `docs/session-lifecycle.md` and
`docs/configuration.md` separately for one behaviour change.
The flag table stays where people look for flags, and the
paragraph above it links to the guide.

## Step plan

Every step writes different files, but all run `pre-commit` in
the shared tree, so they run in order. Steps 2 and 3 link to
the guide's sections, so step 1 runs first; step 4 links to
everything, so it runs last. Each step is one commit, with the
subject given.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1 | high | sonnet | none | The operator guide, `docs/configuration.md`'s *RDP Mode* section cut to flags, and the crate README's client claim. Commit: "Add an operator guide for ryll --rdp." See brief 1. |
| 2 | high | opus | none | `docs/rdp-mode-internals.md`. Commit: "Document how ryll --rdp works inside." See brief 2. |
| 3 | high | opus | none | The RDP column and section in `docs/multi-mode-parity.md`. Commit: "Add an RDP column to the parity matrix." See brief 3. |
| 4 | medium | sonnet | none | `ARCHITECTURE.md`, `README.md`, `docs/index.md`, `docs/features.md`, and the master plan's answers, *Future work* and success criteria. Commit: "Index the RDP docs and close the open questions." See brief 4. |
| 5 | - | - | - | Management session: the definition of done, and the client check of decision 6 if Mikal has a client. No commit of its own unless it records a client test. |

Every brief also carries the implement-phase rules: leave the
work uncommitted and report which files belong to the step; use
the scratch directory the management session names; run
`pre-commit run --all-files` (after `git add -N` on new files);
no plan, phase or decision citations outside `docs/plans`;
report each claim it could not check against the code, and any
defect found, with file and line; report in under 400 words.

### Brief 1: the operator guide

Files: new `docs/rdp-frontend.md`; `docs/configuration.md`
(*RDP Mode*, `:76-129`); `shakenfist-spice-rdp/README.md`.

- Read `docs/web-frontend.md` for tone and outline, and
  decision 1 for the sections. Read `docs/configuration.md`'s
  *RDP Mode* section, `docs/session-lifecycle.md`'s *RDP mode*
  section, `docs/development.md`'s *Verifying `--rdp`* (`:587`
  on) and `docs/ci.md`'s *The RDP smoke lane*: most facts the
  guide needs are already written there, accurately.
- Check every behavioural claim against the code:
  `ryll/src/rdp.rs` (launch output, the reconnect supervisor,
  exit status), `ryll/src/config.rs` (the `--rdp-*` flags and
  their defaults, port 3389 falling back to an ephemeral
  port), `shakenfist-spice-rdp/src/server.rs` (takeover, the
  30 s handshake limit), `auth.rs` (TLS and NLA), `display.rs`
  (resize, the outage frame), `input.rs` (Unicode keys
  dropped), `clipboard.rs` (the 100 MiB limit), `audio.rs`
  (Opus, then PCM).
- Getting a binary (survey item 5): no release includes the
  mode yet. Give `make release-rdp` (it leaves the binary at
  `target/rdp-smoke/ryll`; check the Makefile) and the cargo
  route, and say a release build without the feature refuses
  `--rdp` with a clear message (quote it from the code).
- Clients per decision 6. Phase 2's measurements for the cost
  section are in the master plan's open question 7 answer;
  give them as measured, with the date, the build and the
  client.
- Service mode: what a systemd unit needs (the password: a
  generated one is printed at each launch, so a service wants
  `--rdp-password` from an `EnvironmentFile` and the
  process-list caveat the flag's description gives; TLS
  certificate files and their permissions; Ctrl+C/SIGINT is
  the clean stop, so check what SIGTERM does in
  `ryll/src/main.rs` before writing a unit's `KillSignal`).
- Security note: the default bind is loopback; what exposing
  it means; one unauthenticated client can hold the
  authentication slot for up to 30 s at a time.
- `docs/configuration.md`: per decision 3. Keep every flag row;
  cut the prose to a paragraph that links the guide.
- Crate README: reword the client sentence per decision 6.

### Brief 2: the internals page

File: new `docs/rdp-mode-internals.md`.

- Read `docs/web-mode-internals.md`'s first screen for
  purpose, then the code: `shakenfist-spice-rdp/src/` (`lib.rs`
  for `SessionHandles` and the public surface, `server.rs`,
  `display.rs`, `input.rs`, `clipboard.rs`, `audio.rs`,
  `auth.rs`, `cursor.rs` or wherever the cursor relay lives)
  and `ryll/src/rdp.rs`.
- One section per module or concern, each saying what it
  does, what it hands to whom, and the invariant a change must
  keep. The invariants worth naming include: IronRDP runs on a
  thread of its own with a `LocalSet`, one `RdpServer` per
  accepted connection; takeover happens on the first
  `updates()` call after authentication and only between
  connections to the same session; a connection's slot is
  cleared on takeover and its held keys released; the display
  handler's future must lose nothing when dropped at an await
  (the comment in `display.rs` explains why); the outage frame
  is read from the mirror without changing it; the supervisor
  gives each SPICE attempt its own channels and relays the
  fixed ones; the mirror is cleared before a new session's
  events reach the bus. Check each against the code before
  writing it; drop any the code does not bear out.
- Name the IronRDP version the page was checked against, and
  say that the crate pins it on purpose.
- A mermaid diagram of the data flow is welcome if it shows
  the real mechanism (SPICE session → event bus → mirror →
  display handler → IronRDP → client, and input back); the
  repository lints mermaid in pre-commit.
- Link to `docs/rdp-frontend.md` for operation and to
  `docs/session-lifecycle.md` for reconnect behaviour rather
  than repeating them.

### Brief 3: the parity column

File: `docs/multi-mode-parity.md`.

- Read the whole page, decision 4, and `docs/rdp-frontend.md`
  (step 1). Add an *RDP* column to every row of the matrix,
  and an *RDP frontend (--rdp mode)* section of RDP-only rows
  (security modes, generated credentials, certificate
  fingerprint, takeover, the outage frame, SPICE
  auto-reconnect) with all four columns filled.
- Judge every cell from the code: `ryll/src/rdp.rs` is
  `run_rdp`, the RDP equivalent of `run_web` and
  `run_headless`; compare what it wires up with what they do
  (control socket, capture, pedantic, notifications, USB,
  WebDAV, paste, cadence). For a `partial` or `missing`
  verdict give the source reference, as the page's protocol
  asks.
- Update the opening "three delivery modes" line, the *four
  modes* table's RDP row (drop "the column lands with the
  operator guide"), and the maintenance paragraph's audit
  record with today's date.
- Write a check and run it: every row in the matrix has five
  cells, and every RDP cell starts with `available`,
  `partial`, `missing` or `n/a — intrinsic`. Put the script in
  the report.

### Brief 4: index, architecture, README, master plan

Files: `ARCHITECTURE.md`, `README.md`, `docs/index.md`,
`docs/features.md`, `docs/plans/PLAN-rdp-frontend.md`.

- `ARCHITECTURE.md` per survey item 3: seven crates with a
  `shakenfist-spice-rdp` row; an RDP row in the mode table;
  "All four modes ship"; *Where the detail lives* rows for
  `docs/rdp-frontend.md` and `docs/rdp-mode-internals.md`;
  "four modes" in the parity row; a paragraph in *Crate
  boundaries* on why the RDP crate is separate and
  feature-gated (IronRDP brings `sspi` and `picky`; read
  `ryll/Cargo.toml:58-62` and `docs/development.md:127`).
  Keep it a map; detail stays in the linked pages. Check the
  *Code organisation* section for `ryll/src/rdp.rs` and add it
  if missing.
- `README.md` per decision 5. `docs/index.md`: guide and
  internals in the *Documentation Index*. `docs/features.md`:
  an *RDP mode* bullet beside *Web mode*, one sentence and a
  link.
- Master plan: open question 5's answer and question 8's
  status per decision 7; phase 5's deferred items (survey item
  8) added to *Future work* under *Other extensions*, each in
  one or two lines; a *What shipped* note in the phase 6
  section; and under *Success criteria*, a line per criterion
  saying whether it holds today and how that was checked, with
  the first criterion reworded per decision 6. Leave the
  Execution table alone; the management session closes the
  phase out.

## Step 5: the management session's checks

- Run every definition-of-done item below and record the
  results in *Results*.
- If Mikal has another client (decision 6), run a session with
  it against the desktop guest (`make test-qemu-desktop` with
  its own SPICE port and PID, QMP and OVMF variables paths,
  never port 5900): connect, type, move the pointer, resize,
  copy text both ways, play audio, and reconnect. Record what
  worked, update the guide's client list, and commit that as
  "Record testing ryll --rdp with <client>."
- File an issue for every defect a step reported.

## Risks and mitigations

- **A doc states something the code does not do.** The briefs
  name the files to check each claim against, and ask for a
  list of claims that could not be checked. The management
  session re-checks a sample of each step's claims against the
  code, weighted to anything a user would act on: exit
  statuses, flags and defaults, what is dropped.
- **The parity column is filled by analogy with the Web
  column.** Brief 3 asks for each cell to be judged from
  `run_rdp`, with source references for every gap. The
  management session reviews the column at high effort and
  checks every `available` that has no counterpart in
  `run_rdp`.
- **The same fact ends up on two pages again.** Decision 3
  moves rather than copies; the definition of done greps for
  the moved sentences.
- **Docs promise a future.** No "will be released" or "coming
  soon": the guide describes what is true on `develop`.

## Definition of done

- [x] `docs/rdp-frontend.md` and `docs/rdp-mode-internals.md`
      exist, and `git grep -n 'operator guide is to come\|the
      column lands with'` finds nothing.
- [x] Every row of the parity matrix has five cells and every
      RDP cell starts with an allowed value (step 3's script,
      re-run by the management session).
- [x] `git grep -n -i 'three modes\|three delivery modes\|all
      three modes' -- ':!docs/plans'` finds nothing.
- [x] `ARCHITECTURE.md` says seven crates and names
      `shakenfist-spice-rdp`, and its *Where the detail lives*
      table links both new pages.
- [x] `README.md` links `docs/rdp-frontend.md` and has one
      `--rdp` usage example; `docs/index.md` links both new
      pages.
- [x] Every `--rdp-*` flag in `ryll/src/config.rs` appears in
      `docs/configuration.md`'s flag table, and none of the
      behaviour paragraphs moved out of it is still there
      (a grep for "half brightness" and "100 MiB" finds them in
      the guide and not in `docs/configuration.md`).
- [x] Every relative link in the changed docs resolves (a
      script over the changed files, run by the management
      session).
- [x] The guide names FreeRDP 3.15.0 as tested and every other
      client as untested, unless step 5 tested one; the crate
      README makes no untested claim.
- [x] Open question 5 has an answer and open question 8 a
      status; the master plan's *Future work* holds every item
      from phase 5's.
- [x] `pre-commit run --all-files` passes.
- [x] `git grep -n -i 'phase [0-9]\|decision [0-9]\|survey
      item' -- docs ARCHITECTURE.md README.md
      shakenfist-spice-rdp ':!docs/plans'` finds nothing new.

## Results

**Definition of done, 2026-10-27 (AEDT), at c72a727.** Every
item passed, checked as follows:

- The guide and internals page exist; the two removed phrases
  are found only in this plan.
- Step 3's check script: 118 feature rows, five cells each,
  RDP verdicts 61 available, 13 partial, 15 missing, 29 n/a.
- No "three modes" wording outside `docs/plans/`.
- `ARCHITECTURE.md` says 7 crates, has a `shakenfist-spice-rdp`
  row, and links both new pages; `README.md` links the guide
  and has one `ryll --rdp --file` example; `docs/index.md`
  links both pages.
- All eight `--rdp*` flags are in `docs/configuration.md`'s
  table; "half brightness" and "100 MiB" are in the guide and
  not in `docs/configuration.md`.
- A link checker over the twelve changed markdown files found
  every relative link and anchor resolving.
- The guide lists FreeRDP 3.15.0 as the only tested client; the
  crate README says other clients are untried.
- Open question 5 has an answer and question 8 a deferred
  status; all seven of phase 5's *Future work* items are in the
  master plan's.
- `pre-commit run --all-files` passes; the citation grep finds
  nothing.

**No client session.** Mikal has no RDP client other than
FreeRDP, so decision 6's optional session did not run.

**Issues filed** for what the steps found and this phase left
alone, as it changes no code:
[#642](https://github.com/shakenfist/ryll/issues/642) (SIGTERM
not handled, docs say it is),
[#643](https://github.com/shakenfist/ryll/issues/643) (a
positional `.vv` in `docs/web-frontend.md`),
[#644](https://github.com/shakenfist/ryll/issues/644) (the
`--rdp` help text and code comments that overclaim),
[#645](https://github.com/shakenfist/ryll/issues/645) (dropped
input counts never reported) and
[#646](https://github.com/shakenfist/ryll/issues/646) (stale Web
and Headless parity cells). The first review of the pull request
added [#648](https://github.com/shakenfist/ryll/issues/648) (an
`--rdp-password-file` flag), and the second asked for step 3's
check to be committed: it is `tools/check-parity-matrix.py`, run
by pre-commit.

## Execution notes

- **Back brief, 2026-10-27 (AEDT).** Mikal confirmed decision 3
  (the behaviour prose moves to the guide), said FreeRDP is the
  only client available, so step 5 runs no client session, and
  gave permission to push and open the pull request once the
  definition of done passes.
- **Step 1** (Sonnet, high effort) wrote the guide with a
  service-mode section that sets `KillSignal=SIGINT`, because
  ryll handles only SIGINT: `ctrlc` is built without its
  `termination` feature (`ryll/Cargo.toml:167`,
  `ryll/src/main.rs:113`), so SIGTERM ends the process with no
  clean stop. It also found that ryll has no positional `.vv`
  argument (`ryll/src/config.rs:73` is `--file`), so the
  `ryll --web connection.vv` examples in `README.md` and
  `docs/web-frontend.md` fail to parse. Both predate this plan;
  docs that claim SIGTERM is handled are `docs/web-frontend.md`,
  `examples/ryll-web.service`, `docs/multi-mode-parity.md` and
  `docs/session-lifecycle.md`. Issues are filed in step 5; step
  4 writes the README's `--rdp` example with `--file`. The old
  `--rdp` flag row said the certificate fingerprint is always
  printed; it is printed only for a generated certificate, and
  the rewritten row says so.
- **Step 2** (Opus, high effort) kept every invariant brief 2
  listed, qualifying two: the display handler's drop rule is
  IronRDP's contract even though the pinned release only drops
  the future at the end of a connection, and the mirror clear
  relies on the backoff outlasting the bus. The management
  session's review replaced the page's IronRDP version numbers
  with "the pinned release" and a pointer to the crate's
  `Cargo.toml`, because `AGENTS.md` forbids quoting dependency
  versions in `docs/`; this plan's Prompt and brief 2 were wrong
  to ask for the number. It also corrected the page's claim,
  repeated from a code comment at `ryll/src/rdp.rs:646`, that
  printing the launch lines with `println!` keeps the password
  out of journald: under systemd stdout is the journal, as the
  guide says. Defects the step found, for step 5's issues: that
  comment; a comment at `shakenfist-spice-rdp/src/display.rs:644`
  naming `poll_cursor`, which is `poll_session`; and dropped-input
  counts that are kept but never reported (`input.rs:203-212`,
  and `relay`'s return value in `ryll/src/rdp.rs`, discarded
  when the task is aborted).
- **Step 3** (Opus, high effort) filled 118 RDP cells (61
  available, 13 partial, 15 missing, 29 n/a — intrinsic) and an
  eleven-row *RDP frontend* section, with a check script that
  asserts five cells per row and an allowed verdict in every RDP
  cell. It found that `--usb-disk`, `--usb-disk-ro`,
  `--share-dir` and `--share-dir-ro` reach the session in `--rdp`
  as in headless (`run_rdp` passes them to every attempt), which
  contradicted the guide's *What is lost*, the configuration
  paragraph, the crate README and the internals page, all of
  which said the mode loses them. The management session
  confirmed it in the code and reworded all five to say the RDP
  client cannot pass its own devices or folders while ryll's own
  flags still work, untested through `--rdp`; the `--rdp` help
  text in `ryll/src/config.rs:285` says the same wrong thing and
  goes in step 5's issues. The step also reported Web and
  Headless cells it believes are stale (USB, WebDAV, snapshots,
  traffic ring buffer, PING samples and `--intimate` in Web; the
  headless reconnect cell called intrinsic), left for an issue
  rather than changed here.
- **Step 4** (Sonnet, medium effort) also fixed the positional
  `.vv` example in `docs/features.md`, a third instance beside the
  README's, which it was asked to fix; `docs/web-frontend.md`'s
  stays for the issue. The management session's brief asked it to
  remove a duplicated *Success criteria* heading that does not
  exist: the duplicate was an artefact of two overlapping extracts
  in the survey. The review rewrapped a paragraph in
  `ARCHITECTURE.md` and cut "mstsc" from its mode table's client
  column, since mstsc is untested. The step noted that
  `shakenfist-spice-rdp/src/lib.rs`'s module docs still name
  mstsc and the platform apps as served clients; that is a doc
  comment in code, so it joins step 5's issue about the RDP help
  text.

## Future work

None yet.

## Back brief

Before step 1, confirm with Mikal:

- Decision 3, moving the RDP behaviour prose out of
  `docs/configuration.md` into the guide.
- Decision 6: whether he has an RDP client other than FreeRDP
  to test with (Windows App on a Mac, mstsc on any Windows
  machine), and if so, whether to run step 5's client session.
- Whether this session may push and open the pull request once
  the definition of done passes.
