# PR 739 follow-up: GLZ cross-image reference sharing

## Situation

PR 739 (ThisSeanZhang) made GLZ cross-image references share
their cached source pixels instead of cloning the whole source
image per fragment: `ByteBoundedLru` now stores
`Arc<Vec<u8>>` internally and exposes a `pub(crate)`,
feature-gated `get_shared()`, and the cross-image copy in
`decompress_glz` became a single bounded `copy_from_slice`.
The contributor measured a 1920x1080 decode with 128
references at 175.7 ms before and 1.5 ms after.

The change was reviewed under `MERGE-TEMPLATE.md` on
2026-11-18: Wave 0 scanners all clean, Wave 1 (injection,
supply chain, vulnerability) clean with one low-severity
hardening item, Wave 2 (correctness, style, tests, docs)
found no wrong byte. Every new test fixture and the new
benchmark example's hand-rolled encoder were verified
byte-for-byte against spice-server's
`glz-encode-match.tmpl.c` and spice-gtk's
`decode-glz-tmpl.c`. Local `make lint`, `make test` (1821
tests) and `make package-check` all passed on the branch.

Nothing was merge-blocking. Teachy mode was Light, so four
mechanical items were asked of the contributor in a single PR
comment: the `saturating_sub` hardening at `glz.rs:518-527`, a
stale comment at `glz.rs:425-426`, a stray trailing-comma edit
at `byte_bounded_lru.rs:748`, and moving the benchmark runbook
out of the crate README. This plan tracks everything else.

**The contributor delivered all four on 2026-11-18 in
1269a38fd385 ("Guard GLZ cross-image copy bounds"), reviewed
2026-11-19.** Wave 0 was clean on the new head, the delta was
four files and nine added lines, and `make lint`, `make test`
and `make package-check` passed again. Note for the record that
the README section F7 asked about was added by this same pull
request (in b795aaa), not inherited from develop: the README at
the branch head is byte-identical to develop's and the file has
left the diff entirely. `PUSH-AUDIT.md`'s `readme-discipline`
block calls README growth of that kind a blocking finding in its
own right and says to flag it *and move it* — the contributor did
the deleting half, and item 5 below is the moving half, which is
ours. They also took item 10
below unprompted. Nothing in that commit changed what this plan
has to do, except where noted against each item.

## What has landed

#739 merged on 2026-11-19 as 128f572; CodeQL's post-merge run on
develop passed. The first follow-up branch carries:

| Commit | Closes |
|---|---|
| 64c25ed | Must fix 1, and the troubleshooting half of should fix 4 |
| 5c6fdbf | Must fix 2, test gaps 1, 2 and 5 |
| 3d6dcd2 | Should fix 3, 5 and 6, and the configuration half of 4 |

`make lint`, `make test` (1825 tests) and `make package-check` all
pass on that tree. The three guards in 5c6fdbf were mutation-checked:
both clamps and the `Arc` sharing were each reverted in turn and each
was caught by its own test. Writing those mutants is what found that
only one of the two skip paths had a test at all.

Two things worth recording against the items as written:

- **Must fix 1's figures were derived again and came out lower.** The
  review's estimate was 111 bytes before #739 and 143 after. Deriving
  it from the `lru` crate's node layout against glibc gives about 91 and
  123. The +32 delta the decision rested on is the same either way,
  and 128 still sits inside the honest band, so the operator's choice
  stands; the doc comment now shows the derivation rather than
  asserting a figure.
- **Should fix 3 was re-verified rather than softened.** Both kerbside
  references are deleted upstream (`kerbside/utilities/glz.py` in
  8bc00cc, `testclient/ryll/decompressors.py` in 32dfbf8), but git
  still has them, so the claim was checked against the real file. For
  RGB32 the new bulk copy moves the same bytes as kerbside's loop on
  any well-formed stream. Malformed streams are where kerbside,
  spice-gtk and ryll all differ, which sharpens should consider 9
  below from an unease into a decision.
