# Phase 1: Relocate the renderer's tests

Master plan: [PLAN-bugreport-split.md](/components/ryll/plans/PLAN-bugreport-split/).

## Planning effort

**Medium.** The work is mechanical: 651 lines leave one file and
arrive in another, unchanged. The judgement was in confirming the
destination compiles under every build the renderer has, and in
settling the master plan's open questions 1 and 3 before phase 3
acts on them. Both are done below.

**Review effort: low.** The commit is a move; the review is a
diff of the moved block against the original, which the
*Definition of done* makes a command rather than a reading.

## Scope

In:

* Move nine snapshot serialisation tests and the one helper they
  share out of `ryll/src/bugreport.rs` and into a new
  `shakenfist-spice-renderer/tests/snapshots.rs`.
* Delete the two test-module imports the move strands.
* Lower `ryll/src/bugreport.rs`'s entry in
  `tools/source-size-baseline`.
* Answer the master plan's open questions 1 and 3, and correct
  the master plan where this survey found it wrong.

Out:

* Every other test in `ryll/src/bugreport.rs`, including
  `keyboard_snapshot_serialises` and
  `test_app_snapshot_serialises`, which test ryll's own
  `KeyboardSnapshot` and `AppSnapshot`.
* Any production code. Nothing in `ryll/src/` or
  `shakenfist-spice-renderer/src/` changes in this phase.
* `docs/diagnostics.md`'s claim that `ChannelSnapshots` is "the
  struct in `ryll/src/bugreport.rs`". It is wrong today, but the
  re-export it came from survives until phase 3, and the
  sentence has to be rewritten then anyway. See decision 6.
* Issues #784 and #548, which touch the code this plan moves.
  See decision 7.

## What the survey found

The master plan's phase 1 section is accurate on its headline
claim -- nine tests, destination
`shakenfist-spice-renderer/tests/snapshots.rs` -- and wrong on
one number elsewhere. Details, measured on `develop` at
`c7793b9`:

**1. The moving set is contiguous and self-contained.** Lines
2,507-3,150 of `ryll/src/bugreport.rs` hold
`test_display_snapshot_serialises`,
`test_inputs_snapshot_serialises`,
`test_cursor_snapshot_serialises`, the helper
`assert_baseline_channel_fields`,
`test_main_snapshot_serialises`,
`test_playback_snapshot_serialises`,
`test_playback_codec_round_trips`,
`test_usbredir_snapshot_serialises` and
`test_webdav_snapshot_serialises`; lines 3,260-3,266 hold
`test_channel_snapshots_new`. 651 lines in two blocks, each
block starting at a `#[test]` attribute rather than at the `fn`
-- `test_display_snapshot_serialises` carries ten lines of
`#[test]` and `#[expect(...)]` above its signature, and an
extraction anchored on the `fn` lines silently drops two of the
nine tests' attributes. The master plan's "roughly 645" was that
mistake; `sed -n '2507,3150p;3260,3266p'` yields 651 lines, nine
`#[test]` and ten `fn`. Grepping
the two blocks for every ryll type in the module returns
nothing: they reference only `DisplaySnapshot`,
`InputsSnapshot`, `InputEventRecord`, `CursorSnapshot`,
`CursorCacheEntry`, `MainSnapshot`, `OutstandingResizeSnapshot`,
`PlaybackSnapshot`, `PlaybackSessionInfo`, `PlaybackCodec`,
`UsbredirSnapshot`, `RedirectedDevice`, `WebdavSnapshot`,
`StreamSnapshot`, `DecodeResult` and `ChannelSnapshots` -- all
defined in `shakenfist-spice-renderer/src/snapshots.rs` -- plus
`H264Offer` and the `serde_json::json!` macro.

**2. The helper moves with them.** `assert_baseline_channel_fields`
is called from six of the nine moving tests and from nowhere
else in the file.

**3. Everything the moved tests touch is public.** Every type
above is `pub` and, per an `awk` pass over `snapshots.rs`
looking for a lowercase field without `pub`, so is every field
of every one of them. `ChannelSnapshots::new` and
`snapshot_json_for` are `pub`. So the tests compile as an
integration test, which settles open question 3 in favour of
`tests/snapshots.rs` over folding them into
`src/snapshots.rs`.

