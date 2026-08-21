# webrtc-rs 0.20 upgrade — phase 03: socket binding configuration

## Prompt

Expose the UDP socket binding policy phase 02 invented as
operator-facing configuration, and build the `WebrtcBridgeConfig`
command-line plumbing that does not exist yet.

Before executing any step, read
`shakenfist-spice-webrtc/src/bind_addrs.rs` end to end — it is
the module this phase makes configurable, and its module docs
already state which filters are mechanical and which are policy.
Then read Decision 4 and review items 18 and "interface
allowlist" in
`docs/plans/PLAN-webrtc-0.20-upgrade-phase-02-bump.md`: this
phase is the other half of a decision phase 02 deliberately made
in two parts.

Planning effort: high. The mechanical work is small, but what
the flags *mean* — which of `bind_addrs`' filters an operator
may override and which are non-negotiable — is a design decision
that outlives the phase.

## Scope

In:

- Three new command-line flags on `ryll`: `--web-media-addr`
  (repeatable), `--web-media-port`, `--web-ice-server`
  (repeatable).
- The plumbing to carry them from `config::Args` through
  `WebState` into `WebrtcBridgeConfig`. None of this path exists
  today; `ice_servers` is hard-coded empty at the single
  production construction site.
- A bind policy type in `shakenfist-spice-webrtc` that
  `WebrtcBridge::new` resolves, replacing today's unconditional
  `host_udp_bind_addrs()` call.
- Interface *names* as well as addresses in the selector, which
  is the "interface allowlist, not just a port pin" item the
  phase 02 review deferred here.
- Loopback-only operation as an explicit opt-in (phase 02 review
  item 18).
- `docs/configuration.md`, `docs/web-frontend.md`,
  `docs/web-mode-internals.md`.

Out:

- Anything about *which* codecs are offered, TURN credentials,
  or ICE transport policy (`relay`-only). `--web-ice-server`
  carries STUN/TURN URLs and nothing else; a TURN server that
  needs a username and credential is a follow-up, recorded in
  Future work rather than half-built here.
- Reconfiguring a running session. The flags are read at launch
  and apply to every bridge the process builds.
