# Owning more of the QEMU stack: QMP, and one day maybe libvirt's job too

## Prompt

Before responding to questions or discussion points in this
document, explore the shakenfist codebase thoroughly. Read
relevant source files, understand existing patterns (object
lifecycle, state machines, MariaDB storage via the three-layer
direct/gRPC/public pattern, Pydantic schemas, daemon
architecture, operation queue system, event logging), and
ground your answers in what the code actually does today. Do
not speculate about the codebase when you could read it
instead. Where a question touches on external concepts
(KVM/libvirt, VXLAN networking, MariaDB/Galera, gRPC/protobuf,
QEMU/QMP), research as needed to give a confident answer. Flag
any uncertainty explicitly rather than guessing.

All planning documents should go into `docs/plans/`.

Consult `ARCHITECTURE.md` for the system architecture
overview, object types, and daemon structure. Consult
`CLAUDE.md` for build commands, project conventions, and
database access patterns. Consult `GOALS.md` for current
development priorities. Key references inside the repo
include `shakenfist/baseobject.py` (object lifecycle and state
machine), `shakenfist/mariadb.py` (three-layer database
access pattern), `shakenfist/schema/` (Pydantic models), and
`shakenfist/daemons/database/main.py` (gRPC database daemon).

When we get to detailed planning, I prefer a separate plan
file per detailed phase. These separate files should be named
for the master plan, in the same directory as the master
plan, and simply have `-phase-NN-descriptive` appended before
the `.md` file extension. Tracking of these sub-phases should
be done via a table like this in this master plan under the
Execution section:

```
| Phase | Plan | Status |
|-------|------|--------|
| 1. Schema migration | PLAN-thing-phase-01-schema.md | Not started |
| 2. gRPC endpoints | PLAN-thing-phase-02-grpc.md | Not started |
| ...   | ...  | ...    |
```

I prefer one commit per logical change, and at minimum one
commit per phase. Do not batch unrelated changes into a
single commit. Each commit should be self-contained: it
should build, pass tests, and have a clear commit message
explaining what changed and why.

## Situation

This plan started life as an idea bubble from an unrelated
ryll debugging session (ryll's `web-feedback` branch,
post-008f) and has since grown into a broader exploration of
where Shaken Fist wants to sit on the libvirt ↔ raw-QEMU
spectrum.

The provoking moment was test-session-008f, where the ryll
web client successfully sent `VDAgentMonitorsConfig` messages
for browser-window-driven resolution requests, and Wayland
mutter on the Debian 13 guest silently dropped every request
whose dimensions weren't in the virtio-gpu EDID mode list.
The short-term fix in ryll (commit `3adf3e42` on
`web-feedback`) was to snap viewport requests to a list of
common modes most virtio-gpu EDIDs advertise. The real fix
would be for Shaken Fist to advertise the *operator's*
preferred mode list — or even update it live — via QMP,
which today SF does not touch at all.

That observation generalises in two directions. The narrow
one is "what else could SF do with a QMP socket per guest?"
The broader one is "almost every open-source cloud platform
is a libvirt wrapper, so we're all limited to whatever
libvirt currently exposes — what would SF look like if it
owned the QEMU layer directly?"

This document captures both questions. The QMP work is the
near-term, low-risk slice that delivers ryll's needed EDID
fix and several other already-asked-for features without
changing SF's fundamental architecture. The libvirt-exit
discussion is the strategic frame: even if we never act on
it, articulating the gain/cost tradeoff sharpens decisions
about every new VM-layer feature we add between now and
then.

## Mission and problem statement

Two related missions, deliberately separated by time horizon:

**Near-term (this branch's likely deliverable):** add a
QMP-control surface to Shaken Fist that closes the
ryll-discovered EDID gap and unlocks the small handful of
operator-facing features that have been intermittently
requested (graceful ACPI shutdown, screendump-on-event,
balloon-driven right-sizing, filesystem-quiesced snapshots).

**Long-term (the conversation this branch should also
start):** decide whether SF wants to keep libvirt as its
domain-XML translator forever, or migrate to a mode where SF
owns the QEMU command line, supervision, and QMP channel
directly. Capture the gain/cost so future SF decisions can
be made knowingly rather than by inertia.

## Where SF sits on the libvirt spectrum today

A survey of every libvirt touchpoint in the repo turned up a
much narrower dependency than the libvirt-API surface area
might suggest. Concretely:

**What SF uses libvirt for:**

- Domain XML generation via `shakenfist/deploy/templates/libvirt.tmpl`
  (227 lines, Jinja2). Translates SF's instance description
  into a QEMU command line (that's all libvirt does for us
  in the end).
- Lifecycle: `defineXML`, `create`, `destroy`, `undefine`,
  `setAutostart`, `reboot(ACPI)`, `reset`, `suspend`,
  `resume`. Hot-plug via `attachDeviceFlags` for NICs.
- State queries: power state enum mapping, `getCPUStats`,
  `blockStats`, `interfaceStats`, `XMLDesc` for post-hotplug
  audit, framebuffer streaming for screenshots.
- Process supervision: when SF daemons die, QEMU keeps
  running because libvirt is supervising it. When SF
  daemons come back, they reconnect to a fresh
  `libvirt.open('qemu:///system')` and find the still-
  running domains by name (`sf:<uuid>`).
- NVRAM/UEFI/secureboot via the libvirt domain XML
  attributes (OVMF code+vars pflash, SMM, secboot variant
  selection).
- Per-domain AppArmor profile generation (via
  `libvirt-aa-helper` — invisible to SF but doing real
  work).
- The `virsh` debug surface that operators already know
  (`virsh list`, `virsh dumpxml`, `virsh qemu-monitor-command`).

**What SF does NOT use libvirt for** (i.e. what we wouldn't
lose by leaving):

- Networking — SF runs its own VXLAN mesh; libvirt is only
  told "attach to this pre-existing bridge".
- Storage pools — SF manages disk files on the filesystem
  directly; libvirt doesn't see them as pool members.
- Secrets — none.
- Events — SF polls power state via the cleaner loop; we
  do not subscribe to libvirt domain events.
- Live migration — SF does not currently use libvirt
  migration; node drains are recreate-on-other-node.
- Snapshots — SF orchestrates disk archiving directly via
  the blob layer.
- Long-lived libvirt connections — each operation opens
  and closes its own connection (`shakenfist/util/libvirt.py:29`).

**What SF works around because libvirt doesn't expose it:**

- Direct PID discovery. SF regex-matches QEMU cmdlines in
  `/proc` against `guest=sf:<uuid>` to find each instance's
  kvm_pid, because libvirt's PID accessor is unreliable
  across versions. Lives in `daemons/resources/main.py:482`.
- NVMe disks — these are passed via `<qemu:commandline>`
  rather than `<disk/>` because libvirt's NVMe handling
  doesn't match what we want (template lines 49–56 +
  213–226).
- Port collisions on define/create (`instance.py:1587–1622`)
  with retry loops — symptom of libvirt's port management
  not being driven from SF.
- ryll's EDID problem — the original motivation. Libvirt
  exposes EDID via XML only at domain define time, not
  dynamically.

The negative space matters: SF uses ~5% of libvirt's surface
area, and most of that 5% is "translate XML to a QEMU
command line and supervise the process".

## The two questions, separated

