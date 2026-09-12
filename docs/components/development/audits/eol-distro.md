# Audit: End-of-life distributions

## What we check

An operating system release reaches the end of its support on a
published date. Everything built on it inherits that, and nothing
about it breaks: a CI job pinned to a retired runner image keeps
passing, a container built `FROM` a retired base keeps shipping, and
the only signal that either has happened is somebody remembering the
date.

This criterion is that memory written down. A retired release is
listed once, in `EOL_RELEASES` in
`scripts/audit/checks/distros.py`, and from the next morning's run
every repository still building on it fails until it moves.

### The retired list

Two of these three dates end *standard* support rather than every
fix: Debian hands a release to the LTS team and Ubuntu moves an LTS
to ESM, so a machine running one is not instantly unpatched. It is
being patched by fewer people, for fewer packages, under a policy
nothing here tracks -- and on a subscription, in Ubuntu's case. The
fleet's rule is to move at end of standard support rather than ride
the extension, so those are the dates the table carries, and the
"then" column says what each one hands over to.

| Release | Support ended | Then | Runner labels | Replace with |
|---------|---------------|------|---------------|--------------|
| Ubuntu 20.04 LTS (focal) | 2025-05-31 | Ubuntu Pro ESM | `ubuntu-20.04`, `ubuntu-2004` | `ubuntu:24.04` (noble) |
| Debian 12 (bookworm) | 2026-06-10 | Debian LTS | `debian-12`, `debian-12-docker`, `debian-gnome-12` | `debian-13`, `debian-13-docker`, or a `debian:13` (trixie) image |
| Debian 11 (bullseye) | 2026-08-31 | nothing, this is end of LTS | `debian-11`, `debian-11-docker` | as above |

Moving a runner label is a two-file change. The repository's
`.github/actionlint.yaml` declares the self-hosted labels its
workflows may name, and actionlint fails a workflow naming one that
is not declared -- so the replacement goes into that list in the same
commit, or the fix trades an audit finding for a lint failure. The
finding text says so too, because that list is per repository: every
project this criterion files against makes the same two-file change,
and describing it as a one-line one costs each of them the same
rediscovery.

Adding the next release is one entry in that table and one row here.
It is deliberately not a new check: the reason Debian 12 lingered in
fifteen repositories is that noticing was a person's job, and a
criterion that has to be re-invented per release is the same job
wearing a hat.

An entry may be written before its date arrives, which is the normal
way to plan a migration. The end-of-life date is compared against
today, and a release whose date is still in the future is recognised
but not reported -- otherwise an entry added a quarter early fails
every repository at once, with an issue whose own text says the
release goes end of life next quarter.