**4. Both dependencies the moved tests need are already
unconditional dependencies of the renderer crate.**
`serde_json = "1"` and
`shakenfist-spice-protocol = { path = ... }` are in
`[dependencies]`, not `[dev-dependencies]`, and Cargo makes a
package's dependencies available to all of its targets,
integration tests included. Nothing is added to any manifest.

**5. `snapshots.rs` has no `cfg(feature)` gate anywhere**, so
`UsbredirSnapshot` and `WebdavSnapshot` exist in the slim build
too. This matters because `tools/check-slim-renderer.sh` runs
`cargo clippy --no-default-features --all-targets -- -D warnings`
and then once per feature in isolation, and `--all-targets`
compiles integration tests. A new test target that referenced a
feature-gated type would fail a lane nothing else in CI covers.

**6. The `#[expect(clippy::too_many_lines)]` survives the
move.** `shakenfist-spice-renderer/Cargo.toml` has
`[lints] workspace = true`, so it inherits
`too_many_lines = "warn"` and `clippy.toml`'s
`too-many-lines-threshold = 200`;
`test_display_snapshot_serialises` is 271 lines, so the
expectation stays fulfilled and does not become an
`unfulfilled_lint_expectations` warning under `-D warnings`.

**7. `allow-unwrap-in-tests` covers integration tests.** The
moving block has 18 bare `unwrap()` calls and the workspace
lints `unwrap_used = "warn"`. `tests/control_socket.rs` already
has 13 of them with no file-level `allow`, and CI is green, so
the clippy.toml setting reaches `tests/`.

**8. Two imports become unused and must go.** After the move,
the test module's `use shakenfist_spice_protocol::link::H264Offer`
and
`use shakenfist_spice_renderer::snapshots::OutstandingResizeSnapshot`
have no remaining user in `bugreport.rs` (0 references outside
the moving blocks). `serde_json::json` has 18 and
`crate::notifications::NotificationStore` stays. Leaving the two
produces an unused-import warning, which `-D warnings` makes a
failure.

**9. Nothing is duplicated at the destination.**
`src/snapshots.rs`'s single test,
`bytes_totals_sums_every_channel`, exercises
`ChannelSnapshots::bytes_totals`, which no moving test touches.

**10. The master plan says four files where the answer is
five.** `ChannelSnapshots` is imported from `crate::bugreport`
by `app/connection.rs`, `app/mod.rs`, `auto_snapshot.rs`,
`main.rs` and `rdp.rs`. The original count missed
`app/mod.rs`, whose `use` is wrapped across three lines and so
escaped a single-line grep. Corrected in the master plan and in
`docs/plans/index.md` as part of this phase's planning commit;
a later step should not redo it.

### The consumer survey, which settles open question 1

Every name imported from `crate::bugreport` anywhere in
`ryll/src/`, with the concern it belongs to and the files that
import it. This is the table phase 3 executes.