### Q1: Should SF add a QMP control surface? (Yes, almost certainly.)

This is the near-term question and the original prompt for
the document. Adding QMP does not require leaving libvirt —
libvirt supports `virDomainQemuMonitorCommand` as a passthrough,
or we can attach a second QMP listener via the domain XML
template (chardev + monitor element) and let SF connect
directly. Either way the SF code change is bounded.

Triage of features the inventory turned up, with rough
sizing for a first phase:

- **Dynamic EDID** — closes the ryll loop. Modest scope.
- **Graceful ACPI shutdown** (`system_powerdown`) — should
  arguably already be SF's default; current `destroy` is
  hard power-off.
- **Screendump-on-event** — bug reports could capture a PNG
  of the framebuffer at the moment of an SF error event.
- **Filesystem-quiesced snapshots** (`guest-fsfreeze-*`) —
  unlocks crash-consistent snapshots without guest
  cooperation gymnastics.
- **Balloon control** — supports the long-rumoured "shrink
  idle guests" automation.
- **`query-blockstats`/`query-cpus-fast`** — finer-grained
  observability than `blockStats`/`getCPUStats`.

Lower-priority for phase 1 but worth mentioning: device
hot-plug (`device_add`/`device_del`), `block_resize`,
`migrate`, dirty-bitmap incremental backup primitives,
`guest-exec`, `human-monitor-command` (the universal escape
hatch).

### Q2: Should SF eventually drop libvirt and own the QEMU layer? (Open. Articulate the tradeoff now; decide later.)

The interesting question. The point of writing this section
is *not* to commit to dropping libvirt — it's to make sure
the next year of QMP/instance/snapshot work is taken with
eyes open about which direction we're heading in.

#### What SF would gain by owning QEMU directly

- **Distro libvirt lag stops mattering.** RH-derived
  distros in particular ship libvirt versions that lag
  upstream QEMU by 12–24 months, and Debian's not always
  much better. SF could target whatever QEMU the operator
  installs, including upstream builds.
- **First-class QMP from day one.** No `qemu-monitor-command`
  passthrough; QMP is just *how SF talks to a VM*. Every
  command in the QMP feature inventory above becomes
  trivially reachable. New QEMU QMP commands are usable on
  release day.
- **No double bookkeeping.** Libvirt has its own state
  database (`/var/lib/libvirt/qemu/`) which mirrors a
  subset of what SF stores in MariaDB. Loss of sync between
  the two is a real source of "this VM is in a weird state"
  bugs. Owning the layer makes SF's MariaDB the only source
  of truth.
- **Custom storage flow.** SF already has a content-addressed
  blob store. Today SF materialises blobs as files and points
  libvirt at them. Owning the QEMU command line means SF can
  drive `qemu-img`/`-drive` configurations that match its
  blob model more naturally — for example wiring QEMU at a
  backing file in the blob store directly, instead of
  copying.
- **NIC attachment without ceremony.** SF could pass already-
  configured TAP file descriptors directly to QEMU
  (`-netdev tap,fd=N`), removing the bridge-attach dance
  and the port-collision retry loop.
- **A simpler debuggable surface.** One process per VM,
  owned by SF, with a known QMP socket path. No "what is
  libvirt thinking right now" investigations.
