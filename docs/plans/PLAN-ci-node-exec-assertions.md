# CI node-exec assertions and floating / network lifecycle tests

## Thought bubble

This is a design note, not a phased delivery plan. It captures why the
functional CI suite grew a "run an assertion on a specific cluster node"
primitive, how that primitive is meant to generalise, and the shape of the
floating-IP / network lifecycle tests that are its first consumers.

## Motivation

The floating-IP lifecycle test (`TestFloatingIPLifecycle`) was written on the
assumption that "the primary node is the network node and runs this suite".
That is true for an all-in-one dev cluster but **false for the
`shakenfist.shakenfist` collection's multi-node CI topologies**, where:

* the suite runs on the `primary` node, which is deployed with
  `SHAKENFIST_NODE_IS_NETWORK_NODE=False`; and
* the network node is a *separate* node (`sf1` in the slim topologies), which
  is where a floating IP's netns, `flt-<hex>` veth, `/32` and DNAT rule are
  actually plumbed, and where the floating / egress network
  (`192.168.230.0/24`) lives as an uplink-less island on `egr-br-eth0`.

The consequences were both a false negative and a hidden false positive:

1. `_await_floating_ping` ran `ping <floating>` locally on `primary`, which has
   no interface or route onto the floating network, so it could never succeed
   (300s timeout -> "Could not ping floating address"). This is the visible
   merge-queue failure on PR #3389.
2. The host-level plumbing/cleanup assertions guarded on a *local*
   `/var/run/netns/<net>` check, which is false on `primary`, so they silently
   no-op'd. The test's actual regression coverage (the #3378-#3383 floating
   leak) never ran.

The existing API-level `ping` endpoint (`ip netns exec <net> ping`) is **not**
a usable oracle for floating reachability: inside the network namespace the
floating `/32` is local and the DNAT PREROUTING path is never exercised. A
floating IP can only be validated from an egress-side vantage, i.e. the root
namespace of the network node.

## The primitive: run an assertion on any cluster node over the mesh

Rather than special-casing the network node, the suite gains a generic
capability: **discover cluster nodes from the API, then run a command on a
chosen node over the management mesh.** Floating IPs are simply the first
caller.

Design points:

* **Discovery is API-native, never hardcoded.** `GET /nodes`
  (`system_client.get_nodes()`) returns each node's `name`, mesh `ip`,
  `is_network_node` and `is_hypervisor`. The helper resolves roles and mesh
  addresses from this data, so the tests carry no knowledge of the CI IP plan
  or node names. On a single-node cluster the "network node" resolves to the
  local host and exec runs directly.
* **Local vs remote is decided by IP, not by name.** The helper compares the
  target node's mesh `ip` against the addresses present on the local host
  (`ip -json addr`). A match runs the command directly; otherwise it SSHes to
  the mesh IP as the base-image user. This sidesteps the SF-node-name
  (`config.NODE_NAME`, e.g. `sf1`) versus OS-fqdn (`t-6dFds-1`) confusion that
  already causes the placement tests to skip.
* **The exec channel is SSH over the mesh**, matching how the rest of CI
  reaches nodes (`debian@<node>` with passwordless sudo). Key path and user are
  configurable via `SF_CI_SSH_KEY` (default `~/.ssh/id_rsa`) and
  `SF_CI_SSH_USER` (default `debian`), with `StrictHostKeyChecking=no` and a
  throwaway known-hosts file as elsewhere in CI.
* **Unavailable channel skips loudly, never silently.** If node-exec cannot be
  established (no key, auth failure, unreachable), the affected assertions call
  `skipTest` with an explicit reason. Silent no-op is exactly what hid the
  breakage above; a visible skip does not.

### Deployment prerequisite

For the remote path, the node running the suite (`primary`) must be able to
SSH to the other nodes over the mesh. Every node already trusts the CI key
(`id_ci`) for `debian` (that is how the runner and ansible reach them), so the
only missing piece is making the **private** key available to the suite host
during the test run (e.g. `id_ci` -> `primary:~/.ssh/id_rsa`, as the post-test
`ci-node-checks` step already does for the runner). This lives in the
`shakenfist/actions` reusable smoke-cluster workflow. Until it lands the new
assertions skip loudly rather than failing; once it lands they run for real.

### Future consumers (not built here)

The same primitive generalises well beyond networking. Candidates worth
building on it later:

* Assert an instance's block devices are the disk *type* / bus / cache mode
  requested, by inspecting `virsh dumpxml` / `qemu` args on the hosting
  hypervisor.
* Assert a virtual network's VXLAN plumbing (`br-vxlan-<hex>`, `vxlan-<hex>`)
  exists on exactly the nodes hosting an instance on it (see test C below) and
  nowhere else.
* Assert per-node resource accounting (libvirt vs SF's view) after
  create/delete churn.
* Assert daemon-local on-disk state (lease files, nvram templates) is cleaned
  on teardown.

Each is a thin wrapper over "discover the node, run a command, assert on the
output"; none needs new SF surface area.

## The floating / network lifecycle tests

Three tests consume the primitive. All host-level assertions and the
reachability ping run **on the network node** via the exec helper, and all use
bounded polling because network-node reconcile (and the floating-gateway
"deletion halo" reaper) is asynchronous.

### Test A - float / defloat reachability and plumbing

Create one instance, then for two full float/defloat cycles:

* float, wait for the API to report the floating address;
* on the network node, assert the `flt-<hex>` interface and exactly one DNAT
  PREROUTING rule to the inner address exist;
* on the network node (root namespace), assert the floating address is
  pingable — the egress -> DNAT -> VXLAN -> guest path actually carries
  traffic;
* defloat, and assert the interface and DNAT rule are gone.

The second cycle is the regression for state leaked by the first: a reused
floating address must start from a clean slate.

### Test B - deleting an instance with a float attached cleans up

Float an instance, confirm plumbed, then delete the *instance* (not the float)
and assert the floating host state is fully removed. This is the common
ephemeral-CI path and a distinct code path from an explicit defloat.

### Test C - multi-node network lifecycle and cleanup

The sharper, multi-node test:

1. Allocate a network. Assert (bonus) it is present only on the network node
   (DHCP/netns) and absent on pure hypervisors.
2. Discover two hypervisors that are **not** the network node and pin one
   instance to each via `force_placement=<node name>`. Not trusting the
   scheduler to spread is what makes the presence assertion deterministic;
   using two non-network-node hypervisors is what makes it *sharp* (after
   teardown the network must vanish from both while still present on the
   network node).
3. Assert the network's VXLAN plumbing is now present on both hypervisors.
4. Delete both instances. Await and assert the network plumbing is gone from
   both hypervisors, still present on the network node.
5. Delete the network. Await and assert it is gone everywhere, including the
   netns, floating gateway and egress rules on the network node.

The presence invariant, stated precisely: a network is present on a node **iff**
that node hosts an instance on it **or** the node is the network node (which
always carries it for DHCP/NAT). This catches a different leak class from the
floating work — stale VXLAN plumbing stranded on drained hypervisors.

## Non-goals

* No new SF API surface. Assertions observe real kernel/libvirt state
  out-of-band; they do not read SF's own bookkeeping (which could hide the very
  drift they exist to catch).
* No periodic node-state logging to Loki. It cannot prove reachability, is a
  racy oracle for absence/cleanup, and couples test correctness to log volume.
  An on-demand operator diagnostic that dumps a network's host-side plumbing may
  be worth building on its own merits, but not as these tests' mechanism.