| Name | Goes to | Importers |
|---|---|---|
| `TrafficBuffers` | `traffic` | `app/bug_report.rs`, `app/connection.rs`, `app/mod.rs`, `app/notification_snapshots.rs`, `app/ui/traffic_panel.rs`, `auto_snapshot.rs`, `main.rs`, `rdp.rs` |
| `TrafficDirection`, `TrafficViewEntry` | `traffic` | `app/ui/traffic_panel.rs` |
| `GITHUB_ATTACHMENT_LIMIT_BYTES`, `oversized_for_attachment` | `traffic::budget` | `app/bug_report.rs` |
| `AppSnapshot` | `snapshot` | `app/mod.rs`, `auto_snapshot.rs`, `main.rs`, `rdp.rs` |
| `SurfaceInfo` | `snapshot` | `app/events.rs` |
| `OpenUiElement`, `UiChange`, `UiSnapshot`, `UiState` | `snapshot` | `app/ui_state.rs` |
| `HeldKeyRecord`, `KeyLogEntry`, `KeyLogEvent`, `KeyModifiers`, `KeyOutcome`, `KeyboardSnapshot`, `SentScancodes` | `snapshot::keyboard` | `input_egui.rs` |
| `BugReport` | `bugreport` | `app/bug_report.rs`, `app/mod.rs`, `auto_snapshot.rs`, `main.rs`, `rdp.rs` |
| `BugReportType` | `bugreport::metadata` | `app/bug_report.rs`, `app/mod.rs`, `app/ui/dialogs.rs`, `app/ui/keys.rs`, `app/ui/stats_bar.rs`, `auto_snapshot.rs` |
| `NotificationSnapshotState`, `ReportRegion`, `TriggerTimestamps` | `bugreport::metadata` | `app/bug_report.rs`, `app/trigger_snapshot.rs` |
| `ChannelEnd`, `DisconnectCause` | `bugreport::disconnect` | `app/bug_report.rs`, `app/mod.rs` |
| `PedanticConfig` | `bugreport::pedantic` | `app/mod.rs`, `main.rs`, `rdp.rs` |
| `chrono_now`, `filename_timestamp` | `format` (crate root) | `app/screenshots.rs`, `app/trigger_snapshot.rs`, `capture.rs` |
| `encode_png` | `png` (crate root) | `app/screenshots.rs`, `app/trigger_snapshot.rs` |
| `format_size` | `format` (crate root) | `app/ui/traffic_panel.rs` |
| `ChannelSnapshots` | `shakenfist_spice_renderer::snapshots` | `app/connection.rs`, `app/mod.rs`, `auto_snapshot.rs`, `main.rs`, `rdp.rs` |

The result that decides open question 1: **eight of the
seventeen files stop importing from `bugreport` altogether**
once the split lands -- `app/connection.rs`, `app/events.rs`,
`app/notification_snapshots.rs`, `app/screenshots.rs`,
`app/ui/traffic_panel.rs`, `app/ui_state.rs`, `capture.rs` and
`input_egui.rs`. Every name each of those imports is traffic,
snapshot or helper; none of them is about bug reports. A
`bugreport/traffic/` subdirectory would leave all eight still
naming `bugreport` in their imports, which is the defect the
split is for. Siblings it is.

### Related open issues

Neither is in scope; both are recorded so a later phase does not
resolve one by accident while moving code past it.

* **#784, "Record webdav channel traffic in the bug-report ring
  buffer".** This is the issue for the asymmetry documented in
  `TrafficBuffers::buffer_for`'s doc comment, which phase 3
  moves into `traffic/mod.rs`. The comment and the gap both
  survive the move unchanged.
* **#548, "DisplaySnapshot is shared by every display
  channel".** A renderer-crate design issue about a type whose
  tests this phase moves. It is an argument *for* the move: if
  #548 is ever fixed, the fixture that has to change is then in
  the same crate as the type.

## Decisions

1. **The tests become an integration test, not a unit test.**
   `shakenfist-spice-renderer/tests/snapshots.rs`, alongside the
   four test files already there. Findings 3, 4, 5 and 7 above
   say it compiles and lints; the alternative, folding them into
   `src/snapshots.rs`, would take that file from 1,154 lines to
   about 1,800 and put it in `tools/source-size-baseline` --
   trading the baseline entry this plan is removing for a new
   one in another crate. Open question 3 is answered; the master
   plan is updated to say so.

2. **Three sibling modules, confirmed.** Open question 1 is
   answered by the consumer survey above, in favour of what the
   master plan proposed. The fallback it described
   (`bugreport/traffic/`) is not taken, and the master plan's
   open question 1 is marked answered rather than left for phase
   3 to reopen.

3. **The helper moves and keeps its name.**
   `assert_baseline_channel_fields` has no caller outside the
   moving set, so it is not left behind and not duplicated.

4. **The `#[expect(clippy::too_many_lines)]` and its reason move
   verbatim.** The reason is an argument about why one fully
   populated fixture is the thing under test and must not be
   split; it is as true in the new file as the old, and
   rewriting it would lose the argument.

5. **The move is byte-identical apart from the file header.**
   The new file gets a module doc comment and the `use` lines it
   needs; every other line is the original. This is what makes
   the review a `diff` rather than a re-read, and it is what the
   *Definition of done* checks.

