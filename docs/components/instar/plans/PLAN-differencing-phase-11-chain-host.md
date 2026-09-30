# Differencing phase 11: host chain discovery and `info --chain`

## Prompt

Phase 11 of [PLAN-differencing.md](/components/instar/plans/PLAN-differencing/), the first of
the six composition phases. The master plan describes it as "resolving a
locator path to a real parent, applying the same resolution and depth
rules qcow2 backing chains get, and attaching each chain member as its
own virtio device", ending with "`info --chain` walking a VHD or VHDX
chain, which is the cheapest possible proof the host half works and
needs no guest change at all" (`PLAN-differencing.md:487-493`).

The survey below found that two of those three clauses describe code
that already exists, so this phase is materially smaller than the
master plan's section implies. What is left is one gate, one resolution
rule per format, and the output of one command.

## Planning effort

High. The master plan's per-phase effort list stops at phase 10
(`PLAN-differencing.md:603-608`) and says nothing about 11. High is the
right level anyway, and not because the diff is large: the phase turns
on a security-boundary question (what a host may resolve out of an
untrusted image) and on an invariant an earlier phase deliberately
established and this phase must not break. Both are decided below
rather than left to the implementer.

Review effort: high, concentrated on one question -- whether any
operation's refusal became contingent on a parent file existing. That
is the failure this phase is exposed to, and it is invisible in a green
test run unless the test was written to look for it. Phase 8 wrote one;
see `TestDifferencingParentAbsent` (`tests/test_differencing.py:609`).

## Scope

**In scope.**

* The VHD/VHDX gate in `discover_backing_chain`
  (`src/vmm/src/main.rs:2701-2707`) and the walk policy that replaces
  it.
* Resolving a VHD parent and a VHDX parent to a real file on the host,
  under the existing allowlist and depth rules.
* `instar info --chain` output for a walked chain, including its
  `--output json` form.
* `docs/chain-discovery.md` and `docs/info.md`, which state the
  one-image behaviour as current truth and carry a reason that is
  already false.
* Rust unit tests and Python integration tests for all of the above.

**Out of scope.**

* Any guest change. The guest chain machinery is qcow2-specific
  (`qcow2::ChainStates`, `qcow2::init_chain_states` at
  `src/crates/qcow2/src/lib.rs:9442`); giving VHD and VHDX a compose
  path is phases 12 and 13.
* Lifting any operation's refusal. Every composing operation keeps the
  gate and keeps refusing, byte for byte. That is phase 14, operation
  by operation.
* Parent identity verification -- see decision 5.
* The VHD parent locator table as a resolution source -- see decision
  3.
* The read-side rows of `docs/format-coverage.md` and the compose
  sections of `docs/chain-config.md`, which stay with phase 16.
