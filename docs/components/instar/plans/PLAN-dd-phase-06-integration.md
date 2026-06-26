# PLAN-dd phase 06: Integration test consolidation

Master plan: [PLAN-dd.md](/components/instar/plans/PLAN-dd/)
Previous phase: [PLAN-dd-phase-05-rust-tests.md](/components/instar/plans/PLAN-dd-phase-05-rust-tests/)

## Status: Complete (b6cffd2)

> **Outcome.** 13 tests added (TestDdErrors 7, TestDdOutputDefault
> 1, TestDdInputFormats 5); 30 dd tests total, all pass; convert
> 201/0. Parity finding: all seven rejection cases exit non-zero in
> **both** instar and qemu-img dd (no accept/reject divergence),
> including the bare `=`-less token. dd correctly reads vmdk/vhd/
> vhdx and backing-chain qcow2 inputs.

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Flag any uncertainty
explicitly rather than guessing.

## Mission

Complete the `dd` integration-test matrix by filling the
**non-windowing** dimensions that phases 3 and 4 did not cover. The
windowing rows (aligned/unaligned skip, count clamp, short final
block, empty, sub-sector `bs`, on raw + qcow2 + the four structured
output formats) are already covered by `TestDdRawWindow` and
`TestDdStructuredWindow` in `tests/test_dd.py`. This phase adds the
remaining cross-validation rows: **CLI rejection cases**, the
**`-O` default-is-raw** quirk, and **input-format coverage**.

## What's already covered (don't duplicate)

`tests/test_dd.py` today:
- `TestDdWholeImage` — whole-image raw (qcow2→raw, raw→raw), and
  `test_dd_missing_of_errors`.
- `TestDdRawWindow` — 10 raw window scenarios × raw + qcow2 inputs,
  each byte-identical to `qemu-img dd`.
- `TestDdStructuredWindow` — qcow2/vmdk/vpc/vhdx × {aligned
  skip+count, non-512 end, sub-sector bs} + per-format empty-window
  handling, validated by `qemu-img info` virtual-size + round-trip
  to raw.

All inputs so far are **raw or qcow2**. CLI error cases other than
missing `of=` are only **unit-tested** (phase 2's
`dd_operand_tests`), not exercised end-to-end.

## Gaps to fill (this phase)

### 1. CLI rejection matrix (`TestDdErrors`)