6. **`docs/diagnostics.md`'s wrong sentence is fixed in phase 3,
   not here.** It says `ChannelSnapshots` is a struct in
   `ryll/src/bugreport.rs`. That is wrong today, so fixing it
   now would be defensible -- but the re-export that caused it
   survives until phase 3, the sentence has to be rewritten then
   regardless, and this phase's commit is a pure move whose
   value is that it contains nothing else. One stale sentence
   for two phases is the cheaper error.

7. **#784 and #548 stay open and untouched.** Phase 1 moves
   tests; neither issue is about the tests. Recording them here
   is so that phase 3's mover, reading `buffer_for`'s comment
   about the missing webdav ring, does not helpfully add one.

8. **The baseline is lowered, not left stale.** The ratchet only
   fails on growth, so a baseline entry of 5,110 against a
   4,455-line file passes while being wrong.
   `tools/source-size-ratchet.py --update` lowers it; the hook's
   own header says that is the intended way.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | medium | sonnet | none | Move the nine snapshot tests and their helper to a new renderer integration test |
| 1b | low | haiku | none | Lower the ratchet baseline and prove the new test target builds slim and per-feature |

### Step 1a: the move

Commit subject: `Move snapshot serialisation tests to the renderer.`

Create `shakenfist-spice-renderer/tests/snapshots.rs` holding,
in this order and otherwise unchanged:

* lines 2,507-3,150 of `ryll/src/bugreport.rs` -- nine `#[test]`
  functions and the helper `assert_baseline_channel_fields`
  between the third and fourth of them; and
* lines 3,260-3,266 of the same file --
  `test_channel_snapshots_new`.

Both ranges start at a `#[test]` line, not at an `fn` line.
Delete 2,507-3,151 and 3,260-3,267 from the source -- one line
further in each case -- so the blank line that separated each
block from its neighbour goes with it and the remaining tests
keep exactly one blank line between them.

Dedent each moved line by four spaces: they are currently inside
`mod tests` and will be at file scope. Keep every
`#[test]`, every `#[expect(...)]` with its full reason string,
every comment and every assertion exactly as they are.

The new file's header is a doc comment saying these tests pin
the JSON field names that a ryll bug report's
`channel-state.json` exposes, that they moved here from
`ryll/src/bugreport.rs` because the types they test are defined
in `src/snapshots.rs`, and these imports:

```rust
use serde_json::json;
use shakenfist_spice_protocol::link::H264Offer;
use shakenfist_spice_renderer::snapshots::{
    ChannelSnapshots, CursorCacheEntry, CursorSnapshot, DecodeResult, DisplaySnapshot,
    InputEventRecord, InputsSnapshot, MainSnapshot, OutstandingResizeSnapshot, PlaybackCodec,
    PlaybackSessionInfo, PlaybackSnapshot, RedirectedDevice, StreamSnapshot, UsbredirSnapshot,
    WebdavSnapshot,
};
```

Trim that list to what the moved code actually names -- do not
add an import to silence an error without checking the code uses
it. No manifest changes: both `serde_json` and
`shakenfist-spice-protocol` are already unconditional
dependencies of the renderer crate, and dependencies are
available to integration tests.

Then delete the two blocks from `ryll/src/bugreport.rs`, and
delete these two now-unused lines from its `mod tests` header:

```rust
use shakenfist_spice_protocol::link::H264Offer;
use shakenfist_spice_renderer::snapshots::OutstandingResizeSnapshot;
```

Keep `use super::*`, `use crate::notifications::NotificationStore`
and `use serde_json::json` -- the remaining tests use all three.

Change nothing else. Do not reformat the moved tests, rename
them, split the long one, add assertions, or "fix" anything you
notice while moving: this commit's whole value is that a
reviewer can confirm it is a move by diffing. If something in
the moved code looks wrong, say so in your report and leave it.

Verify, and report the numbers:

```
make test
git grep -c '#\[test\]' -- '*.rs' | awk -F: '{s+=$2} END {print s}'   # must be 1408
wc -l ryll/src/bugreport.rs                                          # expect 4455
cargo test -p shakenfist-spice-renderer --test snapshots -- --list
```

