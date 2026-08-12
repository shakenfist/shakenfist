# Phase 2b: qemu output-parity widen (map/snapshot/vpc on older qemu)

Master plan: [PLAN-distro-matrix-ci.md](/components/instar/plans/PLAN-distro-matrix-ci/).
Planning effort: **high** (touches instar's core output emitters, the
VHD writer, and the version model). Isolation: none for the code;
instar-testdata baseline work lands via its own `--no-commit`-audited
PR. **Phase 4 (the merge-queue matrix) depends on this** — without it
the matrix is red on every distro shipping qemu < 9.0.

## Why this phase exists

Phase 2 concluded, from the `info` baselines alone, that instar's
two-boolean output model (`include_child_node` ≥8.0, `include_dirty_flag`
≥6.1) needed no widening. Phase 2c then ran the **full** suite against
every matrix distro's own qemu-img and disproved that for three other
commands: instar hard-codes its newest-qemu behaviour for `map`,
`snapshot -l`, and the `vpc` writer, so it diverges on any qemu older
than the dev host's 10.x. This is the widen-vs-document decision phase
2d reserved for management; Michael chose **inventory, then widen**
(2026-08-09).

One of the three turned out not to be an output-formatting divergence
at all. See 2b-E: instar writes VHD footers that every qemu before
10.0 reads as **short**, silently truncating the tail of the disk.
That is a data-loss defect on Debian 12, Ubuntu 22.04, Ubuntu 24.04 and
any RHEL 9 carrying its original qemu, and it is the highest-priority
item in this phase.

## The inventory (phase-2c full runs, 2026-08-09)

Full suite via `tools/test-package-functional.sh`, all seven matrix
distros, 3253 tests each:

| Distro (qemu) | Failed | map-json `compressed` | snapshot `-l` | qcow1→vpc |
|---------------|--------|-----------------------|---------------|-----------|
| debian:12 (7.2.22) | 21 | 19 | 1 | 1 |
| ubuntu:22.04 (6.2.0) | 21 | 19 | 1 | 1 |
| ubuntu:24.04 (8.2.2) | 2 | **0** | 1 | 1 |
| debian:13 (10.0.11) | 0 | 0 | 0 | 0 |
| fedora (10.2.2) | 0 | 0 | 0 | 0 |
| rockylinux:9 (10.1.0) | 0 | 0 | 0 | 0 |
| rockylinux/rockylinux:10 (10.1.0) | 0 | 0 | 0 | 0 |

The set is bounded and reduces to the same three classes everywhere;
6.2.0 surfaces nothing 7.2.22 does not. Ubuntu 24.04 is the row that
separates them: at 8.2.2 the map failures are already gone while
snapshot and vpc still fail, so the three boundaries are distinct.

## Grounding facts (measured 2026-08-10)

**instar-testdata ships 80 static per-version `qemu-img` builds** at
`qemu-img-binaries/x86_64/<version>/qemu-img` (6.0.0 → 10.2.0,
static-pie, run directly on this host). Every boundary below was
measured with them in seconds. The earlier draft of this plan proposed
building qemu from git tags to find the snapshot boundary; that is
unnecessary. **Measure with these binaries; do not reason from the
version-map, and do not reason from qemu source.**

### map-json `compressed` — boundary 8.2.0, confirmed both ways

    8.1.5:  { ... "data": true, "offset": 589824}
    8.2.0:  { ... "data": true, "compressed": false, "offset": 589824}

Absent at 8.1.5, present at 8.2.0, and the live suite agrees (Ubuntu
24.04 at 8.2.2 has zero map failures). The field sits between `data`
and `offset`. instar emits it unconditionally at
`src/vmm/src/main.rs:15141` and `:15149`.

### snapshot `-l` layout — boundary exactly 9.0.0