- Firefox/H.264 (#289, #290), the audio-by-ear check, and the
  soak. Those are phase 04.

## What the survey found

The master plan's phase 03 section is accurate in substance.
Four corrections and one omission, all fixed at source in this
commit:

1. **`ice_servers` is empty at
   `ryll/src/web/signalling.rs:300-303`**, not `:299` — the
   master plan's line number drifted by one during phase 02.
   The claim itself holds: it is a `vec![]` literal, and it is
   the *only* production construction of `WebrtcBridgeConfig` in
   the tree.
2. **`WebrtcBridgeConfig` has about twenty construction sites**
   (`shakenfist-spice-webrtc/src/bridge.rs:492-513` defines it;
   `bridge.rs` unit tests, `tests/loopback.rs`,
   `tests/lifecycle.rs` and `ryll/src/web/lifecycle.rs`
   construct it). All but one are tests, and all of them use
   struct literals even though `WebrtcBridgeConfig::new` exists
   (`bridge.rs:504-513`). Adding a field touches every one of
   them. Decision 4 says what to do about that.
3. **`docs/configuration.md` documents no `--web` flag at
   all** — `grep -- '--web' docs/configuration.md` returns
   nothing, despite `--web`, `--web-host`, `--web-port`,
   `--web-tls-cert` and `--web-tls-key` all shipping
   (`ryll/src/config.rs:161-190`). So "touches
   `docs/configuration.md`" is really "adds the web section that
   was never written". `docs/web-frontend.md` is where those
   flags are documented today (`:13-15`, `:117-119`).
4. **`bind_addrs` discards interface names.**
   `host_udp_bind_addrs` maps `iface.ip()`
   (`bind_addrs.rs:128`), so the `if_addrs::Interface::name`
   an allowlist needs is thrown away one line before it would be
   used. The port is likewise hard-coded to 0 in
   `bindable_udp_addrs` (`bind_addrs.rs:112`).
5. **There is a second consumer.**
   `test_client.rs:263` and `:610` (`TestPeerBuilder`, behind
   the `test-support` feature) call `host_udp_bind_addrs` and
   reject an empty result exactly as `bridge.rs:715-724` does.
   It does not need the config surface, but it must keep
   compiling, and its duplicated error message must not drift
   away from the bridge's.

`WebState` (`ryll/src/web/server.rs:53-148`) carries no
configuration values whatsoever — only channels, slots and
counters. Both constructors funnel through `build`
(`server.rs:185`), and `WebState::new` is `#[cfg(test)]`, so a
new field costs one default in the test constructor and one
argument in `with_channels`.

Nothing else in the master plan's phase 03 section was wrong.

## Decisions

**1. Three flags, named for the `--web-*` family.**
`--web-media-addr <IP|IFACE>` (repeatable),
`--web-media-port <PORT>`, `--web-ice-server <URL>`
(repeatable). "media" rather than "udp" or "rtp" because it
distinguishes them from `--web-host`/`--web-port` by *what they
carry* rather than by transport trivia, and `docs/web-frontend.md:247`
already warns that operators confuse those two things.

**2. The bridge takes a policy, not a resolved address list.**
`WebrtcBridge::new` resolves the policy to socket addresses on
every call, as it resolves `host_udp_bind_addrs()` today. The
alternative — resolve once at launch and pass a
`Vec<SocketAddr>` — is simpler but wrong for a process that
outlives a DHCP lease or an interface flap: a session started
before the VPN came up would keep advertising the addresses that
existed at launch. Ryll still *validates* the flags at startup
(parse the addresses, reject a malformed one) so bad input fails
at launch rather than at the first `POST /offer`.

**3. An explicit address list overrides policy filters, not
mechanical ones.** This is the decision most likely to be argued
with. `bind_addrs`' module docs already separate the two:
loopback and (implicitly) IPv4 link-local and IPv6 ULA are
*policy* — reasonable defaults about what is worth advertising —
while unspecified (`0.0.0.0`, `::`) and zoneless `fe80::/10` are
*mechanical*: a `SocketAddr` cannot represent them in a way ICE
can use. So `--web-media-addr 127.0.0.1` is honoured, and gives
phase 02 review item 18 its loopback-only opt-in for free,
without a fourth flag. `--web-media-addr 0.0.0.0` is rejected at
startup with an error naming the unroutable-candidate failure,
because it is not a preference, it is the exact bug
`bind_addrs.rs` exists to prevent. The argument against: an
operator who types `0.0.0.0` means "all interfaces", and we
could helpfully expand it. The argument that wins: that is
already the default with no flag at all, and silently
reinterpreting an address as a wildcard is how a deployment ends
up advertising an interface its operator thought they had
excluded.

**4. Add the field and migrate the test sites to
`WebrtcBridgeConfig::new`.** Twenty struct literals break on any
new field, and they will break again in phase 04 or whenever the
next option lands. Rather than reach for `#[non_exhaustive]`
(which does nothing for `ryll`, an in-workspace consumer) or a
full builder, the field is added and every *test* site is
migrated to `WebrtcBridgeConfig::new(encoder_control)` followed
by field assignment for whatever it actually cares about. The
one production site keeps an explicit literal so a reviewer can
see every value it chooses. The next field addition then costs
one line, not twenty.

**5. A pinned port applies to every selected address, and a
bind failure is fatal.** webrtc-rs binds with a plain
`UdpSocket::bind` over the addresses handed to `with_udp_addrs`
(`webrtc-0.20.2/src/peer_connection/mod.rs:691-697`), so a
pinned port that is already in use surfaces as an error from
`build()`. Do not catch it and fall back to an ephemeral port:
an operator pins a port precisely because a firewall rule names
it, and a silent fallback produces a bridge that no browser
outside the LAN can reach — the same class of failure as phase
02's `0.0.0.0`. Binding one pinned port across several interface
addresses is fine (distinct addresses, same port), which is why
the port pin and the address selector stay orthogonal flags
rather than one `ADDR:PORT` argument.

**6. `--web-ice-server` takes bare URLs.** `RTCIceServer` has
`username` and `credential` fields, and the bridge already maps
a URL string into one (`bridge.rs:733-742`). Authenticated TURN
needs a way to associate a credential pair with a specific URL,
which is a flag-syntax question worth more thought than a
half-hour of this phase; STUN and open TURN work with the URL
alone. Recorded in Future work.

**7. The published crate's surface changes.**
`shakenfist-spice-webrtc` is published, and a new public field
on `WebrtcBridgeConfig` plus a new public policy type is a
breaking change for downstream users. At `0.1.x` that is a minor
bump handled by the existing release process
(`docs/plans/PLAN-crate-release.md`); it needs no special
handling here beyond not pretending it did not happen.

## Step plan

| Step | Effort | Model | Isolation | Brief for sub-agent |
|------|--------|-------|-----------|---------------------|
| 3a | medium | sonnet | none | Add the bind policy type to `shakenfist-spice-webrtc/src/bind_addrs.rs`. Introduce `pub struct UdpBindPolicy { pub selectors: Vec<BindSelector>, pub port: u16 }` and `pub enum BindSelector { Addr(IpAddr), Interface(String) }`, plus `UdpBindPolicy::resolve(&self) -> Vec<SocketAddr>`. Empty `selectors` must reproduce today's behaviour exactly — every non-loopback, non-unspecified, non-link-local-v6 interface address — so `Default` gives the current policy and `host_udp_bind_addrs()` becomes `UdpBindPolicy::default().resolve()`. Keep `host_udp_bind_addrs` as a public function (it is re-exported at `lib.rs:19` and used by `test_client.rs:263,610`). With a non-empty `selectors`, resolve each: `Addr(ip)` is taken as given, subject only to the mechanical rejections (unspecified and zoneless `fe80::/10`); `Interface(name)` matches `if_addrs::Interface::name` and yields that interface's addresses, again subject only to the mechanical rejections — see Decision 3, and note that this means the enumeration must stop discarding names at `bind_addrs.rs:128`. Apply `self.port` to every resolved address instead of the hard-coded `0` at `bind_addrs.rs:112`. Add a `pub fn validate(&self) -> Result<()>` that rejects an unspecified or zoneless-link-local `Addr` selector with a message naming the unroutable-candidate consequence; ryll calls it at startup. Unit-test the new paths with synthetic inputs only, following the existing tests' shape — the module docs at `bind_addrs.rs:38-45` explain at length why enumerating the real host is off-limits. Update the module docs: the "policy question for whoever decides what a deployment should advertise, which is phase 03's job" sentence now has an answer. |
| 3b | medium | sonnet | none | Wire the policy into the bridge. Add `pub udp_bind: UdpBindPolicy` to `WebrtcBridgeConfig` (`bridge.rs:492-513`) with a doc comment, default it in `WebrtcBridgeConfig::new`, and make `bridge.rs:715` call `config.udp_bind.resolve()` in place of `host_udp_bind_addrs()`. Keep the empty-result error and extend its text: when the policy had explicit selectors, an empty result means the selectors matched nothing on this host, which is a different fix from "bring up an interface" — say which case happened. Then migrate every *test* construction site of `WebrtcBridgeConfig` to `WebrtcBridgeConfig::new(tx)` plus field assignment, per Decision 4: sites are in `bridge.rs` (around `:2119`, `:2131`, `:2174`, `:2225`, `:2258`, `:2309`, `:2318`, `:2438`, `:2542`, `:2596`, `:2626`, `:2660`, `:2690`), `tests/loopback.rs:39,266`, `tests/lifecycle.rs:36` and `ryll/src/web/lifecycle.rs:295,314,389`. Leave the production site at `ryll/src/web/signalling.rs:300` as an explicit literal. Add one unit test near the existing candidate-parsing tests (`bridge.rs:2438-2520` already filters `a=candidate:` lines out of an answer SDP): build a bridge with a pinned port and assert every host candidate carries that port. Do not add CLI flags — 3c does that. |
| 3c | medium | sonnet | none | Add the flags and the plumbing. In `ryll/src/config.rs`, alongside the existing `--web-*` block at `:161-190`, add `--web-media-addr` (`Vec<String>`, repeatable), `--web-media-port` (`u16`, default 0) and `--web-ice-server` (`Vec<String>`, repeatable), each with the doc comment clap renders as help — mention on `--web-media-addr` that it accepts an IP address or an interface name and that giving it explicitly is what enables loopback-only operation. Parse the strings into `BindSelector` values (an interface name is anything that does not parse as an `IpAddr`), build a `UdpBindPolicy`, call `validate()` and fail the launch on error — `ryll/src/main.rs:499-501` is where the other web args are read. Carry the policy and the ICE server list into `WebState` as new fields: add them as arguments to `WebState::with_channels` (`ryll/src/web/server.rs:170`) and `build` (`:185`), and default them in the `#[cfg(test)]` `WebState::new` (`:156`) so the existing test call sites do not change. Consume them at `ryll/src/web/signalling.rs:300-303`, replacing `ice_servers: vec![]`. Add a `config.rs` unit test that `--web-media-addr 0.0.0.0` is rejected and that an interface name and an IP both parse into the right `BindSelector` variant. |
| 3d | medium | sonnet | none | Documentation. `docs/configuration.md` has no `--web` flags at all (see survey finding 3): add a "Web mode" table covering `--web`, `--web-host`, `--web-port`, `--web-tls-cert`, `--web-tls-key` and the three new flags, in the same table style as the existing sections, and link across to `docs/web-frontend.md` for the operator narrative rather than duplicating it. In `docs/web-frontend.md`, rewrite the firewall bullets at `:236-247`: "Pinning a specific port is not configurable yet, so a static firewall rule currently has to open the OS's whole ephemeral range" is now false — document `--web-media-port` and the single static rule it permits, keep and sharpen the `--web-host` versus media-address warning now that `--web-media-addr` exists, and add the loopback-only case. The troubleshooting entry phase 02 added about loopback-only hosts should now point at `--web-media-addr 127.0.0.1` as the fix. In `docs/web-mode-internals.md`, update the "UDP bind addresses" subsection at `:150-170` to describe the policy type rather than the unconditional enumeration. Check `AGENTS.md` and `ARCHITECTURE.md` and change them only if a convention or the shape of the system changed — adding flags is neither. |

Dependencies are linear: 3a → 3b → 3c → 3d. No back-brief gate;
every step is reviewable on its own diff and none of them is
expensive to redo.

## Risks and mitigations

- **The policy silently resolves to nothing** — an operator
  names an interface that does not exist, or one that is down,
  and gets the same "no bindable network interface" error as a
  loopback-only host. Mitigation: 3b's error text distinguishes
  the two cases, and 3c's startup validation catches only what
  it can catch statically (a malformed or unspecified address),
  so the message the operator does see has to be the one that
  tells them which happened. The management session checks that
  text against both cases when reviewing 3b.
- **A pinned port collides and the error is unreadable.** The
  failure arrives from inside `PeerConnectionBuilder::build`,
  which `bridge.rs:749-752` propagates with `?` — so an operator
  may get a bare `Address already in use` with no mention of
  `--web-media-port`. Mitigation: 3b wraps the `build()` error
  with context naming the port and the flag when the policy
  pinned one. Checked by review of the 3b diff, not by a test —
  reliably occupying a port in a test is more fragile than the
  thing it would protect.
- **Behaviour drift with no flags set.** Every existing
  deployment must keep the phase 02 behaviour exactly.
  Mitigation: `UdpBindPolicy::default()` is the current policy by
  construction (3a), and `make test` exercises the default path
  throughout — a drift here fails `tests/loopback.rs`, which is
  the same signal phase 02 relied on.
- **The bridge and `TestPeerBuilder` error messages diverge.**
  They are hand-duplicated today (`bridge.rs:716-723` versus
  `test_client.rs:264-270` and `:611-617`). Mitigation: 3b
  updates the bridge's; the reviewer checks whether the test
  helper's is now stale, and either updates it or accepts the
  divergence explicitly rather than by omission.

## Definition of done

Falsifiable, in the order a reviewer would check them:

- `ryll --help` lists `--web-media-addr`, `--web-media-port` and
  `--web-ice-server`, each with help text that says what it
  takes.
- `ryll --web --web-media-addr 0.0.0.0 …` exits at startup with
  an error that names the unroutable-candidate consequence, not
  at the first `POST /offer`.
- A unit test builds a bridge with a pinned port and asserts
  every `a=candidate:` line in the answer SDP carries that port.
- A unit test asserts `--web-media-addr 127.0.0.1` produces a
  `BindSelector::Addr` that survives validation — loopback is a
  supported explicit choice, not a filtered one.
- A unit test asserts an argument that is not an `IpAddr`
  becomes `BindSelector::Interface`.
- `UdpBindPolicy::default().resolve()` and
  `host_udp_bind_addrs()` return the same thing, and no test in
  `tests/loopback.rs` or `tests/lifecycle.rs` changed behaviour
  to accommodate this phase.
- `grep -- '--web-media-port' docs/configuration.md
  docs/web-frontend.md` matches in both, and
  `grep -i 'not configurable' docs/web-frontend.md` returns
  nothing.
- No fact about UDP bind behaviour is stated differently in
  `docs/configuration.md`, `docs/web-frontend.md` and
  `docs/web-mode-internals.md`.
- `make test`, `make lint` and `pre-commit run --all-files` all
  pass.

## Effort

The master plan estimates half a day. Revise to **one day**. The
half-day estimate was written when phase 03 was "add a flag for
the bind address"; the survey found that `WebrtcBridgeConfig`
has no command-line path at all, that twenty construction sites
move with any new field, that the interface allowlist and the
loopback opt-in were both deferred into this phase by phase 02's
review, and that `docs/configuration.md` never documented web
mode in the first place. None of it is hard, and there is a lot
of it.

| Step | Estimate |
|---|---|
| 3a — bind policy type | 2 hours |
| 3b — bridge wiring and site migration | 2 hours |
| 3c — flags and plumbing | 2 hours |
| 3d — documentation | 2 hours |

## Status

Complete. All four steps landed, `make test`, `make lint`,
`make web-smoke`, `make web-smoke-tls` and
`pre-commit run --all-files` are clean, and both browser-visible
Definition-of-done items were checked against the built binary
rather than only asserted in tests:

    $ ryll --web --direct 127.0.0.1:5900 --web-media-addr 0.0.0.0
    Error: web media binding (`--web-media-addr` /
    `--web-media-port`): 0.0.0.0 cannot be used as a media bind
    address: it binds successfully and then advertises itself
    verbatim as an ICE host candidate, which every browser
    discards …

## What landed

Nineteen `WebrtcBridgeConfig` construction sites moved to
`WebrtcBridgeConfig::new`, which matches the "about twenty" the
survey estimated.

Five things differ from the plan as written:

1. **Resolved addresses are deduplicated, which the plan did not
   ask for.** An operator can name an interface and one of its
   addresses, and a host can report one address on two
   interfaces. With an ephemeral port a duplicate is cosmetic —
   two sockets, two candidates — but with a pinned port the
   second `UdpSocket::bind` on the same address:port fails and
   takes the whole peer connection with it. `dedup_with_port`
   applies to the default path too, so the `--web-media-port`
   pin is safe without any selectors.
2. **`bindable_udp_addrs` now returns `Vec<IpAddr>`, not
   `Vec<SocketAddr>`.** The port is applied once, at the end, by
   `dedup_with_port`, rather than by each filtering path. The
   existing unit tests kept their assertions verbatim through a
   `bound()` helper in the test module.
3. **`WebState::build` carries an
   `#[allow(clippy::too_many_arguments)]`.** Two more arguments
   pushed it past the threshold. The alternative — a config
   struct for the constructor — is a larger refactor than this
   phase should be doing, and `build` is private with two
   callers.
4. **`ARCHITECTURE.md` changed by one annotation.** The plan said
   to touch it only if the shape of the system changed, and it
   did not. But its `shakenfist-spice-webrtc/` file-tree
   annotation named `host_udp_bind_addrs()` as what
   `bind_addrs.rs` is, which is now the default case of
   `UdpBindPolicy` rather than the whole module. Corrected in
   place, not expanded.
5. **Implemented in the management session, not by sub-agents.**
   The shared execution-model block calls for one sub-agent per
   step. This session ran under Claude Code's auto mode, whose
   standing policy is not to spawn sub-agents unless the operator
   asks for them, and the operator's request was "implement the
   phase plan" rather than "run it as sub-agents" — so the four
   steps were executed directly, in order, with `make lint` and
   the crate's tests between them. Nothing was disabled; the
   capability was available and the mode's default is simply not
   to reach for it unasked. Recorded because the convention
   exists, not because anything about the output differs.

The two risks the plan named as review-only checks were both
handled: `WebrtcBridge::new` now distinguishes "no bindable
network interface" from "no media bind address matched", and a
`build()` failure under a pinned port is wrapped with the port
number and the flag that chose it.

## What the review changed

The automated review of PR #298 raised three action items and
eight suggestions. All three action items and six suggestions
landed; two suggestions were declined, with reasons.

The action items were the stale `host_udp_bind_addrs:` log prefix
in `test_client.rs` (the divergence this plan explicitly asked a
reviewer to decide on rather than leave to silence — it was
stale, and both copies now say `bind_addrs:`), `docs/features.md`
still listing the pre-PR web flag set, and the missing
`docs/multi-mode-parity.md` row that `AGENTS.md` makes mandatory.

Four suggestions closed gaps this plan had knowingly left open:

- **Interface-selector hit paths are now tested.** `select_from`
  was split out of `selected_addrs` as a pure function taking the
  interface list, so name matching, an interface with several
  addresses, one address on two interfaces, and a link-local-only
  interface are all covered against a synthetic fixture. The
  module docs rule out enumerating the real host in a test, which
  argues for injecting the list rather than leaving the logic
  uncovered.
- **The "no media bind address matched" branch has a test.**
  Risk 1 settled for a review-time check; it is deterministic via
  a nonexistent interface name and binds no socket, so it is now
  pinned rather than re-checked by eye each time.
- **`--web-ice-server` is validated at launch.** Decision 2 said
  bad input fails at launch, and this flag was the one that did
  not. It matters most here: an operator only reaches for an ICE
  server when host candidates already fail, so a silently useless
  URL is indistinguishable from WebRTC being broken.
- **A malformed address literal no longer becomes an interface
  name.** The address-or-interface fallback is ambiguous in one
  direction only, and this plan's own error text steers people
  into it: told that `fe80::1` needs a zone id, the natural next
  attempt is `fe80::1%eth0`, which Rust cannot parse and which
  therefore became a nonexistent interface name. A single colon
  is still a name (`eth0:0` is a real alias label).

Two more were diagnostic polish: a `build()` failure now names
`--web-media-addr` as well as `--web-media-port`, each clause
appearing only when that flag was actually set; and the
whole-policy `validate()` error is prefixed with the flag family
rather than `--web-media-addr`, so the first port-related check
added later cannot inherit the wrong flag name.

Declined:

- **Declaring `rust-version = "1.88"` for the `as_chunks`
  rewrite.** The floor is real, but it is set by the dependency
  tree rather than by these three lines, and nothing in CI builds
  on the oldest supported toolchain — so the number would be an
  unverified promise on two published crates. Whether this
  workspace declares an MSRV at all is a repo-wide decision that
  deserves its own change, with a CI job that makes the claim
  true.
- **Tightening the `--web-*` flags to require `--web`.** Recorded
  as an observation rather than an action item by the review
  itself, and it is: `--web-host` and `--web-port` have always
  behaved this way, so changing only the three new flags would
  make them behave unlike their siblings.

One informational item has no artefact to carry it: the review
suggests noting in the crate's release notes that
`host_udp_bind_addrs()` now deduplicates, since it is public and
its observable output changed. There is no changelog in this
repo, so it is recorded here and in deviation 1 above for
whoever writes the next `shakenfist-spice-webrtc` release notes.

## Back brief

Before executing any step of this plan, back brief the operator
on your understanding of it — in particular on Decision 3, which
is the one a reviewer is most likely to disagree with, and on
what "the default with no flags set must not change" means for
the step you are about to run.