- **A path to SF-native live migration.** The actual state
  streaming (pre-copy, post-copy, multifd, auto-converge,
  xbzrle, TLS) is all QEMU's work, reachable through QMP
  `migrate` / `migrate-incoming` / `query-migrate`.
  Libvirt's value-add on top is a five-phase v3 handshake,
  TLS cert distribution, and NBD-based non-shared-storage
  coordination — useful but bounded. Driving it ourselves
  gives us migration semantics that understand SF's blob
  and VXLAN layers (versus libvirt's storage-pool /
  network-aware model that we don't use anyway).
- **Hot failover (COLO) — a differentiator-grade
  feature.** See the dedicated subsection below. QEMU has
  had active-active VM replication in tree for roughly a
  decade. Libvirt does not expose it. No major OSS cloud
  ships it. SF's existing daemon mesh is unusually
  well-shaped to provide the missing heartbeat layer.
- **Confidential computing (SEV-SNP / TDX) — the second
  differentiator-grade feature.** See the dedicated
  subsection below. AWS, Azure, and GCP all ship
  "confidential VMs" as flagship products. OSS clouds lag
  primarily because libvirt's distro packaging has lagged
  upstream QEMU support by years. A direct-QEMU SF could
  offer this on whatever QEMU the operator installs.
- **Adjacent capabilities** — record/replay, TCG plugins,
  virtiofs, network filter chains, direct kernel boot,
  background snapshots. None are "the reason to leave",
  but each is something QEMU exposes today that libvirt
  either hides or wraps thinly. See the "Adjacent
  capabilities worth tracking" subsection.
- **Smaller install footprint** — libvirt + its dependency
  graph (polkit, libvirt-clients, libvirt-daemon-system,
  ebtables, dnsmasq...) is a chunk of surface area on
  every hypervisor node. SF only needs QEMU itself,
  qemu-utils, and OVMF.
- **Per-VM cgroup resource limits, for free.** The
  systemd-unit-per-VM supervision model brings
  `MemoryMax=` / `CPUQuota=` / `CPUAffinity=` /
  `IOWeight=` / `IOReadBandwidthMax=` /
  `IOWriteBandwidthMax=` / `TasksMax=` per instance.
  Libvirt has some cgroup support but SF doesn't use it;
  going through systemd makes resource limits the obvious
  path rather than the "we should configure that
  someday" path. Unlocks proper noisy-neighbour control
  and per-namespace QoS tiers.
- **Per-VM sandboxing as a unit-file concern.** Mount
  namespaces, seccomp, capability bounding, namespace
  restriction, and device cgroup control are all
  expressed declaratively in the unit file — composable,
  reviewable in PR, and unit-testable. See the
  "Confinement via systemd" subsection below.
- **Domain XML is just code.** The Jinja2 template that
  generates domain XML would become a Jinja2 template
  (or builder class) that generates a QEMU argv. Same
  effort, more directly aimed.

#### What SF would lose / have to rebuild

- **Process supervision (the hard one — see next section).**
  Libvirt keeps QEMU alive across libvirt and SF restarts.
  SF would need an equivalent.
- **Per-domain AppArmor — overstated, see "Confinement
  via systemd" below.** `libvirt-aa-helper` generates a
  tight per-domain profile that confines QEMU to just the
  paths it should touch. SF would lose that *generator*,
  but the systemd-unit-per-VM supervision model brings a
  confinement framework of its own (mount namespaces +
  seccomp + capability bounding + namespace restriction +
  cgroup device control) that is at least as strong for
  our threat model and brings cgroup resource limits that
  AppArmor doesn't even cover. systemd also composes with
  AppArmor via the `AppArmorProfile=` unit directive, so
  if we want LSM-level mediation on top we can add a
  single broad `sf-qemu` profile without writing a
  per-VM generator. See the dedicated subsection
  below.
- **Standard ops UX.** `virsh list`, `virsh dumpxml`,
  `virsh console` are well-known. SF would need to either
  preserve those (by also writing libvirt-format XML for
  inspection only) or make `sf` CLI tools idiomatic
  enough that ops people don't miss virsh.
- **NVRAM file lifecycle.** SF would manage OVMF VARS
  template copying itself (already partially does this —
  `instance.py:1423`). Not large.
- **cgroup setup, NUMA pinning, hugepages.** Libvirt has
  carefully-tuned defaults for these. SF doesn't use any
  of them today, so this only matters if we ever want to.
  Almost certainly we'd want a systemd-unit-per-VM model
  (next section) which gives us cgroups for free.
- **The libvirt sanity check.** Libvirt rejects malformed
  domain XML before launch. Our equivalent would be unit
  tests on the argv generator plus QEMU's own argument
  parsing.
- **Recovery semantics during upgrade.** Libvirt's API is
  versioned and stable; QEMU's QMP schema and CLI evolve
  release-to-release. We'd need to test against the
  specific QEMU versions we support, and to be more
  deliberate about following QEMU upstream.

#### A differentiator-grade feature: hot failover via COLO

The first of two strong concrete arguments for owning the
QEMU layer directly is a feature called **COLO**
(COarse-grained LOck-stepping). It has been in upstream QEMU since around
2015, is documented in current QEMU master, and provides
VMware-FT-style active-active VM replication with sub-second
failover and zero state loss. Libvirt's domain XML schema
has no `<colo>` element, and libvirt's migration documentation
does not mention it. Operators who want COLO today drive it
via raw QMP. No major OSS cloud (OpenStack Nova, oVirt,
Proxmox, CloudStack, KubeVirt) ships it as a supported
feature.

**How it works.** A primary VM (PVM) and secondary VM (SVM)
run in parallel on two hosts, both receiving every input
packet. A QEMU netfilter object called `colo-compare` sits in
the network path and compares PVM versus SVM output packets
in real time. While outputs match, packets are released
immediately — no steady-state latency penalty. On divergence,
a **checkpoint** is triggered: a fast micro-migration of
dirty pages and device state from PVM to SVM resynchronises
them. On PVM failure, the operator (or a heartbeat layer)
invokes `x-colo-lost-heartbeat` on the SVM and it takes over
with byte-identical state.

**The QMP surface is small.** `migrate-set-capabilities
x-colo`, `migrate` (to begin replication),
`x-checkpoint-delay` (idle checkpoint frequency),
`x-colo-lost-heartbeat` (trigger failover), plus the usual
`object-add` / `chardev-add` / `nbd-server-start` for the
netfilter and block-replication plumbing.

**Why SF is unusually well-shaped to ship this.** The
upstream docs are explicit that **heartbeat detection is not
implemented in QEMU** — the failure detector is the
operator's problem. For every other QEMU orchestrator that's
a non-trivial new subsystem. For SF it's a feature the
existing daemon mesh already has the bones for: node-state
tracking, sentinels, cluster-membership signals, the cluster
maintainer's wake-on-lock-loss pattern. A COLO heartbeat
implementation built on top of those primitives is plausible,
maybe even small.

**The honest caveats.** This is real but fiddly:

- **Heartbeat / failure detection is SF's problem.** Per
  above — the bones are there but it's still net-new code
  that must distinguish real host failure from transient
  network partition. Getting this wrong means split-brain
  on the network packet stream, with both VMs claiming
  primacy. The price of a wrong failover decision is high.
- **2× RAM on the secondary host** for every replicated
  instance. Operators pay for two instances' worth of
  resources to get one HA instance. Easy to bill for,
  but not free.
- **Identical QEMU versions on both nodes.** Same QMP
  schema, same VMState layout. Rolling upgrades become
  more constrained for COLO-protected instances.
- **Block replication setup is fiddly.** Requires a
  `replication` block driver layered over `quorum` / NBD.
  Whether SF's content-addressed blob model plugs into
  this naturally or needs a shim is a real architectural
  question — but one we could answer because we'd own
  both ends.
- **Network filter chain on both VMs** — mirror-redirect
  on PVM, redirect on SVM, plus a rewriter to fix TCP
  sequence numbers across failover. SF already owns the
  VXLAN layer, so this is less alien than it would be
  for a generic orchestrator, but it's still work.
- **Performance is workload-dependent.** Deterministic
  workloads (web servers handling identical requests) get
  cheap checkpoints. Workloads with lots of internal
  nondeterminism (heavy multi-threading, RNG-driven code)
  diverge often and pay checkpoint cost frequently. The
  feature is best shipped as per-instance opt-in, not a
  default.
- **No production users to copy from.** This is both
  warning and opportunity: nobody has wrapped COLO well
  yet (chicken-and-egg with libvirt support), so the
  field is open, but SF would be discovering production
  edge cases first-hand.

The strategic framing: **the features QEMU has that libvirt
hides are exactly the features that would differentiate an
opinionated cloud from a generic VM wrapper.** Libvirt's
surface area is "the lowest common denominator across
hypervisors and use cases." Anything ambitious lives outside
it. COLO is the clearest single example, but the same shape
applies to several other items in the QMP feature inventory
(dirty-bitmap incremental backup, NBD streaming snapshot,
guest-fsfreeze-quiesced snapshot trees). Owning the QEMU
layer is the price of admission for any of them.

This is not a commitment to ship COLO. It is an argument
that, if SF ever does want to ship it, the direct-QEMU path
is required — and that the prospect of being the OSS cloud
that ships hot failover is worth weighing alongside the
costs in the gain/cost lists above.

#### A second differentiator-grade feature: confidential computing

The other strong argument for owning the QEMU layer is
**confidential computing** — guest VMs whose memory the host
cannot read, with cryptographic attestation that the guest is
running on real protected hardware. AMD calls its
implementations SEV, SEV-ES, and SEV-SNP (in increasing order
of guarantee strength); Intel calls its version TDX. All of
them are upstream in QEMU today, reachable through standard
`-machine` / `-object` arguments and QMP. AWS Nitro Enclaves,
Azure Confidential VMs, and GCP Confidential Compute all
ship versions of this as flagship products.

OSS clouds lag for two reasons, both of which the
libvirt-exit conversation directly addresses:

- **Libvirt's domain XML schema took years to catch up.**
  Early SEV support landed in upstream QEMU in 2017–2018;
  libvirt's `<launchSecurity type='sev'>` element followed
  later, and SEV-SNP/TDX support arrived later again.
  Direct-QEMU SF could target whichever launch-security
  mode the operator's QEMU build supports.
- **The distro-libvirt-lag problem bites hardest here.**
  Confidential computing is the area where upstream QEMU
  moves fastest (TDX support, SNP attestation flows, guest
  policy refinements), and where RH-derived distros' libvirt
  packaging lags worst. An SF that bypasses libvirt
  inherits the QEMU upgrade cadence directly.

**The QMP / config surface** is small but non-trivial:
`-object sev-guest,...` (or `tdx-guest`), `-machine
confidential-guest-support=...`, `query-sev` /
`query-sev-launch-measure` / `query-sev-attestation-report`
QMP commands for the attestation handshake, and the
launch-secret injection flow for unsealing guest secrets
post-attestation.

**Why this fits SF.** Multi-tenant clouds are the natural
market: the value proposition is "run your workload here,
the cloud operator literally cannot read your memory or
inspect your VM state." SF's namespace-based multi-tenancy
model already shapes well to per-namespace "confidential
tier" instances. The attestation flow integrates naturally
with the existing JWT/auth model — the guest attestation
report becomes another credential the SF API can verify.

**Hardware availability — tempered reality check.** This is
strictly silicon-dependent. The QEMU side is necessary but
not sufficient; the host CPU has to support the relevant
extensions. The picture as of 2026:

- **AMD SEV-SNP** (the version anyone actually wants for
  meaningful "host can't tamper with my VM" guarantees):
  shipping in **EPYC 7003 / Milan (2021)** and every
  generation since — Genoa (9004, 2022), Bergamo (97xx,
  2023), Turin (9005, 2024), Venice (announced for 2026).
  Older SEV / SEV-ES variants reach back further (EPYC
  Naples and Rome) but lack memory integrity protection
  and aren't really the modern story. **No Ryzen consumer
  CPU has any SEV variant** — AMD has deliberately kept
  the feature EPYC-only.
- **Intel TDX:** **Sapphire Rapids / 4th Gen Xeon (2023)**
  shipped TDX with significant SKU limitations and a
  microcode-update dance; **Emerald Rapids / 5th Gen
  Xeon (2023–2024)** is the first generation with broadly
  available TDX support. **Granite Rapids / Xeon 6
  (2024 onward)** continues it. **No Core / consumer
  CPU has TDX.**
- **ARM CCA (Realms):** announced, not yet at deployment
  scale, not on Apple Silicon. Out of scope for this
  conversation.

In practice this means:

- **Cloud operators / datacenter SF deployments:** the
  hardware is mainstream and easy to acquire. Every
  reputable server vendor (Supermicro, Lenovo, HPE, Dell)
  sells SEV-SNP-capable EPYC and TDX-capable Xeon as
  standard configurations. Hetzner, OVH, and most
  second-tier providers rent Milan / Genoa hardware.
  Hyperscalers all offer confidential VMs as flagship
  products.
- **Used-hardware path:** EPYC Milan is now ~5 years old
  and findable on the used market — single-socket Milan
  systems are in the low thousands of dollars. Emerald
  Rapids is still mostly new-only.
- **SF home-lab / single-node deployments on consumer
  hardware:** locked out. Which is structurally fine —
  confidential computing exists to defend against an
  untrusted host operator, a threat model that doesn't
  apply to your own basement box.

The right framing for SF: confidential computing is a
**per-node capability** that the scheduler tracks via a
CPUID probe at host startup. Not every node in a cluster
will be CC-capable. Confidential-tier instances are
scheduled onto CC-capable nodes; non-confidential
instances are placed anywhere. This is standard SF
resources-daemon territory and not a feature gate at the
SF level. Operators without CC silicon get no confidential
tier; operators who buy or rent CC silicon get one
automatically.

The strategic point holds: **the bottleneck for shipping CC
in an OSS cloud is not silicon scarcity, it's the
orchestrator side.** The hardware is increasingly mainstream
in the server market; what's missing is a cloud orchestrator
that exposes the feature without libvirt's distro-packaging
lag in the way. That gap is the libvirt-exit argument in
sharpest form.

**Honest caveats.**

- **Live migration of confidential VMs is restricted.**
  Memory is encrypted with host-bound keys; cross-host
  migration is possible but requires key derivation /
  re-attestation steps that are still maturing upstream.
  Worth knowing if we ever combine confidential computing
  with our SF-native migration story.
- **Boot flow is more constrained.** OVMF must be a
  measured boot path; guest images need to be built with
  attestation expectations in mind. Some of this is a
  per-image policy question, not just an SF
  infrastructure question.
- **Operationally observable failure modes are harder.**
  When you can't read guest memory, you can't memdump
  for forensics on the confidential tier. Operators need
  to know that's a deliberate tradeoff, not a bug.
- **Heterogeneous-cluster scheduling complexity.** Mixed
  clusters (some CC-capable nodes, some not) need the
  scheduler to track and respect the per-node capability,
  and operators need to understand that draining a CC
  node may not have a same-tier replacement to schedule
  to. Not hard, but real ops surface.

This is not a Stage 0 feature — it requires direct-QEMU
control of the launch path — but it's a strategic prize
that, combined with COLO, would give SF two genuinely
differentiating capabilities no other OSS cloud ships
well.

#### Adjacent capabilities worth tracking

Beyond the two big strategic features above, QEMU has a long
tail of well-baked capabilities that libvirt either hides
poorly or doesn't surface at all. None individually justify
leaving libvirt, but they should inform how we shape any
direct-QEMU path so they remain reachable. Brief
characterisations:

- **Record/replay (`-icount` + `record-replay`).** QEMU can
  record a VM's full non-deterministic input stream
  (interrupt timing, disk reads, network packets, RNG,
  time) and replay it bit-for-bit later. The canonical use
  cases are malware analysis (run once, replay with
  different observers/plugins attached), reproducing
  Heisenbugs, and debugging non-deterministic test
  failures. Current limitation: typically single-vCPU only,
  and some I/O backends interfere with determinism.
  Underused outside academia. Combined with TCG plugins,
  this is a credible "security research tenant"
  differentiator.
- **TCG plugins.** Load custom C/C++ shared libraries that
  instrument every instruction or memory access in the
  guest. Industry uses include taint tracking, cache
  simulation, branch profiling, instruction-level fuzzing
  feedback. The "load your own plugin per instance" pattern
  is how SF would expose this; obviously gated by operator
  policy.
- **Network filter chains** (`filter-mirror`, `filter-dump`,
  `filter-rewriter`, `filter-redirect`). The same QEMU
  netfilter infrastructure COLO uses for output comparison,
  exposed individually. The most immediately useful is
  `filter-dump`: per-VM pcap capture without touching the
  guest, the host bridge, or any tcpdump-style external
  process. "Capture my VM's traffic for the next 60
  seconds" becomes a single API call. `filter-mirror`
  enables IDS feeds and forensics; `filter-rewriter`
  enables programmatic packet modification (TCP sequence
  fixes, header rewriting). All reachable via QMP today.
- **virtiofs.** Shared host filesystem mounted inside the
  guest at near-bare-metal performance — vastly better than
  9p or NFS for the same job. Natural fit with SF's blob
  layer: blob trees could be mounted directly inside
  guests without staging copies. Requires a `virtiofsd`
  side process per mount.
- **Background snapshots** (`migrate` to file with the
  `background-snapshot` capability). Take a full RAM+disk
  snapshot of a running VM **without pausing it**. The
  pause-on-snapshot wart of the current `snapshot-save`
  flow disappears. Reachable via QMP today.
- **swtpm / vTPM.** Per-guest emulated TPM, required for
  Windows 11 guests and any guest-side attestation flow
  (BitLocker, measured boot, software-bound secrets). A
  side process per VM, plumbed in via standard QEMU args.
  Not differentiator-grade, but a real customer gap if SF
  wants to support Windows 11 well.
- **gdbstub.** QEMU's built-in gdb-remote server (`-s` /
  `-gdb`). Attach gdb to a running guest from outside,
  single-step the kernel without guest cooperation. Useful
  for kernel panic forensics, kernel development, rootkit
  detection. SF could expose this as a per-instance debug
  socket gated by operator policy.