* Six defects, none of them this phase's to fix. Two were found by
  this survey and filed during it: the silent chain-device truncation
  (#601) and the VHD locator-table resolution gap (#602). Two were
  already open and are recorded here because composition depends on
  them: #565, where `resize` grows a differencing VHDX without its
  parent, and #566, which makes parent identity vacuous for any chain
  instar wrote end to end -- see decision 5. Four more came out of the
  review rounds on the pull request. Round 1: #608, that the walk never
  checks a resolved parent is the same format as its child and that
  errors past the differencing link are not fail-soft, and #609, that
  image-derived path strings reach the terminal unescaped in the human
  output forms (the JSON forms escape them). Round 2: #611, that
  resolution probes an absolute reference before the allowlist is
  consulted, so the stderr reason distinguishes whether an
  attacker-chosen host path exists -- pre-existing for qcow2, but now
  reachable from a command that exits 0; and #612, that the file carries
  two JSON escape helpers of which only one escapes the C0 range.
  #608's and #611's boundaries are both documented in
  `docs/chain-discovery.md` rather than left implicit. #609 and #611 are
  each wider than this change: the older stdout printer carries the same
  strings, and `resolve_backing_path` is shared with qcow2 and VMDK.

## What the survey found

Measured against `036252f` (the phase 10 merge) on 2026-09-28, with a
binary built from that tree by `make instar`. Four of the findings below
are measured rather than read: F1, F4, F9 and the absent-parent baseline.
The commands and their exact output:

```
$ instar info --chain vhd-diff-child-aligned.vhd
Chain: 1 image(s)
  [0] .../vhd-diff-child-aligned.vhd (vpc) -> vhd-diff-parent.vhd
      virtual size: 16 MiB (16777216 bytes)
      disk size: 4 MiB (4198912 bytes)
      cluster size: 2097152 bytes
                                                            # rc 0

$ instar info --chain vhdx-diff-child.vhdx
Chain: 1 image(s)
  [0] .../vhdx-diff-child.vhdx (vhdx) -> vhdx-diff-parent.vhdx
      ...                                                   # rc 0
```

The VHDX line is F4 measured: the parent reads `vhdx-diff-parent.vhdx`,
not `.\vhdx-diff-parent.vhdx`, so the Windows-path rationale the gate
still gives is already spent. Renaming the parent out of the way changes
neither output nor exit status -- byte-identical, rc 0 -- which is the
absent-parent baseline the definition of done compares against. And
`instar info --chain --output json vhdx-diff-child.vhdx` prints exactly
the human text above and exits 0, which is F9.

**F1. `info --chain` already exists, and already reports the parent.**
`run_info` handles `args.chain` at `src/vmm/src/main.rs:10269-10282`:
it discovers the chain, calls `print_backing_chain` and returns before
any guest launches. `print_backing_chain` (`:2724`) already renders
`backing_file_raw` as `-> parent`, so a differencing child today prints
a one-image chain annotated with the parent it did not follow -- measured
above, for both formats. The flag,
the printer and the annotation are all built. **The master plan's phase
11 section implies this command is a deliverable of the phase; it is
not.** Corrected at source in this commit.

**F2. Chain-member device attachment is already format-agnostic.**
`open_chain_devices` (`:2831`), `open_chain_devices_rw` (`:2923`) and
`write_chain_device_entries` (`:2765`) iterate `chain.images()` and
attach each as a virtio device with no format dispatch whatsoever; the
per-device format code is just `image.format.to_shared_format_u32()`,
and codes 5 (`Vhd`) and 6 (`Vhdx`) are already allocated and documented
(`docs/chain-config.md:63-64`). **So the master plan's "attaching each
chain member as its own virtio device" is not phase 11 work either.**
Also corrected at source.

**F3. What is actually left is a seven-line gate.** At
`src/vmm/src/main.rs:2701-2707`:

```rust
if matches!(image_format, ImageFormat::Vhd | ImageFormat::Vhdx) {
    debug!(...);
    break;
}
```

Everything above it -- canonicalisation, circular-reference detection,
depth limiting, allowlist validation -- is format-agnostic and needs no
change. The master plan's claim that "phase 11 extends it rather than
writing one" (`PLAN-differencing.md:466`) is correct and holds.

**F4. Half of the gate's stated rationale is now false.** Its comment
(`:2678-2700`) gives two reasons. The first is that a VHDX parent
locator holds a Windows-style relative path (`.\parent.vhdx`) that does
not resolve on POSIX. That was true when the comment was written --
commit `10ab838d`, 2026-09-13 -- and stopped being true eight days
later: `7733730` ("Report a parent path the way the user typed it",
2026-09-21) added `vhdx::posix_relative_path`
(`src/crates/vhdx/src/lib.rs:984`), which the info operation applies
before reporting (`src/operations/info/src/main.rs:1243-1248`). A
relative VHDX locator therefore arrives at the host already POSIX-shaped
and resolvable. The claim survives only for `absolute_win32_path` and
`volume_path`, which are reported verbatim -- see decision 4. The same
false reason is copied into `docs/chain-discovery.md:200-202`.

**F5. The gate's second reason is sound, and is the constraint this
phase is built around.** From the same comment: "the outcome became
contingent on the parent's presence: the same differencing image gave
the typed refusal when its parent happened to sit beside it and a path
error when it did not. A refusal that depends on a file instar is not
going to read is not a refusal." That is correct, it is load-bearing,
and phase 8 wrote a test for it (`TestDifferencingParentAbsent`,
`tests/test_differencing.py:609`). Decision 1 answers it by
construction.

**F6. Only one of the ten call sites needs to walk.**
`discover_backing_chain` is called ten times: `run_bench` (`:4614`),
`run_rebase` (`:6471`, `:6483`), `run_commit` (`:6829`), `run_info`
(`:10274`), `run_check` (`:11212`), `run_compare` (`:11953`, `:11959`),
`execute_convert` (`:12792`) and `run_dd` (`:14218`). Nine of them go on
to attach devices and launch a guest. `run_info --chain` is the only one
that prints and returns. Note in particular that `run_check --chain`
is **not** display-only: it opens chain devices at `:11336` and writes
the chain config at `:11347`, so it composes and keeps the gate.

**F7. The default allowlist already refuses the adversarial fixtures.**
`get_backing_allowlist` (`src/vmm/src/config.rs:239`) defaults to
`MARKER_IMAGE_DIR` -- the image's own directory -- and
`get_max_chain_depth` (`:250`) defaults to 16. Every adversarial locator
fixture in `instar-testdata/custom/audit/` names something outside that
directory (`/etc/passwd`, `../../../etc/passwd`, a UNC path, a URL), so
the existing validation rejects all six without a line of new security
code. This is why decision 1 is safe, and the definition of done pins it.

**F8. VHD's parent reference is the unicode name, not a locator entry.**
`info`'s VHD arm reports the parent *unicode name* field
(`src/operations/info/src/main.rs:437-478`, via
`parse_vhd_parent_name`). The locator table is parsed by `crates/vhd`
(`VhdParentLocator` at `src/crates/vhd/src/lib.rs:552`,
`VhdParentLocatorTable` at `:823`) but never reaches the host: nothing
in `InfoResult` carries it. The master plan's phrase "resolving a
locator path to a real parent" is therefore imprecise for VHD --
decision 3.

**F9. `info --chain` silently ignores `--output json`.** `InfoArgs`
declares `--output` with a `["human", "json"]` value parser
(`src/vmm/src/main.rs:3617-3619`), but the `--chain` branch calls
`print_backing_chain` unconditionally and returns, so
`instar info --chain --output json` prints human text and exits 0. There
is no JSON chain printer anywhere (`grep` for one finds nothing), and the
measurement above confirms the flag is silently ignored rather than
rejected. Pre-existing, and qemu-img's
`info --backing-chain --output json` emits a JSON array. Decision 7
brings it into scope, with the argument against.

**F10. Chain devices are silently truncated, and at least two
operations can reach it.** `write_chain_config` caps `device_count` at
`MAX_CHAIN_DEVICES` and reports the truncation with `debug!` only
(`src/vmm/src/main.rs:3041-3050`), and both `write_chain_device_entries`
loops `break` silently (`:2776`, `:2802`). The default
`max_chain_depth` is 16 and `MAX_CHAIN_DEVICES` is 16, so a 16-image
chain whose top image has a QCOW2 external data file needs 17 slots and
loses its deepest parent -- reading as zeros rather than erroring. Of
the five `write_chain_config` callers, `run_check` (`:11347`) and
`run_bench_guest` (`:4897`) have no device-budget guard in their own
bodies, where `execute_convert` does (`:12809`). There is also a second
`MAX_CHAIN_DEVICES` defined locally in the VMM (`:331`) shadowing
`shared::MAX_CHAIN_DEVICES` (`src/shared/src/lib.rs:4725`); both are 16
today. **Pre-existing, affects qcow2 and VMDK now, and out of scope.**
File it (step 11f, #601) rather than fixing it here.

**F11. The tests that pin the current boundary already exist and say
so.** `test_info_chain_stops_at_a_vhd_parent`
(`tests/test_differencing.py:586`) asserts a one-image chain and its
docstring reads "phase 11 will change it and should have to change this
test to do so". `test_info_reports_the_locator_without_following_it`
(`:830`) asserts the same for the six adversarial fixtures, and must
keep passing. `docs/info.md:67-68` states the one-image behaviour in
prose.

Nothing else the master plan's phase 11 section claims was found to be
wrong: `discover_backing_chain` does carry circular-reference detection,
depth limiting and allowlist checking; `security.backing_path_allowlist`
and `security.max_chain_depth` are at `src/vmm/src/config.rs:65` and
`:67` as stated; the VMDK flat-descriptor short-circuit is real
(`:2546`); and both chain documentation pages exist at the line counts
phase 10 recorded (209 and 210). The only numeric drift is the function's
own line: the master plan says `src/vmm/src/main.rs:2501`, it is now
`:2519`. Corrected at source.

## Decisions

**1. A walk policy, not a lifted gate. Exactly one call site walks.**
`discover_backing_chain` gains an explicit policy argument, and VHD and
VHDX parents are walked only under the reporting policy. `run_info`'s
`--chain` branch passes it. The other nine call sites pass the composing
policy and keep behaviour that is bit-identical to today.

This is the decision that answers F5 by construction rather than by
care. The invariant phase 4 established -- a refusal must not depend on
whether the parent happens to exist -- cannot be broken by an operation
that never resolves the parent in the first place. The alternative
(walk everywhere, and make every composing operation tolerate an
unresolvable parent) puts the invariant at the mercy of ten error paths
staying correct, and phase 14 has to revisit each of them anyway as it
adds composition. Threading a policy is the smaller and more auditable
change, and phase 14's per-operation work becomes "flip this call site's
policy", which is exactly the granularity the master plan asked for
("phase 14 replaces phase 4's refusal op by op",
`PLAN-differencing.md:376`).

Shape it as a two-variant enum, not a bool: a bool at a call site reads
as `discover_backing_chain(path, size, &cfg, true)` and tells the reader
nothing. Name the variants for what the caller does with the result, not
for the formats affected, because phase 14 changes which formats are
affected and must not have to rename anything.

**2. Under the reporting policy, an unresolvable parent ends the walk
without erroring.** Whatever the reason -- the file is absent, the path
is outside the allowlist, the depth limit is reached, the path is a
Windows absolute path -- the walk stops at the last image it did
resolve, the unresolved reference stays in that image's
`backing_file_raw` so the annotation survives, and discovery returns
`Ok`. A single line naming the reason goes to stderr.

`info` reports and refuses nothing; that is decision 4 of phase 4 and
`docs/info.md` states it. Turning `instar info --chain` into a command
that exits non-zero because a parent is missing would contradict it, and
would be a worse regression than the gate it replaces. The reason goes
to stderr rather than being swallowed so a user with a broken chain
learns why, and so the integration tests can assert *which* reason
fired -- see the definition of done.

Note what this does **not** weaken: nothing opens a rejected path. The
allowlist check still runs and still rejects; the only change is that
its rejection ends a listing instead of failing a command.

**3. VHD resolves from the parent unicode name. The locator table is
not a resolution source in this phase.** F8 establishes that the
unicode name is the only parent reference that reaches the host today,
and widening `InfoResult` to carry eight locator entries is an ABI
change this phase does not otherwise need.

The stronger reason is oracles. libvhdi -- this plan's oracle, chosen in
phase 1 -- resolves VHD parents from the unicode name alone and **never
parses the VHD parent locator table**; `libvhdi_parent_locator*` is
reached only from the VHDX metadata path (master plan open question 2,
`PLAN-differencing.md:236-242`). qemu-img reads no differencing VHD at
all. So a locator-table resolution path would be the one part of this
feature with no reference implementation to disagree with us, in a plan
whose standing warning is that plausible-looking format claims here go
unchecked.

**The argument against, which I accept as real:** the locator table is
the VHD spec's designated mechanism and `W2ru` is what Windows actually
writes, so a third-party child with an empty unicode name and a valid
locator will resolve under Hyper-V and not under instar. That is a
genuine gap. It is recorded as future work with an issue (step 11f, #602)
rather than guessed at here, and the fixtures to settle it already exist
in `instar-testdata/custom/audit/`.

**4. A Windows absolute VHDX path ends the walk; it is never
rewritten.** `relative_path` values arrive POSIX-rendered (F4) and
resolve normally. `absolute_win32_path` (`C:\images\parent.vhdx`) and
`volume_path` (`\\?\Volume{GUID}\...`) are reported verbatim by design
and must stay that way: rewriting `C:\images\parent.vhdx` into
`/images/parent.vhdx` would invent a host path out of a drive letter and
hand it to an allowlist check, which is a path-traversal primitive
dressed as a convenience. So they terminate the walk under decision 2,
with a reason that says the path is a Windows absolute path rather than
that the file is missing.

**5. No parent identity verification in this phase.** VHD records the
parent's footer unique id in the child's dynamic header and VHDX records
the parent's DataWriteGuid in `parent_linkage`, so a host that walked a
chain could check it found the right parent. Three reasons not to, here:
the identity fields do not reach the host either (same ABI point as
decision 3); the check belongs where composition happens, because that
is where being wrong corrupts data rather than mislabelling a listing;
and #566 makes it **vacuous for any chain instar wrote end to end** --
every instar VHD carries an all-zero footer id and every instar VHDX
derives its DataWriteGuid from the sequence number, so any instar parent
satisfies any instar child. A verification step that passes for the
single most likely chain in the wild is worse than none, because it
reads as coverage.

Record the dependency loudly: phases 12 and 13 must not rely on parent
identity for correctness until #566 is fixed.

**6. No new security knobs, no new defaults.** The walk inherits
`security.backing_path_allowlist` and `security.max_chain_depth`
unchanged (F7). The image-directory default is already the right
behaviour for relative VHD and VHDX parents, which is the overwhelmingly
common shape, and it is what rejects all six adversarial fixtures. A
knob nobody needs is a knob nobody tests.

**7. `info --chain --output json` is fixed here.** F9 is pre-existing
and would normally be an issue rather than phase work. It is in scope
because this phase's entire user-visible deliverable is that command's
output, and shipping a walked chain that the documented JSON mode
silently cannot express would make the feature unusable from a script
while appearing to work -- the failure mode is a zero exit and wrong
text, which is the shape this plan exists to eliminate.

**This is the decision a reviewer is most likely to push back on**, and
the push-back is reasonable: it is scope the master plan did not ask
for, in a phase that is already about something else. Two mitigations.
It is a separate step (11e) with its own commit, so it can be dropped
without touching anything else. And the issue is filed either way, so
dropping it does not lose it. Match qemu-img's `--backing-chain
--output json` shape -- an array of objects -- rather than inventing one.

## Step plan

Each step is one commit. 11b depends on 11a; the rest are independent of
each other.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 11a | high | opus | none | Replace the VHD/VHDX gate with a walk policy. Add a two-variant enum (decision 1 -- name the variants for what the caller does with the chain, e.g. one for callers that go on to attach devices and launch a guest, one for callers that only report) and a parameter for it on `discover_backing_chain` (`src/vmm/src/main.rs:2519`). At `:2701-2707`, keep the existing `break` for the composing variant and fall through to `validate_backing_path` for the reporting variant. Update all ten call sites listed in F6: only `run_info`'s `--chain` branch (`:10274`) passes the reporting variant; the other nine pass composing, including `run_check` (`:11212`), which composes despite being a `--chain` flag -- it opens devices at `:11336` and writes the chain config at `:11347`. Rewrite the gate's comment: its first rationale is false since `7733730` (see F4) and it cites a plan phase, which this repo does not allow in landed code (issue #592, and `~/.claude/CLAUDE.md`) -- state the invariant instead of citing where it was decided. Do not implement decision 2's fail-soft behaviour yet; this step only moves the decision point, and `validate_backing_path` errors are still propagated. The tree must build and the whole Python suite must pass unchanged after this step, because no reachable behaviour has changed yet: the reporting variant's new path is only taken for VHD/VHDX, and `run_info` still errors on an unresolvable parent exactly as `run_check` would have. Note that `tests/test_differencing.py:586` will now fail for the three resolvable fixtures -- that is expected and 11d updates it; say so in the commit message rather than editing the test here. |
| 11b | high | opus | none | Make the reporting walk fail-soft, per decisions 2 and 4. In `discover_backing_chain`, under the reporting variant only, convert every `ChainError` arising from resolving a *VHD or VHDX* parent into a terminated walk that returns `Ok`: emit one `eprintln!` line naming the image, the unresolved reference and the reason, then `break`. The reasons to distinguish, because 11d asserts on them: file absent, outside the allowlist, depth limit reached, and a Windows absolute path. The last needs detecting on the host -- a value with a drive-letter prefix (`X:\`) or a `\\` prefix is `absolute_win32_path` or `volume_path` (F4, `src/operations/info/src/main.rs:1232-1249` explains which key produces which convention) and must be reported as such, never rewritten into a POSIX path (decision 4). Composing callers keep propagating every error unchanged -- this is the only thing standing between the tree and a contingent refusal, so keep the two paths textually obvious rather than clever. VHD resolves from the unicode name that `info` already reports in `backing_file`; there is no locator-table path to add (decision 3, F8). Nothing about the allowlist or depth defaults changes (decision 6). |
| 11c | medium | sonnet | none | Rust unit tests for 11a and 11b in `src/vmm/src/main.rs`'s existing test module (find it near the existing `differencing_refusal_error_*` tests at `:945`). Cover: the policy enum selects the gate or the walk; a Windows-absolute value is classified as such rather than as a missing file; and the reason strings are distinct. Where a test would need a real KVM guest to run `execute_info_operation`, test the classification helper 11b introduced directly instead of the whole walk -- factor it out as a small pure function taking the reference string if 11b has not already. `make test-rust` must be clean; note the worktree target-ownership trap in `AGENTS.md` if cargo complains about `src/target`. |
| 11d | medium | sonnet | none | Python integration tests in `tests/test_differencing.py`. Update `test_info_chain_stops_at_a_vhd_parent` (`:586`) to assert a two-image chain for the resolvable fixtures, renaming it for what it now asserts, and keep its docstring's habit of saying which phase owns the boundary. The fixtures are in `DIFFERENCING_CHAIN_FIXTURES`; the resolvable ones sit beside their parents in `instar-testdata/custom/format-coverage/` (`vhd-diff-child-aligned.vhd` -> `vhd-diff-parent.vhd`, `vhdx-diff-child.vhdx` -> `vhdx-diff-parent.vhdx`). Leave `test_info_reports_the_locator_without_following_it` (`:830`) asserting one image and add to it an assertion on the stderr reason, so it passes for the allowlist reason rather than passing by accident (F7, and the definition of done). Add: an orphaned child still lists one image and exits 0; and every composing operation is byte-for-byte unchanged -- extend or cite `TestDifferencingParentAbsent` (`:609`) rather than writing a new class, since it already encodes the invariant. Run the suite with `python3 -m pytest tests/test_differencing.py` or the repo's usual runner; the full suite is expected zero-fail on healthy testdata. |
| 11e | medium | sonnet | none | Decision 7: give `info --chain` a JSON form. `InfoArgs.output` is declared at `src/vmm/src/main.rs:3617-3619`; the `--chain` branch at `:10269-10282` ignores it. Add a JSON printer beside `print_backing_chain` (`:2724`) and select on `args.output`. Match `qemu-img info --backing-chain --output json`: a top-level array, one object per chain member, keys for the resolved filename, format, virtual size, actual size, cluster size where non-zero, and the unresolved backing reference where there is one. Reuse the existing `json_escape` helper (`:17899`, tested at `:20191`) rather than adding a JSON dependency -- the VMM hand-rolls its JSON elsewhere. Add an integration test asserting the output parses with `json.loads` and has one element per chain member. Keep the human form byte-identical; a test that diffs it against the current output is worth more than one that reads it. |
| 11f | low | haiku | none | Housekeeping. (1) File two issues: the silent chain-device truncation from F10 (#601, name `write_chain_config` at `src/vmm/src/main.rs:3041-3050`, the two unguarded callers `run_check` and `run_bench_guest`, the duplicate `MAX_CHAIN_DEVICES` at `:331` versus `src/shared/src/lib.rs:4725`, and that it affects qcow2 and VMDK today); and the VHD locator-table resolution gap from decision 3 (#602). (2) Add a `CHANGELOG.md` entry for the walked chain and the JSON form. (3) Amend this plan's *What the survey found* with anything later steps discovered, and record the issue numbers. Do not add plan references to any source file or comment. |
| 11g | medium | sonnet | none | Rewrite `docs/chain-discovery.md`'s backing-file-support table and its Known limitations section: delete the rationale about Windows-path incompatibility that `vhdx::posix_relative_path` falsified (F4), keeping the one about refusals not becoming contingent on a parent file existing; correct `docs/info.md`'s claim at `:67-68` that `--chain` stops at one image. |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| A composing operation's refusal becomes contingent on the parent existing -- the exact defect the gate was added to prevent | Decision 1 makes it structurally impossible: nine of ten call sites never resolve a VHD or VHDX parent. The management session checks the diff for exactly this, and `TestDifferencingParentAbsent` (`tests/test_differencing.py:609`) fails if it happens. A green suite is not the evidence; the call-site audit is. |
| The adversarial locator fixtures keep reporting one image for the wrong reason -- e.g. resolution silently failing rather than the allowlist rejecting | 11d asserts the stderr reason, not just the image count. The definition of done states it separately because "the test still passes" is precisely how this one hides. |
| A Windows absolute path gets rewritten into a POSIX path by a well-meaning implementer | Decision 4 states it, 11b's brief repeats it, and 11c tests the classification. It is called out three times on purpose: it is the one change here that would turn a listing command into a file-disclosure primitive. |
| `run_check --chain` is mistaken for a display-only caller because of its flag name | F6 and 11a's brief both name it explicitly with the two line numbers that prove it composes (`:11336`, `:11347`). |
| Scope grows into phases 12-16 because the walk makes composition look close | The scope section names the guest machinery and its file (`src/crates/qcow2/src/lib.rs:9442`) so its absence is a fact rather than an impression. No step touches `src/operations/`. |
| The JSON step (11e) drags the phase out | It is last but for housekeeping, independent of every other step, and decision 7 says to drop it if it does. The issue is filed either way. |

## Definition of done

* `instar info --chain` on `vhd-diff-child-aligned.vhd` and on
  `vhdx-diff-child.vhdx` reports a two-image chain, with the parent
  resolved to an absolute path. Demonstrated with the command output in
  the commit message, not asserted.
* `instar info --chain` on each of the six fixtures in
  `ADVERSARIAL_LOCATOR_FIXTURES` reports a one-image chain, exits 0,
  **and** writes a stderr reason, and the reason is the correct one for
  that fixture — `vhd-diff-locator-etc-passwd` is outside the allowlist,
  `vhd-diff-locator-unc` is a Windows absolute path, and `url`,
  `overlong` and `conflicting` are each not found. The second half
  is the part that matters: a one-image chain alone does not distinguish
  "correctly refused" from "silently failed to resolve".

  Corrected after CI: `dotdot` gives either "not found" or "outside the
  allowlist", and which one is a property of the host rather than of
  instar. `../../../` resolves relative to the fixture's own directory,
  so it reaches a real `/etc/passwd` only where the testdata tree sits
  within three levels of the root, as CI's `/testdata/` mount does;
  every development clone is deeper and stops at "not found" first,
  because resolution canonicalises before the allowlist is consulted.
  Both are refusals the walk classified and named, so the test accepts
  either for that fixture and only that fixture.
* `instar info --chain` on a differencing child whose parent has been
  moved away reports a one-image chain and exits 0.
* Every one of the nine composing call sites in F6 passes the composing
  policy, verified by reading the diff and listed in 11a's commit
  message by line number.
* `convert`, `compare`, `dd`, `bench`, `map`, `measure` and `check`
  produce byte-identical output for a differencing source with its
  parent present and with its parent absent. This is the phase's
  central invariant; state the two captured outputs in the commit
  message.
* No value that reached the host from inside an image is rewritten
  between path conventions anywhere in the diff. `grep` the diff for
  `replace` and for `'\\'` and account for every hit.
* `docs/chain-discovery.md` no longer carries the
  `posix_relative_path`-falsified reason (F4), its backing-file-support
  table (`:132-133`) reflects what VHD and VHDX now do, and
  `docs/info.md:67-68` no longer says `--chain` stops at one image.
* `instar info --chain --output json` output parses with `json.loads`
  and has one element per chain member -- or 11e was dropped and the
  issue exists. Say which.
* No source file or comment added by this phase cites a plan phase,
  step or decision number, and the gate comment rewritten by 11a no
  longer does either.
* The two issues 11f asks for are filed, with numbers recorded in
  this plan: #601 (chain devices past `MAX_CHAIN_DEVICES` dropped with
  only a debug log) and #602 (differencing VHD parent resolution
  ignores the locator table). The review rounds on the pull request
  added four more -- #608 and #609 in round 1, #611 and #612 in round
  2 -- recorded in *What the survey found*.
* `make lint`, `make test-rust`, the full Python suite, and
  `pre-commit run --all-files` are clean.

## Back brief

Before implementing, restate: which call sites get which policy and why
`run_check` is not among the reporting ones; what happens to each of the
four unresolvable-parent reasons; and what stops a Windows absolute path
being rewritten.

**Gate on 11a.** Bring the policy enum's shape -- variant names and the
signature change -- back for agreement before editing ten call sites.
The edit is mechanical and the naming outlives the phase: phase 14 flips
these call sites one at a time and should not have to rename anything to
do it.

No gate on 11b through 11f; the decisions above settle them.
