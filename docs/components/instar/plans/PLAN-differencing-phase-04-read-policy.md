# PLAN: Differencing phase 4 — read-side policy

Phase 4 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/).

## Goal

Stop instar reading a differencing image as though it had no
parent. Every operation that composes data from a differencing VHD
or VHDX refuses it, by name, with an exit code that says so —
replacing today's silent wrong answer on VHD and today's
undiagnosed generic failure on VHDX.

This phase refuses. It does not compose: composition is phases 11
to 16, and phase 14 replaces each refusal added here with a real
read. The refusal is an interim state inside this plan, settled by
open question 1, and it is worth its own phase because it is the
only part of the read-side answer that has to be true before the
emitters in phases 5 and 6 ship.

Closes #547 and #548.

## Planning effort

High, as the master plan requires for this phase. The judgement is
not in any individual refusal — it is in choosing where the
refusal lives, and the survey below moved that answer twice.

## Review effort

High, concentrated on one question: **is there a read path that
still reaches sector composition on a differencing image?** A
refusal that covers seven callers out of eight is not a partial
fix, it is the original defect with a smaller footprint. The
management session should enumerate the entry points from the
source rather than from this plan's list, because this plan's list
is exactly the thing that would be wrong.

## Scope

In scope:

* A typed refusal reason, carried from the guest to the host and
  rendered as a message that names the format and the reason.
* Refusal at every read entry point that can reach sector
  composition on a differencing VHD (`disk_type == 4`) or a
  differencing VHDX (`HasParent` set), for `convert`, `compare`,
  `bench`, `check`, `measure` and `dd`.
* Making the VHDX path diagnosable rather than merely non-zero,
  which is what #548 is about.
* `instar info` reporting a parent it can now see, which phase 3
  taught the crates to read.
* Integration tests over the phase 2 fixtures asserting the
  refusal per operation.

Out of scope, and deliberately:

* **Composition.** No op reads through to a parent in this phase.
* **`map` and `resize`**, which already refuse — see the survey.
* **The emitters.** Nothing here writes a differencing image.
* **Host-side chain discovery.** `discover_backing_chain`
  (`src/vmm/src/main.rs:2416`) is phase 11's business; this phase
  does not call it, extend it, or configure it.
* **Changing what `crates/vhd` and `crates/vhdx` parse.** Phase 3
  settled that surface and this phase consumes it unchanged, with
  the single exception recorded in decision 3.

## What the survey found

The master plan's phase 4 material was written 2026-09-05, before
phase 3 executed. Most of it holds. Four things do not, and two of
them change the shape of the work.

**Confirmed unchanged:**

* `VhdState::init` still accepts `DISK_TYPE_DIFFERENCING`
  alongside `DISK_TYPE_DYNAMIC` (`src/crates/vhd/src/lib.rs:1346`).
  The silent misread is live.
* `VhdxState::init` still rejects `has_parent` by returning bare
  `None` (`src/crates/vhdx/src/lib.rs:1647`). Phase 3 added parent
  locator parsing without disturbing it.
* `discover_backing_chain` is at `src/vmm/src/main.rs:2416`, and
  `backing_path_allowlist` / `max_chain_depth` at
  `src/vmm/src/config.rs:65` and `:67`, exactly as claimed.
* Issues #547 and #548 are open.

**Stale claim 1 — `map` is done, and is the template.** The
master plan's success criteria list `map` among the ops phase 4
must fix. `map` has refused a differencing VHD since commit
`eb6e23f` (2026-06-03), well before this plan was written:
`src/operations/map/src/main.rs:462` returns
`MapResult::ERROR_HAS_BACKING`, which the host renders at
`src/vmm/src/main.rs:15053` as a sentence naming the reason and
pointing at the deferral. That is precisely the shape this phase
generalises, so `map` moves from *work* to *precedent*.
`resize` likewise already refuses, at
`src/operations/resize/src/main.rs:629`, with
`ResizeResult::ERROR_UNSUPPORTED_SUBFORMAT`.