Measured on `custom/snapshots/snap-qcow2-longname.qcow2` across
6.0.0 / 7.2.22 / 8.0.0 / 8.1.0 / 8.2.0 / 8.2.2 / 9.0.0 / 9.1.0 /
9.2.0 / 9.2.4 / 10.0.0 / 10.2.0. Every version ≤ 8.2.2 emits the old
layout; every version ≥ 9.0.0 emits the new one. Both header lines
verbatim (old is 79 chars, new is 80):

    ID        TAG               VM SIZE                DATE     VM CLOCK     ICOUNT
    ID      TAG               VM_SIZE                DATE        VM_CLOCK     ICOUNT

The old layout is a single concatenated printf with **no separators**
and different widths — `%-10s%-16s%9s%20s%13s%11s` (the 18/7 split
recorded when this plan was drafted is indistinguishable for any tag
that fits its field; see the execution notes) — where the current
(≥9.0) emitter at `main.rs:16177` uses space-separated
`{:<7} {:<16} {:>8} {:>19} {:>15} {:>10}`. The clock also narrows:
`%02d:%02d:%02d.%03d` (12 chars) below 9.0 versus the 4-digit-hour
`{:04}:{:02}:{:02}.{:03}` (14 chars) at and above it. So this is a
second rendering branch, not a title swap. Verify the reconstruction
byte-for-byte against the static builds before writing code:

    ID %-10s | TAG %-16s | VM SIZE %9s | DATE %20s | VM CLOCK %13s | ICOUNT %11s

Derive field widths by growing an input past the field boundary, not by
reading a single row: adjacent (pad, width) pairs that sum the same are
indistinguishable until something overflows.

### The `snapshot-list-human` testdata baselines are wrong

`profiles/profile-6-0-0/*.stdout.txt` are **byte-identical** to
`profiles/profile-10-0-0/*.stdout.txt` and carry the new underscored
form, so the pre-9.0 profile does not match what real pre-9.0 qemu
emits. The defect is in the profile *derivation*, not the capture: each
baseline's own `.meta.json` records the correct `stdout_bytes` (255 for
the old form) while the file beside it is 258 bytes. The version-map
itself is right — it already splits at 9.0.0, matching the measurement
above.

An audit of every profile-bearing output type (`stdout` set hashed per
profile, plus a size-versus-`meta.stdout_bytes` check) found this in
**`snapshot-list-human` only**: 11 mismatched files, 2 profiles with
identical stdout. `map-json`'s 3 profiles are sound. `create-info-json`
has 80 identical-stdout profiles, which is redundant but not wrong (the
output genuinely does not vary). Re-run that audit as the acceptance
check for 2b-D; the script is trivial and belongs in instar-testdata.

### The vpc case is a writer defect, not a version quirk

The stub plan hypothesised that instar was faithfully emulating an old
qemu that truncates. It is not — **real qemu never truncates, at any
version.** `qemu-img convert -O vpc` then back to raw, on the same
qcow1 source, at 7.2.22 / 8.2.2 / 9.0.0 / 10.0.0: every version yields
2123776 bytes from a 2097152-byte source (qemu rounds *up* to CHS).

Reading instar's own vpc output tells the real story:

| Reader | `instar convert -O vpc` output | qemu's own vpc output |
|--------|-------------------------------|-----------------------|
| qemu 7.2.22 / 8.2.2 / 9.0.0 / 9.2.4 | 2088960 | 2123776 |
| qemu 10.0.0 / 10.0.7 / 10.2.0 | 2097152 | 2123776 |
| `instar info` | 2097152 | — |

The footers explain it:

    instar  app='imgo' current=2097152  CHS=60/4/17 -> 2088960   MISMATCH
    qemu    app='qemu' current=2123776  CHS=61/4/17 -> 2123776   consistent

qemu's writer always rounds the declared size up so CHS and
`current_size` agree. instar keeps the size verbatim and writes the
VHD-spec **floor** geometry, so its CHS addresses 8192 bytes less than
the disk it declares. This is deliberate:
`src/crates/vhd/src/lib.rs:397` `footer_geometry()` documents the
choice as "matching Hyper-V/disk2vhd-style writers (current_size
authoritative, CHS a floor approximation)". The assumption is wrong for
the creator app instar actually writes. qemu's `vpc_open` trusts
`current_size` only for creator `win ` (Hyper-V) or `qem2` (qemu's
`force_size` marker), or when CHS is at its 65535/16/255 maximum;
everything else — including `imgo` — gets the CHS product. qemu 10.0
changed that default, which is why the dev host never noticed. **The
code's stated intent and the bytes it writes disagree.**

