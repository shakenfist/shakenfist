# Shaken Fist Kasm workspace registry

> **Status: concept plan, parked.** The intent is recorded so it is not
> lost; no phases are drafted. Revisit once the hand-registered ryll
> workspace has seen real use against andris, or when someone besides
> Mikal wants ryll in their own Kasm install.

## Prompt

Before responding to questions or discussion points in this
document, explore the ryll codebase thoroughly. Read relevant
source files, understand existing patterns (SPICE protocol
handling, channel architecture, async task model, image
decompression, egui rendering), and ground your answers in
what the code actually does today. Do not speculate about
the codebase when you could read it instead. Where a question
touches on external concepts (SPICE protocol, QEMU, QXL,
TLS/RSA, LZ/GLZ compression), research as needed to give a
confident answer. Flag any uncertainty explicitly rather than
guessing.

Consult `ARCHITECTURE.md` for the system architecture
overview, channel types, data flow, and code organisation.
Consult `docs/development.md` for build commands, and
`AGENTS.md` for project conventions and a table of protocol
reference sources. Key references
include `shakenfist/kerbside` (Python SPICE proxy with
protocol docs and a reference client),
`/srv/src-reference/spice/spice-protocol/` (canonical SPICE
definitions), `/srv/src-reference/spice/spice-gtk/`
(reference C client), and `/srv/src-reference/qemu/qemu/`
(server-side SPICE in `ui/spice-*`).

<!-- shared-block: plan-file-conventions v2 -->
Plan file conventions (shared block; do not edit -- the canonical
copy lives in shakenfist/development at
`templates/shared-blocks/plan-file-conventions.md`):

- All planning documents live in `docs/plans/`.
- Detailed planning gets one plan file per phase. Phase files are
  named for their master plan, sit in the same directory as it,
  and append `-phase-NN-descriptive` before the `.md` extension.
- The master plan tracks its phases in a table under its Execution
  section. `Merged` is last, and the push audit is the last row,
  for the reasons given in `plan-push-audit-phase`:

  | Phase | Plan | Status | Merged |
  |-------|------|--------|--------|
  | 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started | |
  | 2. Public API | PLAN-thing-phase-02-api.md | Not started | |
  | 3. Push audit | - | Not started | |

- One commit per logical change, and at minimum one commit per
  phase. Unrelated changes are not batched into a single commit.
  Each commit is self-contained: it builds, passes tests, and has
  a message explaining what changed and why.
<!-- shared-block-end -->


The Kasm side is documented by Kasm Technologies; the registry format
is defined by `kasmtech/workspaces_registry_template` on GitHub. The
linuxserver.io registry (`https://kasmregistry.linuxserver.io/`) is a
working third-party example. Read `docs/kasm-workspace.md` for how the
ryll workspace is built and configured today.

## Situation

`make kasm-image` builds a Kasm Workspaces image of the ryll GUI
(`tools/kasm/`), and `docs/kasm-workspace.md` explains registering it
by hand in the Kasm admin UI. That works on a single-server Kasm whose
agent already holds the locally built image. It does not get ryll to
anyone else: there is no published image, and every install repeats
the hand registration (image, cores and memory, launch form, Docker
Run Config Override).

Kasm can instead install workspaces from a *workspace registry*: a
static site serving a `list.json` of entries, added once in the admin
UI, after which each workspace is an Install button and new releases
arrive as updates. Read on 2026-10-23 from the linuxserver.io registry
(schema 1.1), an entry carries `friendly_name`, `description`,
`categories`, `image_src` (icon), `architecture`, `docker_registry`,
`run_config`, `exec_config`, and `compatibility` (per Kasm version:
`version`, `image`, `uncompressed_size_mb`).

## Mission and problem statement

Publish ryll as a one-click Kasm workspace:

1. A release job that builds the Kasm image from the released `.deb`
   and pushes it to a public registry (likely
   `ghcr.io/shakenfist/ryll-kasm`), for amd64 and arm64, with the same
   supply-chain care as the other release artefacts.
2. A registry repository from Kasm's template (likely
   `shakenfist/kasm-registry`, served on GitHub Pages), with a ryll
   entry and icon.
3. Documentation of adding the registry and installing ryll, in
   `docs/kasm-workspace.md`.

Site-specific settings stay out of scope and out of the public entry:
a SPICE server's address and a volume mapping of its ticket file
belong to one installation, not to the registry.

## Open questions

- **Does a registry install carry `launch_config`?** Kasm's API knows
  the field, but no linuxserver.io entry uses it, and nobody has
  checked that installing from a registry copies it into the
  workspace. Without it, the connection dialog
  (`tools/kasm/launch_config.json`) is still a hand step.
- **Image tags and Kasm versions.** The image pins its Kasm base image
  (`KASM_BASE` in `tools/kasm/Dockerfile`), and the base must match the
  devcontainer's Debian release. How do ryll releases and Kasm
  releases map onto image tags and `compatibility` rows?
- **One repository or two.** A separate registry repository is what
  Kasm's template expects, but it falls under the Shaken Fist
  consistency audits. Could it live in an existing repository instead?
- **Signing.** Should the image be signed (for example with cosign),
  as the release tags are?
- **Pinning the base by digest.** `KASM_BASE` names a mutable tag,
  which is fine for a hand-built image. A published one should pin the
  base by `@sha256:` digest and let Renovate bump it.

## Execution

No phases are drafted. When this is picked up, the first phase is
likely the GHCR image job, which is useful on its own: a hand-
registered workspace can pull a published image too.

| Phase | Plan | Status | Merged |
|-------|------|--------|--------|
| (phases not yet written) | - | Not started | |
| Push audit | - | Not started | |

## Administration and logistics

### Success criteria

- A Kasm administrator with no ryll checkout adds the Shaken Fist
  registry, installs ryll, launches it, and connects to a SPICE
  server through the launch form.
- A new ryll release appears as an update in that Kasm install
  without anyone building an image by hand.

### Future work

- A ryll-native connect dialog (shown when ryll starts with no
  connection source) would make the workspace usable without
  Kasm's launch form, and would help desktop users too.

### Bugs fixed during this work

None yet.

### Back brief

Not yet planned.
