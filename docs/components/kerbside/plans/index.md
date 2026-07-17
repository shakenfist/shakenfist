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
| 2026-06-02 | [Automated SPICE test harness](/components/kerbside/plans/PLAN-test-harness/) | End-to-end SPICE test harness driving Uncalibrated Sextant via Ryll's control socket, with assertions against the visual digest and serial drain; replaces the OpenStack-dependent integration tests with a direct qemu/KVM lane | In progress | [phase 1](/components/kerbside/plans/PLAN-test-harness-phase-01-digest-crate/) (Sextant PR pending), [phase 2](/components/kerbside/plans/PLAN-test-harness-phase-02-static-hypervisor/) (done), [phase 3](/components/kerbside/plans/PLAN-test-harness-phase-03-control-socket/) (done), [phase 4](/components/kerbside/plans/PLAN-test-harness-phase-04-port-latency/) (done), [phase 5](/components/kerbside/plans/PLAN-test-harness-phase-05-direct-qemu-ci/) (done), [phase 6](/components/kerbside/plans/PLAN-test-harness-phase-06-digest-decoding/) (done), [phase 7](/components/kerbside/plans/PLAN-test-harness-phase-07-scenario-test/) (done), [phase 8](/components/kerbside/plans/PLAN-test-harness-phase-08-openstack-disposition/) (done) |
| 2026-07-04 | [Rust SPICE proxy (kerbside-proxy)](/components/kerbside/plans/PLAN-rust-proxy/) | Replace the Python SPICE proxy with a Rust kerbside-proxy that talks tonic/gRPC over a UDS to the Python daemon, reuses ryll's shakenfist-spice-protocol crate, enforces L0+L1 firewall policy from day one, and ships inside the kerbside pip install via a maturin bin wheel | Complete | [phase 1](/components/kerbside/plans/PLAN-rust-proxy-phase-01-server-primitives/) (done), [phase 2](/components/kerbside/plans/PLAN-rust-proxy-phase-02-grpc-contract/) (done), [phase 3](/components/kerbside/plans/PLAN-rust-proxy-phase-03-proxy-skeleton/) (done), [phase 4](/components/kerbside/plans/PLAN-rust-proxy-phase-04-firewall/) (done), [phase 5](/components/kerbside/plans/PLAN-rust-proxy-phase-05-daemon-integration/) (done), [phase 6](/components/kerbside/plans/PLAN-rust-proxy-phase-06-packaging/) (done), [phase 7](/components/kerbside/plans/PLAN-rust-proxy-phase-07-ci/) (done), [phase 8](/components/kerbside/plans/PLAN-rust-proxy-phase-08-cutover/) (done) |
| 2026-07-17 | [Backend host_subject enforcement](/components/kerbside/plans/PLAN-host-subject/) | Restore hypervisor certificate subject pinning on the proxy's backend TLS leg, lost in the Rust proxy cutover: enforce spice-common host-subject matching semantics in ryll's shakenfist-spice-protocol verifier, adopt it in kerbside, and prove both accept and refuse paths in the direct-qemu CI lane | In progress | [phase 1](/components/kerbside/plans/PLAN-host-subject-phase-01-ryll-verifier/) (done, ryll PR #166), [phase 2](/components/kerbside/plans/PLAN-host-subject-phase-02-kerbside-adoption/) (done, kerbside PR #114) |

## Standalone plans

| Date | Plan | Intent | Status |
|------|------|--------|--------|