**Stale claim 2 — `dd` is not an operation.** The master plan
lists `dd` as one of eight ops. There is no `src/operations/dd`.
`run_dd` (`src/vmm/src/main.rs:13958`) builds a convert execution
and calls `execute_convert`, so `dd` shares convert's guest binary
and inherits whatever convert does. It needs a test, not a fix.

**Structural finding 3 — there is one read entry point, not
eight.** This is the finding that reshaped the phase. `convert`,
`compare`, `bench` and `check` never call `VhdState::init` or
`VhdxState::init`; greping the operations for either name returns
nothing outside `map` and `measure`. They reach a VHD or VHDX
source through the generic chain-state initialiser in the qcow2
crate, which owns `vhd_states` and `vhdx_states` arrays
(`src/crates/qcow2/src/lib.rs:9385` and `:9387`) and dispatches on
`ImageFormat` at `:9478` and `:9494` under the `vhd-input` and
`vhdx-input` features. Only three real `VhdState::init` call sites
exist in the tree: that initialiser, `measure`
(`src/operations/measure/src/main.rs:388`) and `map` (`:438`).

So the refusal has two homes — the shared initialiser and
`measure` — rather than six. A per-op refusal would have been five
copies of one check, and would have been the wrong answer for the
same reason it is wrong to fix a caller five times instead of
fixing the callee once.

**Structural finding 4 — `info` is categorically different.**
`info` does not link the vhd crate at all (`src/operations/info/Cargo.toml`)
and parses the footer itself via `parse_vhd_footer`
(`src/operations/info/src/main.rs:434`). It therefore cannot
inherit any refusal added above, and it should not: `info` reports
metadata, it never composes sector data, so it has no wrong answer
to give. Its defect is an omission — it reports no parent for an
image that has one — which phase 3 made fixable. See decision 4.

*Corrected in review:* the second sentence was read as licence to
keep parsing the VHD side locally, and that was wrong. Not linking
the crate meant not inheriting its validation either, which is how
`info` came to decode a parent name out of an unvalidated
`data_offset` (see "Found in review", item 2). `info` now links
`crates/vhd` for 704 bytes. The first sentence still stands: `info`
does not inherit the *refusal*, only the parsing.

**Mechanism finding 5 — the per-op result struct is *not* a
usable channel, and the right precedent is issue #375.** Both
`init` functions return `Option<Self>` and the chain initialiser
returns `bool`, so none of them can say *why* today; that is the
whole of #548. The obvious fix is a `u32` in the op's result
struct, the way `MapResult::ERROR_HAS_BACKING` (`:2924`) is
rendered by `map_error_message`
(`src/vmm/src/main.rs:15048`). **That does not generalise, and
this plan's first draft was wrong to assume it did.** Three of the
five operations have nowhere to put such a code:

* `convert` has no result struct at all. It reports through
  `send_complete("convert", 0, false)` and its module header
  states that no result message is needed. The host turns that
  into the bare string at `src/vmm/src/main.rs:13241`.
* `CompareResult` (`src/shared/src/lib.rs:2349`) carries a magic
  and flags, but no error constants.
* `CheckResult` (`:2069`) likewise has none.

Adding result structs to three operations to carry one boolean
fact would be a protocol change out of all proportion to the
phase.

The tree already solves this exact problem, for exactly this
reason, in issue #375: when the guest IDT catches a CPU fault, a
run loop that ends without a result must explain why instead of
printing "guest did not return a result". The mechanism is a
single capture in the message decoder — `last_cpu_exception` at
`src/vmm/src/main.rs:770`, set at `:833` when a `Payload::Error`
arrives whose `operation` field marks it — and a single formatter,
`no_result_error` at `:789`, that prefers the captured reason and
falls back to the generic text. `send_error(op, device, sector,
status)` is already on the call table
(`src/core/src/main.rs:430`), so every guest binary can raise it
today with no protocol change.

Phase 4 adds a sibling to that pair. This is op-agnostic, needs
one host-side capture point rather than five, and works for the
three operations that have no result struct.