It is not convert-specific. `instar create -f vpc` is equally affected:

    2M    instar 2088960     / qemu 2123776
    100M  instar 104761344   / qemu 104865792
    1G    instar 1073479680  / qemu 1073995776

So every VHD instar has ever written declares more disk than its
geometry addresses, and any consumer on qemu < 10.0 — Debian 12,
Ubuntu 22.04, Ubuntu 24.04, RHEL 9 — silently loses the tail.

The fix chosen for 2b-E (creator app `qem2`) was validated before
being written down. Rewriting only the creator app in instar's
existing output — no geometry or size change, footer checksums
recomputed in both footer copies — gives:

| Reader | `imgo` (today) | `qem2` |
|--------|---------------|--------|
| 6.0.0 / 6.2.0 / 7.2.22 / 8.2.2 / 9.0.0 / 9.2.4 | 2088960 | 2097152 |
| 10.0.0 / 10.2.0 | 2097152 | 2097152 |

and the qcow1→vpc flatten becomes **byte-identical to the reference
raw** on 6.2.0, 7.2.22, 8.2.2 and 10.0.0 (2097152 bytes on all four,
versus 2088960 today on the first three). `win ` behaves identically
but claims to be Hyper-V; `qem2` is the honest marker, and instar's own
reader already resolves it to `current_size`.

### The VHD *reader* rule is version-dependent too

`src/operations/info/src/main.rs:747` decides virtual size with
`use_chs = (creator == "vpc " || creator == "qemu") && !chs_at_max` —
an allowlist. Real qemu < 10.0 uses the complement (a *denylist*:
CHS unless `win `/`qem2`/max-CHS), so the two rules differ for every
other creator app. Measured on the four CHS-mismatched fixtures in
testdata, no real-world fixture is affected: `virtualpc-dynamic.vhd`
(`vpc `) reads as CHS on all versions, `hyperv2012r2-dynamic.vhd`
(`win `) and `d2v-zerofilled.vhd` (max CHS) read as `current_size` on
all versions. The only file that splits by version is one instar wrote
itself. The exposure is therefore latent, and 2b-E may close it at the
source — see 2b-F. Note this rule lives **guest-side**, unlike map and
snapshot.

### Plumbing: `--qemu-version` is `info`-only

`qemu_version` is a field of `InfoArgs` (`main.rs:3466`) and the
profile is resolved inline inside `run_info` (`main.rs:10097-10118`).
`MapArgs` and `SnapshotArgs` have no such flag and never touch
`version::get_profile()`. Both renderers are host-side
(`MapRenderer::emit_extent`, `SnapshotRenderer::emit_snapshot`), so
their gates need no guest-ABI change — but they do need the profile
plumbed in, and without the flag the fixes can only be exercised on a
pre-boundary distro rather than on the dev host. 2b-A does that first.

## Steps

