# Testing

How Kerbside is tested: running the suite locally, the CI lanes, the
Ryll-based harnesses, the oVirt console probe, the Tempest plugin, and
the load-test container images.

## Running the test suite

```bash
tox -e py3      # unit tests
tox -e flake8   # style checks on changes since HEAD~
tox -e cover    # coverage report into cover/
tox -e bindep   # OS dependency check
```

`py3`, `flake8` and `cover` are the default `envlist`, so a bare `tox`
runs all three.

Test locations:

- Unit tests: `kerbside/tests/unit/`
- Functional tests: `kerbside/tests/functional/`
- Tempest plugin: `tempest-plugin/kerbside_tempest_plugin/` (a separate
  releasable, driven via `tools/run-tempest-tests` and the
  `openstack_matrix` job in `.github/workflows/functional-tests.yml`)
  - `tests/api/test_spice_via_kerbside.py` — OpenStack lane only;
    requires a live cloud.
  - `tests/scenario/test_sextant_scenario.py` — direct-qemu lane; see
    [Sextant scenario test](#sextant-scenario-test-direct-qemu-lane).

## CI tiers

Develop is protected by a GitHub merge queue and the lanes are split
into two tiers.

- **The smoke tier** runs on every pull request push: unit tests and
  lint, the direct-qemu lane, and the Shaken Fist end-to-end lane.
  Between them these deploy the PR's own code and relay real SPICE
  traffic through the real proxy, so a PR still gets end-to-end
  signal in tens of minutes rather than hours.
- **The merge tier** runs only in the merge queue: the oVirt and
  OpenStack cloud matrices, each of which builds an entire cloud
  from scratch. These are the expensive lanes, and their failure
  modes are dominated by upstream environment churn rather than by
  Kerbside regressions, so running them per PR bought little for
  what it cost.

The trade is deliberate: a cloud-specific breakage now surfaces in
the merge queue rather than on the PR, blocking the queue and
costing a rerun.

Both tiers also skip entirely when a change touches only files no
lane exercises: the review-tracking state (`REVIEWS.md`, the
`.vscode` weaudit files and review scope) and `docs/`. Each gating
workflow carries a `check_paths` filter job whose output the heavy
jobs skip on — a filter job rather than trigger-level
`paths-ignore`, because a required check that never reports blocks
the merge forever while a skipped one satisfies it (see the
comments in `functional-tests.yml`). The filter list is duplicated
in the three `check_paths` jobs and in `codeql-analysis.yml`'s
trigger-level `paths-ignore` (safe there because CodeQL is not a
required check); keep the copies in sync. In the merge queue only
`functional-tests.yml` runs its filter, so a review-marks-only or
docs-only queue entry skips the cloud matrices too — confirmed
live on the queue entry for PR 304.

| Workflow | Runs on | Tier |
|----------|---------|------|
| `functional-tests.yml` (`sanity_checks`) | pull_request, merge_group | smoke |
| `functional-tests.yml` (`ovirt_matrix`, `openstack_matrix`) | merge_group, workflow_dispatch | merge |
| `direct-qemu-functional.yml` | pull_request, merge_group, nightly | smoke |
| `sf-e2e-functional.yml` | pull_request, merge_group, nightly | smoke |
| `rust.yml` | push and pull_request, path-filtered to `rust/**` and the proto | neither (advisory) |
| `dev-proxy-wheel.yml` | push to develop, path-filtered to the proxy binary's inputs; workflow_dispatch (dry-run by default) | neither |
| `codeql-analysis.yml` | push, pull_request, weekly | neither |
| `prune-reviews.yml` | push to develop | neither |
| `pin-indirect-dependencies.yml` | daily, and on PRs touching the pinning script | neither |

`rust.yml` is advisory rather than gating. Rust breakage still
blocks merges, because the proxy wheel is built by the direct-qemu
lane in the smoke tier and by both cloud matrices in the merge
tier; what never runs against the merged tree is `clippy` and
`cargo test`, which is an accepted gap.

The direct-qemu lane also runs nightly, because the merge queue
does not re-run it against the merged tree.

Two behaviours only matter when driving CI by hand: on a
`workflow_dispatch` run of `functional-tests.yml` an unselected target
skips cleanly via a job-level `if:` (it does not report red), and
instance readiness in the `shakenfist/actions` provisioning playbook
gates on cloud-init completion, not just an open SSH port.

### Gate jobs and required checks

Branch protection cannot require "whatever ran"; it requires named
checks. Since a job that does not run reports nothing, and a
required check that never reports blocks every merge forever, each
tier ends in a small aggregate gate job whose only work is to
assert that everything it depends on succeeded or skipped:

| Check | Asserts |
|-------|---------|
| `Can see status` | nothing; it always succeeds, proving the workflow was evaluated at all |
| `Can enqueue` | the smoke-tier jobs in `functional-tests.yml`, on non-merge_group events |
| `Can enqueue: direct-qemu` | the direct-qemu lane |
| `Can enqueue: sf-e2e` | the Shaken Fist end-to-end lane |
| `Can merge` | the cloud matrices, on merge_group events only |

Those five names are the entire required-check list on the develop
ruleset. Because a skipped required check satisfies the rule, one
list serves both refs: on a pull request `Can merge` skips, and in
the merge queue the three `Can enqueue` checks skip.

The binding between a required check and the job that satisfies it
is the job's display name, matched as a string. **Renaming a gate
job without updating the ruleset blocks every merge in the
repository.** `sanity_checks` runs `tools/check-required-checks.sh`
to catch that as a red smoke check rather than as an outage: it
asserts every required context in the exported ruleset
(`.github/exported-config/ruleset-*.json`, archived daily by
`export-repo-config.yml`) still matches a job name in
`.github/workflows/`.

### Merge queue concurrency

Every job in `functional-tests.yml` carries a `concurrency` group so a
superseded run is cancelled rather than left to finish. Keying that
group needs care, because `github.ref` means something different in the
merge queue: it is the per-attempt queue branch,
`gh-readonly-queue/develop/pr-NNN-SHA`, and GitHub mints a fresh SHA
each time it rebuilds the group — which it does on every push to
develop. Keying on it puts each rebuild in a group of its own, so
nothing ever matches and nothing is ever cancelled. The superseded runs
keep building whole clouds against sfcbr, starving the one group that
can still merge.

So on `merge_group` the group is keyed on the base branch instead, and
on every other event on `github.ref` as usual. That is only safe
because the develop ruleset sets `max_entries_to_build: 1`: the queue
builds one entry at a time, so any other in-flight `merge_group` run is
by definition superseded and GitHub has already abandoned its queue
branch. **Raising `max_entries_to_build` above 1 would make this wrong**
— speculative groups for different entries would then cancel each other
— so that setting and this concurrency key have to move together.

The sibling smoke workflows (`direct-qemu-functional.yml`,
`sf-e2e-functional.yml`) still key on `github.ref` alone. They trigger
on `merge_group` but skip their heavy jobs there, so a piled-up run
costs seconds and no cloud capacity.

For the design rationale see
[plans/PLAN-two-tier-ci.md](/components/kerbside/plans/PLAN-two-tier-ci/).

## End-to-end CI coverage

The proxy is exercised end to end in CI: the direct-qemu functional
lane boots a real qemu/SPICE guest, drives it with the ryll headless
client through the proxy, and asserts the full Sextant scenario, plus
API-driven in-flight session termination and a non-gating
relay-latency loadtest. See
[plans/PLAN-rust-proxy.md](/components/kerbside/plans/PLAN-rust-proxy/) and
[ARCHITECTURE.md](https://github.com/shakenfist/kerbside/blob/develop/ARCHITECTURE.md).
The same proxy path can be exercised locally without MariaDB or the
daemon via the standalone mock harness — see
[direct-qemu-harness.md](/components/kerbside/direct-qemu-harness/).

## Ryll

Ryll is the upstream Rust SPICE client at
[shakenfist/ryll](https://github.com/shakenfist/ryll). The latency
loadtest image builds the Ryll binary from source (stage 1 of
`loadtests/latency/Dockerfile`) and ships it in the runtime stage. A
Python orchestrator at `loadtests/latency/orchestrator.py` drives
Ryll's control socket and writes a CSV of latency samples.

The metric measured is keypress-to-screen latency: the time between
the orchestrator sending a `send_key down` and the first
`surface_drawn` event that follows it, paired FIFO. The orchestrator
hard-fails if the client does not advertise and accept a
`surface_drawn` subscription (control socket v1.1 or newer), rather
than silently falling back to a different metric under the same column
name. The CSV is one float per line, seconds, no header.

## Testing the SPICE console of an oVirt VM

`tools/test-ovirt-console.py` is Kerbside's oVirt SPICE console probe.
It connects to the oVirt engine API, finds the booted test VM (by
default any VM named `smoke-test-*`), checks that SPICE display is
configured, and performs a SPICE protocol handshake against the
console port. This is the Kerbside-specific check and lives here
because we iterate on it alongside the proxy.

```bash
python tools/test-ovirt-console.py \
    --url https://ovirt-engine.example/ovirt-engine/api \
    --password secret \
    --ca-file /path/to/ca.pem
```

The generic plumbing it builds on lives in the
[shakenfist/actions](https://github.com/shakenfist/actions) repo,
which CI checks out alongside this one:

- `tools/start-test-target.py` — generic oVirt smoke test: sets up a
  datacenter, cluster, hypervisor host, and local storage domain,
  uploads a disk image, and boots a VM (`smoke-test-*`, SPICE display
  by default) to prove the deployment works. `test-ovirt-console.py`
  then probes the VM it creates.
- `tools/ovirt-install-base.sh` — base package installation (EPEL,
  utilities)
- `tools/ovirt-patch-ovn.sh` — patches oVirt 4.5 OVN Ansible role
  bug (#949)
- `tools/ovirt-prepare-host.sh` — engine health check, SSH setup, KVM
  verification
- `tools/ovirt-gather-artifacts.sh` — collects RPM lists and logs for
  CI artifacts

## The direct-qemu lane

The direct-qemu lane publishes a `Can enqueue: direct-qemu` gate job,
which is a required status check; see
[Gate jobs and required checks](#gate-jobs-and-required-checks).

### The proxy wheel is installed the way a deployment installs it

The direct-qemu Rust leg builds and installs the `kerbside-proxy`
wheel into the kerbside venv (`install-proxy-wheel.sh` →
`build-proxy-wheel.sh --native`), so `find_proxy_bin()` resolves it via
`shutil.which` on `PATH` — the real install path, which is what gives
this lane its coverage value. `start-kerbside.sh` pre-checks the proxy
binary through `find_proxy_bin()` for the same reason.

### Live termination

`verify-terminate-live.sh` (Rust leg only) runs on an isolated lane:
it POSTs the REST terminate endpoint and asserts the in-flight
connection drops, via the proxy log line `session terminated by
control plane`. This exercises the DB→`ProxyControl` bridge end to
end rather than the mock.

### The latency loadtest

Both legs run `run-loadtest.sh` (non-gating, `continue-on-error`). It
drives `loadtests/latency/orchestrator.py` to sample keypress-to-screen
latency — real `send_key` events timed against the `surface_drawn`
they produce — through the leg's proxy, and records p50/p95 as an
artifact; the Python-versus-Rust comparison is read off the two legs.

It boots the purpose-built `tests/fixtures/uefi-latency-guest.qcow2`,
which repaints on every keypress, rather than the Sextant scenario
fixture, which leaves its Awaiting screen on the first key and freezes
at the bootloader prompt. Like `verify-terminate-live.sh` it brings up
its own isolated lane (separate WORKDIR, `QCOW2` overridden) and tears
it down before the shared scenario lane starts.

This is distinct from the local mock harness
([direct-qemu-harness.md](/components/kerbside/direct-qemu-harness/)), which needs no
daemon and no database.

## The Shaken Fist end-to-end lane (`sf-e2e`)

`.github/workflows/sf-e2e-functional.yml` is the only lane that
exercises the `type: shakenfist` console source against a real
cluster. It stands up a single-node Shaken Fist (via
`shakenfist/actions/build-smoke-cluster`), provisions `KERBSIDE_URL`
and a signing key, deploys a co-located Kerbside with a
`type: shakenfist` source (via
`shakenfist/actions/deploy-kerbside-on-shakenfist`), and drives an
SF-minted token through offline verification, exchange, and a
proxied SPICE session against the Sextant guest booted inside the SF
instance — followed by an adversarial matrix covering replay,
expiry, wrong audience, unknown kid, and cross-namespace mint.

Driver scripts live in `tools/sf-e2e/` (see
`tools/sf-e2e/README.md`). It is a smoke-tier PR gate, and also runs
nightly and on dispatch.

## The oVirt end-to-end kerbside lane

The `ovirt_matrix` job in `.github/workflows/functional-tests.yml`
does more than build and
probe the oVirt environment: it also deploys the PR's own kerbside
(package plus the manylinux Rust proxy wheel) on the CI runner,
registers a live `type: ovirt` source against the engine it just
built, and relays a real SPICE session from the oVirt hypervisor
through the Rust proxy — asserting from the proxy log that the
backend leg escalated to TLS with a non-empty certificate-subject
pin on every escalation, then terminating the in-flight session via
the REST API and asserting the proxy dropped it.

The engine also holds a second VM, `no-spice-test`: diskless,
network-boot, with a VNC display and therefore no SPICE console
(`tools/create-ovirt-vnc-vm.py`). It exists to be ignored. Discovery
has to skip a VM it cannot broker and carry on scraping, and every
other VM in every lane has a SPICE display, so that branch had never
run in CI — which is how a missing `continue` in
`kerbside/sources/ovirt.py` survived: it errored the whole source,
dropped every VM discovered after the offending one, and reaped
their consoles as no longer available, once a minute.
`drive-console.py` now asserts that VM is absent from the console
list while the SPICE one is present.

Attaching that VM's NIC has one trap worth knowing about. The lane runs
two datacenters — `Default`, from `engine-setup`, and `test`, from
`start-test-target.py` — and each gets its own network named
`ovirtmgmt`, with its own id and its own vNIC profile of the same name.
Selecting a profile by name alone picks whichever the engine lists
first, and attaching the wrong datacenter's profile fails with HTTP 409
`The specified Logical Network doesn't exist in the current Cluster`.
`create-ovirt-vnc-vm.py` therefore resolves the network through the
cluster that will host the VM, which is the constraint the engine
actually enforces; `_resolve_vnic_profile` is covered by
`kerbside/tests/unit/test_create_ovirt_vnc_vm.py`.

That generalises: **anything that looks up an oVirt object by name must
scope the lookup to the `test` cluster or its datacenter** (issue #283).
A bare name match fails only when the engine happens to list the wrong
one first, and this code runs in the merge tier only — so a smoke-green
PR proves nothing about it.

The runner-side scripts live in `tools/ovirt-e2e/` and are
documented in `tools/ovirt-e2e/README.md`.
The lane is a worked example of the deployment described in
[use-cases/ovirt.md](/components/kerbside/use-cases/ovirt/), which is the operator-facing
version of what it proves.

### Log-derived oracles

`drive-console.py` asserts against the proxy's log text. It strips
ANSI before matching, and keeps "the field would not parse" separate
from "the field was empty" — the first is a harness fault, the second
is a real unpinned TLS leg. Conflating them (issue #272) reported a
broken parser as a security failure for two days. Any new
log-derived oracle should draw the same distinction. Proxy log
colouring is described in
[proxy-architecture.md](/components/kerbside/proxy-architecture/).

## Tempest tests against a Kolla-Ansible deployment

The `tempest-plugin/` directory is a separate releasable that
contributes Kerbside-specific Tempest tests; see
`tempest-plugin/README.md` for what it covers.

`tools/run-tempest-tests` drives a curated subset of those tests
against a running Kolla-Ansible deployment. It is invoked
automatically by the `openstack_matrix` job in
`.github/workflows/functional-tests.yml` after the `test-console`
smoke check, so the GitHub Actions CI iterates on the plugin's tests
on every merge-queue entry (the cloud matrices run in the merge tier,
not per-PR) rather than relying on upstream Zuul as the first signal.
The script:

1. Creates a Python venv at `/srv/kerbside-tempest/venv`.
2. Pip-installs `tempest`, `python-tempestconf`, and the local
   `tempest-plugin/` checkout into it.
3. Runs `tempest init` plus `discover-tempest-config` against
   `/etc/kolla/clouds.yaml`'s `kolla-admin` cloud with
   `compute-feature-enabled.spice_console True`.
4. Injects the `[kerbside]` group pointing at the Kolla CA bundle.
5. Runs `tempest run` against a regex that selects the kerbside plugin
   tests. The upstream `tempest.api.compute.admin.test_spice`
   (spice-direct) test deliberately bypasses Kerbside by connecting
   straight to the libvirt SPICE port, so it is not in the default
   regex — pass `--regex` to opt back in if you want it.

Run it manually on a deployed all-in-one node with
`sudo bash tools/run-tempest-tests`; pass `--help` to see knobs
(regex, workspace location, CA bundle path, etc.).

### Sextant scenario test (direct-qemu lane)

The plugin also contains an end-to-end scenario test at
`tempest-plugin/kerbside_tempest_plugin/tests/scenario/test_sextant_scenario.py`.
It drives an Uncalibrated Sextant UEFI guest through the full
Awaiting → Booting → bootloader-ignore → paste → Parked → shutdown
sequence over Ryll's control socket and asserts two independent
oracles: the live `digest_updated` QR event stream (frame counters
strictly increasing; per-beat record predicates) and the post-mortem
serial drain (canonical ordered event subsequence, monotonic
timestamps). The test requires ryll built with
`--features digest-decode` (enabled automatically by the direct-qemu
workflow).

Four `[kerbside]` tempest options support the scenario test:
`control_socket_path`, `serial_log_path`, `scenario_artifact_dir`, and
`scenario_step_timeout` (default 60 s). When `control_socket_path` is
unset the test skips cleanly, so the plugin remains drop-in safe on
the OpenStack lane. On the direct-qemu lane all four options are
written by `tools/direct-qemu/run-scenario.sh`, which runs the test as
the final (deliberately destructive) lane step — the final keypress
causes Sextant to drain serial and ACPI-shutdown, terminating the
guest and the ryll control socket. Screenshots are saved per beat into
`scenario_artifact_dir` and uploaded as CI artifacts alongside
`tempest.log`.

## Build the load testing OCI container images

There are a series of OCI container images intended for load testing.
These need to be built from the top level directory of the repository
because of the way `docker build` likes to constrain what files you
can copy into a container image.

### Latency load test

This is the first load test that was implemented. It uses a UEFI
binary as a test target and drives Ryll (the upstream Rust SPICE
client) in headless mode against an OpenStack-provisioned instance. A
Python orchestrator at `loadtests/latency/orchestrator.py` connects to
Ryll via its control socket, sends spacebar keypresses every two
seconds, collects SPICE PING/PONG round-trip latency samples, and
writes them to a CSV (one float per line, seconds). See the
[Ryll section](#ryll) above for a note on the metric definition.

To build this OCI image, do this:

```
docker build . -f loadtests/latency/Dockerfile -t kerbside-latency:latest
```

For your convenience, there is also a version of this image at
https://images.shakenfist.com/testimages/kerbside-latency.tar.gz
