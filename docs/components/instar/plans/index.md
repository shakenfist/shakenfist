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
| 2026-05-09 | [Release v0.2.0](/components/instar/plans/PLAN-release-v0.2/) | Cut the v0.2.0 tag and publish signed GitHub Release artifacts (tarball, .deb, .rpm) for x86_64 Linux | Drafted, not started | (no phase files; sequential gates) |
