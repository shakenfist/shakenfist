# HTTP input phase 1: the guest I/O wait

## Status: Not started

Master plan: `PLAN-http-input.md`.

## Planning effort

High. The change itself is small -- a bounded loop becomes an
unbounded one -- but it removes the only place in the codebase where
a call-table I/O outlives the call that issued it, and the guest side
is bare-metal `no_std` code with volatile MMIO where a wrong memory
ordering is invisible until it corrupts an image. Review effort:
high for steps 1a and 1e, which touch the virtio request path and
the host's error rendering respectively.

## Decisions already taken by the operator

From the master plan, resolved 2026-11-18:

1. Open question 5: the spin budget is a real defect, reproduced by
   measurement, and phase 1 is a bug fix rather than enabling work.

Taken when this phase was planned, 2026-11-18:

2. **The guest waits indefinitely, and there is no host deadline.**
   Not a watchdog thread, and not a TSC-bounded guest wait. The
   survey below explains why a host deadline cannot be enforced by
   either thread that services the queue.
3. **Issue #849 is out of scope.** The leftover output file after a
   failed convert needs a per-failure keep-or-unlink policy measured
   against qemu-img, which is its own argument.
4. **This phase shares a pull request with the master plan**, as
   PR #921 did for multi-extent VMDK phase 1. The branch is
   `http-input-phase-01-guest-wait`.

## Scope

In scope:

* Removing `MAX_WAIT_ITERATIONS` from `src/core/src/virtio.rs`
  `do_request`, and with it the `send_error("timeout", ...)` path.
  Closes #923.
* Making a guest I/O failure legible from the host without
  `--verbose`: a reserved `send_error` marker for an I/O failure, the
  host decoder capturing it, and `convert` and `dd` rendering it by
  name with an exit code measured against qemu-img rather than
  guessed. Closes #924.
* The LUKS inner-format probe in `src/operations/convert/src/main.rs`
  that treats a failed read as "no inner format", which the master
  plan asked this phase to read carefully. It is a real defect of a
  different shape than the master plan guessed; see the survey.
* A test that proves a slow host read no longer fails an operation,
  and that it would have before.

Out of scope, with reasons:

* **#849**, the leftover output file (decision 3).
* **Queue depth greater than one.** The master plan's Future work
  owns it. This phase makes a slow read survivable, not fast.
* **The VMM's logging defaults.** The survey found that no logger is
  installed at all without `--verbose`, so *every* diagnostic record
  in the VMM is discarded by default, not only guest I/O errors.
  That is wider than #924 and wants its own issue; this phase fixes
  the error the user sees rather than the log they do not.
* **The other operations' I/O error sites.** Step 1d audits all 13
  and records what each one's host side does with it; it converts
  only the ones whose host side currently drops the detail, which
  the survey found to be `convert` and `dd`.

## What the survey found

The master plan's phase 1 section was written before any of this was
measured. One of its claims is wrong, and five things it does not
mention change the shape of the work.

**The one wrong claim, corrected at source in this phase's planning
commit.** The section said the fix was to "move the deadline to the
host where it can be generous and per-request". There is no host
deadline to move it to. `src/vmm/src/io_thread.rs` passes 100 ms to
`epoll_wait`, and the comment there says why: so the loop can
re-check the `running` flag. Nothing times a request, anywhere in
the VMM. Building one is not a move, and decision 2 says not to
build one.

**Why a host deadline is not cheap.** Two paths service a virtio
queue. The I/O thread polls the ioeventfd when `IoEvent::register`
succeeds; when it fails the VMM falls back to processing the queue
from the vCPU exit, which is `process_queue_for_device`, called from
16 operation run loops. Both call `process_queue`, which calls
`self.backing.read_at` synchronously. So the thread that would
notice a request was late is the thread blocked inside the read. A
deadline needs a third thread whose only job is to watch -- new
privileged machinery, in the process holding `/dev/kvm`, to catch a
case (a wedged VMM) that no reproduction in this repository has ever
produced.

**The guest already waits forever on the host elsewhere.**
`src/core/src/serial.rs` `read_config` spins unbounded, in two
loops, waiting for the host to send the device configuration at
startup. An unbounded wait on a trusted host is therefore already
the pattern for one of the two channels between them, which is the
strongest argument that decision 2 is consistent rather than novel.

**The abandon path breaks a documented invariant, which is the
finding most worth reading.** `docs/guest-architecture.md` says the
shared read-modify-write bounce sector in the planner executor is
"safe because all call-table I/O is synchronous and steps execute
serially", and that `Ordering` barriers are a no-op because "issue
order is completion order". The timeout is the only place where a
call-table I/O is *not* synchronous: `do_request` returns `false`
while the host is still working on the request it has no way to
cancel. Compounding it, `do_request` addresses its header, data and
status buffers at fixed addresses -- `DMA_POOL_BASE`,
`DMA_POOL_BASE + 16` and `DMA_POOL_BASE + 16 + sector_size` -- for
every request, while rotating only the descriptor slot. So after a
timeout two requests are in flight against one set of buffers.
Nothing has gone wrong yet because the host services the queue
serially and completes in order, so the guest's next wait still
observes its own bytes. That is safety by accident of serialisation,
and it holds only while nothing in the VMM ever services two
requests concurrently -- which is exactly what the master plan's
Future work proposes to change. Removing the abandon path removes
the hazard before anything depends on it.

