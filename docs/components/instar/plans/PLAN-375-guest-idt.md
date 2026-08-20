# PLAN-375: A guest IDT so codegen miscompiles fail loudly, not silently

## Status: Complete

Landed 2026-07-25 in `a938e4b`, together with this plan document:
`src/core/src/idt.rs` (IDT, 32 exception stubs, common handler),
`idt::install()` as the first step of `_start`, and the host-side
`cpu-exception` capture in `src/vmm/src/main.rs`. Validation is
recorded below.

Issue #375 stays open by design. The `#[inline(never)]` attributes
remain the primary defence and this plan is the safety net that makes
their failure observable; the issue closes only if the underlying LLVM
miscompile is reproduced and root-caused upstream.


## Prompt

"Let's find a permanent fix for #375 instead of a workaround." Issue #375
tracks the guest ops' fragile dependence on `#[inline(never)]` to dodge an
`opt-level=z`+`lto` control-flow miscompile on `x86_64-unknown-none`: with
the runner inlined into the `extern "C"` `_start`, the guest historically
jumped mid-instruction, hit an invalid opcode (`#UD`), and — with no IDT —
triple-faulted before doing any work.

## Investigation (what the fix is *not*)

Two hypotheses were tested and discarded before landing the real fix:

1. **SSE with `CR4.OSFXSR` disabled.** The guest boots with only
   `CR4.PAE` set, so an SSE instruction would `#UD`. But
   `x86_64-unknown-none` builds with `+soft-float`: every guest op binary
   disassembles to **zero** `xmm` instructions. `CR4.OSFXSR` is
   irrelevant. Discarded.

2. **The miscompile still reproduces.** It does not, on nightly
   `1.99.0 (2026-07-23)`. Forcing the exact historical worst case —
   `#[inline(always)]` on amend's `run_qcow2`, which genuinely inlines it
   (the symbol disappears; `_start` grows to ~1.7 KB) — still runs
   correctly. The miscompile is a genuine but **intermittent** LLVM
   control-flow bug that the current toolchain does not exhibit. The
   `#[inline(never)]` attributes are therefore non-load-bearing *today*,
   but the fragility (a future nightly resurfacing it) is real, and a
   recurrence would be a silent triple fault.

Because the bug will not reproduce, "just remove the workaround" cannot be
validated. The permanent fix instead makes any recurrence **loud**.

## The fix

Install a minimal IDT in the guest `core` (`core/src/idt.rs`), as the very
first step of `_start`, covering the Intel exception vectors `0..=31`.
Each gate points at an assembly stub that normalizes the CPU stack frame
(pushing a dummy error code for the vectors that don't push one) and
tail-calls a Rust handler, which reports the vector and faulting RIP to
the host over the same serial `error` channel the panic handler already
uses, then halts.

Effect: a guest CPU exception that previously escalated to a triple fault
(surfaced only as an opaque `VcpuExit::Shutdown`, "possible triple fault")
is now a clean, described failure. Host-side (`vmm`), the `SerialDecoder`
captures the reported `cpu-exception`, and the eight guest-mutation ops'
"guest did not return a result" error is enriched to, e.g.:

```
amend: guest CPU exception: invalid opcode (#UD) at guest RIP 0x3002c
```

Interrupts stay masked (`RFLAGS.IF = 0`, no interrupt controller), so only
synchronous exceptions reach the handlers — no IRQ plumbing is needed. The
IDT install runs on every guest boot; a broken install would fail the
whole integration suite.

## Files

- `src/core/src/idt.rs` — new: IDT, 32 exception stubs, common handler.
- `src/core/src/main.rs` — `mod idt;` + `idt::install()` first in `_start`.
- `src/vmm/src/main.rs` — `exception_name()`, `SerialDecoder`
  capture + `no_result_error()`, enriched `format_message()`, eight
  no-result sites, and unit tests.

## Validation

- `make test-rust`: 2078 passed, 0 failed (incl. 3 new `vmm` unit tests).
- `make lint`: clean (no warnings from the new code).
- Integration sweep across every op family: 2565 passed, 0 failed
  (known baseline/oslo skips only).
- Fault path: a deliberate `ud2` injected into an op is caught and
  reported as `cpu-exception … invalid opcode (#UD)` with the faulting
  RIP, non-verbose and verbose — no triple fault. (Confirmed both by a
  targeted injection and, incidentally, across dozens of real amend cases
  when a stale injected binary was run.)

## Out of scope / follow-ups

- **The `#[inline(never)]` attributes stay.** They remain the primary
  defense; the IDT is the safety net that makes their failure observable.
  Removing them is deferred until (if ever) the miscompile can be
  reproduced and root-caused upstream.
- **A gated fault-injection integration test** was considered for a
  standing end-to-end regression guard but not built: the VMM's guest
  setup is duplicated per-op and lacks a shared injection point, so the
  plumbing cost outweighed the value given the manual validation above.
- **Devcontainer build is broken on the current nightly** (unrelated to
  #375): `cargo install cargo-audit` ICEs compiling tokio on nightly
  `2026-07-23`, failing the "Build devcontainer" CI step. Tracked
  separately; pin the nightly to unblock.