### Step 1b: baseline and the slim build

Commit subject: `Lower the bug-report source-size baseline.`

Run `python3 -I tools/source-size-ratchet.py --update` and
commit only the resulting change to
`tools/source-size-baseline`. The tool never raises an entry, so
if it changes anything other than
`ryll/src/bugreport.rs`'s line, stop and report.

Then prove the new test target is sound in the builds that
nothing else in CI covers, and paste the tail of each:

```
make lint          # runs tools/check-slim-renderer.sh in the devcontainer
make test
```

`check-slim-renderer.sh` runs clippy with
`--no-default-features --all-targets -- -D warnings` and then
once per feature in isolation. `--all-targets` is why the new
integration test is in scope for it. Change no file other than
`tools/source-size-baseline`; if a lane fails, report the error
rather than fixing it, because a failure here means finding 5 of
the survey was wrong and that is a planning question.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| The sub-agent improves the tests while moving them, and the commit stops being reviewable as a move | Decision 5 and the brief both forbid it; the *Definition of done* checks it with a `diff` of the extracted blocks against `git show develop:ryll/src/bugreport.rs`, which the reviewer runs rather than trusts |
| A test is deleted from ryll and never arrives, or arrives but is not run | Three independent counts: the workspace `#[test]` total stays 1,408; `cargo test --test snapshots -- --list` names ten items (nine tests and no helper); `bugreport.rs`'s own `#[test]` count falls by exactly nine |
| The new test target fails the slim or per-feature clippy lanes | Pre-checked in the survey (finding 5: no `cfg(feature)` in `snapshots.rs`); step 1b runs the lane rather than assuming, and is told to report a failure rather than patch around it |
| An import is added to a manifest that does not need one | Finding 4 establishes both dependencies are already there; the brief says no manifest changes, and the done list checks no manifest file changed |
| The `#[expect]` becomes unfulfilled and `-D warnings` fails | Finding 6: the renderer inherits the workspace lints and the test is 271 lines against a 200-line threshold |

## Definition of done

* `shakenfist-spice-renderer/tests/snapshots.rs` exists, and
  `cargo test -p shakenfist-spice-renderer --test snapshots`
  reports nine tests passed and none ignored.
* `git grep -c '#\[test\]' -- '*.rs'` summed across the
  workspace is 1,408, the same as before the phase.
* `ryll/src/bugreport.rs` no longer contains the strings
  `assert_baseline_channel_fields`, `OutstandingResizeSnapshot`
  or `H264Offer`. (This criterion named `DisplaySnapshot` too,
  which was wrong: the `pub use` re-export block still lists it,
  and phase 3 is what removes that block. See the execution
  notes.)
* The moved code is unchanged. This command prints nothing:

  ```
  diff <(git show develop:ryll/src/bugreport.rs |
           sed -n '2507,3150p;3260,3266p' | sed 's/^    //') \
       <(sed -n '/^use shakenfist_spice_renderer/,$p' \
           shakenfist-spice-renderer/tests/snapshots.rs |
           sed -n '/^#\[test\]/,$p')
  ```

  The exact line anchors will need adjusting to the header the
  step actually wrote; the criterion is that a diff of the two
  bodies is empty, not that this spelling of it runs first time.
* `tools/source-size-baseline`'s entry for
  `ryll/src/bugreport.rs` matches `wc -l` on the file.
* `git diff --name-only develop` lists only
  `ryll/src/bugreport.rs`,
  `shakenfist-spice-renderer/tests/snapshots.rs`,
  `tools/source-size-baseline` and files under `docs/plans/`.
  No `Cargo.toml`, no `Cargo.lock`, nothing under `src/`.
* `pre-commit run --all-files` passes, and `make lint` passes
  including `tools/check-slim-renderer.sh`.
* The master plan's open questions 1 and 3 are marked answered,
  with the answer, and its "four files" count is corrected to
  five.

## Execution notes

**2026-11-27 (+1100).** Both steps ran in plan order and
sequentially, as sub-agents, in
`../ryll-wt-bugreport-p01` on branch
`bugreport-split-phase-01-renderer-tests`.

