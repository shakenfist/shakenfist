# Testing

How Kerbside is tested: the CI lanes, the Ryll-based harnesses, the
oVirt console probe, the Tempest plugin, and the load-test container
images.

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

The latency metric currently measured is SPICE PING/PONG round-trip
time (the v1 control-socket `latency` event). This is a temporary
regression from the legacy keypress-to-screen measurement — phase 6 of
the test-harness plan will restore the original metric via a
`surface_drawn` event in the control socket. The CSV shape is
unchanged from the legacy loadtest: one float per line, seconds, no
header. See
[plans/PLAN-test-harness-phase-04-port-latency.md](/components/kerbside/plans/PLAN-test-harness-phase-04-port-latency/)
for the full rationale.

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

## The oVirt end-to-end kerbside lane

Since two-tier CI phase 1, the `ovirt_matrix` job in
`.github/workflows/functional-tests.yml` does more than build and
probe the oVirt environment: it also deploys the PR's own kerbside
(package plus the manylinux Rust proxy wheel) on the CI runner,
registers a live `type: ovirt` source against the engine it just
built, and relays a real SPICE session from the oVirt hypervisor
through the Rust proxy — asserting from the proxy log that the
backend leg escalated to TLS with a non-empty certificate-subject
pin on every escalation, then terminating the in-flight session via
the REST API and asserting the proxy dropped it.

The runner-side scripts live in `tools/ovirt-e2e/` and are
documented in `tools/ovirt-e2e/README.md`; the architecture decision
and bring-up history are in
[plans/PLAN-two-tier-ci-phase-01-ovirt-kerbside.md](/components/kerbside/plans/PLAN-two-tier-ci-phase-01-ovirt-kerbside/).

## Tempest tests against a Kolla-Ansible deployment

The `tempest-plugin/` directory is a separate releasable that
contributes Kerbside-specific Tempest tests; see
`tempest-plugin/README.md` for what it covers.

`tools/run-tempest-tests` drives a curated subset of those tests
against a running Kolla-Ansible deployment. It is invoked
automatically by the `openstack_matrix` job in
`.github/workflows/functional-tests.yml` after the `test-console`
smoke check, so the GitHub Actions CI iterates on the plugin's tests
on every PR rather than relying on upstream Zuul as the first signal.
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