| Step | Effort | Model | Isolation | Brief |
|------|--------|-------|-----------|-------|
| 2b-A | medium | opus | none | **Profile plumbing for map and snapshot (do this first).** Lift the profile resolution at `main.rs:10097-10118` into one helper (`fn resolve_output_profile(flag: Option<&str>) -> Result<OutputProfile, …>`) and call it from `run_info`, `run_map` and `run_snapshot` rather than copying it a third and fourth time. Add `--qemu-version VERSION` to `MapArgs` and `SnapshotArgs` with the same help text and the same "invalid qemu version" error. Pass the resolved `&OutputProfile` into `MapRenderer::new` and `SnapshotRenderer::new`. No behaviour change in this step — the renderers ignore the profile until 2b-B/2b-C — so it lands green everywhere and keeps those two diffs small. Add the flag to `docs/map.md` / `docs/snapshot.md` alongside the existing `info` documentation. |
| 2b-B | medium | opus | none | **map-json `compressed` gate (boundary certain).** Add `include_map_compressed` to `version::OutputProfile` with `for_version` = `v.major > 8 \|\| (v.major == 8 && v.minor >= 2)`, plus unit tests pinning 8.1/8.2 either side. In `MapRenderer::emit_extent` (`main.rs:15136-15152`) emit the key only when set — both the has-offset and no-offset arms. **Field order is load-bearing**: `start, length, depth, present, zero, data, compressed, offset`, pinned by the ordering test at `main.rs:18883`; dropping `compressed` must close the gap, not reorder anything. Make `json_compressed_false_emitted_for_every_state` (`main.rs:18867`) profile-aware and add its below-boundary twin. On the Python side, extend the map baseline tests to iterate profiles with `--qemu-version` the way the info tests do, so the dev host covers both sides of 8.2 instead of only its own. Validate with `tools/test-package-functional.sh --select 'test_map'` on debian:12 and ubuntu:22.04 (19 → 0) and confirm ubuntu:24.04 and the 10.x distros are unchanged. |
| 2b-C | medium | opus | none | **snapshot `-l` pre-9.0 layout.** Add `snapshot_underscored_columns` (or similar) to `OutputProfile`, true for ≥ 9.0. Give `SnapshotRenderer::emit_snapshot` a second human branch emitting the measured old layout — `%-10s%-18s%7s%20s%13s%11s`, titles `VM SIZE`/`VM CLOCK`, and a 2-digit-hour clock helper beside `format_qemu_snapshot_clock`. Keep the byte-width padding trick (`" ".repeat(N.saturating_sub(len))`) in the old branch too: it exists because Rust's `{:<N}` counts chars where C counts bytes, and that reasoning is width-independent. JSON mode is an instar extension and does not change. Make the snapshot-format tests at `main.rs:~18959`, `~19042`, `~19204` profile-aware and add old-layout cases asserting the exact header string above. Depends on 2b-D landing in testdata first. Validate with `--select 'test_snapshot'` on debian:12 (and ubuntu:24.04, which fails today at 8.2.2). |
| 2b-D | medium | opus | none | **Fix the `snapshot-list-human` baselines in instar-testdata.** Regenerate that output type's profile dirs from the raw per-version captures so `profile-6-0-0` carries the real pre-9.0 bytes; the version-map's 9.0.0 split is already correct and must not move. Add two invariants to the generator (`scripts/`, see the testdata_baseline_generator memory note): every `.stdout.txt` length equals its `.meta.json` `stdout_bytes`, and no two profiles of one output type may have identical stdout sets unless the type declares itself version-invariant. Re-run the full audit across all output types to confirm `snapshot-list-human` was the only casualty. Separate PR on instar-testdata, `--no-commit` audited, Maintainer-role token (testdata_push_token). **Land this before 2b-C** and re-point instar at the updated testdata in the same change, or the suite flips red in between. |
| 2b-E | high | opus | worktree | **vpc footer CHS under-addressing — data loss on qemu < 10.0. DECIDED: option (1), Michael, 2026-08-10.** Every VHD instar writes declares a `current_size` its CHS geometry does not cover, so pre-10.0 readers truncate (2 MiB image → 8192 bytes lost; the numbers generalise to create, convert and resize). **Write creator app `qem2`** — qemu's own `force_size` marker — in `build_footer` (`src/crates/vhd/src/lib.rs:1067`), leaving `footer_geometry()`'s verbatim-size behaviour and every declared size untouched. This is exactly the intent `footer_geometry()` already documents, and it is the only option that changes no size on any reader. Pre-validated (see the grounding facts): with `qem2`, all of 6.0.0/6.2.0/7.2.22/8.2.2/9.0.0/9.2.4/10.0.0/10.2.0 report the declared size and the qcow1→vpc flatten is byte-identical to the reference raw on every one. Rejected: (2) a covering geometry, which makes old readers over-report instead of truncating; (3) rounding the declared size up like qemu, which abandons verbatim-size convert semantics; (4) document only, which leaves silent data loss on three of seven matrix distros. Implementation notes: the creator app appears in **both** footer copies of a dynamic VHD (offset 0 and EOF) and the footer checksum must be recomputed for each. Add a permanent regression test reading an instar-written VHD with the oldest and newest static `qemu-img` builds, asserting they agree on virtual size and that a flatten round-trip preserves every byte. Keep the zero-tail assertion in `_test_qcow1_output_format` — it is what caught this. Expect fallout in vpc baselines/hashes and in the `vhd-fixed` fixture (`scripts/create-vhd-testdata.sh`, creator `imgo`), and check `docs/quirks.md`'s VHD sections for text this invalidates. |
| 2b-F | medium | opus | none | **VHD reader rule: gate or document (decide after 2b-E).** instar implements qemu ≥10.0's allowlist; pre-10.0 qemu uses a denylist, so the two disagree for any creator app outside `vpc `/`qemu`/`win `/`d2v `/`qem2` whose CHS ≠ `current_size`. No third-party fixture in testdata is affected and 2b-E removes the only file that was, so the live exposure may fall to zero. This one is **guest-side** (`src/operations/info/src/main.rs:747`), so gating it means getting the profile across the guest ABI — materially more expensive than 2b-B/2b-C. Therefore: first add a fixture with an unknown creator app and CHS < `current_size`, drop its `skip_qemu_img`, and see whether real pre-10.0 qemu-img and instar actually disagree on it. If they do, either widen the ABI or record it as a documented, per-image known divergence with the reason; if they do not, document the rule's version dependence in `docs/quirks.md` and stop. Do not widen the ABI speculatively. |
| 2b-G | low | sonnet | none | **bitmap oracle on the RPM family.** The deb family installs `qemu-storage-daemon` via `qemu-system-common` (commit 01bef5a). Confirm the provider on Fedora/Rocky; if it is genuinely unavailable, make the bitmap differential helpers `skipTest` when `shutil.which('qemu-storage-daemon')` is None — the same absent-oracle discipline `skip_unless_qemu_supports()` established in phase 2c — so those tests skip cleanly rather than erroring. |
| 2b-H | medium | opus | none | **Full-matrix validation + docs.** Re-run the full (not `--smoke`) suite on all seven distros and confirm green modulo anything documented in 2b-F. Run them **serially or with a bounded concurrency**: nine failures in the phase-2c inventory were host-load artifacts that read as data corruption, so a parallel re-run will manufacture its own failures (see Risks). Update `docs/testing.md` with the three measured boundaries and the static-binary measurement recipe, `docs/format-coverage.md` with the vpc outcome, and `docs/quirks.md` for whatever 2b-E and 2b-F settle. Refresh the memory note that still records the info-only "no widen" conclusion. |