**#924 is two layers deep, not one.** Guest messages are formatted
by `format_message` and logged with `debug!` at 16 sites, and
`Level::Error` records go the same way. The logger is installed in
one place, inside `if verbose`, and that is the only
`set_boxed_logger` call in the VMM. So without `-v` there is no
logger at all and the record is discarded before its level is
considered. Separately, `SerialDecoder`'s capture chain names six
guest refusals -- `cpu-exception`, `DifferencingRefusal`,
`VhdFooterChecksumRefusal`, `InvalidL2EntryRefusal`,
`DataFileEntryRefusal`, `MalformedL2EntryRefusal` -- and no I/O
failure, so nothing is kept for `execute_convert` to render in place
of `"convert operation failed"`. That generic string is the only
`"... operation failed"` in the VMM; every other operation already
fails with a sentence.

**What #924 must capture is not `"timeout"`.** Once step 1a lands,
`do_request` never reports a timeout. A genuine host read error
still fails the request through the virtio status byte, and the
*operation* is what reports it: `convert` sends
`send_error("convert", "input", read_sector, 1)` followed by
`send_complete("convert", bytes_read, false)`. Capturing
`"timeout"` would therefore fix a message that step 1a deletes. The
marker has to be the operation's own I/O failure, which is what
outlives this phase and what a remote backing store will report in
phase 3.

**The LUKS probe at `src/operations/convert/src/main.rs` is a
defect, but not the one the master plan guessed.** It wrote that the
`&&`-chained `read_input_sector` "may be a silent-zero-fill bug". It
is not: the probe reads the first payload sector to look for QCOW2
magic, and on a failed read it silently skips the magic check and
falls through to the flat LUKS path. That path then re-reads *the
same sector* -- `payload_offset` is 0, so its `read_sector` equals
the probe's `payload_sector` -- and treats a failure there as fatal,
with `send_error` and a failed completion. So a wrong answer needs
one read to fail and the identical read to succeed moments later,
which is precisely what a transient timeout provides and nothing
else in the tree does. The output would then be a LUKS-wrapped
qcow2 decrypted and written out as a flat image, at exit 0. It is
reachable only through the defect step 1a removes, which is why it
belongs in this phase and not in a later one.

**Test infrastructure does not exist for this, and a missing tool
here is a silent pass.** `strace` appears in no Dockerfile and no CI
workflow in the repository, and the Python suite runs both on the
host (`make test-integration`) and in a container
(`make test-container`). An injection test that skips when `strace`
is absent would therefore be green everywhere and run nowhere --
the shape of the libvhdi oracle problem, where `make
test-container-core` passed for want of an environment variable.
Step 1b has to add the tool where the suite runs and make its
absence loud. The harness's own subprocess timeouts (30 s by
default in `tests/base.py`) are what keep an unbounded guest wait
from hanging CI.

Nothing else in the master plan's phase 1 section was wrong.

## Decisions

1. **The wait becomes unconditional.** `do_request` spins on the
   used ring until the completion arrives, with `spin_loop()` kept
   as the hint. The `timed_out` flag, the budget constant and the
   `send_error("timeout", ...)` branch all go. `do_request` keeps
   returning `false` for the status-byte failure path, which is a
   completed request that failed, not an abandoned one.

2. **No host deadline, and no guest backstop.** Decision 2 of the
   operator's set. The honest statement of the consequence is that a
   VMM which loses a completion hangs the guest until the user
   interrupts it, where today it fails after about 1.3 s with a
   misleading message. That trade is worth taking: a lost completion
   is a VMM bug with no reproduction, a slow read is an everyday
   event with a reproduction in #923, and the current behaviour
   turns the everyday event into the symptom of the rare one.

3. **A reserved marker for an I/O failure, following the refusal
   pattern.** Add `shared::IoFailure` with an `OPERATION` string
   distinct from every real operation name and from the six existing
   markers, its NUL-terminated twin for the call table, and
   documented meanings for `device` (which device failed) and
   `sector`. This is the `InvalidL2EntryRefusal` shape exactly,
   including the unit test that asserts the C string matches the
   Rust string. The guest's I/O error sites send that marker instead
   of their own operation name.

4. **The host renders it in the returned error, not in a log.** The
   decoder captures the marker; `execute_convert` renders it in
   place of `"convert operation failed"`. This follows
   `compare`'s existing wording, which names the image and the
   offset and says what did not happen: "compare: reading the second
   image, ref.raw, failed at virtual offset 327680 (0x50000); the
   images were not compared". The logging defaults stay as they are
   (see Scope).

5. **The exit code is measured, not chosen.** `compare` uses
   qemu-img's read-error code. What `qemu-img convert` exits with on
   a read failure is a question about another tool, and this
   repository has a standing rule that such claims are measured or
   cited rather than asserted. Step 1e measures it with an injected
   read error and records the number in this plan before using it.