`debian-gnome-12` is listed although the CI conductor advertises no
`debian-gnome-13` label yet. The guest image it is built from exists --
[images](https://github.com/shakenfist/images) has built
`debian-gnome:13` since August 2026 -- so what is missing is an entry
in [private-ci](https://github.com/shakenfist/private-ci)'s
`IMAGE_BUILDS` table, not an image. A finding naming it is therefore
a request there rather than a one-line edit in the repository the
issue lands on. It is
listed anyway, because leaving the fleet's one remaining bookworm
runner off would make this page claim Debian 12 was gone when it was
not.

### Where we look

Two surfaces, because these are the two ways a repository depends on a
release mechanically rather than by mentioning it.

**Runner labels**, anywhere in `.github/workflows/*.yml`. Every line
is scanned rather than only `runs-on:` lines, so a matrix value
feeding `runs-on: ${{ matrix.os }}` is caught too, and a match counts
only where YAML could put a value -- after a key, inside a `[...]`
list, or as a `- ` item. A label is matched as a whole token, so
`debian-12` in `debian-12-genericcloud-amd64.qcow2`,
`kolla-ansible-master-debian-12` or `debian-12-gnome-agents` is not a
runner reference and is not reported.

**Container image references**, in a workflow's `container:` and
`image:` keys and in the `FROM` lines of any `Dockerfile`,
`Dockerfile.*`, `*.dockerfile` or `Containerfile` in the tree. The tag
is read as hyphen-separated tokens, which is how the upstream images
spell their variants, so `debian:bookworm-slim`, `rust:slim-bookworm`
and `rust:1.97-bookworm` are all recognised as Debian 12. A bare
version number is only read when the image *is* the distribution:
`debian:12` is bookworm, `rust:1.12` is a Rust release. A reference
with no tag, or one behind a shell or Actions expression, is not
judged.

A codename is read as its release in *any* image's tag, not only the
distribution's own. That is what catches `rust:1.97-bookworm`, and it
equally catches `myapp:bookworm` or `internal/tool:bullseye-builder`.
The criterion does not try to tell a distribution's own image from
something built on one, because it cannot: a project that publishes
artifacts derived from a retired release marks those lines
`audit-ok: eol-distro` rather than the check guessing which side of
the line they are on.

**Workflow templates**, every `.yml` and `.yaml` under a top-level
`templates/` directory. Only shakenfist/development has one, and what
is in it is copied verbatim into ten other repositories -- so a
retired label there files a finding against every project that adopted
the template while the source of the violation goes on passing, and a
repository adopting it tomorrow fails the audit on arrival. The
templates' `README.md` files discuss labels in prose and are not
scanned, for the same reason a plan describing a 2025 bookworm build
is not.

Vendored trees, build output and virtualenvs are skipped when walking
for container build files and templates, on the same reasoning as the
fuzz criteria: a `Dockerfile` under `vendor/` or `target/` belongs to
a dependency.

A `container:` or `image:` line is read with any trailing comment
stripped, so `image: debian:12  # renovate pin` is a finding. A
`FROM` line's leading `--flag` and `--flag=value` tokens are skipped,
so `FROM --platform=$BUILDPLATFORM debian:12` is one too.

### What this does not cover

* **Guest images and cached disks.**
  `images.shakenfist.com/ubuntu:20.04` is an instance template a
  functional test boots, and
  `/srv/ci/cached/debian-12-gnome-agents` is a disk a job copies.
  Both are real dependencies on an old release, and neither is
  decidable from a grep of the consuming repository -- the fix lives
  in [images](https://github.com/shakenfist/images) or in the CI
  conductor. They are the pre-push reviewer's to raise.
* **Upstream job names.** `kolla-ansible-master-debian-12` names a
  job in somebody else's gate. We do not get to choose its platform.
* **Images named in shell.** The image surface is the `container:`
  and `image:` keys and Dockerfile `FROM` lines, each matched on the
  key at the start of a line, so `docker run --rm debian:12` inside a
  `run:` block -- the commonest way a workflow actually uses a
  container -- is not matched. The asymmetry with the runner labels,
  which are deliberately not read inside `run:` blocks, runs the other
  way too: an `image: debian:12` line a `run:` block writes into a
  compose file by heredoc *is* reported. Neither is an accident worth
  closing with a shell parser. The one that is reported is a true
  dependency on a retired release however it got there, and the one
  that is missed would cost more in false positives than it found.
* **Prose.** A plan describing what was done on bookworm in 2025 is
  history, and rewriting history to please an audit is worse than the
  audit not running.
* **Whether the replacement is the *right* one.** The table says what
  to move to; it cannot tell that a job needs `xl` on trixie where
  `l` sufficed on bookworm.

### Overlap with the runner criteria

`ubuntu-20.04` is both a retired release and a GitHub-hosted runner
label, so a workflow naming it fails this criterion *and*
[workflow-standards](/components/development/audits/workflow-standards/)'s self-hosted runners
check. Both findings are true and have different fixes -- one is about
GitHub minutes, the other about security updates -- so neither
suppresses the other.

### Marking a deliberate exception

A repository whose subject matter *is* old distributions has a real
reason to keep one. [instar](https://github.com/shakenfist/instar)
boots a `debian:12` container in its functional tests because
measuring what a bookworm filesystem looks like is the job.

Mark the line, or the line above it, with the reason:

```yaml
          # audit-ok: eol-distro -- test input, we measure old images
          - image: 'debian:12'
```

The marker is the same shape as the `audit-ok: github-hosted-runner`
and `audit-ok: vm-runner-size` markers the runner criteria use.

## Template

No template -- this is a property of a repository's workflows and
container build files, not a file to install.

## Projects

Per-project compliance is regenerated every morning by the consistency
audit: see [the compliance page](/components/development/audits/compliance/#eol-distro).