- **Must fix 2 closed on the clamp alone.** It asked for the
  hole-versus-shift inconsistency between the miss and clipped-hit
  paths to be settled as well. That is a decision about what the
  decoder does with a malformed stream, not about the clamp, so it
  moved to should consider 9; `skip_unsatisfied_reference`'s doc
  comment enumerates the three current behaviours so the question
  is visible where the code is.

## Still open

Should consider 7-10, and test gaps 3, 4, 6, 7 and 8. Gap 8 stays
tracked as #135 rather than duplicated here.

## Must fix

1. **`PER_ENTRY_OVERHEAD_BYTES` understates real per-entry cost,
   and the module's RSS bound is now wrong.**
   Storing `Arc<Vec<u8>>` shrinks the `lru` node by 16 bytes
   (an `Arc` pointer replaces a `Vec` header) but adds a whole
   `ArcInner<Vec<u8>>` allocation of 40 bytes, which the
   allocator rounds to a 48-byte chunk. Measured on
   x86-64/glibc, real per-entry overhead moves from about 111
   bytes to about 143. The constant stays at 96, so the module
   doc's claim at `byte_bounded_lru.rs:15-25` that the entry
   cap "budgets container overhead at no more than the payload
   budget, so the worst-case footprint is about twice
   `cap_bytes`" is now out by 25-35%: for the default 256 MiB
   GLZ dictionary under a pathological stream of 1x1 images
   the real worst case is nearer 640 MiB than 512 MiB. The 96
   figure was already optimistic before this PR; PR 739
   widened the gap.

   **Operator decision, 2026-11-19: bump the constant to 128
   and let the derived entry cap tighten accordingly.** This
   is a behaviour change — the derived entry cap shrinks by
   about 25%, so a pathological small-image stream holds fewer
   entries before eviction. It restores the "about twice
   `cap_bytes`" bound honestly rather than hedging the prose
   around an unsupported number.
   Work: bump `PER_ENTRY_OVERHEAD_BYTES` to 128 in
   `shakenfist-spice-compression/src/byte_bounded_lru.rs`,
   re-derive the figure in its doc comment (lines 42-54) with
   the layout above rather than the hedge PR 739 left there,
   fix the corollary at lines 15-25, and update
   `docs/troubleshooting.md:1023-1031`, which repeats the 96
   estimate and says an entry "really costs around a hundred".
   `entry_cap_is_derived_from_the_byte_cap` is written in terms
   of the constant, so it keeps passing; check the other
   entry-cap tests for hard-coded expectations.

2. **The dictionary-miss paths advance `out_idx` unclipped.**
   `glz.rs:452` and `glz.rs:500` both do
   `out_idx += length * 4` on a miss, where `length` is
   attacker-controlled and grows with `0xFF` continuation
   bytes, so `out_idx` can land far past `output_size`. That is
   safe today only because each is immediately followed by
   `continue`, which re-enters the `while out_idx < output_size`
   guard. PR 739's first draft of the bulk copy computed
   `(output_size - out_idx) / 4`, so it *depended* on that,
   where the per-pixel loop it replaced was self-guarding.
   1269a38fd385 restored the self-guard: `saturating_sub` plus
   an `if bytes > 0` skip, which together make the copy total
   for any `out_idx`, including one past `output_size` — where
   even an empty slice index would otherwise panic. So nothing
   is load-bearing on this item any more. It is still worth
   doing, as the other half, clamping at the source:
   `out_idx = out_idx.saturating_add(length * 4).min(output_size)`
   at both sites, so that neither line depends on the other.
   See also test gap 5 below.

   Two things an independent read of the round-2 delta turned up
   that whoever writes the clamp has to decide about:

   - **The two advances are inconsistent with the hit path.** A
     cross-reference *miss* advances `out_idx` by the full
     `length * 4`, leaving a hole in the output; a *hit whose
     source is truncated* advances only by the bytes it actually
     copied, which shifts every later pixel instead. Both are
     safe, both are attacker-visible as garbage, and they
     disagree about what a reference that cannot be satisfied
     means. Clamping does not settle that. (Deferred to should
     consider 9; see "What has landed".)
   - **The multiply is unclamped as well as the addition.** On a
     32-bit target `length * 4` overflows at roughly 1.07e9
     pixels, which about 4.2 MiB of `0xFF` continuation bytes
     reaches inside the 257 MiB `MAX_MESSAGE_BODY` — debug
     panics, release wraps to a wrong but in-range `out_idx`.
     The workspace targets 64-bit only and no 32-bit target
     appears in the release workflow, so this is theoretical
     today; `saturating_add` and `saturating_mul` cost nothing
     and make it moot.

   Note the irony worth avoiding in the commit message: round 2
   hardened the one expression that could not break the
   `out_idx` invariant and left untouched the two statements
   that do.

