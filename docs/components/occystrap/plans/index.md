# LLM planning documents

I am playing with a more formal style of planning for LLM assisted
coding sessions. This directory contains plans negotiated by myself and
a LLM before implementation starts. New plans start from
`PLAN-TEMPLATE.md` at the repository root, and a plan that is not listed
here is invisible: registering it is part of writing it rather than a
tidy-up afterwards.

Master plans decompose their work into numbered phases. Where the
phases are large enough to want their own documents they are named for
their master plan with `-phase-NN-descriptive` appended, and they are
tracked in the master plan's Execution table rather than listed here.

The `Status` column holds exactly one term from the shared vocabulary in
`PLAN-TEMPLATE.md`: `Proposed`, `Not started`, `In progress`, `Blocked`,
`Complete`, `Abandoned` or `Superseded`. Anything a reader needs beyond
that term belongs in the plan file, with a one line summary in `Intent`.

## Master plans

| Date | Plan | Intent | Status | Phases |
|------|------|--------|--------|--------|
| 2026-02-07 | [Refactor developer automation workflows to shared actions](/components/occystrap/plans/PLAN-workflow-refactor/) | Take the PR bot workflows from imago's copies to shakenfist/actions, with occystrap as the first consumer | Complete | Phases in the plan; the comment addresser it added has since been retired fleet-wide |
| 2026-02-14 | [Implementing `info` and `check` subcommands](/components/occystrap/plans/PLAN-info-check/) | Report what an image contains, and verify that a copy of one is complete and intact, with text and JSON output | Complete | Steps 1-8 in the plan |
| 2026-02-17 | [A more structured and less verbose approach to logging](/components/occystrap/plans/PLAN-structured-logging/) | Make INFO mean milestones, adopt `with_fields()` summaries, and keep it that way with a pre-commit log level check | Complete | Six commits, summarised in the plan |
| 2026-02-26 | [Registry proxy mode](/components/occystrap/plans/PLAN-registry-proxy/) | Run occystrap as a long-lived filtering registry so a batch build can push through it | Complete | 1. Persistent proxy, 2. Concurrent images, 3. Pull-through |
| 2026-03-23 | [Quay.io tag-based bulk image discovery and download](/components/occystrap/plans/PLAN-quay-label-search/) | Expand a `quay://` URI into every matching repository in an organization and process them all | Complete | 1. API client, 2. URI and input, 3. Commands, 4. Tests and docs, 5. Since filter |
| 2026-03-24 | [Make the speed: occystrap performance overhaul](/components/occystrap/plans/PLAN-make-the-speed/) | Replace requests with httpx and make resolution, image processing and output I/O concurrent | Complete | 1. httpx, 2. Parallel resolution, 3. Multi-image concurrency, 4. Parallel output I/O, 5. Benchmarking, 6. Processing summary |
| 2026-03-25 | [Post-write verification for output integrity](/components/occystrap/plans/PLAN-post-write-verification/) | Read back what each output writer produced and confirm it matches the image that was fetched | Complete | 1. Framework and DirWriter, 2. Tar and Docker writers, 3. Registry writer, 4. Docs and functional tests |