6. **The LUKS probe propagates the failure.** A failed probe read
   becomes an error, not a skipped branch. The fix is small; the
   reasoning for why it was reachable is what the commit message has
   to carry, because a reader looking at the diff alone will see a
   failure path added for a read that is retried successfully two
   screens later.

The decision most likely to be argued with is 2. "Wait forever" reads
as a worse answer than "wait longer", and a reviewer may reasonably
ask for a generous TSC-based bound as cheap insurance. The argument
against is that a bound is a number nobody can derive: the guest
cannot distinguish a host that is slow from a host that is stuck,
and any number large enough not to fail a legitimate slow read on a
loaded 16-worker CI host is also large enough that a genuinely
wedged VMM looks like a hang anyway. The bound would buy a better
message in a case that has never been observed, at the cost of
keeping the abandon path -- and the abandon path is what breaks the
synchrony invariant the shared executor relies on.

## Steps

One commit per step, with the subject given in the brief.

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 1a | high | opus | none | **Remove the spin budget.** In `src/core/src/virtio.rs` `do_request`, replace the bounded `for _ in 0..MAX_WAIT_ITERATIONS` loop with an unbounded wait on the used ring, keeping the `read_volatile` of `used_base + 2`, the `expected_used_idx` comparison and `core::hint::spin_loop()` exactly as they are. Delete the `MAX_WAIT_ITERATIONS` constant, the `timed_out` flag, the `if timed_out` block and its `send_error("timeout", ...)` call, and the comment above the constant that claims "100M iterations is generous". Do not touch the status-byte check below it: that path is a completed request that failed and must keep returning `false`. Write a comment in its place saying why the wait is unbounded -- the host is trusted for every byte the guest reads, `src/core/src/serial.rs` `read_config` already waits unbounded on it, and a request cannot be cancelled once issued, so abandoning one leaves two in flight against the fixed `DMA_POOL_BASE` buffers. Do not reference this plan, its phases or its steps in the comment: landed code never cites plan files in this repository. Build with `make instar` (Docker; do not install a Rust toolchain on the host) and confirm pre-commit's binary-size-against-memory-layout hook passes, since this changes `core.bin`'s size. Then reproduce #923 against the new binary and show it no longer fires: `strace -f -qq -e trace=read -e inject=read:delay_enter=3000000:when=30 -o /dev/null <binary> convert -O raw <8 MiB qcow2> out.raw` must exit 0 and produce a byte-identical output to an uninjected run (`cmp`). Report both runs' exit codes and the `cmp` result. Commit subject: `Wait for the virtio completion instead of giving up.` |
| 1b | high | opus | none | **Make the reproduction a test.** Add a test that proves a slow host read no longer fails an operation. Decide where it lives by reading `tools/ci/check-test-partition.sh` and the job claims it checks -- a new module must be claimed by a pull-request job or that guard fails, so either extend an existing claimed module or add the claim in the same commit. The test injects latency with `strace -e inject=read:delay_enter=...:when=...` exactly as #923 documents, asserts exit 0 and a byte-identical output, and must **fail loudly rather than skip** when `strace` cannot inject: probe the capability (run `strace -e inject=read:delay_enter=1:when=1 /bin/true` and check it works, do not test for the binary's presence), and gate the skip behind an environment variable in the style of `INSTAR_REQUIRE_LIBVHDI`, so CI sets it and a missing tool is a failure there while a developer without `strace` still gets a skip. `strace` is in no Dockerfile and no workflow in this repository: add it where the suite runs (`src/.devcontainer/Dockerfile` and/or the CI workflow's package step -- find which actually runs the Python suite, do not guess) and say in your report which files you changed and why. The `when=` index is fragile across trees; make the test derive or search for a value that lands inside the guest run loop rather than hardcoding 30, and say how. Prove the test would have caught the bug: follow the `mutation-check` skill's method, restoring the spin budget in a scratch copy of `virtio.rs`, and confirm this test fails with the budget present and passes without it. Run the module with stestr and check the "Ran: N" line, not just the exit code -- a crashed worker exits 0 in this suite. Commit subject: `Test that a slow host read no longer fails an operation.` |
| 1c | medium | sonnet | none | **The shared I/O-failure marker.** In `src/shared/src/lib.rs`, add an `IoFailure` marker type following `InvalidL2EntryRefusal` as the model, which is the closest existing one: a unit struct, an `OPERATION` string constant distinct from every real operation name and from the six existing markers, the NUL-terminated twin for the call table's `send_error`, and doc comments stating what `device` and `sector` carry (the device that failed, and the sector the operation was reading or writing). Copy the existing markers' test pattern -- there is a test asserting the C string matches the Rust string for each; add the equivalent one here. No guest or host code changes in this step. Run `cargo test -p shared` through the devcontainer (`make test-rust` or the narrower target; do not run native cargo) and report the test count. Commit subject: `Add a shared marker for a guest I/O failure.` |
| 1d | high | opus | none | **Report I/O failures through the marker, and audit the rest.** There are 13 `send_error` call sites across `src/operations/*/src/main.rs` whose arguments look like an I/O failure (grep for `send_error` and filter for `"input`, `"output`, `"chain`, read and write). For each one, record in a table in this plan: the operation, the file and line, what it passes, and what the host does with it today -- whether the host path renders a specific error or drops it into a generic one. The survey established that `convert` is the only operation whose host side has a generic `"... operation failed"` string, so expect most of the 13 to be fine; confirm that rather than assuming it. Convert *only* the sites whose host side drops the detail to use `shared::IoFailure` from step 1c, keeping the sector and adding the device. `dd` shares `execute_convert`, so its sites travel with convert's. Leave the rest as they are and say in the table why each is already adequate. Do not change any operation's result struct or completion message. Build with `make instar` and confirm the guest binaries still fit (the binary-size hook). Commit subject: `Report guest I/O failures through the shared marker.` |
| 1e | high | opus | none | **Complete a failed request, then render it.** Two commits. **First**, the host must answer a failed request instead of abandoning it: `BlockDevice::do_read` propagates a `BackingStore::read_at` failure with `?`, so `process_queue` returns before writing the used ring and the I/O thread drops the error, which since the first commit of this phase means an `EIO` hangs the guest forever rather than failing it. A failure to service a request must complete it with `request_status::IOERR` so the guest sees a completed-but-failed request, raises the marker from the previous step, and fails the operation. Cover the write and flush paths the same way, and do not swallow the underlying error silently -- the host knows the errno and the guest does not. Verify with an injected `EIO` on an input read: `convert` must exit non-zero promptly where it now hangs (reproduce the hang first, bounded by `timeout`, so you know your fix is what changed it). **Second**, render it: in `src/vmm/src/main.rs`: add a field to `SerialDecoder` for a captured I/O failure, capture `shared::IoFailure::OPERATION` in the `Payload::Error` chain alongside the six existing markers (follow `last_invalid_l2_entry` for the shape, including its comment style explaining what `sector` means), add a `format_message` arm so a `--verbose` run reads well, and render it in `execute_convert`'s failure tail in place of `"convert operation failed"` -- in the same position as the existing `last_differencing_refusal` and `l2_entry_refused` branches, which are the pattern to copy. Word it after `compare`'s existing read-failure error: name the image by the path the user passed, name the offset in both decimal and hex, and say what did not happen. `dd` and `convert` share the function, so pick the operation name the way the existing branches do (`exec.window.is_some()`). **Measure the exit code before choosing it**: run `qemu-img convert` against a source whose read fails (inject with `strace` as #923 documents, or use a source on a FUSE or loop device you can fail) and record qemu-img's exit code, the `qemu-img` version, and the exact command in a *Measurements* section of this plan. Use that code if `convert` can; if instar's exit-code plumbing cannot express it, say so in the plan and use the nearest thing, naming both. Add unit tests beside the existing decoder tests (`differencing_refusal_error_*` are the models) for: nothing captured yields the generic message, a captured failure names the image and offset, and the `dd` spelling. Commit subject: `Say what failed when a guest I/O error stops convert.` |
| 1f | medium | opus | none | **The LUKS inner-format probe (#926).** In `src/operations/convert/src/main.rs`, the LUKS path's inner-format detection reads the first payload sector inside an `if` whose condition `&&`-chains `(call_table.read_input_sector)(...)`, so a failed read skips the QCOW2 magic check and falls through to the flat LUKS path. Make the failed read an error: report it through `shared::IoFailure` and fail the operation the way the main read loop below it already does (`send_error` then `send_complete(..., false)`), rather than continuing with an undetected inner format. Read the survey section of this plan first -- the reason this matters is that the main loop re-reads *the same sector* and treats a failure as fatal, so the only way to reach the bad outcome is a read that fails once and succeeds on retry, and the output is then a decrypted qcow2 written out as a flat image at exit 0. The commit message must carry that reasoning, because the diff alone looks like a redundant check. Do not reference this plan. If there is an existing LUKS-wrapped-qcow2 fixture in `tests/manifest.json`, add a test that the probe's failure path fails the operation rather than converting wrongly; if there is not, say so and record it in Future work rather than building a fixture here. Commit subject: `Fail convert when the LUKS inner-format probe cannot read.` |
| 1g | low | sonnet | none | **Close the phase.** Fold this phase's findings back into `PLAN-http-input.md`: record in the Bugs-fixed section what #923 and #924 turned out to be, and add to Future work the two things this phase deliberately left: the VMM installing no logger without `--verbose` (worth its own issue), and #849. Confirm `git diff origin/develop...HEAD -- src/ docs/ tests/ \| grep -nE 'PLAN-\|[Pp]hase [0-9]\|step 1[a-g]'` prints nothing: no plan, phase or step references in landed code, docs or tests. Confirm `docs/guest-architecture.md`'s statement that call-table I/O is synchronous is now true without qualification, and if it needs a sentence to say the wait is unbounded, add it there -- that file is the summary-and-index for the guest, so keep it to a sentence. Do not change the Execution table's status; the next phase's first commit closes this one out. Commit subject: `Record phase 1's findings in the master plan.` |

## Step 1d: the I/O error sites

**The population is larger than the step brief says, and the
correction matters.** "13 sites" was counted with a grep that only
sees a `send_error` written on a single source line. Twelve of those
thirteen are calls (the thirteenth is a doc comment in `measure`), but
the operations raise 37 more from calls rustfmt has broken across
lines, which that grep cannot see. The real population of
I/O-shaped `send_error` calls is 49, across six operations, and
`convert` holds 33 of them rather than four. The four single-line
`convert` sites are a LUKS payload read and three VHD/VHDX header
writes; none of them is the site an ordinary failed input read
reaches, which is `convert_to_raw`'s chain-read failure for raw
output and the matching site in each other writer. Converting only
those four would have left the common case rendering the generic
text.

### The twelve single-line sites

| Operation | Function | Passes | What raises it | What the host does today | Verdict |
|---|---|---|---|---|---|
| copy | `_start` | `"copy"`, `"input"`, 0, 1 | input sector size not a power of two in 512..=64K | nothing: `run_copy` breaks on HLT, logs its "completed successfully" line at debug level and returns `Ok(())` | Not I/O. Left: this is a configuration refusal, not a failed request |
| copy | `_start` | `"copy"`, `"output"`, 0, 2 | output sector size likewise | as above | Not I/O. Left |
| copy | `_start` | `"copy"`, `"input"`, `input_sector`, 1 | `read_input_sector` returned false | as above: exit 0, no message, short output | I/O, and the host drops it entirely -- but the host side is a missing failure, not a vague message, and this phase may not touch `src/vmm/`. Left; see finding 1 |
| copy | `_start` | `"copy"`, `"output"`, `output_sector`, 2 | `write_output_sector` returned false | as above | I/O. Left, same reason |
| check | `_start` | `"check"`, `"input"`, 0, 1 | sector size out of range | `Err("image check failed: no result received")` | Not I/O. Left |
| check | `_start` | `"check"`, `"input"`, 0, 1 | header `read_input_sector` returned false | `Err("image check failed: no result received")` -- a sentence that says what happened, and nothing step 1e renders | I/O, but converting it changes no host output while the decoder ignores the marker. Left |
| convert | `convert_native_luks` | `"convert"`, `"input"`, `read_sector`, 1 | `read_input_sector` returned false | `Err("convert operation failed")` | Converted |
| convert | `convert_to_vhd` | `"convert"`, `"output"`, 0, 2 | footer-copy `write_bytes_to_output` returned false | `Err("convert operation failed")` | Converted |
| convert | `convert_to_vhdx` | `"convert"`, `"output"`, 0, 2 | file-identifier write returned false | `Err("convert operation failed")` | Converted |
| convert | `convert_to_vhdx_fixed` | `"convert"`, `"output"`, 0, 2 | file-identifier write returned false | `Err("convert operation failed")` | Converted |
| info | `_start` | `"info"`, `"input"`, 0, 1 | sector size out of range | `info_without_a_result`: "No info result received from guest" | Not I/O. Left |
| info | `_start` | `"info"`, `"input"`, 0, 1 | header `read_input_sector` returned false | same sentence | I/O, not rendered by step 1e. Left |

### The rest of the population, by operation

| Operation | Sites | What raises them | What the host does today | Verdict |
|---|---|---|---|---|
| convert | 29 further sites, in `convert_to_raw`, `convert_to_qcow2`, `convert_to_qcow2_compressed`, `convert_to_vmdk`, `convert_to_vmdk_compressed`, `convert_to_vhd`, `convert_to_vhdx`, `convert_to_vhdx_fixed` and `convert_native_luks` | a false return from `read_input_sector`, `write_output_sector`, `write_bytes_to_output`, `write_cluster_to_output` or `qcow2::read_chain_virtual_cluster` | `Err("convert operation failed")` -- the only generic failure string in the VMM, as the survey said | Converted, all 29 |
| convert | 2 sites in `convert_to_vhdx` and `convert_to_vhdx_fixed` | `vhdx::layout` rejected the geometry; the device argument carries a reason string, not a device | `Err("convert operation failed")` | Not I/O. Left: the device field is already a human reason, and a marker would discard it |
| copy | 2 further sites in `_start` | read and write failures in the many-to-one and one-to-many sector paths | nothing, as above | I/O. Left with the other two copy sites |
| bench | 3 sites in `run_qcow2_write` and `_start`, device `"read"` / `"write"` | a false return from the chain reader or a write | each is followed by `fail(..., ERROR_IO_READ` or `ERROR_IO_WRITE, ...)`, so the result struct carries the reason and `map_bench_error` renders it by name | Adequate. Left |
| compare | 3 sites in `_start`, device `"input0"` / `"input1"` | a false return from the chain reader | `CompareResult::read_failed` carries the image and the offset, and `compare_failure_error` renders "compare: reading the second image, ref.raw, failed at virtual offset N (0xN); the images were not compared" -- the wording step 1e copies | Adequate. Left |
| check, info, measure | the refusal sites (`DifferencingRefusal`, `VhdFooterChecksumRefusal`) | a named refusal, not I/O | captured and rendered by name already | Not I/O. Left |
| every operation | the `panic` sites, device = the operation name, status `0xDEAD` | a guest panic | rendered through the panic path | Not I/O. Left |

So the survey's claim holds: `convert` is the only operation whose
host side answers an I/O failure with a generic string. `copy` is
worse in a different way, and `check` and `info` answer with a
sentence that says no result arrived. Converting a site whose host
side does not capture the marker would change nothing a user sees,
and step 1e renders only `convert` and `dd`, so everything outside
`convert` is left as it is.

### Findings

1. **`copy` reports a failed read as success.** Every `send_error`
   path in `copy` returns early, skipping the
   `send_complete(..., true)` at the end of its run, and `run_copy`
   neither requires a completion nor inspects one: it breaks on HLT,
   logs "Copy operation completed successfully!" at debug level and
   returns `Ok(())`. A `copy` whose input read fails therefore exits
   0, prints nothing, and leaves a short output. That is a
   success-shaped wrong answer, not a wording problem, and it needs
   its own issue and a host-side fix.
2. **A host read error reaches the guest as a hang, not a status
   byte.** `VirtioBlockDevice::do_read` propagates a
   `BackingStore::read_at` failure with `?`, so `process_request`
   returns `Err` without writing the used ring, and the I/O thread
   discards it (`if let Ok(io_stats) = device.process_queue(...)`).
   Measured: a single injected `EIO` on any read of the input image
   makes `convert` spin forever (three indices tried, each killed at
   25 s), with no error and no completion. The pre-step-1a budget
   would have ended that wait with the misleading "timeout"; now
   nothing ends it. So the I/O-failure marker is raised today only
   when the device *completes* a request with an error status, which
   `validate_io_request` does for an out-of-range request -- the
   reproduction used below. Giving a backing-store error a status
   byte belongs with the VMM work, and it is what makes the marker
   reachable for the case the master plan cares about.
3. **The `status` argument's old convention was redundant and not
   even consistent.** It duplicated `device` (1 for input, 2 for
   output) except in `convert_native_luks`, where an *output* write
   failure passed 1. Every converted site now passes 0, as
   `IoFailure` documents.
4. **Step 1e must keep the refusal branches ahead of the I/O
   branch.** Eleven of the converted sites report a false return from
   `qcow2::read_chain_virtual_cluster`, which fails for a structural
   refusal as well as for a failed request; the refusal markers are
   the specific signal and already come first in `execute_convert`,
   `run_compare` and `run_bench`. Appending the I/O branch after them
   preserves that order. A structural failure with no marker of its
   own will render as an I/O failure, which is the same claim the
   operation already makes when it passes `device = "input"`.

### Reproduction used

With no way to deliver a failed read through the device (finding 2),
the marker was exercised through the completed-but-failed path: a
1 MiB qcow2 whose second L2 entry was repointed to a cluster-aligned
host offset past the end of the file, so `validate_io_request`
answers the guest's read with `IOERR`. Before this step,
`instar -v convert -O raw` on it logged
`[ERROR] error op=convert device=input sector=1 status=1`; after it,
`[ERROR] error op=io-failure device=input sector=1 status=0`. The
user-visible error is unchanged in both
cases -- `Error: "convert operation failed"`, exit 1 -- which is step
1e's half.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| An unbounded wait turns a VMM bug into a hang, in CI or for a user | **Understated as written, and measured during step 1d: it is not only a VMM bug that hangs.** An ordinary `EIO` from the backing store never completes the request at all, so before step 1e's host-side half the wait is indefinite for a failing disk, not just a wedged VMM. Step 1e completes a failed request with an error status, which is what makes the unbounded wait safe. What remains afterwards is the genuinely lost completion, for which the mitigation as written stands: the harness bounds every subprocess (30 s by default in `tests/base.py`), a user can interrupt, and step 1g records a watchdog in Future work to be argued on its own merits |
| Step 1b's test passes everywhere and runs nowhere | The step requires a capability probe rather than a presence check, an environment-variable guard in the style of `INSTAR_REQUIRE_LIBVHDI`, and the tool added where the suite actually runs. The management session checks the claim by running the module with the variable set and unset and confirming the two outcomes differ |
| The `when=` injection index drifts as the tree changes, so the test silently stops injecting into the guest run loop | Step 1b must derive or search for the index rather than hardcoding it, and assert that the injection actually delayed a read -- a test that injects into the wrong syscall and passes is worse than no test. The mutation check in the same step is what proves it is injecting where it matters |
| Converting the 13 `send_error` sites changes a message some test asserts on | Step 1d converts only the sites whose host side drops the detail, and the audit table is produced before any edit. The full Python suite is the check; per the repository's standing rule, a failure is root-caused against `develop` rather than waved through as pre-existing |
| The exit code for a failed convert is guessed and diverges from qemu-img | Decision 5 and step 1e require it to be measured, with the command, version and result written into this plan before the code uses it |
| Step 1a's comment or commit message cites the plan | Explicit in both briefs, and step 1g greps for it across `src/`, `docs/` and `tests/` |

## Definition of done

Each item is falsifiable; the management session checks them before
the push.

1. `grep -rn 'MAX_WAIT_ITERATIONS\|timed_out' src/core/` prints
   nothing, and `grep -rn '"timeout"' src/core/ src/operations/`
   prints nothing.
2. The #923 reproduction -- `strace -f -qq -e trace=read -e
   inject=read:delay_enter=3000000:when=<index> -o /dev/null instar
   convert -O raw <8 MiB qcow2> out.raw` -- exits 0, and `cmp`
   against an uninjected conversion of the same source reports no
   difference.
3. The same reproduction against a build with the spin budget
   restored fails, and step 1b's test fails with it and passes
   without it. Recorded in this plan as a mutation result, naming the
   test.
4. `instar convert` with a genuinely failing read, and **no**
   `--verbose`, prints an error that names the image, names the
   offset, and says the conversion did not happen. The exit code is
   the one step 1e measured from qemu-img, and this plan records that
   measurement with its command and version.
5. The `"convert operation failed"` string is reachable only for
   failures that are not I/O: `grep -n 'convert operation failed'
   src/vmm/src/main.rs` shows it still exists, and the I/O path no
   longer reaches it.
6. `shared::IoFailure`'s C marker and Rust string are asserted equal
   by a unit test, as the six existing markers are.
7. The failed LUKS probe fails the operation: demonstrated by a test
   where one exists, or by a recorded manual injection where a
   fixture does not.
8. Step 1d's audit table is in this plan, covering all 13 sites, with
   a reason for every one left unconverted.
9. `git diff origin/develop...HEAD -- src/ docs/ tests/ | grep -nE
   'PLAN-|[Pp]hase [0-9]|step 1[a-g]'` prints nothing.
10. `pre-commit run --all-files` exits 0, `make test-rust` passes,
    and the Python suite's `test_convert` and `test_compare` modules
    pass with their "Ran: N" lines checked rather than their exit
    codes trusted.
11. #923 and #924 are closed by this phase's pull request body, and
    #849 is not.

## Execution notes

Back brief answered 2026-11-18 (AEDT). Decisions taken there, and
departures from the plan as written, are recorded here as they
happen.

1. The whole step table runs in order, each row as its own
   sub-agent at the row's model and effort, reviewed and committed
   by the management session.
2. Step 1b adds `strace` where the suite runs **and** requires it
   in CI, with the skip gated behind an environment variable in the
   style of `INSTAR_REQUIRE_LIBVHDI`. A missing tool is a CI
   failure, not a silent pass.
3. The gate in the Back brief below is **waived**: the management
   session decides step 1e's error wording and exit code itself and
   reports both afterwards, rather than pausing mid-phase. Decision
   5 still stands -- the exit code is measured from qemu-img, not
   chosen -- and the measurement is recorded in this plan.
4. The LUKS probe defect found by this phase's survey is filed as
   **#926**, so step 1f has an issue to close. Its body carries the
   reasoning that the defect is reachable only through #923, which
   is why fixing #923 would otherwise hide it.
5. **Step 1e grew a second half, and step 1a is incomplete without
   it.** Step 1d measured what a real host read error does, and the
   answer is that it never reaches the guest at all.
   `BlockDevice::do_read` propagates a `BackingStore::read_at`
   failure with `?`, so `process_queue` returns before it writes the
   used ring, and the I/O thread discards the error because it
   matches only `Ok`. No status byte, no used-ring entry, no
   interrupt. While the spin budget existed it ended that wait after
   about 1.3 s and blamed a timeout; with the budget gone nothing
   ends it. Reproduced on this branch: one injected `EIO` on an
   input read hangs `convert` indefinitely, killed at 20 s, where
   the same injection against a `develop` binary exits 1 in one
   second. So the honest reading of the first commit on its own is
   that it trades a misleading failure for a hang.

   Step 1e therefore covers the host completing a failed request
   with an error status before it renders anything, and lands as two
   commits. This is not scope creep: the phase's own definition of
   done requires a genuinely failing read to produce an error that
   names the image, and until the request completes there is nothing
   to name it with. Note for a bisect: the guest-side commit alone
   leaves an `EIO` hanging, so it and the host-side commit are a
   pair.
9. The branch was rebased onto `develop` at `2e8a3022` on 2026-11-19
   (AEDT) after #925 landed, which conflicted twice: the
   multi-extent-VMDK row in `docs/plans/index.md` (five of this
   branch's commits rewrite the row below it) and both sides adding
   a `### Fixed` entry to `CHANGELOG.md`. Both were resolved
   mechanically, keeping `develop`'s row and entry and this
   branch's; `git range-diff` showed no other commit changed.
10. Review round 1, 2026-11-19 (AEDT):
    [PR #929 review](https://github.com/shakenfist/instar/pull/929#pullrequestreview-5956655575),
    0 fix / 1 document / 9 consider / 3 none. Six `consider` items
    were taken as defects in code this phase added -- the errno
    paired with the wrong side of the operation, the wait's exit
    condition, the generic message naming `convert` for `dd`, the
    errno parsing being untested, the used-ring length on a failed
    request, and a fixture skip that an environment declaring
    `INSTAR_REQUIRE_STRACE` could still take silently. Three were
    recorded rather than coded: the unanswerable virtqueue request
    (#932, and a new `docs/guest-architecture.md` section), the
    busy-spin's now-unbounded cost (master plan Future work), and
    index-drift triage (`docs/testing.md`). The `document` item's
    premise was wrong -- `docs/testing.md`'s test-structure list
    names 11 of 35 modules, so it was never the exhaustive index the
    item assumed -- so this phase's modules were added, the list was
    marked as curated, and the real gap is #933.

## Measurements

### qemu-img convert's exit code on a source read error

Measured 2026-11-18 (AEDT) on kasm, qemu-img 10.0.13 (Debian
1:10.0.13+ds-0+deb13u1), against an 8 MiB qcow2 written from
`/dev/urandom`:

```
strace -f -qq -e trace=pread64 -e inject=pread64:error=EIO:when=N \
    -o /dev/null qemu-img convert -O raw src.qcow2 out.raw
```

| when | rc | out.raw | stderr |
|------|----|---------|--------|
| 6 | 1 | 8388608 | `qemu-img: error while reading at byte 6291456: Input/output error` |
| 8 | 1 | 8388608 | `qemu-img: error while reading at byte 0: Input/output error` |
| 10 | 1 | 8388608 | as the first |
| 12 | 0 | 8388608 | injection landed past the last image read |

Three conclusions, which settle decision 5:

* **Exit 1**, which is what instar's `convert` already exits. So this
  phase changes the message and not the code, and `compare`'s exit 4
  is not the precedent to follow -- that is qemu-img *compare*'s own
  read-error code, from a vocabulary `convert` does not share.
* The offset is reported in decimal bytes, with no hex and no sector
  number. instar's `compare` gives both decimal and hex, which is the
  house style this phase follows instead.
* qemu-img **keeps** its output file, at full declared size. That is
  evidence for #849's keep-or-unlink question -- qemu's answer on this
  failure is to keep -- recorded here for whoever takes that issue,
  and not acted on in this phase.

Note for anyone reproducing it: injecting on `read` does not reach
qemu-img's image data at all, because it reads the source with
`pread64` from several threads. #923's recipe is written for instar,
which seeks and reads.

## Mutation results

Each of the phase's three behavioural fixes was shown to fail
without the fix, so that no test in it is passing for a reason other
than the one claimed. All three were measured on 2026-11-18 (AEDT).

**The spin budget**, guarded by
`tests/test_slow_host_read.py::TestSlowHostRead::test_slow_host_read_does_not_fail_convert`.
Proven twice, independently. The implementing sub-agent restored the
budget in a scratch copy of `src/core/src/virtio.rs`, rebuilt, and
recorded the test failing with `rc=1 stderr='Error: "convert
operation failed"'`, then restored the file from its copy and
rebuilt. The management session then reproduced it without a
rebuild, by pointing `INSTAR_BINARY_PATH` at a `develop` build,
which still carries the budget: the test fails at its
`assertEqual(injected.returncode, 0)` with `rc=1`, and passes
against this branch's binary in 4.0 s.

**The unanswered request.** The hang was reproduced before the fix
and timed after it: `strace -f -qq -e trace=read -e
inject=read:error=EIO:when=8` against the branch before the host-side
commit was killed at 20.001 s (`rc=137`), and after it exits 1 in
0.29 s. A `develop` binary exits 1 in about a second at the same
injection, because the budget ended the wait there.

**The LUKS probe**, guarded by
`tests/test_luks_probe_read_failure.py::TestLuksInnerFormatProbeReadFailure::test_failed_probe_read_fails_the_conversion`.
Against a `convert.bin` built from the pre-fix source the test fails
and the conversion *succeeds*: exit 0, with the output beginning
`QFI\xfb` -- the decrypted inner QCOW2 written out as a flat
payload, which is the defect itself rather than a proxy for it.

**Review round 1** (2026-11-19 AEDT) added two mutations, run against
`make test-rust` because a wrapper script and a direct `docker run`
were both refused by this session's tool layer:

* The rendered reason taken from the input side regardless of which
  side the guest named -- `match &side.reason` to
  `match &context.input.reason` in `io_failure_error`. Killed by
  `io_failure_error_does_not_quote_the_other_sides_errno` and by the
  pre-existing `io_failure_error_names_the_output_image_for_a_failed_write`.
* The errno suffix no longer stripped in `record_io_error`. Killed
  only by the new
  `the_recorded_reason_drops_the_errno_number_but_keeps_other_wording`,
  which is the finding: both tests that provoke a real backing-store
  failure produce errors with no `" (os error N)"` suffix (the write
  case is `BackingStore`'s own "write at offset 512 + 512 bytes
  exceeds capacity 1000"), so neither exercised the parsing they were
  nominally about.

Two of the round's changes are deliberately not covered by a test,
which is recorded here rather than left to be rediscovered. The
used-ring length is no longer counted for a failed request, and
nothing reads that field -- the guest never looks at the used
element's length -- so no test can distinguish the fix from its
absence; it is correctness against the virtio spec for a future
multi-request path. And the wait's exit condition now tests that the
used index *moved* rather than that it reached one value, which at
queue depth one is the same condition, so it is hardening for the
depth increase in Future work rather than a behaviour change.

## Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work you
intend to do aligns with that plan.

One gate within the phase: step 1e's wording and exit code are
cheap to propose and expensive to redo once tests assert on them, so
the management session agrees the error sentence and the measured
exit code before step 1e's tests are written.