## Acceptance

- map-json emits `compressed` iff the effective profile is ≥ 8.2; the
  19 map failures clear on debian:12 and ubuntu:22.04; the ≥8.2 distros
  are unchanged (2b-B).
- `snapshot -l` reproduces the measured pre-9.0 layout byte-for-byte
  below the boundary and the current layout at and above it;
  `test_create_list_agreement` passes on debian:12, ubuntu:22.04 and
  ubuntu:24.04 (2b-C).
- `snapshot-list-human/profile-6-0-0` matches real pre-9.0 qemu; every
  baseline's size matches its meta; the audit is clean across all
  output types and the size invariant is enforced in the generator
  (2b-D).
- An instar-written VHD reads back with identical virtual size on
  every static qemu-img build from 6.0.0 to 10.2.0, and a
  convert→flatten round-trip loses no bytes on a pre-10.0 reader
  (2b-E).
- The VHD reader-rule exposure is either gated or documented against a
  fixture that demonstrates it, not against reasoning (2b-F).
- Both map and snapshot accept `--qemu-version`, and the dev host's
  `make test-integration` exercises both sides of every boundary this
  phase introduces (2b-A/2b-B/2b-C).
- Full functional suite green on all seven matrix distros (2b-H);
  `make test` green; `pre-commit` clean.

## Execution results (2026-08-10)