* **The branch is based on `bugreport-split-plan`, not
  `develop`.** The master plan is committed only on that branch
  and the operator has held both local for review, so there was
  no base on `develop` that carried the master plan this phase
  plan references. If the master plan merges first, this branch
  rebases onto `develop` with no conflict.

* **Back brief answers, both as planned.**
  `keyboard_snapshot_serialises` and
  `test_app_snapshot_serialises` stay in ryll; the wrong
  sentence in `docs/diagnostics.md` stays deferred to phase 3
  (decision 6).

* **Step 1a** (Sonnet 5.5, medium effort) reported that it
  deleted the wrong lines partway through and corrected itself
  by restoring `use serde_json::json;`. The management session
  checked the result rather than the account: the final diff of
  `ryll/src/bugreport.rs` is 655 deletions and **zero**
  additions, so the misstep left no trace. The plan's
  byte-identical `diff` criterion passed as written, first time.

* **Correction to the Definition of done.** It required that
  `ryll/src/bugreport.rs` no longer contain the string
  `DisplaySnapshot`. It still does, once, in the
  `pub use shakenfist_spice_renderer::snapshots::{...}`
  re-export block -- which this phase was never going to touch,
  because phase 3 removes it. The criterion was wrong, not the
  implementation; it has been corrected above. The other three
  strings are gone.

* **For phase 3: that re-export block's comment is now stale.**
  It reads "Re-export channel-state snapshot types for
  ryll-side callers (e.g. tests in this file and consumers under
  `ryll/src/app/`)", and the "tests in this file" half stopped
  being true with this phase. It is not fixed here because phase
  3 deletes the comment along with the block, and editing it
  would have cost step 1a its purity for no gain.

* **Verification.** `make test`: the new `tests/snapshots.rs`
  binary reports `test result: ok. 9 passed; 0 failed; 0
  ignored`, and 1,823 tests pass across the workspace with no
  failures. `make lint` passes, including all nine stages of
  `tools/check-slim-renderer.sh` -- `--no-default-features` and
  then each of the seven features in isolation, plus the banned
  dependency-tree check. The management session re-ran both
  rather than taking them from the sub-agents' reports.

**2026-11-27 (+1100), after the master plan merged.** The
master-plan pull request merged and GitHub retargeted this one to
`develop`, which had taken four more merges meanwhile, so this
branch was rebased onto the fresh `develop`. The master plan's own
commit dropped as already landed, and the single conflict was in
`tools/source-size-baseline`, where another pull request had
raised `ryll/src/capture.rs` to 1,789 on the line below this
phase's entry; both lines keep their new value. `make test` (1,823
passed, 0 failed) and `pre-commit run --all-files` were re-run
after the rebase.

* **Review round 1 raised no `fix` item:** 1 document, 2
  consider, 4 none. Taken, in one commit: the document item, and
  one missing blank line between the two moved blocks in the new
  test file. The document item is
  `docs/channel-diagnostics-audit.md`, which named
  `ryll/src/bugreport.rs` as the home of the per-channel
  serialiser tests and as where to write the next one -- true
  before this phase, wrong after it. The reviewer also suggested
  naming the two ryll-owned snapshots there as exceptions; that
  was left out, because the document is channel-scoped and
  mentions neither `AppSnapshot` nor `KeyboardSnapshot`, so the
  distinction would be new information in the wrong place.
  Declined: consolidating the 271-line display fixture, which
  decision 5 forbids and which is now recorded under the master
  plan's *Future work*.

* **The byte-identical `diff` criterion holds against the move
  commit, not against the branch head.** The blank line above is
  one added line in a later commit, so running the criterion
  against the head now reports that one line. Run it against the
  move commit.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of it and how the work you intend to do
aligns with it.

There is no design gate in this phase: both of its decisions are
already taken above, and neither step is expensive to redo. The
one thing worth confirming before step 1a starts is that the
operator agrees the two ryll-owned serialisation tests
(`keyboard_snapshot_serialises`, `test_app_snapshot_serialises`)
stay in ryll. They look exactly like the nine that leave, and
moving them would be the easy mistake.
