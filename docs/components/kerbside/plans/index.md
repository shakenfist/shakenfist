# Plans index

This page summarises every planning document in chronological order.
Master plans decompose work into numbered phases, each with its own
detailed plan file. Standalone plans track issues, follow-ups, or
design decisions that do not require phased execution.

New plans should follow the structure in `PLAN-TEMPLATE.md` at the
repo root. For pre-push audits of our own work see
`PUSH-TEMPLATE.md`.

## Master plans

| Date | Plan | Intent | Status | Phases |
|------|------|--------|--------|--------|
| 2026-06-02 | [Automated SPICE test harness](/components/kerbside/plans/PLAN-test-harness/) | End-to-end SPICE test harness driving Uncalibrated Sextant via Ryll's control socket, with assertions against the visual digest and serial drain; replaces the OpenStack-dependent integration tests with a direct qemu/KVM lane | Not started | (phase plans pending) |
| 2026-07-04 | [Rust SPICE proxy (kerbside-proxy)](/components/kerbside/plans/PLAN-rust-proxy/) | Replace the Python SPICE proxy with a Rust kerbside-proxy that talks tonic/gRPC over a UDS to the Python daemon, reuses ryll's shakenfist-spice-protocol crate, enforces L0+L1 firewall policy from day one, and ships inside the kerbside pip install via a maturin bin wheel | In progress | [phase 1](/components/kerbside/plans/PLAN-rust-proxy-phase-01-server-primitives/) (done), [phase 2](/components/kerbside/plans/PLAN-rust-proxy-phase-02-grpc-contract/) (done), [phase 3](/components/kerbside/plans/PLAN-rust-proxy-phase-03-proxy-skeleton/) (done) |

## Standalone plans

| Date | Plan | Intent | Status |
|------|------|--------|--------|