All steps implemented on the `matrix-ci` branch. Each fix was verified
against the real per-version `qemu-img` binaries, not just against the
test suite.

**2b-A.** `resolve_output_profile()` replaces the inline resolution in
`run_info`; `map` and `snapshot` now accept `--qemu-version` and pass
the profile into their renderers. `run_snapshot` resolves the profile
before dispatch, so a bogus version is refused in the mutating modes
too rather than silently ignored.

**2b-B.** `include_map_compressed` (≥8.2) gates the key, which is now
omitted rather than emitted false. Verified emulating 8.1.5, 8.2.0 and
10.0.0: instar's output matches the corresponding real binary
byte-for-byte, including the separators either side of the gap.

2b-B also added `TestMapCrossProfile`, which drives `--qemu-version`
for every profile the version map declares instead of only the
installed one — the coverage gap that let `compressed` reach the distro
matrix undetected. **It immediately found a fourth boundary the matrix
could never have found:** `map --output=json` gained `present` in
6.1.0 (absent at 6.0.1, the same release that exposed the dirty flag),
and the oldest matrix distro is Ubuntu 22.04 at 6.2.0. Gated on
`include_map_present`. Both keys are now built as complete fragments so
the surrounding separators and field order cannot drift.

**2b-C.** `snapshot_underscored_columns` (≥9.0) selects between the two
layouts, with `format_qemu_snapshot_clock_legacy` for the 2-digit hour.
Verified against 8.2.2 and 9.0.0: byte-identical on both sides.

The pre-9.0 widths in the grounding facts above were **wrong**, and the
error survived every short-tag comparison. `%-18s%7s` and `%-16s%9s`
render identically for any tag that fits its field — 18+7 == 16+9 — and
diverge only once the tag overflows and its padding disappears. The
real widths are **10/16/9/20/13/11**, derived by growing a tag from 1
to 40 characters against real qemu 8.2.2 and watching where the VM SIZE
column lands, rather than by reading a sample row. A regression test
pins the overflow case for both layouts.

Two process points from this. First, it was caught only because
removing a stale skip (below) let the 200-character-tag fixture run
against a real 7.2.22 oracle; deriving a format from one sample row is
not measurement. Second, `TestSnapshotListHuman` had skipped entirely
on any pre-9.0 host since the layout was introduced — "instar targets
the modern ≥9.0 format" — so Debian 12 and Ubuntu 22.04 were asserting
nothing there. That skip is now removed, which is what turned the
matrix run into a real check.

**2b-D.** The raw per-version captures were correct all along — only
the derived `profiles/` were stale, so `detect-profiles.py` regenerated
them cleanly and the version map (already split at 9.0.0) did not move.
Added `validate_profiles()` to that script, which now refuses to commit
a profile tree whose `.stdout.txt` sizes contradict their `.meta.json`
`stdout_bytes`, or where two profiles of one output type have identical
stdout. Confirmed it flags the original defect (12 errors) and passes
on the fixed tree. The audit across every output type is clean;
`create-info-json`'s 80 identical profiles are redundant but legitimate
and are exempted by the "more than one profile" condition.

**2b-E.** `build_footer` stamps `qem2`. Every version from 6.0.0 to
10.2.0 now reports the declared size for an instar-written VHD, and the
qcow1→vpc flatten is byte-identical to the reference raw on 6.2.0,
7.2.22, 8.2.2 and 10.0.0 (previously 8192 bytes short on the first
three). `test_convert_qcow1_to_vpc` passes. Two unit tests pin the
creator app and, more importantly, the *reason*: the second asserts
that any size whose CHS product under-addresses `current_size` is
accompanied by `qem2`.

**2b-F. Decided: document, do not widen the ABI.** The fixture the plan
asked for was built by restamping the creator app on instar's own vpc
output and reading it with eight qemu versions:

| Creator app | qemu < 10.0 | qemu ≥ 10.0 | instar |
|-------------|-------------|-------------|--------|
| `vpc ` | CHS | CHS | CHS (agrees) |
| `win `, `qem2` | current_size | current_size | current_size (agrees) |
| `xen `, `azur`, zeros | **CHS** | current_size | current_size (**diverges below 10.0**) |

