# Convert follow-ups: deferred work after PLAN-convert.md

## Prompt

Before responding to questions or discussion points in this
document, explore the instar codebase thoroughly. Read relevant
source files, understand existing patterns (VMM structure, guest
operation layout, shared crate conventions, call table ABI,
format parsing, test infrastructure), and ground your answers in
what the code actually does today. Do not speculate about the
codebase when you could read it instead. Where a question touches
on external concepts (QCOW2, VMDK, VHD/VHDX, LUKS, KVM, virtio,
disk image formats), research as needed to give a confident
answer. Flag any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the overall system structure
(host VMM, KVM guest, call table, device emulation).
Consult `AGENTS.md` for build commands, project conventions,
code organisation, and the security model summary. Consult
`docs/` for format-specific documentation (`docs/qcow2/`,
`docs/raw/`, etc.) and `docs/commentary/` for architectural
decisions and design rationale.

When we get to detailed planning, I prefer a separate plan
file per detailed phase, named
`PLAN-convert-followups-phase-NN-descriptive.md`.

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit.

## Situation

The original `PLAN-convert.md` (now removed) and its phase plans
delivered `instar convert` plus the `info`, `check`, and
`compare` subcommands across QCOW2, VMDK (sparse and flat,
including multi-extent and backing chains), VHD, VHDX, and raw,
through Phase 24 (extended L2 subcluster-level I/O).

A small number of items were explicitly deferred rather than
descoped. This plan exists so they are not forgotten when the
parent plans are deleted.

## Mission and problem statement

Track and eventually deliver the deferred work from the convert
effort:

1. **Additional `qemu-img` subcommands** — seven subcommands that
   round out feature parity with `qemu-img`. None are required
   for current users (the existing `convert` / `info` / `check`
   / `compare` set covers the v0.2.0 use cases) but each is a
   reasonable extension once there is demand.

2. **`check --repair`** — `CheckConfig::FLAG_REPAIR` is reserved
   in the call-table ABI but not wired to any repair logic. The
   `check` command currently only reports errors.

## Open questions

- Is there demand for any specific subcommand from the seven? If
  not, this plan can stay deferred indefinitely; if there is,
  prioritise that one and split it into its own phase plan.
- For `--repair`, what is the safety model? QCOW2 repair touches
  refcount and L1/L2 tables; we need to decide whether repair
  runs in the guest (consistent with the rest of the call-table
  model) and whether dry-run / backup-first is mandatory.

## Execution

| Phase | Plan | Status |
|-------|------|--------|
| 1. `qemu-img` subcommand parity (create / map / measure / resize / snapshot / rebase / commit) | PLAN-convert-followups-phase-01-subcommands.md (not yet written) | Not started |
| 2. `check --repair` wiring | PLAN-convert-followups-phase-02-check-repair.md (not yet written) | Not started |

Subcommands in phase 1 should be split into one phase plan each
once any one of them is scheduled — they share no implementation
beyond the existing `instar` subcommand scaffolding, so bundling
them into a single phase plan would be artificial.

### Subcommand reference

For phase 1 detail planning, the original scope notes from
`PLAN-convert.md`:

- **create** — Create new empty disk images. Raw via host-side
  truncate; QCOW2 / VMDK / VHD / VHDX via guest operation.
- **map** — Display allocation map. Reuses format parsing,
  reports contiguous extents with start / length / depth / zero
  / data / offset.
- **measure** — Pre-calculate space requirements for conversion.
- **resize** — Change virtual size. Raw via host truncate;
  QCOW2 / VMDK / VHD / VHDX via guest L1/BAT extension.
- **snapshot** — List, apply, create, delete internal QCOW2
  snapshots.
- **rebase** — Change backing file references. Unsafe
  metadata-only mode and safe data-aware mode.
- **commit** — Commit overlay changes into backing file.

## Agent guidance

### Execution model

All implementation work is done by sub-agents, never in the
management session. The management session is reserved for
planning, review, and decision-making.

The workflow is:

1. **Plan** at high effort in the management session.
2. **Spawn a sub-agent** for each implementation step.
3. **Review** the sub-agent's output — read the actual files,
   don't trust the summary.
4. **Fix or retry** if the output is wrong.
5. **Commit** once the management session is satisfied.

### Planning effort

Phase plans for new subcommands should be written at **high
effort** — each touches the call table and a new guest
operation, so understanding the existing patterns matters.

`check --repair` is also high effort: refcount and metadata
repair is subtle and easy to corrupt further.

The security-tests phase is medium effort if the fixtures
already exist, high effort if they need authoring.

## Administration and logistics

### Success criteria

For each phase as it is executed:

* `make instar` builds and `make lint` is clean.
* Guest binaries pass `make check-binary-sizes` (384KB limit).
* `make test-rust`, `make test-integration`, and the relevant
  cross-validation against `qemu-img` all pass.
* `pre-commit run --all-files` passes.
* New subcommands are documented in `docs/usage.md` and the
  `instar --help` output.
* `CHANGELOG.md` is updated.

### Future work

Items beyond the three phases above:

- If `qemu-img amend` ever becomes useful (changing image
  options after creation, e.g. cluster size), add it as a
  fourth phase.
- If `--repair` proves useful, consider extending it to VMDK /
  VHD / VHDX as well; QCOW2 is the natural starting point
  because it has the richest metadata to repair.

### Documentation index maintenance

This plan is registered in `docs/plans/index.md` and
`docs/plans/order.yml`. Phase files, when created, should be
linked from the Execution table here but should *not* be added
to `order.yml`.

### Back brief

Before executing any step of this plan, please back brief the
operator as to your understanding of the plan and how the work
you intend to do aligns with that plan.