- **`dump-guest-memory` format variants.** Already in the
  QMP feature inventory below, but worth flagging: the
  command supports ELF (Volatility / Rekall analysis),
  kdump compressed, and Windows .dmp output. "Give me a
  memory dump I can open in WinDbg" is a button SF could
  offer with no guest cooperation.
- **Direct kernel boot / qemu-microvm.** `-kernel`,
  `-initrd`, `-append` bypass BIOS/UEFI entirely;
  qemu-microvm machine type drops PCI and ACPI for boot
  times in tens of milliseconds. Firecracker-class density
  without leaving the QEMU ecosystem. Not on the SF
  roadmap today but a real product-shape option if we ever
  want a "lots of tiny VMs" tier.

What this list is *not*: a feature roadmap. None of these
should drive Stage 0 work. What it *is*: a constraint on
the architecture of any direct-QEMU path we build later.
The argv generator, supervision model, and QMP plumbing
should be shaped such that surfacing any one of these later
is bounded work, not a re-architecture.

#### The supervision problem in detail

This is the load-bearing engineering question, and the one
the prompt explicitly singles out. If SF daemons own the
QEMU process directly, what happens to running VMs when SF
daemons restart for upgrade?

Options, roughly in increasing order of "feels right":

1. **SF daemon as parent, double-forked QEMU.** QEMU
   detaches from SF via double-fork + setsid. SF daemons
   can restart freely. On restart, SF rediscovers QEMU
   processes by `/proc` scan (we already do this for PID
   discovery) and reconnects to per-VM QMP sockets. Works
   but feels fragile — QEMU is then a true orphan
   reparented to init, with no automatic restart, no
   journald logs, no cgroup isolation.

