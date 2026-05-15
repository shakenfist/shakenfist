# Plans index

This page summarises every planning document in chronological order. Master
plans decompose work into numbered phases, each with its own detailed plan
file. Standalone plans track issues, follow-ups, or design decisions that
do not require phased execution.

New plans should follow the structure in `PLAN-TEMPLATE.md` at the repo
root. For pre-push audits of our own work see `PUSH-TEMPLATE.md` (also
at the repo root).

## Master plans

| Date | Plan | Intent | Status | Phases |
|------|------|--------|--------|--------|
| 2026-05-08 | [Distro matrix CI](/components/instar/plans/PLAN-distro-matrix-ci/) | Run instar's full functional test suite against installed `.deb`/`.rpm` packages on a representative matrix of Linux distributions in the GitHub merge queue, with qemu-img differential coverage | Drafted, not started | (phases not yet written; design blocks pending) |
| 2026-05-09 | [Release v0.2.0](/components/instar/plans/PLAN-release-v0.2/) | Cut the v0.2.0 tag and publish signed GitHub Release artifacts (tarball, .deb, .rpm) for x86_64 Linux | Complete (tagged 2026-05-09) | (no phase files; sequential gates) |
| 2026-05-10 | [First public release of instar](/components/instar/plans/PLAN-release/) | Cargo.toml metadata, release workflow, .deb/.rpm packaging, and signing for instar's public releases (umbrella plan; v0.2.0 execution lives in `PLAN-release-v0.2.md`) | In progress (phases 1-4 complete through v0.2.0; phase 5 audit mostly done; phase 6 coverage fuzzing in progress) | (phases inline) |
| 2026-05-10 | [Security audit](/components/instar/plans/PLAN-audit/) | Sweep instar for security weaknesses across the host VMM, KVM guest, call-table boundary, and format parsers, including coverage-guided fuzzing | In progress (phases 1a-5 done; phase 6 coverage fuzzing in progress) | (phases inline) |
| 2026-05-10 | [Coverage-guided fuzzing](/components/instar/plans/PLAN-coverage-fuzzing/) | Stand up coverage-guided fuzzing across the format parsers and run sustained campaigns | In progress (steps 1-5 infrastructure merged; extended runs not yet complete) | (phases inline) |
| 2026-05-10 | [Fuzz autofix workflow](/components/instar/plans/PLAN-fuzz-autofix/) | Workflow that triages fuzzer-discovered crashes and proposes minimal fixes | In progress (workflow scaffolding merged; not yet exercised end-to-end) | (phases inline) |
| 2026-05-10 | [Convert follow-ups](/components/instar/plans/PLAN-convert-followups/) | Track the deferred work from the (now-removed) convert master plan: extra `qemu-img` subcommands (create / map / measure / resize / snapshot / rebase / commit) and `check --repair` wiring | Not started | 1: subcommand parity, 2: check --repair |
| 2026-05-10 | [`instar measure` subcommand](/components/instar/plans/PLAN-measure/) | Implement the `measure` subcommand (qemu-img parity for raw and qcow2 outputs; instar extensions for vmdk / vhd / vhdx) with cross-version baselines, integration tests, coverage-guided fuzzing, and differential fuzzing | Complete (phases 1-10) | 1: calculators, 2: allocation scanners, 3: guest op, 4: host CLI, 5: target options, 6: baselines, 7: integration tests, 8: coverage fuzz, 9: differential fuzz, 10: docs |