## Should fix

3. **`docs/design-decisions.md:91-93` records a parity check
   against code that no longer exists.** It says the GLZ pixel
   copy loops "were compared against kerbside's Python
   `glz.py` and found faithful". PR 739 replaced the
   cross-image copy loop with a bulk `copy_from_slice`, and the
   new clipping tests compare against a model the contributor
   wrote, not against kerbside. Either re-verify against the
   kerbside reference and say so with its revision, or soften
   the claim to name the paths it still covers. Note that the
   reference was not locatable locally during this review:
   `testclient/ryll/decompressors.py` is in neither
   `~/src/shakenfist/kerbside` nor `kerbside-client`, so Wave
   2a verified against the spice C sources instead. Finding the
   reference, or recording that it has moved, is part of this
   item.

4. **The cap's documented meaning needs a caveat.**
   `docs/troubleshooting.md:1015-1033` and `:1100-1104`, and
   `--glz-dictionary-cap-mib` in `docs/configuration.md:54`,
   all describe the cap as bounding memory. After PR 739 an
   in-flight decode can keep an evicted or cleared source alive
   until its fragment copy finishes, so RSS can briefly exceed
   the cap by up to one source image per concurrent display
   channel. One sentence at `troubleshooting.md:1104`, for
   example: "The cap counts cache-owned pixels; a decode in
   flight can keep an evicted source alive until its copy
   finishes." PR 739 states this honestly in
   `byte_bounded_lru.rs`'s module docs — the README paragraph
   that said it too went out in 1269a38fd385 along with the
   benchmark section — but our operator-facing docs do not say
   it yet.

5. **Document the new benchmark example where our other
   manual benchmarks live.** The precedent is
   `docs/development.md`'s "Manual verification against a
   desktop guest" section (line 493), whose "Example
   control-socket client" subsection (line 675) documents
   `examples/control-socket-demo.py` — the same shape of thing
   as this one: a workspace example with a prose home. "Measuring
   idle CPU" (line 233) is the other nearby precedent for a
   manual measurement. `docs/index.md` needs nothing; it lists
   pages, not subsections, and already carries a row for
   `development.md`. Add a short subsection: the command, that
   it measures elapsed decode time and not session frame rate,
   that there is no CI threshold, and that comparisons need the
   same build profile on both revisions. **Now required rather
   than
   tidying:** 1269a38fd385 removed the README section, so after
   #739 merges the only record of how to run the benchmark is
   the `//!` header of `examples/glz_references.rs`, which is
   the right place for the command but not for the caveats. The
   text to carry across is in that commit's README deletion.

6. **Say what the benchmark does not measure.** The example
   times a single-threaded decode against a one-entry
   dictionary. That isolates the per-fragment copy cost
   correctly, but the other half of the old cost was
   `images` mutex hold time with several display channels
   sharing one dictionary, and the example says nothing about
   it. One line wherever the runbook ends up, so the 100x
   number is not over-read.

## Should consider