2. **systemd-run scope per VM.** Each VM runs inside a
   transient systemd scope. SF spawns via
   `systemd-run --unit=sf-vm-<uuid> --scope ...`. Lifetime
   is tied to the scope, not the SF daemon. Gives cgroups
   and journald for free. Reconnection on restart is "list
   `sf-vm-*` units, connect to known QMP socket paths".

3. **systemd unit-per-VM (the strongest contender).**
   Generate a transient `sf-vm@<uuid>.service` per
   instance via `systemd-run`. systemd owns the QEMU
   process, restart policy is `Restart=no` (a crashed
   guest stays crashed; SF decides whether to relaunch),
   journald captures QEMU stderr, cgroups give us resource
   limits, **per-unit sandboxing directives give us
   confinement equivalent to libvirt's AppArmor profiles
   (see "Confinement via systemd" below)**, and the
   dependency graph lets us order against blob mounts.
   Reconnection after SF restart is again "find the
   sockets, connect". This is essentially what Kata
   Containers and Firecracker VMM managers do.

4. **A tiny dedicated `sf-vmm` daemon.** Minimal
   long-lived supervisor whose sole responsibility is
   "launch QEMU and expose its QMP socket via gRPC".
   SF talks to it. Other SF daemons can restart without
   affecting VMs. This *is* reinventing a slice of libvirt
   — but a much smaller slice than libvirt, tailored to SF.
   Worth considering only if the systemd-unit approach
   turns out to have a fatal flaw.

The systemd-unit-per-VM model (option 3) is almost
certainly the answer if we go this direction. It hands the
supervision problem to a piece of software whose only job
is supervising processes, gives us logging + cgroups +
sandboxing + restart policy as a side effect, and matches
what other modern VMM-orchestrators do. The work is
bounded: a templated unit file, a generator, and a
"reconnect on startup" pass during sf-resources daemon
init.

#### Confinement via systemd

A separate "what would we lose" panic point in earlier
versions of this plan was libvirt's per-domain AppArmor
profiles (generated by `libvirt-aa-helper`). That worry
turns out to be largely overstated once you commit to the
systemd-unit-per-VM model: systemd has grown a serious
per-unit confinement framework that covers the same threats
in a more idiomatic shape, and brings cgroup resource
limits AppArmor doesn't even attempt.

**The systemd sandboxing primitives.** A non-exhaustive
list of unit directives that map directly onto what
`libvirt-aa-helper` does (or wishes it could):

- **Filesystem isolation (mount-namespace based, kernel-
  enforced):** `ProtectSystem=strict` makes /usr, /boot,
  /efi, /etc read-only. `ProtectHome=tmpfs` hides /home,
  /root, /run/user. `ReadWritePaths=`, `ReadOnlyPaths=`,
  `InaccessiblePaths=`, `BindPaths=`, `BindReadOnlyPaths=`
  give per-path control. `PrivateTmp=yes` gives a private
  /tmp. `RootDirectory=` / `RootImage=` enable chroot-style
  isolation if wanted. If each instance's state lives
  under `/var/lib/sf/instances/<uuid>/`,
  `ReadWritePaths=/var/lib/sf/instances/%i/` becomes
  per-domain-effective automatically — the unit only ever
  sees its own subtree.
- **Namespace isolation (things AppArmor doesn't even
  try):** `PrivateNetwork=`, `PrivateUsers=`,
  `PrivateIPC=`, `PrivateMounts=`, `ProtectHostname=`,
  `ProtectClock=`, `ProtectProc=`, `ProcSubset=`.
- **Privilege control:** `NoNewPrivileges=yes`,
  `CapabilityBoundingSet=`, `AmbientCapabilities=`,
  `RestrictSUIDSGID=`, `LockPersonality=`.
- **Syscall and network filtering:** `SystemCallFilter=`
  with seccomp by name or by group (`@system-service`,
  `~@privileged`, `~@resources`). `SystemCallArchitectures=
  native`. `RestrictAddressFamilies=` to allow only
  AF_UNIX, AF_INET, AF_INET6, AF_VSOCK, AF_NETLINK.
  `RestrictNamespaces=`, `RestrictRealtime=`,
  `MemoryDenyWriteExecute=`.
- **Kernel-surface protections:** `ProtectKernelTunables=`,
  `ProtectKernelModules=`, `ProtectKernelLogs=`,
  `ProtectControlGroups=`.
- **Device cgroup control:** `DevicePolicy=closed` plus
  explicit `DeviceAllow=/dev/kvm rwm`, `DeviceAllow=/dev/
  net/tun rwm`. Equivalent to AppArmor's device whitelist
  but enforced at cgroup level.
- **Resource limits (AppArmor doesn't cover any of this):**
  `MemoryMax=`, `MemoryHigh=`, `CPUQuota=`, `CPUWeight=`,
  `CPUAffinity=`, `IOWeight=`, `IOReadBandwidthMax=`,
  `IOWriteBandwidthMax=`, `TasksMax=`, `OOMScoreAdjust=`.

**A representative sf-vm@.service stanza** looks like:

```ini
[Service]
ExecStart=/usr/bin/qemu-system-x86_64 ...
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=tmpfs
ReadWritePaths=/var/lib/sf/instances/%i/
ReadOnlyPaths=/usr/share/OVMF/
DeviceAllow=/dev/kvm rwm
DeviceAllow=/dev/net/tun rwm
DevicePolicy=closed
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW
SystemCallFilter=@system-service
SystemCallFilter=~@privileged @resources
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_VSOCK AF_NETLINK
RestrictNamespaces=yes
MemoryDenyWriteExecute=yes
MemoryMax=<configured>
CPUQuota=<configured>
```

**Where AppArmor still goes further (honest list).** Two
genuine gaps in the systemd-only model, both relatively
academic for our threat model:

- **LSM-hook mediation of runtime file opens.** AppArmor
  evaluates every `open(2)` / `mmap` / `exec` against the
  profile at the LSM hook level. systemd's filesystem
  restrictions are mount-namespace based — equivalent for
  most purposes, but if QEMU is tricked into opening a path
  that *exists in its mount namespace*, only LSM hooks can
  stop it. SF's threat model (known-argv QEMU launched by
  trusted SF code, no plugin loading in the default
  config) makes this largely theoretical.