**Naming hazard 6.** `src/vmm/src/main.rs:18659` and the
`MapRenderer` doc comment above it refer to "Phase 4". That is
**PLAN-map.md's** phase 4, not this one. An agent grepping for
phase 4 in the vmm will find map's streaming-renderer work and
should ignore it.

**Corrections made at source.** As part of the planning commit,
the master plan's phase 4 wording is corrected for `map` and `dd`,
and the phase 3 row's empty *Merged* column is filled in
(`42e879f`, #558). One phase 3 Definition-of-done item was also
false as merged — it asserted `git diff --name-only
develop...HEAD -- src/operations/` is empty, but the review round
that plumbed `metadata_length` into `parse_metadata` changed
`src/operations/check/src/main.rs`, a call-site-only edit with no
behaviour change. That bullet is annotated rather than deleted, so
the record shows what happened.

## Found during implementation, and left alone

Three things surfaced while implementing that are recorded rather
than fixed, so a later reader sees them as decisions:

1. **`resize` accepts a differencing VHDX.**
   `src/crates/resize/src/vhdx.rs:55` guards on `opts.has_parent`,
   but the caller hard-codes `has_parent: false`
   (`src/operations/resize/src/main.rs:839`), so the guard has never
   fired -- dead since `94d73b7` in May 2026. `instar resize` on a
   differencing VHDX therefore succeeds. This is a *write* path and
   predates the plan; `resize` never called `VhdxState::init`, so
   phase 4 neither caused it nor fixes it. It also corrects this
   plan's survey, which recorded "`resize` already refuses": that is
   true for VHD only. It is documented as a known limitation in
   `docs/resize.md` and `docs/quirks.md`, and filed by the operator
   as
   [issue #565](https://github.com/shakenfist/instar/issues/565)
   after the phase landed for review. Reproduced there against the
   `vhdx-diff-child` fixture: a 16 MiB child grows to 100 MiB while
   its parent stays 16 MiB, exit 0. Differencing VHD is refused
   (`error 6: subformat does not support resize`) and qemu-img 10.0.13
   refuses differencing VHDX outright, so instar-on-VHDX is the only
   accepting combination.

2. **`info` prints an unresolvable "actual path" for a VHDX
   parent.** The VHDX locator path is Windows-shaped
   (`.\vhdx-diff-parent.vhdx`), and the host renders it as a POSIX
   path at `src/vmm/src/main.rs:1499`, producing a filename
   containing a backslash that cannot exist. The output is honest
   about where instar would look, and qemu-img prints the same field
   unconditionally, but the underlying issue is path normalisation,
   which belongs to phase 11. Suppressing the display alone would
   paper over it. Phase 4e documents it as a known limitation.

3. **`map`'s VHDX arm had to change after all.** The step plan said
   to leave `map` alone on the strength of its VHD refusal at
   `src/operations/map/src/main.rs:462`. That was wrong: map's VHDX
   safety came entirely from the `VhdxState::init` rejection that
   decision 3 removes, as its own comment recorded. Left untouched,
   `map` would have begun emitting a differencing VHDX's parent
   blocks as holes. The refusal was added using map's existing
   `ERROR_HAS_BACKING`, so the precedent is preserved rather than
   migrated.

## Found in review

The automated reviewer on
[PR #563](https://github.com/shakenfist/instar/pull/563) raised twelve
items. Two were regressions this phase introduced, and are the reason
this section exists rather than a changelog line.

1. **`create -b <differencing VHDX>` started succeeding.** Removing
   `VhdxState::init`'s blanket `has_parent` rejection -- the change
   that lets the read entry points refuse with a reason instead of
   failing anonymously -- also removed the only guard on `create`'s
   `read_backing_virtual_size`, which *reads* a user-supplied image.
   An overlay whose base every read path refuses is a chain that can
   never be read back. Decision 3 anticipated this class of fallout
   and the definition of done enumerated `init`'s call sites, but
   classified `create` as "writes rather than reads" and stopped
   there, which was wrong about this call site.

   Fixed by refusing both formats in `read_backing_virtual_size`,
   with a new `CreateResult::ERROR_BACKING_DIFFERENCING` rather than
   the generic `ERROR_BACKING_PARSE_FAILED`: the backing header parses
   perfectly well, and telling a user a valid image is "truncated,
   corrupted, or an unrecognised format" is precisely the undiagnosed
   failure #548 was filed over. The VHD arm never had a guard at all
   and gains one here, so the two formats now agree.

2. **`info` decoded a parent name without validating the dynamic
   header.** `parse_vhd_parent_name` took `data_offset` from the
   footer and decoded 512 bytes at header offset 64 as UTF-16BE.
   Both `disk_type` and `data_offset` are image-controlled, so an
   image could point `data_offset` anywhere in itself and have
   arbitrary content printed as `backing file:` -- untrusted bytes
   promoted into a structured, user-facing field, with none of the
   validation `crates/vhd` applies to the same structure.

   Structural finding 4 and step 4c's brief are the root cause: they
   left `info` parsing the VHD side locally rather than linking
   `crates/vhd`, on the grounds that the VHD side was "small enough
   to parse locally". That reasoning was already weak once the same
   step added a `vhdx` dependency, and it produced six re-declared
   constants with no mechanism to catch drift -- `info` is a
   `no_main` guest binary that cannot run `cargo test`, so nothing
   could have failed if the crate's values moved. `info` now links `crates/vhd`, uses its
   constants, and gates on `VhdDynamicHeader::parse` -- the crate's
   own `cxsparse` cookie check. Measured cost: `info.bin` went from
   148,512 to 149,216 bytes -- 704 bytes, against a 768 KB ceiling
   the binary uses 18% of. The size argument decision 4 rested on did
   not survive being measured.

   Verified by negative control: with the cookie check compiled out,
   an image carrying UTF-16BE text at a bogus `data_offset` reports
   `backing file: PWNED-SECRET.vhd`; with it in, nothing. The test
   builds that image rather than shipping it as a fixture.

Two further defects were found while writing the tests for those, and
neither was in the review:

3. **`backing-filename-format` claimed a differencing VHD's parent
   was a qcow2.** The field defaults to `"qcow2"` when no backing
   format is recorded, which is right for a qcow2 v2 image with no
   header extension. A differencing image records no such extension
   either, so reporting a parent at all -- new in this phase -- put a
   false claim in a machine-read field. SPEC(VHD) and SPEC(VHDX) both
   require a parent to be the same format as its child, so the format
   is known without a header extension; `vpc` / `vhdx` are now
   reported.

4. **A malformed differencing VHD fails generically, and that is
   correct.** The refusal in `init_chain_states` reads a `VhdState`,
   and `VhdState::init` cannot build one without a valid `cxsparse`
   header -- so an image with a bogus `data_offset` fails as
   malformed before its disk type is consulted. This was an
   assumption in a test I wrote, not in the code, and it is recorded
   here because the test now asserts the real behaviour and says why.
   The property #547 was filed over still holds: no content is
   written and the exit code is non-zero.

The remaining ten items were documentation and test-coverage gaps:
plan phase numbers in the user-facing refusal string and in six
documents (AGENTS.md keeps phase numbers inside `docs/plans/`, and the
string reaches a user of an installed .deb who has neither the plan nor
its numbering); `refuse_differencing` inserted between `detect_and_scan`'s
doc comment and `detect_and_scan`, orphaning an `unsafe fn`'s safety
contract; `check_vhd` computing corruption findings past a refusal that
suppresses them; the six adversarial parent-locator fixtures and
`vhd-diff-child-mixed` untested; per-operation docs unwritten and
`docs/check.md` still claiming differencing VHDs are validated;
`check --output json` emitting no JSON on a refusal; `subTest` missing
from every fixture loop; and a quirks.md heading still describing the
pre-fix behaviour the body now contradicts.

## Decisions

1. **Refuse at the shared chain initialiser and at `measure`, not
   in `VhdState::init`.** Making the crate's `init` reject
   `disk_type == 4` would close the hole in one line for every
   caller at once, and it is the obvious move. It is wrong here
   for three reasons: phase 3's parsing surface exists to be read
   *from* a successfully initialised differencing image, and `map`
   already depends on `init` succeeding so it can read
   `state.disk_type`; phases 11 to 16 need `init` to succeed in
   order to compose; and a crate-level `None` produces exactly the
   undiagnosed failure that #548 exists to complain about. The
   callee is not wrong — the callers are missing a policy check.

2. **One guest-side refusal signal, captured once on the host,
   following issue #375 — not a per-op result code.** The guest
   raises `send_error` with a reserved operation marker and a
   status naming the format; the host decoder captures it beside
   `last_cpu_exception`, and the failure paths render it in place
   of their generic text. Survey finding 5 records why the
   per-op-result-code shape, which this plan proposed in its first
   draft, cannot work: `convert` has no result struct and
   `CompareResult` and `CheckResult` have no error constants, so
   three of the five operations have nowhere to put a code. The
   message still names the operation, because the formatter takes
   the op name as an argument exactly as `no_result_error` does.
   `map` keeps its existing `ERROR_HAS_BACKING` and is not
   migrated: it works, it is already covered by a test, and
   changing it would put a working refusal at risk for
   tidiness.

3. **Make VHDX symmetric with VHD: `VhdxState::init` stops
   rejecting `has_parent`, and the entry points refuse instead.**
   This is the decision most likely to be argued with, because it
   deliberately removes a working safety net. Today a differencing
   VHDX fails closed with a useless message; the alternative of
   keeping the rejection and threading a reason out of `init`
   (changing `Option<Self>` to `Result<Self, Reason>` across
   twelve call sites) preserves fail-closed but leaves VHD and
   VHDX structurally different for phases 11 to 16 to reconcile
   later. Symmetry is worth more: after this change both formats
   initialise, both expose a parent flag (`has_parent` is already
   `pub` on the metadata at `src/crates/vhdx/src/lib.rs:935`), and
   one uniform check at each entry point covers both.

   **The hazard is real and the mitigation is structural**: the
   commit that removes the rejection must be the same commit that
   adds every entry-point refusal. Split across two commits, the
   tree passes through a state where a differencing VHDX is
   silently misread — converting a safe failure into the exact
   defect this phase exists to close. Step 4b is therefore
   indivisible, and the review checks that first.

4. **`info` reports; it does not refuse.** `info` composes
   nothing, so it has no wrong answer to give, and refusing would
   remove the only way to inspect an image the rest of the tool
   declines to read — which is precisely when a user needs `info`
   most. It gains a parent line instead. Note this changes `info`
   output for differencing images, so phase 3's parity script
   (`tools/verify-info-output-parity.sh`) will report differences
   on exactly the differencing fixtures and must be run with that
   expectation stated, not as a pass/fail gate.

5. **`dd` gets a test, not a fix.** It shares convert's guest
   binary. The test exists to catch a future divergence, since
   nothing in the tree records that dd and convert must stay
   linked.

6. **The refusal message points at the deferral.** Following
   map's text, each message says composition is deferred and names
   the plan, so a user who hits it learns it is a known boundary
   rather than a corrupt image.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 4a | medium | sonnet | none | Build the refusal channel, following issue #375's `last_cpu_exception` pair exactly. (i) In `src/shared/src/lib.rs` add a small module of stable constants: a reserved `send_error` operation marker (a short string such as `differencing`) and one `u32` status per format (VHD, VHDX), documented as append-only the way `BenchResult`'s error codes are at `:4232`. `src/shared` is `no_std`. (ii) In `src/vmm/src/main.rs` add a `last_differencing_refusal: Option<u32>` field beside `last_cpu_exception` (`:770`, initialised `:781`), capture it in `add_byte` beside the existing capture (`:829-835`) when the `Payload::Error`'s `operation` equals the marker, and add a formatter beside `no_result_error` (`:789`) that takes the op name and renders a sentence naming the operation, saying the source is a differencing image whose parent instar cannot yet compose, and saying composition is deferred to PLAN-differencing phases 11-16. Follow the wording of `map_error_message` (`:15053`). (iii) Unit-test the formatter both ways, mirroring the two tests at `:863` and `:872`. Nothing raises the error yet — this step adds no behaviour. |
| 4b | high | opus | worktree | **Indivisible — one commit.** (i) Remove the `has_parent` rejection at `src/crates/vhdx/src/lib.rs:1647` and expose the flag on `VhdxState` so callers can test it (`has_parent` is already `pub` on the metadata at `:935`). (ii) In the generic chain-state initialiser `src/crates/qcow2/src/lib.rs:9450-9520`, refuse a differencing source in both the `ImageFormat::Vhd` arm (`:9478`, test `state.disk_type == vhd::DISK_TYPE_DIFFERENCING`) and the `ImageFormat::Vhdx` arm (`:9494`, test the new flag), raising the refusal from *inside* the function rather than threading it out: `init_chain_states` already takes `call_table: &CallTable`, and `send_error` is a field on it (`src/shared/src/lib.rs:914`), so the arm can raise 4a's signal itself and return `false` unchanged. This needs **no signature change and no caller change** — the five real callers (`convert:300` and `:1020`, `compare:211`, `bench:1395`, `rebase:1201`) stay as they are, and the crate already calls the table elsewhere (`call_table.debug_print`). Note `send_error` takes NUL-terminated `*const u8`, while 4a's `OPERATION` is a `&'static str` for the host's comparison, so add a NUL-terminated companion constant beside it in `src/shared/src/lib.rs` with a test asserting the two agree, rather than writing a bare literal at the call site. (iii) Do the same at `measure`'s two direct call sites (`src/operations/measure/src/main.rs:388` and `:400`). (iv) At each refusal, call the call table's `send_error` with 4a's marker and the format's status before returning failure, so the host renders the specific message; `map`'s refusal at `src/operations/map/src/main.rs:459-470` shows the return shape, but use the 4a channel rather than a result code — `convert`, `compare` and `check` have nowhere to put one. (v) Wire the host's generic failure sites to the 4a formatter, at minimum `src/vmm/src/main.rs:13241` (convert) and `:12213` (compare). Constraints: both crates are `no_std`, panic-free, no allocator; the arms are behind the `vhd-input` and `vhdx-input` features, so check both feature combinations build. Do **not** touch `VhdState::init`. |
| 4c | medium | sonnet | none | Teach `info` to report the parent. `info` parses the footer itself (`parse_vhd_footer`, `src/operations/info/src/main.rs:434`) and does not link the vhd crate — decide with the management session whether to add the dependency or extend the local parser, and state the choice. Report the parent name for a differencing VHD and the parent locator's linkage for a differencing VHDX, in both human and `--output json` forms, following how qcow2's backing file is already reported. Refuse nothing. |
| 4d | high | opus | worktree | Integration tests over the phase 2 fixtures. For each of convert, compare, bench, check, measure and dd, assert a non-zero exit and the expected message on `vhd-differencing`, `vhd-diff-child-aligned` and `vhdx-diff-child`; assert `map` still refuses (regression guard on the precedent) and that `info` now reports a parent. Assert dd and convert produce the same refusal, which is the only thing recording that they share a binary. Use the existing integration harness rather than a new one. The composed goldens (`vhd-diff-aligned-composed.raw`, `vhdx-diff-composed.raw`) are phase 11-16 material — do not use them here. |
| 4e | medium | sonnet | none | Documentation and closeout. Update `docs/format-coverage.md` (divergence notes), `docs/quirks.md` and `CHANGELOG.md` to state that differencing VHD and VHDX are refused on read with composition deferred. Close #547 and #548 with a comment naming the commit and the message a user now sees. Do not touch `docs/create.md` or the emitter docs — phases 5, 6 and 10 own those. |

Steps 4a and 4c are independent of each other. 4b depends on 4a.
4d depends on 4b and 4c. 4e last.

## Risks and mitigations

* **A read path is missed, and one op still composes silently.**
  The likeliest failure of this phase, and the reason the entry
  points were enumerated from the source rather than from the
  master plan's op list. *Mitigation:* the management session
  re-derives the list of `VhdState::init` / `VhdxState::init`
  callers and `ImageFormat::Vhd|Vhdx` dispatch sites from the tree
  at review time and compares it against what 4b changed; 4d
  covers every op by name including dd.
* **The 4b window.** Removing the VHDX rejection before the
  refusals land makes the tree briefly worse than it is today.
  *Mitigation:* 4b is one commit, stated in the brief and checked
  first at review.
* **Feature-gate blindness.** The chain initialiser's arms are
  behind `vhd-input` and `vhdx-input`. A refusal added inside a
  gate that some op does not enable protects nothing. *Mitigation:*
  4b's brief requires building both feature combinations; the
  review greps which ops enable which features.
* **`info` parity churn.** Decision 4 deliberately changes `info`
  output for differencing images, which phase 3's parity script
  will report. *Mitigation:* stated in decision 4 and in the DoD;
  the run is read for *which* images changed, and any change
  outside the differencing fixtures is a defect.
* **Guest binary size.** Every guest binary has a 768KB cap and
  `make check-binary-sizes` enforces it. The additions are small,
  but `check` and `convert` are the largest binaries.
  *Mitigation:* in the DoD.

## Definition of done

Checked by step 4e (documentation and closeout) against the commits
listed at the top of this plan's "Found during implementation" section
and against the tree at `HEAD`. Items are ticked where 4e could verify
them directly (by reading the code/tests or diffing commits); where 4e
relied on an earlier step's own record without re-running it, that is
said so explicitly rather than presented as independently checked.

- [x] `instar convert -O raw`, `instar dd`, `instar compare`,
  `instar bench`, `instar check` and `instar measure` each exit
  non-zero on `vhd-differencing`, `vhd-diff-child-aligned` and
  `vhdx-diff-child`, with a message naming the operation and the
  parent reference. Verified by the 4d tests
  (`tests/test_differencing.py`), not by hand. The message names the
  operation and the format (`VHD`/`VHDX`) and says "whose parent
  instar cannot yet compose"; it does not quote the parent's literal
  path (that would require resolving it, which phase 4 deliberately
  does not do — see decision 3's VHDX path-resolution note).
- [x] No operation writes output composed from a differencing source.
  Specifically, `instar convert -O raw` on `vhd-differencing`
  produces **no output file**, where before commit `10ab838` it
  produced a wrong one and exited 0. Verified by
  `TestDifferencingConvertLeavesNoOutput`.
- [x] No operation gained a result struct or a new protocol message:
  the refusal travels on the existing `send_error` channel, and
  `git diff 42e879f..HEAD` touches neither `crates/guest-protocol`
  nor any `*Result` struct definition in `src/shared/src/lib.rs`
  beyond the new `DifferencingRefusal` constants module. Verified by
  diffing the range directly.
- [x] `grep -rn 'VhdState::init\|VhdxState::init' --include=*.rs src/`
  and the `ImageFormat::Vhd`/`ImageFormat::Vhdx` dispatch arms in
  `src/crates/qcow2/src/lib.rs` together enumerate every read
  entry point, and each one either refuses a differencing source
  or is `create` (which writes) or a fuzz target. Checked by
  reading the code in step 4e: the real (non-comment, non-fuzz)
  call sites are `map` (both arms), `measure` (both arms),
  `init_chain_states` in `crates/qcow2` (both arms, serving
  `convert`/`compare`/`bench`/`rebase`), and `create`'s `VhdxState`
  use for output construction — every one of the first three refuses
  a differencing source, and `create` writes rather than reads.
- [x] `src/crates/vhdx/src/lib.rs` no longer rejects `has_parent` in
  `init`, and the commit that removed it is the same commit that
  added every entry-point refusal — verified with
  `git show --stat 10ab838`, which touches `crates/vhdx`,
  `crates/qcow2`, `operations/check`, `operations/map`,
  `operations/measure`, `shared` and `vmm` together.
- [x] `instar info` reports a parent for `vhd-diff-child-aligned` and
  `vhdx-diff-child` in both human and JSON output, and
  `tools/verify-info-output-parity.sh` reports differences on the
  differencing fixtures **and on no others**.

  **This bullet's own wording is imprecise, corrected here rather
  than silently fixed.** `vhd-differencing` — the example this bullet
  originally named — is a dynamic VHD patched to disk type 4 with an
  all-zero parent name, so `info` correctly reports **no** backing
  file for it (`test_info_reports_the_disk_type_4_fixture_without_a_parent`
  pins exactly this). The fixtures that actually have a resolvable
  parent for `info` to report are `vhd-diff-child-aligned` (parent
  `vhd-diff-parent.vhd`) and `vhdx-diff-child` (parent
  `.\vhdx-diff-parent.vhdx`), which is what the corrected wording
  above says. The parity-script claim itself is independently
  confirmed (from a `tools/verify-info-output-parity.sh` run against
  base `42e879f` and this phase's tree, captured during step 4d and
  read by step 4e rather than re-run): `Compared: 208, Failed: 9`,
  with the nine named exactly as the differencing children and the
  locator-audit fixtures (`vhd-diff-child-aligned`,
  `vhd-diff-child-mixed`, `vhdx-diff-child`,
  `vhd-diff-locator-etc-passwd`, `vhd-diff-locator-dotdot`,
  `vhd-diff-locator-unc`, `vhd-diff-locator-url`,
  `vhd-diff-locator-overlong`, `vhd-diff-locator-conflicting`) and no
  others, matching commit `c561aad`'s own record exactly.
- [x] `instar map` still refuses `vhd-differencing` with its existing
  message, unchanged. Verified by reading
  `src/operations/map/src/main.rs:459-470` (unchanged VHD arm) and by
  `TestDifferencingMapStillRefuses`.
- [ ] Issues #547 and #548 are closed, each with a comment quoting the
  message a user now sees. **Deliberately not done by step 4e.** Per
  this step's brief, closing issues and posting GitHub comments is
  left to the operator: the closing comments are drafted to the
  session scratchpad (not committed to the repository) for the
  operator to review and post by hand.
- [x] `VhdState::init` is byte-for-byte unchanged. Verified with
  `git diff 42e879f..HEAD -- src/crates/vhd/src/lib.rs`, which is
  empty.
- [x] `make instar` builds, `make check-binary-sizes` passes, and
  `make test-rust` passes. Confirmed from this session's own
  build/test run logs from step 4d (not re-run by 4e, which is
  docs-only): the release build succeeded (all binaries listed,
  including `check.bin`, `map.bin` and `measure.bin`),
  `check-binary-sizes` passed as part of the same pre-commit run
  noted below, and `make test-rust` reported 0 failures across every
  crate's unit-test binary.
- [x] `pre-commit run --all-files` passes. Confirmed twice: once
  from step 4d's own run log (every hook passed), and again by step
  4e itself against this documentation change (see the commit this
  plan update lands in).
- [x] `make test-integration` passes. Run by the management session
  against the tree with every step of this phase committed:
  `Ran: 3508 tests ... Passed: 2637, Skipped: 871, Failed: 0`, exit 0.
  An earlier run during this phase did record one failure, in
  `test_commit.py`, where a `qemu-img commit` subprocess timed out at
  60 seconds -- the signature of already-tracked issue #528, a
  load-sensitive flake unrelated to differencing. It did not
  reproduce in the clean run above, which is the evidence for calling
  it load and not a regression.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

In particular, back brief before starting **step 4b**: it removes
a working safety net and must land as a single commit, and the
choice of how to thread the refusal reason out of the chain
initialiser's `bool` return should be agreed before the editing
starts rather than discovered in review.