7. **Restore the deleted design rationale.**
   `byte_bounded_lru.rs:32-37` replaced the paragraph
   explaining why the type is deliberately concrete rather
   than generic ("both current consumers want exactly that
   shape ... avoids paying for generic machinery we don't
   need") with a description of the `Arc` internals. Both
   belong: the rationale is why the next person should not
   make it generic.

8. **Consolidate the GLZ stream builders in tests.** There are
   now five hand-rolled builders: `glz_rgb32_row` (`glz.rs`,
   new), `glz_image_2x1` (`glz.rs`, pre-existing), the inline
   header in `fragmented_cross_ref_image`, the encoder in
   `examples/glz_references.rs`, and a copy of `glz_rgb32_row` in
   `tests/glz_shared_source.rs`, which as an integration test
   cannot see the unit tests' helpers either. `glz_rgb32_row` is a strict
   generalisation of `glz_image_2x1`. A wrong fixture is how a
   wrong decoder looks right, so one shared builder is worth
   having. Constraint: an example cannot see `#[cfg(test)]`
   items, so sharing with the example needs a small
   `#[doc(hidden)]` module behind the `glz` feature rather than
   a test helper.

9. **Decide whether lenient clipping is the behaviour we
   want.** spice-gtk aborts the whole decode when a reference
   overruns (`g_return_val_if_fail(op + len <= op_limit)`);
   ryll silently clips and carries on, so a malformed stream
   yields a misaligned image rather than an error. That
   leniency predates PR 739, but its new tests now pin it as
   intended behaviour, so a future decision to refuse such
   streams has to change them. Either write down that the
   leniency is deliberate and why, or change it — but not
   silently. The same decision covers the inconsistency must fix 2
   found: a reference that misses leaves a hole and keeps later
   pixels in place, while a hit whose source runs short shifts
   them, as does one whose offset is past the end of its source
   (it copies and advances nothing).

10. **Done by the contributor in 1269a38fd385.** The example's
    comment overstated its own coverage.
    `examples/glz_references.rs:66` says the non-uniform RGBA
    pixels "expose incorrect offsets and channel ordering". The
    generated stream contains only cross-image references and
    no literals, so the BGR-to-RGBA literal path never runs and
    channel ordering is not exercised. Offsets are covered
    thoroughly. They cut the clause to "expose incorrect
    reference offsets". No work left.

## Test coverage gaps

The nine tests PR 739 adds are good: three pin the new sharing
mechanism in `ByteBoundedLru`, and
`cross_reference_copies_only_complete_pixels_within_both_buffers`
is a genuine differential oracle over about 2850 decodes
against an independently written model. The gaps are:

1. **Nothing fails if per-fragment cloning comes back.** Every
   `glz.rs` test compares decoded pixels, which are identical
   before and after PR 739, so the change's actual purpose has
   no regression guard. The example has no assertion and CI
   never runs it. Closing this needs a test hook: observe
   `Arc::strong_count` or `Arc::as_ptr` from a dictionary-level
   test, or count source allocations, so a test can show a
   decode does not allocate a new source buffer.
2. **Flake risk in
   `waiting_cross_reference_decodes_after_source_insertion`.**
   Ordering is deterministic — it polls the boxed future once
   so the `Notified` registration happens before the insert —
   but it leaves the real 100 ms `CROSS_REF_TIMEOUT_MS` timer
   running, so a deschedule longer than that on a loaded runner
   fails it. `tokio::time::pause` fixes it and needs the
   `test-util` dev feature. The same file already has two
   pre-existing wall-clock tests
   (`cross_ref_to_unknown_id_still_waits` asserts
   `elapsed >= 100ms`, `cross_ref_to_evicted_id_does_not_wait`
   asserts under 50 ms); fix all three together.
3. **The README's own claim is untested at decoder level.** No
   test shows an in-progress fragment keeping a *cap-evicted*
   source alive. `shared_source_survives_dictionary_window_invalidation`
   never runs the decoder and uses `evict_older_than`, not cap
   eviction.
4. **No concurrent-decoder test.** Two decoders referencing the
   same source id while a third thread inserts and evicts,
   asserting identical pixels, no panic and no poisoned lock.
   The dictionary is shared across display channels
   (`session.rs:358`), so this is the real deployment shape.
5. **`GlzDictionary::clear` with live handles, and the
   tombstone fast path.** `clear` touches neither tombstones
   nor live handles; a reference to a cleared id should take
   the wait path rather than the tombstone path. And the
   tombstone path's unclipped `out_idx` advance (must-fix 2)
   needs a test with `length` past the end of the output
   buffer, followed by a literal, asserting neither a panic nor
   a misplaced literal.
6. **Extended encodings are untested.** The grid test uses
   `length` 2..=6 and `offset` 0..=5, so the extended-length
   (`length >= 7`) and extended-offset (`>= 4096`, `>= 1<<17`)
   cross-reference forms are only exercised by the manual
   example. Extend the grid.
7. **Make the grid a proptest.** `proptest` is already a
   dev-dependency of this crate (used in `src/lz4.rs` and
   `src/encode/mod.rs`), and the grid already compares against
   a model, so generating source length, offset, length and
   width subsumes the hand-rolled loops and reaches the
   extended encodings.
8. **No GLZ fuzz target.** `fuzz/` exists only under
   `shakenfist-spice-protocol`, with an LZ4 target and no GLZ
   one. GLZ is parsed straight from untrusted server bytes and
   this PR's arithmetic — the clipping `min` chain and the
   `out_idx` advances — is exactly what a target would hit.
   Already tracked: **#135** ("Broaden fuzz coverage beyond the
   phase-1 link-handshake parsers"), which
   `PLAN-decoder-hardening.md`'s future-work section names as
   covering QUIC, LZ, GLZ and the cursor decoder. Do not file a
   duplicate; record PR 739 as a reason #135 is now worth more.

## Administration

### Tracking

Must-fix 1 is a deliberate behaviour change and should land on
its own commit with the docs edits that justify it. Must-fix 2
is the other half of what the contributor was asked for, and
they did their half in 1269a38fd385, so it should land after
#739 merges. It is the cross-image analogue of #626 ("LZ and
GLZ back-reference one pixel before the image start panics"),
which
`PLAN-decoder-hardening.md` closed by adding the
`out_idx.checked_sub` guard that still stands in the
self-reference path — so the fix has a precedent in this very
function to match.
Items 3-6 are documentation and can land together. Items 7-10
are polish. The test gaps are independent of each other; gaps
1, 2 and 5 are the ones worth doing first, because gap 1 is
the only guard on the change's purpose and gaps 2 and 5 are
about CI reliability and bounds respectively.

The contributor answered within a day and did all four items,
so the plan-B fallback (apply them ourselves on their branch
under `maintainerCanModify`) was not needed. The review's local
state file (`REVIEW-STATE.md` in the `ryll-wt-pr739` worktree,
never committed) has the full finding list and those steps.

### Context

Round 2's delta (1269a38fd385) was additionally given two
independent reads — one on the arithmetic against the spice
reference sources, one on scope, documentation loss, repository
conventions, packaging and provenance. Neither found anything
merge-blocking. The items they added to this plan are marked
above. One claim from those reads was rejected on local
evidence: that the trailing comma proves the first two commits
were `cargo fmt --check` dirty. `make lint` passed at 6e495e4
in round 1 and at 1269a38fd385 in round 2, so rustfmt accepts
both forms inside the `assert_eq!` macro and the series is
format-clean. Two rounds of review have now guessed wrong about
rustfmt's behaviour inside a macro from first principles; run it
instead.

Created while reviewing PR 739 under `MERGE-TEMPLATE.md`,
before merging it, per that document's Step 6. The PR is at
https://github.com/shakenfist/ryll/pull/739 and the review's
findings were posted there as a single comment. The reference
sources used to verify the new fixtures were
`/srv/src-reference/spice/spice/server/glz-encode-match.tmpl.c`,
`glz-encoder.c`, `/srv/src-reference/spice/spice-gtk/src/decode-glz-tmpl.c`
and `/srv/src-reference/spice/spice-common/common/lz_common.h`.