- **Fine-grained socket peer mediation.** AppArmor can
  express "unix socket connect only to
  `/run/libvirt/libvirt-sock`"; systemd's
  `RestrictAddressFamilies=` is per-family rather than
  per-socket-path. For QMP this is moot because SF owns
  the socket path; for arbitrary peers it matters more.

**The three confinement tiers.** systemd composes with
AppArmor (and SELinux, Smack) via the `AppArmorProfile=`,
`SELinuxContext=`, `SmackProcessLabel=` unit directives,
so the choice is not "systemd or AppArmor" but how much of
each:

- **Tier A: systemd sandboxing only.** Sufficient for SF's
  threat model. Portable across AppArmor / SELinux /
  no-LSM distros. Brings cgroup resource limits as a
  bonus. This is the recommended baseline.
- **Tier B: systemd sandboxing + a single broad
  `sf-qemu` AppArmor profile** referenced via
  `AppArmorProfile=sf-qemu` in the unit. Adds LSM-level
  mediation on top, without writing a per-VM profile
  generator. One profile, version-controlled in the SF
  repo, applied to all VMs. Cheap defense-in-depth
  upgrade over Tier A.
- **Tier C: systemd sandboxing + per-VM AppArmor profile
  generation.** Mirrors what libvirt-aa-helper does today.
  Probably overkill — the per-instance directory tree
  already constrains filesystem access, so the profile
  would mostly duplicate that constraint at the LSM layer.
  Worth keeping in mind as an option but not the default
  recommendation.

The "we'd lose per-domain AppArmor" framing in the
"What SF would lose" list above is therefore overstated.
We'd lose the libvirt-aa-helper *generator*; the
*confinement* is achievable through systemd primitives that
are at least as strong for our threat model and unlock
cgroup resource control we don't have today.

#### Risk: distro QEMU versions

The single biggest *operational* risk of leaving libvirt is
that we'd lose libvirt's distro packaging buffer. Today, an
operator on Debian 12 gets whatever QEMU+libvirt combo the
distro shipped, and libvirt smooths over the version
differences. SF on raw QEMU has to know what version of
QEMU it's talking to and adapt.

This is solvable — qemu-system-x86_64 has a stable
`--version`, QMP exposes `query-version` and
`query-commands`, and our argv generator can branch on
detected version. But it's a real ongoing maintenance tax
that the libvirt path doesn't currently impose.

## A staged path

If we ever do this, it should not be a flag day. The
sketched stages, each independently shippable and
independently abandonable:

- **Stage 0 (this plan's near-term work):** Add QMP via
  libvirt's passthrough or a second QMP listener attached
  in the domain XML. Deliver dynamic EDID, graceful
  shutdown, screendump-on-event, and a small handful of
  the inventory features. SF still uses libvirt for
  supervision, XML, lifecycle.
- **Stage 1 (a future plan):** Build a parallel direct-QEMU
  launch path behind a per-node config flag. New
  instances on opted-in nodes run as `sf-vm@<uuid>.service`
  units with SF-generated argv and SF-owned QMP. Existing
  libvirt-launched instances continue under libvirt. This
  is the validation phase — does SF's QEMU argv match the
  features and stability of the libvirt path?
- **Stage 2 (further future):** Default switches to
  direct-QEMU for new instances. Libvirt path remains for
  legacy support and as a fallback during the transition.
- **Stage 3 (only if Stages 1–2 went well):** Deprecate
  the libvirt path. Document the QEMU-version support
  matrix. Drop the libvirt dependency.

Stages 1+ are *not* on this branch's roadmap; they're
documented so the QMP work doesn't accidentally make them
harder (for example, by hardcoding behaviour that assumes
libvirt's XML define-then-create model).

## Feature inventory (QMP — for the Stage 0 work)

The ryll session listed these as candidate wins. They need
to be triaged against what SF already does via libvirt /
`virsh` and what would be net-new:

- **Display & input**
  - Dynamic EDID (the original prompt; live mode-list
    update so ryll's browser-window snap actually tracks
    the window).
  - Multi-monitor (`query-display-options` / equivalent).
  - Arbitrary keys/mouse from the REST API (handy for
    headless automation that doesn't need a SPICE
    round-trip).
- **Power & lifecycle**
  - `system_powerdown` (graceful ACPI vs SF's current
    `destroy`).
  - `system_reset`, `system_wakeup`.
  - `screendump` — pull a PNG of the current framebuffer
    at any moment, useful for the SF bug-report flow.
- **Storage & networking hot-plug**
  - `device_add` / `device_del` for disks, NICs, USB
    without rebooting. Could surface "attach this blob as
    a virtual USB stick now".
  - `block_resize` — grow a disk live so the guest sees
    the new size without a restart.
  - `nbd-server-start` + `block-export-add` — expose a
    guest disk over NBD for streaming snapshot/restore.
  - `object-add` of `filter-dump` — per-VM pcap capture
    without touching the guest, the host bridge, or any
    external tcpdump process. "Capture my VM's traffic for
    the next 60 seconds" as a single API call. Same
    netfilter family as COLO's `colo-compare`. Reachable
    via QMP today.
- **Snapshots & migration**
  - `snapshot-save` / `snapshot-load` for fast full
    VM-state snapshots (RAM + disk).
  - `migrate` to a file with `background-snapshot`
    capability — full RAM+disk snapshot **without pausing
    the VM**. Removes the pause-on-snapshot wart of
    `snapshot-save`.
  - `migrate` to an NBD/socket target for SF-mediated
    live migration between nodes (pre-copy, post-copy,
    multifd, auto-converge, xbzrle all reachable via
    `migrate-set-capabilities` / `migrate-set-parameters`).
  - `x-colo` capability + `x-colo-lost-heartbeat` for
    active-active hot failover (see the COLO subsection
    above; this is the strategic prize, not a Stage 0
    feature).
  - `dirty-bitmap` family for incremental backup
    primitives (cheap "every 5 min diff" backups).
- **Observability**
  - `query-blockstats`, `query-iothreads`,
    `query-cpus-fast`, `query-memory-size-summary` for
    fine-grained per-guest perf metrics into the SF
    events stream / Grafana.
  - `query-status`, `query-vnc`, `query-spice` for
    reliable channel/state discovery.
- **Memory**
  - `balloon` for live RAM reclaim ("shrink idle guests"
    automation).
  - `memory-backend-file` hot-add for live memory
    expansion.
- **Debugging the hard stuff**
  - `dump-guest-memory` — full RAM dump for kernel
    crashes / forensics. Supports ELF (Volatility /
    Rekall analysis), kdump compressed, and Windows .dmp
    output formats. "Give me a memory dump I can open in
    WinDbg" becomes a button.
  - gdbstub exposure (`-s` / `-gdb` chardev) — attach gdb
    to a running guest from outside, single-step the
    kernel without guest cooperation. Useful for kernel
    panic forensics and rootkit detection. Gate per
    instance under operator policy.
  - `human-monitor-command` — escape hatch into HMP for
    anything not yet wrapped (`info qtree`, `info pci`,
    `info numa`).
  - `query-iotrace`, `qom-list-properties` — device-tree
    introspection.
- **Guest-agent adjacent**
  - `guest-exec` — run commands in the guest from the
    API (already in `qemu-ga`, not exposed by SF).
  - `guest-fsfreeze-freeze` — quiesce filesystem for
    crash-consistent snapshots.
  - `guest-set-time` — clock sync after pause/resume.
- **Adjacent side-process integrations (not QMP, but in
  the same Stage 0 scope)**
  - **swtpm** — per-guest emulated TPM, side process
    launched alongside QEMU. Required for Windows 11
    guests, BitLocker, and any guest-side attestation
    flow (measured boot, software-bound secrets). Not
    differentiator-grade, but a real customer gap if SF
    wants to support Windows 11 well. Reachable via
    libvirt today; SF just doesn't surface it.
  - **virtiofsd** — host-filesystem-to-guest shared
    mount via virtiofs, near-bare-metal performance.
    Natural fit with SF's blob layer (mount blob trees
    inside guests without staging copies). Side process
    per mount.

The ryll-session prioritisation, from the operator's gut
sense: dynamic EDID + screendump-on-event + balloon +
filesystem-quiesced snapshots + per-VM `filter-dump` pcap
are probably the biggest UX wins. swtpm is the highest
near-term *customer gap*.

## Open questions

These need to be resolved before any Stage 0 phase work is
planned. The discussion is scoped to Stage 0 — Stages 1+
have their own (larger) open-question lists that we should
articulate only if and when we decide to pursue them.

- **Where does the QMP socket live?** The cleanest options
  are: (a) ask libvirt to expose a command-at-a-time
  passthrough via `virDomainQemuMonitorCommand`, (b)
  attach a second QMP listener via the libvirt XML
  template (cleaner, allows event subscription), (c)
  sidestep libvirt entirely (Stage 1+ territory, not yet).
  Option (b) is probably right for Stage 0: SF gets its
  own dedicated socket with full QMP semantics, libvirt
  retains its monitor, no fighting over the channel.
- **Which SF daemon talks QMP?** Likely the resources
  daemon (it already owns per-node instance state and the
  libvirt connection pattern), but worth cross-checking
  against the cleaner and sidechannel daemons before
  committing.
- **What's the API shape?** A few candidate styles:
  - A thin "exec arbitrary QMP" REST endpoint, mirroring
    `virsh qemu-monitor-command`. Maximum power, minimum
    safety net.
  - Per-feature wrappers (`POST /instances/.../screendump`,
    `POST /instances/.../balloon`). More verbose but
    safer and easier to document/RBAC.
  - A hybrid: typed wrappers for the common cases, plus a
    gated raw-exec endpoint for advanced use. Almost
    certainly the right answer.
- **Permissions model.** QMP can do destructive things;
  the SF auth model needs to decide which features any
  authenticated user can invoke, which need a namespace
  admin, and which are operator-only.
- **Observability.** QMP commands and their responses
  should land in the SF event log — this fits SF's
  existing event-stream pattern but specifics need
  thinking about.
- **QMP event subscription.** A persistent QMP connection
  per VM means SF can subscribe to events
  (`SHUTDOWN`, `STOP`, `RESET`, `BLOCK_IO_ERROR`,
  `GUEST_PANICKED`, `RTC_CHANGE`, ...) and drive SF
  state machines off them instead of polling. This is a
  significant power upgrade over the current cleaner-loop
  polling model.

## Scope boundary

In scope for Stage 0 (this branch):

- A QMP control surface on the SF API.
- Dynamic EDID (closes the ryll loop).
- A small handful of other inventory features that the
  triage settles on.

Explicitly out of scope for Stage 0, but recorded so
they're not lost:

- Direct-QEMU supervision (Stages 1+).
- Live migration orchestration. The QMP-level mechanics
  are smaller than the original framing suggested — most
  of the work is in QEMU and reachable via `migrate` /
  `query-migrate` — but the surrounding scheduler,
  storage, and network coordination is still its own
  plan. Worth opening in parallel with Stage 1 once
  direct-QEMU supervision exists.
- COLO-based hot failover (a plan in its own right,
  predicated on direct-QEMU supervision and a real
  heartbeat layer; the strategic prize, but not
  near-term).
- Anything that needs guest cooperation beyond what
  `qemu-ga` already provides.
- Per-domain AppArmor regeneration (only relevant if we
  leave libvirt).

## Execution

To be filled in once the Stage 0 feature triage is done.
The expected shape:

| Phase | Plan | Status |
|-------|------|--------|
| 0. Decisions and feasibility | TBD | Not started |
| 1. QMP socket plumbing + first feature (dynamic EDID) | TBD | Not started |
| 2. Remaining Stage 0 features | TBD | Not started |
| ...   | ...  | ...    |

Phase 0 should land the socket-location decision, the API
shape decision, the permissions model, and a sized list of
which features from the inventory are in scope for Phase 1.

Phase 1 should also leave the door open to Stages 1+: the
QMP plumbing should not assume libvirt is the owner of the
QEMU process, so that if SF ever takes over supervision the
QMP layer above it doesn't have to change.

## Agent guidance

*(Placeholder — this plan is still exploratory. The
execution model, planning effort, step-level guidance, and
management session review checklist sections from
`PLAN-TEMPLATE.md` apply by default and will be re-stated
here when the plan is ready for sub-agent execution.)*

## Administration and logistics

### Success criteria

Placeholder — to be defined when the plan is fleshed out.
Likely candidates for Stage 0:

- A new QMP control surface exists on the SF API at a
  documented endpoint shape.
- The endpoint is exercised by at least one real feature
  (the inventory triage will pick which one; dynamic EDID
  is the obvious first candidate).
- Documentation in `docs/operator_guide/` covers the new
  endpoint(s), the permissions model, and any safety
  caveats.
- `ARCHITECTURE.md` describes the QMP path.
- This document is updated with a clear "we have / have
  not decided" position on the libvirt-exit question.

### Future work

The ryll session that prompted this plan parked the
viewport-snap workaround in `ryll/src/web/inputs.rs::snap_viewport_to_standard_mode`
(commit `3adf3e42` on `web-feedback`). Once dynamic-EDID
lands here, that snap function should become a no-op
fallback rather than the primary path.

If the libvirt-exit conversation ever moves from
"articulated" to "decided yes", the Stage 1 plan should be
its own master plan rather than a phase of this one — the
scope is too big to live under a QMP-orchestration
heading.

### Bugs fixed during this work

(Empty.)

### Documentation index maintenance

When this plan moves past placeholder status, update:

- `docs/plans/index.md` — add a *Master plans* row.
- `docs/plans/order.yml` — entry already added under the
  current filename `PLAN-qemu-futures.md` (renamed from
  `PLAN-qmp-orchestration.md` once the scope grew beyond
  QMP into the broader libvirt-exit exploration).

### References

External references that inform this plan. Verify against
current upstream before relying on for implementation —
QMP commands and capability names evolve.

QEMU — migration and COLO:
- [QEMU COLO Fault Tolerance documentation (master)](https://www.qemu.org/docs/master/system/qemu-colo.html)
  — primary reference for the COLO architecture, QMP
  command sequences, and the explicit note that heartbeat
  detection is not implemented in QEMU.
- [qemu/docs/COLO-FT.txt (GitHub mirror)](https://github.com/qemu/qemu/blob/master/docs/COLO-FT.txt)
  — same content, easier to track changes via `git log`.
- [QEMU migration developer documentation](https://www.qemu.org/docs/master/devel/migration/main.html)
  — pre-copy / post-copy / multifd / auto-converge /
  xbzrle mechanics, what the migration thread actually
  does.
- [QEMU QMP reference](https://www.qemu.org/docs/master/interop/qemu-qmp-ref.html)
  — canonical list of QMP commands and their schemas;
  the source of truth for what's actually callable.

QEMU — confidential computing:
- [QEMU AMD Secure Encrypted Virtualization (SEV) documentation](https://www.qemu.org/docs/master/system/i386/amd-memory-encryption.html)
  — SEV / SEV-ES / SEV-SNP machine and object
  configuration, attestation flow, and launch-security
  semantics.
- [QEMU Intel Trust Domain Extensions (TDX) documentation](https://www.qemu.org/docs/master/system/i386/tdx.html)
  — TDX guest configuration and attestation. Verify
  current upstream state; TDX has been moving fast.
- AMD SEV-SNP whitepapers and `linux/Documentation/virt/kvm/amd-memory-encryption.rst`
  for the KVM-side ABI when SF's argv generator needs
  the full picture.

Confidential computing — hardware availability:
- [AMD Secure Encrypted Virtualization (SEV) developer portal](https://www.amd.com/en/developer/sev.html)
  — vendor landing page for SEV / SEV-ES / SEV-SNP,
  pointers to processor support matrix.
- [AMD "Using SEV with AMD EPYC Processors" user guide (PDF)](https://www.amd.com/content/dam/amd/en/documents/developer/58207-using-sev-with-amd-epyc-processors.pdf)
  — definitive SEV / SEV-ES / SEV-SNP support matrix
  across EPYC 7001 / 7002 / 7003 / 9004 generations.
- [Lenovo Press — Enabling AMD SEV-SNP on ThinkSystem Servers](https://lenovopress.lenovo.com/lp1893-enabling-amd-sev-snp-on-thinksystem-servers)
  — vendor-side documentation of the SNP enablement flow
  (UEFI options, IOMMU, etc.) on shipping EPYC servers.
- [Phoronix — Evaluating the performance cost to AMD SEV-SNP on modern EPYC VMs](https://www.phoronix.com/review/amd-epyc-9005-sev-snp)
  — independent performance benchmarks on EPYC 9005
  (Turin); useful for sizing the overhead cost.
- [Intel — What Xeon Processors Support Intel TDX](https://www.intel.com/content/www/us/en/support/articles/000091103/processors/intel-xeon-processors.html)
  — authoritative Intel-side list of TDX-supporting
  Xeon SKUs.
- [Intel — TDX Connect support on Xeon 6 processors](https://www.intel.com/content/www/us/en/support/articles/000101754/processors.html)
  — TDX on the current Xeon 6 / Granite Rapids generation.
- [SecurityWeek — Intel adds TDX to confidential computing portfolio with 4th Gen Xeon launch](https://www.securityweek.com/intel-adds-tdx-confidential-computing-portfolio-launch-4th-gen-xeon-processors/)
  — context on Sapphire Rapids TDX launch limitations
  and how Emerald Rapids cleaned them up.
- Wikipedia generation pages ([Sapphire Rapids](https://en.wikipedia.org/wiki/Sapphire_Rapids), [Emerald Rapids](https://en.wikipedia.org/wiki/Emerald_Rapids), [Granite Rapids](https://en.wikipedia.org/wiki/Granite_Rapids))
  — quick reference for release dates, SKU lists,
  socket compatibility.

QEMU — adjacent capabilities:
- [QEMU Record/Replay documentation](https://www.qemu.org/docs/master/system/replay.html)
  — `-icount` and `record-replay` family, current
  limitations (vCPU count, I/O backend restrictions).
- [QEMU TCG Plugins documentation](https://www.qemu.org/docs/master/devel/tcg-plugins.html)
  — plugin ABI, loading model, example plugins in
  `tests/tcg/plugins/`.
- [QEMU Network Filters documentation](https://www.qemu.org/docs/master/system/qemu-block-drivers.html)
  and the `qemu(1)` manual page — `filter-dump`,
  `filter-mirror`, `filter-rewriter`, `filter-redirect`
  configuration. (Filter docs are scattered across the
  QEMU manual; the `object-add` QMP reference is the
  authoritative schema source.)
- [virtio-fs / virtiofsd project](https://virtio-fs.gitlab.io/)
  — shared filesystem semantics, performance notes,
  and the virtiofsd side-process binary.
- [QEMU gdbstub documentation](https://www.qemu.org/docs/master/system/gdb.html)
  — `-s` / `-gdb` chardev configuration, supported gdb
  commands, target-specific notes.
- [swtpm project](https://github.com/stefanberger/swtpm)
  — emulated TPM side process, integration patterns with
  QEMU and libvirt, current TPM 2.0 support state.
- [QEMU microvm machine type documentation](https://www.qemu.org/docs/master/system/i386/microvm.html)
  — minimal machine model for high-density / fast-boot
  use cases.

systemd — supervision and confinement:
- [systemd.exec(5) — sandboxing directives](https://www.freedesktop.org/software/systemd/man/systemd.exec.html)
  — canonical reference for `ProtectSystem=`,
  `ReadWritePaths=`, `SystemCallFilter=`,
  `RestrictAddressFamilies=`, `AppArmorProfile=`, and the
  rest of the per-unit confinement framework discussed in
  the "Confinement via systemd" subsection.
- [systemd.resource-control(5)](https://www.freedesktop.org/software/systemd/man/systemd.resource-control.html)
  — `MemoryMax=`, `CPUQuota=`, `IOWeight=`, and the cgroup
  resource limit directives.
- `systemd-run(1)` and `systemd.service(5)` for the
  transient-unit / template-unit launch patterns the
  supervision section sketches.

Libvirt:
- [libvirt Guest migration documentation](https://libvirt.org/migration.html)
  — what libvirt adds on top of QEMU migration (v3
  protocol handshake, NBD non-shared-storage, TLS, tunnelled
  transport). Note absence of COLO.
- [libvirt Domain XML format](https://libvirt.org/formatdomain.html)
  — confirms there is no `<colo>` element; useful as a
  reference for what SF's argv generator would have to
  replace if Stage 1+ ever happens.
- `virDomainQemuMonitorCommand` in libvirt API docs —
  the QMP passthrough escape hatch that Stage 0 may use
  if we go with option (a) for socket location.

Background:
- Michael Hines' Micro-Checkpointing (MC) series circa
  2014–2015 was never merged into upstream QEMU. Remus
  lives in Xen, not KVM. COLO is the spiritual successor
  that did land. Worth knowing if MC comes up in
  research.

### Back brief

Before executing any step of this plan, please back brief
the operator as to your understanding of the plan and how
the work you intend to do aligns with that plan.