Each of these must exit non-zero from `instar dd`, and — since
`qemu-img dd` rejects the same inputs — the test should confirm
**both** tools reject (rejection parity), even though the error
*text* differs (instar uses native messages per OQ3):
- `bs=0` — both reject (valid range 1..INT_MAX).
- `bs=2147483648` (> INT_MAX) — both reject.
- unknown operand, e.g. `foo=1` — both reject ("unrecognized
  operand").
- a token with no `=`, e.g. `bar` — instar rejects (confirm qemu's
  behaviour; assert instar non-zero regardless).
- missing `if=` (only `of=` given) — both reject.
- missing `of=` (only `if=` given) — both reject (already have a
  variant; keep or fold in).
- unknown output format, `-O bogus` — both reject ("Unknown file
  format" / instar's equivalent).

For the rejection-parity cases, run `instar dd <args>` and
`qemu-img dd <args>` (with both `if=`/`of=` present where the case
isn't specifically about a missing one — for the bad-operand /
bad-format cases supply valid `if=`/`of=` so only the targeted
field is wrong) and assert both return non-zero. Where a case is
about a missing mandatory operand, only the failing form is run.

### 2. `-O` default is **raw**, not the input format (`TestDdOutputDefault`)

A documented qemu-img dd quirk (master plan Mission row 3, matrix
row 10): with no `-O`, the output is **raw**, even from a qcow2
input. Add a test: `instar dd if=<qcow2> of=<out>` (no `-O`), then
assert `qemu-img info --output=json <out>` reports
`format == "raw"` (NOT `qcow2`), and that it equals `qemu-img dd`'s
default output (also raw). This is currently only *implied* by the
whole-image test comparing bytes to a raw baseline; assert the
format explicitly.

### 3. Input-format coverage (`TestDdInputFormats`)

dd inherits convert's input read path (auto-detection + chain
composition), but every existing dd test feeds a raw or qcow2
input. Confirm dd correctly reads the other input formats, windowed,
matching `qemu-img dd`:
- **vmdk, vhd (vpc), vhdx inputs** → `-O raw` output. Build the
  input with `qemu-img convert -O <fmt>` from a patterned raw (or
  `qemu-img create` + `qemu-io`), then for a window (e.g.
  `bs=65536 skip=2 count=4`) assert `instar dd` output is
  byte-identical to `qemu-img dd` (both auto-detect the input
  format). One window per input format is enough — the windowing
  logic is format-independent on the read side; this validates
  detection + read.
- **Backing-chain qcow2 input** → `-O raw`. Create `base.qcow2`,
  an `overlay.qcow2` with `backing_file=base` (and `backing_fmt`),
  write distinct patterns to base and overlay so the composed view
  differs from either layer, then `instar dd <window> if=overlay`
  vs `qemu-img dd <window> if=overlay` — byte-identical. Confirms
  dd composes the chain (via `discover_backing_chain`) like convert.

Optional (only if quick): a `-f <fmt>` test documenting current
behaviour — `-f` is accepted but input-format **forcing** is
deferred (auto-detection is authoritative). A test that
`instar dd -f raw if=<raw> of=<out>` works (matches qemu) documents
that `-f` is at least accepted; do not assert forcing semantics.

### Out of scope / known gaps (do NOT test as parity here)
- `count=0 -O vhdx` empty-window — instar's output is rejected by
  `qemu-img info` (tracked limitation, master plan Future work /
  phase 9). The phase-4 test already asserts exit-0-only for it.
- Encrypted inputs — `run_dd` passes no passphrase, so dd cannot
  read encrypted images; qemu-img dd needs `--object`/secret which
  dd doesn't support (deferred). Out of scope.
- stdin/stdout, `osize=`/`isize=`/`seek=` — PVE/downstream, out of
  scope (master plan).

## Steps

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 6a | medium | sonnet | none | Add `TestDdErrors(InstarTestBase)` to `tests/test_dd.py` covering the rejection matrix in §1. For each rejection-parity case, run `run_instar_dd` and `run_qemu_img_dd` with the same args (valid `if=`/`of=` plus the one bad field) and assert BOTH return non-zero; for missing-mandatory-operand cases run only the failing form and assert instar non-zero (and qemu non-zero where it accepts the form). Reuse the existing `run_instar_dd`/`run_qemu_img_dd` helpers (note `run_qemu_img_dd` injects `bs=512` if absent — pass an explicit `bs=` where the case needs a specific one, and for the `bs=0` case ensure the helper doesn't mask it). Build a small valid input for the cases that need one. Run `^test_dd\.` (all pass) and report. |
| 6b | medium | sonnet | none | Add `TestDdOutputDefault` (§2: `-O` omitted on a qcow2 input ⇒ `qemu-img info` format == "raw", and bytes match qemu-img dd's default output) and `TestDdInputFormats` (§3: vmdk/vhd/vpc/vhdx inputs and a backing-chain qcow2 input, one window each, `-O raw`, byte-identical to `qemu-img dd`). Build inputs with `qemu-img convert`/`create` + `qemu-io` (patterned, position-dependent data); mirror the helper idioms in the existing classes. Run `^test_dd\.` (all pass) and `^test_convert\.` at concurrency 4 as a sanity check (0 failed — do NOT use default concurrency 16). Report. |

Both steps are additive, test-only, in `tests/test_dd.py` (and
`tests/base.py` only if a helper genuinely needs extending). Per the
master plan / `PLAN-TEMPLATE.md`, sub-agents implement and the
management session reviews that the assertions are real (both-tools
-reject for parity cases; explicit format assertion for the default;
byte-equality for input formats). Suggested commits: 6a errors, 6b
default + input formats; or one consolidated "phase 6 integration"
commit.

## Verification

- [ ] `make test-integration` (or `stestr run '^test_dd\.'`) passes,
      including the new error/default/input-format tests.
- [ ] Rejection cases assert **both** instar and qemu-img dd exit
      non-zero (parity), not just instar.
- [ ] `-O`-omitted output is asserted to be **raw** format (not the
      input's), explicitly via `qemu-img info`.
- [ ] dd reads vmdk/vhd/vhdx and backing-chain inputs and matches
      `qemu-img dd` byte-for-byte.
- [ ] `^test_convert\.` still 0 failed (concurrency 4) — no shared
      base.py regression.
- [ ] `pre-commit run --all-files` passes.
- [ ] Only `tests/test_dd.py` (and maybe `tests/base.py`) changed.
- [ ] Commit messages follow conventions (model/context/effort).

## Hand-off

Remaining phases: 7 cross-version baselines (capture reference
`qemu-img dd` outputs across qemu versions in the testdata repo), 8
coverage-guided fuzzing (operand parser + window math +
`chs_rounded_size` + read primitives are good targets), 9
differential fuzzing (random `bs`/`count`/`skip`/`-O` vs qemu-img
dd — resolve the `count=0 -O vhdx` limitation here), 10 docs. The
[[dd-qemu-img-parity-contract]] memory records the verified rules.