So the divergence is real, but only for a creator app outside the known
table whose CHS disagrees with its `current_size`. No such image exists
in the corpus, and after 2b-E instar cannot produce one. The rule is
evaluated guest-side (`src/operations/info/src/main.rs:747`), so gating
it means widening the guest ABI to carry the emulated version — which
the plan explicitly says not to do speculatively. Recorded as a known
divergence in `docs/quirks.md` with the measurements. **If a real image
in this class ever turns up, the fix is an additive
`InfoResultMessage` field carrying the CHS product so the host can
apply the version rule without the guest needing to know the version.**

**2b-G.** The dnf branch of the runner never installed
`qemu-storage-daemon`. Measured on rockylinux:9: the *package* of that
name does not exist on EL9 (`Unable to find a match`), but the binary
is shipped by `qemu-img` itself, which the runner already installs. So
the oracle was available all along and the 53 `test_bitmap` tests —
including the `TestBitmapMergeBits` and `TestBitmapCrossValidation`
cases that actually drive it — run and pass on Rocky exactly as they
do on the .deb distros. The runner now asks for the binary by path
(`dnf install /usr/bin/qemu-storage-daemon`) so the dependency is
explicit rather than incidental, and `_bitmap_dirty_extents` skips if
it is ever genuinely missing. The skip is a safety net that correctly
did not fire, not a workaround: no bitmap coverage is lost on the RPM
family.

**2b-H.** Dev host: Rust unit tests 0-fail; the full integration suite
runs 3344 tests 0-fail (up from 3335 — the cross-profile tests). Full
matrix, each row run **sequentially** on an idle host so no result is a
load artifact, 3262 tests per row (the runner also excludes
`test_bench`):

| Distro (qemu) | Before | After |
|---------------|--------|-------|
| debian:12 (7.2.22) | 21 failed | **0** |
| ubuntu:22.04 (6.2.0) | 21 failed | **0** |
| ubuntu:24.04 (8.2.2) | 2 failed | **0** |
| debian:13 (10.0.11) | 0 | **0** |
| fedora (10.2.2) | 0 | **0** |
| rockylinux:9 (10.1.0) | 0 | **0** (847 skipped: 785 baseline + 62 no-oracle) |
| rockylinux/rockylinux:10 (10.1.0) | 0 | **0** (847 skipped) |

The three ≥10.0 rows are not a formality: 2b-E changes the footer bytes
of every VHD instar writes, on every distro.

## Risks / notes

- **Measure with the static binaries, per command.** Two of this
  phase's three "known" facts were wrong when it was first drafted: the
  snapshot boundary was unknowable from the testdata (whose pre-9.0
  baselines are the *new* form), and the vpc case was assumed to be
  faithful old-qemu emulation when real qemu never truncates at all.
  Both took under a minute to settle against
  `qemu-img-binaries/x86_64/`. Treat any remaining claim about old-qemu
  behaviour as unverified until it has been run.
- **Host load fabricates corruption-looking failures.** The large
  `test_convert` re-encodes fail under contention as `convert operation
  failed` and `Content mismatch at offset 0!`, aborting at ~26s instead
  of passing at ~110s. Replay with `--select` on an idle host before
  attributing any matrix failure (memory:
  diffuzz_spurious_divergence_contention).
- **testdata coupling.** 2b-D must land and instar must be re-pointed
  at it in the same change as 2b-C.
- **2b-E moves output bytes.** Any change to the VHD footer invalidates
  vpc baselines, fixture hashes and possibly `docs/quirks.md` prose.
  Budget for the fixture regeneration; it is not a one-line fix even
  though the code edit is small.
- **Distro backports.** The boundaries here are upstream-derived. A
  distro can carry an output change at a version number upstream did
  not; the runner validates against the actual distro qemu, which is
  the check that matters.
- **Scope creep.** Only `map`, `snapshot -l`, the vpc writer and the
  VHD reader rule are in evidence. Add a gate only where a runner
  failure or a static-binary measurement proves a real divergence.
